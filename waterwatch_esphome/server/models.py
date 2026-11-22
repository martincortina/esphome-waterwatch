import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")]
MAX_VISUAL_REFERENCES = 64


def utcnow() -> datetime:
    return datetime.now(UTC)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include UTC offset")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def micro_m3(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,6})?", value):
        raise ValueError("Use a nonnegative decimal m3 value, at most 6 decimal places")
    return int(Decimal(value) * 1_000_000)


def m3(value: int) -> str:
    return format(Decimal(value) / 1_000_000, ".6f")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DialConfig(StrictModel):
    # Fractions of the rectified ROI, with an elliptical radius on both axes.
    center_x: float = Field(default=.5, ge=.1, le=.9)
    center_y: float = Field(default=.5, ge=.1, le=.9)
    radius: float = Field(default=.45, ge=.15, le=.49)
    zero_angle_degrees: float = Field(default=0, ge=0, lt=360)
    clockwise: bool = True
    pointer_color: Literal["red", "dark"] = "red"

    @model_validator(mode="after")
    def fits(self):
        if any(c-self.radius < 0 or c+self.radius > 1 for c in (self.center_x, self.center_y)):
            raise ValueError("Dial circle must fit inside its region")
        return self


class VisualReference(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    value: float = Field(ge=0, lt=10)
    uncertainty: float = Field(default=0, ge=0, le=.49)
    kind: Literal["position", "sector"] = "position"

    @model_validator(mode="after")
    def sector_label(self):
        if self.kind == "sector" and (not self.value.is_integer() or self.uncertainty):
            raise ValueError("A sector reference needs a completed integer digit without angle uncertainty")
        return self


class ROI(StrictModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    # Clockwise orientation of this box in the aligned image; OCR unrotates it.
    rotation_degrees: float = Field(default=0, ge=-180, le=180)
    kind: Literal["digit", "analog"] = "digit"
    dial: DialConfig | None = None
    include_in_reading: bool = True
    recognition_mode: Literal["auto", "text", "rolling", "calibrated"] = "auto"
    visual_references: list[VisualReference] = Field(default_factory=list, max_length=MAX_VISUAL_REFERENCES)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("ROI must be inside the image")
        if self.kind == "analog" and self.dial is None:
            self.dial = DialConfig()
        if self.kind == "analog" and self.recognition_mode != "auto":
            raise ValueError("Wheel recognition mode applies only to number wheels")
        if self.kind == "digit" and any(not ref.value.is_integer() or ref.uncertainty or ref.kind != "position" for ref in self.visual_references):
            raise ValueError("Digit references need an integer label without phase uncertainty")
        return self


class CameraConfig(StrictModel):
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    resolution: Literal["VGA", "SVGA", "XGA"] = "SVGA"
    jpeg_quality: int = Field(default=12, ge=8, le=40)
    led_duty: int = Field(default=100, ge=0, le=255)
    settle_ms: int = Field(default=600, ge=100, le=2000)
    auto_exposure: bool = True
    exposure: int = Field(default=300, ge=0, le=1200)
    auto_gain: bool = True
    gain: int = Field(default=0, ge=0, le=30)


class FlashConfig(StrictModel):
    # Selecting this explicitly permits only this light's timed flash and cleanup.
    light_entity: str = Field(pattern=r"^light\.[a-z0-9_]+$")
    brightness_pct: int = Field(default=20, ge=1, le=100)
    settle_ms: int = Field(default=1500, ge=500, le=2500)


class ImageSource(StrictModel):
    kind: Literal["upload", "home_assistant"] = "upload"
    camera_entity: str | None = Field(default=None, pattern=r"^camera\.[a-z0-9_]+$")
    enabled: bool = False
    flash: FlashConfig | None = None

    @model_validator(mode="after")
    def camera_required(self):
        if self.kind == "home_assistant" and self.camera_entity is None:
            raise ValueError("Select a Home Assistant camera entity")
        if self.kind == "upload" and self.enabled:
            raise ValueError("Scheduled capture requires a Home Assistant camera")
        if self.kind == "upload" and self.flash is not None:
            raise ValueError("Flash requires a Home Assistant camera")
        return self


class AlignmentConfig(StrictModel):
    enabled: bool = False
    reference_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    anchors: list[ROI] = Field(default_factory=list, max_length=2)
    search_margin: float = Field(default=.12, ge=.01, le=.25)
    max_angle_degrees: float = Field(default=8, ge=.1, le=15)
    min_score: float = Field(default=.85, ge=.5, le=1)
    min_peak_margin: float = Field(default=.04, ge=0, le=.5)

    @model_validator(mode="after")
    def valid_anchors(self):
        if any(roi.kind != "digit" or roi.rotation_degrees or roi.w * roi.h > .1 for roi in self.anchors):
            raise ValueError("Landmarks must be unrotated and cover at most 10% of the image")
        if self.enabled and (not self.reference_id or len(self.anchors) != 2):
            raise ValueError("Automatic alignment needs a saved reference and two landmarks")
        if len(self.anchors) == 2:
            a, b = self.anchors
            distance = ((a.x+a.w/2-b.x-b.w/2)**2 + (a.y+a.h/2-b.y-b.h/2)**2)**.5
            if distance < .15:
                raise ValueError("Choose landmarks farther apart")
        return self


class MeterConfig(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    mode: Literal["Docker", "ESP32", "Auto"] = "Docker"
    rois: list[ROI] = Field(default_factory=list, max_length=12)
    decimal_places: int = Field(default=3, ge=0, le=6)
    ignored_decimal_digits: int = Field(default=0, ge=0, le=6)
    last_decimal_mode: Literal["text", "rolling"] = "text"
    min_confidence: float = Field(default=0.85, ge=0.5, le=1)
    min_sharpness: float = Field(default=20, ge=0, le=10000)
    min_brightness: float = Field(default=20, ge=0, le=255)
    max_brightness: float = Field(default=250, ge=0, le=255)
    max_rate_lph: float = Field(default=2000, gt=0, le=1000000)
    max_step_m3: float = Field(default=2, gt=0, le=1000000)
    tolerance_m3: float = Field(default=0.002, ge=0, le=0.1)
    stale_seconds: int = Field(default=1200, ge=60, le=86400)
    max_upload_age_seconds: int = Field(default=3600, ge=60, le=86400)
    max_rate_gap_seconds: int = Field(default=1800, ge=60, le=86400)
    # Optional normalized TL, TR, BR, BL points in the SOURCE image.
    perspective: list[tuple[float, float]] | None = None
    # Clockwise, after perspective correction; ROIs refer to this final image.
    rotation_degrees: float = Field(default=0, ge=-180, le=180)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    aligned_width: int = Field(default=800, ge=100, le=1600)
    aligned_height: int = Field(default=600, ge=100, le=1200)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    source: ImageSource = Field(default_factory=ImageSource)

    @model_validator(mode="after")
    def layout(self):
        if self.last_decimal_mode == "rolling" and not self.decimal_places:
            raise ValueError("Rolling-wheel mode requires a decimal wheel")
        if self.ignored_decimal_digits > self.decimal_places:
            raise ValueError("Only decimal digits can be excluded from the reading")
        included = [roi for roi in self.rois if roi.include_in_reading]
        if included and self.decimal_places >= len(included):
            raise ValueError("Need at least one integer digit ROI")
        if self.min_brightness >= self.max_brightness:
            raise ValueError("Brightness bounds are inverted")
        if self.perspective is not None:
            if len(self.perspective) != 4 or any(
                not 0 <= v <= 1 for point in self.perspective for v in point
            ):
                raise ValueError("Perspective needs four normalized TL, TR, BR, BL points")
            # Cross products must have a consistent positive sign in image coordinates.
            points = self.perspective
            cross = []
            for i in range(4):
                a, b, c = points[i], points[(i + 1) % 4], points[(i + 2) % 4]
                cross.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
            if min(cross) <= 0.0001:
                raise ValueError("Perspective quadrilateral must be convex and clockwise")
        return self


class ManualReading(StrictModel):
    value_m3: str
    reason: str = Field(min_length=8, max_length=500)
    kind: Literal["correction", "replacement", "reset", "rollover"] = "correction"
    reference_event: str | None = Field(default=None, max_length=80)

    @field_validator("value_m3")
    @classmethod
    def value(cls, value):
        micro_m3(value)
        return value


class LogSelection(StrictModel):
    scope: Literal["selected", "rejected", "all"]
    sequences: list[int] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_selection(self):
        if any(seq <= 0 for seq in self.sequences) or len(set(self.sequences)) != len(self.sequences):
            raise ValueError("Use unique positive reading numbers")
        if (self.scope == "selected") != bool(self.sequences):
            raise ValueError("Select reading numbers only for a selected-row deletion")
        return self


class LogDeletion(LogSelection):
    confirm: Literal["DELETE"]
    through_seq: int = Field(gt=0)
    expected_count: int = Field(gt=0)
