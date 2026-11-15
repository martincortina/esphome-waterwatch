"""Policy regressions, not a claim of physical-meter accuracy."""
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig
from server.neural_ocr import NeuralOCRError, RowRecognition
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


def test_isolated_character_model_error_does_not_veto_qualified_wheel_transition():
    views = [view(2.4, .9994), view(2.4, .9996)]
    english = [('5', .8033), ('5', .7153)]
    latin = [('4', .4479), ('3', .4239), ('4', .6944)]
    results, detail = resolve_wheel(views, [(None, 0)]*2, english+latin, .8,
                                    model_groups=[english, latin])
    assert results == [('2', .9994), ('2', .9996)]
    assert detail['wheel_policy'] == 'transition_verified'
    assert detail['wheel_text_disagreements'] == ['5']
    assert detail['wheel_text_veto_policy'] == 'independent_model_agreement'
    # Neither lower phase support nor a carry-boundary position is relaxed.
    assert resolve_wheel([view(2.4, .79), views[1]], [], english+latin, .8,
                         model_groups=[english, latin])[0] == [(None, 0)]*2
    assert resolve_wheel([view(2.9), view(3.)], [], english+latin, .8,
                         model_groups=[english, latin])[0] == [(None, 0)]*2


def test_two_independent_character_models_can_still_veto_a_wrong_phase_class():
    english, latin = [('5', .96), ('5', .9)], [('5', .95), ('5', .91)]
    results, detail = resolve_wheel([view(2.4), view(2.5)], [(None, 0)]*2,
                                    english+latin, .8, model_groups=[english, latin])
    assert results == [(None, 0)]*2 and detail['wheel_policy'] == 'uncertain'


def test_pipeline_uses_independent_text_models_and_keeps_verified_transition_labels(image):
    class Text:
        def recognize_digits(self, patches, valid):
            return RowRecognition([[('1', .99), ('1', .98)], [(None, 0)]*2], patches[0], {
                'recognition_evidence': [
                    {'primary_recognitions': [('1', .99), ('1', .98)], 'assist_recognitions': [('1', .95)]},
                    {'primary_recognitions': [('5', .8033), ('5', .7153)], 'assist_recognitions': [('4', .6944)]}
                ]})

    class Phase:
        def recognize(self, patch):
            return [view(2.4, .9994), view(2.4, .9996)]

    cfg = configuration()
    cfg.last_decimal_mode = 'rolling'
    cfg.min_confidence = .8
    result = Pipeline(Text(), wheel_engine=Phase()).run(image, cfg)
    assert result.raw_value == '1.2'
    assert result.details['digits'][1]['possible_transition'] == ['2', '3']
    assert result.details['digits'][1]['wheel_text_disagreements'] == ['5']


def test_centered_digit_requires_actual_matching_text_not_phase_rounding_alone():
    views = [view(6.9), view(7.)]
    text = [("7", .97), ("7", .95)]
    values, detail = resolve_wheel(views, text, text, .85)
    assert values == text
    assert detail["wheel_policy"] == "centered_digit_verified"
    assert resolve_wheel(views, [("6", .99)]*2, [], .85)[0] == [(None, 0)]*2


def test_center_support_spans_both_sides_of_integer_without_boosting_text_score():
    logits = np.full(100, -30.)
    logits[49] = logits[50] = 0
    evidence = phase_evidence(logits)
    assert evidence["score"] == pytest.approx(.5)
    assert evidence["center_score"] == pytest.approx(1)
    text = [("5", .93), ("5", .94)]
    result, details = resolve_wheel([evidence]*2, text, text, .85)
    assert result == text and details["wheel_policy"] == "centered_digit_verified"
    weak = {**evidence, "center_score": .7}
    assert resolve_wheel([weak]*2, text, text, .85)[0] == [(None, 0)]*2


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


def test_explicit_rolling_mode_applies_to_a_higher_wheel_during_a_carry(image):
    class HigherText:
        def __init__(self):self.values=iter([('2', .4)]*2+[('7', .99)]*2)
        def digit(self, patch):return next(self.values)
    cfg=configuration()
    cfg.rois[0].recognition_mode='rolling'
    result=Pipeline(HigherText(),wheel_engine=Wheel()).run(image,cfg)
    assert result.raw_value=='7.7'
    assert result.details['digits'][0]['status']=='rolling_verified'
    assert result.details['digits'][1]['status']=='agreed'


def test_learned_wheel_uses_a_verified_pattern_and_rejects_an_unseen_pose(image):
    from server.models import VisualReference
    from server.ocr import decode,crop_digit
    from server.reference_ocr import ReferenceSample

    cfg=configuration();cfg.rois[1].recognition_mode='calibrated'
    cfg.rois[1].visual_references=[VisualReference(id='a'*32,value=9)]
    sample=ReferenceSample(crop_digit(decode(image),cfg.rois[1]),9,identifier='a'*32)
    result=Pipeline(TextEngine(),wheel_engine=Wheel(),recognition_loader=lambda c,i:[sample] if i==1 else []).run(image,cfg)
    assert result.raw_value=='1.9'
    assert result.details['digits'][1]['wheel_policy']=='calibrated_reference_verified'
    assert result.details['digits'][1]['wheel_model_recognitions']==[('7',.98)]*2
    missing=Pipeline(TextEngine(),wheel_engine=Wheel(),recognition_loader=lambda c,i:[]).run(image,cfg)
    assert missing.raw_value=='1.?'
    assert missing.details['digits'][1]['wheel_policy']=='calibration_required'
