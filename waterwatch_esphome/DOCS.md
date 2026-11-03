# Setup after review

Add `https://github.com/martincortina/esphome-waterwatch` to the Home Assistant app store repositories, then install
**WaterWatch ESPHome OCR**. Leave MQTT publication disabled during calibration.
The local-ZIP method below is an alternative for manual installations.

1. Use the supplied `esphome/waterwatch-ai-thinker.yaml` as a **new** device, with its
   own Wi-Fi, native API encryption and OTA credentials in ESPHome `secrets.yaml`.
   Generate new credentials; the example values are public validation fixtures.
   Do not overwrite an existing pulse-counter configuration.
2. Mount/focus the camera rigidly on the water meter. The onboard light starts off.
   For flash photographs, select its ESPHome light entity in WaterWatch, enable
   **Flash for each picture**, choose brightness (start at 20%), and
   save. Keep this light off between pictures. Preview, test and recorded/scheduled
   captures then use the same flash sequence. No firmware update is needed for an
   existing ESPHome monochromatic LED. Confirm orientation, focus and every digit.
3. After separate authorization to install, place the entire generated
   `waterwatch_esphome` directory in Home Assistant's `/local_apps` folder
   (`/addons` on older installations), reload the store, install, start and open its Web UI.
   Build on amd64/aarch64 with internet access for Python packages and Tesseract.
   The template folder in this source repository alone is not a complete build context;
   use the generated package, which includes the server and dependency lock.

   With Studio Code Server and Terminal & SSH already installed, upload
   `waterwatch-esphome-addon.zip` into Studio Code Server's `/config` folder using
   its file explorer. In Terminal & SSH's Web UI run:

   ```sh
   unzip /config/waterwatch-esphome-addon.zip -d /local_apps
   ```

   The resulting `/local_apps/waterwatch_esphome/config.yaml` must be directly in
   the app folder, with `Dockerfile`, `requirements.lock` and `server/` beside it.
   In Settings → Apps (or Add-ons) → Store, choose ⋮ → Check for updates, then
   select **WaterWatch ESPHome OCR** under Local apps and install it. Keep
   `publish_to_mqtt: false`, start it, and open Web UI.
4. Keep `publish_to_mqtt: false`. Register a meter with a stable ID such as
   `house-water`, choose Home Assistant camera as the source, load/select its entity,
   and save. Entity IDs are selected from HA; do not assume an example ID is correct.
5. Preview the camera, draw one box per digit from left to right, choose decimal
   places, and save. Test camera OCR without writing readings. Try a range of real
   lighting conditions and partially rotated wheels. Never lower thresholds merely
   to make ambiguous images pass.
6. Record the first camera image if you want a persistent calibration preview. It is
   stored even before a baseline, with a rejection reason. Verify the physical counter
   and enter a manual baseline with a clear reason. Enable scheduled readings only
   after the camera source and capture interval are correct. Default interval: 300 s.
7. Review the local history against the physical meter over representative conditions.
   Later, with authorization to create HA entities/statistics, enable
   `publish_to_mqtt` and restart. This uses the MQTT service registered with Supervisor.
   If no service is available, publication-enabled startup fails with a safe diagnostic.
   This initial profile expects the normal internal, non-TLS MQTT service. External or
   private-CA brokers should use the existing standalone Docker deployment instead.
8. MQTT discovery creates a cumulative water sensor (`m³`, `water`,
   `total_increasing`), flow and diagnostics. Select the total in HA's water dashboard
   and optionally adapt the repository's utility-meter example. These are new
   `waterwatch_*` IDs; the old `watermeter_*` entities are not reused or overwritten.

## What the add-on reads and writes

- By default HA access is limited to GETs for states and camera images. Flash is
  disabled unless a meter's `source.flash` explicitly selects a light. With flash
  selected, the only additional writes are `light.turn_on` with a timed flash and
  `light.turn_off`, targeting that one entity. There are no ESPHome editing, firmware
  upload or general-purpose HA action methods.
- Supervisor grants `homeassistant_api` an administrator-capable token, not an enforced
  read-only scope. The request restrictions are implemented by this client and tested.
- It writes configurations, images, corrections and readings only in its own `/data`.
- MQTT discovery, state and availability are sent only when publication is enabled.
- No host config directories, Docker socket, host network or privileged hardware access
  are requested. Ingress accepts only the Supervisor peer at `172.30.32.2`, with proxy
  header trust disabled. There is no additional password or exposed host port.
- Supervisor and broker credentials stay in memory. Exported meter JSON has no
  credentials. Backups stop the add-on so SQLite and images can be copied consistently.

## Workshop layout

- **Appearance** at the top offers Dark, Light and System. Dark is the default; only
  this preference is stored in the browser. Credentials stay in memory.
- **Capture & calibrate** puts Preview, Test OCR and Record reading next to the image.
  Camera/flash and recognition settings sit beside it on desktop and below it on
  mobile. Advanced settings and uploaded images are collapsed by default.
- Draw one region per digit, left to right. Zoom in for detail, undo the last region,
  or clear regions. A draft outline follows your pointer while drawing.
- Use **↶ 90° / ↷ 90°** below the image for quarter turns, or **Angle / the
  straightening slider** for a precise angle from -180° to 180° (positive is
  clockwise). Rotate until the digits are horizontal, then draw their regions and
  save. The angle is stored per meter and applies to both preview and host OCR.
  Existing digit boxes are transformed with the photo. If a box cannot fit, the
  orientation change is refused; adjust/clear that layout first. Photo rotation
  resets alignment landmarks and the reference; Discard restores saved calibration.
  With alignment enabled, this rotates the saved calibration reference rather than
  changing its coordinates to an unregistered live frame.
  Original shows the unchanged source image. Rotation never triggers another flash.
- To refine existing regions, use **Edit digits**: click a box, drag inside to move
  it, or drag one of its eight handles to resize. The selected box is yellow.
  The **Digit** picker also selects boxes when they overlap. **Delete digit** removes
  only that box; **Undo / Redo** restore edits, additions, deletions and Clear.
  **Add digit** draws another box in reading order (up to 12). Editing a box keeps
  its number. Handles stay usable when zoomed; boxes remain inside the image.
- To tilt only one digit box, select it in **Edit digits**, then drag its round
  rotation handle or use **Digit rotation → Angle**, quarter turns or Reset digit
  angle. Positive angles turn the box clockwise; the host OCR rectifies this box
  to an upright crop. The image's overall rotation and other digit boxes stay fixed.
  A box near an edge may shift just enough to fit; resize it first if the requested
  orientation cannot fit. Do not rotate a box unless the digit itself is tilted.
  The square resize handles follow its new orientation and keep the opposite side
  anchored. Angle changes are saved and support the same Undo / Redo history.
- With the image focused, arrow keys move the selected region by one image pixel;
  Shift moves by ten. Alt + arrows resize its right/bottom edges. Delete removes the
  selection, Ctrl/Cmd + Z undoes, Ctrl/Cmd + Shift + Z redoes, and Escape cancels a
  drag or clears selection. The editor keeps up to 50 undo steps during the session.
  The [ / ] keys rotate the selected box by 1° (Shift: 10°).
  Changing meter, applying JSON, discarding changes or rotating resets this history.
- The save bar shows unsaved changes. **Save & preview** and **Save & test OCR** save
  the edited configuration before that action. **Discard changes** restores the last
  saved configuration. Refresh and meter switching preserve unsaved work until you
  save or discard it. Edited JSON must first be applied to the editor.
- **History** contains the counter graph, reading filters, exported logs, and manual
  baseline/correction events. An OCR test leaves the reading history unchanged.
- A camera OCR test displays the exact photograph used, with upright digit crops,
  each pass's result/score, agreement counts and brightness/highlight measurements.
  Quality warnings are advisory; original sharpness/brightness rejection gates remain.
  Tests are labelled **Not recorded** and do not write the reading ledger or MQTT
  outbox. **Record reading** explicitly stores a capture and result.
- **Move / rotate all** applies a common shift/rotation to all boxes without changing
  their order or spacing. Uncheck it to resize a single region. **Precise digit
  position & size** provides percentage coordinates and a Duplicate digit action.
- **Collect a test example** downloads a JSON bundle containing the exact photo,
  aligned image, crops, calibration, scores and an optional human-verified label.
  Keep these files private. No upload, automatic training or reading correction is
  performed by this export. Tests otherwise remain only in browser memory.

Perspective points remain in the original source-image coordinates. Processing order
is source image → perspective correction (if configured) → rotation → optional landmark alignment → digit regions.
Each region may have its own `rotation_degrees` in [-180, 180], defaulting to 0
for older configurations. Its x/y/w/h describe the unrotated rectangle; it rotates
about its centre in aligned-image pixel coordinates. OCR maps the oriented region
to an upright crop before quality checks. Rotated corners must remain in the image;
an invalid imported region returns `roi_outside_image` rather than reading it.
The preview uses the same server transform as OCR. Quarter turns preserve pixels;
fine-angle rotation expands the canvas with white borders so the image is not cropped.
Old configurations default to 0°. Stored source images and historic readings remain
unchanged. Arbitrary-angle output is limited to 10 megapixels to bound memory use.

## Automatic alignment

1. Pause alignment and save first if it was enabled. Preview a clear photo with the
   intended orientation and calibrated digit boxes. Use diffuse light and fixed optics.
2. Open **Automatic alignment** and choose **Save displayed photo as reference**.
   This saves a private calibration photo in the add-on data, not a water reading.
3. Mark two distinctive, stationary landmarks far apart and clear of frame edges.
   Logos/case markings are preferable to moving digits, pointers, glare or plain areas.
   The image scrolls into view; Escape cancels landmark drawing.
4. Enable **Align each photo automatically**, save and test. Check both match scores
   and all digit crops. The default search margin is 12%, score threshold 0.85 and
   maximum rotation 8°. These are rejection limits, not a guarantee every movement
   inside them will match. Similar alternative matches and scale changes over 2.5%
   also stop recognition. A digit mapped outside the source photo is rejected.
5. Large movement, changed focus/resolution, reference orientation or perspective
   requires a new reference and landmarks. A rejected alignment shows the original
   test photograph, disables region editing on that original, and preserves history.

On reopening the workshop, a saved reference is labelled **Not a live photo**.
Preview or Test OCR retrieves a new camera image. References survive restarts and
are meter-specific. The last five references plus any older active reference are
retained. HA add-on backups contain them; configuration JSON alone does not contain
reference photographs. Alignment is disabled by default on existing meters.

The recognition engine is still Tesseract. Meter-trained models, rolling-wheel/carry
interpretation, analog pointers, an in-app labelled training gallery and measured
real-meter model validation are subsequent work. Exported test bundles prepare data
for that work without claiming that a neural recognizer is installed.

## Recognition boundaries

This is a host-side OpenCV/Tesseract baseline, not the reference project's trained
neural recognizer. It supports a fixed digital wheel layout with per-digit boxes and
optional static perspective correction. It has no trained analog-pointer reader,
reliable half-rolled wheel interpretation. Optional landmark alignment only compensates
small rigid camera movements; it does not guarantee recognition accuracy. Tesseract
scores and two-pass agreement do not establish real meter accuracy.

Unreadable, low-score, implausible, delayed and out-of-order candidates do not replace
the last verified reading. An explicit baseline is required. Corrections are audited.
Consumption checks cannot prove OCR is right; repeated wrong digits can still pass.
Use measured accuracy on your actual meter as the acceptance gate for publication.

Home Assistant's camera proxy does not provide a trustworthy exposure timestamp.
The ledger records retrieval time and labels that timestamp basis. Byte-identical
repeated frames are rejected, including across service restarts, so frozen images do
not refresh the total. This can also mark a genuinely identical static image stale;
new JPEG bytes alone cannot prove the camera delivered a new exposure.

Preview and test leave the reading ledger and MQTT outbox unchanged. When flash is
selected, they briefly operate the selected meter light, just as recorded captures do.
The UI uses POST for previews that can flash; the legacy GET preview remains read-only.
Disabling MQTT later stops publication but does not delete previously retained
discovery/state or HA statistics. Use a reviewed cleanup/migration procedure if needed.

## Flash capture

`source.flash` is optional; omitting it or setting it to `null` keeps HA reads only.
Example for the supplied camera template (choose actual IDs for other devices):

```json
{
  "kind": "home_assistant",
  "camera_entity": "camera.waterwatch_camera_meter_camera",
  "enabled": false,
  "flash": {
    "light_entity": "light.waterwatch_camera_meter_illumination",
    "brightness_pct": 20,
    "settle_ms": 1500
  }
}
```

Use an ESPHome light that advertises flash support. It must be off before capture;
an unavailable, unsupported or manually-on light blocks flash capture. The client
requests HA's `long` flash (ESPHome restores the previous off state after 10 seconds),
waits for exposure, discards two queued frames, retrieves the image, and immediately
switches the light off before OCR. Normal captures finish sooner than the timer.
The timer is a device-side fallback if the host disappears. Retrievals exceeding
the eight-second flash window, invalid images and cleanup failures are not processed.
Capture operations in one service are serialized so their flashes cannot overlap.
Avoid a second controller operating the same light during capture.

This is a brief LED illumination period, not a synchronized photographic strobe.
Adjust brightness/diffusion to avoid reflections. Three image requests per capture
flush queued frames; HA still provides no trusted exposure timestamp. The image and
OCR checks remain necessary. No readings are published merely by enabling flash.
