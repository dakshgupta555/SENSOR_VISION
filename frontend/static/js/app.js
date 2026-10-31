import { api } from "./api.js";
import { store, loadSimProfiles } from "./store.js";
import { $, $$, esc, icons, toast, modal, timeOf, ago } from "./ui.js";

const ROUTES = {
  home: ["Home", () => import("./pages/home.js")],
  inspection: ["New Inspection", () => import("./pages/inspection.js")],
  live: ["Live Monitoring", () => import("./pages/live.js")],
  optical: ["Optical Inspection", () => import("./pages/optical.js")],
  score: ["Trust Score Engine", () => import("./pages/score.js")],
  library: ["Component Library", () => import("./pages/library.js")],
  manufacturers: ["Manufacturer Database", () => import("./pages/manufacturers.js")],
  history: ["Inspection History", () => import("./pages/history.js")],
  reports: ["Reports", () => import("./pages/reports.js")],
  settings: ["Device Settings", () => import("./pages/settings.js")],
};

let current = null;

async function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "home";
  const [name, ...params] = hash.split("/");
  const def = ROUTES[name] || ROUTES.home;
  if (current && current.destroy) { try { current.destroy(); } catch (e) { console.error(e); } }
  current = null;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (ROUTES[name] ? name : "home")));
  $("#page-title").textContent = def[0];
  document.title = `${def[0]} — IC VISION`;
  const view = $("#view");
  view.innerHTML = `<div class="loading"><i data-lucide="loader-2" class="spin"></i>Loading…</div>`;
  icons(view);
  try {
    const mod = await def[1]();
    view.innerHTML = "";
    current = (await mod.render(view, params)) || null;
    icons(view);
    window.scrollTo(0, 0);
  } catch (e) {
    console.error(e);
    view.innerHTML = `<div class="callout error"><i data-lucide="alert-triangle"></i><div><b>This page could not load.</b><br>${esc(e.message)}</div></div>`;
    icons(view);
  }
}

// ------------------------------------------------------------ header
function renderHeader() {
  const d = store.device || {};
  const sim = store.isSim();
  $$("#mode-switch button").forEach((b) => b.classList.toggle("on", b.dataset.mode === store.mode));
  const chip = $("#hw-chip");
  let dot = "bad", text = "Not connected";
  if (sim) {
    const sp = (store.simProfiles || []).find((p) => p.id === d.sim_profile);
    if (d.state === "paused") { dot = "warn"; text = "Simulator paused"; }
    else { dot = "sim"; text = `Simulator running${sp ? " · " + sp.label.split(" — ")[0] : ""}`; }
  } else {
    const st = d.state;
    if (st === "streaming") { dot = "live"; text = `ESP32 streaming · ${d.sensor || "sensor"} · ${(d.rx_rate_hz || 0).toFixed(1)} Hz`; }
    else if (st === "connected") { dot = "warn"; text = d.message || "Port open — waiting for data"; }
    else if (st === "connecting") { dot = "warn"; text = "Connecting…"; }
    else if (st === "reconnecting") { dot = "warn"; text = d.message || "Reconnecting…"; }
    else if (st === "stale") { dot = "bad"; text = `No data · last sample ${timeOf(d.last_sample_ms)} (${ago(d.last_sample_ms)})`; }
    else { dot = "bad"; text = d.message ? `Not connected · ${d.message}` : "Hardware not connected"; }
  }
  chip.innerHTML = `<span class="dot ${dot}"></span><span>${esc(text.length > 70 ? text.slice(0, 68) + "…" : text)}</span>`;
  chip.title = text;
  $("#port-chip").innerHTML = sim
    ? `<span class="k">Controller</span><span>Simulated</span>`
    : `<span class="k">Port</span><span class="num">${esc(d.port || "—")}${d.baud ? " @ " + d.baud : ""}</span>`;
  const banner = $("#sim-banner");
  banner.hidden = !sim;
  if (sim) {
    const sp = (store.simProfiles || []).find((p) => p.id === d.sim_profile);
    const ov = d.sim_override && d.sim_override !== "profile" ? ` · stream: ${d.sim_override}` : "";
    $("#sim-banner-profile").textContent = sp ? `Profile: ${sp.label}${ov}` : "";
  }
}

function tickClock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) + "  " +
    now.toLocaleTimeString([], { hour12: false });
  if (!store.isSim() && store.device.state === "stale") renderHeader();
}

async function switchMode(mode) {
  if (mode === store.mode) return;
  try {
    const st = await api.post("/api/mode", { mode });
    store.latest = null; store.stats = null;
    applyStatus(st);
    toast(mode === "live" ? "Live hardware mode — only physical measurements are shown." : "Simulation mode — all values are generated.", "ok");
    store.emit("mode", mode);
  } catch (e) { toast(e.message, "error"); }
}

function applyStatus(st) {
  store.status = st;
  store.mode = st.mode;
  store.device = st.device;
  store.settings = st.settings;
  store.stats = st.stats;
  renderHeader();
}

// ------------------------------------------------------------ SSE
function connectStream() {
  const es = new EventSource("/api/stream");
  const on = (evt, fn) => es.addEventListener(evt, (e) => { let d; try { d = JSON.parse(e.data); } catch (_) { return; } fn(d); });
  on("hello", (d) => { store.device = d.device; store.mode = d.device.mode; renderHeader(); store.emit("device", store.device); });
  on("measurement", (d) => { store.latest = d.sample; store.stats = d.stats; store.emit("measurement", d); });
  on("device", (d) => {
    const modeChanged = d.mode !== store.mode;
    if (modeChanged) { store.latest = null; store.stats = null; }
    store.device = d; store.mode = d.mode; renderHeader(); store.emit("device", d);
    if (modeChanged) store.emit("mode", d.mode);
  });
  on("acq_reset", (d) => { store.latest = null; store.stats = null; store.emit("acq_reset", d); });
  for (const evt of ["inspection", "inspection_progress", "functional_row", "iv_point", "serial_log", "serial_msg",
    "inspection_prompt_closed"]) on(evt, (d) => store.emit(evt, d));
  on("inspection_prompt", (d) => { store.emit("inspection_prompt", d); showPrompt(d); });
  es.onerror = () => {
    const chip = $("#hw-chip");
    chip.innerHTML = `<span class="dot bad"></span><span>Server connection lost — retrying</span>`;
  };
  es.onopen = () => api.get("/api/status").then(applyStatus).catch(() => {});
}

let promptOpen = null;
async function showPrompt(d) {
  if (promptOpen === d.id) return;
  promptOpen = d.id;
  const ans = await modal({
    title: d.title,
    body: `<div class="callout warn"><i data-lucide="hand"></i><div>${esc(d.message)}</div></div>
      <p class="small muted" style="margin-bottom:0">Continue only after the DUT supply is disconnected. Skipping records the I-V signature as not measured.</p>`,
    actions: [{ label: "Skip I-V sweep", value: false }, { label: "Probe in place — run sweep", value: true, kind: "btn-primary" }],
  });
  promptOpen = null;
  try { await api.post(`/api/inspections/${d.id}/prompt`, { accept: !!ans }); } catch (e) { /* prompt already closed */ }
}
store.on("inspection_prompt_closed", () => { const b = document.querySelector(".modal-back"); if (b && promptOpen) b.remove(); promptOpen = null; });

// ------------------------------------------------------------ boot
async function boot() {
  icons();
  $$("#mode-switch button").forEach((b) => b.addEventListener("click", () => switchMode(b.dataset.mode)));
  try {
    const st = await api.get("/api/status");
    applyStatus(st);
    $("#sidebar-foot").innerHTML = `IC VISION ${esc(st.app.version)}<br>Reference dataset ${esc(st.app.reference_dataset_version)}`;
  } catch (e) { toast(e.message, "error"); }
  loadSimProfiles().then(renderHeader).catch(() => {});
  connectStream();
  setInterval(tickClock, 1000);
  tickClock();
  window.addEventListener("hashchange", route);
  route();
}

boot();
