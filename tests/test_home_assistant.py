from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.addon import AddonOptions, addon_settings
from server.config import Settings
from server.home_assistant import CameraCollector, CameraError, HomeAssistantCamera
from server.main import create_app
from server.models import ImageSource, MeterConfig
from server.ocr import Recognition


def camera_adapter(tmp_path, handler):
    settings = Settings(
        tmp_path, "a" * 48, home_assistant_url="http://supervisor/core/api",
        home_assistant_token="test-supervisor-token",
    )
    return HomeAssistantCamera(settings, transport=httpx.MockTransport(handler))


def test_camera_client_reads_only_and_hides_camera_access_token(tmp_path, image):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer test-supervisor-token"
        if request.url.path.endswith("/camera_proxy/camera.meter"):
            return httpx.Response(200, content=image, headers={"content-type": "image/png"})
        states = [
            {"entity_id": "camera.meter", "state": "idle", "attributes": {
                "friendly_name": "Meter", "access_token": "must-not-be-exported",
            }},
            {"entity_id": "sensor.meter", "state": "1"},
        ]
        return httpx.Response(200, json=states if request.url.path.endswith("/states") else states[0])

    with_adapter = camera_adapter(tmp_path, handler)
    try:
        assert with_adapter.cameras() == [{"entity_id": "camera.meter", "name": "Meter", "state": "idle"}]
        assert with_adapter.snapshot("camera.meter") == image
        with pytest.raises(CameraError, match="invalid_camera_entity"):
            with_adapter.snapshot("camera.meter/../../services/light/turn_on")
        assert len(requests) == 3
    finally:
        with_adapter.close()


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_camera_http_errors_do_not_expose_response_or_credentials(tmp_path, status):
    adapter = camera_adapter(
        tmp_path, lambda _: httpx.Response(status, text="SECRET backend diagnostic")
    )
    try:
        with pytest.raises(CameraError) as error:
            adapter.cameras()
        assert str(error.value) == f"home_assistant_http_{status}"
    finally:
        adapter.close()


def test_camera_redirect_is_never_followed(tmp_path):
    paths = []

    def handler(request):
        paths.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://another-host.example/"})

    adapter = camera_adapter(tmp_path, handler)
    try:
        with pytest.raises(CameraError, match="home_assistant_http_302"):
            adapter.cameras()
        assert len(paths) == 1
    finally:
        adapter.close()


@pytest.mark.parametrize("failure", ["offline", "content_type", "oversized", "invalid_image"])
def test_bad_snapshots_are_rejected_before_ocr(tmp_path, failure):
    def handler(request):
        if "/states/" in request.url.path:
            return httpx.Response(200, json={"state": "unavailable" if failure == "offline" else "idle"})
        return httpx.Response(
            200, content=b"x" * (2 * 1024 * 1024 + 1) if failure == "oversized" else b"invalid",
            headers={"content-type": "text/html" if failure == "content_type" else "image/jpeg"},
        )

    adapter = camera_adapter(tmp_path, handler)
    try:
        with pytest.raises(CameraError):
            adapter.snapshot("camera.meter")
    finally:
        adapter.close()


class FakeCamera:
    configured = True

    def __init__(self, data):
        self.data = data
        self.calls = 0
        self.error = None

    def snapshot(self, entity):
        self.calls += 1
        if self.error:
            raise CameraError(self.error)
        return self.data

    def close(self):
        pass


def attach(client, image, enabled=False):
    cfg = client.service.store.config("house")
    cfg.source = ImageSource(kind="home_assistant", camera_entity="camera.meter", enabled=enabled)
    client.service.save_config(cfg)
    return CameraCollector(client.service, FakeCamera(image))


def test_capture_dry_run_preserves_history_outbox_and_last_verified(client, image):
    collector = attach(client, image)
    before = client.service.summary()
    outbox = client.service.store.all("SELECT * FROM outbox")
    result = collector.capture("house")
    assert result["would_accept"]
    assert client.service.summary() == before
    assert client.service.store.all("SELECT * FROM outbox") == outbox
    assert collector.status("house")["status"] == "test_complete"


def test_capture_repeated_frame_survives_collector_restart(client, image):
    collector = attach(client, image)
    result = collector.capture("house", test_only=False)
    assert result["accepted"]
    assert result["details"]["capture"]["timestamp_basis"] == "home_assistant_retrieval"
    previous = client.service.state("house")["last_success"]
    restarted = CameraCollector(client.service, FakeCamera(image))
    with pytest.raises(CameraError, match="camera_repeated_frame"):
        restarted.capture("house", test_only=False)
    assert client.service.state("house")["last_success"] == previous
    assert len(client.service.store.all("SELECT * FROM readings")) == 2


def test_unreadable_and_unavailable_captures_preserve_total(client, image):
    collector = attach(client, image)
    client.pipeline.result = Recognition(None, 0, "unreadable")
    assert not collector.capture("house", test_only=False)["accepted"]
    collector.camera.error = "camera_unavailable"
    with pytest.raises(CameraError):
        collector.capture("house", test_only=False)
    assert client.service.state("house")["total_m3"] == "123.456000"


def test_scheduler_opt_in_interval_and_error_backoff(client, image, monkeypatch):
    collector = attach(client, image)
    collector.tick()
    assert collector.camera.calls == 0
    attach(client, image, enabled=True)
    collector.camera.error = "camera_unavailable"
    clock = [1000.0]
    monkeypatch.setattr("server.home_assistant.time.monotonic", lambda: clock[0])
    collector.tick()
    collector.tick()
    assert collector.camera.calls == 1
    clock[0] += 300
    collector.tick()
    assert collector.camera.calls == 2
    assert collector.status("house")["status"] == "camera_unavailable"


def test_configuration_change_during_network_request_is_not_processed(client, image):
    collector = attach(client, image)

    def changed(entity):
        cfg = client.service.store.config("house")
        cfg.decimal_places = 2
        client.service.save_config(cfg)
        return image

    collector.camera.snapshot = changed
    with pytest.raises(CameraError, match="configuration_changed_during_capture"):
        collector.capture("house", test_only=False)
    assert client.pipeline.calls == 0


def test_preview_and_capture_api_auth_defaults_and_mqtt_disabled(tmp_path, client, image):
    settings = Settings(tmp_path / "api-instance", "b" * 48)
    app = create_app(settings, pipeline=client.pipeline, start_background=False, ha_camera=FakeCamera(image))
    cfg = MeterConfig(id="house", name="House", source=ImageSource(kind="home_assistant", camera_entity="camera.meter"))
    app.state.service.save_config(cfg, create=True)
    with TestClient(app) as api:
        assert api.get("/api/meters/house/camera-image").status_code == 401
        api.headers["Authorization"] = "Bearer " + settings.admin_token
        assert api.get("/api/meters/house/camera-image").content == image
        assert api.post("/api/meters/house/capture").json()["reason"] == "baseline_required"
        assert app.state.service.store.all("SELECT * FROM readings") == []
        assert api.post("/api/meters/house/capture?test_only=false").status_code == 200
        assert api.get("/api/status").json()["mqtt"] == "disabled"


def test_ingress_peer_check_blocks_spoofed_forwarding_headers(tmp_path):
    settings = Settings(tmp_path, "a" * 48, ingress_mode=True)
    app = create_app(settings, start_background=False)
    with TestClient(app, client=("192.168.1.20", 1234)) as api:
        response = api.get("/api/meters", headers={"X-Forwarded-For": "172.30.32.2", "X-Remote-User-Id": "owner"})
        assert response.status_code == 403


def test_ingress_auth_and_frame_policy_allow_ha_sidebar(tmp_path):
    settings = Settings(tmp_path, "a" * 48, ingress_mode=True)
    app = create_app(settings, start_background=False)
    with TestClient(app, client=("172.30.32.2", 1234)) as api:
        assert api.get("/api/meters").status_code == 200
        assert api.get("/api/session").json() == {"authentication": "ingress"}
        response = api.get("/")
        assert "frame-ancestors 'self'" in response.headers["Content-Security-Policy"]
        assert 'src="static/app.js"' in response.text


def test_addon_observation_default_does_not_request_mqtt(tmp_path):
    def unexpected(request):
        raise AssertionError("No network request is allowed with MQTT disabled")

    settings = addon_settings(AddonOptions(), "test-token", tmp_path, httpx.MockTransport(unexpected))
    assert settings.ingress_mode and not settings.mqtt_host
    assert settings.home_assistant_url == "http://supervisor/core/api"
    assert "test-token" not in repr(settings)


def test_addon_mqtt_is_explicit_and_credentials_stay_in_memory(tmp_path):
    def handler(request):
        assert request.method == "GET" and request.url.path == "/services/mqtt"
        return httpx.Response(200, json={"result": "ok", "data": {
            "host": "core-mosquitto", "port": 1883, "username": "addon", "password": "test-broker-password",
        }})

    settings = addon_settings(
        AddonOptions(publish_to_mqtt=True), "test-token", tmp_path, httpx.MockTransport(handler),
    )
    assert settings.mqtt_password == "test-broker-password"
    assert list(tmp_path.iterdir()) == []


def test_source_and_connection_validation(tmp_path):
    with pytest.raises(ValidationError):
        ImageSource(kind="home_assistant")
    with pytest.raises(ValidationError):
        ImageSource(enabled=True)
    with pytest.raises(ValidationError):
        ImageSource(kind="home_assistant", camera_entity="camera.meter?token=bad")
    settings = Settings(tmp_path, "a" * 48)
    for url in ("https://user:pass@example.com/api", "https://example.com/api?token=bad", "file:///api"):
        with pytest.raises(ValueError):
            replace(settings, home_assistant_url=url, home_assistant_token="test").validate()
