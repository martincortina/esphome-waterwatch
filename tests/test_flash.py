"""Flash timing, cleanup and opt-in boundaries; synthetic images, no OCR claim."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.config import Settings
from server.home_assistant import CameraError, HomeAssistantCamera
from server.main import create_app
from server.models import FlashConfig, ImageSource, MeterConfig


@pytest.fixture
def flash_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("server.home_assistant.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("server.home_assistant.time.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


def adapter(tmp_path, handler):
    return HomeAssistantCamera(
        Settings(tmp_path, "a" * 48, home_assistant_url="http://supervisor/core/api",
                 home_assistant_token="test-supervisor-token"),
        transport=httpx.MockTransport(handler),
    )


class CaptureHost:
    def __init__(self, image):
        self.image = image
        self.calls = []
        self.frames = 0
        self.state = "off"
        self.supported = 8
        self.failure = None

    def __call__(self, request):
        path = request.url.path.removeprefix("/core/api/")
        self.calls.append((request.method, path))
        if path == "states/camera.meter":
            return httpx.Response(200, json={"state": "idle"})
        if path == "states/light.meter":
            return httpx.Response(200, json={"state": self.state, "attributes": {"supported_features": self.supported}})
        if path.startswith("services/light/"):
            data = json.loads(request.content)
            assert data["entity_id"] == "light.meter" and data["transition"] == 0
            if path.endswith("turn_on"):
                assert data == {"entity_id": "light.meter", "transition": 0, "brightness_pct": 20, "flash": "long"}
                if self.failure == "on_timeout":
                    raise httpx.ReadTimeout("SECRET response", request=request)
                self.state = "on"
            else:
                if self.failure == "off_error":
                    return httpx.Response(500, text="SECRET backend error")
                self.state = "off"
            return httpx.Response(200, json=[])
        assert path == "camera_proxy/camera.meter"
        self.frames += 1
        if self.failure == "image_timeout":
            raise httpx.ReadTimeout("SECRET response", request=request)
        data = b"broken" if self.failure == "invalid_image" else self.image
        return httpx.Response(200, content=data, headers={"content-type": "image/png"})


def test_flash_settles_discards_queued_frames_and_finishes_off(tmp_path, image, flash_clock):
    host = CaptureHost(image)
    camera = adapter(tmp_path, host)
    try:
        assert camera.snapshot("camera.meter", FlashConfig(light_entity="light.meter")) == image
        assert flash_clock[0] == 101.5
        assert host.frames == 3 and host.state == "off"
        assert host.calls[-1] == ("POST", "services/light/turn_off")
    finally:
        camera.close()


@pytest.mark.parametrize("failure,code", [
    ("on_timeout", "flash_light_connection_failed"),
    ("image_timeout", "home_assistant_connection_failed"),
    ("invalid_image", "camera_invalid_image"),
    ("off_error", "flash_light_http_500"),
])
def test_capture_failure_always_attempts_off_and_never_returns_image(tmp_path, image, flash_clock, failure, code):
    host = CaptureHost(image)
    host.failure = failure
    camera = adapter(tmp_path, host)
    try:
        with pytest.raises(CameraError) as error:
            camera.snapshot("camera.meter", FlashConfig(light_entity="light.meter"))
        assert str(error.value) == code
        assert host.calls[-1] == ("POST", "services/light/turn_off")
    finally:
        camera.close()


@pytest.mark.parametrize("state,features,code", [
    ("on", 8, "flash_light_must_be_off"),
    ("off", 0, "flash_light_not_supported"),
    ("unavailable", 8, "flash_light_unavailable"),
])
def test_unsuitable_light_never_receives_a_write(tmp_path, image, state, features, code):
    host = CaptureHost(image)
    host.state, host.supported = state, features
    camera = adapter(tmp_path, host)
    try:
        with pytest.raises(CameraError, match=code):
            camera.snapshot("camera.meter", FlashConfig(light_entity="light.meter"))
        assert all(method == "GET" for method, _ in host.calls)
    finally:
        camera.close()


def test_frame_retrieved_after_flash_deadline_is_discarded(tmp_path, image, flash_clock):
    host = CaptureHost(image)

    def delayed(request):
        response = host(request)
        if "camera_proxy" in request.url.path:
            flash_clock[0] += 9
        return response

    camera = adapter(tmp_path, delayed)
    try:
        with pytest.raises(CameraError, match="flash_capture_timed_out"):
            camera.snapshot("camera.meter", FlashConfig(light_entity="light.meter"))
        assert host.state == "off" and host.frames == 1
    finally:
        camera.close()


def test_flash_preview_is_explicit_post_and_does_not_write_ledger(tmp_path, image, flash_clock):
    host = CaptureHost(image)
    camera = adapter(tmp_path, host)
    settings = Settings(tmp_path / "app", "b" * 48)
    app = create_app(settings, start_background=False, ha_camera=camera)
    app.state.service.save_config(MeterConfig(
        id="house", name="House", source=ImageSource(
            kind="home_assistant", camera_entity="camera.meter",
            flash=FlashConfig(light_entity="light.meter"),
        ),
    ), create=True)
    with TestClient(app) as api:
        assert api.post("/api/meters/house/camera-image").status_code == 401
        assert host.calls == []
        api.headers["Authorization"] = "Bearer " + settings.admin_token
        assert api.get("/api/meters/house/camera-image").content == image
        assert all(method == "GET" for method, _ in host.calls)
        assert api.post("/api/meters/house/camera-image").content == image
        assert host.state == "off"
        assert app.state.service.store.all("SELECT * FROM readings") == []
        assert app.state.service.store.all("SELECT * FROM outbox") == []


def test_failed_flash_cleanup_cannot_refresh_verified_reading(client, image, flash_clock):
    from server.home_assistant import CameraCollector

    cfg = client.service.store.config("house")
    cfg.source = ImageSource(kind="home_assistant", camera_entity="camera.meter",
                             flash=FlashConfig(light_entity="light.meter"))
    client.service.save_config(cfg)
    host = CaptureHost(image)
    host.failure = "off_error"
    camera = adapter(client.service.settings.data_dir, host)
    previous = client.service.state("house")["last_success"]
    try:
        with pytest.raises(CameraError, match="flash_light_http_500"):
            CameraCollector(client.service, camera).capture("house", test_only=False)
        assert client.pipeline.calls == 0
        assert client.service.state("house")["last_success"] == previous
    finally:
        camera.close()


def test_flash_entity_and_brightness_validation_and_default_opt_out():
    assert ImageSource(kind="home_assistant", camera_entity="camera.meter").flash is None
    for fields in (
        {"light_entity": "light.meter/../../services"},
        {"light_entity": "light.meter", "brightness_pct": 0},
        {"light_entity": "light.meter", "settle_ms": 3000},
    ):
        with pytest.raises(ValidationError):
            FlashConfig(**fields)
    with pytest.raises(ValidationError):
        ImageSource(flash=FlashConfig(light_entity="light.meter"))
