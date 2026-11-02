import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig
from server.ocr import Pipeline, align, decode, rotate


@pytest.mark.parametrize("degrees,expected", [
    (90, cv2.ROTATE_90_CLOCKWISE), (-90, cv2.ROTATE_90_COUNTERCLOCKWISE),
    (180, cv2.ROTATE_180), (-180, cv2.ROTATE_180),
])
def test_quarter_turns_preserve_pixels_and_direction(degrees, expected):
    image = np.arange(40 * 80 * 3, dtype=np.uint8).reshape(40, 80, 3)
    cfg = MeterConfig(id="meter", name="Meter", rotation_degrees=degrees)
    assert np.array_equal(align(image, cfg), cv2.rotate(image, expected))


def test_old_configuration_defaults_to_unrotated_image():
    cfg = MeterConfig.model_validate({"id": "meter", "name": "Meter"})
    image = np.zeros((40, 80, 3), dtype=np.uint8)
    assert cfg.rotation_degrees == 0
    assert align(image, cfg) is image


def test_straightening_expands_canvas_and_preserves_corner_marks():
    image = np.full((50, 100, 3), 255, np.uint8)
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 0, 0)]
    for (y, x), color in zip([(1, 1), (1, 91), (41, 1), (41, 91)], colors, strict=True):
        image[y:y+8, x:x+8] = color
    result = rotate(image, 45)
    assert result.shape == (107, 107, 3)
    assert (result[0, 0] == 255).all()
    for color in colors:
        assert np.any(np.all(result == color, axis=2)), f"Lost corner mark {color}"


def test_perspective_points_stay_in_source_coordinates():
    image = np.arange(40 * 80 * 3, dtype=np.uint8).reshape(40, 80, 3)
    cfg = MeterConfig(id="meter", name="Meter", aligned_width=120, aligned_height=100,
                      perspective=[(.1, .1), (.9, .1), (.9, .9), (.1, .9)])
    corrected = align(image, cfg)
    cfg.rotation_degrees = 90
    assert np.array_equal(align(image, cfg), cv2.rotate(corrected, cv2.ROTATE_90_CLOCKWISE))


@pytest.mark.parametrize("angle", [-181, 181, float("nan"), float("inf")])
def test_invalid_rotation_is_rejected(angle):
    with pytest.raises(ValidationError):
        MeterConfig(id="meter", name="Meter", rotation_degrees=angle)


def test_extreme_aspect_ratio_cannot_allocate_an_unbounded_rotated_frame():
    with pytest.raises(ValueError, match="megapixel limit"):
        rotate(np.zeros((40, 10000, 3), np.uint8), 45)


def test_ocr_crops_regions_after_rotation():
    upright = np.full((120, 240, 3), 255, np.uint8)
    cv2.putText(upright, "7", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    sideways = cv2.rotate(upright, cv2.ROTATE_90_COUNTERCLOCKWISE)
    data = cv2.imencode(".png", sideways)[1].tobytes()
    cfg = MeterConfig(id="meter", name="Meter", decimal_places=0, rotation_degrees=90,
                      rois=[ROI(x=.1, y=.1, w=.25, h=.5)])

    class Engine:
        def digit(self, patch):
            assert patch.min() < 100 and patch.max() > 200
            return "7", .98

    result = Pipeline(Engine()).run(data, cfg)
    assert result.raw_value == "7" and result.reason == "candidate"
    assert result.details["rotation_degrees"] == 90
    assert decode(result.overlay).shape == upright.shape
    cfg.rotation_degrees = 0
    assert Pipeline(Engine()).run(data, cfg).reason == "poor_image_quality"


def test_transform_preview_is_authenticated_and_does_not_save(client, image):
    path = "/api/meters/house/transform-image?rotation_degrees=90"
    assert client.post(path, content=image, headers={"Authorization": "Bearer wrong"}).status_code == 401
    history = client.service.store.all("SELECT * FROM readings")
    outbox = client.service.store.all("SELECT * FROM outbox")
    response = client.post(path, content=image, headers={"Content-Type": "image/png"})
    assert response.status_code == 200
    assert decode(response.content).shape[:2] == (400, 200)
    assert client.pipeline.calls == 0
    assert client.service.store.all("SELECT * FROM readings") == history
    assert client.service.store.all("SELECT * FROM outbox") == outbox
    assert client.service.store.config("house").rotation_degrees == 0


@pytest.mark.parametrize("angle", ["181", "nan", "inf"])
def test_transform_preview_rejects_invalid_angle(client, image, angle):
    response = client.post(f"/api/meters/house/transform-image?rotation_degrees={angle}", content=image)
    assert response.status_code == 422
