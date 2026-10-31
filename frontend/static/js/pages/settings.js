import { api } from "../api.js";
import { store, loadProfiles } from "../store.js";
import { $, $$, esc, icons, toast, num, timeOf, ago, chipSVG, confirmDialog } from "../ui.js";

const PKG_PROFILE = { "DIP-14": "nor-74hc02-dip14", "DIP-8": "opamp-lm358-dip8", "DIP-28": "mcu-atmega328p-dip28", MODULE: "mcu-esp32-module" };

export async function render(view) {
  const profiles = await loadProfiles();
  let status = await api.get("/api/status");
  let s = status.settings;
  const st = { pkg: "DIP-14", log: [] };

  view.innerHTML = `
    <div class="page-head"><div><h2>Device settings</h2><p>Operating mode, USB serial link to the ESP32, acquisition rate, fixture wiring and the optional I-V probe.</p></div></div>
    <div class="grid">
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="toggle-right"></i>Operating mode</h3></div><div class="panel-body stack" style="gap:10px">
        <div class="seg" id="mode"><button data-v="live"><i data-lucide="cable"></i>Live hardware</button><button data-v="simulation"><i data-lucide="flask-conical"></i>Simulation</button></div>
        <p class="small muted" style="margin:0"><b>Live hardware</b> shows only readings received from the ESP32; if the link drops, values are withdrawn and the last real timestamp is shown — simulated data is never substituted. <b>Simulation</b> generates labelled demonstration data for all seven simulation profiles.</p>
        <div class="fields"><div class="field"><label for="op">Operator name</label><input id="op" value="${esc(s.operator)}"></div>
          <div class="field"><label for="dl">Device label</label><input id="dl" value="${esc(s.device_label)}"></div></div>
        <div><button class="btn" id="save-id"><i data-lucide="save"></i>Save</button></div></div></section>

      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="usb"></i>Serial connection</h3><span class="meta" id="link-state"></span></div><div class="panel-body stack" style="gap:10px">
        <div class="fields"><div class="field"><label for="port">Serial port</label><div class="row tight"><select id="port" style="flex:1"></select><button class="btn btn-sm" id="refresh" title="Refresh ports"><i data-lucide="refresh-cw"></i></button></div></div>
          <div class="field"><label for="port-m">Or enter the port path</label><input id="port-m" placeholder="COM5 or /dev/ttyUSB0" value="${esc(s.serial.port || "")}"></div>
          <div class="field"><label for="baud">Baud rate</label><select id="baud">${[9600, 57600, 115200, 230400, 460800].map((b) => `<option ${b === s.serial.baud ? "selected" : ""}>${b}</option>`).join("")}</select></div></div>
        <label class="check"><input type="checkbox" id="auto" ${s.serial.auto_reconnect ? "checked" : ""}>Reconnect automatically if the link drops</label>
        <div class="row"><button class="btn btn-primary" id="connect"><i data-lucide="plug"></i>Connect</button><button class="btn" id="disconnect"><i data-lucide="unplug"></i>Disconnect</button></div>
        <div class="kv" id="health"></div></div></section>

      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="terminal"></i>Serial console</h3><span class="meta">Diagnostic commands only</span></div><div class="panel-body stack" style="gap:10px">
        <div class="row tight">${["PING", "GET_INFO", "READ_SENSOR", "STOP_TEST"].map((c) => `<button class="btn btn-sm" data-cmd="${c}">${c}</button>`).join("")}
          <span class="spacer"></span><button class="btn btn-sm btn-ghost" id="clear-log">Clear</button></div>
        <div class="console" id="console" aria-live="off"></div>
        <p class="tiny muted" style="margin:0">Measurement lines are not echoed here to keep the console readable; they appear on Live Monitoring.</p></div></section>

      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="timer"></i>Acquisition</h3></div><div class="panel-body stack" style="gap:10px">
        <div class="fields"><div class="field"><label for="rate">Sample rate (Hz)</label><input id="rate" type="number" min="1" max="20" value="${s.acquisition.rate_hz}"><span class="hint">5–10 Hz recommended. Sent to the ESP32 with SET_RATE.</span></div>
          <div class="field"><label for="stale">Stale after (s)</label><input id="stale" type="number" min="0.5" max="10" step="0.5" value="${s.acquisition.stale_after_s}"><span class="hint">Readings older than this are no longer shown as live.</span></div>
          <div class="field"><label for="win">Statistics window (s)</label><input id="win" type="number" min="5" max="600" value="${s.acquisition.stats_window_s}"></div></div>
        <div><button class="btn" id="save-acq"><i data-lucide="save"></i>Save acquisition settings</button></div></div></section>

      <section class="panel c-12"><div class="panel-head"><h3><i data-lucide="cable"></i>Breadboard fixture map</h3><span class="meta">DUT pin → ESP32 GPIO, per package</span></div>
        <div class="panel-body grid">
          <div class="c-5"><div class="seg" id="pkg" style="margin-bottom:10px">${Object.keys(PKG_PROFILE).map((k) => `<button data-v="${k}">${k}</button>`).join("")}</div>
            <table class="t compact pin-table" id="map"></table>
            <p class="tiny muted">GPIO 0, 1, 3, 6–11, 21 and 22 are rejected (boot straps, flash, UART0, I2C). GPIO 34–39 are input-only. Leave a pin blank if it is not wired.</p></div>
          <div class="c-4" id="map-chip"></div>
          <div class="c-3 stack" style="gap:6px">
            <label class="check"><input type="checkbox" id="wired" ${s.fixture.wiring_verified ? "checked" : ""}>Wiring matches this map (verified with a multimeter)</label>
            <label class="check"><input type="checkbox" id="lvl" ${s.fixture.level_protection_5v ? "checked" : ""}>5 V tests: level protection fitted on every line into the ESP32 (dividers or series resistance)</label>
            <label class="check"><input type="checkbox" id="adc" ${s.fixture.adc_sense_enabled ? "checked" : ""}>Read DUT output levels with the ESP32 ADC (ADC-capable GPIOs only)</label>
            <div class="callout warn"><i data-lucide="shield-alert"></i><div class="small">Fit a 1 kΩ series resistor on every DUT line. It limits current if a wrong profile drives an output pin.</div></div>
            <button class="btn btn-primary" id="save-fx"><i data-lucide="save"></i>Save fixture</button></div></div></section>

      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="spline"></i>Pin I-V probe (optional)</h3></div><div class="panel-body stack" style="gap:10px">
        <p class="small muted" style="margin:0">ESP32 DAC → series resistor → probe on the DUT pin. ADC reads both ends; current = (V<sub>src</sub> − V<sub>pin</sub>) / R. The DUT must be unpowered (VCC tied to GND); the sweep stops at 3.2 V.</p>
        <label class="check"><input type="checkbox" id="pr-en" ${s.probe.enabled ? "checked" : ""}>Probe circuit fitted and checked</label>
        <div class="fields"><div class="field"><label for="pr-r">Series resistor (Ω)</label><input id="pr-r" type="number" min="1000" value="${s.probe.r_ohm}"></div>
          <div class="field"><label for="pr-d">DAC GPIO</label><select id="pr-d">${[25, 26].map((g) => `<option ${g === s.probe.dac_pin ? "selected" : ""}>${g}</option>`).join("")}</select></div>
          <div class="field"><label for="pr-n">Pin-node ADC GPIO</label><input id="pr-n" type="number" value="${s.probe.node_adc_pin}"></div>
          <div class="field"><label for="pr-s">Source-node ADC GPIO</label><input id="pr-s" type="number" value="${s.probe.src_adc_pin}"></div></div>
        <div><button class="btn" id="save-pr"><i data-lucide="save"></i>Save probe settings</button></div></div></section>

      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="hard-drive"></i>Data</h3></div><div class="panel-body stack" style="gap:10px">
        <div class="kv"><div>Software</div><div>IC VISION ${esc(status.app.version)}</div><div>Reference dataset</div><div class="num">${esc(status.app.reference_dataset_version)}</div>
          <div>OCR engine</div><div>${status.ocr.available ? esc(status.ocr.engine) : `<span style="color:var(--fail)">${esc(status.ocr.reason)}</span><div class="tiny muted">${esc(status.ocr.install)}</div>`}</div>
          <div>Measurement session</div><div class="num">${esc(status.device.session_id)}</div></div>
        <div class="row"><button class="btn" id="restore"><i data-lucide="rotate-ccw"></i>Restore built-in reference records</button>
          <a class="btn btn-ghost" href="/api/acq/export.csv?scope=session"><i data-lucide="download"></i>Session measurements CSV</a></div></div></section>
    </div>`;

  // ---------------------------------------------------------- mode
  const paintMode = () => $$("#mode button", view).forEach((b) => b.classList.toggle("on", b.dataset.v === store.mode));
  $("#mode", view).addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || b.dataset.v === store.mode) return;
    try { await api.post("/api/mode", { mode: b.dataset.v }); } catch (err) { toast(err.message, "error"); }
  });
  $("#save-id", view).onclick = () => save({ operator: $("#op", view).value, device_label: $("#dl", view).value });
  async function save(patch, msg = "Settings saved") {
    try { s = await api.put("/api/settings", patch); toast(msg, "ok"); } catch (e) { toast(e.message, "error"); }
  }

  // ---------------------------------------------------------- serial
  async function loadPorts() {
    try {
      const j = await api.getFull("/api/serial/ports");
      const ports = j.data;
      $("#port", view).innerHTML = (ports.length ? ports.map((p) => `<option value="${esc(p.device)}" ${p.device === s.serial.port ? "selected" : ""}>${esc(p.device)} — ${esc(p.description)}</option>`).join("")
        : `<option value="">No serial ports found</option>`);
      if (!j.pyserial) toast("pyserial is not installed — run: pip install pyserial", "error");
    } catch (e) { toast(e.message, "error"); }
  }
  function paintHealth() {
    const d = store.device;
    const sim = store.isSim();
    $("#link-state", view).innerHTML = sim ? `<span class="badge b-warn">Simulation mode — serial link ignored</span>`
      : `<span class="badge ${d.state === "streaming" ? "b-pass" : d.state === "connected" || d.state === "reconnecting" ? "b-warn" : "b-fail"}">${esc(d.state || "disconnected")}</span>`;
    const ser = (status && status.serial) || {};
    $("#health", view).innerHTML = `
      <div>State</div><div>${esc(sim ? "simulation (serial ignored)" : d.state)}${d.message ? `<div class="tiny muted">${esc(d.message)}</div>` : ""}</div>
      <div>Port</div><div class="num">${esc(ser.port || d.port || "—")} ${ser.baud ? "@ " + ser.baud : ""}</div>
      <div>Device ID · firmware</div><div class="num">${esc((ser.info || {}).device_id || (!sim && d.device_id) || "—")} · ${esc((ser.info || {}).firmware || (!sim && d.firmware) || "—")}</div>
      <div>Sensor · shunt</div><div class="num">${esc((ser.info || {}).sensor || (!sim && d.sensor) || "—")} · ${(ser.info || {}).shunt_ohm ? esc(ser.info.shunt_ohm) + " Ω" : "—"} · resolution ${(ser.info || {}).resolution_ma ? ser.info.resolution_ma + " mA" : "—"}</div>
      <div>Receive rate</div><div class="num">${sim ? "—" : num(d.rx_rate_hz || 0, 1) + " Hz"}</div>
      <div>Invalid lines</div><div class="num">${sim ? "—" : (d.invalid_lines || 0)}</div>
      <div>Last real sample</div><div class="num">${!sim && d.last_sample_ms ? `${timeOf(d.last_sample_ms, true)} (${ago(d.last_sample_ms)})` : "—"}</div>`;
  }
  $("#refresh", view).onclick = loadPorts;
  $("#connect", view).onclick = async () => {
    const port = $("#port-m", view).value.trim() || $("#port", view).value;
    if (!port) { toast("Select or enter a serial port", "error"); return; }
    try {
      await api.post("/api/serial/connect", { port, baud: +$("#baud", view).value, auto_reconnect: $("#auto", view).checked });
      status = await api.get("/api/status");
      toast(`Opened ${port}${store.isSim() ? " — switch to Live hardware mode to use it" : ""}`, "ok");
      paintHealth();
    } catch (e) { toast(e.message, "error"); }
  };
  $("#disconnect", view).onclick = async () => { await api.post("/api/serial/disconnect"); status = await api.get("/api/status"); paintHealth(); };
  $$("[data-cmd]", view).forEach((b) => b.onclick = async () => {
    try { await api.post("/api/serial/command", { command: b.dataset.cmd }); } catch (e) { toast(e.message, "error"); }
  });
  const con = $("#console", view);
  const addLog = (e) => {
    const line = document.createElement("div");
    line.className = e.kind;
    line.innerHTML = `<span class="ts">${timeOf(e.t, true)}</span>${e.kind === "tx" ? "→ " : e.kind === "rx" ? "← " : ""}${esc(e.text)}`;
    con.appendChild(line);
    while (con.childNodes.length > 400) con.removeChild(con.firstChild);
    con.scrollTop = con.scrollHeight;
  };
  try { (await api.get("/api/serial/log")).forEach(addLog); } catch (e) { /* ignore */ }
  $("#clear-log", view).onclick = () => { con.innerHTML = ""; };

  // ---------------------------------------------------------- acquisition / probe / data
  $("#save-acq", view).onclick = () => save({ acquisition: { rate_hz: +$("#rate", view).value, stale_after_s: +$("#stale", view).value, stats_window_s: +$("#win", view).value } }, "Acquisition settings saved");
  $("#save-pr", view).onclick = () => save({ probe: { enabled: $("#pr-en", view).checked, r_ohm: +$("#pr-r", view).value, dac_pin: +$("#pr-d", view).value,
    node_adc_pin: +$("#pr-n", view).value, src_adc_pin: +$("#pr-s", view).value } }, "Probe settings saved");
  $("#restore", view).onclick = async () => {
    if (!(await confirmDialog("Restore built-in references?", "Built-in manufacturer and reference records are reset to the shipped dataset. Records you added are kept.", "Restore"))) return;
    await api.post("/api/references/restore"); toast("Built-in records restored", "ok");
  };

  // ---------------------------------------------------------- fixture
  function paintFixture() {
    $$("#pkg button", view).forEach((b) => b.classList.toggle("on", b.dataset.v === st.pkg));
    const p = profiles.find((x) => x.id === PKG_PROFILE[st.pkg]);
    const m = s.fixture.map[st.pkg] || {};
    const pins = st.pkg === "DIP-8" ? [...p.pins.filter((x) => ["output", "input"].includes(x.role)).map((x) => [String(x.pin), x.name]), ["3s", "IN1+ sense (ADC)"]]
      : st.pkg === "DIP-28" ? [["19", "PB5 heartbeat"], ["3", "PD1/TXD"]]
        : st.pkg === "MODULE" ? [["TXD0", "TXD0"]]
          : p.pins.filter((x) => x.role !== "power" && x.role !== "ground").map((x) => [String(x.pin), `${x.name} (74HC02) / ${(profiles.find((q) => q.id === "nand-74hc00-dip14").pins.find((q) => q.pin === x.pin) || {}).name} (74HC00)`]);
    $("#map", view).innerHTML = `<thead><tr><th>DUT pin</th><th>Signal</th><th>ESP32 GPIO</th></tr></thead><tbody>${pins.map(([k, name]) =>
      `<tr><td>${esc(k)}</td><td class="small">${esc(name)}</td><td><input data-pin="${esc(k)}" type="number" min="0" max="39" value="${m[k] ?? ""}" style="width:90px;min-height:28px;padding:3px 6px"></td></tr>`).join("")}</tbody>`;
    $("#map-chip", view).innerHTML = chipSVG(p, { marking: p.base_part, fixture: m });
  }
  $("#pkg", view).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.pkg = b.dataset.v; paintFixture(); });
  $("#save-fx", view).onclick = async () => {
    const m = {};
    $$("#map [data-pin]", view).forEach((i) => { m[i.dataset.pin] = i.value === "" ? null : +i.value; });
    const map = { ...s.fixture.map, [st.pkg]: m };
    await save({ fixture: { map, wiring_verified: $("#wired", view).checked, level_protection_5v: $("#lvl", view).checked, adc_sense_enabled: $("#adc", view).checked } }, "Fixture saved");
    paintFixture();
  };

  const offs = [store.on("device", () => { paintMode(); paintHealth(); }), store.on("serial_log", addLog), store.on("mode", () => { paintMode(); paintHealth(); })];
  paintMode(); paintHealth(); paintFixture(); loadPorts();
  return { destroy() { offs.forEach((f) => f()); } };
}
