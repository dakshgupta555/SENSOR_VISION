import { api } from "../api.js";
import { store, loadProfiles, loadReferences, loadSimProfiles } from "../store.js";
import { $, $$, esc, icons, toast, modal, confirmDialog, num, autoNum, timeOf, statusBadge, classBadge, sourceTag, provTag,
  chipSVG, roleLegend, scoreBarClass, categoryLabel } from "../ui.js";
import { ocrUpload, ocrSample, ocrRecord, imageRecord, identStatusBadge, candidatesHTML, wordsHTML, ocrUnavailableHTML,
  reidentify, categoryOptions } from "../shared.js";
import { lineChart, COLORS } from "../charts.js";

const STEPS = [
  ["component", "Insert / select component"], ["ocr", "OCR identification"], ["reference", "OEM reference lookup"],
  ["run", "Configure & electrical test"], ["analysis", "Optical & electrical analysis"], ["score", "Trust score"], ["report", "Inspection report"],
];
const NARRATION = [
  "The component goes into the breadboard fixture. Identify it from a photo of the package top, or pick its profile — each profile carries its own pin map, supply limits and test definition.",
  "OpenCV isolates the package body, boosts contrast and thresholds the marking; Tesseract reads it. The identification engine matches the text against the local OEM database and shows why each candidate matched.",
  "The matching OEM record supplies datasheet limits with provenance. Verified datasheet values, values derived from them, known-good baselines and simulated values are always kept apart.",
  "The controller streams INA219 supply readings while it runs the profile's functional test. Measured values appear beside their reference limits as they arrive.",
  "Each parameter is judged against an applicable reference. Missing or unsupported measurements are reported as such and are never counted as passes.",
  "S = 0.60·Se + 0.25·Sv + 0.15·Sm over the dimensions that produced evidence. Critical findings and missing electrical evidence override the score thresholds.",
  "The PDF is generated from the stored inspection record and states which values are measured, simulated, unavailable or unverified.",
];
const OPT_MANUAL = [["logo_consistency", "Logo consistency"], ["marking_placement", "Marking placement and font"], ["package_appearance", "Package appearance"],
  ["surface_irregularities", "Surface irregularities (resurfacing, coating)"], ["pin_alignment", "Pin alignment and lead finish"], ["physical_damage", "Visible physical damage"]];

function stepForStatus(s) {
  return { draft: 4, running: 4, error: 4, aborted: 4, measured: 5, completed: 6 }[s] || 4;
}

export async function render(view, params) {
  const [profiles, refs, sims] = await Promise.all([loadProfiles(true), loadReferences(), loadSimProfiles()]);
  const settings = (await api.get("/api/status")).settings;
  const S = { step: 1, ins: null, D: { ocr: null, image: null, text: "", ident: null, refId: null, profileId: null, simId: null, category: "" },
    validation: null, charts: [], liveChart: null, offs: [], stageTab: "body", optManual: {}, compare: null, report: null };
  const profileById = (id) => profiles.find((p) => p.id === id);
  const refById = (id) => refs.find((r) => r.id === id);

  if (params[0]) {
    const full = await api.getFull(`/api/inspections/${params[0]}`);
    S.ins = full.data;
    S.report = full.report;
    S.step = stepForStatus(S.ins.status);
    S.optManual = JSON.parse(JSON.stringify(S.ins.optical_manual || {}));
  } else {
    try {
      const h = JSON.parse(sessionStorage.getItem("icv.handoff") || "null");
      if (h) { Object.assign(S.D, h); sessionStorage.removeItem("icv.handoff"); S.step = h.ocr ? 2 : 1; }
    } catch (e) { /* ignore */ }
  }

  view.innerHTML = `
    <div class="page-head">
      <div><h2 id="ins-title">New inspection</h2><p id="ins-sub">Follow the seven steps from component identification to the screening report.</p></div>
      <div class="actions">
        <label class="check" style="padding:0;align-items:center"><input type="checkbox" id="guided" ${store.guided ? "checked" : ""}>Guided demo narration</label>
        <a class="btn" href="#/inspection" id="new-btn"><i data-lucide="plus"></i>New inspection</a>
      </div>
    </div>
    <div class="workspace">
      <nav class="panel stepper" id="stepper" aria-label="Inspection steps"></nav>
      <div id="content" class="stack"></div>
    </div>`;
  $("#guided", view).onchange = (e) => { store.guided = e.target.checked; try { localStorage.setItem("icv.guided", e.target.checked ? "1" : "0"); } catch (_) {} paint(); };

  // ------------------------------------------------------------ helpers
  const cur = () => S.ins || null;
  const profile = () => profileById(cur() ? cur().profile_id : S.D.profileId);
  const reference = () => refById(cur() ? cur().reference_id : S.D.refId);
  const ocrOf = () => (cur() ? cur().ocr : S.D.ocr);
  const imgOf = () => (cur() ? cur().image : S.D.image);
  const simulated = () => (cur() ? cur().data_source === "simulated" : store.isSim());

  function destroyCharts() { S.charts.forEach((c) => c && c.destroy()); S.charts = []; S.liveChart = null; }

  function maxStep() {
    if (!S.ins) return S.D.profileId ? 3 : (S.D.ocr ? 2 : 1);
    const st = S.ins.status;
    if (st === "completed") return 7;
    if (st === "measured") return 5;
    return 4;
  }

  function paintStepper() {
    const m = maxStep();
    $("#stepper", view).innerHTML = STEPS.map(([, label], i) => {
      const n = i + 1;
      const done = n < S.step || (S.ins && S.ins.status === "completed" && n <= 6) || (n < m && n !== S.step);
      return `<button data-step="${n}" class="${n === S.step ? "cur" : done ? "done" : ""}" ${n > m ? "disabled" : ""}>
        <span class="n">${done && n !== S.step ? "✓" : n}</span>${label}</button>`;
    }).join("") + (S.ins ? `<div class="sep">Inspection</div><div class="small" style="padding:4px 10px">
        <div class="num"><b>${esc(S.ins.id)}</b></div><div class="row tight" style="margin-top:4px">${sourceTag(S.ins.data_source)}
        <span class="badge b-muted">${esc(S.ins.status)}</span></div></div>` : "");
    $$("#stepper button[data-step]", view).forEach((b) => b.addEventListener("click", () => { if (!b.disabled) go(+b.dataset.step); }));
  }

  function go(n) { S.step = n; paint(); }

  function paint() {
    destroyCharts();
    const ins = S.ins;
    $("#ins-title", view).innerHTML = ins ? `Inspection <span class="num">${esc(ins.id)}</span>` : "New inspection";
    $("#ins-sub", view).innerHTML = ins ? `${esc(ins.component.name)} · created ${new Date(ins.created_at).toLocaleString()} ${sourceTag(ins.data_source)}`
      : "Follow the seven steps from component identification to the screening report.";
    $("#new-btn", view).hidden = !ins;
    paintStepper();
    const c = $("#content", view);
    const narr = store.guided ? `<div class="narration"><i data-lucide="presentation"></i><div><b>Step ${S.step} — ${STEPS[S.step - 1][1]}.</b> ${NARRATION[S.step - 1]}</div></div>` : "";
    c.innerHTML = narr + `<div id="step-body" class="stack"></div>`;
    const body = $("#step-body", view);
    [stepComponent, stepOcr, stepReference, stepRun, stepAnalysis, stepScore, stepReport][S.step - 1](body);
    icons(view);
  }

  async function refresh() {
    const full = await api.getFull(`/api/inspections/${S.ins.id}`);
    S.ins = full.data;
    S.report = full.report;
  }

  // ------------------------------------------------------------ step 1
  function stepComponent(body) {
    if (S.ins) {
      const p = profile();
      body.innerHTML = `<section class="panel"><div class="panel-head"><h3><i data-lucide="cpu"></i>Component</h3></div>
        <div class="panel-body grid"><div class="c-6">${chipSVG(p, { marking: p.base_part })}${roleLegend()}</div>
        <div class="c-6"><div class="kv"><div>Profile</div><div>${esc(p.name)}</div><div>Category</div><div>${esc(categoryLabel(p.category))}</div>
          <div>Package</div><div>${esc(p.package)}</div><div>Identification</div><div>${esc(S.ins.component.identification_method)}</div></div>
          <p class="small muted">The component is fixed for this inspection. Start a new inspection to test a different part.</p></div></div></section>`;
      return;
    }
    const D = S.D;
    body.innerHTML = `
      <section class="panel"><div class="panel-head"><h3><i data-lucide="image-plus"></i>Component image</h3><span class="meta">Optional — needed for OCR and optical checks</span></div>
        <div class="panel-body grid">
          <div class="c-7"><div class="scan" id="scan"><span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span><div id="scan-inner"></div></div></div>
          <div class="c-5 stack" style="gap:10px">
            <label class="btn btn-primary"><i data-lucide="upload"></i>Upload package photo<input type="file" accept="image/*" id="file" hidden></label>
            ${store.isSim() ? `<div class="field"><label for="sample">Synthetic sample (simulation)</label>
              <div class="row tight"><select id="sample" style="flex:1">${sims.map((s) => `<option value="${s.id}" ${s.id === (D.simId || store.device.sim_profile) ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
              <button class="btn" id="sample-btn"><i data-lucide="flask-conical"></i>Load</button></div>
              <span class="hint">Rendered package images, labelled SYNTHETIC. OCR still runs for real on them.</span></div>` : ""}
            <div class="callout info"><i data-lucide="camera"></i><div class="small">Camera capture is not connected yet. Photograph the package top straight-on under even light, fill the frame, and upload it.</div></div>
          </div></div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="cpu"></i>Or select the component profile</h3>
        <span class="meta"><select id="cat" style="width:auto">${categoryOptions(D.category)}</select>
        <button class="btn btn-sm" id="custom-btn"><i data-lucide="plus"></i>Custom component</button></span></div>
        <div class="panel-body"><div class="cards" id="cards"></div></div></section>
      <div class="row"><span class="spacer"></span><button class="btn btn-primary" id="next" ${D.profileId || D.ocr ? "" : "disabled"}>Continue<i data-lucide="arrow-right"></i></button></div>`;
    const paintScan = () => {
      $("#scan-inner", view).innerHTML = D.image
        ? `<img src="/api/images/${esc(D.image.image_id)}" alt="Component image"><span class="badge-pos">${D.image.synthetic ? `<span class="sim-tag">SYNTHETIC SAMPLE</span>` : `<span class="badge b-info">Uploaded</span>`}</span>`
        : `<div class="placeholder"><i data-lucide="scan-line"></i><b>Waiting for component identification</b>Drop a package photo here or use the controls.</div>`;
      icons($("#scan", view));
    };
    const paintCards = () => {
      $("#cards", view).innerHTML = profiles.filter((p) => !D.category || p.category === D.category).map((p) => `
        <button class="pcard ${p.id === D.profileId ? "on" : ""}" data-id="${esc(p.id)}">
          <div class="row tight"><span class="pc-name">${esc(p.short_name || p.name)}</span><span class="spacer"></span>${p.physical_demo ? `<span class="badge b-cyan">Physical demo</span>` : ""}${!p.builtin ? `<span class="badge b-muted">Custom</span>` : ""}</div>
          <div class="pc-meta">${esc(categoryLabel(p.category))} · ${esc(p.package)} · ${p.functional_test ? esc(p.functional_test.type.replace(/_/g, " ")) : "supply only"}</div>
        </button>`).join("");
      $$("#cards .pcard", view).forEach((b) => b.addEventListener("click", () => {
        D.profileId = b.dataset.id;
        const p = profileById(D.profileId);
        if (!D.refId || (refById(D.refId) || {}).profile_id !== p.id) D.refId = p.default_reference_id || null;
        paintCards(); $("#next", view).disabled = false;
      }));
    };
    const doOcr = async (fn) => {
      $("#scan-inner", view).innerHTML = `<div class="placeholder"><i data-lucide="loader-2" class="spin"></i><b>Running OCR</b>OpenCV preprocessing and Tesseract…</div>`;
      icons(view);
      try {
        const o = await fn();
        D.ocr = o; D.image = imageRecord(o); D.text = o.text || ""; D.ident = o.identification;
        if (o.sim_profile) { D.simId = o.sim_profile.id; }
        const top = o.identification && o.identification.candidates[0];
        if (top && o.identification.status !== "ambiguous") { D.refId = top.reference_id; D.profileId = top.profile_id || D.profileId; }
        else if (o.sim_profile) { D.profileId = o.sim_profile.component_profile_id; D.refId = o.identification.status === "ambiguous" ? null : o.sim_profile.reference_id; }
        go(2);
      } catch (e) { toast(e.message, "error"); paintScan(); }
    };
    paintScan(); paintCards();
    $("#file", view).onchange = (e) => { const f = e.target.files[0]; if (f) doOcr(() => ocrUpload(f)); };
    const scan = $("#scan", view);
    scan.addEventListener("dragover", (e) => { e.preventDefault(); scan.classList.add("drag"); });
    scan.addEventListener("dragleave", () => scan.classList.remove("drag"));
    scan.addEventListener("drop", (e) => { e.preventDefault(); scan.classList.remove("drag"); const f = e.dataTransfer.files[0]; if (f) doOcr(() => ocrUpload(f)); });
    if ($("#sample-btn", view)) $("#sample-btn", view).onclick = () => doOcr(async () => {
      const sid = $("#sample", view).value;
      await api.post("/api/sim/select", { profile_id: sid });
      return ocrSample(sid);
    });
    $("#cat", view).onchange = (e) => { D.category = e.target.value; paintCards(); };
    $("#custom-btn", view).onclick = customComponentModal;
    $("#next", view).onclick = () => go(D.ocr ? 2 : 3);
  }

  async function customComponentModal() {
    const r = await modal({
      title: "Add a custom component", wide: true,
      body: `<div class="fields">
        <div class="field"><label>Manufacturer</label><input id="cm-mfr"></div>
        <div class="field"><label>Part number *</label><input id="cm-pn" placeholder="e.g. CD4001BE"></div>
        <div class="field"><label>Package type</label><select id="cm-pkg"><option>DIP-8</option><option selected>DIP-14</option><option>DIP-16</option><option>DIP-20</option><option>DIP-28</option></select></div>
        <div class="field"><label>Component category</label><select id="cm-cat"><option value="logic_ic">Logic IC</option><option value="microcontroller">Microcontroller</option><option value="op_amp">Operational amplifier</option><option value="custom" selected>Other</option></select></div>
        <div class="field"><label>Supply voltage min (V)</label><input id="cm-vmin" type="number" step="0.1"></div>
        <div class="field"><label>Supply voltage max (V)</label><input id="cm-vmax" type="number" step="0.1"></div>
        <div class="field" style="grid-column:1/-1"><label>Datasheet URL</label><input id="cm-url" placeholder="https://…"></div>
        <div class="field" style="grid-column:1/-1"><label>Pin configuration</label><textarea id="cm-pins" placeholder="1:1A, 2:1B, 3:1Y, 7:GND, 14:VCC"></textarea>
          <span class="hint">pin:name pairs. VCC/VDD and GND/VSS are recognised as supply pins. Custom profiles start with supply measurements only.</span></div></div>`,
      actions: [{ label: "Cancel", value: null }, { label: "Add component", kind: "btn-primary", handler: async (root) => {
        const v = (id) => $(id, root).value.trim();
        const p = await api.post("/api/profiles", { manufacturer: v("#cm-mfr"), part_number: v("#cm-pn"), package: v("#cm-pkg"), category: v("#cm-cat"),
          supply_min_v: v("#cm-vmin"), supply_max_v: v("#cm-vmax"), datasheet_url: v("#cm-url"), pin_config: v("#cm-pins") });
        return p;
      } }],
    });
    if (r) {
      profiles.push(r);
      S.D.profileId = r.id; S.D.refId = null; S.D.category = r.category;
      toast(`${r.name} added to the component library`, "ok");
      paint();
    }
  }

  // ------------------------------------------------------------ step 2
  function stepOcr(body) {
    const o = ocrOf();
    const img = imgOf();
    if (!o || !img) {
      body.innerHTML = `<section class="panel"><div class="panel-body"><div class="empty"><i data-lucide="image-off"></i><b>No image for this inspection</b>
        Identification was manual. Optical checks other than the operator checklist will be reported as not assessed.</div>
        <div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Back</button><span class="spacer"></span><button class="btn btn-primary" id="next">Continue<i data-lucide="arrow-right"></i></button></div></div></section>`;
      $("#back", view).onclick = () => go(1); $("#next", view).onclick = () => go(3);
      return;
    }
    const D = S.D;
    const ident = S.ins ? (o.identification || {}) : D.ident;
    const locked = !!S.ins;
    const stages = [["original", "Original"], ["body", "Package body"], ["contrast", "Contrast (CLAHE)"], ["otsu", "Threshold (Otsu)"], ["adaptive", "Adaptive threshold"]];
    body.innerHTML = `
      <div class="grid">
        <section class="panel c-7"><div class="panel-head"><h3><i data-lucide="image"></i>Preprocessing</h3><span class="meta">${img.synthetic ? `<span class="sim-tag">SYNTHETIC SAMPLE</span>` : "Uploaded photo"}</span></div>
          <div class="panel-body"><div class="stage-tabs seg" id="stg">${stages.map(([k, l]) => `<button data-k="${k}" class="${k === S.stageTab ? "on" : ""}">${l}</button>`).join("")}</div>
          <div class="img-frame ${S.stageTab === "original" ? "" : "light"}"><img id="stg-img" src="/api/images/${esc(img.image_id)}/${S.stageTab}" alt="Preprocessing stage"></div>
          <p class="tiny muted" style="margin:8px 0 0">Selected OCR pass: ${esc(o.variant || "—")} image, page segmentation mode ${esc(o.psm || "—")}. ${o.attempts ? o.attempts.length : 0} passes evaluated.</p></div></section>
        <section class="panel c-5"><div class="panel-head"><h3><i data-lucide="type"></i>Extracted markings</h3><span class="meta">${esc(o.engine || "")}</span></div>
          <div class="panel-body stack" style="gap:12px">
            ${o.available === false ? ocrUnavailableHTML(o) : ""}
            <div class="field"><label for="ocr-text">Marking text ${locked ? "" : "(edit to correct OCR)"}</label>
              <textarea id="ocr-text" rows="4" ${locked ? "readonly" : ""}>${esc(locked ? (o.edited_text || o.text || "") : D.text)}</textarea></div>
            ${locked ? "" : `<div class="row"><button class="btn btn-sm" id="reid"><i data-lucide="refresh-cw"></i>Re-identify from text</button>
              <span class="tiny muted">Mean word confidence ${o.mean_conf != null ? num(o.mean_conf, 0) : "—"} (engine-reported)</span></div>`}
            <div><div class="label" style="margin-bottom:6px">Recognised words</div>${wordsHTML(o)}
              <div class="tiny muted" style="margin-top:6px">Struck-through words fell below confidence ${esc(o.min_word_conf || 25)} and were left out of the text.</div></div>
          </div></section>
        <section class="panel c-12"><div class="panel-head"><h3><i data-lucide="fingerprint"></i>Identification</h3><span class="meta">${identStatusBadge(ident)}</span></div>
          <div class="panel-body grid">
            <div class="c-7"><p class="small" style="margin-top:0">${esc(ident ? ident.message : "")}</p><div id="cands">${candidatesHTML(ident, locked ? S.ins.reference_id : D.refId)}</div></div>
            <div class="c-5"><div class="kv">
              <div>Manufacturer hints</div><div>${ident && ident.manufacturer_hints.length ? ident.manufacturer_hints.map((h) => `${esc(h.name)} <span class="tiny muted">(${esc(h.evidence)})</span>`).join("<br>") : `<span class="empty-val">None found</span>`}</div>
              <div>Date codes</div><div class="num">${ident && ident.date_codes.length ? ident.date_codes.map((d) => `${esc(d.code)} <span class="tiny muted">(${d.year} wk ${d.week})</span>`).join(", ") : `<span class="empty-val">None found</span>`}</div>
              <div>Profile</div><div>${profile() ? esc(profile().name) : `<span class="empty-val">Not selected</span>`}</div></div>
              ${locked ? "" : `<div class="field" style="margin-top:10px"><label for="man-prof">Manual correction — component profile</label>
                <select id="man-prof"><option value="">Select…</option>${profiles.map((p) => `<option value="${p.id}" ${p.id === D.profileId ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div>`}
            </div></div></section>
      </div>
      <div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Back</button><span class="spacer"></span>
        <button class="btn btn-primary" id="next" ${locked || D.profileId ? "" : "disabled"}>Continue to reference lookup<i data-lucide="arrow-right"></i></button></div>`;
    $$("#stg button", view).forEach((b) => b.addEventListener("click", () => { S.stageTab = b.dataset.k; paint(); }));
    $("#back", view).onclick = () => go(1);
    $("#next", view).onclick = () => go(3);
    if (locked) return;
    const bindCands = () => $$("#cands .cand", view).forEach((el) => el.addEventListener("click", () => {
      D.refId = el.dataset.ref; const r = refById(D.refId);
      if (r && r.profile_id) D.profileId = r.profile_id;
      paint();
    }));
    bindCands();
    $("#ocr-text", view).oninput = (e) => { D.text = e.target.value; };
    $("#reid", view).onclick = async () => {
      try {
        D.ident = await reidentify(D.text);
        D.ocr.identification = D.ident;
        const top = D.ident.candidates[0];
        if (top && D.ident.status !== "ambiguous") { D.refId = top.reference_id; D.profileId = top.profile_id || D.profileId; }
        paint();
      } catch (e) { toast(e.message, "error"); }
    };
    $("#man-prof", view).onchange = (e) => {
      D.profileId = e.target.value || null;
      const p = profileById(D.profileId);
      if (p && (!D.refId || (refById(D.refId) || {}).profile_id !== p.id)) D.refId = p.default_reference_id || null;
      paint();
    };
  }

  // ------------------------------------------------------------ step 3
  function stepReference(body) {
    const p = profile();
    const r = reference();
    const locked = S.ins && S.ins.status !== "draft";
    const options = refs.filter((x) => p && (x.profile_id === p.id || (p.base_part && x.base_part === p.base_part)));
    const specRows = r ? r.specs.map((s) => `<tr><td>${esc(s.label)}</td><td class="num">${esc(s.symbol || "")}</td>
        <td class="num">${s.text ? esc(s.text) : [s.min, s.typ, s.max].map((v) => v == null ? "—" : esc(v)).join(" / ") + (s.min_offset_from_vcc != null ? ` VCC${s.min_offset_from_vcc}` : "")}</td>
        <td>${esc(s.unit || "")}</td><td class="small">${esc(s.conditions || "")}</td><td>${provTag(s)}</td></tr>`).join("") : "";
    body.innerHTML = `
      <section class="panel"><div class="panel-head"><h3><i data-lucide="database"></i>OEM reference record</h3>
        <span class="meta"><select id="ref-sel" style="width:auto;min-width:240px" ${locked ? "disabled" : ""}>
          ${options.map((x) => `<option value="${x.id}" ${r && x.id === r.id ? "selected" : ""}>${esc(x.part_number)} — ${esc(x.manufacturer)}</option>`).join("")}
          <option value="" ${!r ? "selected" : ""}>No reference (reference unavailable)</option></select></span></div>
        <div class="panel-body">${r ? `
          <div class="grid">
            <div class="c-7"><div class="kv">
              <div>Manufacturer</div><div><b>${esc(r.manufacturer)}</b></div>
              <div>Component</div><div><span class="num">${esc(r.part_number)}</span> — ${esc(r.description)}</div>
              <div>Package</div><div>${esc(r.package)}</div>
              <div>Datasheet</div><div>${esc(r.datasheet.title)}<br><span class="small muted">${esc(r.datasheet.doc_id)} · ${esc(r.datasheet.revision)}</span></div>
              <div>Official URL</div><div class="small"><a href="${esc(r.datasheet.url)}" target="_blank" rel="noopener">${esc(r.datasheet.url)}</a></div>
              <div>Function</div><div class="small">${esc(r.functional_description)}</div>
              <div>Operating conditions</div><div class="small">${esc(r.test_conditions)}</div></div></div>
            <div class="c-5 stack" style="gap:10px">
              <a class="btn btn-primary" href="${esc(r.datasheet.url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>VIEW OFFICIAL DATASHEET</a>
              <div class="callout ${r.verification.status === "verified_datasheet" ? "ok" : "warn"}"><i data-lucide="${r.verification.status === "verified_datasheet" ? "badge-check" : "alert-circle"}"></i>
                <div><b>${esc(r.verification.status.replace(/_/g, " "))}</b> · checked ${esc(r.verification.checked_on || "")}<br><span class="small">${esc(r.verification.note || "")}</span></div></div>
              ${(r.legacy_types || []).map((l) => `<div class="callout info"><i data-lucide="history"></i><div class="small"><b>Legacy type ${esc(l.type)}</b> (${esc(l.package)}): ${esc(l.note)}</div></div>`).join("")}
            </div></div>
          <div class="table-wrap" style="margin-top:14px"><table class="t compact"><thead><tr><th>Specification</th><th>Symbol</th><th>Min / Typ / Max</th><th>Unit</th><th>Conditions</th><th>Source</th></tr></thead>
            <tbody>${specRows}</tbody></table></div>`
        : `<div class="callout warn"><i data-lucide="database-zap"></i><div><b>Reference unavailable.</b> No manufacturer record is linked, so manufacturer verification will be inconclusive and electrical values can only be compared against a known-good baseline. The component will not be classified as counterfeit for lacking a reference.
            <br><a href="#/manufacturers">Add a record in the Manufacturer Database</a></div></div>`}
        </div></section>
      <div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Back</button><span class="spacer"></span>
        <button class="btn btn-primary" id="next" ${p ? "" : "disabled"}>${S.ins ? "Continue" : "Create inspection and configure"}<i data-lucide="arrow-right"></i></button></div>`;
    $("#back", view).onclick = () => go(ocrOf() ? 2 : 1);
    $("#ref-sel", view).onchange = async (e) => {
      const v = e.target.value || null;
      if (S.ins) { S.ins = await api.put(`/api/inspections/${S.ins.id}`, { reference_id: v }); } else S.D.refId = v;
      paint();
    };
    $("#next", view).onclick = async () => {
      if (S.ins) return go(4);
      const D = S.D;
      try {
        const ident = D.ident || (D.ocr && D.ocr.identification);
        const ins = await api.post("/api/inspections", {
          profile_id: D.profileId, reference_id: D.refId || null, sim_profile_id: D.simId || store.device.sim_profile,
          image: D.image, ocr: ocrRecord(D.ocr ? { ...D.ocr, identification: ident } : null, D.text),
          component: { identification_method: D.ocr ? (D.image.synthetic ? "OCR on synthetic sample image" : "OCR on uploaded image") + (D.text !== D.ocr.text ? " + operator correction" : "") : "Manual selection",
            date_code: ident && ident.date_codes[0] ? ident.date_codes[0].code : "" },
        });
        S.ins = ins;
        S.optManual = JSON.parse(JSON.stringify(ins.optical_manual || {}));
        history.replaceState(null, "", `#/inspection/${ins.id}`);
        toast(`Inspection ${ins.id} created`, "ok");
        go(4);
      } catch (err) { toast(err.message, "error"); }
    };
  }

  // ------------------------------------------------------------ step 4
  function stepRun(body) {
    const ins = S.ins;
    if (!ins) { go(3); return; }
    if (ins.status === "running") return runView(body);
    const p = profile();
    const cfg = ins.config;
    const fixture = simulated() ? null : (settings.fixture.map || {})[p.package_key];
    const errBox = ins.status === "error" || ins.status === "aborted"
      ? `<div class="callout error"><i data-lucide="octagon-x"></i><div><b>Previous run ${esc(ins.status)}.</b> ${esc((ins.run.errors || []).join(" "))}</div></div>` : "";
    const rails = p.supply.allowed_rails || [];
    const ivSupported = p.evaluation.some((e) => e.kind === "iv_signature");
    body.innerHTML = `${errBox}
      <div class="grid">
        <section class="panel c-7"><div class="panel-head"><h3><i data-lucide="sliders-horizontal"></i>Inspection configuration</h3><span class="meta">${sourceTag(ins.data_source)}</span></div>
          <div class="panel-body stack" style="gap:14px">
            <div class="field"><label>Inspection mode</label><div class="seg" id="mode">${[["full", "Full inspection"], ["optical", "Optical"], ["electrical", "Electrical fingerprinting"], ["manufacturer", "Manufacturer verification"]]
              .map(([v, l]) => `<button data-v="${v}" class="${cfg.mode === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
            <div class="row">${[["optical", "Optical inspection"], ["electrical", "Electrical inspection"], ["manufacturer", "Manufacturer verification"]]
              .map(([k, l]) => `<label class="check"><input type="checkbox" data-dim="${k}" ${cfg[k] ? "checked" : ""}>${l}</label>`).join("")}</div>
            <div class="fields">
              <div class="field"><label>Supply rail</label><div class="seg" id="rail">${rails.map((r) => `<button data-v="${r}" class="${cfg.rail_v === r ? "on" : ""}">${r.toFixed(1)} V</button>`).join("")}</div>
                <span class="hint">Only rails inside the profile and datasheet limits are offered (${p.supply.min_v ?? "?"}–${p.supply.max_v ?? "?"} V). No arbitrary voltage is applied to the DUT.</span></div>
              ${p.clock_options ? `<div class="field"><label for="clock">Clock source</label><select id="clock">${p.clock_options.map((c) => `<option ${c === cfg.clock ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>` : ""}
              <div class="field"><label for="dur">Supply monitoring duration (s)</label><input id="dur" type="number" min="3" max="120" value="${esc(cfg.duration_s)}"></div>
              <div class="field"><label for="amb">Ambient temperature (°C, optional)</label><input id="amb" type="number" step="0.5" value="${esc((cfg.conditions || {}).ambient_c ?? "")}"></div>
            </div>
            <div class="row">
              <label class="check"><input type="checkbox" id="func" ${cfg.functional ? "checked" : ""} ${p.functional_test ? "" : "disabled"}>Functional test${p.functional_test ? ` (${esc(p.functional_test.type.replace(/_/g, " "))})` : " — not defined for this profile"}</label>
              <label class="check"><input type="checkbox" id="iv" ${cfg.iv ? "checked" : ""} ${ivSupported ? "" : "disabled"}>Pin I-V signature${ivSupported ? "" : " — not supported"}</label>
            </div>
            <div class="field"><label for="cnotes">Test-condition notes</label><input id="cnotes" value="${esc((cfg.conditions || {}).notes || "")}" placeholder="e.g. breadboard fixture, 1 kΩ series resistors"></div>
          </div></section>
        <section class="panel c-5"><div class="panel-head"><h3><i data-lucide="cpu"></i>${esc(p.package)} fixture map</h3><span class="meta">${simulated() ? "Simulated fixture" : "From Device Settings"}</span></div>
          <div class="panel-body">${chipSVG(p, { marking: p.base_part, fixture: fixture || (simulated() ? null : {}) })}${roleLegend()}</div></section>
        <section class="panel c-12"><div class="panel-head"><h3><i data-lucide="list-checks"></i>Pre-run checks</h3></div>
          <div class="panel-body grid">
            <div class="c-6">${simulated() ? `<div class="callout sim"><i data-lucide="flask-conical"></i><div>Simulated fixture — wiring preconditions do not apply. Every value produced by this run will be labelled SIMULATED.</div></div>`
              : (p.preconditions || []).map((pc) => `<label class="check"><input type="checkbox" data-pc="${esc(pc.key)}" ${(cfg.confirmations || {})[pc.key] ? "checked" : ""}>${esc(pc.label)}</label>`).join("")}
              ${p.limitations && p.limitations.length ? `<details style="margin-top:8px"><summary class="small muted">Profile limitations</summary><ul class="small muted">${p.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>` : ""}</div>
            <div class="c-6" id="valid"><div class="loading"><i data-lucide="loader-2" class="spin"></i>Validating…</div></div>
          </div></section>
      </div>
      <div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Back</button><span class="spacer"></span>
        <button class="btn btn-primary btn-lg" id="start"><i data-lucide="play"></i>START INSPECTION</button></div>`;

    let saveT = null;
    const save = (patch) => {
      clearTimeout(saveT);
      saveT = setTimeout(async () => {
        try { S.ins = await api.put(`/api/inspections/${ins.id}`, { config: patch }); validate(); } catch (e) { toast(e.message, "error"); }
      }, 250);
    };
    const validate = async () => {
      try {
        const v = await api.get(`/api/inspections/${ins.id}/validate`);
        S.validation = v;
        const tests = Object.entries(v.tests || {}).map(([k, t]) => `<li>${esc(k.replace(/_/g, " "))}: ${t.available ? `<b style="color:var(--pass)">available</b>${t.note ? " — " + esc(t.note) : ""}` : `<b>not available</b> — ${esc(t.reason || "")}`}</li>`).join("");
        $("#valid", view).innerHTML = (v.errors.length ? `<div class="callout error"><i data-lucide="octagon-x"></i><div><b>Resolve before starting</b><ul>${v.errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>` : `<div class="callout ok"><i data-lucide="check-circle-2"></i><div>Configuration valid — ready to start.</div></div>`)
          + (v.warnings.length ? `<div class="callout warn" style="margin-top:8px"><i data-lucide="alert-triangle"></i><div><ul style="margin:0">${v.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>` : "")
          + (tests ? `<ul class="small muted" style="margin:8px 0 0">${tests}</ul>` : "");
        $("#start", view).disabled = !v.ok;
        icons($("#valid", view));
      } catch (e) { $("#valid", view).innerHTML = `<div class="callout error">${esc(e.message)}</div>`; }
    };
    $("#mode", view).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
      $$("#mode button", view).forEach((x) => x.classList.toggle("on", x === b));
      const presets = { full: [1, 1, 1], optical: [1, 0, 0], electrical: [0, 1, 0], manufacturer: [0, 0, 1] }[b.dataset.v];
      ["optical", "electrical", "manufacturer"].forEach((k, i) => { $(`[data-dim=${k}]`, view).checked = !!presets[i]; });
      save({ mode: b.dataset.v }); });
    $$("[data-dim]", view).forEach((c) => c.addEventListener("change", () => {
      $$("#mode button", view).forEach((x) => x.classList.remove("on"));
      save({ [c.dataset.dim]: c.checked });
    }));
    $("#rail", view).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
      $$("#rail button", view).forEach((x) => x.classList.toggle("on", x === b)); save({ rail_v: +b.dataset.v }); });
    if ($("#clock", view)) $("#clock", view).onchange = (e) => save({ clock: e.target.value });
    $("#dur", view).onchange = (e) => save({ duration_s: +e.target.value });
    $("#func", view).onchange = (e) => save({ functional: e.target.checked });
    $("#iv", view).onchange = (e) => save({ iv: e.target.checked });
    const cond = () => ({ conditions: { ambient_c: $("#amb", view).value === "" ? null : +$("#amb", view).value, notes: $("#cnotes", view).value } });
    $("#amb", view).onchange = () => save(cond());
    $("#cnotes", view).onchange = () => save(cond());
    $$("[data-pc]", view).forEach((c) => c.addEventListener("change", () => {
      const conf = {}; $$("[data-pc]", view).forEach((x) => { conf[x.dataset.pc] = x.checked; }); save({ confirmations: conf });
    }));
    $("#back", view).onclick = () => go(3);
    $("#start", view).onclick = async () => {
      $("#start", view).disabled = true;
      try { S.ins = await api.post(`/api/inspections/${ins.id}/start`); paint(); }
      catch (e) { toast(e.message, "error"); $("#start", view).disabled = false; }
    };
    validate();
  }

  function runView(body) {
    const ins = S.ins;
    const p = profile();
    const evalCur = p.evaluation.find((e) => e.key === "supply_current");
    const r = reference();
    const spec = r && evalCur && evalCur.spec_order ? r.specs.find((s) => evalCur.spec_order.includes(s.key)) : null;
    const limTxt = spec ? `${spec.max != null ? "≤ " + spec.max : spec.min + "–" + spec.max} ${spec.unit} (${spec.symbol}, datasheet)` : "No datasheet limit";
    const vspec = r ? r.specs.find((s) => s.key === "vcc_range") : null;
    body.innerHTML = `
      <div class="grid">
        <section class="panel c-5"><div class="panel-head"><h3><i data-lucide="loader-2" class="spin"></i>Inspection running</h3><span class="meta">${sourceTag(ins.data_source)}</span></div>
          <div class="panel-body"><div class="progress"><i id="prog" style="width:${ins.run.progress || 0}%"></i></div>
            <div class="small muted" id="run-msg" style="margin:8px 0 10px">${esc(ins.run.message || "")}</div>
            <div class="stages" id="stages"></div>
            <div class="row" style="margin-top:12px"><button class="btn btn-danger" id="abort"><i data-lucide="square"></i>Abort</button></div></div></section>
        <section class="panel c-7"><div class="panel-head"><h3><i data-lucide="activity"></i>Live measurement vs reference</h3><span class="meta" id="lv-tag"></span></div>
          <div class="panel-body">
            <table class="t compact"><thead><tr><th>Quantity</th><th class="num">Latest</th><th class="num">Mean (window)</th><th>Reference</th></tr></thead>
              <tbody><tr><td>Supply voltage</td><td class="num" id="lv-v">—</td><td class="num" id="lv-vm">—</td><td class="small">Rail ${esc(ins.config.rail_v)} V ±5 %${vspec ? `; rating ${vspec.min}–${vspec.max} V` : ""}</td></tr>
              <tr><td>Supply current</td><td class="num" id="lv-i">—</td><td class="num" id="lv-im">—</td><td class="small">${esc(limTxt)}</td></tr>
              <tr><td>Power</td><td class="num" id="lv-p">—</td><td class="num" id="lv-pm">—</td><td class="small muted">Derived (V × I)</td></tr></tbody></table>
            <div class="chart-box short" style="margin-top:10px"><canvas id="run-chart"></canvas></div></div></section>
        <section class="panel c-12"><div class="panel-head"><h3><i data-lucide="binary"></i>Functional test results</h3></div>
          <div class="panel-body grid"><div class="c-8" id="func-live"><div class="muted small">Waiting for the functional stage…</div></div>
            <div class="c-4" id="func-chip">${chipSVG(p, { marking: p.base_part })}</div></div></section>
      </div>`;
    const paintStages = () => {
      const st = S.ins.run.stages || [];
      $("#stages", view).innerHTML = st.map((s) => `<div class="stage ${s.status}"><span class="ic">${
        s.status === "done" ? `<i data-lucide="check"></i>` : s.status === "running" || s.status === "waiting" ? `<i data-lucide="loader-2" class="spin"></i>` :
          s.status === "error" ? `<i data-lucide="x"></i>` : s.status === "skipped" ? `<i data-lucide="minus"></i>` : ""}</span>
        <div><div>${esc(s.label)}</div>${s.message ? `<div class="msg">${esc(s.message)}</div>` : ""}</div><span class="badge b-muted">${esc(s.status)}</span></div>`).join("");
      $("#prog", view).style.width = `${S.ins.run.progress || 0}%`;
      $("#run-msg", view).textContent = S.ins.run.message || "";
      icons($("#stages", view));
    };
    paintStages();
    const rows = [];
    const results = {};
    const highlight = {};
    const paintFunc = () => {
      const ft = p.functional_test;
      if (!ft) { $("#func-live", view).innerHTML = `<div class="muted small">Functional test not configured for this profile.</div>`; return; }
      if (ft.type === "logic_truth_table" && rows.length) {
        $("#func-live", view).innerHTML = `<table class="t compact truth"><thead><tr><th>Gate</th><th>A</th><th>B</th><th>Expected Y</th><th>Observed Y</th><th class="num">VOUT</th><th>Result</th></tr></thead>
          <tbody>${rows.map((r) => `<tr><td>${esc(r.gate)}</td><td class="num">${r.vector[0]}</td><td class="num">${r.vector[1]}</td><td class="num">${r.expected}</td>
          <td class="num ${r.pass ? "obs-pass" : "obs-fail"}">${r.observed}</td><td class="num">${r.v_out_mv != null ? (r.v_out_mv / 1000).toFixed(3) + " V" : "—"}</td><td>${statusBadge(r.pass ? "PASS" : "FAIL")}</td></tr>`).join("")}</tbody></table>`;
      } else if (ft.type === "analog_buffer" && rows.length) {
        $("#func-live", view).innerHTML = `<table class="t compact"><thead><tr><th class="num">Set</th><th class="num">VIN</th><th class="num">VOUT</th><th class="num">Error</th></tr></thead>
          <tbody>${rows.map((r) => `<tr><td class="num">${r.set_mv} mV</td><td class="num">${r.vin_mv} mV</td><td class="num">${r.vout_mv} mV</td><td class="num">${r.vout_mv - r.vin_mv} mV</td></tr>`).join("")}</tbody></table>`;
      } else if (Object.keys(results).length) {
        $("#func-live", view).innerHTML = Object.entries(results).map(([k, v]) => `<div class="kv"><div>${esc(k.replace(/_/g, " "))}</div><div class="num">${esc(
          k === "heartbeat" ? `${v.freq_hz} Hz (${v.edges} edges in ${v.window_ms} ms)` : v.found ? v.line : "No matching line received")}</div></div>`).join("");
      }
      $("#func-chip", view).innerHTML = chipSVG(p, { marking: p.base_part, highlight });
    };
    const chart = lineChart($("#run-chart", view), { datasets: [{ label: "Current (mA)", color: COLORS.current }], yTitle: "mA", xTitle: "s", sim: simulated() });
    S.charts.push(chart);
    const t0 = Date.now();
    const onMeas = (d) => {
      const s = d.sample;
      $("#lv-tag", view).innerHTML = sourceTag(s.src);
      $("#lv-v", view).textContent = autoNum(s.v, "V"); $("#lv-i", view).textContent = autoNum(s.i, "mA"); $("#lv-p", view).textContent = autoNum(s.p, "mW");
      if (d.stats && d.stats.v) { $("#lv-vm", view).textContent = autoNum(d.stats.v.mean, "V"); $("#lv-im", view).textContent = autoNum(d.stats.i.mean, "mA"); $("#lv-pm", view).textContent = autoNum(d.stats.p.mean, "mW"); }
      if (chart && s.valid) {
        const data = chart.data.datasets[0].data;
        data.push({ x: (s.t - t0) / 1000, y: s.i });
        if (data.length > 600) data.shift();
        chart.update("none");
      }
    };
    const onRow = (d) => {
      if (d.id !== ins.id) return;
      if (d.row) {
        rows.push(d.row);
        if (d.row.gate && p.functional_test && p.functional_test.gates) {
          const g = p.functional_test.gates.find((x) => x.name === d.row.gate);
          if (g) { g.inputs.forEach((pin, i) => { highlight[pin] = d.row.vector[i] ? "high" : "low"; }); highlight[g.output] = d.row.pass ? (d.row.observed ? "high" : "low") : "fail"; }
        }
      }
      if (d.result) results[d.result] = d.data;
      paintFunc();
    };
    const onIns = async (d) => {
      if (d.id !== ins.id) return;
      S.ins.run = d.run || S.ins.run;
      S.ins.status = d.status;
      if (d.status !== "running") {
        offRun.forEach((f) => f());
        await refresh();
        if (d.status === "measured") { toast("Measurements complete — review the analysis", "ok"); go(5); }
        else { toast(`Inspection ${d.status}: ${(S.ins.run.errors || []).join(" ")}`, "error"); go(4); }
        return;
      }
      paintStages();
    };
    const onProg = (d) => { if (d.id === ins.id) { S.ins.run.progress = d.progress; $("#prog", view).style.width = `${d.progress}%`; } };
    const offRun = [store.on("measurement", onMeas), store.on("functional_row", onRow), store.on("inspection", onIns), store.on("inspection_progress", onProg)];
    S.offs.push(...offRun);
    $("#abort", view).onclick = async () => {
      if (await confirmDialog("Abort inspection?", "The run stops and is saved as aborted. No partial result is scored.", "Abort", true)) {
        await api.post(`/api/inspections/${ins.id}/abort`);
      }
    };
    // catch up if the run finished before the page rendered
    refresh().then(() => { if (S.ins.status !== "running") onIns({ id: ins.id, status: S.ins.status, run: S.ins.run }); }).catch(() => {});
  }

  // ------------------------------------------------------------ step 5
  function evalTable(evals) {
    return `<div class="table-wrap"><table class="t"><thead><tr><th>Parameter</th><th>Measured</th><th>Reference</th><th>Status</th><th>Remarks</th></tr></thead><tbody>
      ${evals.map((e) => `<tr><td>${esc(e.label)}${e.mandatory ? ` <span class="tiny muted">mandatory</span>` : ""}<div class="tiny muted">weight ${num(e.weight, 2)}</div></td>
        <td class="num">${esc(e.measured_text)}</td><td class="small">${esc((e.reference || {}).text || "—")}<div>${provTag(e.reference)}</div></td>
        <td>${statusBadge(e.status)}${e.critical ? ` <span class="badge b-fail">Critical</span>` : ""}</td><td class="small">${esc(e.remarks)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function stepAnalysis(body) {
    const ins = S.ins;
    if (!ins || !["measured", "completed"].includes(ins.status)) { go(4); return; }
    const p = profile();
    const el = ins.electrical || {};
    const sum = el.summary || {};
    const img = ins.image;
    const meas = ins.measurements || {};
    const fn = ins.functional || {};
    body.innerHTML = `
      <section class="panel"><div class="panel-head"><h3><i data-lucide="zap"></i>Electrical fingerprinting</h3>
        <span class="meta">${sourceTag(ins.data_source)} ${sum.score != null ? `<span class="badge b-info num">Se ${num(sum.score, 1)}</span>` : `<span class="badge b-muted">${esc(sum.status || "not performed")}</span>`}
        <span class="num">coverage ${Math.round((sum.coverage || 0) * 100)} %</span></span></div>
        <div class="panel-body stack" style="gap:12px">
          ${ins.config.electrical ? `
          ${(sum.reasons || []).length ? `<div class="callout warn"><i data-lucide="alert-triangle"></i><div>${sum.reasons.map(esc).join("<br>")}</div></div>` : ""}
          <div class="small muted num">${meas.n_valid ?? 0} valid of ${meas.n_total ?? 0} supply samples · ${esc(meas.method || "")}${meas.n_invalid ? ` · ${meas.n_invalid} invalid samples excluded` : ""}</div>
          ${evalTable(el.evaluations || [])}
          <div class="grid"><div class="c-7"><div class="label" style="margin-bottom:6px">Supply current during the run</div><div class="chart-box"><canvas id="a-chart"></canvas></div></div>
            <div class="c-5">${ins.iv && ins.iv.points ? `<div class="label" style="margin-bottom:6px">Pin I-V signature (pin ${esc(ins.iv.pin)})</div><div class="chart-box"><canvas id="a-iv"></canvas></div>`
              : `<div class="label" style="margin-bottom:6px">Functional test</div><div class="small">${esc(fn.summary_text || fn.reason || "Not run")}</div>${chipSVG(p, { marking: p.base_part })}`}</div></div>` : `<div class="muted">Electrical inspection was not part of this inspection mode.</div>`}
        </div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="scan-eye"></i>Optical inspection</h3><span class="meta">${ins.config.optical ? "Operator review" : "Not selected"}</span></div>
        <div class="panel-body">${ins.config.optical ? `<div class="grid">
          <div class="c-5">${img ? `<div class="img-frame"><img src="/api/images/${esc(img.image_id)}" alt="Inspection image"></div>
            <div class="row" style="margin-top:8px">${img.synthetic ? `<span class="sim-tag">SYNTHETIC SAMPLE</span>` : ""}<span class="small muted" id="cmp-res"></span></div>
            <div id="cmp-img" style="margin-top:8px"></div>` : `<div class="empty"><i data-lucide="image-off"></i><b>No image</b>Only the operator checklist can be assessed.</div>`}</div>
          <div class="c-7"><div class="small muted" style="margin-bottom:8px">Readability, part-number consistency and reference-image similarity are computed automatically. Assess the remaining checks visually${simulated() ? " — values pre-filled by the simulation profile are labelled simulated" : ""}.</div>
            <table class="t compact"><tbody>${OPT_MANUAL.map(([k, l]) => {
              const v = S.optManual[k] || { status: "not_assessed", note: "" };
              return `<tr><td style="width:42%">${esc(l)}${v.simulated ? ` <span class="sim-tag">SIM</span>` : ""}</td><td><div class="seg status" data-k="${k}">${[["pass", "Pass"], ["warn", "Warn"], ["fail", "Fail"], ["not_assessed", "N/A"]]
                .map(([sv, sl]) => `<button data-v="${sv}" class="${v.status === sv ? "on " + sv : ""}">${sl}</button>`).join("")}</div>
                <input class="small" data-note="${k}" value="${esc(v.note || "")}" placeholder="Observation" style="margin-top:5px"></td></tr>`; }).join("")}</tbody></table>
            <p class="tiny muted">Visual inspection identifies visible inconsistencies and suspicious markings. It cannot establish electrical genuineness.</p></div></div>` : `<div class="muted">Optical inspection was not part of this inspection mode.</div>`}</div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="database"></i>Manufacturer verification</h3></div>
        <div class="panel-body">${!ins.config.manufacturer ? `<div class="muted">Not part of this inspection mode.</div>` : reference()
          ? `<div class="small">Reference <b class="num">${esc(reference().part_number)}</b> (${esc(reference().manufacturer)}) · ${esc(reference().datasheet.doc_id)} · ${esc(reference().verification.status.replace(/_/g, " "))}. Factors are scored when you compute the screening result.</div>`
          : `<div class="callout warn"><i data-lucide="database-zap"></i><div><b>Reference unavailable</b> — this dimension will be inconclusive.</div></div>`}</div></section>
      <div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Back</button>
        <button class="btn" id="baseline" ${ins.config.electrical ? "" : "disabled"}><i data-lucide="bookmark-plus"></i>Record as known-good baseline</button>
        <a class="btn btn-ghost" href="/api/inspections/${esc(ins.id)}/measurements.csv"><i data-lucide="download"></i>Measurements CSV</a>
        <span class="spacer"></span><button class="btn btn-primary btn-lg" id="finalize"><i data-lucide="calculator"></i>Compute screening result</button></div>`;
    // charts
    if (ins.config.electrical && meas.series && $("#a-chart", view)) {
      const sp = (ph) => meas.series.filter((x) => x.phase === ph && x.valid).map((x) => ({ x: x.t, y: x.i }));
      S.charts.push(lineChart($("#a-chart", view), { datasets: [{ label: "Supply monitoring", color: COLORS.current, data: sp("static") },
        { label: "Functional test", color: COLORS.voltage, data: sp("functional") }], yTitle: "mA", xTitle: "Time (s)", sim: simulated(), legend: true }));
    }
    if (ins.iv && ins.iv.points && $("#a-iv", view)) {
      const ds = [{ label: "Measured", color: COLORS.current, data: ins.iv.points.map((q) => ({ x: q.v, y: q.i_ua })), points: 2 }];
      if (ins.iv.reference) ds.push({ label: "Reference", color: COLORS.ref, dashed: true, data: ins.iv.reference.points.map((q) => ({ x: q.v, y: q.i_ua })) });
      S.charts.push(lineChart($("#a-iv", view), { datasets: ds, xTitle: "Pin voltage (V)", yTitle: "µA", sim: simulated(), legend: true }));
    }
    // image comparison preview
    if (ins.config.optical && img) {
      api.post("/api/optical/compare", { image_id: img.image_id, reference_id: ins.reference_id, profile_id: ins.profile_id,
        sim_profile_id: img.synthetic ? img.sim_profile_id : null }).then((c) => {
        const res = $("#cmp-res", view);
        if (!res) return;
        if (!c) { res.textContent = "No reference image stored for this part — similarity not assessed."; return; }
        res.innerHTML = `Reference image (${esc(c.provenance)}): SSIM <b class="num">${num(c.ssim, 2)}</b>${c.aligned ? " after alignment" : ""} — indicative only`;
        if (c.reference_url) $("#cmp-img", view).innerHTML = `<div class="img-frame" style="min-height:0"><img src="${esc(c.reference_url)}" alt="Reference image" style="max-height:150px"></div>`;
      }).catch(() => {});
    }
    $$(".seg.status", view).forEach((seg) => seg.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const k = seg.dataset.k;
      S.optManual[k] = { ...(S.optManual[k] || {}), status: b.dataset.v, simulated: false };
      $$("button", seg).forEach((x) => { x.className = x === b ? "on " + x.dataset.v : ""; });
    }));
    $$("[data-note]", view).forEach((inp) => inp.addEventListener("input", () => {
      const k = inp.dataset.note; S.optManual[k] = { ...(S.optManual[k] || { status: "not_assessed" }), note: inp.value, simulated: false };
    }));
    $("#back", view).onclick = () => go(4);
    $("#baseline", view).onclick = async () => {
      const ok = await confirmDialog("Record known-good baseline?",
        `Use this only for a sample you have verified as genuine (e.g. bought from an authorised distributor). Its supply current${ins.iv && ins.iv.points ? ", I-V curve" : ""}${img && !img.synthetic ? " and image" : ""} become the empirical reference for <b>${esc(p.name)}</b> at ${esc(ins.config.rail_v)} V${simulated() ? ". This baseline is SIMULATED and is only used for simulated inspections" : ""}.`, "Record baseline");
      if (!ok) return;
      try { await api.post(`/api/inspections/${ins.id}/baseline`); toast("Baseline recorded", "ok"); } catch (e) { toast(e.message, "error"); }
    };
    $("#finalize", view).onclick = async () => {
      $("#finalize", view).disabled = true;
      try { S.ins = await api.post(`/api/inspections/${ins.id}/finalize`, { optical_manual: S.optManual }); go(6); }
      catch (e) { toast(e.message, "error"); $("#finalize", view).disabled = false; }
    };
  }

  // ------------------------------------------------------------ step 6
  function stepScore(body) {
    const ins = S.ins;
    if (!ins || ins.status !== "completed") { go(5); return; }
    body.innerHTML = scoreView(ins) + `<div class="row"><button class="btn" id="back"><i data-lucide="arrow-left"></i>Revise analysis</button><span class="spacer"></span>
      <button class="btn btn-primary btn-lg" id="next"><i data-lucide="file-text"></i>Generate inspection report</button></div>`;
    $("#back", view).onclick = () => go(5);
    $("#next", view).onclick = () => go(7);
  }

  // ------------------------------------------------------------ step 7
  function stepReport(body) {
    const ins = S.ins;
    if (!ins || ins.status !== "completed") { go(5); return; }
    body.innerHTML = `<section class="panel"><div class="panel-head"><h3><i data-lucide="file-text"></i>Inspection report</h3><span class="meta">${sourceTag(ins.data_source)}</span></div>
      <div class="panel-body stack" style="gap:12px">
        <div class="row"><button class="btn btn-primary" id="gen"><i data-lucide="file-cog"></i>${S.report ? "Regenerate PDF" : "Generate PDF"}</button>
          <a class="btn" id="dl" href="/api/inspections/${esc(ins.id)}/report.pdf?download=1" ${S.report ? "" : "hidden"}><i data-lucide="download"></i>Download PDF</a>
          <a class="btn btn-ghost" id="open" href="/api/inspections/${esc(ins.id)}/report.pdf" target="_blank" rel="noopener" ${S.report ? "" : "hidden"}><i data-lucide="external-link"></i>Open in new tab</a>
          <span class="small muted" id="rep-meta">${S.report ? `Last generated ${new Date(S.report.created_at).toLocaleString()}` : "The report is built from the stored inspection record."}</span></div>
        <div id="pdf">${S.report ? `<iframe class="iframe-pdf" src="/api/inspections/${esc(ins.id)}/report.pdf#view=FitH" title="Inspection report"></iframe>` : ""}</div>
      </div></section>`;
    $("#gen", view).onclick = async () => {
      $("#gen", view).disabled = true;
      try {
        await api.post(`/api/inspections/${ins.id}/report`);
        await refresh();
        toast("Report generated", "ok");
        paint();
      } catch (e) { toast(e.message, "error"); $("#gen", view).disabled = false; }
    };
  }

  paint();
  return { destroy() { destroyCharts(); S.offs.forEach((f) => f()); } };
}

// Shared with the Trust Score page.
export function scoreView(ins) {
  const sc = ins.scores;
  const inc = sc.classification_key === "inconclusive";
  const mf = ins.manufacturer || {};
  const opt = ins.optical || {};
  return `
    <section class="panel accent"><div class="panel-body grid" style="align-items:center">
      <div class="c-5"><div class="label">Screening result ${sourceTag(ins.data_source)}</div>
        <div style="margin:8px 0">${classBadge(sc.classification, sc.classification_key)}</div>
        <div class="small muted">${sc.reasons.map(esc).join("<br>")}</div></div>
      <div class="c-3"><div class="label">Overall trust score</div>
        <div class="big-score">${sc.overall != null && !inc ? num(sc.overall, 1) : "—"}<small>/100</small></div>
        ${inc && sc.overall != null ? `<div class="tiny muted">Partial ${num(sc.overall, 1)} over available evidence — not a screening score</div>` : ""}</div>
      <div class="c-4"><div class="label">Evidence coverage</div><div class="big-score" style="font-size:30px">${Math.round(sc.coverage * 100)}<small>%</small></div>
        <div class="tiny muted">Minimum ${Math.round(sc.thresholds.min_coverage * 100)} %; electrical evidence ${sc.thresholds.require_electrical ? "required" : "optional"}</div></div>
    </div></section>
    <section class="panel"><div class="panel-head"><h3><i data-lucide="sigma"></i>Score calculation</h3></div>
      <div class="panel-body stack" style="gap:12px"><div class="formula">${esc(sc.formula)}</div>
        <table class="t"><thead><tr><th>Dimension</th><th class="num">Weight</th><th class="num">Score</th><th class="num">Weighted</th><th style="width:30%">Score bar</th><th>Status</th></tr></thead><tbody>
        ${sc.dimensions.map((d) => `<tr><td>${esc(d.label)}</td><td class="num">${num(d.weight, 2)}</td><td class="num">${d.score != null ? num(d.score, 1) : "—"}</td>
          <td class="num">${d.contribution != null ? num(d.contribution, 2) : "excluded"}</td><td><div class="bar ${scoreBarClass(d.score)}"><i style="width:${d.score || 0}%"></i></div></td>
          <td class="small">${esc((d.status || "").replace(/_/g, " "))}${d.reasons && d.reasons.length && d.score == null ? `<div class="tiny muted">${esc(d.reasons.join("; "))}</div>` : ""}</td></tr>`).join("")}</tbody></table>
        <div class="tiny muted">Dimensions without a score are excluded from the weighted mean and lower the coverage. Missing evidence never counts as a pass. Thresholds: ≥ ${sc.thresholds.consistent} reference-consistent, ≥ ${sc.thresholds.investigate} investigation recommended, below that significant deviation.</div></div></section>
    ${sc.flags.length ? `<section class="panel"><div class="panel-head"><h3><i data-lucide="flag"></i>Findings</h3></div><div class="panel-body"><table class="t compact"><tbody>
      ${sc.flags.map((f) => `<tr><td style="width:110px"><span class="badge ${f.severity === "critical" ? "b-fail" : "b-warn"}">${esc(f.severity)}</span></td><td style="width:120px" class="small">${esc(f.dimension)}</td><td class="small">${esc(f.message)}</td></tr>`).join("")}
      </tbody></table></div></section>` : ""}
    <div class="grid">
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="database"></i>Manufacturer factors</h3><span class="meta">${mf.score != null ? `Sm ${num(mf.score, 1)}` : esc((mf.status || "").replace(/_/g, " "))}</span></div>
        <div class="panel-body">${(mf.factors || []).length ? `<table class="t compact"><tbody>${mf.factors.map((f) => `<tr><td>${esc(f.label)}<div class="tiny muted">${esc(f.detail)}</div></td><td class="num">${f.points}/${f.max}</td></tr>`).join("")}</tbody></table>
          <p class="tiny muted">Measures the availability and consistency of reference data — not a rating of the manufacturer.</p>` : `<div class="muted small">${esc((mf.reasons || []).join(" ") || "Not performed")}</div>`}</div></section>
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="scan-eye"></i>Optical checks</h3><span class="meta">${opt.summary && opt.summary.score != null ? `Sv ${num(opt.summary.score, 1)}` : esc((opt.summary || {}).status || "")}</span></div>
        <div class="panel-body">${(opt.checks || []).length ? `<table class="t compact"><tbody>${opt.checks.map((c) => `<tr><td>${esc(c.label)}<div class="tiny muted">${esc(c.observed || c.note || "")}</div></td><td>${statusBadge(c.status)}</td></tr>`).join("")}</tbody></table>` : `<div class="muted small">Not performed</div>`}</div></section>
    </div>`;
}
