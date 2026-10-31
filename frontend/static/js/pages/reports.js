import { api } from "../api.js";
import { $, $$, esc, icons, toast, num, dateOf, sourceTag, classBadge } from "../ui.js";

export async function render(view) {
  view.innerHTML = `
    <div class="page-head"><div><h2>Reports</h2><p>Formal PDF inspection reports, generated from stored inspection records. Simulated inspections carry a SIMULATED DATA watermark on every page.</p></div></div>
    <div class="grid"><section class="panel c-5"><div class="panel-head"><h3><i data-lucide="files"></i>Completed inspections</h3></div><div class="panel-body flush" id="list"></div></section>
      <section class="panel c-7"><div class="panel-head"><h3><i data-lucide="eye"></i>Preview</h3><span class="meta" id="pv-actions"></span></div><div class="panel-body" id="pv">
        <div class="empty"><i data-lucide="file-text"></i><b>Select an inspection</b>Generate or open its report.</div></div></section></div>`;
  const [done, reps] = await Promise.all([api.get("/api/inspections?status=completed"), api.get("/api/reports")]);
  const repBy = Object.fromEntries(reps.map((r) => [r.inspection_id, r]));
  $("#list", view).innerHTML = done.length ? `<table class="t"><thead><tr><th>Inspection</th><th>Result</th><th>Report</th></tr></thead><tbody>
    ${done.map((r) => `<tr class="clickable" data-id="${esc(r.id)}"><td><b class="num">${esc(r.id)}</b> ${sourceTag(r.data_source)}<div class="tiny muted">${esc(r.component_name || "")} · ${dateOf(r.created_at)}</div></td>
      <td>${classBadge(r.result)}</td><td class="small">${repBy[r.id] ? `<span class="badge b-pass">${esc(repBy[r.id].id)}</span>` : `<span class="badge b-muted">Not generated</span>`}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty"><i data-lucide="file-x"></i><b>No completed inspections</b>Compute a screening result first.</div>`;
  $$("#list tr.clickable", view).forEach((tr) => tr.onclick = () => select(tr.dataset.id));

  async function select(id) {
    $$("#list tr", view).forEach((t) => t.style.background = t.dataset.id === id ? "var(--primary-weak)" : "");
    const has = !!repBy[id];
    $("#pv-actions", view).innerHTML = `<button class="btn btn-sm btn-primary" id="gen"><i data-lucide="file-cog"></i>${has ? "Regenerate" : "Generate"}</button>
      ${has ? `<a class="btn btn-sm" href="/api/inspections/${esc(id)}/report.pdf?download=1"><i data-lucide="download"></i>Download</a>` : ""}`;
    $("#pv", view).innerHTML = has ? `<iframe class="iframe-pdf" src="/api/inspections/${esc(id)}/report.pdf#view=FitH" title="Report preview"></iframe>`
      : `<div class="empty"><i data-lucide="file-plus"></i><b>No report yet</b>Generate it from the stored record.</div>`;
    $("#gen", view).onclick = async () => {
      try { const r = await api.post(`/api/inspections/${id}/report`); repBy[id] = { id: r.report_id }; toast("Report generated", "ok"); select(id); }
      catch (e) { toast(e.message, "error"); }
    };
    icons(view);
  }
  if (done.length) select(done[0].id);
  icons(view);
}
