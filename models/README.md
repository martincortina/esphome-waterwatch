# Host recognition model and provenance

The host recognizer uses the public PP-OCRv5 English and Latin mobile ONNX text models,
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

The assistance asset is `latin_PP-OCRv5_rec_mobile.onnx`, 7,904,513 bytes,
SHA-256 `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a`.
It comes from the same v3.10.0 catalog under `onnx/PP-OCRv5/rec/`, with the
same Apache-2.0 license and BGR/NCHW runtime contract. Its character dictionary is
read from its own embedded metadata.

The outer ROI is rectified first. Its central 60% defines the digit area. Two rows
use horizontal normalization factors 1.4 and 1.6. Emissions map to known cell
positions, so an omitted digit does not shift subsequent positions. Multiple or
non-numeric emissions in a cell are rejected. Scores come directly from the first
CTC emission, without rescaling. The English views remain available as original
evidence. Five Latin views include the central 60% width and approximately 85%
height, with explicit half-up pixel rounding. They normalize neutral colour
(minimum BGR channel), green, and luma contrast between the 2nd/98th percentiles.
Neutral/green stretch by 1.2 and luma by 1.0. A wider neutral view retains
80% width; a full-height luma view uses Otsu binarization and stretches by 1.4.
All views retain the fixed source-cell mapping.

At least two Latin views must agree to support a weak or missing English reading.
The two strongest agreeing raw scores are reported, with their actual view names.
A competing numeric result scoring at least .85 from either model stops promotion;
an existing conflicting raw numeric pair with both scores at least .25 also remains
unresolved. Consistent stronger English evidence remains usable when assistance is
inconclusive. Neither letters nor the expected reading are converted into digits.
All original and assisting predictions remain in diagnostics. Configured quality,
confidence, baseline and plausibility checks still apply.

`python -m server.model_assets` fetches and checks both assets during the image build.
Startup and inference make no model downloads. Missing or modified weights fail
closed. The source-only package excludes model binaries and private meter photos.

Development evidence: both passes recover seven human-verified digits from one
supplied photograph and disagree on its unverified last digit. This is a development
regression, not held-out accuracy or hardware qualification.

Tesseract remains an explicit Python adapter for comparisons. Future models need
source/dataset rights, training split, SHA-256, shape/color/quantization/operator
contracts, held-out accuracy, rejection coverage and latency. Transition and analog
models need richer outputs; never silently round a continuous wheel position.
