# Changelog

## 0.9.0

- Edit, resize, redraw or delete alignment landmarks on the saved reference image.
  Landmark edits support Undo/Redo and precise coordinates. Deleting a landmark
  keeps alignment disabled until two valid landmarks are restored.
- Delete individual, selected, rejected or all reading logs after reviewing a
  confirmation. Capture photos are removed. A minimal verified-counter checkpoint
  preserves validation and consumption continuity; deleted upload IDs stay deduplicated.
- Optionally exclude the smallest decimal wheels without deleting their ROI boxes.
  Uncertain wheel transitions remain rejected at full precision. Excluded wheels
  cannot lower the confidence or quality of the digits still used in the reading.

## 0.8.0

- Local PP-OCRv5 English recognition replaces the isolated-character Tesseract
  baseline. Two row normalizations must agree on each digit; character positions
  map to their source ROI so missing digits cannot shift the counter.
- Inner and outer ROI frames show the central 60% digit area and 20% margins.
  Draw a digit tightly to add its outer frame automatically, or draw the crop directly.
- Frames rotate together; digit numbers stay outside the outer crop.
- Character scores remain unchanged and the configured acceptance threshold stays
  in force. Missing, conflicting and unclear final digits remain rejected.
- The public English model is pinned by SHA-256, fetched during the container
  build and runs offline on the HA host. Apache-2.0 notices accompany the model.

## 0.7.1

- Preview digit numbers sit outside the boxes, including rotated regions, so the
  meter digits stay visible while calibrating. Badges move below or beside boxes
  near image edges and avoid neighbouring regions where space permits.

## 0.7.0

- Optional reference-image alignment using two fixed landmarks, with confidence,
  ambiguity, movement, scale and clipped-digit checks before recognition.
- OCR tests display their exact source photograph, oriented crops, per-pass scores
  and advisory quality measurements, while preserving reading history and MQTT state.
- Download labelled test bundles locally for later review/model validation.
- Move/rotate all digit boxes together; precise percentage coordinates and duplication.
- Whole-photo rotation reprojects existing digit boxes instead of clearing them.
- Calibration references survive restarts and are private to their meter.

## 0.6.0

- Rotate each digit box independently with a round handle, angle field or quarter turns.
- Rotated boxes support selection, movement, resizing, undo/redo and saved calibration.
- Host OCR rectifies each oriented region before checking quality and reading its digit.
- Legacy digit boxes default to 0 degrees; image rotation remains independent.

## 0.5.0

- Select existing digit boxes and drag to move or resize them with eight handles.
- Select overlapping digits from the picker, delete a selected box, and undo/redo edits.
- Fine keyboard adjustments and zoom-aware handles; digit order is preserved.
- Save or discard the edited layout; orientation changes reset the edit history.

## 0.4.0

- Rotate photographs by 90 degrees or fine-tune any angle to level the meter digits.
- Saved orientation applies to previews and host OCR; source images stay unchanged.
- Shared server transformation keeps preview geometry and digit crops consistent.
- Rotation changes clear previous digit regions so they can be redrawn correctly.

## 0.3.0

- Compact, responsive camera/calibration workspace with a dedicated history view.
- Dark, light and system appearance; only the theme preference is stored locally.
- Visible unsaved changes, discard, save-and-preview/test actions and capture feedback.
- Region drawing preview, undo, image zoom, friendly results and reading filters.
- Advanced configuration and uploads are collapsed by default.

## 0.2.1

- Optional timed ESPHome light flash for preview and OCR captures, with automatic
  device timeout, queued-frame flushing and cleanup on capture failures.
- HA ingress workshop, camera selection, digit calibration and opt-in scheduled OCR.
- Local reading ledger, explicit baselines and optional MQTT discovery/publication.

This is an experimental host OCR baseline. Qualify recognition on the actual meter.
