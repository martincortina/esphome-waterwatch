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
   Build on amd64/aarch64 with internet access for Python packages and the pinned
   PP-OCRv5 text and optional wheel-phase models. Weights are SHA-256-verified at build time; recognition
   runs offline on the HA host, without model downloads at startup.
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
   In Settings → Apps (or Add-ons) → Store, choose â‹® → Check for updates, then
   select **WaterWatch ESPHome OCR** under Local apps and install it. Keep
   `publish_to_mqtt: false`, start it, and open Web UI.
4. Keep `publish_to_mqtt: false`. Register a meter with a stable ID such as
   `house-water`, choose Home Assistant camera as the source, load/select its entity,
   and save. Entity IDs are selected from HA; do not assume an example ID is correct.
5. Preview the camera, draw one box per digit from left to right, choose decimal
   places, and save. Test camera OCR without writing readings. Try a range of real
   lighting conditions and partially rotated wheels. Never lower thresholds merely
   to make ambiguous images pass.

   In version 0.8.0 each digit has two frames: the solid outer rectangle is its
   recognition crop; the dashed inner rectangle covers the central 60%, with 20%
   margins above, below, left and right. Fit the complete glyph inside the inner
   frame. **Draw around → Digit** adds the outer margins automatically; **Outer
   crop directly** gives control over the full crop. Existing saved crops keep
   their positions. Frames move and rotate together, with numbers outside.

   The local English neural recognizer reads two normalized digit rows and checks
   each character against its source region. A missing character cannot shift
   later digits. Conflicting digits remain `?`, and low-score agreements remain
   below the configured threshold. This is a general text model; verify your
   meter over multiple captures before enabling automatic readings.
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
8. MQTT discovery creates a cumulative water sensor (`mÂ³`, `water`,
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
- Use **â†¶ 90Â° / â†· 90Â°** below the image for quarter turns, or **Angle / the
  straightening slider** for a precise angle from -180Â° to 180Â° (positive is
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
  The [ / ] keys rotate the selected box by 1Â° (Shift: 10Â°).
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
Old configurations default to 0Â°. Stored source images and historic readings remain
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
   maximum rotation 8Â°. These are rejection limits, not a guarantee every movement
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

The default recognizer uses pinned PP-OCRv5 text models. Optional last-decimal
phase recognition is described below. Coupled carries and a labelled training gallery remain subsequent work. New mounts need validation.

## Recognition boundaries

This is host-side OpenCV/PP-OCRv5 recognition with a general English model. It supports a fixed digital wheel layout with per-digit boxes and
optional static perspective correction. Analog pointers use calibrated geometric detection, not a trained pointer model.
Last-decimal phase recognition remains
experimental and can reject intermediate or boundary positions. Optional landmark alignment only compensates
small rigid camera movements; it does not guarantee recognition accuracy. Recognition
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


## Landmark editing, log deletion and rolling wheels (0.9.0)

Open **Automatic alignment → Edit landmarks on reference**. When alignment is
active, **Pause & edit landmarks** disables and saves it before editing. Select a
blue landmark and drag it or its handles, use the percentage fields, or choose
**Redraw**. **Delete landmark** removes it; remaining landmarks are renumbered.
Undo/Redo works for these edits. Two fixed, distinctive landmarks must remain
inside the image, below 10% of its area each and at least 15% apart. Enable and save
alignment again after completing the edit. Digit boxes are preserved.

In **History**, delete one row, select multiple rows, delete rejected readings, or
clear the log. Review the count and confirm **Delete permanently**. The deletion
is meter-specific and includes associated capture photos; new records arriving after
the review are kept. Logs are removed from the local database and export. The current
verified counter, its validation timestamp and minimal event/image fingerprints are
retained separately to prevent a counter reset, replayed upload or frozen camera
from corrupting consumption. Home Assistant statistics and exported copies are not
removed. This does not erase the entire add-on data directory or backups.

A mechanical wheel can stop between two values. The text recognizer does not
measure its fractional rotation or reconstruct carries. Full precision keeps such
uncertain readings rejected. Where two sufficiently scored passes disagree on
adjacent digits, diagnostics show a **possible wheel transition**, without choosing
one. This is advisory and cannot detect every transition.

To keep reliable larger readings, set **Exclude smallest decimal wheels** in
**Digit recognition**. For eight ROIs and three decimal places, excluding one gives
seven used digits, two decimal places and 10-litre resolution. The eighth ROI and
its OCR/quality diagnostics remain visible; its value is excluded, never filled in
or rounded. Exclude only decimal wheels. Defaults remain zero excluded wheels.
Existing verified totals stay unchanged; if reduced precision falls below a previous
more precise total, monotonic validation waits until the counter catches up.


## Recognition stability (0.10.0)

The host includes pinned English and Latin PP-OCRv5 models. Narrow meter glyphs
and a horizontal reflection can make the English model see a letter instead of a
digit. Five colour/contrast/context Latin views provide additional evidence;
two must agree before assisting a weak or missing English result. Model conflicts
remain rejected. Per-digit cards name the two actual supporting views. Complete
original and assistance predictions remain in the test details and bundle export.
Scores are unmodified model outputs, not calibrated probabilities. Confidence,
quality, baseline and flow thresholds still apply. A hundred successful test frames
qualify that reading and capture setup, not every future wheel position or lighting
condition. Full-precision ambiguous transitions are still unresolved.

## Guided setup and rolling wheel (0.11.0)

Use **Take a photo > Adjust digits > Check reading**. The digit editor is folded
away for normal use. After changing the support, take a fresh photo, open
**Adjust photo & digits**, straighten the row and move or rotate all boxes together.
Check each inner frame, then refresh the alignment reference and fixed landmarks
if the camera view changed. **Done editing** closes tools; it does not save.
**Check reading** saves pending calibration before a non-recording test.
Per-digit cards show the crop, result, direct **Adjust box** action and expandable
scores. Results give a next action for the first failing digit.

In **Digit recognition**, choose **Rotating wheel > phase model (experimental)**
for a smallest decimal wheel that is usually between numbers. Set decimal places
and keep its full outer ROI visible. Two views must agree on its completed digit;
7.5 means the wheel has completed 7 and is moving toward 8. It does not round up.
Near a carry it also needs confident matching text OCR; uncertain results remain
rejected. This does not infer coupled carries in the larger wheels.

The phase model by jomjol / AI-on-the-edge-device has a separate private,
non-commercial license, included in the image; commercial use needs permission
from that project's rights holder. See `models/README.md` and the unchanged
`server/ocr_models/AI_ON_THE_EDGE_LICENSE.txt`. WaterWatch is an independent
integration. Models are downloaded and SHA-256-checked only at image build.

**Reading precision** explicitly controls excluded decimal wheels. Choosing a
larger litre unit can keep reliable larger digits while leaving a distorted wheel
visible for inspection. It reduces precision rather than increasing confidence.
Neither mode nor precision is changed automatically in an existing configuration.
Validate fresh photos on the actual meter before relying on automatic readings.

## Compact workspace, baseline and analog dials (0.12.0)

**Read** keeps the counter crop and result together. The full photo and calibration
controls live in **Setup**. Expand **Digits & dials** only when investigating a
region; scores, exports and uploaded-image tools remain available in Setup.
Unsaved changes reveal the save bar. Setup retains the full source image and zoom.
The focused Read image is only a display crop; saved ROI coordinates stay in the
full aligned photo.

To establish a baseline, select **Set baseline** on Read. The app opens History's
baseline form. Enter the meter total you verified physically in m³, add a reason
(at least eight characters), and select **Record baseline**. For example, a counter
of 00158.837 means 158 m³ plus 837 litres; do not multiply the red decimals by ten.
A test result is a candidate, not physical confirmation. The shortcut leaves the
value empty and never records automatically. A baseline establishes the starting
total for future validation, preserving previous events. Automatic capture and
MQTT publication are separate settings and stay unchanged by baseline setup.

For a numbered 0–9 dial with a directional red or dark pointer:

1. Take a photo and open **Setup > Add dial**. Draw around the complete dial,
   including the scale. The box must contain at least 32 source-image pixels in
   each dimension; a larger, focused crop gives more useful evidence.
2. Select the new dial, choose pointer colour and clockwise/counterclockwise order.
   Mark its pivot with **Mark centre**, then click the printed 0 tick with
   **Mark 0 on scale**. Fit the blue circle to the rim using the radius setting.
   A non-circular view uses the crop's two dimensions to rectify the ellipse.
3. Check the reading. The dial starts with `include_in_reading: false`: its position
   appears in diagnostics but cannot alter the current reading or decimal point.
4. Confirm the printed multiplier **per numbered step**, not per full revolution.
   If the current counter has three decimals, the next dial must be 0.0001 m³ per
   step (0.1 L); a full numbered 0–9 revolution then spans 0.001 m³ (1 L).
   The UI calculates this scale. Select **Confirm ×0.1 & include in total** and
   save. This appends one decimal and selects full precision. Use **Add next dial
   ×0.1** for another dial, draw its region and confirm inclusion in reading order.
   With three counter decimals, successive dial steps are 0.1 L, 0.01 L and 0.001 L.
   The total supports up to six decimal places in m³; finer dials may be previewed.
   Verify the physical ratio before confirming; arbitrary multipliers are unsupported.
5. Undo restores the region and its decimal layout. Duplicated dials start as
   preview-only. Removing the last included decimal dial restores the prior
   precision. Check the reading again before enabling automatic capture.

Pointer detection isolates the configured colour, excludes the central hub and
outer scale, and compares angular evidence in two annular bands. Their angles must
agree within four degrees and their completed digits must agree. A 0.08-digit
boundary guard avoids changing a counter digit due to small angle errors. Support
scores describe geometric separation from competing rays, not calibrated accuracy
probabilities. Image-quality and configured confidence checks still apply. The
implementation uses OpenCV only and does not download an analog model.

A red reflection, counterweight, second pointer, obstruction or incorrect pivot
can make the dial uncertain. Full-turn spinners and unnumbered flow indicators
are unsupported. A preview is not proof of measurement accuracy. Verify the scale
and pointer orientation against the physical meter at several positions, including
carries; this feature does not infer coupled carries in larger wheels.

## Verified visual references (0.12.1)

If glass distortion or a counterweight defeats the general recognizer, an
administrator can attach private, physically verified reference crops to a region.
`POST /api/meters/{id}/recognition-reference?region=0&value=1` accepts a source
JPEG/PNG and returns a reference descriptor. Append that descriptor to the region's
`visual_references` in its saved configuration. Region indices start at zero;
each region supports at most 80 references. Digital labels must be integers
with no uncertainty. A pointer label is a position on its 0–9 scale and can include
an explicit `uncertainty` up to 0.49. Use several distinct calibration photos;
reserve fresh photos for validation. Never label an unknown digit by its OCR result.

Digital reference recognition compares foreground shape and edges, with at least
0.85 support in both views, or the configured threshold if higher. Strong unrelated
text predictions veto a reference match. Ordinary confident OCR remains in use
for digits that do not need references. A reference does not train the general
model or infer missing numbers from the previous reading.

Pointer references compare angular colour profiles in two radial bands, tolerate
white-balance changes, and follow pointer rotation. Both bands need sufficient
correlation, separation from competing angles and angular agreement. Their scores
are measured similarities, not probabilities. A verified approximate position near
a numbered tick can be shown in a preview while its completed digit remains
uncertain. It cannot silently supply a numeric digit to the total.

References stay in the app's private data directory and backups. Changing region,
image orientation, dial calibration or alignment invalidates the affected
references; recapture and verify them after such changes. Removing descriptors
from the configuration disables their use. References are calibration aids, not
proof of accuracy at unseen positions. Test transitions, glare and moving pointers
separately. A stationary 100-photo run measures repeatability at that state.

### Uneven light and region geometry (0.12.2)

For a static digit, a strong numeric model view can corroborate a reference when
uneven illumination changes its binary foreground. The additional comparison
requires at least 0.90 contrast correlation, 0.85 edge agreement, a foreground
overlap floor and an independently qualifying numeric model vote for that label.
The result reports the original numeric score and measured reference support;
it does not raise a weak model score. Strong original reference matches retain
their existing behavior. This additional route cannot bypass a rolling wheel's
carry checks.

Keep metal frame edges and large reflections outside each digit region. Confirm
the outer box and its inner guides against the actual number window, including
between-number positions. An oversized box can make the phase model treat a
transition as a centered digit. Recheck after moving or enlarging a box; prior
reference crops are tied to its geometry.

For a dial seen at an angle, use a rectangular region around its elliptical face
and place the center on the pointer pivot. The radius should follow the dial
inside the printed scale on both axes. A square region centered elsewhere can
make two angular bands disagree. Recreate references after changing this geometry.
Coarse labels must carry their uncertainty: for example, a verified position
between 3 and 4 can be represented as 3.5 with uncertainty 0.49. Such a reference
supports an approximate preview, not an exact decimal measurement. Confirm the
physical multiplier and verify other positions before adding the dial to totals.

### Character disagreement on a split wheel (0.12.3)

A partial pair of numbers can look like an unrelated character to ordinary OCR.
For an interior wheel transition, two agreeing phase views still need the full
configured confidence and carry checks. When both English and Latin character
models are available, a competing unrelated number vetoes that result only if
both independently trained character models support the same number at the
configured threshold. An isolated text-model guess is retained in diagnostics.
Adapters without independent model evidence retain the conservative text veto.
This policy does not relax near-integer carry checks or round the next digit up.


## Dial chains and individual wheel recognition (0.13.0)

In **Setup**, select a number wheel and choose its recognition mode. **Rolling**
uses phase recognition even for a higher wheel during a carry. A wheel moving
from 5 to 6 contributes the completed digit 5; a 9-to-0 transition contributes 9
until the carry has completed. Do not round an incomplete wheel up.

If distortion makes the generic phase model consistently recognize the wrong
pair, use **Teach a verified wheel photo**. Take a photo, enter the physically
verified completed digit (0–9), and save it. This selects **Learned** mode for
that region. Use several distinct poses for each digit. A matching reference
uses measured foreground/edge agreement; an unseen pose requires agreement of
both independent text models with the phase model or remains uncertain. A
reference is not a replacement for validation on fresh images.

For a difficult dial, **Teach a difficult dial position** saves its completed
integer digit as a private sector reference. For example, a pointer between 6
and 7 has completed digit 6. This labels a sector, not an exact angle of 6.0.
Only matching absolute pointer poses qualify in both angular bands; arbitrary
rotations do not inherit the label. The API uses `kind=sector`; its existing
`kind=position` references retain their prior position/uncertainty semantics.

Teaching a reference does not record consumption, change a baseline or publish
MQTT. Up to 80 references per region are supported. Photos and calibration stay
in private app storage and backups. Recheck references after moving the camera
or changing region geometry. Reserve fresh photos and different wheel/dial
positions for accuracy checks; reusing the calibration photo measures only a
calibration round trip.


### Reflective wheel references under flash (0.13.1)

In Learned mode, thin reflective strokes may change their binary edge map with
small JPEG or flash variations. When the strict foreground/edge comparison does
not qualify, a second private-reference route requires a qualifying numeric
model vote, contrast correlation of at least 0.97 and signed-gradient correlation
of at least 0.93. Higher configured thresholds still apply. Comparisons ignore
translated padding, and a differently labelled reference must remain at least
0.10 below the chosen match. Diagnostics report the original model score and
measured image correlations. Unmatched positions remain uncertain; this does not
change ordinary text recognition or infer a value from previous readings.


### Selecting pointer calibration precision (0.13.2)

When several pointer position references pass the existing correlation,
competing-peak and two-band angle checks, prefer the reference with the lowest
label uncertainty. Use match quality to break precision ties. Coarse labels such
as 4.5 ±0.49 must not override a more precise reference solely because their
correlation is slightly higher. Conflicting reference angles still reject the
result; the selected reference retains its original scores and uncertainty.


### Bounded flash retries (0.13.3)

A flash capture may take up to three separate photos if OCR is ambiguous,
unreadable, low-confidence or reports poor image quality. Each flash has the
existing timeout and cleanup. A qualifying result uses all digits from one
fresh photo; digits are never combined across frames or copied from history.
Frozen/repeated images and changed configurations are rejected. Baseline, flow,
timestamp and other validation rejections do not trigger additional photos.

Intermediate OCR checks do not write readings or MQTT. A recorded cycle writes
at most one final result; a test cycle writes none. Capture details list attempt
image hashes, raw results, reasons and scores; the UI shows retry counts. If all
three photos remain uncertain, the result stays rejected. When testing stability,
report successful capture cycles separately from total OCR photos and retries;
100 successful cycles do not mean every individual photo was readable.


## Photo archive

Open **Photos** and enable **Archive photos automatically** to begin. From the
gallery you can view the selected meter's photos or download the original
JPEG or PNG. WaterWatch archives previews, OCR tests, recorded and rejected
readings, and each photo used in a flash retry. Exposure warm-up frames are
excluded. Originals for the same day share one folder under
`/media/waterwatch/YYYY-MM-DD/`. Names begin with the capture date and time in
UTC, followed by a unique suffix. Existing archived photos are moved to this
layout automatically. The gallery uses the same filenames for downloads.

The archive keeps up to 30 days, with a 2 GB total limit across all meters.
WaterWatch removes the oldest originals and their metadata when necessary.
Turn off **Archive photos automatically** to stop saving new photos. Existing
photos remain viewable and continue to expire normally. This setting is shared
by all meters. The archive is independent of consumption history and starts
with new photos after the update.
