from datetime import timedelta

import pytest

from server.home_assistant import CameraCollector, CameraError
from server.models import ImageSource, MeterConfig, utcnow
from server.ocr import Recognition
from server.storage import Store
from tests.test_api import upload

BASE = "/api/meters/house/history"


def plan(client, scope="all", sequences=None):
    selection = {"scope": scope, "sequences": sequences or []}
    response = client.post(BASE + "/delete-preview", json=selection)
    assert response.status_code == 200
    preview = response.json()
    return {**selection, "confirm": "DELETE", "through_seq": preview["through_seq"],
            "expected_count": preview["count"]}


def test_deleting_latest_verified_log_keeps_counter_checks_and_removes_capture_images(client, image):
    at = utcnow()
    row = upload(client, when=at, data=image).json()
    for variant in ("overlay", "processed"):
        (client.service.images / f"{row['image_key']}-{variant}.jpg").write_bytes(image)
    keys = list(client.service.images.iterdir())
    assert len(keys) == 3 and row["accepted"]
    before = client.service.state("house")
    result = client.post(BASE + "/delete", json=plan(client, "selected", [row["seq"]]))
    assert result.status_code == 200 and result.json()["deleted"] == 1
    assert not list(client.service.images.iterdir())
    assert client.service.state("house")["total_m3"] == before["total_m3"]
    assert client.get(f"/api/meters/house/images/{row['event_id']}/original").status_code == 404
    assert row["seq"] not in [r["seq"] for r in client.get(BASE).json()]
    client.pipeline.result = Recognition("123.455", .99, "candidate")
    lower = upload(client, event="lower", when=at+timedelta(seconds=1)).json()
    assert lower["reason"] == "decrease_requires_manual_event"
    client.pipeline.result = Recognition("123.458", .99, "candidate")
    next_row = upload(client, event="higher", when=at+timedelta(seconds=2)).json()
    assert next_row["accepted"] and client.service.state("house")["total_m3"] == "123.458000"


def test_clear_log_preserves_counter_across_restart_and_does_not_resurrect_retries(client, image):
    at = utcnow()
    row = upload(client, event="original", when=at, data=image).json()
    assert client.post(BASE + "/delete", json=plan(client)).json()["deleted"] == 2
    assert client.get(BASE).json() == []
    assert client.get("/api/meters/house/logs").text == ""
    reopened = Store(client.service.settings.data_dir / "waterwatch.sqlite3")
    try:
        checkpoint = reopened.last_good("house")
        assert checkpoint["value_micro"] == 123457000 and checkpoint["seq"] == row["seq"]
        assert checkpoint["image_key"] is None and checkpoint["details"] == "{}"
    finally:
        reopened.close()
    calls = client.pipeline.calls
    retry = upload(client, event="original", when=at, data=image).json()
    assert retry == {"event_id": "original", "duplicate": True, "deleted": True}
    assert client.pipeline.calls == calls and client.get(BASE).json() == []
    assert upload(client, event="original", when=at, data=b"changed").status_code == 422
    assert client.get("/api/meters").json()[0]["state"]["total_m3"] == "123.457000"


def test_deletion_cutoff_keeps_new_captures_and_rejected_scope_keeps_verified_rows(client):
    preview = plan(client)
    new = upload(client, event="arrived-after-preview").json()
    assert client.post(BASE + "/delete", json=preview).json()["deleted"] == 1
    assert [r["seq"] for r in client.get(BASE).json()] == [new["seq"]]
    client.pipeline.result = Recognition("?", 0, "ambiguous_digit")
    bad = upload(client, event="rejected", when=utcnow()+timedelta(seconds=1)).json()
    assert not bad["accepted"]
    assert client.post(BASE + "/delete", json=plan(client, "rejected")).json()["deleted"] == 1
    assert [r["seq"] for r in client.get(BASE).json()] == [new["seq"]]


def test_delete_needs_admin_confirmation_and_matching_review_count(client):
    preview = plan(client)
    assert client.post(BASE + "/delete", json=preview, headers={"Authorization": "Bearer " + client.camera_token}).status_code == 401
    assert client.post(BASE + "/delete", json={k: v for k, v in preview.items() if k != "confirm"}).status_code == 422
    assert client.post(BASE + "/delete", json={**preview, "expected_count": 99}).status_code == 409
    assert len(client.get(BASE).json()) == 1
    assert client.post(BASE + "/delete", json=preview).status_code == 200
    assert client.post(BASE + "/delete", json=preview).status_code == 409


def test_log_deletion_cannot_cross_meter_boundaries(client):
    client.service.save_config(MeterConfig(id="other", name="Other"), create=True)
    seq = client.get(BASE).json()[0]["seq"]
    assert client.post("/api/meters/other/history/delete-preview", json={"scope": "selected", "sequences": [seq]}).status_code == 409
    response = client.post("/api/meters/other/history/delete", json={"scope": "all", "confirm": "DELETE", "through_seq": seq, "expected_count": 1})
    assert response.status_code == 409 and len(client.get(BASE).json()) == 1
    assert client.post(BASE + "/delete-preview", json={"scope": "selected", "sequences": [seq, seq]}).status_code == 422


def test_invalid_image_key_never_deletes_files_outside_the_capture_directory(client):
    victim = client.service.settings.data_dir / "victim-original.jpg"
    victim.write_bytes(b"keep")
    with client.service.store.transaction() as db:
        db.execute("UPDATE readings SET image_key='../victim'")
    assert client.post(BASE + "/delete", json=plan(client)).status_code == 200
    assert victim.read_bytes() == b"keep"


def test_clearing_legacy_camera_logs_does_not_lose_frozen_frame_detection(client, image):
    class Camera:
        def snapshot(self, entity):
            return image
    cfg = client.service.store.config("house")
    cfg.source = ImageSource(kind="home_assistant", camera_entity="camera.meter")
    client.service.save_config(cfg)
    CameraCollector(client.service, Camera()).capture("house", test_only=False)
    # Emulate an existing 0.8.0 database with no dedicated camera marker.
    with client.service.store.transaction() as db:
        db.execute("DELETE FROM camera_frames")
    assert client.post(BASE + "/delete", json=plan(client)).status_code == 200
    with pytest.raises(CameraError, match="camera_repeated_frame"):
        CameraCollector(client.service, Camera()).capture("house", test_only=False)
    assert client.get(BASE).json() == []
