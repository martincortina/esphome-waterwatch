"""HA camera reads, with optional, explicitly selected ESPHome flash illumination."""

import hashlib
import json
import re
import threading
import time
import uuid

import httpx

from server.models import timestamp, utcnow
from server.ocr import decode

MAX_IMAGE_BYTES = 2 * 1024 * 1024
CAMERA_ENTITY = re.compile(r"^camera\.[a-z0-9_]+$")
LIGHT_ENTITY = re.compile(r"^light\.[a-z0-9_]+$")
FLASH_FEATURE = 8  # Home Assistant LightEntityFeature.FLASH
FLASH_CAPTURE_SECONDS = 8  # ESPHome's HA 'long' flash restores its state after 10 s.


class CameraError(Exception):
    """A stable diagnostic code; never includes URLs, headers or server responses."""


class HomeAssistantCamera:
    def __init__(self, settings, transport=None):
        self.configured = bool(settings.home_assistant_url and settings.home_assistant_token)
        self.base = settings.home_assistant_url.rstrip("/")
        self.lock = threading.RLock()
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def _read(self, path, limit, image=False, deadline=None):
        if not self.configured:
            raise CameraError("home_assistant_not_configured")
        try:
            timeout = 20 if deadline is None else self._remaining(deadline)
            with self.client.stream("GET", self.base + path, timeout=timeout) as response:
                if response.status_code != 200:
                    raise CameraError(f"home_assistant_http_{response.status_code}")
                if image and response.headers.get("content-type", "").split(";")[0] not in (
                    "image/jpeg", "image/png",
                ):
                    raise CameraError("camera_invalid_content_type")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    if deadline is not None:
                        self._remaining(deadline)
                    size += len(chunk)
                    if size > limit:
                        raise CameraError("home_assistant_response_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            raise CameraError("home_assistant_connection_failed") from None

    def _json(self, path):
        try:
            return json.loads(self._read(path, 8 * 1024 * 1024))
        except (ValueError, UnicodeError):
            raise CameraError("home_assistant_invalid_response") from None

    def cameras(self):
        states = self._json("/states")
        if not isinstance(states, list):
            raise CameraError("home_assistant_invalid_response")
        return [
            {
                "entity_id": state["entity_id"],
                "name": state.get("attributes", {}).get("friendly_name", state["entity_id"]),
                "state": state.get("state", "unknown"),
            }
            for state in states
            if isinstance(state, dict) and CAMERA_ENTITY.fullmatch(state.get("entity_id", ""))
        ]

    def flash_lights(self):
        states = self._json("/states")
        if not isinstance(states, list):
            raise CameraError("home_assistant_invalid_response")
        return [
            {
                "entity_id": state["entity_id"],
                "name": state.get("attributes", {}).get("friendly_name", state["entity_id"]),
                "state": state.get("state", "unknown"),
            }
            for state in states
            if isinstance(state, dict)
            and LIGHT_ENTITY.fullmatch(state.get("entity_id", ""))
            and self._supports_flash(state)
        ]

    @staticmethod
    def _supports_flash(state):
        features = state.get("attributes", {}).get("supported_features", 0)
        return isinstance(features, int) and bool(features & FLASH_FEATURE)

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CameraError("flash_capture_timed_out")
        return remaining

    def _light(self, entity_id, brightness_pct=None):
        # No general-purpose HA action endpoint: the only writes are a timed flash
        # and immediate off for the selected meter light. Never fall back to steady on.
        action = "turn_off" if brightness_pct is None else "turn_on"
        payload = {"entity_id": entity_id, "transition": 0}
        if brightness_pct is not None:
            payload.update(brightness_pct=brightness_pct, flash="long")
        try:
            with self.client.stream(
                "POST", self.base + "/services/light/" + action, json=payload, timeout=5,
            ) as response:
                if response.status_code != 200:
                    raise CameraError(f"flash_light_http_{response.status_code}")
        except httpx.HTTPError:
            raise CameraError("flash_light_connection_failed") from None

    def _image(self, entity_id, deadline=None):
        data = self._read(
            f"/camera_proxy/{entity_id}", MAX_IMAGE_BYTES, image=True, deadline=deadline,
        )
        try:
            decode(data)
        except ValueError:
            raise CameraError("camera_invalid_image") from None
        if deadline is not None:
            self._remaining(deadline)
        return data

    def snapshot(self, entity_id, flash=None):
        with self.lock:
            return self._snapshot(entity_id, flash)

    def _snapshot(self, entity_id, flash):
        if not CAMERA_ENTITY.fullmatch(entity_id):
            raise CameraError("invalid_camera_entity")
        state = self._json(f"/states/{entity_id}")
        if not isinstance(state, dict) or state.get("state") in (None, "unknown", "unavailable"):
            raise CameraError("camera_unavailable")
        if flash is None:
            return self._image(entity_id)
        if not LIGHT_ENTITY.fullmatch(flash.light_entity):
            raise CameraError("invalid_flash_light_entity")
        light = self._json(f"/states/{flash.light_entity}")
        if not isinstance(light, dict) or light.get("state") not in ("on", "off"):
            raise CameraError("flash_light_unavailable")
        if not self._supports_flash(light):
            raise CameraError("flash_light_not_supported")
        # The device restores the pre-flash state on its own. Require off so a host
        # crash cannot restore an always-on LED. Do not override a manual light use.
        if light["state"] != "off":
            raise CameraError("flash_light_must_be_off")
        deadline = time.monotonic() + FLASH_CAPTURE_SECONDS
        try:
            self._light(flash.light_entity, flash.brightness_pct)
            time.sleep(flash.settle_ms / 1000)
            self._remaining(deadline)
            # Flush two queued frames: a snapshot request may first receive a frame
            # exposed before illumination. Never send those warm-up frames to OCR.
            self._image(entity_id, deadline)
            self._image(entity_id, deadline)
            return self._image(entity_id, deadline)
        finally:
            # Runs even after a failed turn-on acknowledgement or a bad image. If
            # HA is unreachable, the device's 10-second flash still expires itself.
            self._light(flash.light_entity)


class CameraCollector:
    def __init__(self, service, camera):
        self.service = service
        self.camera = camera
        self.next_due = {}
        self.statuses = {}
        self.lock = threading.RLock()

    def status(self, meter_id):
        with self.lock:
            return dict(self.statuses.get(meter_id, {"status": "not_captured"}))

    def preview(self, meter_id):
        with self.lock:
            return self._image(self.service.store.config(meter_id))

    def _image(self, cfg):
        if cfg.source.kind != "home_assistant":
            raise CameraError("home_assistant_camera_not_selected")
        if cfg.source.flash is not None:
            return self.camera.snapshot(cfg.source.camera_entity, flash=cfg.source.flash)
        return self.camera.snapshot(cfg.source.camera_entity)

    def capture(self, meter_id, test_only=True, include_images=False):
        with self.lock:
            cfg = self.service.store.config(meter_id)
            if cfg.source.kind != "home_assistant":
                raise CameraError("home_assistant_camera_not_selected")
            try:
                data = self._image(cfg)
                captured_at = utcnow()
                digest = hashlib.sha256(data).hexdigest()
                # The proxy has no trustworthy exposure timestamp. An identical response
                # must not make an old/frozen frame count as a fresh verified reading.
                previous = self.service.store.one(
                    "SELECT details FROM readings WHERE meter_id=? AND image_key IS NOT NULL "
                    "ORDER BY seq DESC LIMIT 1", (meter_id,),
                )
                if not test_only and previous:
                    capture = json.loads(previous["details"]).get("capture", {})
                    if (
                        capture.get("camera_entity") == cfg.source.camera_entity
                        and capture.get("image_sha256") == digest
                    ):
                        raise CameraError("camera_repeated_frame")
                with self.service.processing:
                    if self.service.store.config(meter_id) != cfg:
                        raise CameraError("configuration_changed_during_capture")
                    result = self.service.process(
                        meter_id, f"ha-{uuid.uuid4().hex}", captured_at, data,
                        test_only=test_only,
                        include_images=include_images,
                        capture_metadata={
                            "camera_entity": cfg.source.camera_entity,
                            "image_sha256": digest,
                            "timestamp_basis": "home_assistant_retrieval",
                            "flash": cfg.source.flash.model_dump() if cfg.source.flash else None,
                        },
                    )
                self.statuses[meter_id] = {
                    "status": "test_complete" if test_only else "captured",
                    "last_retrieved_at": timestamp(captured_at),
                    "reading_reason": result["reason"],
                }
                return result
            except CameraError as exc:
                self.statuses[meter_id] = {"status": str(exc)}
                raise

    def tick(self):
        with self.lock:
            configs = self.service.store.configs()
            active_ids = {cfg.id for cfg in configs}
            self.next_due = {k: v for k, v in self.next_due.items() if k in active_ids}
            for cfg in configs:
                if cfg.source.kind != "home_assistant" or not cfg.source.enabled:
                    self.next_due.pop(cfg.id, None)
                    continue
                now = time.monotonic()
                if now < self.next_due.get(cfg.id, 0):
                    continue
                # Set before requesting so errors cannot cause a rapid retry loop.
                self.next_due[cfg.id] = now + cfg.camera.interval_seconds
                try:
                    self.capture(cfg.id, test_only=False)
                except CameraError:
                    pass  # The safe diagnostic is exposed in status(), without headers.
