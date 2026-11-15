import math

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from server.analog_ocr import recognize_dial
from server.models import ROI, DialConfig, MeterConfig
from server.neural_ocr import RowRecognition
from server.ocr import Pipeline


def dial(angle=136.8, color=(0, 0, 210), second=None, center=(80, 80)):
    image = np.full((160, 160, 3), 235, np.uint8)
    cv2.circle(image, center, 72, (60, 60, 60), 2)
    for degrees in range(0, 360, 36):
        a = math.radians(degrees)
        p = (round(center[0]+65*math.sin(a)), round(center[1]-65*math.cos(a)))
        cv2.circle(image, p, 2, (20, 20, 20), -1)
    for degrees in [angle] + ([] if second is None else [second]):
        a = math.radians(degrees)
        tip = (round(center[0]+61*math.sin(a)), round(center[1]-61*math.cos(a)))
        cv2.arrowedLine(image, center, tip, color, 6, tipLength=.25)
    cv2.circle(image, center, 10, color, -1)
    return image


@pytest.mark.parametrize("value", [n+.4 for n in range(10)])
@pytest.mark.parametrize("color", ["red", "dark"])
def test_pointer_all_ten_sectors_in_both_colors(value, color):
    patch = dial(value*36, (0, 0, 210) if color == "red" else (25, 25, 25))
    results, details = recognize_dial(patch, DialConfig(pointer_color=color))
    assert [d for d, _ in results] == [str(int(value))]*2
    assert min(s for _, s in results) > .85
    assert all(abs(v["phase"]-value) < .08 for v in details["analog_views"])


def test_zero_reference_and_counterclockwise_direction():
    results, _ = recognize_dial(dial(90-3.4*36), DialConfig(zero_angle_degrees=90, clockwise=False))
    assert [d for d, _ in results] == ["3", "3"]


@pytest.mark.parametrize("patch", [np.full((160, 160, 3), 200, np.uint8),
                                   np.full((160, 160, 3), (0, 0, 200), np.uint8),
                                   dial(140, second=320), dial(360*.0001)])
def test_blank_blob_opposite_pointers_and_wrap_boundary_reject(patch):
    results, _ = recognize_dial(patch, DialConfig())
    assert any(d is None or score < .8 for d, score in results)


def test_geometry_validation_and_small_crop_reject():
    with pytest.raises(ValidationError):
        DialConfig(center_x=.1)
    results, details = recognize_dial(dial()[:20, :20], DialConfig())
    assert results == [(None, 0)]*2 and details["analog_policy"] == "region_too_small"


class Text:
    def __init__(self):
        self.calls = []

    def recognize_digits(self, patches, valid):
        self.calls.append(len(patches))
        return RowRecognition([[('1', .99), ('1', .98)] for _ in patches],
                              np.full((150, 110, 3), 180, np.uint8), {})


def test_mixed_pipeline_keeps_analog_out_of_text_row_and_uses_its_own_region():
    frame = np.full((200, 400, 3), 200, np.uint8)
    frame[20:180, 220:380] = dial()
    cv2.putText(frame, "1", (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 3, (20, 20, 20), 5)
    data = cv2.imencode('.png', frame)[1].tobytes()
    cfg = MeterConfig(id="a", name="a", decimal_places=1,
                      rois=[ROI(x=.02, y=.1, w=.4, h=.8),
                            ROI(x=.55, y=.1, w=.4, h=.8, kind="analog")])
    engine = Text()
    result = Pipeline(engine).run(data, cfg)
    assert engine.calls == [1]
    assert result.raw_value == "1.3"
    assert result.details["digits"][1]["status"] == "analog_verified"
    # Preview-only dials cannot multiply or change the existing counter's precision.
    cfg.rois[-1].include_in_reading = False
    cfg.decimal_places = 0
    preview = Pipeline(Text()).run(data, cfg)
    assert preview.raw_value == "1"
    assert preview.details["digits"][-1]["used_for_reading"] is False


def test_analog_only_preview_has_no_numeric_counter():
    data = cv2.imencode('.png', dial())[1].tobytes()
    cfg = MeterConfig(id="a", name="a", rois=[ROI(x=0, y=0, w=1, h=1, kind="analog", include_in_reading=False)])
    engine = Text()
    result = Pipeline(engine).run(data, cfg)
    assert engine.calls == []
    assert result.raw_value is None and result.reason == "preview_only"


def test_two_sequential_dials_extend_the_counter_with_two_decimal_places():
    frame = np.hstack([np.full((160,160,3),200,np.uint8),dial(3.4*36),dial(4.4*36)])
    cv2.putText(frame,'1',(40,130),cv2.FONT_HERSHEY_SIMPLEX,3,(20,20,20),5)
    data = cv2.imencode('.png',frame)[1].tobytes()
    cfg = MeterConfig(id='a',name='a',decimal_places=2,rois=[
        ROI(x=0,y=0,w=1/3,h=1),
        ROI(x=1/3,y=0,w=1/3,h=1,kind='analog'),
        ROI(x=2/3,y=0,w=1/3,h=1,kind='analog')])
    result = Pipeline(Text()).run(data,cfg)
    assert result.raw_value == '1.34'
    assert result.confidence >= .8

