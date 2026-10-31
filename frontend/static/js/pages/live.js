import { api } from "../api.js";
import { store, loadProfiles, loadSimProfiles } from "../store.js";
import { $, $$, esc, icons, toast, num, autoNum, timeOf, ago, sourceTag, statusBadge, chipSVG, roleLegend } from "../ui.js";
import { lineChart, COLORS, setSim } from "../charts.js";

const srcNow = () => (store.isSim() ? "simulated" : "live");

export async function render(view) {
  const [profiles, sims] = await Promise.all([loadProfiles(), loadSimProfiles()]);
  const st = { win: 60, paused: false, tab: "dc", t0: null, charts: {}, funcProfile: null, lastIns: null, ivIns: null, runStart: null,
    activeId: null, funcRows: [] };
  const status = await api.get("/api/status");
  st.activeId = status.active_inspection;
  const cur = sims.find((s) => s.id === store.device.sim_profile);
  st.funcProfile = (cur && cur.component_profile_id) || (profiles.find((p) => p.physical_demo) || profiles[0]).id;

  view.innerHTML = `
    <div class="page-head"><div><h2>Live monitoring</h2><p>Continuous supply-side measurements from the INA219/INA226 via the ESP32, or the simulator. Graphs update without reloading.</p></div>
      <div class="actions">
        <select id="win" style="width:auto" aria-label="Graph window">${[30, 60, 120, 300].map((w) => `<option value="${w}" ${w === st.win ? "selected" : ""}>Last ${w} s</option>`).join("")}</select>
        <button class="btn" id="pause"><i data-lucide="pause"></i><span>Pause</span></button>
        <button class="btn" id="clear"><i data-lucide="eraser"></i>Clear graph</button>
        <button class="btn" id="reset"><i data-lucide="rotate-ccw"></i>Reset</button>
        <a class="btn" id="csv" href="#"><i data-lucide="download"></i>Export CSV</a>
        <label class="check" style="padding:0 4px;align-items:center"><input type="checkbox" id="logging">Log to CSV file</label>
      </div></div>
    <div id="simbar"></div>
    <div class="readouts" id="cards" style="margin-bottom:16px"></div>
    <div class="grid">
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="trending-up"></i>Supply current</h3><span class="meta" id="tag-i"></span></div>
        <div class="panel-body"><div class="chart-box tall"><canvas id="ch-i"></canvas><div class="chart-overlay" id="ov-i" hidden></div></div></div></section>
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="zap"></i>Supply voltage</h3><span class="meta" id="tag-v"></span></div>
        <div class="panel-body"><div class="chart-box tall"><canvas id="ch-v"></canvas><div class="chart-overlay" id="ov-v" hidden></div></div></div></section>
      <section class="panel c-12"><div class="tabs" id="tabs">
          <button data-t="dc"><i data-lucide="table-2"></i>DC parameters</button>
          <button data-t="iv"><i data-lucide="spline"></i>I-V characteristics</button>
          <button data-t="stab"><i data-lucide="waves"></i>Current stability</button>
          <button data-t="func"><i data-lucide="binary"></i>Functional testing</button></div>
        <div class="panel-body" id="tab-body"></div></section>
    </div>`;

  const xTicks = (v) => (st.t0 ? new Date(st.t0 + v * 1000).toLocaleTimeString([], { hour12: false }) : v);
  st.charts.i = lineChart($("#ch-i", view), { datasets: [{ label: "Current", color: COLORS.current, fillTo: "origin", fill: "rgba(22,139,255,0.07)" }], yTitle: "mA", sim: store.isSim(), xTicks });
  st.charts.v = lineChart($("#ch-v", view), { datasets: [{ label: "Voltage", color: COLORS.voltage }], yTitle: "V", sim: store.isSim(), xTicks });

  // ---------------------------------------------------------- data
  async function loadWindow() {
    try {
      const j = await api.getFull(`/api/acq/window?seconds=${st.win}`);
      st.t0 = j.data.length ? j.data[0].t : Date.now();
      for (const k of ["i", "v"]) st.charts[k].data.datasets[0].data = j.data.filter((s) => s.valid).map((s) => ({ x: (s.t - st.t0) / 1000, y: s[k] }));
      store.stats = j.stats;
      rescale(); update(); paintCards();
    } catch (e) { toast(e.message, "error"); }
  }
  function rescale() {
    for (const k of ["i", "v"]) {
      const d = st.charts[k].data.datasets[0].data;
      const last = d.length ? d[d.length - 1].x : 0;
      st.charts[k].options.scales.x.min = Math.max(0, last - st.win);
      st.charts[k].options.scales.x.max = Math.max(last, st.win * 0.2);
      setSim(st.charts[k], store.isSim());
    }
  }
  function update() { for (const k of ["i", "v"]) st.charts[k].update("none"); if (st.tab === "stab") paintStabChart(); }

  function onMeasurement(d) {
    const s = d.sample;
    if (!st.paused && s.valid) {
      if (!st.t0) st.t0 = s.t;
      for (const k of ["i", "v"]) {
        const arr = st.charts[k].data.datasets[0].data;
        arr.push({ x: (s.t - st.t0) / 1000, y: s[k] });
        while (arr.length && arr[0].x < (s.t - st.t0) / 1000 - st.win - 5) arr.shift();
      }
      rescale(); update();
    }
    paintCards();
    if (st.tab === "dc") paintDC();
  }

  // ---------------------------------------------------------- cards
  function paintCards() {
    const s = store.latest, stats = store.stats || {};
    const d = store.device;
    const sim = store.isSim();
    const live = store.isStreaming();
    const stale = !live;
    const staleTxt = sim ? "Simulator paused" : d.last_sample_ms ? `No signal — last ${timeOf(d.last_sample_ms)}` : "No signal";
    const tag = s && live ? sourceTag(s.src) : "";
    const card = (label, icon, val, unit, sub, extra = "") => `<div class="readout ${stale ? "stale" : ""} ${sim ? "sim" : ""}" data-stale="${esc(staleTxt)}">
      <div class="r-label"><i data-lucide="${icon}"></i>${label}</div><span class="r-tag">${tag}</span>
      <div class="r-value">${stale ? "—" : val}<small>${unit}</small></div><div class="r-sub">${sub}</div>${extra}</div>`;
    const rng = (o, dd) => o ? `<span>min ${num(o.min, dd)}</span><span>avg ${num(o.mean, dd)}</span><span>max ${num(o.max, dd)}</span>` : "<span>—</span>";
    const cv = stats.cv;
    const sd = stats.i ? stats.i.std : null;
    const mean = stats.i ? Math.abs(stats.i.mean) : 0;
    const stab = sd == null ? "—" : sd <= Math.max(0.15, 0.03 * mean) ? "Stable" : sd <= Math.max(0.4, 0.1 * mean) ? "Elevated" : "Unstable";
    const runSecs = st.runStart ? (Date.now() - st.runStart) / 1000 : null;
    const acqSecs = stats.first_t ? (stats.last_t - stats.first_t) / 1000 : null;
    $("#cards", view).innerHTML =
      card("Supply voltage", "zap", s ? num(s.v, 3) : "—", "V", rng(stats.v, 3)) +
      card("Supply current", "trending-up", s ? num(s.i, 3) : "—", "mA", rng(stats.i, 3)) +
      card("Power consumption", "flame", s ? num(s.p, 2) : "—", "mW", rng(stats.p, 2)) +
      card("Current stability", "waves", sd != null ? num(sd, 3) : "—", "mA σ", `<span>${stab}</span><span>CV ${cv != null && mean > 0.5 ? (cv * 100).toFixed(1) + " %" : "—"}</span><span>n ${stats.n || 0}</span>`) +
      `<div class="readout"><div class="r-label"><i data-lucide="timer"></i>Test duration</div>
        <div class="r-value">${runSecs != null ? runSecs.toFixed(0) : acqSecs != null ? acqSecs.toFixed(0) : "—"}<small>s</small></div>
        <div class="r-sub"><span>${runSecs != null ? `Inspection ${esc(st.activeId)}` : `Stats window ${stats.window_s || "—"} s`}</span></div></div>` +
      `<div class="readout"><div class="r-label"><i data-lucide="${sim ? "flask-conical" : "usb"}"></i>Device connection</div>
        <div class="r-value" style="font-size:19px;margin-top:10px"><span class="dot ${live ? (sim ? "sim" : "live") : "bad"}" style="display:inline-block;margin-right:8px;vertical-align:middle"></span>${esc(sim ? (d.state === "paused" ? "Paused" : "Simulating") : ({ streaming: "Streaming", connected: "Port open", stale: "Stale", reconnecting: "Reconnecting", connecting: "Connecting" }[d.state] || "Disconnected"))}</div>
        <div class="r-sub"><span>${sim ? "Simulated controller" : esc(d.port || "No port")}</span><span>${num(d.rx_rate_hz || 0, 1)} Hz</span><span>last ${d.last_sample_ms ? timeOf(d.last_sample_ms, true) : "—"}</span></div></div>`;
    $("#tag-i", view).innerHTML = tag; $("#tag-v", view).innerHTML = tag;
    for (const k of ["i", "v"]) {
      const ov = $(`#ov-${k}`, view);
      ov.hidden = live;
      if (!live) ov.innerHTML = sim ? `<div><b style="color:var(--sim)">Simulator paused</b>Resume to continue the simulated stream.</div>`
        : `<div><b>${d.state === "stale" ? "No data from hardware" : "Hardware not connected"}</b>${d.last_sample_ms ? `Last real sample ${timeOf(d.last_sample_ms, true)} (${ago(d.last_sample_ms)}). Older readings are not shown as live.` : "Connect the ESP32 in Device Settings."}</div>`;
    }
    icons($("#cards", view));
  }

  // ---------------------------------------------------------- sim controls
  function paintSimBar() {
    const bar = $("#simbar", view);
    if (!store.isSim()) { bar.innerHTML = ""; $("#pause span", view).textContent = st.paused ? "Resume display" : "Pause display"; return; }
    const d = store.device;
    bar.innerHTML = `<section class="panel" style="margin-bottom:16px"><div class="panel-body row">
      <span class="sim-tag">SIMULATOR</span>
      <div class="field" style="min-width:280px"><label for="sim-prof">Simulation profile</label><select id="sim-prof">${sims.map((s) => `<option value="${s.id}" ${s.id === d.sim_profile ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select></div>
      <div class="field"><label>Stream behaviour</label><div class="seg" id="sim-ov">${[["profile", "Profile default"], ["normal", "Normal"], ["abnormal", "Abnormal current"], ["unstable", "Unstable current"]]
        .map(([v, l]) => `<button data-v="${v}" class="${(d.sim_override || "profile") === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <span class="spacer"></span><span class="small muted" style="max-width:420px">${esc((sims.find((s) => s.id === d.sim_profile) || {}).description || "")}</span></div></section>`;
    $("#pause span", view).textContent = d.state === "paused" ? "Resume" : "Pause";
    $("#sim-prof", view).onchange = async (e) => {
      try { await api.post("/api/sim/select", { profile_id: e.target.value }); clearGraph(); } catch (err) { toast(err.message, "error"); }
    };
    $("#sim-ov", view).addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      try { await api.post("/api/sim/select", { override: b.dataset.v }); paintSimBar(); } catch (err) { toast(err.message, "error"); }
    });
  }

  function clearGraph() {
    st.t0 = null;
    for (const k of ["i", "v"]) st.charts[k].data.datasets[0].data = [];
    update();
  }

  // ---------------------------------------------------------- tabs
  function paintTabs() {
    $$("#tabs button", view).forEach((b) => b.classList.toggle("on", b.dataset.t === st.tab));
    if (st.charts.stab) { st.charts.stab.destroy(); st.charts.stab = null; }
    if (st.charts.iv) { st.charts.iv.destroy(); st.charts.iv = null; }
    ({ dc: paintDC, iv: paintIV, stab: paintStab, func: paintFunc })[st.tab]();
    icons($("#tab-body", view));
  }

  function paintDC() {
    const s = store.latest, stats = store.stats || {};
    const live = store.isStreaming();
    const row = (l, latest, o, dd, u) => `<tr><td>${l}</td><td class="num">${live && latest != null ? num(latest, dd) : "—"}</td><td class="num">${o ? num(o.min, dd) : "—"}</td>
      <td class="num">${o ? num(o.mean, dd) : "—"}</td><td class="num">${o ? num(o.max, dd) : "—"}</td><td>${u}</td></tr>`;
    const vo = st.lastIns && st.lastIns.electrical ? st.lastIns.electrical.evaluations.filter((e) => e.kind === "output_high" || e.kind === "output_low") : [];
    $("#tab-body", view).innerHTML = `<div class="grid"><div class="c-7"><table class="t"><thead><tr><th>Parameter</th><th class="num">Latest</th><th class="num">Min</th><th class="num">Mean</th><th class="num">Max</th><th>Unit</th></tr></thead><tbody>
      ${row("Supply (bus) voltage", s && s.v, stats.v, 3, "V")}
      ${row("Shunt voltage", s && s.shunt_mv, null, 3, "mV")}
      ${row("Supply current", s && s.i, stats.i, 3, "mA")}
      ${row("Power", s && s.p, stats.p, 2, "mW")}</tbody></table>
      <p class="tiny muted">Window: last ${stats.window_s || "—"} s, ${stats.n || 0} valid samples, ${stats.rate_hz || "—"} samples/s. ${s ? sourceTag(s.src) : ""}</p></div>
      <div class="c-5 stack" style="gap:10px">
        <div><div class="label">Input / output voltage</div>${vo.length ? `<table class="t compact">${vo.map((e) => `<tr><td>${esc(e.label)}</td><td class="num">${esc(e.measured_text)}</td><td>${statusBadge(e.status)}</td></tr>`).join("")}</table>
          <div class="tiny muted">From inspection ${esc(st.lastIns.id)} ${sourceTag(st.lastIns.data_source)}</div>` : `<div class="small muted">Measured during a functional test when ADC sensing is enabled in Device Settings. No measured values yet.</div>`}</div>
        <div><div class="label">Leakage current</div><div class="small muted">Not supported by the current measurement circuit. The INA219/INA226 measures supply-side current only; pin leakage (nA range) needs a dedicated sense circuit.</div></div>
      </div></div>`;
  }

  async function paintIV() {
    const body = $("#tab-body", view);
    body.innerHTML = `<div class="loading"><i data-lucide="loader-2" class="spin"></i>Looking for I-V data…</div>`;
    icons(body);
    if (!st.ivIns) {
      try {
        const list = (await api.get("/api/inspections")).filter((r) => ["measured", "completed"].includes(r.status) && r.data_source === srcNow()).slice(0, 15);
        for (const r of list) { const ins = await api.get(`/api/inspections/${r.id}`); if (ins.iv && ins.iv.points && ins.iv.points.length) { st.ivIns = ins; break; } }
      } catch (e) { /* none */ }
    }
    if (st.tab !== "iv") return;
    const ins = st.ivIns;
    if (!ins) {
      body.innerHTML = `<div class="empty"><i data-lucide="spline"></i><b>No I-V sweep recorded yet</b>Enable “Pin I-V signature” when configuring an inspection.
        ${store.isSim() ? "Simulated logic-IC profiles include an I-V model." : "Live sweeps need the probe circuit (DAC → 10 kΩ → pin, ADC on both ends) enabled in Device Settings, with the DUT unpowered."}</div>`;
      icons(body); return;
    }
    const ref = ins.iv.reference;
    body.innerHTML = `<div class="grid"><div class="c-8"><div class="chart-box tall"><canvas id="ch-ivc"></canvas></div></div>
      <div class="c-4"><div class="kv"><div>Inspection</div><div><a href="#/inspection/${esc(ins.id)}">${esc(ins.id)}</a> ${sourceTag(ins.data_source)}</div>
        <div>Component</div><div>${esc(ins.component.name)}</div><div>Pin</div><div class="num">${esc(ins.iv.pin)}</div>
        <div>Probe resistor</div><div class="num">${esc(ins.iv.r_ohm)} Ω</div><div>Points</div><div class="num">${ins.iv.points.length}</div>
        <div>Reference curve</div><div>${ref ? esc(ref.source) : `<span class="empty-val">None — no reference profile; none fabricated</span>`}</div></div>
        <p class="tiny muted">Pin signature with the DUT unpowered: current through the input protection structure as the probe voltage rises.</p></div></div>`;
    const ds = [{ label: "Measured", color: COLORS.current, data: ins.iv.points.map((q) => ({ x: q.v, y: q.i_ua })), points: 2 }];
    if (ref) ds.push({ label: "Reference", color: COLORS.ref, dashed: true, data: ref.points.map((q) => ({ x: q.v, y: q.i_ua })) });
    st.charts.iv = lineChart($("#ch-ivc", view), { datasets: ds, xTitle: "Pin voltage (V)", yTitle: "Current (µA)", sim: ins.data_source === "simulated", legend: true });
    icons(body);
  }

  function paintStab() {
    const stats = store.stats || {};
    const i = stats.i;
    const mean = i ? Math.abs(i.mean) : 0;
    const label = !i ? "—" : i.std <= Math.max(0.15, 0.03 * mean) ? ["Stable", "b-pass"] : i.std <= Math.max(0.4, 0.1 * mean) ? ["Elevated variation", "b-warn"] : ["Unstable", "b-fail"];
    $("#tab-body", view).innerHTML = `<div class="grid"><div class="c-8"><div class="chart-box tall"><canvas id="ch-stab"></canvas></div>
      <div class="legend-inline" style="margin-top:6px"><span><i style="background:${COLORS.current}"></i>Current</span><span><i style="background:${COLORS.ref}"></i>Mean</span><span><i style="background:rgba(22,139,255,.25)"></i>±1σ band</span></div></div>
      <div class="c-4"><div class="kv">
        <div>Mean current</div><div class="num">${i ? num(i.mean, 3) + " mA" : "—"}</div>
        <div>Minimum</div><div class="num">${i ? num(i.min, 3) + " mA" : "—"}</div>
        <div>Maximum</div><div class="num">${i ? num(i.max, 3) + " mA" : "—"}</div>
        <div>Standard deviation</div><div class="num">${i ? num(i.std, 4) + " mA" : "—"}</div>
        <div>Coefficient of variation</div><div class="num">${stats.cv != null && mean > 0.5 ? (stats.cv * 100).toFixed(2) + " %" : "— (current near zero)"}</div>
        <div>Trend</div><div class="num">${stats.drift_ma_per_min != null ? `${num(stats.drift_ma_per_min, 3)} mA/min${stats.drift_significant ? "" : " (not significant)"}` : "—"}</div>
        <div>Samples</div><div class="num">${stats.n || 0} over ${stats.window_s || "—"} s</div>
        <div>Stability indication</div><div>${Array.isArray(label) ? `<span class="badge ${label[1]}">${label[0]}</span>` : label}</div></div>
        <p class="tiny muted">Indicative, using the default criterion (σ ≤ max(0.15 mA, 3 % of mean)). Inspections apply each profile's own criterion and drift test.</p></div></div>`;
    st.charts.stab = lineChart($("#ch-stab", view), { datasets: [
      { label: "+σ", color: "rgba(22,139,255,0)", fill: "rgba(22,139,255,0.12)", fillTo: "+1", width: 0, order: 2 },
      { label: "−σ", color: "rgba(22,139,255,0)", width: 0, order: 2 },
      { label: "Mean", color: COLORS.ref, dashed: true, width: 1.2, order: 1 },
      { label: "Current", color: COLORS.current, order: 0 }], yTitle: "mA", sim: store.isSim(), xTicks });
    paintStabChart();
  }
  function paintStabChart() {
    const c = st.charts.stab; if (!c) return;
    const d = st.charts.i.data.datasets[0].data;
    const sw = (store.stats && store.stats.window_s) || 30;
    const last = d.length ? d[d.length - 1].x : 0;
    const pts = d.filter((p) => p.x >= last - sw);
    const m = pts.length ? pts.reduce((a, p) => a + p.y, 0) / pts.length : 0;
    const sd = pts.length > 1 ? Math.sqrt(pts.reduce((a, p) => a + (p.y - m) ** 2, 0) / (pts.length - 1)) : 0;
    const x0 = pts.length ? pts[0].x : 0;
    c.data.datasets[0].data = [{ x: x0, y: m + sd }, { x: last, y: m + sd }];
    c.data.datasets[1].data = [{ x: x0, y: m - sd }, { x: last, y: m - sd }];
    c.data.datasets[2].data = [{ x: x0, y: m }, { x: last, y: m }];
    c.data.datasets[3].data = pts;
    c.options.scales.x.min = x0; c.options.scales.x.max = last;
    setSim(c, store.isSim());
    c.update("none");
  }

  async function paintFunc() {
    const p = profiles.find((x) => x.id === st.funcProfile);
    const ft = p && p.functional_test;
    let last = null;
    try {
      const list = (await api.get(`/api/inspections?profile_id=${encodeURIComponent(st.funcProfile)}`)).filter((r) => ["measured", "completed"].includes(r.status) && r.data_source === srcNow());
      if (list.length) last = await api.get(`/api/inspections/${list[0].id}`);
    } catch (e) { /* none */ }
    if (st.tab !== "func") return;
    const fn = last && last.functional;
    let table = "";
    if (!ft) table = `<div class="callout info"><i data-lucide="info"></i><div><b>Functional test not configured.</b> This profile has no functional test definition, so only supply measurements are available.</div></div>`;
    else if (ft.type === "logic_truth_table") {
      const obs = {};
      ((fn && fn.rows) || []).forEach((r) => { obs[`${r.gate}|${r.vector.join("")}`] = r; });
      st.funcRows.forEach((r) => { obs[`${r.gate}|${r.vector.join("")}`] = r; });
      table = `<table class="t compact truth"><thead><tr><th>Gate (pins)</th><th>A</th><th>B</th><th>Expected Y</th><th>Observed Y</th><th>Result</th></tr></thead><tbody>
        ${ft.gates.map((g) => ft.vectors.map((v, i) => { const o = obs[`${g.name}|${v.slice(0, 2).join("")}`];
          return `<tr><td>${i === 0 ? `${esc(g.name)} <span class="tiny muted">(${g.inputs.join(", ")} → ${g.output})</span>` : ""}</td><td class="num">${v[0]}</td><td class="num">${v[1]}</td><td class="num">${v[2]}</td>
          <td class="num ${o ? (o.pass ? "obs-pass" : "obs-fail") : ""}">${o ? o.observed : "—"}</td><td>${o ? statusBadge(o.pass ? "PASS" : "FAIL") : `<span class="tiny muted">not run</span>`}</td></tr>`; }).join("")).join("")}</tbody></table>`;
    } else if (ft.type === "mcu_firmware") {
      const r = (fn && fn.results) || {};
      table = `<table class="t compact"><thead><tr><th>Test</th><th>Definition</th><th>Last result</th></tr></thead><tbody>
        <tr><td>Heartbeat</td><td class="small">PB5 (pin ${ft.heartbeat.pin}) at ${ft.heartbeat.expected_hz} Hz ± ${ft.heartbeat.tolerance_pct} %</td><td class="num">${r.heartbeat ? `${r.heartbeat.freq_hz} Hz` : "—"}</td></tr>
        <tr><td>UART signature</td><td class="small">TXD (pin ${ft.uart_signature.pin}), ${ft.uart_signature.baud} baud, expect ${ft.uart_signature.expected}</td><td class="num">${r.uart_signature ? esc(r.uart_signature.line || "no line") : "—"}</td></tr></tbody></table>
        <p class="small muted">${esc(ft.description)} A generic GPIO toggle does not verify an arbitrary microcontroller; these tests rely on the IC VISION heartbeat firmware.</p>`;
    } else if (ft.type === "analog_buffer") {
      table = `<p class="small">${esc(ft.description)}</p><table class="t compact"><thead><tr><th class="num">Set</th><th class="num">VIN</th><th class="num">VOUT</th><th class="num">Error</th></tr></thead><tbody>
        ${((fn && fn.rows) || []).map((r) => `<tr><td class="num">${r.set_mv} mV</td><td class="num">${r.vin_mv} mV</td><td class="num">${r.vout_mv} mV</td><td class="num">${r.vout_mv - r.vin_mv} mV</td></tr>`).join("") || `<tr><td colspan="4" class="muted small">Not run yet</td></tr>`}</tbody></table>`;
    } else if (ft.type === "boot_log") {
      table = `<p class="small">${esc(ft.description)} Listening on ${esc(ft.uart.pin)} at ${ft.uart.baud} baud for “${esc(ft.uart.token)}”.</p>`;
    }
    $("#tab-body", view).innerHTML = `<div class="row" style="margin-bottom:12px"><div class="field" style="min-width:300px"><label for="fprof">Component profile</label>
      <select id="fprof">${profiles.map((x) => `<option value="${x.id}" ${x.id === st.funcProfile ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>
      <span class="small muted">${last ? `Observed values from inspection <a href="#/inspection/${esc(last.id)}">${esc(last.id)}</a> ${sourceTag(last.data_source)}` : "No inspection of this profile yet — observed values appear when a test runs."}</span></div>
      <div class="grid"><div class="c-8">${table}</div><div class="c-4">${p ? chipSVG(p, { marking: p.base_part }) + roleLegend() : ""}</div></div>`;
    $("#fprof", view).onchange = (e) => { st.funcProfile = e.target.value; st.funcRows = []; paintFunc(); };
    icons($("#tab-body", view));
  }

  // ---------------------------------------------------------- controls
  $("#win", view).onchange = (e) => { st.win = +e.target.value; loadWindow(); };
  $("#pause", view).onclick = async () => {
    if (store.isSim()) {
      const paused = store.device.state === "paused";
      try { await api.post("/api/sim/control", { action: paused ? "resume" : "pause" }); } catch (e) { toast(e.message, "error"); }
    } else {
      st.paused = !st.paused;
      $("#pause span", view).textContent = st.paused ? "Resume display" : "Pause display";
      toast(st.paused ? "Display paused — acquisition and logging continue." : "Display resumed.");
    }
  };
  $("#clear", view).onclick = clearGraph;
  $("#reset", view).onclick = async () => {
    try { await api.post(store.isSim() ? "/api/sim/control" : "/api/acq/reset", store.isSim() ? { action: "reset" } : {}); clearGraph(); toast("Statistics reset", "ok"); }
    catch (e) { toast(e.message, "error"); }
  };
  $("#csv", view).onclick = (e) => { e.preventDefault(); location.href = `/api/acq/export.csv?scope=window&seconds=${st.win}`; };
  $("#logging", view).checked = !!store.device.csv_logging;
  $("#logging", view).onchange = async (e) => {
    try { const r = await api.post("/api/acq/logging", { enabled: e.target.checked }); toast(r.enabled ? `Logging to ${r.path}` : "CSV logging stopped", "ok"); }
    catch (err) { toast(err.message, "error"); }
  };
  $("#tabs", view).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.tab = b.dataset.t; paintTabs(); });

  // latest inspection for DC output levels
  try {
    const list = (await api.get("/api/inspections")).filter((r) => ["measured", "completed"].includes(r.status) && r.data_source === srcNow());
    if (list.length) st.lastIns = await api.get(`/api/inspections/${list[0].id}`);
  } catch (e) { /* none */ }

  const offs = [
    store.on("measurement", onMeasurement),
    store.on("device", () => { paintCards(); paintSimBar(); }),
    store.on("acq_reset", () => { clearGraph(); }),
    store.on("mode", () => { st.lastIns = null; st.ivIns = null; st.funcRows = []; clearGraph(); loadWindow(); paintSimBar(); paintTabs(); }),
    store.on("inspection", (d) => { if (d.status === "running") { st.activeId = d.id; st.runStart = st.runStart || Date.now(); } else if (d.id === st.activeId) { st.activeId = null; st.runStart = null; } }),
    store.on("functional_row", (d) => { if (d.row && d.row.gate) { st.funcRows.push(d.row); if (st.tab === "func") paintFunc(); } }),
  ];
  paintSimBar(); paintTabs(); await loadWindow();
  return { destroy() { offs.forEach((f) => f()); Object.values(st.charts).forEach((c) => c && c.destroy()); } };
}
