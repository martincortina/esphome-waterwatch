from datetime import datetime

from server.models import MeterConfig, micro_m3
from server.ocr import Recognition


def validate(candidate: Recognition, cfg: MeterConfig, captured_at, now, previous):
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        return None, "timestamp_timezone_required", None
    age = (now - captured_at).total_seconds()
    if age < -60:
        return None, "future_timestamp", None
    if age > cfg.max_upload_age_seconds:
        return None, "stale_capture", None
    if previous and captured_at <= datetime.fromisoformat(previous["captured_at"]):
        return None, "out_of_order", None
    if candidate.reason != "candidate" or candidate.raw_value is None:
        return None, candidate.reason, None
    if candidate.confidence < cfg.min_confidence:
        return None, "low_confidence", None
    try:
        value = micro_m3(candidate.raw_value)
    except ValueError:
        return None, "invalid_numeric_reading", None
    # First value always needs an explicit human baseline to avoid poisoning statistics.
    if previous is None:
        return None, "baseline_required", None
    delta = (value - previous["value_micro"]) / 1_000_000
    if delta < 0:
        return None, "decrease_requires_manual_event", None
    seconds = (captured_at - datetime.fromisoformat(previous["captured_at"])).total_seconds()
    if (
        delta > cfg.max_step_m3
        or delta > cfg.max_rate_lph / 1000 * seconds / 3600 + cfg.tolerance_m3
    ):
        return None, "implausible_increase", None
    # This is average flow over the interval, never instantaneous flow.
    rate = round(delta * 1000 * 3600 / seconds, 3) if seconds <= cfg.max_rate_gap_seconds else None
    if previous["source"] in ("replacement", "reset", "rollover"):
        rate = None  # avoid deriving flow across a meter lifecycle boundary
    return value, "accepted", rate
