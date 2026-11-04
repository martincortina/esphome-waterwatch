import csv
import io
import math
import shutil
import subprocess
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from server.alignment import AlignmentError, register
from server.models import ROI, MeterConfig
from server.neural_ocr import NeuralOCREngine, NeuralOCRError


@dataclass
class Recognition:
    raw_value: str | None = None
    confidence: float = 0
    reason: str = "unreadable"
    details: dict = field(default_factory=dict)
    overlay: bytes | None = None
    processed: bytes | None = None
    aligned: bytes | None = None
    crops: list[bytes] = field(default_factory=list)


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


def align(image, cfg: MeterConfig, reference=None, diagnostics=None, use_registration=True):
    if cfg.perspective is None:
        corrected = rotate(image, cfg.rotation_degrees)
    else:
        height, width = image.shape[:2]
        src = np.float32(cfg.perspective) * (width - 1, height - 1)
        dst = np.float32([[0, 0], [cfg.aligned_width-1, 0],
                         [cfg.aligned_width-1, cfg.aligned_height-1], [0, cfg.aligned_height-1]])
        matrix = cv2.getPerspectiveTransform(src.astype(np.float32), dst)
        corrected = rotate(cv2.warpPerspective(image, matrix, (cfg.aligned_width, cfg.aligned_height)), cfg.rotation_degrees)
    if cfg.alignment.enabled and use_registration:
        corrected, result = register(corrected, reference, cfg)
        if diagnostics is not None:
            diagnostics["alignment"] = result
    return corrected


def digit_corners(roi: ROI, width: int, height: int):
    """Clockwise TL/TR/BR/BL image-edge coordinates, matching the canvas editor."""
    center = np.array([(roi.x + roi.w / 2) * width, (roi.y + roi.h / 2) * height])
    theta = math.radians(roi.rotation_degrees)
    matrix = np.array([[math.cos(theta), -math.sin(theta)],
                       [math.sin(theta), math.cos(theta)]])
    offsets = np.array([[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]])
    return (offsets * (roi.w * width, roi.h * height)) @ matrix.T + center


def inner_frame(roi: ROI):
    return roi.model_copy(update={"x": roi.x+roi.w*.2, "y": roi.y+roi.h*.2,
                                  "w": roi.w*.6, "h": roi.h*.6})


def crop_digit(image, roi: ROI):
    """Rectify one oriented digit box before image-quality checks and recognition."""
    height, width = image.shape[:2]
    w, h = int(roi.w * width), int(roi.h * height)
    if min(w, h) < 8:
        raise ValueError("roi_too_small")
    corners = digit_corners(roi, width, height)
    if np.any(corners < -.001) or np.any(corners > np.array([width, height]) + .001):
        raise ValueError("roi_outside_image")
    if roi.rotation_degrees == 0:
        x, y = int(roi.x * width), int(roi.y * height)
        return image[y:y+h, x:x+w]
    if max(width, height, w, h) >= 32767:
        raise ValueError("roi_transform_too_large")
    # Map pixel centres (half a pixel inside the box edges) to the upright crop.
    # Shrink in the box's local axes, then convert edge coordinates to CV centres.
    local = roi.model_copy(update={"w": roi.w * (w-1)/w, "h": roi.h * (h-1)/h,
                                   "x": roi.x + roi.w/(2*w), "y": roi.y + roi.h/(2*h)})
    pixel_corners = (digit_corners(local, width, height) - .5).astype(np.float32)
    destination = np.float32([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]])
    matrix = cv2.getPerspectiveTransform(pixel_corners, destination)
    return cv2.warpPerspective(image, matrix, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


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
    def __init__(self, engine=None, reference_loader=None):
        self.engine = engine or NeuralOCREngine()
        self.reference_loader = reference_loader

    def run(self, data: bytes, cfg: MeterConfig) -> Recognition:
        context = {}
        try:
            reference = self.reference_loader(cfg) if cfg.alignment.enabled and self.reference_loader else None
            image = align(decode(data), cfg, reference=reference, diagnostics=context)
        except AlignmentError as error:
            return Recognition(reason=str(error), details={"alignment": error.details})
        aligned = jpeg(image)
        overlay = image.copy()
        height, width = image.shape[:2]
        if not cfg.rois:
            return Recognition(reason="roi_not_configured", overlay=aligned, aligned=aligned, details=context)
        row_result = None
        if callable(getattr(self.engine, "recognize_digits", None)):
            colors, valid = [], []
            for index, roi in enumerate(cfg.rois):
                try:
                    color = crop_digit(image, roi)
                except ValueError as error:
                    return Recognition(reason=str(error), details={**context, "digit": index},
                                       aligned=aligned, overlay=aligned)
                gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
                colors.append(color)
                valid.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()) >= cfg.min_sharpness
                             and cfg.min_brightness <= float(gray.mean()) <= cfg.max_brightness)
            if any(valid):
                try:
                    row_result = self.engine.recognize_digits(colors, valid)
                    context.update(row_result.details)
                except (OSError, NeuralOCRError):
                    return Recognition(reason="ocr_engine_failure", details=context, aligned=aligned,
                                       overlay=aligned, crops=[jpeg(color) for color in colors])
        digits, confidences, diagnostics, previews, crops = [], [], [], [], []
        poor_quality = False
        used_count = len(cfg.rois) - cfg.ignored_decimal_digits
        for index, roi in enumerate(cfg.rois):
            corners = digit_corners(roi, width, height)
            try:
                color = crop_digit(image, roi)
                crops.append(jpeg(color))
                patch = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
            except ValueError as error:
                return Recognition(reason=str(error), details={**context, "digit": index}, overlay=jpeg(overlay), aligned=aligned, crops=crops)
            sharpness = float(cv2.Laplacian(patch, cv2.CV_64F).var())
            brightness = float(patch.mean())
            diagnostics.append({"digit": index, "sharpness": sharpness, "brightness": brightness,
                                "used_for_reading": index < used_count,
                                "rotation_degrees": roi.rotation_degrees,
                                "contrast": round(float(np.percentile(patch, 95)-np.percentile(patch, 5)), 2),
                                "highlight_fraction": round(float(np.mean(patch >= 245)), 4),
                                "shadow_fraction": round(float(np.mean(patch <= 10)), 4),
                                "quality_warnings": (["dark_digit"] if brightness < 35 else []) +
                                (["soft_or_blurred"] if sharpness < cfg.min_sharpness else []) +
                                (["check_highlights"] if np.mean(patch >= 245) > .2 else [])})
            cv2.polylines(overlay, [np.rint(corners).astype(np.int32)], True, (80, 210, 80), 2)
            inner = digit_corners(inner_frame(roi), width, height)
            cv2.polylines(overlay, [np.rint(inner).astype(np.int32)], True, (255, 200, 114), 1)
            x, y = np.rint(corners.min(axis=0)).astype(int)
            cv2.putText(
                overlay,
                str(index+1),
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
                diagnostics[-1]["status"] = "poor_image_quality"
                digits.append("?")
                confidences.append(0)
                poor_quality = poor_quality or index < used_count
                previews.append(cv2.resize(patch, (110, 150)))
                continue
            if row_result is not None:
                results = row_result.results[index]
            else:
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
                    return Recognition(reason="ocr_engine_failure", details={**context, "digits": diagnostics},
                                       overlay=jpeg(overlay), aligned=aligned, crops=crops)
            diagnostics[-1]["recognitions"] = results
            evidence = context.get("recognition_evidence", [])
            if index < len(evidence):
                diagnostics[-1].update(evidence[index])
            choices = {r[0] for r in results if r[0] is not None and r[1] >= .5}
            if len(choices) == 2 and all(len(c) == 1 and c in "0123456789" for c in choices):
                for first in sorted(choices):
                    following = str((int(first)+1) % 10)
                    if following in choices:
                        diagnostics[-1]["possible_transition"] = [first, following]
                        break
            agreed = results[0][0] is not None and results[0][0] == results[1][0]
            diagnostics[-1]["status"] = ("agreed" if min(r[1] for r in results) >= cfg.min_confidence else "low_confidence") if agreed else "ambiguous"
            # Require agreement between two preprocessing passes. Never guess a missing digit.
            if results[0][0] is None or results[0][0] != results[1][0]:
                digits.append("?")
                confidences.append(0)
            else:
                digits.append(results[0][0])
                confidences.append(min(r[1] for r in results))
        full_text = "".join(digits)
        if cfg.decimal_places:
            full_text = full_text[: -cfg.decimal_places] + "." + full_text[-cfg.decimal_places :]
        text = "".join(digits[:used_count])
        decimals = cfg.decimal_places - cfg.ignored_decimal_digits
        if decimals:
            text = text[:-decimals] + "." + text[-decimals:]
        readable = "?" not in text
        return Recognition(
            raw_value=text,
            confidence=min(confidences[:used_count]),
            reason="poor_image_quality" if poor_quality else "candidate" if readable else "ambiguous_digit",
            details={**context, "digits": diagnostics, "engine": type(self.engine).__name__,
                     "full_raw_value": full_text, "ignored_decimal_digits": cfg.ignored_decimal_digits,
                     "resolution_litres": 1000 * 10**-decimals,
                     "rotation_degrees": cfg.rotation_degrees},
            overlay=jpeg(overlay),
            processed=jpeg(row_result.processed if row_result is not None else np.hstack(previews)),
            aligned=aligned, crops=crops,
        )
