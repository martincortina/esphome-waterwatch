from datetime import timedelta
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.models import ManualReading, MeterConfig, utcnow
from server.ocr import Recognition


class StubPipeline:
    """Deterministic adapter for API/validation tests; does not measure OCR accuracy."""

    engine = SimpleNamespace(available=True)

    def __init__(self):
        self.result = Recognition("123.457", 0.98, "candidate")
        self.calls = 0

    def run(self, data, cfg):
        self.calls += 1
        return Recognition(self.result.raw_value, self.result.confidence, self.result.reason)


@pytest.fixture
def image():
    frame = np.full((200, 400, 3), 230, dtype=np.uint8)
    cv2.putText(frame, "12345678", (10, 135), cv2.FONT_HERSHEY_SIMPLEX, 1.9, (20, 20, 20), 3)
    return cv2.imencode(".png", frame)[1].tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    pipeline = StubPipeline()
    settings = Settings(tmp_path, "a" * 48)
    app = create_app(settings, pipeline=pipeline, start_background=False)
    service = app.state.service
    camera_token = service.save_config(MeterConfig(id="house", name="House water"), create=True)
    frozen = utcnow() - timedelta(seconds=300)
    monkeypatch.setattr("server.service.utcnow", lambda: frozen)
    service.manual("house", ManualReading(value_m3="123.456", reason="Physical counter verified"))
    monkeypatch.setattr("server.service.utcnow", utcnow)
    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer " + settings.admin_token})
        c.camera_token = camera_token
        c.pipeline = pipeline
        c.service = service
        c.bridge = app.state.bridge
        yield c
