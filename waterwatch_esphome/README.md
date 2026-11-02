# WaterWatch ESPHome OCR

An experimental Home Assistant add-on that reads an ESPHome camera through Home
Assistant, recognizes a mechanical water counter on the host, and keeps an audited
ledger. Open Web UI provides image previews, digit region calibration, dry-run OCR,
baseline/correction events and history.

Optional flash capture briefly illuminates only the selected ESPHome meter light,
then turns it off before OCR. Flash and scheduled capture are disabled by default.

**Source prototype. Not installed or qualified on a physical meter.** MQTT publication
is disabled by default. The AI Thinker ESP32-CAM firmware example is delivered
separately in the prototype bundle. See `DOCS.md` for setup and limitations.
