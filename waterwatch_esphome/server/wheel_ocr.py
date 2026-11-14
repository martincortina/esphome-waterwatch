"""Optional mechanical-wheel phase recognition, limited to the last decimal ROI.

The third-party model has a separate non-commercial license; see models/README.md.
Phase scores are model support, not calibrated probabilities of correctness.
"""
import hashlib
import threading
from pathlib import Path

import cv2
import numpy as np

from server.neural_ocr import MODEL_DIRECTORY, NeuralOCRError

WHEEL_MODEL_NAME = "dig-class100-0182-s2_q.tflite"
WHEEL_MODEL_SHA256 = "78f8ee04f8e195ef9e8e1314c77690d530ef151e24c1cf7ab3c66055d0c2b950"
WHEEL_MODEL_URL = (
    "https://raw.githubusercontent.com/jomjol/AI-on-the-edge-device/"
    "04fad5d3efc518c4c2ad8cd14904f1d966ac3605/sd-card/config/" + WHEEL_MODEL_NAME
)


def phase_evidence(logits):
    """Convert the 100 phase logits into a peak and completed-digit support."""
    values = np.asarray(logits, dtype=np.float64).reshape(-1)
    if values.size != 100 or not np.isfinite(values).all():
        raise NeuralOCRError("Invalid rolling-wheel output")
    scores = np.exp(values-values.max())
    scores /= scores.sum()
    peak = int(scores.argmax())
    digit = peak // 10
    center = (int(np.floor(peak/10+.5)) % 10)*10
    center_indices = (np.arange(center-2, center+3) % 100).astype(int)
    return {"phase": peak / 10, "completed_digit": str(digit),
            "score": float(scores[digit*10:(digit+1)*10].sum()),
            "center_score": float(scores[center_indices].sum())}


def resolve_wheel(views, text_results, raw_text, threshold):
    """Require consistent phase views; never round a transition into its next digit.

    A guard of 0.2 digit either side of a boundary prevents a phase error from
    becoming a false carry. Near a boundary only confident, matching full-digit
    OCR may resolve it. History and user-supplied labels are never consulted.
    """
    rejected = [(None, 0.0), (None, 0.0)]
    detail = {"wheel_views": views, "wheel_policy": "uncertain", "wheel_model": WHEEL_MODEL_NAME}
    if len(views) != 2:
        return rejected, detail
    phases = [v["phase"] for v in views]
    distance = abs((phases[0]-phases[1]+5) % 10-5)
    if distance > .2 + 1e-9:
        return rejected, detail
    # At a centered digit, phase 6.9 and 7.0 can describe the same visible 7.
    nearest = [int(np.floor(p+.5)) % 10 for p in phases]
    centered = all(abs((p-n+5) % 10-5) <= .2+1e-9 for p, n in zip(phases, nearest, strict=True))
    if centered:
        if (nearest[0] == nearest[1] and len(text_results) == 2
                and min(v.get("center_score", v["score"]) for v in views) >= threshold
                and all(d == str(nearest[0]) and s >= threshold for d, s in text_results)):
            detail["wheel_policy"] = "centered_digit_verified"
            return text_results, detail
        return rejected, detail
    digits = [v["completed_digit"] for v in views]
    if (digits[0] != digits[1] or min(v["score"] for v in views) < threshold
            or any(not .2 < p % 1 < .8 for p in phases)):
        return rejected, detail
    digit = digits[0]
    following = str((int(digit)+1) % 10)
    if any(d is not None and d not in {digit, following} and s >= threshold for d, s in raw_text):
        return rejected, detail
    detail.update(wheel_policy="transition_verified", possible_transition=[digit, following])
    return [(digit, v["score"]) for v in views], detail


class WheelOCREngine:
    def __init__(self, model_dir=None):
        self.path = Path(model_dir or MODEL_DIRECTORY) / WHEEL_MODEL_NAME
        self._lock = threading.Lock()
        self._interpreter = None

    def recognize(self, patch):
        with self._lock:
            if self._interpreter is None:
                if not self.path.is_file() or hashlib.sha256(self.path.read_bytes()).hexdigest() != WHEEL_MODEL_SHA256:
                    raise NeuralOCRError("Pinned rolling-wheel model is missing or changed")
                try:
                    from ai_edge_litert.interpreter import Interpreter
                    self._interpreter = Interpreter(model_path=str(self.path), num_threads=1)
                    self._interpreter.allocate_tensors()
                    self._input = self._interpreter.get_input_details()[0]
                    self._output = self._interpreter.get_output_details()[0]
                    if tuple(self._input["shape"]) != (1, 32, 20, 3) or tuple(self._output["shape"]) != (1, 100):
                        raise ValueError("Unexpected wheel model shape")
                except Exception as error:
                    self._interpreter = None
                    raise NeuralOCRError("Cannot initialize rolling-wheel recognition") from error
            views = []
            # Preserve the outer ROI and RGB 0..255 input used by the original model.
            for interpolation in (cv2.INTER_LINEAR, cv2.INTER_AREA):
                rgb = cv2.cvtColor(cv2.resize(patch, (20, 32), interpolation=interpolation), cv2.COLOR_BGR2RGB)
                try:
                    self._interpreter.set_tensor(self._input["index"], rgb.astype(np.float32)[None])
                    self._interpreter.invoke()
                    views.append(phase_evidence(self._interpreter.get_tensor(self._output["index"])))
                except Exception as error:
                    raise NeuralOCRError("Rolling-wheel inference failed") from error
            return views
