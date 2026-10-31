import { api } from "../api.js";
import { store, loadProfiles, loadReferences, loadSimProfiles } from "../store.js";
import { $, esc, icons, toast, num, autoNum, timeOf, sourceTag, classBadge, scoreBarClass, categoryLabel } from "../ui.js";
import { ocrUpload, ocrSample, ocrRecord, imageRecord, identStatusBadge, candidatesHTML, profileOptions, categoryOptions } from "../shared.js";

export async function render(view) {
  const [profiles, refs, sims] = await Promise.all([loadProfiles(), loadReferences(), loadSimProfiles()]);
  const st = { ocr: null, image: null, refId: null, profileId: "", category: "", mode: "full", busy: false, lastIns: null, active: null };
  const status = await api.get("/api/status");
  st.active = status.active_inspection;
  try {
    const done = await api.get("/api/inspections?status=completed");
    if (done.length) st.lastIns = await api.get(`/api/inspections/${done[0].id}`);
  } catch (e) { /* none yet */ }

  view.innerHTML = `
  <div class="grid">
    <section class="panel c-8 accent"><div class="hero">
      <div>
        <div class="state" id="h-state"></div>
        <h2 id="h-title">READY FOR INSPECTION</h2>
        <p id="h-sub">Insert a semiconductor component into the test fixture and configure inspection parameters to begin.</p>
        <div class="flow" id="h-flow"></div>
      </div>
      <div class="mini-readouts" id="h-readouts" aria-live="off"></div>
    </div></section>

    <section class="panel c-4">
      <div class="panel-head"><h3><i data-lucide="gauge"></i>Trust score</h3><span class="meta" id="ts-meta"></span></div>
      <div class="panel-body" id="ts-body"></div>
    </section>

    <section class="panel c-5">
      <div class="panel-head"><h3><i data-lucide="scan"></i>Component scanning</h3>
        <span class="meta"><i data-lucide="camera-off"></i>No camera connected</span></div>
      <div class="panel-body">
        <div class="scan" id="scan"><span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
          <div id="scan-inner"></div></div>
        <div class="row" style="margin-top:12px">
          <label class="btn btn-primary"><i data-lucide="upload"></i>Upload component image<input type="file" accept="image/*" id="file" hidden></label>
          <span class="row tight" id="sample-wrap"></span>
          <button class="btn btn-ghost" id="manual-btn"><i data-lucide="list"></i>Select manually</button>
        </div>
        <p class="tiny muted" style="margin:10px 0 0">Camera capture will appear here when a camera module is added. Until then, upload a photo of the package top or select the component manually.</p>
      </div>
    </section>

    <section class="panel c-3">
      <div class="panel-head"><h3><i data-lucide="fingerprint"></i>Component identification</h3><span class="meta" id="id-badge"></span></div>
      <div class="panel-body" id="id-body"></div>
    </section>

    <section class="panel c-4">
      <div class="panel-head"><h3><i data-lucide="sliders-horizontal"></i>Test configuration</h3></div>
      <div class="panel-body stack" style="gap:12px">
        <div class="field"><label for="cat">Component category</label><select id="cat">${categoryOptions("")}</select></div>
        <div class="field"><label for="model">Component model</label><select id="model"></select></div>
        <div class="field"><label>Inspection mode</label>
          <div class="seg" id="mode">${[["full", "Full inspection"], ["optical", "Optical"], ["electrical", "Electrical fingerprinting"], ["manufacturer", "Manufacturer verification"]]
            .map(([v, l]) => `<button data-v="${v}" class="${v === "full" ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <button class="btn btn-primary btn-block start" id="start"><i data-lucide="play"></i>START INSPECTION</button>
        <div class="tiny muted" id="start-note"></div>
      </div>
    </section>

    <section class="panel c-4 module"><div class="panel-head"><h3><i data-lucide="scan-eye"></i>Optical inspection</h3></div>
      <div class="panel-body" id="m-opt"></div></section>
    <section class="panel c-4 module"><div class="panel-head"><h3><i data-lucide="zap"></i>Electrical fingerprinting</h3><span class="meta" id="m-el-tag"></span></div>
      <div class="panel-body" id="m-el"></div></section>
    <section class="panel c-4 module"><div class="panel-head"><h3><i data-lucide="database"></i>Manufacturer verification</h3></div>
      <div class="panel-body" id="m-mf"></div></section>
  </div>`;

  const profileById = (id) => profiles.find((p) => p.id === id);
  const refById = (id) => refs.find((r) => r.id === id);

  function renderSampleControl() {
    const wrap = $("#sample-wrap", view);
    if (!store.isSim()) { wrap.innerHTML = ""; return; }
    wrap.innerHTML = `<select id="sample-sel" style="width:auto;max-width:240px" aria-label="Simulation sample">${sims.map((s) =>
      `<option value="${s.id}" ${s.id === store.device.sim_profile ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
      <button class="btn" id="sample-btn"><i data-lucide="flask-conical"></i>Load sample</button>`;
    icons(wrap);
    $("#sample-btn", view).onclick = async () => {
      const sid = $("#sample-sel", view).value;
      await runOcr(async () => {
        await api.post("/api/sim/select", { profile_id: sid });
        return ocrSample(sid);
      });
    };
  }

  async function runOcr(fn) {
    st.busy = true;
    $("#scan-inner", view).innerHTML = `<div class="placeholder"><i data-lucide="loader-2" class="spin"></i><b>Processing image</b>OpenCV preprocessing and Tesseract OCR…</div>`;
    icons(view);
    try {
      const o = await fn();
      st.ocr = o;
      st.image = imageRecord(o);
      const ident = o.identification;
      const top = ident && ident.candidates && ident.candidates[0];
      if (o.sim_profile) {
        st.profileId = o.sim_profile.component_profile_id;
        st.refId = o.sim_profile.reference_id;
        if (top && ident.status !== "ambiguous") { st.refId = top.reference_id; st.profileId = top.profile_id || st.profileId; }
      } else if (top) {
        st.refId = top.reference_id;
        st.profileId = top.profile_id || st.profileId;
      }
      const p = profileById(st.profileId);
      if (p) st.category = p.category;
      if (!o.available) toast("OCR engine unavailable — select the component manually.", "error");
    } catch (e) { toast(e.message, "error"); }
    st.busy = false;
    paint();
  }

  function paintScan() {
    const inner = $("#scan-inner", view);
    if (st.image) {
      const o = st.ocr;
      inner.innerHTML = `<img src="/api/images/${esc(st.image.image_id)}" alt="Uploaded component">
        <span class="badge-pos">${o.synthetic ? `<span class="sim-tag">SYNTHETIC SAMPLE</span>` : `<span class="badge b-info">Uploaded</span>`}</span>`;
    } else {
      inner.innerHTML = `<div class="placeholder"><i data-lucide="scan-line"></i><b>Waiting for component identification</b>Drop a package photo here, upload one, or select the component manually.</div>`;
    }
  }

  function paintIdent() {
    const o = st.ocr;
    const p = profileById(st.profileId);
    const r = refById(st.refId);
    $("#id-badge", view).innerHTML = o ? identStatusBadge(o.identification) : (p ? `<span class="badge b-info">Manual</span>` : "");
    const val = (v) => v ? esc(v) : `<span class="empty-val">—</span>`;
    let html = `<div class="kv">
      <div>Part number</div><div class="num">${val(r ? r.part_number : (p && p.base_part))}</div>
      <div>Manufacturer</div><div>${val(r && r.manufacturer)}</div>
      <div>Category</div><div>${p ? esc(categoryLabel(p.category)) : val("")}</div>
      <div>Package type</div><div>${val(r ? r.package : p && p.package)}</div>
      <div>Batch / lot</div><div class="num">${val(o && o.identification && o.identification.date_codes[0] ? "Date code " + o.identification.date_codes[0].code : "")}</div>
      <div>Datasheet</div><div>${r && r.datasheet ? `<a href="${esc(r.datasheet.url)}" target="_blank" rel="noopener">${esc(r.datasheet.doc_id)}</a>` : val("")}</div></div>`;
    if (o) {
      html += `<div class="hr"></div><div class="small muted" style="margin-bottom:6px">OCR text ${o.mean_conf != null ? `· mean confidence ${num(o.mean_conf, 0)} (Tesseract)` : ""}</div>
        <div class="mono small" style="white-space:pre-wrap">${esc(o.text || (o.available ? "No legible text" : "OCR unavailable"))}</div>`;
      if (o.identification && o.identification.status === "ambiguous") {
        html += `<div class="hr"></div><div class="small" style="margin-bottom:6px">${esc(o.identification.message)}</div>${candidatesHTML(o.identification, st.refId)}`;
      }
    }
    $("#id-body", view).innerHTML = html;
    view.querySelectorAll("#id-body .cand").forEach((el) => el.addEventListener("click", () => {
      st.refId = el.dataset.ref; const ref = refById(st.refId);
      if (ref && ref.profile_id) st.profileId = ref.profile_id;
      paint();
    }));
  }

  function paintConfig() {
    $("#cat", view).value = st.category;
    $("#model", view).innerHTML = `<option value="">Select a component profile…</option>` + profileOptions(profiles, st.profileId, st.category);
    view.querySelectorAll("#mode button").forEach((b) => b.classList.toggle("on", b.dataset.v === st.mode));
    const live = !store.isSim();
    const blocked = live && store.device.state !== "streaming" && st.mode !== "optical" && st.mode !== "manufacturer";
    const btn = $("#start", view);
    btn.disabled = !st.profileId || !!st.active;
    $("#start-note", view).innerHTML = st.active
      ? `An inspection is running — <a href="#/inspection/${esc(st.active)}">open it</a>.`
      : !st.profileId ? "Identify or select a component to enable the inspection."
        : blocked ? "Live hardware is not streaming. You can still configure the inspection; electrical tests need the ESP32."
          : store.isSim() ? "Runs against the simulated controller. All values will be labelled SIMULATED." : "Runs on the connected ESP32 fixture.";
  }

  function paintHero() {
    const live = !store.isSim();
    const d = store.device;
    let state = `<i data-lucide="circle-check"></i>SYSTEM READY`, title = "READY FOR INSPECTION",
      sub = "Insert a semiconductor component into the test fixture and configure inspection parameters to begin.";
    if (st.active) { state = `<i data-lucide="loader-2" class="spin"></i>INSPECTION RUNNING`; title = "INSPECTION IN PROGRESS"; sub = `Inspection ${st.active} is running.`; }
    else if (live && d.state !== "streaming") { state = `<i data-lucide="unplug"></i>HARDWARE NOT STREAMING`; sub = "Connect the ESP32 in Device Settings to run electrical tests, or switch to Simulation mode."; }
    else if (!live) state = `<i data-lucide="flask-conical"></i>SIMULATION MODE`;
    $("#h-state", view).innerHTML = state;
    $("#h-title", view).textContent = title;
    $("#h-sub", view).textContent = sub;
    const s1 = !!(st.image || st.profileId), s2 = !!st.profileId;
    const steps = [["Insert component", s1], ["Configure inspection", s2], ["Run inspection", false]];
    const cur = !s1 ? 0 : !s2 ? 1 : 2;
    $("#h-flow", view).innerHTML = steps.map(([l, done], i) =>
      `<div class="st ${done ? "done" : ""} ${i === cur ? "cur" : ""}"><span class="n">${done ? "✓" : i + 1}</span>${l}</div>`).join("");
    icons($("#h-state", view));
  }

  function paintReadouts() {
    const s = store.latest;
    const ok = store.isStreaming() && s;
    const tag = s ? sourceTag(s.src) : "";
    const v = (x, u, d) => ok ? `${num(x, d)}<small>${u}</small>` : `—<small>${u}</small>`;
    $("#h-readouts", view).innerHTML = `
      <div class="mr"><div class="l">Supply voltage</div><div class="v">${v(s && s.v, "V", 3)}</div></div>
      <div class="mr"><div class="l">Supply current</div><div class="v">${v(s && s.i, "mA", 2)}</div></div>
      <div class="mr"><div class="l">Power</div><div class="v">${v(s && s.p, "mW", 2)}</div></div>
      <div class="mr" style="align-self:center">${ok ? tag : `<span class="badge b-muted">No signal</span>`}</div>`;
    $("#m-el-tag", view).innerHTML = ok ? tag : "";
    const stats = store.stats;
    $("#m-el", view).innerHTML = ok
      ? `<div class="m-status"><span class="dot ${store.isSim() ? "sim" : "live"}"></span>${store.isSim() ? "Receiving simulated readings" : "Receiving sensor readings"}</div>
         <div class="m-big num">${autoNum(s.i, "mA")} · ${autoNum(s.v, "V")}</div>
         <div class="tiny muted num">σ ${stats && stats.i ? num(stats.i.std, 3) : "—"} mA over ${stats ? stats.n : 0} samples · last ${timeOf(s.t, true)}</div>`
      : `<div class="m-status"><span class="dot"></span>Waiting for sensor readings</div>
         <div class="tiny muted" style="margin-top:6px">${store.isSim() ? "Simulator paused." : store.device.state === "stale" ? `Last real sample at ${timeOf(store.device.last_sample_ms)} — not shown as live.` : "Connect the ESP32 in Device Settings."}</div>`;
  }

  function paintModules() {
    const o = st.ocr;
    $("#m-opt", view).innerHTML = o
      ? `<div class="m-status">${o.available ? `<span class="dot ok"></span>OCR processed (${esc(o.engine || "")})` : `<span class="dot bad"></span>OCR engine unavailable`}</div>
         <div class="m-big num">${o.mean_conf != null ? num(o.mean_conf, 0) + " / 100" : "—"}</div>
         <div class="tiny muted">Engine-reported mean word confidence. Visual checks are assessed during the inspection.</div>`
      : `<div class="m-status"><span class="dot"></span>Waiting for image or input</div>`;
    const r = refById(st.refId);
    $("#m-mf", view).innerHTML = r
      ? `<div class="m-status"><span class="dot ok"></span>Reference record found</div>
         <div class="m-big num">${esc(r.part_number)}</div>
         <div class="tiny muted">${esc(r.manufacturer)} · ${esc(r.datasheet.doc_id)} · ${esc((r.verification || {}).status || "").replace(/_/g, " ")}</div>`
      : st.profileId || o ? `<div class="m-status"><span class="dot warn"></span>Reference unavailable</div><div class="tiny muted" style="margin-top:6px">This dimension will be inconclusive unless a record is selected.</div>`
        : `<div class="m-status"><span class="dot"></span>Waiting for component identification</div>`;
  }

  function paintScore() {
    const ins = st.lastIns;
    const rows = [["optical", "Visual score"], ["electrical", "Electrical score"], ["manufacturer", "Manufacturer score"]];
    if (!ins || !ins.scores) {
      $("#ts-meta", view).textContent = "No inspection yet";
      $("#ts-body", view).innerHTML = `<div class="score-rows">${rows.map(([, l]) => `<div class="score-row"><span class="s-name">${l}</span><span class="s-val num">--<small>/100</small></span><div class="bar"></div></div>`).join("")}
        <div class="score-row score-overall"><span class="s-name"><b>Overall trust score</b></span><span class="s-val num">--<small>/100</small></span></div></div>
        <p class="tiny muted" style="margin:10px 0 0">Scores appear only after an inspection has been conducted.</p>`;
      return;
    }
    const sc = ins.scores;
    const dims = Object.fromEntries(sc.dimensions.map((d) => [d.key, d]));
    $("#ts-meta", view).innerHTML = `${sourceTag(ins.data_source)} <a href="#/inspection/${esc(ins.id)}">${esc(ins.id)}</a>`;
    const inc = sc.classification_key === "inconclusive";
    $("#ts-body", view).innerHTML = `<div class="score-rows">${rows.map(([k, l]) => {
      const d = dims[k]; const s = d && d.score;
      return `<div class="score-row"><span class="s-name">${l}</span><span class="s-val num">${s != null ? num(s, 1) : "--"}<small>/100</small></span>
        <div class="bar ${scoreBarClass(s)}"><i style="width:${s || 0}%"></i></div></div>`; }).join("")}
      <div class="score-row score-overall"><span class="s-name"><b>Overall trust score</b></span><span class="s-val num">${sc.overall != null && !inc ? num(sc.overall, 1) : "--"}<small>/100</small></span></div></div>
      <div style="margin-top:10px">${classBadge(sc.classification, sc.classification_key)}</div>
      <p class="tiny muted" style="margin:8px 0 0">Last completed inspection · ${esc(ins.component.name)}</p>`;
  }

  function paint() {
    paintHero(); paintScan(); paintIdent(); paintConfig(); paintModules(); paintReadouts(); paintScore(); renderSampleControl();
    icons(view);
  }

  // events
  $("#file", view).addEventListener("change", (e) => { const f = e.target.files[0]; if (f) runOcr(() => ocrUpload(f)); e.target.value = ""; });
  const scan = $("#scan", view);
  scan.addEventListener("dragover", (e) => { e.preventDefault(); scan.classList.add("drag"); });
  scan.addEventListener("dragleave", () => scan.classList.remove("drag"));
  scan.addEventListener("drop", (e) => { e.preventDefault(); scan.classList.remove("drag"); const f = e.dataTransfer.files[0]; if (f) runOcr(() => ocrUpload(f)); });
  $("#manual-btn", view).onclick = () => $("#model", view).focus();
  $("#cat", view).onchange = (e) => { st.category = e.target.value; paintConfig(); };
  $("#model", view).onchange = (e) => {
    st.profileId = e.target.value;
    const p = profileById(st.profileId);
    if (p && (!st.refId || (refById(st.refId) || {}).profile_id !== p.id)) st.refId = p.default_reference_id || null;
    if (p) st.category = p.category;
    paint();
  };
  $("#mode", view).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.mode = b.dataset.v; paintConfig(); });
  $("#start", view).onclick = async () => {
    const btn = $("#start", view);
    btn.disabled = true;
    try {
      const ident = st.ocr && st.ocr.identification;
      const payload = {
        profile_id: st.profileId, reference_id: st.refId || null, mode: st.mode,
        sim_profile_id: st.image && st.image.sim_profile_id ? st.image.sim_profile_id : store.device.sim_profile,
        image: st.image, ocr: ocrRecord(st.ocr),
        component: { identification_method: st.ocr ? (st.ocr.synthetic ? "OCR on synthetic sample image" : "OCR on uploaded image") : "Manual selection",
          date_code: ident && ident.date_codes[0] ? ident.date_codes[0].code : "" },
        config: { mode: st.mode },
      };
      const ins = await api.post("/api/inspections", payload);
      const v = await api.get(`/api/inspections/${ins.id}/validate`);
      if (v.ok) {
        await api.post(`/api/inspections/${ins.id}/start`);
        toast(`Inspection ${ins.id} started`, "ok");
      } else {
        toast("Complete the inspection configuration to start.", "");
      }
      location.hash = `#/inspection/${ins.id}`;
    } catch (e) { toast(e.message, "error"); btn.disabled = false; }
  };

  const offs = [
    store.on("measurement", () => paintReadouts()),
    store.on("device", () => { paintHero(); paintReadouts(); paintConfig(); }),
    store.on("mode", () => { renderSampleControl(); paint(); }),
    store.on("inspection", (d) => { st.active = d.status === "running" ? d.id : (st.active === d.id ? null : st.active); paintHero(); paintConfig(); }),
  ];
  paint();
  return { destroy() { offs.forEach((f) => f()); } };
}
