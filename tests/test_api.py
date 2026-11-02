from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from server.models import MeterConfig, timestamp, utcnow
from server.ocr import Recognition


def upload(client, event="capture-1", when=None, data=b"stub-image", token=None):
    return client.post(
        "/api/device/house/images",
        params={"captured_at": timestamp(when or utcnow()), "event_id": event},
        content=data,
        headers={"Authorization": "Bearer " + (token or client.camera_token), "X-Wifi-Rssi": "-63"},
    )


def test_no_unauthenticated_admin_or_camera(client):
    assert client.get("/api/meters", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert upload(client, token="bad").status_code == 401
    assert client.get("/healthz", headers={"Authorization": ""}).status_code == 200
    assert client.get("/api/openapi.json", headers={"Authorization": ""}).status_code == 401


def test_camera_token_has_no_admin_permissions(client):
    assert (
        client.get(
            "/api/meters", headers={"Authorization": "Bearer " + client.camera_token}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/device/house/config", headers={"Authorization": "Bearer " + client.camera_token}
        ).json()["mode"]
        == "Docker"
    )
    assert client.post("/api/meters", json={"id": "garden", "name": "Garden"}).status_code == 201
    assert (
        client.get(
            "/api/device/garden/config", headers={"Authorization": "Bearer " + client.camera_token}
        ).status_code
        == 401
    )


def test_upload_validation_and_last_good_preserved(client):
    good = upload(client).json()
    assert good["accepted"] and good["value_m3"] == "123.457000"
    client.pipeline.result = Recognition(None, 0, "unreadable")
    bad = upload(client, "capture-2").json()
    assert not bad["accepted"] and bad["value_m3"] is None
    summary = client.get("/api/meters").json()[0]
    assert summary["state"]["total_m3"] == "123.457000"
    assert summary["latest"]["reason"] == "unreadable"
    assert summary["last_verified"]["event_id"] == "capture-1"
    assert summary["state"]["rate_lph"] is None


def test_idempotent_retry_and_conflicting_id(client):
    when = utcnow()
    first = upload(client, when=when)
    second = upload(client, when=when)
    assert first.status_code == 200 and second.json()["duplicate"]
    assert client.pipeline.calls == 1
    assert upload(client, when=when, data=b"different").status_code == 422


def test_out_of_order_capture_kept_for_diagnostics(client):
    now = utcnow()
    assert upload(client, when=now).json()["accepted"]
    result = upload(client, "older", when=now - timedelta(seconds=10)).json()
    assert result["reason"] == "out_of_order"
    assert client.service.state("house")["total_m3"] == "123.457000"


def test_max_upload_size_and_timestamp(client):
    assert upload(client, data=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    response = client.post(
        "/api/device/house/images?event_id=x&captured_at=2026-10-19T12:00:00",
        content=b"x",
        headers={"Authorization": "Bearer " + client.camera_token},
    )
    assert response.status_code == 422


def test_unsupported_mode_and_identity_change(client):
    cfg = client.service.store.config("house").model_dump()
    cfg["mode"] = "ESP32"
    assert client.put("/api/meters/house", json=cfg).status_code == 409
    cfg["mode"] = "Auto"
    assert client.put("/api/meters/house", json=cfg).status_code == 409
    cfg["id"] = "other"
    assert client.put("/api/meters/house", json=cfg).status_code == 422


def test_manual_events_audited_and_downward_correction_explicit(client):
    assert (
        client.post(
            "/api/meters/house/manual", json={"value_m3": "0", "reason": "Physical counter reset"}
        ).status_code
        == 422
    )
    result = client.post(
        "/api/meters/house/manual",
        json={"value_m3": "0", "kind": "replacement", "reason": "New physical meter fitted"},
    ).json()
    assert result["accepted"] and result["source"] == "replacement"
    assert len(client.get("/api/meters/house/history").json()) == 2
    assert client.service.state("house")["total_m3"] == "0.000000"


def test_dry_run_does_not_publish_or_write_history(client):
    before = client.service.store.one("SELECT count(*) AS n FROM readings")["n"]
    response = client.post(
        "/api/meters/house/test", params={"captured_at": timestamp(utcnow())}, content=b"stub-image"
    )
    assert response.status_code == 200 and response.json()["would_accept"]
    assert client.service.store.one("SELECT count(*) AS n FROM readings")["n"] == before


def test_token_rotation_invalidates_old_camera_token(client):
    new = client.post("/api/meters/house/rotate-token").json()["camera_token"]
    assert upload(client).status_code == 401
    assert upload(client, token=new).status_code == 200


def test_import_is_atomic_and_exports_no_secrets(client):
    exported = client.get("/api/config/export").json()
    assert client.camera_token not in str(exported)
    exported["meters"][0]["name"] = "Must not survive rollback"
    exported["meters"].append(MeterConfig(id="unregistered", name="Unregistered").model_dump())
    assert client.post("/api/config/import", json=exported).status_code == 422
    assert client.service.store.config("house").name == "House water"


def test_multiple_meters_remain_isolated(client):
    registered = client.post("/api/meters", json={"id": "garden", "name": "Garden"}).json()
    assert registered["camera_token"] != client.camera_token
    assert client.service.state("garden")["total_m3"] is None
    upload(client)
    assert client.get("/api/meters/garden/history").json() == []


def test_images_and_logs_require_auth(client):
    upload(client)
    assert (
        client.get(
            "/api/meters/house/images/capture-1/original", headers={"Authorization": "Bearer bad"}
        ).status_code
        == 401
    )
    assert client.get("/api/meters/house/images/capture-1/original").content == b"stub-image"
    assert "capture-1" in client.get("/api/meters/house/logs").text
    assert client.get("/api/meters/house/images/no-event/original").status_code == 404


def test_concurrent_duplicate_processes_once(client):
    when = utcnow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.service.process("house", "same", when, b"image"), range(2))
        )
    assert sum(bool(r.get("duplicate")) for r in results) == 1
    assert client.pipeline.calls == 1


def test_security_headers_and_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200 and "WaterWatch" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/static/app.js").status_code == 200


def test_manual_baseline_preserves_latest_capture_preview(client):
    upload(client)
    result = client.post(
        "/api/meters/house/manual",
        json={"value_m3": "123.457", "reason": "Current counter checked again"},
    )
    assert result.status_code == 200
    summary = client.get("/api/meters").json()[0]
    assert summary["latest"]["source"] == "correction"
    assert summary["latest_capture"]["event_id"] == "capture-1"
    assert summary["latest_capture"]["image_key"]
    assert summary["state"]["confidence"] is None  # Manual verification is not an OCR score.
