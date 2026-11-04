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


class NeuralOCRError(RuntimeError):
    pass


@dataclass
class RowRecognition:
    results: list
    processed: np.ndarray
    details: dict


def counter_strip(patches, stretch, valid=None):
    """Take each outer ROI's central 60%, then keep separate known digit cells."""
    if not 1 <= len(patches) <= 12 or valid is not None and len(valid) != len(patches):
        raise NeuralOCRError("Invalid digit-cell count")
    cells, columns, x = [], [], 0
    for index, patch in enumerate(patches):
        height, width = patch.shape[:2]
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


class NeuralOCREngine:
    """One local CPU model; unmodified character scores from two normalizations."""
    def __init__(self, model_dir=None):
        self.path = Path(model_dir or MODEL_DIRECTORY) / MODEL_NAME
        self._session = None
        self._load_lock = threading.Lock()

    @property
    def available(self):
        return self.path.is_file()

    def _load(self):
        with self._load_lock:
            if self._session is not None:
                return
            if not self.available or hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
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
        return RowRecognition(
            results=[list(pair) for pair in zip(*passes, strict=True)],
            processed=rows[0],
            details={"model": "PP-OCRv5 English mobile", "model_sha256": MODEL_SHA256,
                     "recognition_passes": ["wide row", "wider row"],
                     "row_texts": texts, "roi_inner_fraction": .6},
        )
