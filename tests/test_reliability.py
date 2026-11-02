import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from server.models import MeterConfig, utcnow
from server.ocr import Pipeline, Recognition
from server.service import Service


def test_last_known_good_survives_service_restart(client):
    replacement = Service(client.service.settings)
    try:
        assert replacement.state("house")["total_m3"] == "123.456000"
        assert replacement.store.one("SELECT count(*) AS n FROM outbox")["n"] == 1
    finally:
        replacement.store.close()


def test_stale_measurement_and_disconnected_camera(client, monkeypatch):
    now = utcnow()
    client.service.process("house", "capture", now, b"image", -60)
    monkeypatch.setattr("server.service.utcnow", lambda: now + timedelta(seconds=1300))
    state = client.service.state("house")
    assert state["stale"] and not state["camera_online"]
    assert state["total_m3"] == "123.457000" and state["rate_lph"] is None


def test_day_of_samples_is_monotonic_and_mqtt_queue_bounded(client, monkeypatch):
    start = utcnow()
    total = Decimal("123.456")
    for index in range(288):
        now = start + timedelta(seconds=index * 300)
        monkeypatch.setattr("server.service.utcnow", lambda now=now: now)
        total += Decimal(".001")
        client.pipeline.result = Recognition(str(total), 0.99, "candidate")
        row = client.service.process("house", f"day-{index}", now, b"image")
        assert row["accepted"], row["reason"]
    assert client.service.state("house")["total_m3"] == "123.744000"
    assert client.service.store.one("SELECT count(*) AS n FROM outbox")["n"] == 1
    assert len(client.service.store.all("SELECT seq FROM readings WHERE accepted=1")) == 289


def test_recorded_degraded_fixtures_rejected_before_inference():
    root = Path(__file__).parent / "fixtures"

    class NoInference:
        def digit(self, patch):
            raise AssertionError("Quality rejection must happen before inference")

    for item in json.loads((root / "manifest.json").read_text()):
        if item["category"] in ("dark", "blurred"):
            result = Pipeline(NoInference()).run(
                (root / item["file"]).read_bytes(), MeterConfig.model_validate(item["config"])
            )
            assert result.reason == "poor_image_quality"
