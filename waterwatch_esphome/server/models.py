import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")]


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


class ROI(StrictModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("ROI must be inside the image")
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


class MeterConfig(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=80)
    mode: Literal["Docker", "ESP32", "Auto"] = "Docker"
    rois: list[ROI] = Field(default_factory=list, max_length=12)
    decimal_places: int = Field(default=3, ge=0, le=6)
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
    aligned_width: int = Field(default=800, ge=100, le=1600)
    aligned_height: int = Field(default=600, ge=100, le=1200)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    source: ImageSource = Field(default_factory=ImageSource)

    @model_validator(mode="after")
    def layout(self):
        if self.rois and self.decimal_places >= len(self.rois):
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
