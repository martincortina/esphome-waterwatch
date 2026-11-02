from datetime import timedelta

import pytest
from pydantic import ValidationError

from server.models import ROI, MeterConfig, m3, micro_m3, timestamp, utcnow
from server.ocr import Recognition
from server.validation import validate


@pytest.mark.parametrize(
    "raw,expected",
    [("0", 0), ("00123.456", 123456000), ("0.000001", 1), ("999999999.999999", 999999999999999)],
)
def test_exact_decimal_roundtrip(raw, expected):
    assert micro_m3(raw) == expected
    assert micro_m3(m3(expected)) == expected


@pytest.mark.parametrize(
    "raw", ["", "-1", "NaN", "Infinity", "1e3", "0.1234567", "12?3", "1,234", "1.2.3", " 1"]
)
def test_invalid_numbers_never_become_zero(raw):
    with pytest.raises(ValueError):
        micro_m3(raw)


def check(value="123.457", confidence=0.99, reason="candidate", seconds=0, previous=True):
    now = utcnow()
    prev = (
        {
            "value_micro": 123456000,
            "captured_at": timestamp(now - timedelta(seconds=300)),
            "source": "Docker",
        }
        if previous
        else None
    )
    return validate(
        Recognition(value, confidence, reason),
        MeterConfig(id="house", name="House"),
        now + timedelta(seconds=seconds),
        now,
        prev,
    )


def test_valid_increment_and_interval_flow():
    assert check() == (123457000, "accepted", 12.0)


def test_zero_is_readable_but_not_a_substitute_for_missing():
    assert check("0")[1] == "decrease_requires_manual_event"
    assert check(None, reason="unreadable")[0] is None


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"confidence": 0.4}, "low_confidence"),
        ({"value": "123.4?7", "reason": "ambiguous_digit"}, "ambiguous_digit"),
        ({"value": "123.455"}, "decrease_requires_manual_event"),
        ({"value": "999.000"}, "implausible_increase"),
        ({"seconds": -300}, "out_of_order"),
        ({"seconds": -4000}, "stale_capture"),
        ({"seconds": 120}, "future_timestamp"),
        ({"previous": False}, "baseline_required"),
    ],
)
def test_invalid_candidates_are_rejected(kwargs, reason):
    assert check(**kwargs)[1] == reason


def test_unbounded_gap_does_not_create_instantaneous_flow():
    now = utcnow()
    prev = {
        "value_micro": 1000000,
        "captured_at": timestamp(now - timedelta(hours=2)),
        "source": "Docker",
    }
    value, reason, rate = validate(
        Recognition("1.010", 0.99, "candidate"), MeterConfig(id="a", name="a"), now, now, prev
    )
    assert value == 1010000 and reason == "accepted" and rate is None


def test_timezone_is_required():
    with pytest.raises(ValueError):
        timestamp(utcnow().replace(tzinfo=None))


def test_roi_and_layout_bounds():
    with pytest.raises(ValidationError):
        ROI(x=0.9, y=0, w=0.2, h=0.2)
    with pytest.raises(ValidationError):
        MeterConfig(id="../escape", name="bad")
    with pytest.raises(ValidationError):
        MeterConfig(id="a", name="a", rois=[ROI(x=0, y=0, w=0.1, h=0.1)], decimal_places=3)
    with pytest.raises(ValidationError):
        MeterConfig(id="a", name="a", perspective=[(0, 0), (1, 1), (1, 0), (0, 1)])


def test_nonfinite_threshold_rejected():
    with pytest.raises(ValidationError):
        MeterConfig(id="a", name="a", max_rate_lph=float("inf"))
