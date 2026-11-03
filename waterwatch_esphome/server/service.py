import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from datetime import datetime

import cv2

from server.alignment import AlignmentError, register
from server.models import ManualReading, MeterConfig, m3, micro_m3, timestamp, utcnow
from server.ocr import Pipeline, align, decode
from server.storage import Store
from server.validation import validate

log = logging.getLogger(__name__)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public_reading(row):
    if row is None:
        return None
    result = dict(row)
    result["schema_version"] = 1
    result.pop("fingerprint", None)
    result["accepted"] = bool(result["accepted"])
    value = result.pop("value_micro")
    result["value_m3"] = m3(value) if value is not None else None
    result["details"] = json.loads(result["details"])
    return result


class Service:
    def __init__(self, settings, pipeline=None):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.images = settings.data_dir / "images"
        self.images.mkdir(exist_ok=True)
        self.references = settings.data_dir / "references"
        self.references.mkdir(exist_ok=True)
        self.store = Store(settings.data_dir / "waterwatch.sqlite3")
        self.pipeline = pipeline or Pipeline(reference_loader=self.load_reference)
        # Bound inference concurrency and serialize validation, configuration, and corrections.
        self.processing = threading.RLock()

    def save_config(self, cfg: MeterConfig, create=False):
        if cfg.mode != "Docker":
            raise ValueError("This MVP supports Docker OCR only; local OCR model is not installed")
        token = secrets.token_urlsafe(32)
        with self.processing, self.store.transaction() as db:
            if cfg.alignment.enabled:
                self.validate_alignment(cfg)
            if create:
                db.execute(
                    "INSERT INTO meters(id,config,token_hash) VALUES(?,?,?)",
                    (cfg.id, cfg.model_dump_json(), token_hash(token)),
                )
            else:
                if (
                    db.execute(
                        "UPDATE meters SET config=? WHERE id=?", (cfg.model_dump_json(), cfg.id)
                    ).rowcount
                    != 1
                ):
                    raise KeyError(cfg.id)
        return token if create else None

    @staticmethod
    def reference_geometry(cfg):
        return {"rotation_degrees": cfg.rotation_degrees, "perspective": cfg.perspective,
                "aligned_width": cfg.aligned_width, "aligned_height": cfg.aligned_height}

    def save_reference(self, meter_id, data):
        with self.processing:
            cfg = self.store.config(meter_id)
            image = align(decode(data), cfg, use_registration=False)
            key = uuid.uuid4().hex
            path = self.references / f"{cfg.id}-{key}.png"
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise ValueError("Reference image encoding failed")
            path.write_bytes(encoded.tobytes())
            path.with_suffix(".json").write_text(json.dumps(self.reference_geometry(cfg)))
            path.with_suffix(".source").write_bytes(data)
            protected = {key, cfg.alignment.reference_id}
            previous = sorted(self.references.glob(f"{cfg.id}-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in previous[5:]:
                old_key = old.stem.removeprefix(cfg.id+"-")
                if old_key in protected:
                    continue
                if old.resolve().parent != self.references.resolve() or len(old_key) != 32 or any(c not in "0123456789abcdef" for c in old_key):
                    continue
                for extension in (".png", ".json", ".source"):
                    old.with_suffix(extension).unlink(missing_ok=True)
            return {"reference_id": key, "width": image.shape[1], "height": image.shape[0]}

    def load_reference(self, cfg):
        key = cfg.alignment.reference_id
        if not key:
            raise AlignmentError("alignment_reference_missing")
        path = self.references / f"{cfg.id}-{key}.png"
        if not path.exists() or not path.with_suffix(".json").exists():
            raise AlignmentError("alignment_reference_missing")
        expected = json.loads(json.dumps(self.reference_geometry(cfg)))
        if json.loads(path.with_suffix(".json").read_text()) != expected:
            raise AlignmentError("alignment_reference_geometry_changed")
        image = cv2.imread(str(path))
        if image is None:
            raise AlignmentError("alignment_reference_missing")
        return image

    def validate_alignment(self, cfg):
        reference = self.load_reference(cfg)
        register(reference, reference, cfg)

    def transform(self, data, cfg):
        reference = self.load_reference(cfg) if cfg.alignment.enabled else None
        return align(decode(data), cfg, reference=reference)

    @staticmethod
    def test_images(data, result):
        def encoded(value, mime="image/jpeg"):
            return {"mime": mime, "base64": base64.b64encode(value).decode()} if value else None
        return {"original": encoded(data, "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"),
                "aligned": encoded(result.aligned), "overlay": encoded(result.overlay),
                "processed": encoded(result.processed),
                "digits": [encoded(crop) for crop in result.crops]}

    def rotate_token(self, meter_id):
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            if (
                db.execute(
                    "UPDATE meters SET token_hash=? WHERE id=?", (token_hash(token), meter_id)
                ).rowcount
                != 1
            ):
                raise KeyError(meter_id)
        return token

    def state(self, meter_id):
        cfg = self.store.config(meter_id)
        good = self.store.last_good(meter_id)
        latest = self.store.one(
            "SELECT * FROM readings WHERE meter_id=? ORDER BY seq DESC LIMIT 1", (meter_id,)
        )
        camera = self.store.one("SELECT last_seen,rssi FROM meters WHERE id=?", (meter_id,))
        now = utcnow()
        stale = (
            good is None
            or (now - datetime.fromisoformat(good["captured_at"])).total_seconds()
            > cfg.stale_seconds
        )
        camera_online = bool(
            camera["last_seen"]
            and (now - datetime.fromisoformat(camera["last_seen"])).total_seconds()
            < cfg.camera.interval_seconds * 3
        )
        error = (
            latest["reason"] if latest and not latest["accepted"] else ("stale" if stale else "ok")
        )
        return {
            "meter_id": meter_id,
            "total_m3": m3(good["value_micro"]) if good else None,
            "last_success": good["captured_at"] if good else None,
            "confidence": round(latest["confidence"] * 100, 2)
            if latest and latest["source"] in ("Docker", "ESP32")
            else None,
            "rate_lph": good["rate_lph"]
            if good and latest and good["seq"] == latest["seq"] and not stale
            else None,
            "rssi": camera["rssi"],
            "camera_online": camera_online,
            "stale": stale,
            "error": error,
        }

    def _queue(self, db, meter_id):
        self.store.enqueue(
            db, f"{self.settings.topic_prefix}/{meter_id}/state", self.state(meter_id)
        )

    def process(
        self, meter_id, event_id, captured_at, data, rssi=None, test_only=False,
        capture_metadata=None,
        include_images=False,
    ):
        captured_at = timestamp(captured_at)
        fingerprint = hashlib.sha256(data + captured_at.encode()).hexdigest()
        with self.processing:
            cfg = self.store.config(meter_id)
            if not test_only:
                prior = self.store.one(
                    "SELECT * FROM readings WHERE meter_id=? AND event_id=?", (meter_id, event_id)
                )
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise ValueError(
                            "Event ID was already used with different image or timestamp"
                        )
                    return {**public_reading(prior), "duplicate": True}
            started = time.perf_counter()
            result = self.pipeline.run(data, cfg)
            elapsed = (time.perf_counter() - started) * 1000
            result.details["inference_ms"] = round(elapsed, 2)
            if capture_metadata:
                result.details["capture"] = capture_metadata
            value, reason, rate = validate(
                result,
                cfg,
                datetime.fromisoformat(captured_at),
                utcnow(),
                self.store.last_good(meter_id),
            )
            if test_only:
                return {
                    "raw_value": result.raw_value,
                    "confidence": result.confidence,
                    "reason": reason,
                    "would_accept": value is not None,
                    "details": result.details,
                    "images": self.test_images(data, result),
                    "calibration": cfg.model_dump(),
                }
            key = uuid.uuid4().hex
            for suffix, content in (
                ("original", data),
                ("overlay", result.overlay),
                ("processed", result.processed),
            ):
                if content:
                    (self.images / f"{key}-{suffix}.jpg").write_bytes(content)
            now = timestamp(utcnow())
            with self.store.transaction() as db:
                db.execute(
                    """INSERT INTO readings(meter_id,event_id,fingerprint,captured_at,received_at,
                    raw_value,value_micro,confidence,accepted,reason,source,rate_lph,image_key,details)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        meter_id,
                        event_id,
                        fingerprint,
                        captured_at,
                        now,
                        result.raw_value,
                        value,
                        result.confidence,
                        int(value is not None),
                        reason,
                        "Docker",
                        rate,
                        key,
                        json.dumps(result.details),
                    ),
                )
                # A delayed capture must not make the camera appear freshly connected.
                if (
                    utcnow() - datetime.fromisoformat(captured_at)
                ).total_seconds() <= cfg.camera.interval_seconds * 2:
                    db.execute(
                        "UPDATE meters SET last_seen=?,rssi=? WHERE id=?", (now, rssi, meter_id)
                    )
                self._queue(db, meter_id)
            log.info(
                "meter=%s event=%s reason=%s inference_ms=%.1f", meter_id, event_id, reason, elapsed
            )
            reading = public_reading(
                self.store.one(
                    "SELECT * FROM readings WHERE meter_id=? AND event_id=?", (meter_id, event_id)
                )
            )
            if include_images:
                reading["images"] = self.test_images(data, result)
                reading["calibration"] = cfg.model_dump()
            return reading

    def manual(self, meter_id, correction: ManualReading):
        with self.processing:
            self.store.config(meter_id)
            previous = self.store.last_good(meter_id)
            value = micro_m3(correction.value_m3)
            if previous and value < previous["value_micro"] and correction.kind == "correction":
                raise ValueError(
                    "Decreasing the counter requires an explicit reset, rollover or replacement event"
                )
            if correction.reference_event and not self.store.one(
                "SELECT seq FROM readings WHERE meter_id=? AND event_id=?",
                (meter_id, correction.reference_event),
            ):
                raise ValueError("Referenced event does not exist for this meter")
            # Never rewrite prior published statistics. Manual events establish a new current baseline.
            now = timestamp(utcnow())
            event_id = f"manual-{uuid.uuid4().hex}"
            with self.store.transaction() as db:
                db.execute(
                    """INSERT INTO readings(meter_id,event_id,fingerprint,captured_at,received_at,
                    raw_value,value_micro,confidence,accepted,reason,source,details)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        meter_id,
                        event_id,
                        "manual",
                        now,
                        now,
                        correction.value_m3,
                        value,
                        1,
                        1,
                        "manual_baseline",
                        correction.kind,
                        correction.model_dump_json(),
                    ),
                )
                self._queue(db, meter_id)
            return public_reading(self.store.last_good(meter_id))

    def summary(self):
        results = []
        for cfg in self.store.configs():
            latest = self.store.one(
                "SELECT * FROM readings WHERE meter_id=? ORDER BY seq DESC LIMIT 1", (cfg.id,)
            )
            latest_capture = self.store.one(
                "SELECT * FROM readings WHERE meter_id=? AND image_key IS NOT NULL ORDER BY seq DESC LIMIT 1",
                (cfg.id,),
            )
            results.append(
                {
                    "config": cfg.model_dump(),
                    "state": self.state(cfg.id),
                    "latest": public_reading(latest),
                    "latest_capture": public_reading(latest_capture),
                    "last_verified": public_reading(self.store.last_good(cfg.id)),
                }
            )
        return results

    def cleanup_images(self):
        cutoff = time.time() - self.settings.image_retention_days * 86400
        for path in self.images.glob("*.jpg"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
