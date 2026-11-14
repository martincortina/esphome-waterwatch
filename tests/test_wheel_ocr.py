"""Policy regressions, not a claim of physical-meter accuracy."""
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig
from server.neural_ocr import NeuralOCRError
from server.ocr import Pipeline
from server.wheel_ocr import WheelOCREngine, phase_evidence, resolve_wheel


def view(phase, score=.98):
    return {"phase": phase, "completed_digit": str(int(phase)), "score": score}


def test_phase_scores_use_all_100_logits_without_fabricating_top_class_confidence():
    logits = np.full(100, -30.)
    logits[74:77] = 0
    evidence = phase_evidence(logits)
    assert evidence["phase"] == 7.4
    assert evidence["score"] == pytest.approx(1)
    for bad in [np.zeros(99), np.full(100, np.nan), np.full(100, np.inf)]:
        with pytest.raises(NeuralOCRError):
            phase_evidence(bad)


@pytest.mark.parametrize("digit", range(10))
def test_mid_transition_uses_completed_digit_including_nine_to_zero(digit):
    following = str((digit+1) % 10)
    raw = [(str(digit), .94), (following, .95)]
    values, detail = resolve_wheel([view(digit+.4), view(digit+.5)], [(None, 0)]*2, raw, .85)
    assert values == [(str(digit), .98)]*2
    assert detail["possible_transition"] == [str(digit), following]
    assert detail["wheel_policy"] == "transition_verified"


@pytest.mark.parametrize("phases", [(7.1, 7.1), (7.2, 7.2), (7.8, 7.8), (7.9, 8.),
                                   (7.3, 7.6), (7.4, 8.4), (9.9, 0.)])
def test_uncertain_carry_or_disagreeing_views_cannot_be_forced(phases):
    result, detail = resolve_wheel([view(p) for p in phases], [(None, 0)]*2, [], .85)
    assert result == [(None, 0)]*2
    assert detail["wheel_policy"] == "uncertain"


def test_low_support_or_nonadjacent_high_score_text_rejects_transition():
    for views, raw in [([view(7.4, .84), view(7.5)], []),
                       ([view(7.4), view(7.5)], [("3", .96)])]:
        assert resolve_wheel(views, [(None, 0)]*2, raw, .85)[0] == [(None, 0)]*2


def test_centered_digit_requires_actual_matching_text_not_phase_rounding_alone():
    views = [view(6.9), view(7.)]
    text = [("7", .97), ("7", .95)]
    values, detail = resolve_wheel(views, text, text, .85)
    assert values == text
    assert detail["wheel_policy"] == "centered_digit_verified"
    assert resolve_wheel(views, [("6", .99)]*2, [], .85)[0] == [(None, 0)]*2


class TextEngine:
    def __init__(self):
        self.values = iter([("1", .99), ("1", .99), ("7", .95), ("8", .96)])

    def digit(self, patch):
        return next(self.values)


class Wheel:
    def recognize(self, patch):
        return [view(7.4), view(7.5)]


def configuration():
    return MeterConfig(id="meter", name="Meter", decimal_places=1,
                       rois=[ROI(x=.02, y=.2, w=.12, h=.6), ROI(x=.15, y=.2, w=.12, h=.6)])


def test_pipeline_only_applies_opt_in_phase_model_to_last_decimal(image):
    cfg = configuration()
    strict = Pipeline(TextEngine(), wheel_engine=Wheel()).run(image, cfg)
    assert strict.raw_value == "1.?" and strict.reason == "ambiguous_digit"
    cfg.last_decimal_mode = "rolling"
    rolling = Pipeline(TextEngine(), wheel_engine=Wheel()).run(image, cfg)
    assert rolling.raw_value == "1.7" and rolling.confidence == .98
    assert rolling.details["digits"][0]["status"] == "agreed"
    assert rolling.details["digits"][1]["status"] == "rolling_verified"
    assert rolling.reason == "candidate"


def test_missing_model_is_visible_and_does_not_fall_back_to_a_guessed_digit(image, tmp_path):
    cfg = configuration()
    cfg.last_decimal_mode = "rolling"
    result = Pipeline(TextEngine(), wheel_engine=WheelOCREngine(tmp_path)).run(image, cfg)
    assert result.raw_value == "1.?"
    assert result.details["digits"][-1]["wheel_policy"] == "unavailable"
    cfg.ignored_decimal_digits = 1
    coarse = Pipeline(TextEngine(), wheel_engine=WheelOCREngine(tmp_path)).run(image, cfg)
    assert coarse.raw_value == "1" and coarse.confidence == .99
    assert coarse.details["digits"][-1]["used_for_reading"] is False


def test_model_configuration_is_backward_compatible_and_requires_decimal_wheel():
    assert configuration().last_decimal_mode == "text"
    with pytest.raises(ValidationError):
        MeterConfig(id="a", name="a", decimal_places=0, last_decimal_mode="rolling")


def test_preview_dial_after_rolling_wheel_cannot_shift_its_position_or_scale(image):
    cfg = configuration()
    cfg.last_decimal_mode = "rolling"
    cfg.rois.append(ROI(x=.3, y=.2, w=.12, h=.6, kind="analog", include_in_reading=False))
    result = Pipeline(TextEngine(), wheel_engine=Wheel()).run(image, cfg)
    assert result.raw_value == "1.7" and result.confidence == .98
    assert result.details["digits"][1]["status"] == "rolling_verified"
    assert result.details["digits"][2]["used_for_reading"] is False
