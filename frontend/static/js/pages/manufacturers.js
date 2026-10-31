import { api } from "../api.js";
import { loadReferences, loadManufacturers, loadProfiles } from "../store.js";
import { $, $$, esc, icons, toast, modal, confirmDialog, statusBadge, provTag, sourceTag, categoryLabel } from "../ui.js";
import { candidatesHTML, identStatusBadge } from "../shared.js";

export async function render(view) {
  let [refs, mfrs, profiles] = await Promise.all([loadReferences(true), loadManufacturers(true), loadProfiles()]);
  const st = { q: "", mfr: "", sel: refs[0] && refs[0].id, insSel: null };

  view.innerHTML = `
    <div class="page-head"><div><h2>Manufacturer database</h2><p>Local OEM reference records, usable offline: component identification → manufacturer → datasheet → specifications → comparison with measurements.</p></div>
      <div class="actions"><button class="btn" id="add-mfr"><i data-lucide="factory"></i>Add manufacturer</button>
        <button class="btn btn-primary" id="add-ref"><i data-lucide="plus"></i>Add component record</button></div></div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><h3><i data-lucide="search"></i>Lookup from marking text</h3><span class="meta" id="lk-badge"></span></div>
      <div class="panel-body grid"><div class="c-5"><div class="row tight"><input id="lk" placeholder="Paste marking text, e.g. NXP 74HC00N 1029"><button class="btn" id="lk-btn">Look up</button></div>
        <p class="tiny muted">Normalises the text, corrects common OCR look-alikes (O/0, I/1, S/5) and searches part-number patterns.</p></div>
        <div class="c-7" id="lk-out"></div></div></section>
    <div class="split">
      <section class="panel"><div class="panel-body" style="padding:10px"><input id="q" placeholder="Search part or manufacturer"><select id="mf" style="margin-top:8px"><option value="">All manufacturers</option>${mfrs.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select></div>
        <div class="list" id="list"></div></section>
      <div class="stack" id="detail"></div>
    </div>`;

  function paintList() {
    const q = st.q.toLowerCase();
    const items = refs.filter((r) => (!st.mfr || r.manufacturer_id === st.mfr) && (!q || `${r.part_number} ${r.base_part} ${r.manufacturer}`.toLowerCase().includes(q)));
    $("#list", view).innerHTML = items.map((r) => `<div class="list-item ${r.id === st.sel ? "on" : ""}" data-id="${esc(r.id)}">
      <div class="row tight"><span class="li-title">${esc(r.part_number)}</span><span class="spacer"></span>${verBadge(r)}</div>
      <div class="li-sub">${esc(r.manufacturer)} · ${esc(categoryLabel(r.category))} · ${esc(r.package)}</div></div>`).join("") || `<div class="empty">No records match.</div>`;
    $$("#list .list-item", view).forEach((el) => el.onclick = () => { st.sel = el.dataset.id; st.insSel = null; paintList(); paintDetail(); });
  }
  const verBadge = (r) => {
    const s = (r.verification || {}).status;
    return s === "verified_datasheet" ? `<span class="badge b-pass">Verified</span>` : s === "partially_verified" ? `<span class="badge b-warn">Partly verified</span>` : `<span class="badge b-muted">Unverified</span>`;
  };

  async function paintDetail() {
    const r = refs.find((x) => x.id === st.sel);
    const box = $("#detail", view);
    if (!r) { box.innerHTML = ""; return; }
    const mfr = mfrs.find((m) => m.id === r.manufacturer_id) || {};
    let using = [];
    try { using = (await api.get("/api/inspections")).filter((i) => i.reference_id === r.id && ["measured", "completed"].includes(i.status)); } catch (e) { /* ignore */ }
    if (!st.insSel && using.length) st.insSel = using[0].id;
    box.innerHTML = `
      <section class="panel"><div class="panel-head"><h3><i data-lucide="file-badge"></i><span class="num">${esc(r.part_number)}</span></h3>
        <span class="meta">${verBadge(r)}${r.builtin ? `<span class="badge b-muted">Built-in</span>` : `<button class="btn btn-sm btn-danger" id="del"><i data-lucide="trash-2"></i>Delete</button>`}</span></div>
        <div class="panel-body grid">
          <div class="c-7"><div class="kv">
            <div>Identified manufacturer</div><div><b>${esc(r.manufacturer)}</b>${mfr.aliases ? ` <span class="tiny muted">aliases: ${esc(mfr.aliases.join(", "))}</span>` : ""}</div>
            <div>Identified component</div><div>${esc(r.description)}</div>
            <div>Category · package</div><div>${esc(categoryLabel(r.category))} · ${esc(r.package)}</div>
            <div>Datasheet source</div><div>${esc(r.datasheet.title || "—")}<br><span class="small muted">${esc(r.datasheet.doc_id || "")} ${esc(r.datasheet.revision || "")}</span></div>
            <div>Datasheet URL</div><div class="small">${r.datasheet.url ? `<a href="${esc(r.datasheet.url)}" target="_blank" rel="noopener">${esc(r.datasheet.url)}</a>` : `<span class="empty-val">Reference unavailable</span>`}</div>
            <div>Verification status</div><div class="small">${esc((r.verification || {}).status || "").replace(/_/g, " ")} · ${esc((r.verification || {}).checked_on || "")}<br><span class="muted">${esc((r.verification || {}).note || "")}</span></div>
            <div>Match patterns</div><div>${(r.match_patterns || []).map((m) => `<span class="chip mono">${esc(m)}</span>`).join(" ")}</div>
            <div>Linked profile</div><div>${esc((profiles.find((p) => p.id === r.profile_id) || {}).name || "None")}</div>
            <div>Dataset version</div><div class="num">${esc(r.dataset_version || "")}</div></div></div>
          <div class="c-5 stack" style="gap:10px">${r.datasheet.url ? `<a class="btn btn-primary" href="${esc(r.datasheet.url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>VIEW OFFICIAL DATASHEET</a>` : ""}
            ${!r.builtin && (r.verification || {}).status !== "verified_datasheet" ? `<button class="btn" id="verify"><i data-lucide="badge-check"></i>Mark verified against datasheet</button>` : ""}
            <div class="small"><b>Function.</b> ${esc(r.functional_description || "—")}</div><div class="small"><b>Test conditions.</b> ${esc(r.test_conditions || "—")}</div>
            ${(r.legacy_types || []).map((l) => `<div class="callout info"><i data-lucide="history"></i><div class="small"><b>Legacy ${esc(l.type)}</b> — ${esc(l.note)}</div></div>`).join("")}
            <p class="tiny muted">IC VISION stores only publicly available datasheet values. No manufacturer partnership or proprietary dataset is implied.</p></div></div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="table"></i>Extracted reference specifications</h3><span class="meta">${r.specs.length} rows</span></div>
        <div class="panel-body flush"><div class="table-wrap"><table class="t"><thead><tr><th>Specification</th><th>Symbol</th><th class="num">Min</th><th class="num">Typ</th><th class="num">Max</th><th>Unit</th><th>Conditions</th><th>Provenance</th></tr></thead><tbody>
        ${r.specs.map((s) => `<tr><td>${esc(s.label)}</td><td class="mono">${esc(s.symbol || "")}</td><td class="num">${s.text ? `<span class="small">${esc(s.text)}</span>` : s.min_offset_from_vcc != null ? `VCC${s.min_offset_from_vcc}` : s.min ?? "—"}</td>
          <td class="num">${s.typ ?? "—"}</td><td class="num">${s.max ?? "—"}</td><td>${esc(s.unit || "")}</td><td class="small">${esc(s.conditions || "")}${s.note ? `<div class="tiny muted">${esc(s.note)}</div>` : ""}</td><td>${provTag(s)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No specification rows.</td></tr>`}</tbody></table></div></div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="git-compare-arrows"></i>Measured versus reference</h3>
        <span class="meta">${using.length ? `<select id="ins-sel" style="width:auto">${using.map((i) => `<option value="${i.id}" ${i.id === st.insSel ? "selected" : ""}>${esc(i.id)} (${esc(i.data_source)})</option>`).join("")}</select>` : ""}</span></div>
        <div class="panel-body" id="mvr">${using.length ? `<div class="loading"><i data-lucide="loader-2" class="spin"></i>Loading…</div>` : `<div class="muted small">No inspection has used this record yet.</div>`}</div></section>`;
    if ($("#del", view)) $("#del", view).onclick = async () => {
      if (!(await confirmDialog("Delete record?", `${esc(r.part_number)} will be removed from the local database.`, "Delete", true))) return;
      await api.del(`/api/references/${r.id}`); refs = await loadReferences(true); st.sel = refs[0].id; paintList(); paintDetail();
    };
    if ($("#verify", view)) $("#verify", view).onclick = async () => {
      if (!(await confirmDialog("Mark as verified?", "Confirm you have checked every specification row against the official datasheet.", "Mark verified"))) return;
      const specs = r.specs.map((s) => ({ ...s, verified: true }));
      await api.put(`/api/references/${r.id}`, { specs, verification: { status: "verified_datasheet", checked_on: new Date().toISOString().slice(0, 10), note: "Verified by operator against the datasheet." } });
      refs = await loadReferences(true); paintList(); paintDetail();
    };
    if ($("#ins-sel", view)) $("#ins-sel", view).onchange = (e) => { st.insSel = e.target.value; paintMvr(); };
    icons(box);
    if (using.length) paintMvr();
  }

  async function paintMvr() {
    const ins = await api.get(`/api/inspections/${st.insSel}`);
    const ev = ((ins.electrical || {}).evaluations || []);
    $("#mvr", view).innerHTML = `<div class="small" style="margin-bottom:8px">${sourceTag(ins.data_source)} ${esc(ins.component.name)} · rail ${esc(ins.config.rail_v)} V</div>
      <table class="t"><thead><tr><th>Parameter</th><th>Measured</th><th>Reference</th><th>Status</th></tr></thead><tbody>
      ${ev.map((e) => `<tr><td>${esc(e.label)}</td><td class="num">${esc(e.measured_text)}</td><td class="small">${esc((e.reference || {}).text || "—")} ${provTag(e.reference)}</td><td>${statusBadge(e.status)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No electrical evaluation in this inspection.</td></tr>`}</tbody></table>`;
    icons($("#mvr", view));
  }

  $("#q", view).oninput = (e) => { st.q = e.target.value; paintList(); };
  $("#mf", view).onchange = (e) => { st.mfr = e.target.value; paintList(); };
  const lookup = async () => {
    try {
      const r = await api.post("/api/identify", { text: $("#lk", view).value });
      $("#lk-badge", view).innerHTML = identStatusBadge(r);
      $("#lk-out", view).innerHTML = `<p class="small" style="margin-top:0">${esc(r.message)}</p>${r.candidates.length ? candidatesHTML(r, null) : ""}`;
      $$("#lk-out .cand", view).forEach((el) => el.onclick = () => { st.sel = el.dataset.ref; paintList(); paintDetail(); });
      icons($("#lk-out", view));
    } catch (e) { toast(e.message, "error"); }
  };
  $("#lk-btn", view).onclick = lookup;
  $("#lk", view).onkeydown = (e) => { if (e.key === "Enter") lookup(); };

  $("#add-mfr", view).onclick = async () => {
    const m = await modal({ title: "Add manufacturer", body: `<div class="stack" style="gap:10px">
      <div class="field"><label>Name *</label><input id="m-name"></div>
      <div class="field"><label>Marking aliases (comma separated)</label><input id="m-al" placeholder="e.g. ST, STMICRO"></div>
      <div class="field"><label>Website</label><input id="m-web" placeholder="https://"></div>
      <div class="field"><label>Notes</label><input id="m-notes"></div></div>`,
      actions: [{ label: "Cancel", value: null }, { label: "Add", kind: "btn-primary", handler: (root) => api.post("/api/manufacturers", {
        name: $("#m-name", root).value, aliases: $("#m-al", root).value, website: $("#m-web", root).value, notes: $("#m-notes", root).value }) }] });
    if (m) { mfrs = await loadManufacturers(true); toast(`${m.name} added`, "ok"); render(view); }
  };

  $("#add-ref", view).onclick = async () => {
    const specRow = () => `<tr><td><input data-f="label" placeholder="e.g. Static supply current"></td><td><input data-f="key" placeholder="icc_static_max"></td><td><input data-f="min" type="number" step="any"></td>
      <td><input data-f="typ" type="number" step="any"></td><td><input data-f="max" type="number" step="any"></td><td><input data-f="unit" placeholder="µA"></td><td><input data-f="conditions"></td></tr>`;
    const rec = await modal({
      title: "Add component record", wide: true,
      body: `<div class="fields">
        <div class="field"><label>Manufacturer *</label><select id="r-m">${mfrs.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Part number *</label><input id="r-pn" placeholder="SN74HC04N"></div>
        <div class="field"><label>Base part *</label><input id="r-bp" placeholder="74HC04"></div>
        <div class="field"><label>Category *</label><select id="r-cat"><option value="logic_ic">Logic IC</option><option value="microcontroller">Microcontroller</option><option value="op_amp">Operational amplifier</option><option value="custom">Other</option></select></div>
        <div class="field"><label>Package</label><input id="r-pkg" placeholder="PDIP-14"></div>
        <div class="field"><label>Linked profile</label><select id="r-prof"><option value="">None</option>${profiles.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div>
        <div class="field" style="grid-column:1/-1"><label>Datasheet title</label><input id="r-dt"></div>
        <div class="field"><label>Document ID / revision</label><input id="r-doc"></div>
        <div class="field" style="grid-column:span 2"><label>Official datasheet URL</label><input id="r-url" placeholder="https://… (from the manufacturer's own site)"></div>
        <div class="field" style="grid-column:1/-1"><label>Description</label><input id="r-desc"></div></div>
        <div class="label" style="margin:14px 0 6px">Specification rows (values as printed in the datasheet; key names such as vcc_range and icc_static_max are used by the evaluation engine)</div>
        <div class="table-wrap"><table class="t compact" id="spec-t"><thead><tr><th>Label</th><th>Key</th><th>Min</th><th>Typ</th><th>Max</th><th>Unit</th><th>Conditions</th></tr></thead><tbody>${specRow()}${specRow()}</tbody></table></div>
        <button class="btn btn-sm" id="add-row" style="margin-top:6px"><i data-lucide="plus"></i>Add row</button>
        <p class="tiny muted">New records are saved as <b>unverified</b> until you confirm every value against the datasheet.</p>`,
      onMount: (root) => { $("#add-row", root).onclick = () => { $("#spec-t tbody", root).insertAdjacentHTML("beforeend", specRow()); }; },
      actions: [{ label: "Cancel", value: null }, { label: "Save record", kind: "btn-primary", handler: (root) => {
        const specs = $$("#spec-t tbody tr", root).map((tr) => {
          const g = (f) => $(`[data-f=${f}]`, tr).value.trim();
          if (!g("label") && !g("key")) return null;
          const n = (f) => g(f) === "" ? undefined : +g(f);
          return { key: g("key") || g("label").toLowerCase().replace(/[^a-z0-9]+/g, "_"), label: g("label"), min: n("min"), typ: n("typ"), max: n("max"),
            unit: g("unit"), conditions: g("conditions"), source_type: "oem", verified: false };
        }).filter(Boolean);
        const doc = $("#r-doc", root).value.split("/");
        return api.post("/api/references", { manufacturer_id: $("#r-m", root).value, part_number: $("#r-pn", root).value, base_part: $("#r-bp", root).value,
          category: $("#r-cat", root).value, package: $("#r-pkg", root).value, profile_id: $("#r-prof", root).value || null, description: $("#r-desc", root).value,
          datasheet: { title: $("#r-dt", root).value, doc_id: (doc[0] || "").trim(), revision: (doc[1] || "").trim(), url: $("#r-url", root).value.trim() }, specs });
      } }] });
    if (rec) { refs = await loadReferences(true); st.sel = rec.id; paintList(); paintDetail(); toast("Record saved (unverified)", "ok"); }
  };

  paintList();
  await paintDetail();
}
