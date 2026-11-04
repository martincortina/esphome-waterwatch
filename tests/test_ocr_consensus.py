import numpy as np
import pytest

from server.neural_ocr import counter_strip, select_evidence


def test_two_assisting_views_must_agree_and_their_raw_scores_are_not_rescaled():
    primary = [(None, 0), (None, 0)]
    assist = [("1", .91), ("1", .94), ("1", .73)]
    selected, sources, policy = select_evidence(primary, assist)
    assert selected == [("1", .94), ("1", .91)]
    assert sources == ["Latin · green contrast", "Latin · neutral contrast"]
    assert policy == "assisted_consensus"


def test_one_strong_assisted_prediction_cannot_invent_a_missing_digit():
    primary = [(None, 0), (None, 0)]
    selected, _, policy = select_evidence(primary, [("1", .99), (None, 0), (None, 0)])
    assert selected == primary and policy == "primary_views"


def test_a_weak_consensus_remains_weak_for_the_configured_confidence_gate():
    selected, _, _ = select_evidence([(None, 0)]*2, [("1", .72), ("1", .61), (None, 0)])
    assert min(score for digit, score in selected) == .61


def test_conflicting_raw_numeric_views_abstain_even_if_assistance_looks_confident():
    selected, _, policy = select_evidence([("1", .32), ("4", .35)], [("0", .99)]*3)
    assert selected == [(None, 0), (None, 0)] and policy == "raw_numeric_conflict"


@pytest.mark.parametrize("primary", [[("6", .99), (None, 0)], [("6", .7), ("6", .72)]])
def test_incompatible_numeric_evidence_is_not_overridden_by_a_higher_score(primary):
    selected, _, policy = select_evidence(primary, [("1", .99)]*3)
    assert selected == [(None, 0), (None, 0)] and policy == "model_conflict"


def test_a_strong_contradictory_assist_view_also_prevents_promotion():
    selected, _, policy = select_evidence([("7", .99), ("7", .98)], [("7", .9), ("7", .9), ("3", .99)])
    assert selected == [(None, 0), (None, 0)] and policy == "model_conflict"


def test_existing_strong_recognition_survives_inconclusive_assistance():
    primary = [("7", .99), ("7", .98)]
    selected, _, policy = select_evidence(primary, [(None, 0), ("3", .4), (None, 0)])
    assert selected == primary and policy == "primary_views"


def test_two_competing_assistance_quorums_cannot_fall_back_to_an_unrelated_digit():
    selected, _, policy = select_evidence([("7", .99)]*2, [("1", .99)]*2+[("2", .99)]*2)
    assert selected == [(None, 0), (None, 0)] and policy == "model_conflict"


def test_contrast_views_do_not_make_low_quality_cells_visible_to_the_model():
    patches = [np.full((40, 30, 3), value, np.uint8) for value in (70, 120, 170)]
    for normalization in ("neutral", "green", "luma"):
        row, columns = counter_strip(patches, 1.2, [True, False, True], normalization)
        left, right = columns[1]
        assert not row[:, left:right].any()
