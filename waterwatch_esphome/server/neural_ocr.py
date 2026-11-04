"""Offline counter-row recognition with per-cell CTC scores and two-pass agreement.

PP-OCRv5 English weights are distributed by RapidAI under Apache-2.0. Model
provenance and preprocessing are documented in models/README.md and the notices.
"""
import hashlib
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL_NAME = "en_PP-OCRv5_rec_mobile.onnx"
MODEL_SHA256 = "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8"
MODEL_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/"
    "onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx"
)
MODEL_DIRECTORY = Path(__file__).parent / "ocr_models"
ASSIST_MODEL_NAME = "latin_PP-OCRv5_rec_mobile.onnx"
ASSIST_MODEL_SHA256 = "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a"
ASSIST_MODEL_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/"
    "onnx/PP-OCRv5/rec/latin_PP-OCRv5_rec_mobile.onnx"
)
ASSIST_VIEWS = (("neutral contrast", "neutral", 1.2, .6, .85, False),
                ("green contrast", "green", 1.2, .6, .85, False),
                ("luma contrast", "luma", 1.0, .6, .85, False),
                ("wider neutral contrast", "neutral", 1.2, .8, .85, False),
                ("full-window binary", "luma", 1.4, .6, 1.0, True))


class NeuralOCRError(RuntimeError):
    pass


@dataclass
class RowRecognition:
    results: list
    processed: np.ndarray
    details: dict


def counter_strip(patches, stretch, valid=None, normalization=None, width_fraction=.6,
                  height_fraction=.85, binary=False):
    """Take each outer ROI's central 60%, then keep separate known digit cells."""
    if not 1 <= len(patches) <= 12 or valid is not None and len(valid) != len(patches):
        raise NeuralOCRError("Invalid digit-cell count")
    cells, columns, x = [], [], 0
    for index, patch in enumerate(patches):
        height, width = patch.shape[:2]
        if normalization:
            top, left = math.floor(height*(1-height_fraction)/2+.5), round(width*(1-width_fraction)/2)
            inner = patch[top:height-top, left:width-left]
            if min(inner.shape[:2]) < 2:
                raise NeuralOCRError("Digit inner frame is too small")
            gray = (inner.min(axis=2) if normalization == "neutral" else inner[:, :, 1]
                    if normalization == "green" else cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY))
            low, high = np.percentile(gray, (2, 98))
            gray = np.clip((gray.astype(np.float64)-low)*255/max(1, high-low), 0, 255).astype(np.uint8)
            if binary:
                gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
            inner = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        else:
            inner = patch[round(height*.2):round(height*.8), round(width*.2):round(width*.8)]
        if min(inner.shape[:2]) < 2:
            raise NeuralOCRError("Digit inner frame is too small")
        cell_width = max(1, round(inner.shape[1]*80/inner.shape[0]*stretch))
        if cell_width > 256:
            raise NeuralOCRError("Digit frame is too wide for bounded inference")
        cell = cv2.resize(inner, (cell_width, 80), interpolation=cv2.INTER_CUBIC)
        if valid is not None and not valid[index]:
            cell = np.zeros_like(cell)
        cell = cv2.copyMakeBorder(cell, 8, 8, 5, 5, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        cells.append(cell)
        columns.append((x, x+cell.shape[1]))
        x += cell.shape[1]
    return np.hstack(cells), columns


def model_input(strip):
    height, width = strip.shape[:2]
    padded_width = max(320, int(48*width/height))
    resized_width = min(padded_width, math.ceil(48*width/height))
    resized = cv2.resize(strip, (resized_width, 48)).astype(np.float32)
    normalized = resized.transpose(2, 0, 1) / 255
    normalized -= .5
    normalized /= .5
    tensor = np.zeros((1, 3, 48, padded_width), np.float32)
    tensor[0, :, :, :resized_width] = normalized
    return tensor, resized_width, padded_width


def decode_cells(probabilities, characters, columns, image_width, resized_width, padded_width):
    """Map emissions to their source cells; missing/extra symbols never shift digits."""
    scores = np.asarray(probabilities)
    if (scores.ndim != 3 or scores.shape[0] != 1 or scores.shape[2] != len(characters)
            or not np.isfinite(scores).all() or np.any(scores < 0) or np.any(scores > 1)):
        raise NeuralOCRError("Invalid model probabilities")
    values = scores[0]
    if not len(values) or not np.allclose(values.sum(axis=1), 1, atol=.002):
        raise NeuralOCRError("Invalid model distribution")
    indices = values.argmax(axis=1)
    emissions = [[] for _ in columns]
    text = []
    start = 0
    unmatched = False
    while start < len(indices):
        end = start+1
        while end < len(indices) and indices[end] == indices[start]:
            end += 1
        token = int(indices[start])
        if token:
            char = characters[token]
            # A token covers a time interval; use its centre in the unpadded row.
            x = (start+end)/2/len(indices)*padded_width/resized_width*image_width
            slot = next((i for i, (left, right) in enumerate(columns) if left <= x < right), None)
            text.append(char)
            if slot is None:
                unmatched = True
            else:
                emissions[slot].append((char, float(values[start, token])))
        start = end
    result = []
    for symbols in emissions:
        if (unmatched or len(symbols) != 1 or len(symbols[0][0]) != 1
                or symbols[0][0] not in "0123456789"):
            result.append((None, 0.0))
        else:
            result.append(symbols[0])
    return result, "".join(text)


def select_evidence(primary, assisted):
    """Choose a genuine two-view consensus, retaining the unmodified model scores."""
    digit = primary[0][0] if primary[0][0] is not None and primary[0][0] == primary[1][0] else None
    # A conflicting raw numeric pair can represent a rolling wheel, even at low scores.
    raw_conflict = (primary[0][0] is not None and primary[1][0] is not None
                    and primary[0][0] != primary[1][0] and min(p[1] for p in primary) >= .25)
    votes = {}
    for index, (value, score) in enumerate(assisted):
        if value is not None and score >= .5:
            votes.setdefault(value, []).append((score, index))
    candidates = [(value, sorted(support, reverse=True)[:2])
                  for value, support in votes.items() if len(support) >= 2]
    if raw_conflict:
        return [(None, 0.0), (None, 0.0)], ["conflicting raw views"]*2, "raw_numeric_conflict"
    if len(candidates) > 1:
        return [(None, 0.0), (None, 0.0)], ["ambiguous assistance"]*2, "model_conflict"
    if len(candidates) == 1:
        value, support = candidates[0]
        # Strong contradictory evidence from either model always stops promotion.
        conflict = any(other is not None and other != value and score >= .85
                       for other, score in [*primary, *assisted])
        if conflict or digit is not None and digit != value:
            return [(None, 0.0), (None, 0.0)], ["model conflict"]*2, "model_conflict"
        selected = [(value, score) for score, index in support]
        if digit is None or min(s for d, s in selected) > min(s for d, s in primary):
            return selected, ["Latin · "+ASSIST_VIEWS[index][0] for score, index in support], "assisted_consensus"
    return primary, ["English · wide row", "English · wider row"], "primary_views"


class NeuralOCREngine:
    """One local CPU model; unmodified character scores from two normalizations."""
    def __init__(self, model_dir=None):
        self.path = Path(model_dir or MODEL_DIRECTORY) / MODEL_NAME
        self.assist_path = Path(model_dir or MODEL_DIRECTORY) / ASSIST_MODEL_NAME
        self._session = None
        self._load_lock = threading.Lock()

    @property
    def available(self):
        return self.path.is_file() and self.assist_path.is_file()

    def _load(self):
        with self._load_lock:
            if self._session is not None:
                return
            if (not self.available or hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256
                    or hashlib.sha256(self.assist_path.read_bytes()).hexdigest() != ASSIST_MODEL_SHA256):
                raise NeuralOCRError("Pinned OCR model is missing or changed; rebuild the application")
            try:
                import onnxruntime as ort
                ort.disable_telemetry_events()
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(str(self.path), sess_options=options,
                                               providers=["CPUExecutionProvider"])
                alphabet = session.get_modelmeta().custom_metadata_map["character"].splitlines()
                self.characters = ["", *alphabet, " "]
                self.input_name = session.get_inputs()[0].name
                assist = ort.InferenceSession(str(self.assist_path), sess_options=options,
                                             providers=["CPUExecutionProvider"])
                self.assist_characters = ["", *assist.get_modelmeta().custom_metadata_map["character"].splitlines(), " "]
                self.assist_input_name = assist.get_inputs()[0].name
                self._assist_session = assist
                self._session = session
            except Exception as error:
                raise NeuralOCRError("Cannot initialize the local OCR model") from error

    def recognize_digits(self, patches, valid=None):
        self._load()
        passes, rows, texts = [], [], []
        for stretch in (1.4, 1.6):
            row, columns = counter_strip(patches, stretch, valid)
            tensor, resized_width, padded_width = model_input(row)
            try:
                probabilities = self._session.run(None, {self.input_name: tensor})[0]
            except Exception as error:
                raise NeuralOCRError("Local OCR inference failed") from error
            digits, text = decode_cells(probabilities, self.characters, columns,
                                        row.shape[1], resized_width, padded_width)
            if valid is not None:
                digits = [digit if valid[i] else (None, 0.0) for i, digit in enumerate(digits)]
            passes.append(digits)
            rows.append(row)
            texts.append(text)
        assisted, assist_texts = [], []
        for _name, normalization, stretch, width_fraction, height_fraction, binary in ASSIST_VIEWS:
            row, columns = counter_strip(patches, stretch, valid, normalization,
                                        width_fraction, height_fraction, binary)
            tensor, resized_width, padded_width = model_input(row)
            try:
                probabilities = self._assist_session.run(None, {self.assist_input_name: tensor})[0]
            except Exception as error:
                raise NeuralOCRError("Local assistance inference failed") from error
            values, text = decode_cells(probabilities, self.assist_characters, columns,
                                        row.shape[1], resized_width, padded_width)
            assisted.append(values)
            assist_texts.append(text)
        results, evidence = [], []
        for index, primary in enumerate(zip(*passes, strict=True)):
            choices = [view[index] for view in assisted]
            selected, sources, policy = select_evidence(list(primary), choices)
            if valid is not None and not valid[index]:
                selected = [(None, 0.0), (None, 0.0)]
            results.append(selected)
            evidence.append({"recognition_sources": sources, "recognition_policy": policy,
                             "primary_recognitions": list(primary), "assist_recognitions": choices})
        return RowRecognition(
            results=results,
            processed=rows[0],
            details={"model": "PP-OCRv5 English + Latin mobile", "model_sha256": MODEL_SHA256,
                     "assist_model_sha256": ASSIST_MODEL_SHA256, "assist_row_texts": assist_texts,
                     "recognition_evidence": evidence,
                     "recognition_passes": ["wide row", "wider row"],
                     "row_texts": texts, "roi_inner_fraction": .6},
        )
