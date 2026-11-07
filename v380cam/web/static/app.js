"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const DEFAULT_LENS = { yaw: 0, pitch: 50, roll: 0, fov: 100 };

const S = {
  devices: [], scanning: false, view: "cameras", cam: null, lens: { ...DEFAULT_LENS },
  flat: false, sid: "", sig: "", pollTimer: null, statusTimer: null, thumbTimer: null,
};

/* ---------- helpers ---------- */
function hydrateIcons(root = document) {
  $$("[data-ic]", root).forEach(el => {
    const html = icon(el.dataset.ic, +el.dataset.size || 19);
    if (el.tagName === "BUTTON" || el.classList.contains("badge")) el.innerHTML = html;
    else el.innerHTML = html;
  });
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function api(path, body) {
  const r = await fetch("/api/" + path, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch (_) {}
  return { ok: r.ok, status: r.status, data };
}
let toastTimer;
function toast(msg, tone = "ok") {
  const t = $("#toast");
  t.textContent = msg;
  t.style.setProperty("--tc", { ok: "var(--ok)", bad: "var(--bad)", warn: "var(--warn)" }[tone] || "var(--accent)");
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* ---------- navigation ---------- */
function show(view) {
  S.view = view;
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + view));
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === view ||
    (view === "viewer" && b.dataset.view === "cameras")));
  if (view !== "viewer") closeViewer();
  history.replaceState(null, "", view === "viewer" ? "#/camera/" + encodeURIComponent(S.cam.key) : "#/" + view);
}
$$(".nav-btn").forEach(b => b.onclick = () => show(b.dataset.view));

/* ---------- scanning + device list ---------- */
async function startScan() {
  $("#scan-btn").disabled = true;
  await api("scan", {});
  poll(true);
}
$("#scan-btn").onclick = startScan;

async function poll(fast) {
  clearTimeout(S.pollTimer);
  let next = 4000;
  try {
    const { data } = await api("devices");
    S.scanning = data.scanning;
    S.devices = data.devices || [];
    renderStatus(data);
    renderCameras();
    renderDevices();
    next = data.scanning ? 600 : 4000;
  } catch (_) { next = 2500; }
  S.pollTimer = setTimeout(poll, next);
}

function renderStatus(d) {
  const cams = S.devices.filter(x => x.is_camera).length;
  const btn = $("#scan-btn");
  btn.disabled = S.scanning;
  $("#scan-label").textContent = S.scanning ? "Scanning…" : "Scan network";
  $("#progress-bar").style.width = S.scanning ? Math.max(3, d.progress * 100) + "%" : "0";
  $("#radar").classList.toggle("idle", !S.scanning);
  const dot = $("#netdot");
  dot.className = "dot pulse";
  dot.style.background = S.scanning ? "var(--accent)" : cams ? "var(--ok)" : "var(--warn)";
  $("#nettitle").textContent = S.scanning ? "Scanning" : plural(cams, "camera");
  $("#netsub").textContent = S.scanning ? d.message : `${S.devices.length} devices on your network`;
  $("#cam-sub").textContent = S.scanning
    ? `${d.message}  ·  ${plural(S.devices.length, "device")} so far`
    : cams ? `${plural(cams, "camera")} found  ·  ${plural(S.devices.length, "device")} on your network`
           : "No cameras found on your network";
}

function renderCameras() {
  const cams = S.devices.filter(d => d.is_camera);
  const sig = JSON.stringify(cams.map(d => [d.key, d.ip, d.saved, d.name, d.kind]));
  const grid = $("#cam-grid"), empty = $("#cam-empty");
  grid.hidden = !cams.length;
  empty.hidden = !!cams.length;
  if (!cams.length) {
    $("#empty-title").textContent = S.scanning ? "Looking for cameras…" : "No cameras found";
    $("#empty-sub").textContent = S.scanning ? "Make sure the camera is powered on and on the same Wi-Fi."
      : `${plural(S.devices.length, "device")} responded, none look like a camera. Check power and Wi-Fi, then scan again.`;
  }
  if (sig === S.sig) return;
  S.sig = sig;
  grid.innerHTML = cams.map((d, i) => `
    <article class="card" tabindex="0" data-key="${esc(d.key)}" style="animation-delay:${i * 70}ms">
      <div class="thumb">
        ${d.saved ? `<span class="live"><i></i>LIVE</span>` : ""}
        <div class="ph">${icon(d.saved ? "camera" : "lock", 44, 1.3)}<span>${d.saved ? "Loading preview…" : "Sign in to preview"}</span></div>
        ${d.saved ? `<img alt="" data-thumb="${esc(d.key)}">` : ""}
      </div>
      <div class="card-body">
        <div class="card-head"><span>${esc(d.name)}</span>${icon("arrow", 18, 1.9)}</div>
        <div class="card-sub">${esc(d.ip)}${d.cam_id ? "  ·  ID " + esc(d.cam_id) : ""}</div>
        <div class="chips">${d.kind === "v380"
          ? `<span class="chip ${d.saved ? "ok" : "warn"}">${icon(d.saved ? "shield" : "lock", 13, 2)}${d.saved ? "Saved login" : "Sign-in needed"}</span><span class="chip accent">V380</span>`
          : `<span class="chip">${icon("wifi", 13, 2)}RTSP / ONVIF</span><span class="chip warn">Not supported yet</span>`}</div>
      </div>
    </article>`).join("");
  $$(".card", grid).forEach(c => {
    const open = () => openCamera(S.devices.find(d => d.key === c.dataset.key));
    c.onclick = open;
    c.onkeydown = e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open());
  });
  refreshThumbs();
}

function refreshThumbs() {
  $$("img[data-thumb]").forEach(img => {
    const probe = new Image();
    probe.onload = () => {
      img.src = probe.src;
      const ph = $(".ph", img.parentElement);
      if (ph) ph.style.display = "none";
    };
    probe.src = `/api/snapshot/${encodeURIComponent(img.dataset.thumb)}?t=${Date.now()}`;
  });
}
S.thumbTimer = setInterval(() => { if (S.view === "cameras" && !document.hidden) refreshThumbs(); }, 2500);

function renderDevices() {
  const n = S.devices.filter(d => d.is_camera).length;
  $("#dev-sub").textContent = S.devices.length
    ? `${plural(S.devices.length, "device")} responded  ·  ${plural(n, "camera")}` : "Run a scan to list every device that answered.";
  $("#dev-rows").innerHTML = S.devices.map((d, i) => `
    <div class="row ${d.is_camera ? "cam" : ""}" style="animation-delay:${i * 40}ms">
      <div class="ico">${icon(d.is_camera ? "camera" : "devices", 20, 1.8)}</div>
      <div><b>${esc(d.is_camera ? d.name : d.ip)}</b><div class="meta">${esc(d.ip)}  ·  ${esc(d.mac || "MAC unknown")}</div></div>
      <div class="chips">${d.services.slice(0, 5).map(s => `<span class="chip ${s.startsWith("V380") ? "accent" : ""}">${esc(s)}</span>`).join("")}
        ${d.is_camera ? `<span class="chip ok">${icon("camera", 13, 2)}Camera</span>` : ""}</div>
    </div>`).join("");
}

/* ---------- login ---------- */
let loginDev = null;
function openLogin(dev, error = "") {
  loginDev = dev;
  $("#m-sub").textContent = `${dev.name}  ·  ${dev.ip}`;
  $("#f-id").hidden = !!dev.cam_id;
  $("#m-hint").textContent = dev.cam_id ? `Saved against camera ID ${dev.cam_id}, so it still works if its IP changes.` : "";
  $("#in-pw").value = "";
  $("#in-pw").type = "password";
  $("#m-err").hidden = !error;
  $("#m-err").textContent = error;
  $("#modal").hidden = false;
  setTimeout(() => (dev.cam_id ? $("#in-pw") : $("#in-id")).focus(), 60);
}
function closeLogin() { $("#modal").hidden = true; loginDev = null; }
$("#m-close").onclick = $("#m-cancel").onclick = closeLogin;
$("#modal").onmousedown = e => { if (e.target.id === "modal") closeLogin(); };
$("#pw-eye").onclick = () => {
  const i = $("#in-pw"), show = i.type === "password";
  i.type = show ? "text" : "password";
  $("#pw-eye").innerHTML = icon(show ? "eye_off" : "eye", 18);
};
$("#login-form").onsubmit = async e => {
  e.preventDefault();
  const dev = loginDev, go = $("#m-go"), err = $("#m-err");
  const camId = dev.cam_id || $("#in-id").value.trim();
  const fail = m => { err.textContent = m; err.hidden = false; err.style.animation = "none"; err.offsetWidth; err.style.animation = ""; };
  if (!$("#in-pw").value) return fail("Enter the camera password.");
  if (!/^\d+$/.test(camId)) return fail("Enter the numeric camera ID.");
  go.disabled = true; go.textContent = "Verifying…"; err.hidden = true;
  const remember = $("#in-remember").checked;
  const r = await api("login", { key: dev.key, user: $("#in-user").value.trim() || "admin",
    password: $("#in-pw").value, remember, cam_id: camId });
  go.disabled = false; go.innerHTML = `Connect ${icon("arrow", 16)}`;
  if (!r.data.ok) return fail((r.data.error || "Login failed").replace(/^./, c => c.toUpperCase()));
  closeLogin();
  toast("Signed in" + (remember ? "  ·  login saved" : ""));
  const { data } = await api("devices");
  S.devices = data.devices; S.sig = ""; renderCameras();
  openViewer(S.devices.find(d => d.cam_id === r.data.cam_id) || dev);
};

/* ---------- camera open ---------- */
function openCamera(dev) {
  if (!dev) return;
  if (dev.kind !== "v380") return toast("RTSP / ONVIF cameras aren't supported yet.", "warn");
  dev.saved ? openViewer(dev) : openLogin(dev);
}

/* ---------- viewer ---------- */
const stage = $("#stage"), img = $("#v-img");
function lensQuery() {
  const l = S.lens;
  return `dewarp=${S.flat ? 1 : 0}&yaw=${l.yaw}&pitch=${l.pitch}&roll=${l.roll}&fov=${l.fov}`;
}
function openViewer(dev) {
  S.cam = dev;
  S.sid = Math.random().toString(36).slice(2, 12);
  $("#v-name").textContent = dev.name;
  $("#v-meta").textContent = `${dev.ip}  ·  ID ${dev.cam_id}  ·  ${dev.mac || ""}`;
  $("#v-msg").classList.remove("gone");
  $("#v-msg").innerHTML = `<div><b>Connecting…</b><span>Opening a secure session with the camera</span></div>`;
  setMode(S.flat, false);
  show("viewer");
  img.src = `/api/stream/${encodeURIComponent(dev.key)}?${lensQuery()}&sid=${S.sid}`;
  clearInterval(S.statusTimer);
  S.statusTimer = setInterval(viewerStatus, 800);
}
function closeViewer() {
  clearInterval(S.statusTimer);
  if (img.getAttribute("src")) img.removeAttribute("src");        // drops the MJPEG connection
  if (document.fullscreenElement) document.exitFullscreen();
}
async function viewerStatus() {
  if (!S.cam) return;
  const { data: s } = await api("status/" + encodeURIComponent(S.cam.key));
  const tone = { live: ["var(--ok)", "Live"], connecting: ["var(--warn)", "Connecting"],
    error: ["var(--bad)", "Reconnecting"], auth_failed: ["var(--bad)", "Login rejected"] }[s.status] || ["var(--muted)", "Idle"];
  const dot = $("#v-dot");
  dot.className = "dot pulse"; dot.style.background = tone[0];
  $("#v-state").textContent = tone[1];
  const msg = $("#v-msg");
  if (s.status === "live") { msg.classList.add("gone"); $("#v-hud").textContent = `${s.width}×${s.height}  ·  ${Math.round(s.fps)} fps`; }
  else if (s.status === "auth_failed") {
    msg.classList.remove("gone");
    msg.innerHTML = `<div><b>Login rejected</b><span>The saved password no longer works.</span><br><br><button class="btn primary" id="relogin">Sign in again</button></div>`;
    $("#relogin").onclick = () => { show("cameras"); openLogin(S.cam); };
  } else if (s.status === "error" && !msg.classList.contains("gone")) {
    msg.innerHTML = `<div><b>Can't reach the camera</b><span>${esc(s.error || "Retrying…")}</span></div>`;
  }
}
$("#v-back").onclick = () => show("cameras");
$("#v-forget").onclick = async () => {
  await api("forget", { key: S.cam.key });
  toast("Saved login removed", "warn");
  const { data } = await api("devices"); S.devices = data.devices; S.sig = "";
  show("cameras"); renderCameras();
};

/* lens / dewarp */
let pushTimer = null;
function pushView() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => api("view", { sid: S.sid, dewarp: S.flat, ...S.lens }), 40);
}
function syncSliders() {
  for (const k of ["yaw", "pitch", "roll", "fov"]) {
    const el = $("#s-" + k);
    el.value = S.lens[k];
    el.style.setProperty("--p", ((el.value - el.min) / (el.max - el.min) * 100) + "%");
    $("#o-" + k).textContent = Math.round(S.lens[k]) + "°";
  }
}
function setMode(flat, push = true) {
  S.flat = flat;
  $("#seg-raw").classList.toggle("active", !flat);
  $("#seg-flat").classList.toggle("active", flat);
  $("#lens").hidden = !flat;
  stage.classList.toggle("flat", flat);
  syncSliders();
  if (push) pushView();
}
$("#seg-raw").onclick = () => setMode(false);
$("#seg-flat").onclick = () => setMode(true);
for (const k of ["yaw", "pitch", "roll", "fov"]) {
  $("#s-" + k).oninput = e => { S.lens[k] = +e.target.value; syncSliders(); pushView(); };
}
$("#v-reset").onclick = () => { S.lens = { ...DEFAULT_LENS }; syncSliders(); pushView(); };
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const wrap = v => ((v + 180) % 360 + 360) % 360 - 180;
let drag = null;
stage.onpointerdown = e => { if (!S.flat) return; drag = { x: e.clientX, y: e.clientY }; stage.setPointerCapture(e.pointerId); stage.classList.add("drag"); };
stage.onpointermove = e => {
  if (!drag) return;
  const k = S.lens.fov / stage.clientWidth;
  S.lens.yaw = wrap(S.lens.yaw - (e.clientX - drag.x) * k);
  S.lens.pitch = clamp(S.lens.pitch - (e.clientY - drag.y) * k, -89, 89);
  drag = { x: e.clientX, y: e.clientY };
  syncSliders(); pushView();
};
stage.onpointerup = stage.onpointercancel = () => { drag = null; stage.classList.remove("drag"); };
stage.onwheel = e => {
  if (!S.flat) return;
  e.preventDefault();
  S.lens.fov = clamp(S.lens.fov + e.deltaY * 0.06, 40, 150);
  syncSliders(); pushView();
};
$("#v-snap").onclick = () => {
  const a = document.createElement("a");
  a.href = `/api/snapshot/${encodeURIComponent(S.cam.key)}?${lensQuery()}`;
  a.download = `${S.cam.cam_id}_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.jpg`;
  a.click();
  toast("Snapshot saved");
};
$("#v-full").onclick = toggleFull;
function toggleFull() { document.fullscreenElement ? document.exitFullscreen() : stage.requestFullscreen?.(); }
document.onkeydown = e => {
  if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
  if (e.key === "Escape") { if (!$("#modal").hidden) closeLogin(); else if (S.view === "viewer" && !document.fullscreenElement) show("cameras"); return; }
  if (S.view !== "viewer") return;
  const step = 5;
  if (e.key === "f") toggleFull();
  else if (e.key === "d") setMode(!S.flat);
  else if (S.flat && e.key.startsWith("Arrow")) {
    e.preventDefault();
    if (e.key === "ArrowLeft") S.lens.yaw = wrap(S.lens.yaw - step);
    if (e.key === "ArrowRight") S.lens.yaw = wrap(S.lens.yaw + step);
    if (e.key === "ArrowUp") S.lens.pitch = clamp(S.lens.pitch - step, -89, 89);
    if (e.key === "ArrowDown") S.lens.pitch = clamp(S.lens.pitch + step, -89, 89);
    syncSliders(); pushView();
  } else if (S.flat && (e.key === "+" || e.key === "=")) { S.lens.fov = clamp(S.lens.fov - 5, 40, 150); syncSliders(); pushView(); }
  else if (S.flat && e.key === "-") { S.lens.fov = clamp(S.lens.fov + 5, 40, 150); syncSliders(); pushView(); }
};

/* ---------- boot ---------- */
hydrateIcons();
syncSliders();
poll().then(() => {
  const m = location.hash.match(/^#\/camera\/(.+)$/);
  if (m) { const d = S.devices.find(x => x.key === decodeURIComponent(m[1])); if (d && d.saved) openViewer(d); }
  else if (location.hash === "#/devices") show("devices");
});
