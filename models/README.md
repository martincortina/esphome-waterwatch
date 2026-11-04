# Host recognition model and provenance

The default host recognizer uses the public PP-OCRv5 English mobile ONNX text model,
distributed by RapidAI's v3.10.0 catalog under Apache-2.0. This is a general text
recognizer with WaterWatch's digit-row adapter. No AI-on-the-edge-device weights
are distributed. It is not a wheel-position or analog-pointer model.

- Asset: `en_PP-OCRv5_rec_mobile.onnx`, 7,872,351 bytes.
- SHA-256: `c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8`.
- Source: `https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx`.
- Input: BGR float32 NCHW, height 48, dynamic width at least 320; image divided by
  255, then normalized with mean .5 and scale .5, with zero padding on the right.
- Output: CTC probabilities; the alphabet is embedded in ONNX metadata.
- Runtime: ONNX Runtime 1.31.1, CPU only, one thread, telemetry disabled.
- License: `server/licenses/paddleocr-apache-2.0.txt` and `THIRD_PARTY_NOTICES.md`.

The outer ROI is rectified first. Its central 60% defines the digit area. Two rows
use horizontal normalization factors 1.4 and 1.6. Emissions map to known cell
positions, so an omitted digit does not shift subsequent positions. Multiple or
non-numeric emissions in a cell are rejected. Scores come directly from the first
CTC emission, without rescaling. Both passes must agree, and normal image-quality,
confidence, baseline and plausibility gates apply.

`python -m server.model_assets` fetches and checks the asset during the image build.
Startup and inference make no model downloads. Missing or modified weights fail
closed. The source-only package excludes model binaries and private meter photos.

Development evidence: both passes recover seven human-verified digits from one
supplied photograph and disagree on its unverified last digit. This is a development
regression, not held-out accuracy or hardware qualification.

Tesseract remains an explicit Python adapter for comparisons. Future models need
source/dataset rights, training split, SHA-256, shape/color/quantization/operator
contracts, held-out accuracy, rejection coverage and latency. Transition and analog
models need richer outputs; never silently round a continuous wheel position.
