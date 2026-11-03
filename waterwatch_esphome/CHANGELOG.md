# Changelog

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
