/* All secrets stay in memory. No CDN, telemetry, local storage or token URLs. */
"use strict";
const $ = id => document.getElementById(id);
let token = "", meters = [], current = null, picture = null, showingOriginal = false, startPoint = null;
let cameras = [], flashLights = [], ingress = false;
const canvas = $("preview"), ctx = canvas.getContext("2d");
const objectUrls = new Map();
function message(text) { $("message").textContent = text; }
async function api(path, options = {}, credential = token) {
  const headers = {...(credential ? {Authorization: `Bearer ${credential}`} : {}), ...options.headers};
  if (options.body && typeof options.body === "string") headers["Content-Type"] = "application/json";
  const response = await fetch(new URL(path.replace(/^\/+/, ""), document.baseURI), {...options, headers});
  if (!response.ok) { let body; try { body = await response.json(); } catch { body = {}; } throw new Error(JSON.stringify(body.detail || response.statusText)); }
  return response;
}
function act(fn) { return async event => { event?.preventDefault(); try { await fn(event); } catch (error) { message(error.message); } }; }
function selected() { return current?.config.id; }
function renderConfig() {
  if (!current) return;
  const c = current.config;
  $("config").value = JSON.stringify(c, null, 2);
  $("decimals").value = c.decimal_places; $("threshold").value = c.min_confidence;
  $("maxRate").value = c.max_rate_lph; $("interval").value = c.camera.interval_seconds;
  $("led").value = c.camera.led_duty; $("resolution").value = c.camera.resolution;
  $("sourceKind").value = c.source.kind;
  $("scheduledCapture").checked = c.source.enabled;
  $("flashEnabled").checked = Boolean(c.source.flash);
  $("flashBrightness").value = c.source.flash?.brightness_pct ?? 20;
  renderCameras(c.source.camera_entity);
  renderFlashLights(c.source.flash?.light_entity);
  $("led").disabled = $("resolution").disabled = c.source.kind === "home_assistant";
  sourceControls();
}
function sourceControls(){const fromHa=$("sourceKind").value==="home_assistant";$("scheduledCapture").disabled=$("flashEnabled").disabled=!fromHa;if(!fromHa){$("scheduledCapture").checked=false;$("flashEnabled").checked=false;}for(const id of ["cameraEntity","cameraPreview","cameraTest","cameraCapture"])$(id).disabled=!fromHa;for(const id of ["flashEntity","flashBrightness"])$(id).disabled=!fromHa||!$("flashEnabled").checked;}
$("sourceKind").onchange=sourceControls;
$("flashEnabled").onchange=sourceControls;
function renderCameras(selectedEntity) {
  const choices = [...cameras];
  if (selectedEntity && !choices.some(c => c.entity_id === selectedEntity)) choices.push({entity_id:selectedEntity,name:selectedEntity,state:"not checked"});
  $("cameraEntity").replaceChildren(new Option("Select a camera", ""), ...choices.map(c => new Option(`${c.name} (${c.state})`, c.entity_id)));
  $("cameraEntity").value = selectedEntity || "";
}
function renderFlashLights(selectedEntity) {
  const choices = [...flashLights];
  if (selectedEntity && !choices.some(c => c.entity_id === selectedEntity)) choices.push({entity_id:selectedEntity,name:selectedEntity,state:"not checked"});
  $("flashEntity").replaceChildren(new Option("Select the meter's ESPHome light", ""), ...choices.map(c => new Option(`${c.name} (${c.state})`, c.entity_id)));
  $("flashEntity").value = selectedEntity || "";
}
function drawPreview() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (picture) ctx.drawImage(picture, 0, 0, canvas.width, canvas.height);
  if (!current || showingOriginal && current.config.perspective) return;
  ctx.lineWidth = 2; ctx.font = "16px sans-serif";
  current.config.rois.forEach((r, i) => { ctx.strokeStyle = "#54eeaf"; ctx.fillStyle = "#153a2b"; ctx.strokeRect(r.x*canvas.width,r.y*canvas.height,r.w*canvas.width,r.h*canvas.height); ctx.fillText(String(i),r.x*canvas.width+3,r.y*canvas.height+18); });
}
async function loadImage(variant = "overlay") {
  picture = null; showingOriginal = variant === "original";
  const row = current?.latest_capture;
  if (!row?.image_key) { drawPreview(); return; }
  const path = `/api/meters/${selected()}/images/${row.event_id}/${variant}`;
  try {
    const response = await api(path); const blob = await response.blob();
    if (objectUrls.has("preview")) URL.revokeObjectURL(objectUrls.get("preview"));
    const url = URL.createObjectURL(blob); objectUrls.set("preview", url);
    picture = await createImageBitmap(blob); canvas.width = picture.width; canvas.height = picture.height; drawPreview();
    $("imageStamp").textContent = new Date(row.captured_at).toLocaleString();
  } catch (error) { drawPreview(); message(`Preview: ${error.message}`); }
  try {
    const response = await api(`/api/meters/${selected()}/images/${row.event_id}/processed`);
    if (objectUrls.has("processed")) URL.revokeObjectURL(objectUrls.get("processed"));
    const url = URL.createObjectURL(await response.blob()); objectUrls.set("processed", url);
    $("processed").src = url; $("processed").hidden = false;
  } catch { $("processed").hidden = true; }
}
function drawGraph(rows) {
  const c = $("graph"), g = c.getContext("2d"), points = rows.filter(r => r.accepted).reverse();
  g.clearRect(0,0,c.width,c.height); if (points.length < 2) return;
  const values = points.map(r => Number(r.value_m3)), times = points.map(r => Date.parse(r.captured_at));
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max-min,.001), duration = Math.max(times.at(-1)-times[0],1);
  g.strokeStyle = "#2f7d61"; g.lineWidth = 3; g.beginPath();
  points.forEach((p,i) => { const x=55+(times[i]-times[0])/duration*(c.width-85), y=180-(values[i]-min)/span*140; if(i) g.lineTo(x,y); else g.moveTo(x,y); }); g.stroke();
  g.fillStyle = "#60826e"; g.font = "14px sans-serif"; g.fillText(`${max.toFixed(3)} m³`,10,22); g.fillText(`${min.toFixed(3)} m³`,10,210);
}
async function renderMeter() {
  current = meters.find(m => m.config.id === $("meter").value) || null;
  if (!current) { $("registration").hidden = false; return; }
  const s = current.state;
  $("total").textContent = s.total_m3 === null ? "—" : Number(s.total_m3).toLocaleString(undefined,{maximumFractionDigits:6});
  $("last").textContent = s.last_success ? new Date(s.last_success).toLocaleString() : "Baseline required";
  $("freshness").textContent = s.stale ? "No recent verified reading" : "Verified reading is fresh";
  $("confidence").textContent = s.confidence === null ? "—" : `${s.confidence}%`;
  $("readingStatus").textContent = s.error; $("readingStatus").className = s.error === "ok" ? "small good" : "small bad";
  $("rssi").textContent = `Wi-Fi ${s.rssi ?? "—"} dBm · camera ${s.camera_online ? "online" : "unavailable"}`;
  $("captureStatus").textContent = `Camera capture: ${current.capture_status?.status || "not captured"}`;
  renderConfig(); await loadImage();
  const rows = await (await api(`/api/meters/${selected()}/history`)).json();
  $("history").replaceChildren(...rows.map(r => {
    const tr = document.createElement("tr"); tr.className = r.accepted ? "good" : "bad";
    [new Date(r.captured_at).toLocaleString(),r.raw_value ?? "—",r.value_m3 ?? "—",`${(r.confidence*100).toFixed(1)}%`,r.reason,r.source].forEach(text => { const td=document.createElement("td"); td.textContent=text; tr.append(td); }); return tr;
  })); drawGraph(rows);
}
async function refresh() {
  const id = $("meter").value;
  meters = await (await api("/api/meters")).json();
  $("meter").replaceChildren(...meters.map(m => new Option(m.config.name,m.config.id)));
  if (meters.some(m => m.config.id === id)) $("meter").value = id;
  const status = await (await api("/api/status")).json();
  $("connection").textContent = `MQTT ${status.mqtt} · OCR ${status.ocr_available ? "ready" : "engine unavailable"}`;
  await renderMeter();
}
$("loginForm").onsubmit = act(async () => { token=$("token").value; await refresh(); $("token").value=""; $("login").hidden=true; $("workspace").hidden=false; message(""); });
$("logout").onclick = () => { token=""; current=null; meters=[]; $("workspace").hidden=true; $("login").hidden=false; $("history").replaceChildren(); $("cameraToken").textContent=""; $("uploadToken").value=""; $("processed").hidden=true; $("processed").removeAttribute("src"); picture=null; ctx.clearRect(0,0,canvas.width,canvas.height); for(const url of objectUrls.values()) URL.revokeObjectURL(url); objectUrls.clear(); message(""); };
$("refresh").onclick=act(refresh); $("meter").onchange=act(renderMeter);
$("add").onclick=()=>{$("registration").hidden=!$("registration").hidden;};
$("registerForm").onsubmit=act(async()=>{const data=await(await api("/api/meters",{method:"POST",body:JSON.stringify({id:$("newId").value,name:$("newName").value})})).json(); $("cameraToken").textContent=`Camera token (shown once): ${data.camera_token}`; await refresh(); $("meter").value=data.config.id; await renderMeter();});
$("save").onclick=act(async()=>{if(!current)return; const c=structuredClone(current.config); c.decimal_places=Number($("decimals").value); c.min_confidence=Number($("threshold").value); c.max_rate_lph=Number($("maxRate").value); c.camera.interval_seconds=Number($("interval").value); c.camera.led_duty=Number($("led").value); c.camera.resolution=$("resolution").value; c.source={kind:$("sourceKind").value,camera_entity:$("sourceKind").value==="home_assistant"?$("cameraEntity").value:null,enabled:$("scheduledCapture").checked,flash:$("flashEnabled").checked?{light_entity:$("flashEntity").value,brightness_pct:Number($("flashBrightness").value),settle_ms:c.source.flash?.settle_ms??1500}:null}; await api(`/api/meters/${selected()}`,{method:"PUT",body:JSON.stringify(c)}); await refresh(); message(c.source.flash?"Saved. Preview and readings will briefly flash the selected meter light.":c.source.kind==="home_assistant"?"Saved. Capture interval applies to host polling; camera settings stay in ESPHome.":"Saved. Camera parameters apply on the next configuration poll.");});
$("loadJson").onclick=act(async()=>{const c=JSON.parse($("config").value); if(c.id!==selected()) throw new Error("Meter ID cannot change"); current.config=c; renderConfig(); drawPreview(); message("Loaded into editor. Save to apply.");});
$("clearRois").onclick=()=>{if(current){current.config.rois=[];renderConfig();drawPreview();}};
function point(e){const b=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))};}
canvas.onpointerdown=e=>{if(!current||!picture)return;if(showingOriginal&&current.config.perspective){message("Select aligned / ROIs before editing perspective-corrected regions.");return;}startPoint=point(e);canvas.setPointerCapture(e.pointerId);};
canvas.onpointerup=e=>{if(!startPoint||!current)return;const end=point(e),r={x:Math.min(startPoint.x,end.x),y:Math.min(startPoint.y,end.y),w:Math.abs(startPoint.x-end.x),h:Math.abs(startPoint.y-end.y)};startPoint=null;if(r.w>.005&&r.h>.005){current.config.rois.push(r);renderConfig();drawPreview();}};
$("original").onclick=act(()=>loadImage("original")); $("overlay").onclick=act(()=>loadImage("overlay"));
async function uploaded(test){if(!selected())throw new Error("Register a meter first");const file=$("upload").files[0];if(!file)throw new Error("Select an image first");const now=encodeURIComponent(new Date().toISOString());const path=test?`/api/meters/${selected()}/test?captured_at=${now}`:`/api/device/${selected()}/images?captured_at=${now}&event_id=${crypto.randomUUID()}`;const result=await(await api(path,{method:"POST",headers:{"Content-Type":file.type},body:file},test?token:$("uploadToken").value)).json();$("testResult").textContent=JSON.stringify(result,null,2);if(!test)await refresh();}
$("testImage").onclick=act(()=>uploaded(true));$("submitImage").onclick=act(()=>uploaded(false));
$("manualForm").onsubmit=act(async()=>{await api(`/api/meters/${selected()}/manual`,{method:"POST",body:JSON.stringify({value_m3:$("manualValue").value,kind:$("manualKind").value,reason:$("manualReason").value})});await refresh();message("Manual event recorded. Previous statistics have not been rewritten.");});
async function download(path,name){const response=await api(path);const url=URL.createObjectURL(await response.blob());const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("export").onclick=act(()=>download("/api/config/export","waterwatch-config.json"));$("logs").onclick=act(()=>download(`/api/meters/${selected()}/logs`,`${selected()}-log.ndjson`));
// Refresh only on request: background refresh would discard unsaved ROI edits.
$("loadCameras").onclick=act(async()=>{const entity=$("cameraEntity").value,light=$("flashEntity").value;[cameras,flashLights]=await Promise.all([api("/api/home-assistant/cameras").then(r=>r.json()),api("/api/home-assistant/flash-lights").then(r=>r.json())]);renderCameras(entity);renderFlashLights(light);message(cameras.length?"Select your meter camera and optional ESPHome flash light, then save.":"No cameras found.");});
$("cameraPreview").onclick=act(async()=>{if(!selected())throw new Error("Register a meter and save its camera first");const response=await api(`/api/meters/${selected()}/camera-image`,{method:"POST"});picture=await createImageBitmap(await response.blob());showingOriginal=true;canvas.width=picture.width;canvas.height=picture.height;drawPreview();$("imageStamp").textContent="Live preview · not recorded";$("processed").hidden=true;message(current.config.perspective?"Preview is unaligned. Use a recorded capture to edit aligned regions.":"Draw one region per digit, then save before testing OCR.");});
async function captureCamera(testOnly){if(!selected())throw new Error("Register a meter and save its camera first");const result=await(await api(`/api/meters/${selected()}/capture?test_only=${testOnly}`,{method:"POST"})).json();$("testResult").textContent=JSON.stringify(result,null,2);if(!testOnly)await refresh();message(testOnly?"OCR test complete. Reading history unchanged.":"Capture recorded. Review the result before relying on the reading.");}
$("cameraTest").onclick=act(()=>captureCamera(true));
$("cameraCapture").onclick=act(()=>captureCamera(false));
async function boot(){try{const session=await(await api("/api/session")).json();ingress=session.authentication==="ingress";if(ingress){await refresh();$("login").hidden=true;$("workspace").hidden=false;$("logout").hidden=true;}}catch(error){message(error.message);}}
boot();
