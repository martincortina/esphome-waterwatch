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
   **Flash the meter light for each picture**, choose brightness (start at 20%), and
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

## Recognition boundaries

This is a host-side OpenCV/Tesseract baseline, not the reference project's trained
neural recognizer. It supports a fixed digital wheel layout with per-digit boxes and
optional static perspective correction. It has no trained analog-pointer reader,
automatic movement alignment or reliable half-rolled wheel interpretation. Tesseract
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
