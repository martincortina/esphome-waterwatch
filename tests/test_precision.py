from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig
from server.ocr import Pipeline


class RowEngine:
    def recognize_digits(self, patches, valid):
        return SimpleNamespace(results=[[("1", .98), ("1", .97)], [("0", .91), ("1", .92)]],
                               processed=np.zeros((96, 300, 3), np.uint8), details={})


def config(**values):
    return MeterConfig(id="house", name="Meter", decimal_places=1,
                       rois=[ROI(x=.02, y=.2, w=.12, h=.6), ROI(x=.2, y=.2, w=.12, h=.6)], **values)


def test_possible_wheel_transition_is_reported_but_never_guessed(image):
    result = Pipeline(RowEngine()).run(image, config())
    assert result.raw_value == "1.?" and result.reason == "ambiguous_digit"
    assert result.details["digits"][1]["possible_transition"] == ["0", "1"]
    assert result.confidence == 0


def test_lower_precision_keeps_all_boxes_and_uses_only_reliable_digits(image):
    cfg = config(ignored_decimal_digits=1)
    result = Pipeline(RowEngine()).run(image, cfg)
    assert result.raw_value == "1" and result.reason == "candidate"
    assert result.confidence == .97 and len(cfg.rois) == len(result.crops) == 2
    assert result.details["full_raw_value"] == "1.?"
    assert result.details["digits"][1]["used_for_reading"] is False
    assert result.details["resolution_litres"] == 1000


def test_used_digits_keep_the_same_quality_gates_when_lower_precision_is_selected(image):
    cfg = config(ignored_decimal_digits=1, min_sharpness=10000)
    assert Pipeline(RowEngine()).run(image, cfg).reason == "poor_image_quality"


def test_integer_digits_cannot_be_discarded():
    with pytest.raises(ValidationError, match="Only decimal digits"):
        config(ignored_decimal_digits=2)
