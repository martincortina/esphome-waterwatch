import base64

import cv2
import numpy as np
import pytest

from server.alignment import AlignmentError, register
from server.models import ROI, AlignmentConfig, MeterConfig, utcnow
from server.ocr import Pipeline, decode
from tests.test_api import upload


def reference_frame():
    image = np.full((240, 400, 3), 220, np.uint8)
    cv2.putText(image, "A", (35, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (10, 10, 10), 3)
    cv2.putText(image, "B", (300, 195), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (10, 10, 10), 3)
    cv2.putText(image, "7", (180, 145), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3)
    return image


def configuration():
    return MeterConfig(id="house", name="Meter", decimal_places=0,
                       rois=[ROI(x=.43, y=.35, w=.14, h=.3)],
                       alignment=AlignmentConfig(enabled=True, reference_id="a"*32,
                       anchors=[ROI(x=.07, y=.13, w=.15, h=.21),
                                ROI(x=.72, y=.65, w=.15, h=.21)]))


@pytest.mark.parametrize("angle,dx,dy", [(0, 9, -6), (1.5, 6, -4), (-1.5, -4, 5)])
def test_two_landmarks_compensate_small_camera_translation_and_rotation(angle, dx, dy):
    reference = reference_frame()
    matrix = cv2.getRotationMatrix2D((200, 120), angle, 1)
    matrix[:, 2] += (dx, dy)
    moved = cv2.warpAffine(reference, matrix, (400, 240), borderValue=(220, 220, 220))
    output, details = register(moved, reference, configuration())
    area = np.s_[85:155, 170:230]
    before = np.mean(abs(moved[area].astype(float)-reference[area]))
    after = np.mean(abs(output[area].astype(float)-reference[area]))
    assert after < before * .4
    assert details["status"] == "aligned" and min(m["score"] for m in details["landmarks"]) >= .85
    assert abs(details["rotation_degrees"]-angle) < .5


def test_missing_uniform_or_out_of_search_landmarks_fail_closed():
    reference = reference_frame()
    with pytest.raises(AlignmentError, match="reference_missing"):
        register(reference, None, configuration())
    with pytest.raises(AlignmentError, match="low_contrast"):
        register(reference, np.full_like(reference, 220), configuration())
    moved = cv2.warpAffine(reference, np.float32([[1, 0, 100], [0, 1, 0]]), (400, 240))
    with pytest.raises(AlignmentError, match="landmark_not_found"):
        register(moved, reference, configuration())


def test_repeated_reference_features_reject_ambiguous_match():
    reference = reference_frame()
    cfg = configuration()
    cfg.alignment.anchors[0] = ROI(x=.08, y=.18, w=.06, h=.10)
    template = reference[43:67, 32:56].copy()
    reference[43:67, 65:89] = template
    with pytest.raises(AlignmentError, match="ambiguous"):
        register(reference, reference, cfg)


def test_resolution_change_and_clipped_digit_do_not_reach_ocr():
    reference = reference_frame()
    with pytest.raises(AlignmentError, match="size_changed"):
        register(reference[:, :-1], reference, configuration())
    cfg = configuration()
    cfg.rois[0] = ROI(x=0, y=.3, w=.1, h=.3)
    moved = cv2.warpAffine(reference, np.float32([[1, 0, -10], [0, 1, 0]]), (400, 240))
    with pytest.raises(AlignmentError, match="digit_outside"):
        register(moved, reference, cfg)


def test_unmatched_alignment_abstains_without_calling_digit_engine():
    class Unused:
        def digit(self, patch):
            pytest.fail("OCR must not run after a failed alignment")

    pipeline = Pipeline(Unused(), reference_loader=lambda cfg: reference_frame())
    data = cv2.imencode(".png", np.full((240, 400, 3), 220, np.uint8))[1].tobytes()
    result = pipeline.run(data, configuration())
    assert result.reason == "alignment_landmark_not_found" and result.raw_value is None
    assert result.details["alignment"]["enabled"]


def test_reference_is_private_persistent_and_does_not_modify_ledger_or_config(client):
    data = cv2.imencode(".png", reference_frame())[1].tobytes()
    before = client.service.store.config("house")
    readings = client.service.store.all("SELECT * FROM readings")
    outbox = client.service.store.all("SELECT * FROM outbox")
    path = "/api/meters/house/alignment/reference"
    assert client.post(path, content=data, headers={"Authorization": "Bearer wrong"}).status_code == 401
    result = client.post(path, content=data)
    assert result.status_code == 200 and result.json()["width"] == 400
    cfg = configuration()
    cfg.alignment.reference_id = result.json()["reference_id"]
    assert client.service.store.config("house") == before
    assert client.put("/api/meters/house", json=cfg.model_dump()).status_code == 200
    assert np.array_equal(client.service.load_reference(cfg), reference_frame())
    assert client.get(path).content == data
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.service.store.all("SELECT * FROM readings") == readings
    assert client.service.store.all("SELECT * FROM outbox") == outbox
    cfg.rotation_degrees = 90
    assert client.put("/api/meters/house", json=cfg.model_dump()).status_code == 409
    cfg.rotation_degrees = 0
    cfg.id = "other"
    with pytest.raises(AlignmentError, match="missing"):
        client.service.load_reference(cfg)


def test_dry_run_returns_exact_test_photo_crops_and_scores_without_persistence(client, image):
    class Engine:
        def digit(self, patch):
            return "1", .92

    client.service.pipeline = Pipeline(Engine())
    cfg = MeterConfig(id="house", name="Meter", decimal_places=0,
                      rois=[ROI(x=.02, y=.2, w=.12, h=.6)])
    client.service.save_config(cfg)
    readings = client.service.store.all("SELECT * FROM readings")
    outbox = client.service.store.all("SELECT * FROM outbox")
    files = list(client.service.images.iterdir())
    result = client.post("/api/meters/house/test", params={"captured_at": utcnow().isoformat()}, content=image).json()
    assert base64.b64decode(result["images"]["original"]["base64"]) == image
    assert decode(base64.b64decode(result["images"]["aligned"]["base64"])).shape == (200, 400, 3)
    assert len(result["images"]["digits"]) == 1
    assert result["details"]["digits"][0]["status"] == "agreed"
    assert "highlight_fraction" in result["details"]["digits"][0]
    assert client.service.store.all("SELECT * FROM readings") == readings
    assert client.service.store.all("SELECT * FROM outbox") == outbox
    assert list(client.service.images.iterdir()) == files


def test_uniform_reference_cannot_be_enabled_or_imported(client):
    data = cv2.imencode(".png", np.full((240, 400, 3), 220, np.uint8))[1].tobytes()
    key = client.post("/api/meters/house/alignment/reference", content=data).json()["reference_id"]
    cfg = configuration()
    cfg.alignment.reference_id = key
    assert client.put("/api/meters/house", json=cfg.model_dump()).status_code == 409
    assert client.post("/api/config/import", json={"version": 1, "meters": [cfg.model_dump()]}).status_code == 422
    assert not client.service.store.config("house").alignment.enabled


def test_rotation_limit_is_enforced_after_landmarks_match():
    reference = reference_frame()
    cfg = configuration()
    cfg.alignment.max_angle_degrees = .2
    matrix = cv2.getRotationMatrix2D((200, 120), 1.5, 1)
    moved = cv2.warpAffine(reference, matrix, (400, 240), borderValue=(220, 220, 220))
    with pytest.raises(AlignmentError, match="movement_too_large"):
        register(moved, reference, cfg)


def test_quality_failure_reports_each_digit_without_guessing_or_running_ocr():
    class Unused:
        def digit(self, patch):
            pytest.fail("Bad quality crops must not be sent to OCR")

    cfg = MeterConfig(id="meter", name="Meter", decimal_places=0,
                      rois=[ROI(x=.1, y=.1, w=.2, h=.5), ROI(x=.5, y=.1, w=.2, h=.5)])
    data = cv2.imencode(".png", np.full((120, 200, 3), 255, np.uint8))[1].tobytes()
    result = Pipeline(Unused()).run(data, cfg)
    assert result.reason == "poor_image_quality" and result.confidence == 0
    assert len(result.crops) == len(result.details["digits"]) == 2
    assert all(d["status"] == "poor_image_quality" for d in result.details["digits"])


def test_recorded_images_are_opt_in_and_device_upload_response_stays_small(client, image):
    response = upload(client, event="small-device-response", data=image).json()
    assert "images" not in response and "calibration" not in response
    result = client.service.process("house", "desktop-diagnostics", utcnow(), image, include_images=True)
    assert base64.b64decode(result["images"]["original"]["base64"]) == image
    assert result["calibration"]["id"] == "house"
    row = client.service.store.one("SELECT details FROM readings WHERE event_id=?", ("desktop-diagnostics",))
    assert result["images"]["original"]["base64"] not in row["details"]
