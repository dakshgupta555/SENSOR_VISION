import { api } from "../api.js";
import { loadProfiles, loadManufacturers } from "../store.js";
import { $, $$, esc, icons, toast, num, dateOf, sourceTag, classBadge, confirmDialog } from "../ui.js";

export async function render(view) {
  const [profiles, mfrs] = await Promise.all([loadProfiles(), loadManufacturers()]);
  const f = { q: "", profile_id: "", manufacturer: "", result: "", from: "", to: "", drafts: false };
  view.innerHTML = `
    <div class="page-head"><div><h2>Inspection history</h2><p>Every inspection is stored locally in SQLite with its measurements, references used, scores and screening result.</p></div>
      <div class="actions"><a class="btn" href="/api/inspections/export.csv"><i data-lucide="download"></i>Export CSV</a><a class="btn btn-primary" href="#/inspection"><i data-lucide="plus"></i>New inspection</a></div></div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-body fields" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      <div class="field"><label for="q">Search</label><input id="q" placeholder="ID, part, manufacturer"></div>
      <div class="field"><label for="pf">Component</label><select id="pf"><option value="">All components</option>${profiles.map((p) => `<option value="${p.id}">${esc(p.short_name || p.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="mf">Manufacturer</label><select id="mf"><option value="">All manufacturers</option>${mfrs.map((m) => `<option>${esc(m.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="rs">Screening status</label><select id="rs"><option value="">Any result</option>${["Reference-consistent", "Investigation recommended", "Significant deviation detected", "Inconclusive"].map((r) => `<option>${r}</option>`).join("")}</select></div>
      <div class="field"><label for="from">From</label><input id="from" type="date"></div>
      <div class="field"><label for="to">To</label><input id="to" type="date"></div>
      <label class="check" style="align-self:end"><input type="checkbox" id="dr">Include drafts and unscored runs</label>
    </div></section>
    <section class="panel"><div class="panel-body flush"><div class="table-wrap" id="tbl"></div></div></section>`;

  async function load() {
    const qs = new URLSearchParams();
    for (const k of ["q", "profile_id", "manufacturer", "result", "from", "to"]) if (f[k]) qs.set(k, f[k]);
    qs.set("drafts", f.drafts ? "1" : "0");
    let rows = await api.get(`/api/inspections?${qs}`);
    if (!f.drafts) rows = rows.filter((r) => r.status === "completed" || r.status === "measured");
    $("#tbl", view).innerHTML = rows.length ? `<table class="t"><thead><tr><th>Inspection</th><th>Date</th><th>Component</th><th>Manufacturer</th><th>Part number</th><th>Mode</th><th>Source</th><th class="num">Overall</th><th>Screening result</th><th></th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="clickable" data-id="${esc(r.id)}"><td class="num"><b>${esc(r.id)}</b><div class="tiny muted">${esc(r.status)}</div></td><td class="small">${dateOf(r.created_at)}</td>
        <td>${esc(r.component_name || "")}</td><td class="small">${esc(r.manufacturer || "—")}</td><td class="num">${esc(r.part_number || "—")}</td><td class="small">${esc(r.mode || "")}</td>
        <td>${sourceTag(r.data_source)}</td><td class="num">${r.overall != null && r.result !== "Inconclusive" ? num(r.overall, 1) : "—"}</td><td>${r.result ? classBadge(r.result) : `<span class="badge b-muted">Not scored</span>`}</td>
        <td><div class="row tight">${r.status === "completed" ? `<a class="btn btn-sm btn-ghost" href="/api/inspections/${esc(r.id)}/report.pdf" target="_blank" rel="noopener" title="Report"><i data-lucide="file-text"></i></a>` : ""}
          <button class="btn btn-sm btn-ghost btn-danger" data-del="${esc(r.id)}" title="Delete"><i data-lucide="trash-2"></i></button></div></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty"><i data-lucide="history"></i><b>No inspections match</b>Adjust the filters or run a new inspection.</div>`;
    $$("#tbl tr.clickable", view).forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.closest("a,button")) return; location.hash = `#/inspection/${tr.dataset.id}`; }));
    $$("[data-del]", view).forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      if (!(await confirmDialog("Delete inspection record?", `${esc(b.dataset.del)} and its measurements and report will be permanently deleted.`, "Delete", true))) return;
      try { await api.del(`/api/inspections/${b.dataset.del}`); toast("Inspection deleted", "ok"); load(); } catch (err) { toast(err.message, "error"); }
    });
    icons(view);
  }
  const bind = (id, key, ev = "change") => $(id, view).addEventListener(ev, (e) => { f[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value; load(); });
  bind("#q", "q", "input"); bind("#pf", "profile_id"); bind("#mf", "manufacturer"); bind("#rs", "result"); bind("#from", "from"); bind("#to", "to"); bind("#dr", "drafts");
  await load();
}
