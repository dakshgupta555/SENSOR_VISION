import { api } from "../api.js";
import { store, loadProfiles, loadSimProfiles, loadReferences } from "../store.js";
import { $, $$, esc, icons, toast, num, confirmDialog, chipSVG, roleLegend, categoryLabel, classBadge } from "../ui.js";
import { categoryOptions } from "../shared.js";

export async function render(view) {
  let [profiles, sims, refs] = await Promise.all([loadProfiles(true), loadSimProfiles(), loadReferences()]);
  const st = { cat: "", sel: profiles[0] && profiles[0].id };

  view.innerHTML = `
    <div class="page-head"><div><h2>Component library</h2><p>Component test profiles: pinout, supply limits, functional test definition, supported measurements and limitations. The system decides which tests apply from the selected profile.</p></div>
      <div class="actions"><select id="cat" style="width:auto">${categoryOptions("")}</select><a class="btn btn-primary" href="#/inspection"><i data-lucide="plus"></i>Add custom component</a></div></div>
    <div class="split">
      <section class="panel"><div class="list" id="list"></div></section>
      <div class="stack" id="detail"></div>
    </div>
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h3><i data-lucide="flask-conical"></i>Simulation library</h3><span class="meta">All data in these profiles is dummy data, labelled SIMULATED</span></div>
      <div class="panel-body flush"><div class="table-wrap"><table class="t"><thead><tr><th>Profile</th><th>Component</th><th>Scenario</th><th>Synthetic marking</th><th>Expected screening outcome</th><th></th></tr></thead>
      <tbody>${sims.map((s) => `<tr><td><b>${esc(s.label)}</b><div class="tiny muted">${esc(s.description)}</div></td><td class="small">${esc((profiles.find((p) => p.id === s.component_profile_id) || {}).short_name || s.component_profile_id)}</td>
        <td><span class="badge b-muted">${esc(s.scenario.replace(/_/g, " "))}</span></td><td class="mono small">${esc([s.marking.logo, ...s.marking.lines].filter(Boolean).join(" / "))}</td>
        <td>${classBadge(s.expected_result)}</td><td>${store.isSim() ? `<button class="btn btn-sm" data-sim="${s.id}"><i data-lucide="play"></i>Load in simulator</button>` : `<span class="tiny muted">Switch to simulation mode</span>`}</td></tr>`).join("")}</tbody></table></div></div></section>`;

  function paintList() {
    $("#list", view).innerHTML = profiles.filter((p) => !st.cat || p.category === st.cat).map((p) => `
      <div class="list-item ${p.id === st.sel ? "on" : ""}" data-id="${esc(p.id)}"><div class="row tight"><span class="li-title">${esc(p.short_name || p.name)}</span><span class="spacer"></span>
        ${p.physical_demo ? `<span class="badge b-cyan">Physical demo</span>` : ""}${p.builtin ? "" : `<span class="badge b-muted">Custom</span>`}</div>
        <div class="li-sub">${esc(categoryLabel(p.category))} · ${esc(p.package)} · ${p.functional_test ? esc(p.functional_test.type.replace(/_/g, " ")) : "supply only"}</div></div>`).join("");
    $$("#list .list-item", view).forEach((el) => el.onclick = () => { st.sel = el.dataset.id; paintList(); paintDetail(); });
  }

  async function paintDetail() {
    const p = profiles.find((x) => x.id === st.sel);
    if (!p) { $("#detail", view).innerHTML = ""; return; }
    let baselines = [];
    try { baselines = (await api.getFull(`/api/profiles/${p.id}`)).baselines || []; } catch (e) { /* ignore */ }
    const ref = refs.find((r) => r.id === p.default_reference_id);
    const ft = p.functional_test;
    $("#detail", view).innerHTML = `
      <section class="panel"><div class="panel-head"><h3><i data-lucide="cpu"></i>${esc(p.name)}</h3>
        <span class="meta">${p.builtin ? "Built-in profile" : `<button class="btn btn-sm btn-danger" id="del"><i data-lucide="trash-2"></i>Delete</button>`}</span></div>
        <div class="panel-body grid">
          <div class="c-5">${chipSVG(p, { marking: p.base_part })}${roleLegend()}</div>
          <div class="c-7"><div class="kv">
            <div>Category</div><div>${esc(categoryLabel(p.category))}</div>
            <div>Package</div><div>${esc(p.package)}</div>
            <div>Supply limits</div><div class="num">${p.supply.min_v ?? "?"} – ${p.supply.max_v ?? "?"} V · rails ${p.supply.allowed_rails.map((r) => r + " V").join(", ")}</div>
            <div>Supply pins</div><div class="num">VCC ${p.supply.vcc_pins.join(", ")} · GND ${p.supply.gnd_pins.join(", ")}</div>
            <div>Reference record</div><div>${ref ? `<a href="#/manufacturers">${esc(ref.part_number)}</a> — ${esc(ref.manufacturer)}` : `<span class="empty-val">None</span>`}</div>
            <div>Functional test</div><div class="small">${ft ? esc(ft.description || `${ft.function} truth table, ${ft.gates.length} gates × ${ft.vectors.length} vectors`) : "Not defined — functional test not configured"}</div>
            <div>Supported measurements</div><div>${p.supported_measurements.map((m) => `<span class="chip">${esc(m.replace(/_/g, " "))}</span>`).join(" ")}</div>
            ${p.custom ? `<div>Datasheet URL</div><div class="small">${p.custom.datasheet_url ? `<a href="${esc(p.custom.datasheet_url)}" target="_blank" rel="noopener">${esc(p.custom.datasheet_url)}</a>` : "—"}</div>` : ""}
          </div></div></div></section>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="ruler"></i>Evaluated parameters</h3></div>
        <div class="panel-body flush"><table class="t"><thead><tr><th>Parameter</th><th>Method</th><th class="num">Weight</th><th>Mandatory</th><th>Reference used</th></tr></thead><tbody>
        ${p.evaluation.map((e) => `<tr><td>${esc(e.label)}</td><td class="small">${esc(e.kind.replace(/_/g, " "))}</td><td class="num">${num(e.weight, 2)}</td><td>${e.mandatory ? "Yes" : "No"}</td>
          <td class="small">${esc(e.spec || (e.spec_order || []).join(", ") || (e.limits ? "profile criterion" : "test definition"))}${e.critical_multiple ? ` · critical above ${e.critical_multiple}× limit` : ""}${e.critical_on_fail ? " · critical on fail" : ""}</td></tr>`).join("")}</tbody></table></div></section>
      ${ft && ft.type === "logic_truth_table" ? `<section class="panel"><div class="panel-head"><h3><i data-lucide="binary"></i>${esc(ft.function)} truth table</h3></div><div class="panel-body grid">
        <div class="c-4"><table class="t compact truth"><thead><tr><th>A</th><th>B</th><th>Expected Y</th></tr></thead><tbody>${ft.vectors.map((v) => `<tr><td class="num">${v[0]}</td><td class="num">${v[1]}</td><td class="num">${v[2]}</td></tr>`).join("")}</tbody></table></div>
        <div class="c-8"><table class="t compact"><thead><tr><th>Gate</th><th>Input pins</th><th>Output pin</th></tr></thead><tbody>${ft.gates.map((g) => `<tr><td>${esc(g.name)}</td><td class="num">${g.inputs.join(", ")}</td><td class="num">${g.output}</td></tr>`).join("")}</tbody></table>
          <p class="tiny muted">Pin mapping comes from this profile; the fixture map in Device Settings converts DUT pins to ESP32 GPIOs.</p></div></div></section>` : ""}
      <div class="grid">
        <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="list-checks"></i>Preconditions</h3></div><div class="panel-body"><ul class="small" style="margin:0;padding-left:18px">${(p.preconditions || []).map((x) => `<li>${esc(x.label)}</li>`).join("")}</ul></div></section>
        <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="triangle-alert"></i>Test limitations</h3></div><div class="panel-body"><ul class="small" style="margin:0;padding-left:18px">${(p.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></section>
      </div>
      <section class="panel"><div class="panel-head"><h3><i data-lucide="bookmark"></i>Known-good baselines</h3><span class="meta">Empirical references recorded from inspections</span></div>
        <div class="panel-body flush">${baselines.length ? `<table class="t"><thead><tr><th>Parameter</th><th class="num">Mean</th><th class="num">σ</th><th class="num">n</th><th>Conditions</th><th>Source</th><th></th></tr></thead><tbody>
          ${baselines.map((b) => `<tr><td>${esc(b.param.replace(/_/g, " "))}</td><td class="num">${b.mean != null ? num(b.mean, 3) + " " + esc(b.unit) : "curve"}</td><td class="num">${b.std != null ? num(b.std, 3) : "—"}</td><td class="num">${b.n}</td>
            <td class="small">${esc(b.conditions.rail_v)} V${b.conditions.clock ? ", " + esc(b.conditions.clock) : ""}</td><td>${b.data_source === "simulated" ? `<span class="sim-tag">SIMULATED</span>` : `<span class="live-tag">LIVE</span>`} <a class="small" href="#/inspection/${esc(b.source_inspection_id)}">${esc(b.source_inspection_id)}</a></td>
            <td><button class="btn btn-sm btn-ghost" data-bdel="${b.id}" aria-label="Delete baseline"><i data-lucide="trash-2"></i></button></td></tr>`).join("")}</tbody></table>`
          : `<div class="empty"><i data-lucide="bookmark"></i><b>No baselines</b>After inspecting a verified genuine sample, use “Record as known-good baseline” in the analysis step.</div>`}</div></section>`;
    if ($("#del", view)) $("#del", view).onclick = async () => {
      if (!(await confirmDialog("Delete custom profile?", `${esc(p.name)} will be removed. Existing inspections keep their stored data.`, "Delete", true))) return;
      try { await api.del(`/api/profiles/${p.id}`); profiles = await loadProfiles(true); st.sel = profiles[0].id; paintList(); paintDetail(); } catch (e) { toast(e.message, "error"); }
    };
    $$("[data-bdel]", view).forEach((b) => b.onclick = async () => {
      if (!(await confirmDialog("Delete baseline?", "Future inspections will no longer compare against it.", "Delete", true))) return;
      await api.del(`/api/baselines/${b.dataset.bdel}`); paintDetail();
    });
    icons($("#detail", view));
  }

  $("#cat", view).onchange = (e) => { st.cat = e.target.value; paintList(); };
  $$("[data-sim]", view).forEach((b) => b.onclick = async () => {
    try { await api.post("/api/sim/select", { profile_id: b.dataset.sim }); location.hash = "#/live"; } catch (e) { toast(e.message, "error"); }
  });
  paintList();
  await paintDetail();
}
