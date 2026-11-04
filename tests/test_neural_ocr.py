from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from server.models import ROI, MeterConfig
from server.neural_ocr import (
    MODEL_NAME,
    NeuralOCREngine,
    NeuralOCRError,
    counter_strip,
    decode_cells,
    model_input,
)
from server.ocr import Pipeline, inner_frame


def emissions(tokens, classes=12):
    result = np.full((1, len(tokens), classes), .001, np.float32)
    for time, token in enumerate(tokens):
        result[0, time, token] = 1-.001*(classes-1)
    return result


ALPHABET = ["", *"0123456789", "X"]


def test_ctc_keeps_identical_adjacent_digits_separate_and_uses_raw_scores():
    scores = emissions([0, 1, 1, 0, 1, 1, 0, 3, 3, 0, 0, 0])
    values, text = decode_cells(scores, ALPHABET, [(0, 40), (40, 80), (80, 120)], 120, 120, 120)
    assert text == "002"
    assert [x[0] for x in values] == list("002")
    assert values[0][1] == pytest.approx(float(scores[0, 1, 1]))


def test_a_missing_digit_does_not_shift_the_remaining_cells():
    scores = emissions([0, 1, 1, 0, 0, 0, 0, 0, 3, 3, 0, 0])
    values, _ = decode_cells(scores, ALPHABET, [(0, 40), (40, 80), (80, 120)], 120, 120, 120)
    assert [x[0] for x in values] == ["0", None, "2"]


def test_non_digits_and_multiple_symbols_in_one_cell_are_rejected():
    scores = emissions([0, 1, 0, 2, 0, 11, 11, 0, 3, 3, 0, 0])
    values, _ = decode_cells(scores, ALPHABET, [(0, 40), (40, 80), (80, 120)], 120, 120, 120)
    assert [x[0] for x in values] == [None, None, "2"]


def test_predictions_in_right_padding_cannot_turn_into_an_extra_digit():
    scores = emissions([0, 1, 1, 0, 0, 0, 0, 0, 3, 3, 0, 0])
    values, _ = decode_cells(scores, ALPHABET, [(0, 40), (40, 80)], 80, 80, 120)
    assert values == [(None, 0), (None, 0)]


@pytest.mark.parametrize("invalid", [float("nan"), -1, 1.1])
def test_invalid_probabilities_fail_closed(invalid):
    scores = emissions([0, 1, 0])
    scores[0, 1, 1] = invalid
    with pytest.raises(NeuralOCRError):
        decode_cells(scores, ALPHABET, [(0, 20)], 20, 20, 20)


def test_inner_frame_preserves_the_outer_crop_and_uses_the_same_centre():
    outer = ROI(x=.2, y=.3, w=.2, h=.3, rotation_degrees=-17)
    inner = inner_frame(outer)
    assert inner.w == pytest.approx(.12) and inner.h == pytest.approx(.18)
    assert inner.rotation_degrees == -17 and outer.w == .2
    assert inner.x+inner.w/2 == pytest.approx(outer.x+outer.w/2)


def test_bad_quality_cells_are_blanked_without_collapsing_their_positions():
    patches = [np.full((40, 30, 3), value, np.uint8) for value in (70, 120, 170)]
    strip, columns = counter_strip(patches, 1.4, [True, False, True])
    left, right = columns[1]
    assert not strip[:, left:right].any()
    assert len(columns) == 3 and strip[:, columns[2][0]:].max() == 170
    tensor, resized, padded = model_input(strip)
    assert tensor.dtype == np.float32 and tensor.shape == (1, 3, 48, padded)
    assert resized <= padded and tensor.min() >= -1 and tensor.max() <= 1


def test_missing_or_modified_weights_cannot_silently_fall_back(tmp_path):
    engine = NeuralOCREngine(tmp_path)
    assert not engine.available
    with pytest.raises(NeuralOCRError):
        engine.recognize_digits([np.zeros((40, 30, 3), np.uint8)])
    (tmp_path / MODEL_NAME).write_bytes(b"changed weights")
    with pytest.raises(NeuralOCRError):
        engine.recognize_digits([np.zeros((40, 30, 3), np.uint8)])


def test_extremely_wide_digit_frames_are_rejected_before_large_model_allocations():
    with pytest.raises(NeuralOCRError):
        counter_strip([np.zeros((8, 3000, 3), np.uint8)], 1.6)


def test_pipeline_retains_ambiguity_instead_of_recording_a_contextual_guess(image):
    class RowEngine:
        def recognize_digits(self, patches, valid):
            assert len(patches) == 2 and all(valid)
            return SimpleNamespace(results=[[("1", .98), ("1", .97)], [("1", .3), ("4", .35)]],
                                   processed=np.zeros((96, 300, 3), np.uint8), details={"model": "test"})
    cfg = MeterConfig(id="a", name="Meter", decimal_places=1,
                      rois=[ROI(x=.02, y=.2, w=.12, h=.6), ROI(x=.2, y=.2, w=.12, h=.6)])
    result = Pipeline(RowEngine()).run(image, cfg)
    assert result.raw_value == "1.?" and result.reason == "ambiguous_digit"
    assert result.confidence == 0
    assert result.details["digits"][1]["status"] == "ambiguous"


def test_all_poor_quality_cells_skip_neural_inference():
    class Unused:
        def recognize_digits(self, *args):
            pytest.fail("Poor quality images must not reach the model")
    data = cv2.imencode(".png", np.zeros((100, 100, 3), np.uint8))[1].tobytes()
    cfg = MeterConfig(id="a", name="A", decimal_places=0, rois=[ROI(x=.1, y=.1, w=.8, h=.8)])
    assert Pipeline(Unused()).run(data, cfg).reason == "poor_image_quality"
