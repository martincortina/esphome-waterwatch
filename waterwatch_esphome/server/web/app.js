/* Credentials stay in memory. Only the appearance preference is stored locally. */
"use strict";
const $ = id => document.getElementById(id);
let token = "", meters = [], current = null, savedConfig = null, picture = null;
let cameras = [], flashLights = [], historyRows = [], ingress = false, busy = false, dirty = false, jsonDraft = false;
let showingOriginal = false, startPoint = null, draftPoint = null, layoutMeterId = null, messageTimer;
let sourceBlob = null, transformEpoch = 0, transformAbort = null, rotationTimer, transformPending = false;
let selectedDigit = -1, digitMode = "edit", digitDrag = null, digitUndo = [], digitRedo = [];
const canvas = $("preview"), ctx = canvas.getContext("2d"), stage = $("previewStage");
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const reasons = {
  ok:"Verified", candidate:"Recognized", accepted:"Accepted", stale:"Reading is stale",
  baseline_required:"Baseline required", manual_baseline:"Verified manually", roi_not_configured:"Set digit regions",
  roi_outside_image:"A rotated digit box extends outside the image. Adjust its angle, size or position",
  ambiguous_digit:"Ambiguous digit", poor_image_quality:"Image needs adjustment", unreadable:"Unreadable",
  camera_unavailable:"Camera unavailable", camera_repeated_frame:"Repeated camera image",
  flash_light_must_be_off:"Turn the meter light off before a flash capture",
  flash_capture_timed_out:"Flash capture timed out. Check the camera connection",
  not_captured:"No capture yet", test_complete:"Test complete", captured:"Capture recorded",
  home_assistant_not_configured:"Home Assistant camera access is unavailable",
  home_assistant_camera_not_selected:"Select and save a Home Assistant camera first"
};
function readable(value) { return reasons[value] || String(value || "Unknown").replaceAll("_", " ").replace(/^./, c => c.toUpperCase()); }
function message(text, error = false) {
  clearTimeout(messageTimer); $("message").textContent = text; $("message").classList.toggle("error", error);
  if (text && !error) messageTimer = setTimeout(() => { $("message").textContent = ""; }, 6500);
}
function applyTheme() {
  const preference = $("theme").value;
  document.documentElement.dataset.theme = preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
  drawGraph(historyRows);
}
try { const choice = localStorage.getItem("waterwatch-theme"); if (["dark","light","system"].includes(choice)) $("theme").value = choice; } catch { /* Storage is optional in embedded views. */ }
$("theme").onchange = () => { applyTheme(); try { localStorage.setItem("waterwatch-theme", $("theme").value); } catch { /* Keep the in-memory choice. */ } };
systemTheme.addEventListener("change", () => { if ($("theme").value === "system") applyTheme(); });
applyTheme();

async function api(path, options = {}, credential = token) {
  const headers = {...(credential ? {Authorization:`Bearer ${credential}`} : {}), ...options.headers};
  if (typeof options.body === "string") headers["Content-Type"] = "application/json";
  const response = await fetch(new URL(path.replace(/^\/+/, ""), document.baseURI), {...options, headers});
  if (!response.ok) {
    let body; try { body = await response.json(); } catch { body = {}; }
    const detail = body.detail || response.statusText;
    throw new Error(Array.isArray(detail) ? detail.map(item => item.msg).join(". ") : readable(detail));
  }
  return response;
}
function act(fn) {
  return async event => {
    event?.preventDefault(); if (busy) return;
    busy = true; updateControls(); message("");
    try { await fn(event); } catch (error) { message(error.message, true); }
    finally { busy = false; updateControls(); }
  };
}
function selected() { return current?.config.id; }
function setDirty(value = true) {
  dirty = value; $("saveState").textContent = value ? "Unsaved changes" : "All changes saved";
  $("saveState").classList.toggle("dirty", value); updateControls();
}
function updateControls() {
  $("workspace").setAttribute("aria-busy", String(busy)); $("busyStatus").hidden = !busy;
  for (const element of document.querySelectorAll("#workspace button, #workspace input, #workspace select, #workspace textarea")) element.disabled = busy;
  $("logout").disabled = busy;
  const fromHa = $("sourceKind").value === "home_assistant";
  for (const id of ["cameraEntity","flashEnabled","scheduledCapture","cameraPreview","cameraTest","cameraCapture"]) $(id).disabled = busy || !fromHa;
  for (const id of ["flashEntity","flashBrightness"]) $(id).disabled = busy || !fromHa || !$("flashEnabled").checked;
  $("led").disabled = $("resolution").disabled = busy || fromHa;
  $("save").disabled = $("discard").disabled = busy || !dirty;
  $("clearRois").disabled = busy || !current?.config.rois.length;
  $("original").disabled = $("overlay").disabled = busy || !sourceBlob;
  $("zoom").disabled = busy || !picture;
  $("cameraPreview").textContent = dirty ? "Save & preview" : "Preview";
  $("cameraTest").textContent = dirty ? "Save & test OCR" : "Test OCR";
  for (const id of ["rotateLeft","rotateRight","rotation","rotationSlider","resetRotation"]) $(id).disabled = busy || !current;
  updateDigitControls();
}
function sourceControls() {
  const fromHa = $("sourceKind").value === "home_assistant";
  if (!fromHa) { $("scheduledCapture").checked = false; $("flashEnabled").checked = false; }
  $("haSourceFields").hidden = !fromHa; $("flashFields").hidden = !fromHa || !$("flashEnabled").checked;
  $("uploadDeviceSettings").hidden = fromHa; $("haSettingsNote").hidden = !fromHa;
  updateControls();
}
function renderChoices(id, choices, selectedEntity, placeholder) {
  const list = [...choices];
  if (selectedEntity && !list.some(item => item.entity_id === selectedEntity)) list.push({entity_id:selectedEntity,name:selectedEntity,state:"saved"});
  $(id).replaceChildren(new Option(placeholder, ""), ...list.map(item => new Option(`${item.name} (${item.state})`, item.entity_id)));
  $(id).value = selectedEntity || "";
}
function renderConfig() {
  if (!current) return;
  const cfg = current.config;
  jsonDraft = false;
  $("config").value = JSON.stringify(cfg, null, 2);
  for (const [id,value] of Object.entries({decimals:cfg.decimal_places,threshold:cfg.min_confidence,maxRate:cfg.max_rate_lph,interval:cfg.camera.interval_seconds,led:cfg.camera.led_duty,resolution:cfg.camera.resolution,sourceKind:cfg.source.kind,flashBrightness:cfg.source.flash?.brightness_pct ?? 20})) $(id).value = value;
  $("scheduledCapture").checked = cfg.source.enabled; $("flashEnabled").checked = Boolean(cfg.source.flash);
  $("rotation").value = $("rotationSlider").value = cfg.rotation_degrees ?? 0;
  $("rotationStatus").textContent = `${cfg.rotation_degrees || 0}° · Clockwise +`;
  renderChoices("cameraEntity", cameras, cfg.source.camera_entity, "Select a camera");
  renderChoices("flashEntity", flashLights, cfg.source.flash?.light_entity, "Select the meter’s ESPHome light");
  sourceControls(); updateRegionCount();
}
function editedConfig() {
  const cfg = structuredClone(current.config);
  cfg.decimal_places = Number($("decimals").value); cfg.min_confidence = Number($("threshold").value);
  cfg.max_rate_lph = Number($("maxRate").value); cfg.camera.interval_seconds = Number($("interval").value);
  cfg.camera.led_duty = Number($("led").value); cfg.camera.resolution = $("resolution").value;
  cfg.rotation_degrees = Number($("rotation").value);
  const fromHa = $("sourceKind").value === "home_assistant";
  cfg.source = {kind:$("sourceKind").value,camera_entity:fromHa ? $("cameraEntity").value : null,enabled:fromHa && $("scheduledCapture").checked,flash:fromHa && $("flashEnabled").checked ? {light_entity:$("flashEntity").value,brightness_pct:Number($("flashBrightness").value),settle_ms:cfg.source.flash?.settle_ms ?? 1500} : null};
  return cfg;
}
async function saveConfiguration(notify = true) {
  if (!current) throw new Error("Add a meter first.");
  if (jsonDraft) throw new Error("Apply the JSON to the editor before saving, or discard your changes.");
  const numericFields = ["decimals","threshold","maxRate","interval","rotation"];
  if ($("sourceKind").value === "home_assistant" && $("flashEnabled").checked) numericFields.push("flashBrightness");
  if ($("sourceKind").value === "upload") numericFields.push("led");
  for (const id of numericFields) {
    const field = $(id), value = Number(field.value);
    if (field.value.trim() === "" || !Number.isFinite(value) || value < Number(field.min) || value > Number(field.max || Infinity)) throw new Error(`Check ${field.closest("label").childNodes[0].textContent.trim().toLowerCase()}.`);
  }
  const cfg = editedConfig();
  if (cfg.source.kind === "home_assistant" && !cfg.source.camera_entity) throw new Error("Choose a camera using Find devices.");
  if (cfg.source.flash && !cfg.source.flash.light_entity) throw new Error("Select the meter’s ESPHome flash light.");
  await api(`/api/meters/${selected()}`, {method:"PUT",body:JSON.stringify(cfg)});
  current.config = cfg; savedConfig = structuredClone(cfg); setDirty(false); renderConfig(); updateHint();
  if (sourceBlob) await renderSourceImage();
  if (notify) message("Configuration saved.");
}
function showView(name) {
  for (const key of ["capture","history"]) {
    const active = key === name;
    $(`${key}Tab`).setAttribute("aria-selected", String(active)); $(`${key}Tab`).tabIndex = active ? 0 : -1;
    $(`${key}View`).hidden = !active;
  }
  requestAnimationFrame(resizePreview);
}
for (const name of ["capture","history"]) {
  $(`${name}Tab`).onclick = () => showView(name);
  $(`${name}Tab`).onkeydown = event => { if (["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? "capture" : event.key === "End" ? "history" : name === "capture" ? "history" : "capture"; showView(next); $(`${next}Tab`).focus(); } };
}
function updateHint() {
  if (!current) return;
  const cfg = current.config; let text, action, target;
  if (cfg.source.kind !== "home_assistant" || !cfg.source.camera_entity) { text = "1. Connect your meter camera, then preview the image."; action = "Choose camera"; target = "camera"; }
  else if (!cfg.rois.length) { text = "2. Preview the meter, then draw a box around each digit."; action = "Preview camera"; target = "preview"; }
  else if (current.state.total_m3 === null) { text = "3. Test recognition, then enter the reading verified on the physical meter."; action = "Set baseline"; target = "baseline"; }
  else { text = cfg.source.enabled ? `Automatic readings every ${cfg.camera.interval_seconds}s${cfg.source.flash ? " · Flash enabled" : ""}.` : "Test the camera reading. Enable automatic readings when calibration is reliable."; action = ""; target = ""; }
  $("setupHintText").textContent = text; $("setupHintAction").textContent = action; $("setupHintAction").hidden = !action; $("setupHintAction").dataset.target = target;
}
$("setupHintAction").onclick = () => {
  const target = $("setupHintAction").dataset.target;
  if (target === "baseline") { showView("history"); $("baselinePanel").open = true; $("manualValue").focus(); }
  else { showView("capture"); if (target === "camera") { $("cameraSettings").open = true; $("sourceKind").focus(); } else $("cameraPreview").click(); }
};
function updateRegionCount() { $("roiCount").textContent = `${current?.config.rois.length || 0} regions`; renderDigitSelection(); }
function digitEditorReady() { return Boolean(current && picture && !busy && !transformPending && !showingOriginal && !jsonDraft && JSON.stringify(current.config.perspective) === JSON.stringify(savedConfig?.perspective)); }
function updateDigitControls() {
  const ready=digitEditorReady(), count=current?.config.rois.length || 0;
  $("editDigits").disabled=!ready; $("addDigit").disabled=!ready || count>=12;
  $("selectedDigit").disabled=!ready || !count;
  $("deleteDigit").disabled=!ready || selectedDigit<0 || selectedDigit>=count;
  for(const id of ["digitAngle","digitRotateLeft","digitRotateRight","digitResetAngle"])$(id).disabled=!ready || selectedDigit<0 || selectedDigit>=count || digitMode!=="edit";
  $("undoRoi").disabled=!ready || !digitUndo.length; $("redoRoi").disabled=!ready || !digitRedo.length;
  $("clearRois").disabled=!ready || !count;
  $("editDigits").classList.toggle("active",digitMode==="edit"); $("addDigit").classList.toggle("active",digitMode==="add");
  $("editDigits").setAttribute("aria-pressed",String(digitMode==="edit")); $("addDigit").setAttribute("aria-pressed",String(digitMode==="add"));
  $("digitHelp").textContent=digitMode==="add" ? "Drag a new box around the next digit. Switch to Edit digits to adjust existing boxes." : "Select a box: drag inside to move, square handles to resize, or the round handle to rotate. Numbers define reading order.";
  canvas.style.cursor=digitCursor(digitDrag);
}
function renderDigitSelection() {
  const count=current?.config.rois.length || 0;
  if(selectedDigit>=count) selectedDigit=count-1;
  const select=$("selectedDigit");
  if(select.options.length!==count+1) select.replaceChildren(new Option("Select a digit","-1"),...Array.from({length:count},(_,index)=>new Option(`Digit ${index+1}`,String(index))));
  select.value=String(selectedDigit); $("digitSelection").textContent=selectedDigit<0?"No digit selected":`Digit ${selectedDigit+1} selected`;
  if(document.activeElement!==$("digitAngle"))$("digitAngle").value=current?.config.rois[selectedDigit]?.rotation_degrees || 0;
  updateDigitControls();
}
function resetDigitEditor() { selectedDigit=-1; digitDrag=null; startPoint=null; draftPoint=null; digitUndo=[]; digitRedo=[]; digitMode=current?.config.rois.length?"edit":"add"; renderDigitSelection(); }
function digitSnapshot() { return {rois:structuredClone(current.config.rois),selected:selectedDigit}; }
function restoreDigitSnapshot(snapshot) { current.config.rois=structuredClone(snapshot.rois); selectedDigit=snapshot.selected; digitDrag=null; startPoint=null; draftPoint=null; }
function digitEditChanged() {
  $("config").value=JSON.stringify(editedConfig(),null,2);
  $("resultCard").hidden=true; $("testResult").textContent=""; $("processed").hidden=true;
  setDirty(jsonDraft || JSON.stringify(editedConfig())!==JSON.stringify(savedConfig)); drawPreview(); updateHint();
}
function commitDigitEdit(before) {
  if(before.rois.length===current.config.rois.length && before.rois.every((roi,index)=>["x","y","w","h","rotation_degrees"].every(key=>Math.abs((roi[key]||0)-(current.config.rois[index][key]||0))<1e-12))) { current.config.rois=structuredClone(before.rois); drawPreview(); return; }
  digitUndo.push(before); if(digitUndo.length>50)digitUndo.shift(); digitRedo=[]; digitEditChanged();
}
function undoDigitEdit(redo=false) {
  if(!digitEditorReady())return;
  const source=redo?digitRedo:digitUndo, destination=redo?digitUndo:digitRedo;
  if(!source.length)return;
  destination.push(digitSnapshot()); restoreDigitSnapshot(source.pop()); digitEditChanged();
}
function deleteSelectedDigit() {
  if(!digitEditorReady() || !current.config.rois[selectedDigit])return;
  const before=digitSnapshot();current.config.rois.splice(selectedDigit,1);selectedDigit=Math.min(selectedDigit,current.config.rois.length-1);commitDigitEdit(before);
}
function minimumDigitSize() { return {w:Math.min(1,9/canvas.width),h:Math.min(1,9/canvas.height)}; }
function digitSize() { return {width:canvas.width,height:canvas.height}; }
function digitRotationHandle(roi) { const box=canvas.getBoundingClientRect();return RoiGeometry.rotationHandle(roi,25*canvas.height/Math.max(1,box.height),digitSize()); }
function digitHit(point) { const box=canvas.getBoundingClientRect(),radius=window.matchMedia("(pointer: coarse)").matches?13:8,roi=current.config.rois[selectedDigit];return RoiGeometry.hitTest(current.config.rois,point,{x:radius/box.width,y:radius/box.height},selectedDigit,digitSize(),roi?digitRotationHandle(roi):null); }
function digitCursor(hit) {
  if(!digitEditorReady())return "default";if(digitMode==="add")return "crosshair";if(!hit)return "default";
  if(hit.handle==="move")return "move";if(hit.handle==="rotate")return "grab";
  const angles={e:0,se:45,s:90,sw:135,w:180,nw:225,n:270,ne:315};
  return ["ew-resize","nwse-resize","ns-resize","nesw-resize"][((Math.round((angles[hit.handle]+(current.config.rois[hit.index]?.rotation_degrees||0))/45)%4)+4)%4];
}
function changeDigitRotation(value,updateInput=true) {
  if(!digitEditorReady() || !current.config.rois[selectedDigit])return;
  const angle=Number(value);
  if(String(value).trim()==="" || !Number.isFinite(angle) || angle < -180 || angle > 180){message("Use a digit angle from -180° to 180°.",true);$("digitAngle").value=current.config.rois[selectedDigit].rotation_degrees || 0;return;}
  const before=digitSnapshot(),roi=RoiGeometry.rotate(current.config.rois[selectedDigit],angle,digitSize());
  if(!roi){message("This angle cannot fit in the image. Make the digit box smaller first.",true);$("digitAngle").value=current.config.rois[selectedDigit].rotation_degrees || 0;return;}
  current.config.rois[selectedDigit]=roi;if(updateInput)$("digitAngle").value=roi.rotation_degrees;commitDigitEdit(before);
}
$("digitAngle").oninput=()=>{const field=$("digitAngle");if(field.value.trim()!=="" && field.validity.valid)changeDigitRotation(field.value,false);};
$("digitAngle").onchange=$("digitAngle").onblur=()=>changeDigitRotation($("digitAngle").value);
$("digitRotateLeft").onclick=()=>changeDigitRotation((((current.config.rois[selectedDigit]?.rotation_degrees||0)-90+540)%360)-180);
$("digitRotateRight").onclick=()=>changeDigitRotation((((current.config.rois[selectedDigit]?.rotation_degrees||0)+90+540)%360)-180);
$("digitResetAngle").onclick=()=>changeDigitRotation(0);
$("editDigits").onclick=()=>{digitMode="edit";drawPreview();};
$("addDigit").onclick=()=>{digitMode="add";drawPreview();};
$("selectedDigit").onchange=()=>{selectedDigit=Number($("selectedDigit").value);digitMode="edit";drawPreview();};
$("deleteDigit").onclick=deleteSelectedDigit;
function resizePreview() {
  if ($("captureView").hidden) return;
  const zoom = Number($("zoom").value) / 100;
  const fit = Math.min((stage.clientWidth - 20) / canvas.width, (stage.clientHeight - 20) / canvas.height);
  if (fit <= 0) return;
  canvas.style.width = `${Math.floor(canvas.width * fit * zoom)}px`; canvas.style.height = `${Math.floor(canvas.height * fit * zoom)}px`;
  stage.classList.toggle("zoomed", zoom > 1); $("zoomValue").value = `${Math.round(zoom * 100)}%`;
}
new ResizeObserver(drawPreview).observe(stage);
$("zoom").oninput = drawPreview;
function drawPreview() {
  resizePreview();
  ctx.clearRect(0, 0, canvas.width, canvas.height); $("previewEmpty").hidden = Boolean(picture);
  if (picture) ctx.drawImage(picture, 0, 0, canvas.width, canvas.height);
  if (current && !showingOriginal) {
    ctx.lineWidth = Math.max(2, canvas.width / 400); ctx.font = `${Math.max(15, canvas.width / 45)}px sans-serif`;
    function outline(roi) {
      const points=RoiGeometry.corners(roi,digitSize());ctx.beginPath();
      points.forEach((p,index)=>{if(index)ctx.lineTo(p.x*canvas.width,p.y*canvas.height);else ctx.moveTo(p.x*canvas.width,p.y*canvas.height);});ctx.closePath();return points;
    }
    current.config.rois.forEach((roi, index) => { const points=outline(roi),x=points[0].x*canvas.width,y=points[0].y*canvas.height;ctx.strokeStyle="#8ff5bc";ctx.stroke();ctx.fillStyle="#123a2a";ctx.fillRect(x,y,25,23);ctx.fillStyle="#c5ffdd";ctx.fillText(String(index+1),x+5,y+18); });
    const roi=current.config.rois[selectedDigit];
    if(roi && digitMode==="edit") {
      outline(roi);ctx.fillStyle="#ffd46a22";ctx.fill();ctx.strokeStyle="#ffd46a";ctx.stroke();
      const box=canvas.getBoundingClientRect(),w=9*canvas.width/Math.max(1,box.width),h=9*canvas.height/Math.max(1,box.height);
      const handles=RoiGeometry.handles(roi,digitSize()),rotation=digitRotationHandle(roi);
      ctx.beginPath();ctx.moveTo(handles.n.x*canvas.width,handles.n.y*canvas.height);ctx.lineTo(rotation.x*canvas.width,rotation.y*canvas.height);ctx.strokeStyle="#ffd46a";ctx.stroke();
      ctx.beginPath();ctx.ellipse(rotation.x*canvas.width,rotation.y*canvas.height,w*.7,h*.7,0,0,Math.PI*2);ctx.fillStyle="#ffd46a";ctx.fill();ctx.strokeStyle="#1e241f";ctx.stroke();
      for(const handle of Object.values(handles)) {
        const hx=handle.x*canvas.width-w/2,hy=handle.y*canvas.height-h/2;
        ctx.fillStyle="#ffd46a";ctx.fillRect(hx,hy,w,h);ctx.strokeStyle="#1e241f";ctx.strokeRect(hx,hy,w,h);
      }
    }
    if (startPoint && draftPoint) { ctx.strokeStyle="#ffffff";ctx.setLineDash([5,4]);ctx.strokeRect(startPoint.x*canvas.width,startPoint.y*canvas.height,(draftPoint.x-startPoint.x)*canvas.width,(draftPoint.y-startPoint.y)*canvas.height);ctx.setLineDash([]); }
  }
  $("original").classList.toggle("active", showingOriginal); $("overlay").classList.toggle("active", !showingOriginal);
  updateRegionCount();
}
async function setPicture(blob, epoch = transformEpoch) {
  const decoded = await createImageBitmap(blob);
  if (epoch !== transformEpoch) { decoded.close(); return; }
  picture?.close(); picture = decoded;
  canvas.width = picture.width; canvas.height = picture.height; $("zoom").value = 100; drawPreview(); updateControls();
}
async function renderSourceImage() {
  clearTimeout(rotationTimer);
  const epoch = ++transformEpoch;
  transformAbort?.abort(); transformAbort = new AbortController();
  if (!sourceBlob || !current) { transformPending = false; return; }
  transformPending = true; $("rotationStatus").textContent = "Updating preview…";
  updateControls();
  const cfg = current.config, original = showingOriginal;
  try {
    let blob = sourceBlob;
    if (!original && (cfg.rotation_degrees || cfg.perspective)) {
      const response = await api(`/api/meters/${selected()}/transform-image?rotation_degrees=${encodeURIComponent(cfg.rotation_degrees || 0)}`, {method:"POST",body:blob,headers:{"Content-Type":blob.type || "application/octet-stream"},signal:transformAbort.signal});
      blob = await response.blob();
    }
    if (epoch === transformEpoch) await setPicture(blob, epoch);
  } catch (error) {
    if (epoch === transformEpoch && error.name !== "AbortError") { picture?.close(); picture=null; drawPreview(); message(error.message,true); }
  } finally {
    if (epoch === transformEpoch) { transformPending=false; $("rotationStatus").textContent = original ? "Original image" : `${cfg.rotation_degrees || 0}° · Clockwise +`; updateControls(); }
  }
}
function changeRotation(value) {
  if (!current || jsonDraft) { if(jsonDraft) message("Apply or discard the JSON edit before rotating.",true); return; }
  const angle = Math.round(Number(value)*10)/10;
  if (!Number.isFinite(angle) || angle < -180 || angle > 180) return;
  $("rotation").value = $("rotationSlider").value = angle;
  if (angle === (current.config.rotation_degrees || 0)) return;
  current.config.rotation_degrees=angle; current.config.rois=[];
  resetDigitEditor();
  $("resultCard").hidden=true; $("testResult").textContent=""; $("processed").hidden=true;
  startPoint=null; draftPoint=null; showingOriginal=false;
  $("config").value=JSON.stringify(editedConfig(),null,2); setDirty(); updateRegionCount(); updateHint(); drawPreview();
  clearTimeout(rotationTimer); transformEpoch++; transformAbort?.abort();
  transformPending=Boolean(sourceBlob); $("rotationStatus").textContent=sourceBlob?"Updating preview…":`${angle}° · Clockwise +`;
  updateControls();
  rotationTimer=setTimeout(()=>{void renderSourceImage();},180);
  message("Rotation changed. Draw new digit regions, then save.");
}
$("rotateLeft").onclick=()=>changeRotation(((Number($("rotation").value)-90+540)%360)-180);
$("rotateRight").onclick=()=>changeRotation(((Number($("rotation").value)+90+540)%360)-180);
$("resetRotation").onclick=()=>changeRotation(0);
$("rotation").oninput=()=>{const value=$("rotation").value;if(value.trim()==="" || !Number.isFinite(Number(value)) || Number(value)<-180 || Number(value)>180){setDirty();message("Use an angle from -180° to 180°.",true);return;}changeRotation(value);};
$("rotationSlider").oninput=()=>changeRotation($("rotationSlider").value);
async function loadImage(variant = "overlay") {
  transformEpoch++; transformAbort?.abort(); clearTimeout(rotationTimer);
  sourceBlob=null; transformPending=false; picture?.close(); picture = null; showingOriginal = variant === "original"; $("processed").hidden = true;
  const row = current?.latest_capture;
  if (!row?.image_key) { $("imageStamp").textContent = "No image yet"; drawPreview(); return; }
  try {
    const response = await api(`/api/meters/${selected()}/images/${row.event_id}/original`);
    sourceBlob=await response.blob(); await renderSourceImage(); $("imageStamp").textContent = `Capture · ${new Date(row.captured_at).toLocaleString()}`;
  } catch (error) { drawPreview(); message(`Image: ${error.message}`, true); }
  if ((row.details?.rotation_degrees ?? 0) !== (current.config.rotation_degrees || 0)) return;
  try {
    const response = await api(`/api/meters/${selected()}/images/${row.event_id}/processed`);
    const oldUrl = $("processed").dataset.url; if (oldUrl) URL.revokeObjectURL(oldUrl);
    const url = URL.createObjectURL(await response.blob()); $("processed").dataset.url = url; $("processed").src = url; $("processed").hidden = false;
  } catch { $("processed").hidden = true; }
}
function drawGraph(rows) {
  const graph = $("graph"), g = graph.getContext("2d"), points = rows.filter(row => row.accepted).slice().reverse();
  g.clearRect(0,0,graph.width,graph.height); $("graphEmpty").hidden = points.length >= 2;
  if (points.length < 2) return;
  const styles = getComputedStyle(document.documentElement), line = styles.getPropertyValue("--line"), muted = styles.getPropertyValue("--muted");
  const values = points.map(row=>Number(row.value_m3)), times = points.map(row=>Date.parse(row.captured_at));
  const min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,.001),duration=Math.max(times.at(-1)-times[0],1);
  g.font="13px sans-serif";g.lineWidth=1;
  for(let i=0;i<4;i++){const y=22+i*46;g.strokeStyle=line;g.beginPath();g.moveTo(92,y);g.lineTo(graph.width-18,y);g.stroke();g.fillStyle=muted;g.fillText((max-span*i/3).toFixed(3),8,y+4);}
  g.strokeStyle=styles.getPropertyValue("--accent");g.lineWidth=3;g.beginPath();
  points.forEach((row,index)=>{const x=92+(times[index]-times[0])/duration*(graph.width-110),y=22+(max-values[index])/span*138;if(index)g.lineTo(x,y);else g.moveTo(x,y);});g.stroke();
  g.fillStyle=muted;g.font="12px sans-serif";g.fillText(new Date(times[0]).toLocaleString(),92,190);const end=new Date(times.at(-1)).toLocaleString();g.fillText(end,graph.width-18-g.measureText(end).width,190);
}
function renderHistory() {
  const filter = $("historyFilter").value;
  const rows = historyRows.filter(row => filter === "all" || (filter === "accepted" ? row.accepted : !row.accepted));
  $("historyCount").textContent = String(rows.length);
  $("history").replaceChildren(...rows.map(row => {
    const tr=document.createElement("tr");
    [new Date(row.captured_at).toLocaleString(),row.raw_value??"—",row.value_m3??"—",`${(row.confidence*100).toFixed(1)}%`,readable(row.reason),row.source === "Docker" ? "Host OCR" : readable(row.source)].forEach((text,index)=>{const td=document.createElement("td");if(index===4){const badge=document.createElement("span");badge.className=`reading-badge ${row.accepted?"good":"bad"}`;badge.textContent=text;td.append(badge);}else td.textContent=text;tr.append(td);});return tr;
  }));
  if (!rows.length) { const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=6;td.className="empty-row";td.textContent=historyRows.length?"No readings match this filter.":"No readings yet. Test OCR, verify the meter and set a baseline.";tr.append(td);$("history").append(tr); }
  drawGraph(historyRows);
}
$("historyFilter").onchange = renderHistory;
async function renderMeter() {
  current = meters.find(meter=>meter.config.id === $("meter").value) || null;
  resetDigitEditor();
  $("meterWorkspace").hidden = !current;
  if (!current) { $("registration").hidden=false; return; }
  $("cameraToken").hidden = $("cameraToken").dataset.meter !== selected();
  savedConfig = structuredClone(current.config); setDirty(false); $("resultCard").hidden=true; $("testResult").textContent="";
  const state = current.state;
  $("total").textContent = state.total_m3 === null ? "—" : Number(state.total_m3).toLocaleString(undefined,{maximumFractionDigits:6});
  $("last").textContent = state.last_success ? new Date(state.last_success).toLocaleString() : "Set a baseline";
  $("freshness").textContent = state.total_m3 === null ? "No verified reading yet" : state.stale ? "Last verified value · stale" : "Verified reading is fresh";
  $("confidence").textContent = state.confidence === null ? "—" : `${state.confidence}%`;
  $("readingStatus").textContent = state.total_m3 === null ? "Baseline required" : readable(state.error); $("readingStatus").className = `small ${state.total_m3 === null ? "warning" : state.error === "ok" ? "good" : "bad"}`;
  $("rssi").textContent = state.rssi === null ? (current.config.source.kind === "home_assistant" ? "Home Assistant camera" : "Waiting for an image") : `Wi-Fi ${state.rssi} dBm`;
  $("captureStatus").textContent = readable(current.capture_status?.status || "not_captured");
  renderConfig(); updateHint();
  if (layoutMeterId !== selected()) { const compact = window.matchMedia("(max-width: 760px)").matches; $("cameraSettings").open = !compact || !current.config.source.camera_entity; $("recognitionSettings").open = !compact; layoutMeterId = selected(); }
  await loadImage(); historyRows = await (await api(`/api/meters/${selected()}/history`)).json(); renderHistory();
}
async function refresh() {
  if (dirty) { message("Save or discard your changes before refreshing.", true); return; }
  const id = $("meter").value;
  const [list,status] = await Promise.all([api("/api/meters").then(response=>response.json()),api("/api/status").then(response=>response.json())]);
  meters=list; $("meter").replaceChildren(...meters.map(meter=>new Option(meter.config.name,meter.config.id)));
  if (meters.some(meter=>meter.config.id === id)) $("meter").value=id;
  $("connection").textContent=`OCR ${status.ocr_available?"ready":"unavailable"} · MQTT ${status.mqtt === "disabled"?"off":status.mqtt}`;
  $("connection").className=`status-chip ${status.ocr_available?"good":"warn"}`;
  await renderMeter();
}
function renderResult(result, testOnly) {
  const accepted = testOnly ? result.would_accept : result.accepted;
  $("resultCard").hidden=false; $("resultCard").classList.toggle("rejected", !accepted);
  $("resultMode").textContent=testOnly?"OCR TEST · NOT RECORDED":"RECORDED CAPTURE";
  $("resultValue").textContent=result.raw_value ? `${result.raw_value} m³` : "Unreadable";
  $("resultReason").textContent=readable(result.reason); $("resultReason").className=`result-reason ${accepted?"good":"bad"}`;
  $("resultConfidence").textContent=`${((result.confidence||0)*100).toFixed(1)}% confidence${testOnly?" · History unchanged":""}`;
  $("testResult").textContent=JSON.stringify(result,null,2);
}

$("loginForm").onsubmit=act(async()=>{token=$("token").value;try{await refresh();$("token").value="";$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=false;requestAnimationFrame(resizePreview);}catch(error){token="";throw error;}});
$("logout").onclick=()=>{token="";current=null;savedConfig=null;meters=[];historyRows=[];setDirty(false);$("workspace").hidden=true;$("login").hidden=false;$("logout").hidden=true;$("cameraToken").textContent="";$("uploadToken").value="";$("manualValue").value="";$("manualReason").value="";$("newName").value="";$("newId").value="";$("history").replaceChildren();$("testResult").textContent="";$("resultCard").hidden=true;$("processed").hidden=true;$("processed").removeAttribute("src");if($("processed").dataset.url)URL.revokeObjectURL($("processed").dataset.url);transformEpoch++;transformAbort?.abort();clearTimeout(rotationTimer);sourceBlob=null;picture?.close();picture=null;drawPreview();message("");};
$("refresh").onclick=act(refresh);
$("meter").onchange=act(async()=>{if(dirty){$("meter").value=selected();throw new Error("Save or discard changes before switching meters.");}await renderMeter();});
$("add").onclick=()=>{$("registration").hidden=!$("registration").hidden;if(!$("registration").hidden)$("newName").focus();};
$("cancelRegistration").onclick=()=>{if(current)$("registration").hidden=true;};
let customId=false;
$("newId").oninput=()=>{customId=Boolean($("newId").value);};
$("newName").oninput=()=>{if(!customId)$("newId").value=$("newName").value.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,48);};
$("registerForm").onsubmit=act(async()=>{if(dirty)throw new Error("Save or discard changes before adding a meter.");const data=await(await api("/api/meters",{method:"POST",body:JSON.stringify({id:$("newId").value,name:$("newName").value})})).json();$("cameraToken").textContent=`${data.config.name} upload token (shown once): ${data.camera_token}`;$("cameraToken").dataset.meter=data.config.id;await refresh();$("meter").value=data.config.id;await renderMeter();$("registration").hidden=true;showView("capture");message("Meter created. Select Home Assistant camera, then find your devices.");});
$("save").onclick=act(()=>saveConfiguration());
$("discard").onclick=()=>{if(!current||!savedConfig)return;current.config=structuredClone(savedConfig);resetDigitEditor();renderConfig();setDirty(false);void renderSourceImage();drawPreview();updateHint();message("Unsaved changes discarded.");};
for(const element of document.querySelectorAll(".settings-column input, .settings-column select")){element.addEventListener("input",()=>{sourceControls();setDirty();});}
$("config").oninput=()=>{jsonDraft=true;setDirty();};
$("loadJson").onclick=()=>{try{const cfg=JSON.parse($("config").value);if(cfg.id!==selected())throw new Error("Meter ID cannot change.");if(!cfg.camera||!cfg.source||!Array.isArray(cfg.rois))throw new Error("Configuration must include camera, source and digit regions.");current.config=cfg;resetDigitEditor();renderConfig();setDirty();drawPreview();void renderSourceImage();message("JSON loaded into the editor. Save to apply.");}catch(error){message(error.message,true);}};
function point(event){const box=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),y:Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))};}
function cancelDigitDrag() {
  if(!digitDrag)return;
  restoreDigitSnapshot(digitDrag.before);drawPreview();
}
canvas.onpointerdown=event=>{
  if(!event.isPrimary || event.button!==0 || digitDrag)return;
  if(!digitEditorReady()) {
    if(showingOriginal)message("Switch to Aligned / regions to edit digit boxes.",true);
    else if(jsonDraft)message("Apply or discard the JSON edit before editing digits.",true);
    else if(current && JSON.stringify(current.config.perspective)!==JSON.stringify(savedConfig?.perspective))message("Save perspective settings before editing digit regions.",true);
    return;
  }
  event.preventDefault();canvas.focus({preventScroll:true});
  const position=point(event),before=digitSnapshot();
  if(digitMode==="add") {
    if(current.config.rois.length>=12){message("Up to 12 digit regions are supported.",true);return;}
    startPoint=position;draftPoint=position;digitDrag={kind:"add",before,pointerId:event.pointerId};
  } else {
    const hit=digitHit(position);
    selectedDigit=hit?.index ?? -1;
    if(hit)digitDrag={...hit,kind:"edit",before,origin:position,roi:{...current.config.rois[hit.index]},pointerId:event.pointerId};
  }
  if(digitDrag)canvas.setPointerCapture(event.pointerId);
  drawPreview();
};
function updateDigitDrag(position) {
  if(digitDrag.kind==="add")draftPoint=position;
  else {
    const {roi,origin,handle,index}=digitDrag;
    if(handle==="rotate") {
      const delta=RoiGeometry.angleAt(roi,position,digitSize())-RoiGeometry.angleAt(roi,origin,digitSize());
      const rotated=RoiGeometry.rotate(roi,(roi.rotation_degrees||0)+delta,digitSize());
      if(rotated)current.config.rois[index]=rotated;
      drawPreview();return;
    }
    const dx=position.x-origin.x,dy=position.y-origin.y;
    const anchor=handle==="move"?null:RoiGeometry.handles(roi,digitSize())[handle];
    current.config.rois[index]=handle==="move"?RoiGeometry.move(roi,dx,dy,digitSize()):RoiGeometry.resize(roi,handle,{x:anchor.x+dx,y:anchor.y+dy},minimumDigitSize(),digitSize());
  }
  drawPreview();
}
canvas.onpointermove=event=>{
  if(digitDrag && event.pointerId===digitDrag.pointerId)updateDigitDrag(point(event));
  else if(current)canvas.style.cursor=digitCursor(digitEditorReady()?digitHit(point(event)):null);
};
canvas.onpointerup=event=>{
  if(!digitDrag || event.pointerId!==digitDrag.pointerId)return;
  updateDigitDrag(point(event));
  const {before,kind}=digitDrag;
  if(kind==="add") {
    const roi=RoiGeometry.fromPoints(startPoint,draftPoint,minimumDigitSize());
    if(roi){current.config.rois.push(roi);selectedDigit=current.config.rois.length-1;}
    else message("Draw a box at least 9 image pixels wide and high.",true);
  }
  digitDrag=null;startPoint=null;draftPoint=null;
  if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
  commitDigitEdit(before);
};
canvas.onpointercancel=canvas.onlostpointercapture=canvas.onblur=cancelDigitDrag;
canvas.onkeydown=event=>{
  if(event.key==="Escape") {
    event.preventDefault();if(digitDrag)cancelDigitDrag();else{selectedDigit=-1;drawPreview();}return;
  }
  if(!digitEditorReady() || digitDrag)return;
  if(event.ctrlKey || event.metaKey) {
    if(event.key.toLowerCase()==="z"){event.preventDefault();undoDigitEdit(event.shiftKey);}
    else if(event.key.toLowerCase()==="y"){event.preventDefault();undoDigitEdit(true);}
    return;
  }
  if(["Delete","Backspace"].includes(event.key)){event.preventDefault();deleteSelectedDigit();return;}
  const roi=current.config.rois[selectedDigit];
  if(roi && ["BracketLeft","BracketRight"].includes(event.code)){event.preventDefault();changeDigitRotation((((roi.rotation_degrees||0)+(event.code==="BracketRight"?1:-1)*(event.shiftKey?10:1)+540)%360)-180);return;}
  if(!roi || !["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;
  event.preventDefault();const before=digitSnapshot(),step=event.shiftKey?10:1;
  const dx=(event.key==="ArrowRight"?step:event.key==="ArrowLeft"?-step:0)/canvas.width;
  const dy=(event.key==="ArrowDown"?step:event.key==="ArrowUp"?-step:0)/canvas.height;
  const target=RoiGeometry.localPoint(roi,{x:dx?roi.x+roi.w+dx:roi.x+roi.w/2,y:dy?roi.y+roi.h+dy:roi.y+roi.h/2},digitSize());
  current.config.rois[selectedDigit]=event.altKey?RoiGeometry.resize(roi,dx?"e":"s",target,minimumDigitSize(),digitSize()):RoiGeometry.move(roi,dx,dy,digitSize());
  commitDigitEdit(before);
};
$("undoRoi").onclick=()=>undoDigitEdit();
$("redoRoi").onclick=()=>undoDigitEdit(true);
$("clearRois").onclick=()=>{if(!digitEditorReady())return;const before=digitSnapshot();current.config.rois=[];selectedDigit=-1;commitDigitEdit(before);};
$("original").onclick=act(async()=>{showingOriginal=true;await renderSourceImage();});$("overlay").onclick=act(async()=>{showingOriginal=false;await renderSourceImage();});
$("loadCameras").onclick=act(async()=>{const camera=$("cameraEntity").value,light=$("flashEntity").value;[cameras,flashLights]=await Promise.all([api("/api/home-assistant/cameras").then(response=>response.json()),api("/api/home-assistant/flash-lights").then(response=>response.json())]);renderChoices("cameraEntity",cameras,camera,"Select a camera");renderChoices("flashEntity",flashLights,light,"Select the meter’s ESPHome light");message(cameras.length?"Devices loaded. Select your meter camera and optional flash light.":"No cameras found in Home Assistant.");});
$("cameraPreview").onclick=act(async()=>{if(dirty)await saveConfiguration(false);if(!selected())throw new Error("Add a meter and select its camera first.");const response=await api(`/api/meters/${selected()}/camera-image`,{method:"POST"});sourceBlob=await response.blob();showingOriginal=false;await renderSourceImage();$("imageStamp").textContent="Live preview · Not recorded";$("processed").hidden=true;message("Rotate the image to level the digits, then draw one box per digit and save.");});
async function captureCamera(testOnly){if(dirty)await saveConfiguration(false);if(!selected())throw new Error("Add a meter and select its camera first.");const result=await(await api(`/api/meters/${selected()}/capture?test_only=${testOnly}`,{method:"POST"})).json();if(!testOnly)await refresh();renderResult(result,testOnly);$("captureStatus").textContent=testOnly?"Test complete · Not recorded":"Capture recorded";message(testOnly?"Test complete. The last preview remains displayed; reading history is unchanged.":"Capture recorded. Review its result in History.");}
$("cameraTest").onclick=act(()=>captureCamera(true));
$("cameraCapture").onclick=act(async()=>{if(dirty)throw new Error("Save configuration before recording a reading.");await captureCamera(false);});
async function uploaded(testOnly){if(!selected())throw new Error("Add a meter first.");if(dirty)throw new Error("Save configuration before testing or recording an upload.");const file=$("upload").files[0];if(!file)throw new Error("Select a JPEG or PNG first.");const now=encodeURIComponent(new Date().toISOString());const path=testOnly?`/api/meters/${selected()}/test?captured_at=${now}`:`/api/device/${selected()}/images?captured_at=${now}&event_id=${crypto.randomUUID()}`;const result=await(await api(path,{method:"POST",headers:{"Content-Type":file.type},body:file},testOnly?token:$("uploadToken").value)).json();if(!testOnly)await refresh();renderResult(result,testOnly);if(testOnly){sourceBlob=file;showingOriginal=false;await renderSourceImage();$("imageStamp").textContent="Uploaded test · Not recorded";}}
$("testImage").onclick=act(()=>uploaded(true));$("submitImage").onclick=act(()=>uploaded(false));
$("manualForm").onsubmit=act(async()=>{if(dirty)throw new Error("Save or discard configuration changes before recording a baseline.");await api(`/api/meters/${selected()}/manual`,{method:"POST",body:JSON.stringify({value_m3:$("manualValue").value,kind:$("manualKind").value,reason:$("manualReason").value})});await refresh();$("manualReason").value="";message("Verified baseline recorded. Previous history is preserved.");});
async function download(path,name){const response=await api(path);const url=URL.createObjectURL(await response.blob());const anchor=document.createElement("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("export").onclick=act(()=>download("/api/config/export","waterwatch-config.json"));$("logs").onclick=act(()=>download(`/api/meters/${selected()}/logs`,`${selected()}-log.ndjson`));
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
async function boot(){try{const session=await(await api("/api/session")).json();ingress=session.authentication==="ingress";if(ingress){busy=true;updateControls();await refresh();$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=true;requestAnimationFrame(resizePreview);}}catch(error){message(error.message,true);}finally{busy=false;updateControls();}}
boot();
