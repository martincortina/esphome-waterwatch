/* Credentials stay in memory. Only the appearance preference is stored locally. */
"use strict";
const $ = id => document.getElementById(id);
let token = "", meters = [], current = null, savedConfig = null, picture = null;
let cameras = [], flashLights = [], historyRows = [], ingress = false, busy = false, dirty = false, jsonDraft = false;
let showingOriginal = false, startPoint = null, draftPoint = null, layoutMeterId = null, messageTimer;
let sourceBlob = null, transformEpoch = 0, transformAbort = null, rotationTimer, transformPending = false;
let selectedDigit = -1, digitMode = "edit", digitDrag = null, digitUndo = [], digitRedo = [];
let landmarkTarget=null, selectedLandmark=-1, diagnosticUrls=[];
let selectedLogs=new Set(), pendingDeletion=null;
const sourceSizes=new WeakMap();
let lastTest=null;
let referenceSource=null, referenceSourceId=null;
const canvas = $("preview"), ctx = canvas.getContext("2d"), stage = $("previewStage");
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const reasons = {
  ok:"Verified", candidate:"Recognized", accepted:"Accepted", stale:"Reading is stale",
  baseline_required:"Baseline required", manual_baseline:"Verified manually", roi_not_configured:"Set digit regions",
  roi_outside_image:"A rotated digit box extends outside the image. Adjust its angle, size or position",
  ambiguous_digit:"Ambiguous digit", poor_image_quality:"Image needs adjustment", unreadable:"Unreadable",
  camera_unavailable:"Camera unavailable", camera_repeated_frame:"Repeated camera image",
  flash_light_must_be_off:"Turn the meter light off before a flash capture",
  alignment_reference_missing:"Save an alignment reference before enabling alignment",
  alignment_landmark_not_found:"A landmark could not be matched confidently. Check the photo or recalibrate",
  alignment_landmark_ambiguous:"A landmark matches several locations. Choose a more distinctive mark",
  alignment_movement_too_large:"The camera moved beyond alignment limits. Check its position or recalibrate",
  alignment_reference_geometry_changed:"Image orientation or perspective changed. Save a new alignment reference",
  alignment_digit_outside_image:"Camera movement puts a digit outside the photo",
  alignment_image_size_changed:"Image resolution changed. Restore it or recalibrate alignment",
  alignment_landmark_low_contrast:"Choose a larger, higher-contrast fixed landmark",
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
  updateLogControls();
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
  cfg.alignment ??= {enabled:false,reference_id:null,anchors:[],search_margin:.12,max_angle_degrees:8,min_score:.85,min_peak_margin:.04};
  cfg.ignored_decimal_digits ??= 0;
  jsonDraft = false;
  $("config").value = JSON.stringify(cfg, null, 2);
  for (const [id,value] of Object.entries({decimals:cfg.decimal_places,threshold:cfg.min_confidence,maxRate:cfg.max_rate_lph,interval:cfg.camera.interval_seconds,led:cfg.camera.led_duty,resolution:cfg.camera.resolution,sourceKind:cfg.source.kind,flashBrightness:cfg.source.flash?.brightness_pct ?? 20})) $(id).value = value;
  $("scheduledCapture").checked = cfg.source.enabled; $("flashEnabled").checked = Boolean(cfg.source.flash);
  $("rotation").value = $("rotationSlider").value = cfg.rotation_degrees ?? 0;
  $("ignoredDecimals").value=cfg.ignored_decimal_digits;$("ignoredDecimals").max=cfg.decimal_places;
  $("rotationStatus").textContent = `${cfg.rotation_degrees || 0}° · Clockwise +`;
  $("alignmentEnabled").checked=cfg.alignment.enabled;
  $("alignmentScore").value=cfg.alignment.min_score;$("alignmentSearch").value=cfg.alignment.search_margin*100;$("alignmentAngle").value=cfg.alignment.max_angle_degrees;
  renderChoices("cameraEntity", cameras, cfg.source.camera_entity, "Select a camera");
  renderChoices("flashEntity", flashLights, cfg.source.flash?.light_entity, "Select the meter’s ESPHome light");
  sourceControls(); updateRegionCount();
}
function editedConfig() {
  const cfg = structuredClone(current.config);
  cfg.decimal_places = Number($("decimals").value); cfg.min_confidence = Number($("threshold").value);
  cfg.ignored_decimal_digits=Number($("ignoredDecimals").value);
  cfg.max_rate_lph = Number($("maxRate").value); cfg.camera.interval_seconds = Number($("interval").value);
  cfg.camera.led_duty = Number($("led").value); cfg.camera.resolution = $("resolution").value;
  cfg.rotation_degrees = Number($("rotation").value);
  cfg.alignment={...cfg.alignment,enabled:$("alignmentEnabled").checked,min_score:Number($("alignmentScore").value),search_margin:Number($("alignmentSearch").value)/100,max_angle_degrees:Number($("alignmentAngle").value)};
  const fromHa = $("sourceKind").value === "home_assistant";
  cfg.source = {kind:$("sourceKind").value,camera_entity:fromHa ? $("cameraEntity").value : null,enabled:fromHa && $("scheduledCapture").checked,flash:fromHa && $("flashEnabled").checked ? {light_entity:$("flashEntity").value,brightness_pct:Number($("flashBrightness").value),settle_ms:cfg.source.flash?.settle_ms ?? 1500} : null};
  return cfg;
}
async function saveConfiguration(notify = true) {
  if (!current) throw new Error("Add a meter first.");
  if (jsonDraft) throw new Error("Apply the JSON to the editor before saving, or discard your changes.");
  const numericFields = ["decimals","ignoredDecimals","threshold","maxRate","interval","rotation","alignmentScore","alignmentSearch","alignmentAngle"];
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
  if(JSON.stringify(cfg)!==JSON.stringify(savedConfig))clearDiagnostics();
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
function baseEditorReady() { return Boolean(current && picture && !busy && !transformPending && !showingOriginal && !jsonDraft && JSON.stringify(current.config.perspective) === JSON.stringify(savedConfig?.perspective)); }
function digitEditorReady() { return baseEditorReady() && digitMode!=="landmark" && landmarkTarget===null && (!current.config.alignment?.enabled && !savedConfig?.alignment?.enabled || JSON.stringify(editedConfig().alignment)===JSON.stringify(savedConfig?.alignment)); }
function landmarkEditorReady() { return baseEditorReady() && !$("alignmentEnabled").checked && !savedConfig?.alignment?.enabled && sourceBlob===referenceSource && referenceSourceId===current.config.alignment?.reference_id; }
function updateDigitControls() {
  const ready=digitEditorReady(), count=current?.config.rois.length || 0;
  $("editDigits").disabled=!baseEditorReady(); $("addDigit").disabled=!baseEditorReady() || count>=12;
  $("drawDigitFrame").disabled=!ready;
  $("selectedDigit").disabled=!ready || !count;
  $("deleteDigit").disabled=!ready || selectedDigit<0 || selectedDigit>=count;
  $("editGroup").disabled=!ready || !count || digitMode!=="edit";
  for(const id of ["digitAngle","digitRotateLeft","digitRotateRight","digitResetAngle"])$(id).disabled=!ready || selectedDigit<0 || selectedDigit>=count || digitMode!=="edit";
  for(const id of ["digitX","digitY","digitW","digitH","applyDigitGeometry","duplicateDigit"])$(id).disabled=!ready || selectedDigit<0 || selectedDigit>=count || digitMode!=="edit" || $("editGroup").checked || id==="duplicateDigit" && count>=12;
  const canUndo=ready || landmarkEditorReady();
  $("undoRoi").disabled=!canUndo || !digitUndo.length; $("redoRoi").disabled=!canUndo || !digitRedo.length;
  $("clearRois").disabled=!ready || !count;
  $("exportTest").disabled=busy || !lastTest;
  $("editDigits").classList.toggle("active",digitMode==="edit"); $("addDigit").classList.toggle("active",digitMode==="add");
  $("editDigits").setAttribute("aria-pressed",String(digitMode==="edit")); $("addDigit").setAttribute("aria-pressed",String(digitMode==="add"));
  $("digitHelp").textContent=digitMode==="add" ? ($("drawDigitFrame").value==="inner" ? "Draw tightly around the complete digit. The outer OCR frame adds 20% margins on all sides. Leave room around the digit near photo edges." : "Draw the outer OCR crop. Its dashed inner 60% should fit the complete digit, with 20% margins on each side.") : "Fit each complete digit inside its dashed inner 60%. Move or resize the solid outer crop, or use the round handle to rotate. Numbers define reading order.";
  if($("editGroup").checked && digitMode==="edit")$("digitHelp").textContent="All digit boxes: drag any box to move the group, or use the angle controls to rotate it. Uncheck to resize one digit.";
  if(landmarkTarget!==null)$("digitHelp").textContent=`Draw a box around fixed landmark ${landmarkTarget+1}. Use a stationary, distinctive mark; Escape cancels.`;
  else if(digitMode==="landmark")$("digitHelp").textContent="Select a blue landmark to move or resize it. Delete removes it; Undo restores it. Enable automatic alignment and save after marking two fixed landmarks.";
  canvas.style.cursor=landmarkTarget!==null && baseEditorReady()?"crosshair":digitCursor(digitDrag);
  const alignment=current?.config.alignment, paused=!$("alignmentEnabled").checked && !savedConfig?.alignment?.enabled;
  $("saveReference").disabled=busy || !sourceBlob || !baseEditorReady() || !paused;
  for(const id of ["markAnchor1","markAnchor2"])$(id).disabled=!landmarkEditorReady() || !alignment?.reference_id || !paused;
  $("editLandmarks").disabled=busy || !alignment?.reference_id || !sourceBlob;
  $("editLandmarks").textContent=alignment?.enabled || savedConfig?.alignment?.enabled?"Pause & edit landmarks":"Edit landmarks on reference";
  const anchors=alignment?.anchors||[],picker=$("selectedLandmark");
  if(selectedLandmark>=anchors.length)selectedLandmark=anchors.length-1;
  picker.replaceChildren(new Option("Select landmark","-1"),...anchors.map((_,index)=>new Option(`Landmark ${index+1}`,String(index))));picker.value=String(selectedLandmark);
  picker.disabled=!landmarkEditorReady() || !anchors.length;
  for(const id of ["deleteLandmark","redrawLandmark","applyLandmarkGeometry","landmarkX","landmarkY","landmarkW","landmarkH"])$(id).disabled=!landmarkEditorReady() || selectedLandmark<0 || digitMode!=="landmark";
  for(const [id,key] of [["landmarkX","x"],["landmarkY","y"],["landmarkW","w"],["landmarkH","h"]])if(document.activeElement!==$(id))$(id).value=Number(((anchors[selectedLandmark]?.[key]||0)*100).toFixed(4));
  $("alignmentEnabled").disabled=busy || !alignment?.reference_id || alignment.anchors.length!==2;
  $("landmarkStatus").textContent=landmarkTarget!==null?`Drag a box around fixed landmark ${landmarkTarget+1}. Escape cancels.`:!alignment?.reference_id?"No reference saved.":`${alignment.anchors.length}/2 landmarks marked${alignment.enabled?" · Alignment enabled":""}.`;
}
function renderDigitSelection() {
  const count=current?.config.rois.length || 0;
  if(selectedDigit>=count) selectedDigit=count-1;
  const select=$("selectedDigit");
  if(select.options.length!==count+1) select.replaceChildren(new Option("Select a digit","-1"),...Array.from({length:count},(_,index)=>new Option(`Digit ${index+1}`,String(index))));
  select.value=String(selectedDigit); $("digitSelection").textContent=selectedDigit<0?"No digit selected":`Digit ${selectedDigit+1} selected`;
  if(document.activeElement!==$("digitAngle"))$("digitAngle").value=current?.config.rois[selectedDigit]?.rotation_degrees || 0;
  for(const [id,key] of [["digitX","x"],["digitY","y"],["digitW","w"],["digitH","h"]])if(document.activeElement!==$(id))$(id).value=Number(((current?.config.rois[selectedDigit]?.[key]||0)*100).toFixed(4));
  updateDigitControls();
}
function resetDigitEditor() { selectedDigit=-1;selectedLandmark=-1;landmarkTarget=null;$("editGroup").checked=false;digitDrag=null;startPoint=null;draftPoint=null;digitUndo=[];digitRedo=[];digitMode=current?.config.rois.length?"edit":"add";renderDigitSelection(); }
function digitSnapshot() { return {rois:structuredClone(current.config.rois),alignment:structuredClone(current.config.alignment),selected:selectedDigit,landmark:selectedLandmark,mode:digitMode}; }
function restoreDigitSnapshot(snapshot) { current.config.rois=structuredClone(snapshot.rois);current.config.alignment=structuredClone(snapshot.alignment);$("alignmentEnabled").checked=current.config.alignment.enabled;selectedDigit=snapshot.selected;selectedLandmark=snapshot.landmark;digitMode=snapshot.mode;landmarkTarget=null;digitDrag=null;startPoint=null;draftPoint=null; }
function digitEditChanged() {
  $("config").value=JSON.stringify(editedConfig(),null,2);
  $("resultCard").hidden=true; $("testResult").textContent=""; $("processed").hidden=true;
  clearDiagnostics();
  setDirty(jsonDraft || JSON.stringify(editedConfig())!==JSON.stringify(savedConfig)); drawPreview(); updateHint();
}
function commitDigitEdit(before) {
  if(JSON.stringify(before.rois)===JSON.stringify(current.config.rois) && JSON.stringify(before.alignment)===JSON.stringify(current.config.alignment)){drawPreview();return;}
  digitUndo.push(before); if(digitUndo.length>50)digitUndo.shift(); digitRedo=[]; digitEditChanged();
}
function undoDigitEdit(redo=false) {
  if(!digitEditorReady() && !landmarkEditorReady())return;
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
function digitHit(point) { const box=canvas.getBoundingClientRect(),radius=window.matchMedia("(pointer: coarse)").matches?13:8,roi=current.config.rois[selectedDigit],group=$("editGroup").checked;return RoiGeometry.hitTest(current.config.rois,point,{x:radius/box.width,y:radius/box.height},group?-1:selectedDigit,digitSize(),!group&&roi?digitRotationHandle(roi):null); }
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
  if($("editGroup").checked){const group=RoiGeometry.rotateGroup(current.config.rois,angle-(current.config.rois[selectedDigit].rotation_degrees||0),digitSize());if(!group){message("The rotated group would leave the image. Move it inward or adjust individual boxes first.",true);$("digitAngle").value=current.config.rois[selectedDigit].rotation_degrees||0;return;}current.config.rois=group;if(updateInput)$("digitAngle").value=angle;commitDigitEdit(before);return;}
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
$("drawDigitFrame").onchange=()=>drawPreview();
$("selectedDigit").onchange=()=>{selectedDigit=Number($("selectedDigit").value);digitMode="edit";drawPreview();};
$("deleteDigit").onclick=deleteSelectedDigit;
$("applyDigitGeometry").onclick=()=>{
  if(!digitEditorReady() || !current.config.rois[selectedDigit] || $("editGroup").checked)return;
  const roi={...current.config.rois[selectedDigit]},minimum=minimumDigitSize();
  for(const [id,key] of [["digitX","x"],["digitY","y"],["digitW","w"],["digitH","h"]]){if($(id).value.trim()==="" || !Number.isFinite(Number($(id).value))){message("Enter valid percentages for position and size.",true);return;}roi[key]=Number($(id).value)/100;}
  if(roi.w<minimum.w || roi.h<minimum.h || !RoiGeometry.inside(roi,digitSize())){message("Keep the full rotated box inside the image and at least 9 image pixels wide/high.",true);return;}
  const before=digitSnapshot();current.config.rois[selectedDigit]=roi;commitDigitEdit(before);
};
$("duplicateDigit").onclick=()=>{if(!digitEditorReady() || !current.config.rois[selectedDigit] || current.config.rois.length>=12)return;const before=digitSnapshot(),copy=RoiGeometry.move(current.config.rois[selectedDigit],.01,.01,digitSize());current.config.rois.splice(selectedDigit+1,0,copy);selectedDigit++;commitDigitEdit(before);};
$("editGroup").onchange=()=>{if($("editGroup").checked && selectedDigit<0)selectedDigit=0;drawPreview();};
$("alignmentEnabled").addEventListener("input",()=>{if(current){current.config.alignment.enabled=$("alignmentEnabled").checked;drawPreview();}});
$("saveReference").onclick=act(async()=>{
  if(!sourceBlob || showingOriginal)throw new Error("Preview the photo first.");
  if(dirty)await saveConfiguration(false);
  const result=await(await api(`/api/meters/${selected()}/alignment/reference`,{method:"POST",body:sourceBlob,headers:{"Content-Type":sourceBlob.type}})).json();
  referenceSource=sourceBlob;referenceSourceId=result.reference_id;
  current.config.alignment={...current.config.alignment,enabled:false,reference_id:result.reference_id,anchors:[]};resetDigitEditor();renderConfig();setDirty();drawPreview();
  message("Reference saved. Mark two fixed landmarks, check digit boxes, then enable alignment and save.");
});
for(const [index,id] of ["markAnchor1","markAnchor2"].entries())$(id).onclick=()=>{if(index && !current.config.alignment.anchors.length){message("Mark landmark 1 first.",true);return;}landmarkTarget=index;drawPreview();stage.scrollIntoView({block:"center"});canvas.focus({preventScroll:true});};
$("editLandmarks").onclick=act(async()=>{
  if(dirty)throw new Error("Save or discard changes before opening the reference editor.");
  if(current.config.alignment.enabled){current.config.alignment.enabled=false;$("alignmentEnabled").checked=false;setDirty();await saveConfiguration(false);}
  if(!referenceSource || referenceSourceId!==current.config.alignment.reference_id){referenceSource=await(await api(`/api/meters/${selected()}/alignment/reference`)).blob();referenceSourceId=current.config.alignment.reference_id;}
  sourceBlob=referenceSource;showingOriginal=false;await renderSourceImage();digitMode="landmark";selectedLandmark=current.config.alignment.anchors.length?0:-1;drawPreview();$("imageStamp").textContent="Saved reference · Landmark editor";stage.scrollIntoView({block:"center"});
});
function validLandmark(roi,index){
  if(!roi || roi.rotation_degrees || roi.w*roi.h>.1 || !RoiGeometry.inside(roi,digitSize()))return false;
  return current.config.alignment.anchors.every((other,i)=>i===index || Math.hypot(roi.x+roi.w/2-other.x-other.w/2,roi.y+roi.h/2-other.y-other.h/2)>=.15);
}
function deleteSelectedLandmark(){
  if(!landmarkEditorReady() || !current.config.alignment.anchors[selectedLandmark])return;
  const before=digitSnapshot();current.config.alignment.anchors.splice(selectedLandmark,1);selectedLandmark=Math.min(selectedLandmark,current.config.alignment.anchors.length-1);current.config.alignment.enabled=false;$("alignmentEnabled").checked=false;commitDigitEdit(before);message("Landmark removed. Remaining landmarks are renumbered. Alignment needs two landmarks before it can be enabled.");
}
$("selectedLandmark").onchange=()=>{selectedLandmark=Number($("selectedLandmark").value);digitMode="landmark";landmarkTarget=null;drawPreview();};
$("deleteLandmark").onclick=deleteSelectedLandmark;
$("redrawLandmark").onclick=()=>{landmarkTarget=selectedLandmark;drawPreview();canvas.focus();};
$("applyLandmarkGeometry").onclick=()=>{
  if(!landmarkEditorReady() || selectedLandmark<0)return;
  const roi={rotation_degrees:0};
  for(const [id,key] of [["landmarkX","x"],["landmarkY","y"],["landmarkW","w"],["landmarkH","h"]]){if($(id).value.trim()==="" || !Number.isFinite(Number($(id).value))){message("Enter valid landmark percentages.",true);return;}roi[key]=Number($(id).value)/100;}
  if(roi.w<minimumDigitSize().w || roi.h<minimumDigitSize().h || !validLandmark(roi,selectedLandmark)){message("Keep landmarks inside the image, under 10% area and at least 15% apart.",true);return;}
  const before=digitSnapshot();current.config.alignment.anchors[selectedLandmark]=roi;commitDigitEdit(before);
};
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
  if (current && picture && !showingOriginal && !transformPending) {
    ctx.lineWidth = Math.max(2, canvas.width / 400); ctx.font = `${Math.max(15, canvas.width / 45)}px sans-serif`;
    function outline(roi) {
      const points=RoiGeometry.corners(roi,digitSize());ctx.beginPath();
      points.forEach((p,index)=>{if(index)ctx.lineTo(p.x*canvas.width,p.y*canvas.height);else ctx.moveTo(p.x*canvas.width,p.y*canvas.height);});ctx.closePath();return points;
    }
    const digitBounds=current.config.rois.map(roi=>{
      const points=outline(roi);ctx.strokeStyle=$("editGroup").checked?"#ffd46a":"#8ff5bc";ctx.stroke();
      ctx.save();outline(RoiGeometry.innerFrame(roi));ctx.setLineDash([5,4]);ctx.lineWidth=Math.max(1,canvas.width/700);ctx.strokeStyle="#72c8ff";ctx.stroke();ctx.restore();
      return {left:Math.min(...points.map(p=>p.x))*canvas.width,right:Math.max(...points.map(p=>p.x))*canvas.width,
        top:Math.min(...points.map(p=>p.y))*canvas.height,bottom:Math.max(...points.map(p=>p.y))*canvas.height};
    });
    const roi=current.config.rois[selectedDigit];
    if(roi && digitMode==="edit" && !$("editGroup").checked) {
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
    if (startPoint && draftPoint) {
      ctx.save();ctx.strokeStyle="#ffffff";ctx.setLineDash([5,4]);ctx.strokeRect(startPoint.x*canvas.width,startPoint.y*canvas.height,(draftPoint.x-startPoint.x)*canvas.width,(draftPoint.y-startPoint.y)*canvas.height);ctx.restore();
      if(digitDrag?.kind==="add"){
        const drawn=RoiGeometry.fromPoints(startPoint,draftPoint,minimumDigitSize()),outer=drawn && ($("drawDigitFrame").value==="inner"?RoiGeometry.outerFrame(drawn,digitSize()):drawn);
        if(outer){ctx.save();outline(outer);ctx.strokeStyle="#8ff5bc";ctx.stroke();outline(RoiGeometry.innerFrame(outer));ctx.setLineDash([5,4]);ctx.strokeStyle="#72c8ff";ctx.stroke();ctx.restore();}
      }
    }
    // Keep upright number badges outside the entire rotated box, clear of the digit.
    ctx.save();ctx.textBaseline="middle";
    const fontSize=Math.max(15,canvas.width/45),padding=fontSize/3,gap=Math.max(5,ctx.lineWidth*2),placed=[];
    const overlaps=(a,b)=>a.x<b.right && a.x+a.w>b.left && a.y<b.bottom && a.y+a.h>b.top;
    digitBounds.forEach((bounds,index)=>{
      const text=String(index+1),w=Math.ceil(ctx.measureText(text).width+padding*2),h=Math.ceil(fontSize+padding*2);
      const x=Math.max(0,Math.min(bounds.left,canvas.width-w)),y=Math.max(0,Math.min(bounds.top,canvas.height-h));
      const candidates=[{x,y:bounds.top-gap-h},{x,y:bounds.bottom+gap},{x:bounds.right+gap,y},{x:bounds.left-gap-w,y}].map(p=>({...p,w,h}));
      const visible=candidates.filter(p=>p.x>=0 && p.y>=0 && p.x+w<=canvas.width && p.y+h<=canvas.height);
      const badge=visible.find(p=>!digitBounds.some(b=>overlaps(p,b)) && !placed.some(b=>overlaps(p,b))) || visible[0] || candidates[0];
      ctx.fillStyle="#123a2a";ctx.fillRect(badge.x,badge.y,w,h);ctx.fillStyle="#c5ffdd";ctx.fillText(text,badge.x+padding,badge.y+h/2);
      placed.push({left:badge.x,top:badge.y,right:badge.x+w,bottom:badge.y+h});
    });
    ctx.restore();
    for(const [index,anchor] of (current.config.alignment?.anchors||[]).entries()){
      outline(anchor);ctx.strokeStyle="#72c8ff";ctx.stroke();ctx.fillStyle="#72c8ff";ctx.fillText(`L${index+1}`,anchor.x*canvas.width,Math.max(18,anchor.y*canvas.height-5));
      if(digitMode==="landmark" && selectedLandmark===index){const box=canvas.getBoundingClientRect(),w=9*canvas.width/Math.max(1,box.width),h=9*canvas.height/Math.max(1,box.height);ctx.fillStyle="#72c8ff22";ctx.fill();ctx.fillStyle="#72c8ff";for(const p of Object.values(RoiGeometry.handles(anchor,digitSize())))ctx.fillRect(p.x*canvas.width-w/2,p.y*canvas.height-h/2,w,h);}
    }
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
async function cacheSourceSize(blob) { if(!sourceSizes.has(blob)){const decoded=await createImageBitmap(blob);sourceSizes.set(blob,{width:decoded.width,height:decoded.height});decoded.close();} }
async function renderSourceImage() {
  clearTimeout(rotationTimer);
  const epoch = ++transformEpoch;
  transformAbort?.abort(); transformAbort = new AbortController();
  if (!sourceBlob || !current) { transformPending = false; return; }
  transformPending = true; $("rotationStatus").textContent = "Updating preview…";
  updateControls();
  const cfg = current.config, original = showingOriginal;
  try {
    await cacheSourceSize(sourceBlob);
    if(epoch!==transformEpoch)return;
    let blob = sourceBlob;
    if (!original && (cfg.rotation_degrees || cfg.perspective || cfg.alignment?.enabled)) {
      const response = await api(`/api/meters/${selected()}/transform-image?rotation_degrees=${encodeURIComponent(cfg.rotation_degrees || 0)}&use_alignment=${Boolean(cfg.alignment?.enabled)}`, {method:"POST",body:blob,headers:{"Content-Type":blob.type || "application/octet-stream"},signal:transformAbort.signal});
      blob = await response.blob();
    }
    if (epoch === transformEpoch) await setPicture(blob, epoch);
  } catch (error) {
    if (epoch === transformEpoch && error.name !== "AbortError") { picture?.close(); picture=null; drawPreview(); message(error.message,true); }
  } finally {
    if (epoch === transformEpoch) { transformPending=false; $("rotationStatus").textContent = original ? "Original image" : `${cfg.rotation_degrees || 0}° · Clockwise +`; updateControls(); drawPreview(); }
  }
}
function changeRotation(value) {
  if (!current || jsonDraft) { if(jsonDraft) message("Apply or discard the JSON edit before rotating.",true); return; }
  const angle = Math.round(Number(value)*10)/10;
  if (!Number.isFinite(angle) || angle < -180 || angle > 180) return;
  $("rotation").value = $("rotationSlider").value = angle;
  if (angle === (current.config.rotation_degrees || 0)) return;
  const cfg=current.config,previous=cfg.rotation_degrees||0;
  let basis=sourceBlob;
  const alignedCalibration=cfg.alignment?.enabled || savedConfig?.alignment?.enabled;
  if(alignedCalibration){if(referenceSourceId!==cfg.alignment?.reference_id || !referenceSource){$("rotation").value=$("rotationSlider").value=previous;message("Load the saved reference before rotating an aligned calibration.",true);return;}basis=referenceSource;}
  let regions=cfg.rois;
  if(regions.length){
    if(!sourceBlob){$("rotation").value=$("rotationSlider").value=previous;message("Load a preview before rotating so digit boxes can be preserved.",true);return;}
    let base;
    if(cfg.perspective)base={width:cfg.aligned_width,height:cfg.aligned_height};
    else{base=sourceSizes.get(basis);if(!base){$("rotation").value=$("rotationSlider").value=previous;message("Wait for the preview before rotating.",true);return;}}
    regions=RoiGeometry.reorient(regions,base,previous,angle);
    if(!regions){$("rotation").value=$("rotationSlider").value=previous;message("These digit boxes cannot fit at this orientation. Adjust or clear them first.",true);return;}
  }
  current.config.rotation_degrees=angle; current.config.rois=regions;
  if(alignedCalibration){sourceBlob=basis;$("imageStamp").textContent="Rotated calibration reference · Not a live photo";}
  current.config.alignment={...current.config.alignment,enabled:false,reference_id:null,anchors:[]};$("alignmentEnabled").checked=false;
  resetDigitEditor();
  $("resultCard").hidden=true; $("testResult").textContent=""; $("processed").hidden=true;
  startPoint=null; draftPoint=null; showingOriginal=false;
  $("config").value=JSON.stringify(editedConfig(),null,2); setDirty(); updateRegionCount(); updateHint(); drawPreview();
  clearTimeout(rotationTimer); transformEpoch++; transformAbort?.abort();
  transformPending=Boolean(sourceBlob); $("rotationStatus").textContent=sourceBlob?"Updating preview…":`${angle}° · Clockwise +`;
  updateControls();
  rotationTimer=setTimeout(()=>{void renderSourceImage();},180);
  drawPreview();
  clearDiagnostics();message("Photo rotated and digit boxes preserved. Alignment landmarks must be set again; check the layout and save.");
}
$("rotateLeft").onclick=()=>changeRotation(((Number($("rotation").value)-90+540)%360)-180);
$("rotateRight").onclick=()=>changeRotation(((Number($("rotation").value)+90+540)%360)-180);
$("resetRotation").onclick=()=>changeRotation(0);
$("rotation").oninput=()=>{const value=$("rotation").value;if(value.trim()==="" || !Number.isFinite(Number(value)) || Number(value)<-180 || Number(value)>180){setDirty();message("Use an angle from -180° to 180°.",true);return;}changeRotation(value);};
$("rotationSlider").oninput=()=>changeRotation($("rotationSlider").value);
async function loadImage(variant = "overlay") {
  transformEpoch++; transformAbort?.abort(); clearTimeout(rotationTimer);
  sourceBlob=null; transformPending=false; picture?.close(); picture = null; showingOriginal = variant === "original"; $("processed").hidden = true;
  referenceSource=null;referenceSourceId=null;
  const row = current?.latest_capture;
  if(current?.config.alignment?.reference_id){try{sourceBlob=await(await api(`/api/meters/${selected()}/alignment/reference`)).blob();referenceSource=sourceBlob;referenceSourceId=current.config.alignment.reference_id;await renderSourceImage();$("imageStamp").textContent="Saved reference · Not a live photo";return;}catch(error){message(error.message,true);}}
  if (!row?.image_key) {
    $("imageStamp").textContent = "No image yet"; drawPreview(); return;
  }
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
  selectedLogs=new Set([...selectedLogs].filter(seq=>historyRows.some(row=>row.seq===seq)));
  const filter = $("historyFilter").value;
  const rows = historyRows.filter(row => filter === "all" || (filter === "accepted" ? row.accepted : !row.accepted));
  $("historyCount").textContent = String(rows.length);
  $("history").replaceChildren(...rows.map(row => {
    const tr=document.createElement("tr");
    const cell=document.createElement("td"),check=document.createElement("input");check.type="checkbox";check.checked=selectedLogs.has(row.seq);check.setAttribute("aria-label",`Select reading ${row.seq}`);check.onchange=()=>{if(check.checked)selectedLogs.add(row.seq);else selectedLogs.delete(row.seq);updateLogControls();};cell.append(check);tr.append(cell);
    [new Date(row.captured_at).toLocaleString(),row.raw_value??"—",row.value_m3??"—",`${(row.confidence*100).toFixed(1)}%`,readable(row.reason),row.source === "Docker" ? "Host OCR" : readable(row.source)].forEach((text,index)=>{const td=document.createElement("td");if(index===4){const badge=document.createElement("span");badge.className=`reading-badge ${row.accepted?"good":"bad"}`;badge.textContent=text;td.append(badge);}else td.textContent=text;tr.append(td);});
    const action=document.createElement("td"),button=document.createElement("button");button.className="quiet danger";button.textContent="Delete";button.setAttribute("aria-label",`Delete reading ${row.seq}`);button.onclick=act(()=>prepareLogDeletion("selected",[row.seq]));action.append(button);tr.append(action);return tr;
  }));
  if (!rows.length) { const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=8;td.className="empty-row";td.textContent=historyRows.length?"No readings match this filter.":"No saved logs. The current verified counter is kept separately.";tr.append(td);$("history").append(tr); }
  drawGraph(historyRows);
  updateLogControls();
}
$("historyFilter").onchange = renderHistory;
function updateLogControls(){
  $("deleteSelectedLogs").disabled=busy || !selectedLogs.size;$("deleteSelectedLogs").textContent=selectedLogs.size?`Delete selected (${selectedLogs.size})`:"Delete selected";
  $("clearReadingLog").disabled=busy || !historyRows.length;$("deleteRejectedLogs").disabled=busy || !historyRows.length;
  const visible=historyRows.filter(row=>$("historyFilter").value==="all" || ($("historyFilter").value==="accepted"?row.accepted:!row.accepted));
  $("selectVisibleLogs").disabled=busy || !visible.length;$("selectVisibleLogs").checked=visible.length>0 && visible.every(row=>selectedLogs.has(row.seq));$("selectVisibleLogs").indeterminate=visible.some(row=>selectedLogs.has(row.seq)) && !$("selectVisibleLogs").checked;
}
$("selectVisibleLogs").onchange=()=>{const checked=$("selectVisibleLogs").checked;for(const row of historyRows.filter(row=>$("historyFilter").value==="all" || ($("historyFilter").value==="accepted"?row.accepted:!row.accepted))){if(checked)selectedLogs.add(row.seq);else selectedLogs.delete(row.seq);}renderHistory();};
async function prepareLogDeletion(scope,sequences=[]){
  if(dirty)throw new Error("Save or discard configuration changes before deleting logs.");
  const meter=selected(),selection={scope,sequences},preview=await(await api(`/api/meters/${meter}/history/delete-preview`,{method:"POST",body:JSON.stringify(selection)})).json();
  if(!preview.count){message("No matching reading logs to delete.");return;}
  pendingDeletion={meter,body:{...selection,confirm:"DELETE",through_seq:preview.through_seq,expected_count:preview.count}};
  $("deleteLogSummary").textContent=`Permanently delete ${preview.count} ${scope==="rejected"?"rejected ":""}reading log${preview.count===1?"":"s"} for ${current.config.name}? New readings arriving after this review are kept.`;
  $("deleteLogDialog").showModal();$("cancelLogDeletion").focus();
}
$("deleteSelectedLogs").onclick=act(()=>prepareLogDeletion("selected",[...selectedLogs]));
$("deleteRejectedLogs").onclick=act(()=>prepareLogDeletion("rejected"));
$("clearReadingLog").onclick=act(()=>prepareLogDeletion("all"));
$("cancelLogDeletion").onclick=()=>{$("deleteLogDialog").close();pendingDeletion=null;};
$("deleteLogDialog").addEventListener("cancel",()=>{pendingDeletion=null;});
$("deleteLogForm").onsubmit=act(async()=>{
  if(!pendingDeletion || pendingDeletion.meter!==selected())throw new Error("Review the deletion again.");
  const result=await(await api(`/api/meters/${pendingDeletion.meter}/history/delete`,{method:"POST",body:JSON.stringify(pendingDeletion.body)})).json();
  $("deleteLogDialog").close();pendingDeletion=null;selectedLogs.clear();await refresh();message(`${result.deleted} reading logs deleted. Current verified counter preserved.${result.image_cleanup_pending?" Some capture photos could not be removed yet.":""}`);
});
async function renderMeter() {
  clearDiagnostics();
  selectedLogs.clear();pendingDeletion=null;$("deleteLogDialog").close();
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
function clearDiagnostics() { for(const url of diagnosticUrls)URL.revokeObjectURL(url);diagnosticUrls=[];lastTest=null;$("exampleLabel").value="";$("exportTest").disabled=true;$("digitResults").replaceChildren();$("digitDiagnostics").hidden=true;$("resultCard").hidden=true;$("testResult").textContent="";$("processed").hidden=true;if($("processed").dataset.url){URL.revokeObjectURL($("processed").dataset.url);$("processed").dataset.url="";$("processed").removeAttribute("src");} }
function imageBlob(payload) { const bytes=Uint8Array.from(atob(payload.base64),c=>c.charCodeAt(0));return new Blob([bytes],{type:payload.mime}); }
function diagnosticImage(payload,alt) { const img=document.createElement("img");img.alt=alt;const url=URL.createObjectURL(imageBlob(payload));diagnosticUrls.push(url);img.src=url;return img; }
async function renderResult(result, testOnly) {
  clearDiagnostics();
  if(testOnly && result.images?.original)lastTest={schema_version:1,kind:"waterwatch_ocr_test",collected_at:new Date().toISOString(),...result};
  const accepted = testOnly ? result.would_accept : result.accepted;
  $("resultCard").hidden=false; $("resultCard").classList.toggle("rejected", !accepted);
  $("resultMode").textContent=testOnly?"OCR TEST · NOT RECORDED":"RECORDED CAPTURE";
  $("resultValue").textContent=result.raw_value ? `${result.raw_value} m³` : "Unreadable";
  $("resultReason").textContent=testOnly && accepted?"Would be accepted":readable(result.reason); $("resultReason").className=`result-reason ${accepted?"good":"bad"}`;
  const digits=result.details?.digits || [],used=digits.filter(d=>d.used_for_reading!==false),agreed=used.filter(d=>d.recognitions?.[0]?.[0]!==null && d.recognitions?.[0]?.[0]===d.recognitions?.[1]?.[0] && d.recognitions?.length===2);
  const minimum=agreed.length?Math.min(...agreed.map(d=>Math.min(...d.recognitions.map(r=>r[1])))):null;
  $("resultConfidence").textContent=digits.length?`${agreed.length}/${used.length} used digits agreed${minimum===null?"":` · Lowest agreed score ${(minimum*100).toFixed(1)}%`}${result.details?.ignored_decimal_digits?` · ${result.details.resolution_litres} L resolution`:""}${testOnly?" · History unchanged":""}`:`${((result.confidence||0)*100).toFixed(1)}% confidence${testOnly?" · History unchanged":""}`;
  const {images,...summary}=result;$("testResult").textContent=JSON.stringify(summary,null,2);
  $("viewTestPhoto").hidden=!images?.original;
  const alignment=result.details?.alignment;
  $("alignmentResult").textContent=alignment?.status==="aligned"?`Landmarks ${alignment.landmarks.map(m=>`${Math.round(m.score*100)}%`).join(" / ")}`:alignment?.landmarks?`Alignment stopped · Landmarks ${alignment.landmarks.map(m=>`${Math.round(m.score*100)}%`).join(" / ")}`:alignment?readable(alignment.status):"Fixed calibration";
  $("digitDiagnostics").hidden=!digits.length && !alignment;
  for(const digit of digits){
    const card=document.createElement("article");card.className=`digit-result ${digit.status||""}`;
    const title=document.createElement("strong");title.textContent=`Digit ${digit.digit+1} · ${digit.used_for_reading===false?"Excluded at chosen precision":readable(digit.status||"quality check")}`;card.append(title);
    if(digit.possible_transition){const note=document.createElement("p");note.textContent=`Possible wheel transition ${digit.possible_transition.join(" → ")}. No digit inferred.`;card.append(note);}
    if(images?.digits?.[digit.digit])card.append(diagnosticImage(images.digits[digit.digit],`Actual crop for digit ${digit.digit+1}`));
    for(const [index,recognition] of (digit.recognitions||[]).entries()){const line=document.createElement("p");line.textContent=`${digit.recognition_sources?.[index]||`Pass ${index+1}`}: ${recognition[0]??"?"} · ${(recognition[1]*100).toFixed(1)}%`;card.append(line);}
    if(digit.recognition_policy==="assisted_consensus"){const note=document.createElement("p");note.textContent="Supported by two normalized Latin-model views";card.append(note);}
    const quality=document.createElement("p");quality.textContent=`Brightness ${Math.round(digit.brightness)} · Highlights ${Math.round((digit.highlight_fraction||0)*100)}%`;card.append(quality);
    if(digit.quality_warnings?.length){const note=document.createElement("p");note.textContent=digit.quality_warnings.map(readable).join(" · ");card.append(note);}
    $("digitResults").append(card);
  }
  if(images?.original){
    sourceBlob=imageBlob(images.original);transformEpoch++;transformAbort?.abort();clearTimeout(rotationTimer);transformPending=false;
    await cacheSourceSize(sourceBlob);
    showingOriginal=!images.aligned;
    if(images.aligned)await setPicture(imageBlob(images.aligned));
    else if(alignment){await setPicture(sourceBlob);}
    else{showingOriginal=false;await renderSourceImage();}
    $("imageStamp").textContent=alignment && !images.aligned?"Original from this capture · Alignment failed":testOnly?"Photo used by this OCR test · Not recorded":"Photo used by this recorded reading";
    $("processed").hidden=!images.processed;
    if(images.processed){if($("processed").dataset.url)URL.revokeObjectURL($("processed").dataset.url);const url=URL.createObjectURL(imageBlob(images.processed));$("processed").dataset.url=url;$("processed").src=url;}
  }
  if(testOnly)requestAnimationFrame(()=>$("resultCard").scrollIntoView({block:"start"}));
}
$("viewTestPhoto").onclick=()=>{stage.scrollIntoView({block:"center"});canvas.focus({preventScroll:true});};
$("exportTest").onclick=()=>{
  if(!lastTest)return;
  try {
    const blob=new Blob([TestBundle.serialize(lastTest,$("exampleLabel").value)],{type:"application/json"}),url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download=`${selected()}-ocr-test-${Date.now()}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message("Test bundle downloaded for local review. Reading history is unchanged.");
  }catch(error){message(error.message,true);}
};

$("loginForm").onsubmit=act(async()=>{token=$("token").value;try{await refresh();$("token").value="";$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=false;requestAnimationFrame(resizePreview);}catch(error){token="";throw error;}});
$("logout").onclick=()=>{clearDiagnostics();referenceSource=null;referenceSourceId=null;token="";current=null;savedConfig=null;meters=[];historyRows=[];setDirty(false);$("workspace").hidden=true;$("login").hidden=false;$("logout").hidden=true;$("cameraToken").textContent="";$("uploadToken").value="";$("manualValue").value="";$("manualReason").value="";$("newName").value="";$("newId").value="";$("history").replaceChildren();$("testResult").textContent="";$("resultCard").hidden=true;$("processed").hidden=true;$("processed").removeAttribute("src");if($("processed").dataset.url)URL.revokeObjectURL($("processed").dataset.url);transformEpoch++;transformAbort?.abort();clearTimeout(rotationTimer);sourceBlob=null;picture?.close();picture=null;drawPreview();message("");};
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
for(const element of document.querySelectorAll(".settings-column input, .settings-column select")){if(["selectedLandmark","landmarkX","landmarkY","landmarkW","landmarkH"].includes(element.id))continue;element.addEventListener("input",()=>{if(element.id==="decimals")$("ignoredDecimals").max=element.value;sourceControls();setDirty();});}
$("config").oninput=()=>{jsonDraft=true;setDirty();};
$("loadJson").onclick=()=>{try{const cfg=JSON.parse($("config").value);if(cfg.id!==selected())throw new Error("Meter ID cannot change.");if(!cfg.camera||!cfg.source||!Array.isArray(cfg.rois))throw new Error("Configuration must include camera, source and digit regions.");current.config=cfg;resetDigitEditor();renderConfig();setDirty();drawPreview();void renderSourceImage();message("JSON loaded into the editor. Save to apply.");}catch(error){message(error.message,true);}};
function point(event){const box=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),y:Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))};}
function cancelDigitDrag() {
  if(!digitDrag)return;
  restoreDigitSnapshot(digitDrag.before);drawPreview();
}
canvas.onpointerdown=event=>{
  if(!event.isPrimary || event.button!==0 || digitDrag)return;
  if(!(landmarkTarget!==null || digitMode==="landmark"?landmarkEditorReady():digitEditorReady())) {
    if(showingOriginal)message("Switch to Aligned / regions to edit digit boxes.",true);
    else if(jsonDraft)message("Apply or discard the JSON edit before editing digits.",true);
    else if(current && JSON.stringify(current.config.perspective)!==JSON.stringify(savedConfig?.perspective))message("Save perspective settings before editing digit regions.",true);
    return;
  }
  event.preventDefault();canvas.focus({preventScroll:true});
  const position=point(event),before=digitSnapshot();
  if(landmarkTarget!==null){startPoint=position;draftPoint=position;digitDrag={kind:"landmark",target:landmarkTarget,before,pointerId:event.pointerId};}
  else if(digitMode==="landmark"){
    const box=canvas.getBoundingClientRect(),hit=RoiGeometry.hitTest(current.config.alignment.anchors,position,{x:8/box.width,y:8/box.height},selectedLandmark,digitSize());
    selectedLandmark=hit?.index??-1;
    if(hit)digitDrag={...hit,kind:"landmark-edit",before,origin:position,roi:{...current.config.alignment.anchors[hit.index]},pointerId:event.pointerId};
  }
  else if(digitMode==="add") {
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
  if(["add","landmark"].includes(digitDrag.kind))draftPoint=position;
  else {
    const {roi,origin,handle,index}=digitDrag;
    if(digitDrag.kind==="landmark-edit"){
      const dx=position.x-origin.x,dy=position.y-origin.y,anchor=handle==="move"?null:RoiGeometry.handles(roi,digitSize())[handle];
      const next=handle==="move"?RoiGeometry.move(roi,dx,dy,digitSize()):RoiGeometry.resize(roi,handle,{x:anchor.x+dx,y:anchor.y+dy},minimumDigitSize(),digitSize());
      if(validLandmark(next,index))current.config.alignment.anchors[index]=next;
      drawPreview();return;
    }
    if(handle==="rotate") {
      const delta=RoiGeometry.angleAt(roi,position,digitSize())-RoiGeometry.angleAt(roi,origin,digitSize());
      const rotated=RoiGeometry.rotate(roi,(roi.rotation_degrees||0)+delta,digitSize());
      if(rotated)current.config.rois[index]=rotated;
      drawPreview();return;
    }
    const dx=position.x-origin.x,dy=position.y-origin.y;
    if(handle==="move" && $("editGroup").checked){current.config.rois=RoiGeometry.moveGroup(digitDrag.before.rois,dx,dy,digitSize());drawPreview();return;}
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
  if(kind==="landmark"){
    const roi=RoiGeometry.fromPoints(startPoint,draftPoint,minimumDigitSize());
    if(validLandmark(roi,digitDrag.target)){current.config.alignment.anchors[digitDrag.target]=roi;selectedLandmark=digitDrag.target;digitMode="landmark";landmarkTarget=null;}
    else message("Choose a landmark at least 9 image pixels wide/high, covering at most 10% of the photo.",true);
    digitDrag=null;startPoint=null;draftPoint=null;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);commitDigitEdit(before);return;
  }
  if(kind==="add") {
    const drawn=RoiGeometry.fromPoints(startPoint,draftPoint,minimumDigitSize());
    const roi=drawn && ($("drawDigitFrame").value==="inner"?RoiGeometry.outerFrame(drawn,digitSize()):drawn);
    if(roi){current.config.rois.push(roi);selectedDigit=current.config.rois.length-1;}
    else message(drawn?"Leave room for the outer 20% margins inside the photo. Move the digit frame inward or draw the outer crop directly.":"Draw a box at least 9 image pixels wide and high.",true);
  }
  digitDrag=null;startPoint=null;draftPoint=null;
  if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
  commitDigitEdit(before);
};
canvas.onpointercancel=canvas.onlostpointercapture=canvas.onblur=cancelDigitDrag;
canvas.onkeydown=event=>{
  if(event.key==="Escape") {
    event.preventDefault();if(digitDrag)cancelDigitDrag();else{selectedDigit=-1;landmarkTarget=null;drawPreview();}return;
  }
  if((!digitEditorReady() && !(digitMode==="landmark" && landmarkEditorReady())) || digitDrag)return;
  if(event.ctrlKey || event.metaKey) {
    if(event.key.toLowerCase()==="z"){event.preventDefault();undoDigitEdit(event.shiftKey);}
    else if(event.key.toLowerCase()==="y"){event.preventDefault();undoDigitEdit(true);}
    return;
  }
  if(["Delete","Backspace"].includes(event.key)){event.preventDefault();if(digitMode==="landmark")deleteSelectedLandmark();else deleteSelectedDigit();return;}
  if(digitMode==="landmark"){
    const roi=current.config.alignment.anchors[selectedLandmark];
    if(!roi || !["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;
    event.preventDefault();const step=event.shiftKey?10:1,dx=(event.key==="ArrowRight"?step:event.key==="ArrowLeft"?-step:0)/canvas.width,dy=(event.key==="ArrowDown"?step:event.key==="ArrowUp"?-step:0)/canvas.height;
    const next=event.altKey?RoiGeometry.resize(roi,dx?"e":"s",{x:roi.x+roi.w+dx,y:roi.y+roi.h+dy},minimumDigitSize(),digitSize()):RoiGeometry.move(roi,dx,dy,digitSize());
    if(validLandmark(next,selectedLandmark)){const before=digitSnapshot();current.config.alignment.anchors[selectedLandmark]=next;commitDigitEdit(before);}return;
  }
  const roi=current.config.rois[selectedDigit];
  if(roi && ["BracketLeft","BracketRight"].includes(event.code)){event.preventDefault();changeDigitRotation((((roi.rotation_degrees||0)+(event.code==="BracketRight"?1:-1)*(event.shiftKey?10:1)+540)%360)-180);return;}
  if(!roi || !["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;
  event.preventDefault();const before=digitSnapshot(),step=event.shiftKey?10:1;
  const dx=(event.key==="ArrowRight"?step:event.key==="ArrowLeft"?-step:0)/canvas.width;
  const dy=(event.key==="ArrowDown"?step:event.key==="ArrowUp"?-step:0)/canvas.height;
  if($("editGroup").checked){if(event.altKey){message("Select one digit to resize it.",true);return;}current.config.rois=RoiGeometry.moveGroup(current.config.rois,dx,dy,digitSize());commitDigitEdit(before);return;}
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
async function captureCamera(testOnly){if(dirty)await saveConfiguration(false);if(!selected())throw new Error("Add a meter and select its camera first.");const result=await(await api(`/api/meters/${selected()}/capture?test_only=${testOnly}&include_images=true`,{method:"POST"})).json();if(!testOnly)await refresh();await renderResult(result,testOnly);$("captureStatus").textContent=testOnly?"Test complete · Not recorded":"Capture recorded";message(testOnly?"Test complete. The photo and available diagnostics are shown. History is unchanged.":"Capture recorded. Review its result in History.");}
$("cameraTest").onclick=act(()=>captureCamera(true));
$("cameraCapture").onclick=act(async()=>{if(dirty)throw new Error("Save configuration before recording a reading.");await captureCamera(false);});
async function uploaded(testOnly){if(!selected())throw new Error("Add a meter first.");if(dirty)throw new Error("Save configuration before testing or recording an upload.");const file=$("upload").files[0];if(!file)throw new Error("Select a JPEG or PNG first.");const now=encodeURIComponent(new Date().toISOString());const path=testOnly?`/api/meters/${selected()}/test?captured_at=${now}`:`/api/device/${selected()}/images?captured_at=${now}&event_id=${crypto.randomUUID()}`;const result=await(await api(path,{method:"POST",headers:{"Content-Type":file.type},body:file},testOnly?token:$("uploadToken").value)).json();if(!testOnly)await refresh();await renderResult(result,testOnly);}
$("testImage").onclick=act(()=>uploaded(true));$("submitImage").onclick=act(()=>uploaded(false));
$("manualForm").onsubmit=act(async()=>{if(dirty)throw new Error("Save or discard configuration changes before recording a baseline.");await api(`/api/meters/${selected()}/manual`,{method:"POST",body:JSON.stringify({value_m3:$("manualValue").value,kind:$("manualKind").value,reason:$("manualReason").value})});await refresh();$("manualReason").value="";message("Verified baseline recorded. Previous history is preserved.");});
async function download(path,name){const response=await api(path);const url=URL.createObjectURL(await response.blob());const anchor=document.createElement("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("export").onclick=act(()=>download("/api/config/export","waterwatch-config.json"));$("logs").onclick=act(()=>download(`/api/meters/${selected()}/logs`,`${selected()}-log.ndjson`));
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
async function boot(){try{const session=await(await api("/api/session")).json();ingress=session.authentication==="ingress";if(ingress){busy=true;updateControls();await refresh();$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=true;requestAnimationFrame(resizePreview);}}catch(error){message(error.message,true);}finally{busy=false;updateControls();}}
boot();
