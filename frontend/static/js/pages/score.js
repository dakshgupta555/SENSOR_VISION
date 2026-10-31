import { api } from "../api.js";
import { $, esc, icons, toast, num, sourceTag } from "../ui.js";
import { scoreView } from "./inspection.js";

export async function render(view, params) {
  const status = await api.get("/api/status");
  let settings = status.settings;
  const list = await api.get("/api/inspections?status=completed");
  let sel = params[0] || (list[0] && list[0].id) || null;

  view.innerHTML = `
    <div class="page-head"><div><h2>Trust score engine</h2><p>A transparent weighted model. Every input, weight and override is shown; missing evidence is excluded rather than counted as a pass.</p></div></div>
    <div class="grid">
      <section class="panel c-5"><div class="panel-head"><h3><i data-lucide="sliders"></i>Weights and thresholds</h3></div>
        <div class="panel-body stack" style="gap:12px" id="cfg"></div></section>
      <section class="panel c-7"><div class="panel-head"><h3><i data-lucide="book-open"></i>Method</h3></div>
        <div class="panel-body small">
          <div class="formula" style="margin-bottom:12px">S = (w<sub>E</sub>·S<sub>e</sub> + w<sub>O</sub>·S<sub>v</sub> + w<sub>M</sub>·S<sub>m</sub>) / Σw<sub>available</sub></div>
          <p>With all three dimensions present this is exactly <b>S = 0.60·Se + 0.25·Sv + 0.15·Sm</b>. Each dimension score is 0–100.</p>
          <table class="t compact"><thead><tr><th>Dimension</th><th>How it is scored</th></tr></thead><tbody>
            <tr><td>Electrical S<sub>e</sub></td><td>Weighted mean of the profile's parameters (supply condition, supply current vs datasheet limit or known-good baseline, stability and drift, truth table / heartbeat / signature / follower, output levels, pin I-V). Parameter-specific tolerances; sensor resolution is taken into account.</td></tr>
            <tr><td>Optical S<sub>v</sub></td><td>Weighted checklist: readability and part-number consistency from OCR, similarity to a stored reference image, and operator visual checks. Pass 100, warning 50, fail 0.</td></tr>
            <tr><td>Manufacturer S<sub>m</sub></td><td>Reference-data consistency: datasheet record, part number on marking, manufacturer identity, verification status of the values used, type-number currency (legacy types vs date code). Not a rating of any manufacturer.</td></tr></tbody></table>
          <div class="hr"></div>
          <p style="margin:0 0 6px"><b>Missing-data rules</b></p>
          <ul style="margin:0;padding-left:18px">
            <li>Not measured, no reference and not applicable parameters are excluded from scoring — never counted as passes.</li>
            <li>Invalid samples (sensor overflow, out of range) are dropped before statistics.</li>
            <li>A mandatory parameter without evidence makes the electrical dimension inconclusive.</li>
            <li>An invalid test condition (wrong supply rail) makes the result inconclusive.</li>
            <li>Critical findings (truth-table mismatch, wrong device signature, supply current far above the datasheet maximum) override the threshold and give “Significant deviation detected”.</li>
            <li>Electrical evidence is required for a screening result unless disabled below; coverage below the minimum gives “Inconclusive”.</li></ul>
          <p class="muted" style="margin:10px 0 0">The output is a <b>screening result</b>, not a genuine/counterfeit verdict.</p></div></section>
      <section class="c-12 stack" id="calc"></section>
    </div>`;

  function paintCfg() {
    const w = settings.weights, s = settings.scoring;
    $("#cfg", view).innerHTML = `
      <div class="fields">${[["electrical", "Electrical"], ["optical", "Optical"], ["manufacturer", "Manufacturer"]].map(([k, l]) =>
        `<div class="field"><label for="w-${k}">${l} weight</label><input id="w-${k}" type="number" min="0" max="1" step="0.05" value="${w[k]}"></div>`).join("")}</div>
      <div class="small" id="w-sum"></div>
      <div class="fields">
        <div class="field"><label for="t-c">Reference-consistent from</label><input id="t-c" type="number" min="1" max="100" value="${s.threshold_consistent}"></div>
        <div class="field"><label for="t-i">Investigation recommended from</label><input id="t-i" type="number" min="0" max="99" value="${s.threshold_investigate}"></div>
        <div class="field"><label for="t-cov">Minimum evidence coverage</label><input id="t-cov" type="number" min="0" max="1" step="0.05" value="${s.min_coverage}"></div>
      </div>
      <label class="check"><input type="checkbox" id="t-req" ${s.require_electrical ? "checked" : ""}>Require electrical evidence for a screening result</label>
      <div class="row"><button class="btn btn-primary" id="save"><i data-lucide="save"></i>Save configuration</button>
        <button class="btn btn-ghost" id="defaults">Restore 0.60 / 0.25 / 0.15</button></div>`;
    const sum = () => {
      const t = ["electrical", "optical", "manufacturer"].reduce((a, k) => a + (+$(`#w-${k}`, view).value || 0), 0);
      $("#w-sum", view).innerHTML = Math.abs(t - 1) < 0.001 ? `<span style="color:var(--pass)">Weights sum to 1.00</span>` : `<span style="color:var(--fail)">Weights sum to ${t.toFixed(2)} — they must sum to 1.00</span>`;
      $("#save", view).disabled = Math.abs(t - 1) >= 0.001;
    };
    ["electrical", "optical", "manufacturer"].forEach((k) => $(`#w-${k}`, view).oninput = sum);
    sum();
    $("#defaults", view).onclick = () => { $("#w-electrical", view).value = 0.6; $("#w-optical", view).value = 0.25; $("#w-manufacturer", view).value = 0.15; sum(); };
    $("#save", view).onclick = async () => {
      try {
        settings = await api.put("/api/settings", {
          weights: { electrical: +$("#w-electrical", view).value, optical: +$("#w-optical", view).value, manufacturer: +$("#w-manufacturer", view).value },
          scoring: { threshold_consistent: +$("#t-c", view).value, threshold_investigate: +$("#t-i", view).value, min_coverage: +$("#t-cov", view).value,
            require_electrical: $("#t-req", view).checked } });
        toast("Scoring configuration saved. Recompute an inspection to apply it.", "ok");
      } catch (e) { toast(e.message, "error"); }
    };
  }

  async function paintCalc() {
    const box = $("#calc", view);
    if (!sel) { box.innerHTML = `<section class="panel"><div class="panel-body"><div class="empty"><i data-lucide="gauge"></i><b>No completed inspection yet</b>Run an inspection to see its score calculation here.</div></div></section>`; icons(box); return; }
    const ins = await api.get(`/api/inspections/${sel}`);
    box.innerHTML = `<section class="panel"><div class="panel-body row"><div class="field" style="min-width:320px"><label for="sel">Inspection</label>
        <select id="sel">${list.map((r) => `<option value="${r.id}" ${r.id === sel ? "selected" : ""}>${esc(r.id)} — ${esc(r.component_name || "")} (${esc(r.data_source)})</option>`).join("")}</select></div>
        <span>${sourceTag(ins.data_source)}</span><span class="small muted">Weights used: ${["electrical", "optical", "manufacturer"].map((k) => `${k} ${num(ins.scores.weights[k], 2)}`).join(", ")}</span>
        <span class="spacer"></span><button class="btn" id="recompute"><i data-lucide="refresh-cw"></i>Recompute with current configuration</button>
        <a class="btn btn-ghost" href="#/inspection/${esc(ins.id)}"><i data-lucide="external-link"></i>Open inspection</a></div></section>` + scoreView(ins);
    $("#sel", view).onchange = (e) => { sel = e.target.value; paintCalc(); };
    $("#recompute", view).onclick = async () => {
      try { await api.post(`/api/inspections/${sel}/finalize`, { optical_manual: ins.optical_manual }); toast("Recomputed", "ok"); paintCalc(); }
      catch (e) { toast(e.message, "error"); }
    };
    icons(box);
  }

  paintCfg();
  await paintCalc();
}
