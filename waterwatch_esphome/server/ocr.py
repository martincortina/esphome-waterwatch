import csv
import io
import math
import shutil
import subprocess
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from server.models import MeterConfig


@dataclass
class Recognition:
    raw_value: str | None = None
    confidence: float = 0
    reason: str = "unreadable"
    details: dict = field(default_factory=dict)
    overlay: bytes | None = None
    processed: bytes | None = None


def jpeg(image) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("Image encoding failed")
    return encoded.tobytes()


def decode(data: bytes):
    # Inspect dimensions before OpenCV allocates a decoded framebuffer.
    try:
        with Image.open(io.BytesIO(data)) as header:
            if header.format not in ("JPEG", "PNG") or header.width * header.height > 5_000_000:
                raise ValueError("Only JPEG/PNG images up to 5 megapixels are supported")
            if min(header.size) < 32:
                raise ValueError("Image is too small")
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        raise ValueError("Invalid or oversized image") from exc
    if image is None:
        raise ValueError("Image cannot be decoded")
    return image


def rotate(image, degrees):
    """Rotate clockwise without cropping. Quarter turns preserve source pixels."""
    if degrees == 0:
        return image
    quarter_turns = {90: cv2.ROTATE_90_CLOCKWISE, -90: cv2.ROTATE_90_COUNTERCLOCKWISE,
                     180: cv2.ROTATE_180, -180: cv2.ROTATE_180}
    if degrees in quarter_turns:
        return cv2.rotate(image, quarter_turns[degrees])
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2), -degrees, 1)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    out_width = math.ceil(width * cosine + height * sine)
    out_height = math.ceil(height * cosine + width * sine)
    if out_width * out_height > 10_000_000 or max(out_width, out_height) >= 32767:
        raise ValueError("Rotated image exceeds the 10 megapixel limit")
    matrix[0, 2] += (out_width - width) / 2
    matrix[1, 2] += (out_height - height) / 2
    return cv2.warpAffine(image, matrix, (out_width, out_height),
                          flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def align(image, cfg: MeterConfig):
    if cfg.perspective is None:
        return rotate(image, cfg.rotation_degrees)
    height, width = image.shape[:2]
    src = np.float32(cfg.perspective) * (width - 1, height - 1)
    dst = np.float32(
        [
            [0, 0],
            [cfg.aligned_width - 1, 0],
            [cfg.aligned_width - 1, cfg.aligned_height - 1],
            [0, cfg.aligned_height - 1],
        ]
    )
    matrix = cv2.getPerspectiveTransform(src.astype(np.float32), dst)
    corrected = cv2.warpPerspective(image, matrix, (cfg.aligned_width, cfg.aligned_height))
    return rotate(corrected, cfg.rotation_degrees)


class TesseractEngine:
    """CPU baseline. Scores are engine scores, not calibrated probabilities."""

    @property
    def available(self):
        return shutil.which("tesseract") is not None

    def digit(self, patch):
        ok, png = cv2.imencode(".png", patch)
        if not ok:
            return None, 0
        result = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                "eng",
                "--psm",
                "10",
                "-c",
                "tessedit_char_whitelist=0123456789",
                "tsv",
            ],
            input=png.tobytes(),
            capture_output=True,
            timeout=8,
            check=True,
        )
        words = [
            r
            for r in csv.DictReader(io.StringIO(result.stdout.decode()), delimiter="\t")
            if r.get("text", "").strip()
        ]
        if len(words) != 1:
            return None, 0
        word = words[0]
        text = word["text"].strip()
        if len(text) != 1 or text not in "0123456789":
            return None, 0
        return text, max(0, float(word["conf"]) / 100)


class Pipeline:
    def __init__(self, engine=None):
        self.engine = engine or TesseractEngine()

    def run(self, data: bytes, cfg: MeterConfig) -> Recognition:
        image = align(decode(data), cfg)
        overlay = image.copy()
        height, width = image.shape[:2]
        if not cfg.rois:
            return Recognition(reason="roi_not_configured", overlay=jpeg(image))
        digits, confidences, diagnostics, previews = [], [], [], []
        for index, roi in enumerate(cfg.rois):
            x, y = int(roi.x * width), int(roi.y * height)
            w, h = int(roi.w * width), int(roi.h * height)
            if min(w, h) < 8:
                return Recognition(reason="roi_too_small", overlay=jpeg(overlay))
            patch = cv2.cvtColor(image[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(patch, cv2.CV_64F).var())
            brightness = float(patch.mean())
            diagnostics.append({"digit": index, "sharpness": sharpness, "brightness": brightness})
            cv2.rectangle(overlay, (x, y), (x + w, y + h), (80, 210, 80), 2)
            cv2.putText(
                overlay,
                str(index),
                (x, max(12, y - 3)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (40, 120, 255),
                1,
            )
            if (
                sharpness < cfg.min_sharpness
                or not cfg.min_brightness <= brightness <= cfg.max_brightness
            ):
                return Recognition(
                    reason="poor_image_quality",
                    details={"digits": diagnostics},
                    overlay=jpeg(overlay),
                )
            enlarged = cv2.resize(patch, (80, 120), interpolation=cv2.INTER_CUBIC)
            enhanced = cv2.createCLAHE(clipLimit=2, tileGridSize=(4, 4)).apply(enlarged)
            threshold = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            variants = [
                cv2.copyMakeBorder(p, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
                for p in (enhanced, threshold)
            ]
            previews.append(variants[1])
            try:
                results = [self.engine.digit(p) for p in variants]
            except (OSError, subprocess.SubprocessError):
                return Recognition(reason="ocr_engine_failure", overlay=jpeg(overlay))
            diagnostics[-1]["recognitions"] = results
            # Require agreement between two preprocessing passes. Never guess a missing digit.
            if results[0][0] is None or results[0][0] != results[1][0]:
                digits.append("?")
                confidences.append(0)
            else:
                digits.append(results[0][0])
                confidences.append(min(r[1] for r in results))
        text = "".join(digits)
        if cfg.decimal_places:
            text = text[: -cfg.decimal_places] + "." + text[-cfg.decimal_places :]
        readable = "?" not in text
        return Recognition(
            raw_value=text,
            confidence=min(confidences),
            reason="candidate" if readable else "ambiguous_digit",
            details={"digits": diagnostics, "engine": type(self.engine).__name__,
                     "rotation_degrees": cfg.rotation_degrees},
            overlay=jpeg(overlay),
            processed=jpeg(np.hstack(previews)),
        )
