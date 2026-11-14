# Host recognition model and provenance

The host recognizer uses the public PP-OCRv5 English and Latin mobile ONNX text models,
distributed by RapidAI's v3.10.0 catalog under Apache-2.0. This is a general text
recognizer with WaterWatch's digit-row adapter. Text OCR remains the default.
An optional last-decimal phase model is described below; analog pointers are unsupported.

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

`python -m server.model_assets` fetches and checks the pinned text and wheel assets during the image build.
Startup and inference make no model downloads. Missing or modified weights fail
closed. The source-only package excludes model binaries and private meter photos.

Development evidence: both passes recover seven human-verified digits from one
supplied photograph and disagree on its unverified last digit. This is a development
regression, not held-out accuracy or hardware qualification.

Tesseract remains an explicit Python adapter for comparisons. Future models need
source/dataset rights, training split, SHA-256, shape/color/quantization/operator
contracts, held-out accuracy, rejection coverage and latency. Transition and analog
models need richer outputs; never silently round a continuous wheel position.

## Optional last decimal wheel (0.11.0)

`last_decimal_mode: rolling` enables an independent WaterWatch adapter for jomjol's
`dig-class100-0182-s2_q.tflite`, from AI-on-the-edge-device commit
`04fad5d3efc518c4c2ad8cd14904f1d966ac3605`.

- Upstream: https://github.com/jomjol/AI-on-the-edge-device
- Asset: `sd-card/config/dig-class100-0182-s2_q.tflite`, 226,656 bytes.
- SHA-256: `78f8ee04f8e195ef9e8e1314c77690d530ef151e24c1cf7ab3c66055d0c2b950`.
- License: upstream Dual Use License v1.0, private/non-commercial use; commercial use
  requires a separate agreement with its rights holder. The unchanged license is
  included as `server/ocr_models/AI_ON_THE_EDGE_LICENSE.txt`. The model is not MIT.
- This is an independent integration, not an upstream release or endorsement.
- Runtime: LiteRT 2.3.0, one CPU thread. Input is the entire outer ROI resized to
  RGB float32 `[1,32,20,3]`, values 0..255. The model outputs 100 logits for phases
  0.0 through 9.9. A stable softmax is aggregated over each completed-digit bucket;
  these support scores are not calibrated accuracy probabilities.

Only the last physical decimal ROI uses the phase model. Two resize views must
support the same completed digit, with phase distance at most 0.2, and both scores
must meet the configured threshold. Interior phases strictly between n+0.2 and
n+0.8 can yield n. Thus 7.5 supports a completed 7, and 9.5 supports 9 rather than
rounding to 0. A strong nonadjacent text prediction vetoes this result. Near an
integer boundary, confident agreeing text OCR must also verify the centered digit.
Uncertain views, low support, missing weights or poor image quality fail closed.
No previous reading, supplied test label or user's description is used to infer a
missing digit. Other wheels retain their existing text checks; coupled mechanical
carry on higher wheels is not solved by this feature.

The model and adapter still need validation on the mounted meter, including
transitions and carries. Unit tests are policy checks, not 100-photo accuracy
verification. Existing fixed-reading results do not qualify a new camera mount.

## Analog pointers (0.12.0)

Analog regions use WaterWatch's geometric OpenCV reader, with no additional model
asset. HSV red selection or an Otsu dark mask feeds 720 angular rays in two annular
bands (0.32–0.70 and 0.48–0.84 of the calibrated ellipse radius). Competing lobes,
poor radial support, inconsistent angles or a near-integer boundary remain
uncertain. These scores are geometric support, not neural probabilities. A source
crop must be at least 32 px in both dimensions. The dial centre, zero and direction
must be calibrated. New UI dials remain preview-only until their decimal scale is
explicitly confirmed. Glass-reflected red shapes can prevent reliable detection.
