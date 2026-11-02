import subprocess

import cv2
import numpy as np
import pytest

from server.models import ROI, MeterConfig
from server.ocr import Pipeline, TesseractEngine, align, decode


class Engine:
    def __init__(self, results):
        self.results = iter(results)

    def digit(self, patch):
        return next(self.results)


def config():
    return MeterConfig(
        id="a", name="Meter", decimal_places=0, rois=[ROI(x=0.02, y=0.2, w=0.12, h=0.6)]
    )


def test_decode_limits_and_invalid_input(image):
    assert decode(image).shape == (200, 400, 3)
    with pytest.raises(ValueError):
        decode(b"not-an-image")


def test_empty_roi_rejects_without_running_ocr(image):
    assert (
        Pipeline(Engine([])).run(image, MeterConfig(id="a", name="a")).reason
        == "roi_not_configured"
    )


def test_consensus_recognizes_one_digit(image):
    r = Pipeline(Engine([("1", 0.97), ("1", 0.99)])).run(image, config())
    assert r.raw_value == "1" and r.confidence == 0.97 and r.reason == "candidate"
    assert r.overlay and r.processed


def test_disagreeing_digit_transition_is_rejected(image):
    r = Pipeline(Engine([("1", 0.99), ("2", 0.99)])).run(image, config())
    assert r.raw_value == "?" and r.confidence == 0 and r.reason == "ambiguous_digit"


@pytest.mark.parametrize("level", [0, 255])
def test_dark_and_overexposed_images_rejected(level):
    data = cv2.imencode(".png", np.full((200, 400, 3), level, np.uint8))[1].tobytes()
    assert Pipeline(Engine([])).run(data, config()).reason == "poor_image_quality"


def test_blurred_image_rejected(image):
    frame = decode(image)
    blur = cv2.GaussianBlur(frame, (81, 81), 20)
    data = cv2.imencode(".png", blur)[1].tobytes()
    assert Pipeline(Engine([])).run(data, config()).reason == "poor_image_quality"


def test_perspective_rectification(image):
    cfg = config()
    cfg.perspective = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    result = align(decode(image), cfg)
    assert result.shape[:2] == (600, 800)


def test_engine_failure_is_an_error_not_zero(image):
    class Broken:
        def digit(self, patch):
            raise subprocess.TimeoutExpired("tesseract", 8)

    r = Pipeline(Broken()).run(image, config())
    assert r.reason == "ocr_engine_failure" and r.raw_value is None


@pytest.mark.skipif(
    not TesseractEngine().available,
    reason="Native Tesseract not installed; included in Docker image",
)
def test_real_tesseract_adapter():
    patch = np.full((160, 120), 255, np.uint8)
    cv2.putText(patch, "7", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 3, (0,), 5)
    digit, confidence = TesseractEngine().digit(patch)
    assert digit == "7" and 0 <= confidence <= 1
