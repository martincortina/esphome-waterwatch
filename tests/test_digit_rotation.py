import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig
from server.ocr import Pipeline, crop_digit, decode, digit_corners


def asymmetric_patch():
    patch = np.full((80, 40, 3), 230, np.uint8)
    cv2.putText(patch, "7", (3, 57), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3)
    patch[3:10, 3:10] = (0, 30, 255)
    patch[65:75, 25:35] = (255, 80, 0)
    return patch


def centered_roi(angle=0):
    return ROI(x=140/320, y=80/240, w=40/320, h=80/240, rotation_degrees=angle)


@pytest.mark.parametrize("angle,turn", [(90, cv2.ROTATE_90_CLOCKWISE),
                                       (-90, cv2.ROTATE_90_COUNTERCLOCKWISE),
                                       (180, cv2.ROTATE_180)])
def test_individual_rotation_rectifies_clockwise_box_without_rotating_other_pixels(angle, turn):
    patch = asymmetric_patch()
    rotated = cv2.rotate(patch, turn)
    image = np.full((240, 320, 3), 230, np.uint8)
    h, w = rotated.shape[:2]
    image[120-h//2:120+h//2, 160-w//2:160+w//2] = rotated
    original = image.copy()
    assert np.array_equal(crop_digit(image, centered_roi(angle)), patch)
    assert np.array_equal(image, original)


@pytest.mark.parametrize("angle", [-23.5, 13.2])
def test_fine_angle_crop_keeps_digit_upright(angle):
    patch = asymmetric_patch()
    image = np.full((240, 320, 3), 230, np.uint8)
    image[80:160, 140:180] = patch
    matrix = cv2.getRotationMatrix2D((159.5, 119.5), -angle, 1)
    image = cv2.warpAffine(image, matrix, (320, 240), flags=cv2.INTER_CUBIC,
                           borderValue=(230, 230, 230))
    restored = crop_digit(image, centered_roi(angle))
    assert restored.shape == patch.shape
    assert np.mean(np.abs(restored.astype(float)-patch.astype(float))) < 5


def test_old_boxes_default_to_zero_and_keep_exact_original_crop():
    roi = ROI(x=.1, y=.2, w=.3, h=.4)
    image = np.arange(100*200*3, dtype=np.uint8).reshape(100, 200, 3)
    assert roi.rotation_degrees == 0
    assert np.array_equal(crop_digit(image, roi), image[20:60, 20:80])


@pytest.mark.parametrize("angle", [-181, 181, float("nan"), float("inf")])
def test_invalid_digit_angles_rejected(angle):
    with pytest.raises(ValidationError):
        ROI(x=.2, y=.2, w=.1, h=.4, rotation_degrees=angle)


def test_outside_rotated_box_rejects_before_ocr():
    class UnusedEngine:
        def digit(self, patch):
            pytest.fail("An out-of-frame digit must not reach OCR")

    roi = ROI(x=0, y=0, w=.2, h=.6, rotation_degrees=45)
    cfg = MeterConfig(id="meter", name="Meter", decimal_places=0, rois=[roi])
    data = cv2.imencode(".png", np.full((120, 200, 3), 230, np.uint8))[1].tobytes()
    result = Pipeline(UnusedEngine()).run(data, cfg)
    assert result.reason == "roi_outside_image" and result.raw_value is None


def test_ocr_receives_same_upright_variants_after_per_digit_rectification():
    class InspectEngine:
        def __init__(self):
            self.patches = []

        def digit(self, patch):
            self.patches.append(patch.copy())
            return "7", .99

    patch = asymmetric_patch()
    upright = np.full((240, 320, 3), 230, np.uint8)
    upright[80:160, 140:180] = patch
    sideways = np.full_like(upright, 230)
    sideways[100:140, 120:200] = cv2.rotate(patch, cv2.ROTATE_90_CLOCKWISE)
    engines = [InspectEngine(), InspectEngine()]
    for image, angle, engine in zip([upright, sideways], [0, 90], engines, strict=True):
        cfg = MeterConfig(id="meter", name="Meter", decimal_places=0, rois=[centered_roi(angle)])
        result = Pipeline(engine).run(cv2.imencode(".png", image)[1].tobytes(), cfg)
        assert result.raw_value == "7" and result.reason == "candidate"
        assert result.details["digits"][0]["rotation_degrees"] == angle
        assert decode(result.overlay).shape == image.shape
    for left, right in zip(engines[0].patches, engines[1].patches, strict=True):
        assert np.array_equal(left, right)


def test_digit_angles_roundtrip_without_changing_global_angle_or_reading_history(client):
    cfg = MeterConfig(id="house", name="House water", decimal_places=0,
                      rois=[centered_roi(-7.5), centered_roi(90)])
    history = client.service.store.all("SELECT * FROM readings")
    outbox = client.service.store.all("SELECT * FROM outbox")
    assert client.put("/api/meters/house", json=cfg.model_dump()).status_code == 200
    stored = client.get("/api/meters").json()[0]["config"]
    assert [roi["rotation_degrees"] for roi in stored["rois"]] == [-7.5, 90]
    assert stored["rotation_degrees"] == 0
    assert client.service.store.all("SELECT * FROM readings") == history
    assert client.service.store.all("SELECT * FROM outbox") == outbox
    assert client.pipeline.calls == 0


def test_rotated_pixel_geometry_uses_image_aspect_ratio():
    corners = digit_corners(centered_roi(90), 320, 240)
    assert np.allclose(corners, [[200, 100], [200, 140], [120, 140], [120, 100]])


def test_extreme_input_dimensions_cannot_crash_opencv_digit_transform():
    image = np.zeros((32, 50000, 3), np.uint8)
    roi = ROI(x=.4, y=.3, w=.0002, h=.4, rotation_degrees=10)
    with pytest.raises(ValueError, match="roi_transform_too_large"):
        crop_digit(image, roi)
