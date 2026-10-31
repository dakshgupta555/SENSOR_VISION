import { api } from "../api.js";
import { store, loadSimProfiles, loadReferences } from "../store.js";
import { $, $$, esc, icons, toast, num } from "../ui.js";
import { ocrUpload, ocrSample, imageRecord, identStatusBadge, candidatesHTML, wordsHTML, ocrUnavailableHTML, reidentify } from "../shared.js";

const CHECKS = ["Marking readability", "Logo consistency", "Part-number consistency", "Package appearance", "Visible physical damage",
  "Pin alignment", "Surface irregularities", "Marking placement"];

export async function render(view) {
  const [sims, refs] = await Promise.all([loadSimProfiles(), loadReferences()]);
  const ocrStatus = await api.get("/api/ocr/status");
  const st = { o: null, text: "", ident: null, refId: null, stage: "body", compare: null };

  view.innerHTML = `
    <div class="page-head"><div><h2>Optical inspection</h2><p>Image preprocessing, marking OCR and comparison with stored reference images. Manual image input — camera capture plugs in here later.</p></div>
      <div class="actions"><span class="badge ${ocrStatus.available ? "b-pass" : "b-fail"}">${ocrStatus.available ? esc(ocrStatus.engine) : "Tesseract not found"}</span></div></div>
    ${ocrStatus.available ? "" : `<div class="callout error" style="margin-bottom:16px"><i data-lucide="alert-octagon"></i><div><b>${esc(ocrStatus.reason)}</b><br>${esc(ocrStatus.install)}<br>
      <span class="small">Until it is installed, enter the marking text manually below. No OCR output is substituted.</span></div></div>`}
    <div class="grid">
      <section class="panel c-4"><div class="panel-head"><h3><i data-lucide="image-plus"></i>Image input</h3></div>
        <div class="panel-body stack" style="gap:10px">
          <label class="btn btn-primary"><i data-lucide="upload"></i>Upload package photo<input type="file" accept="image/*" id="file" hidden></label>
          ${store.isSim() ? `<div class="row tight"><select id="sample" style="flex:1">${sims.map((s) => `<option value="${s.id}">${esc(s.label)}</option>`).join("")}</select>
            <button class="btn" id="sample-btn"><i data-lucide="flask-conical"></i>Load</button></div>` : ""}
          <div class="callout info"><i data-lucide="camera"></i><div class="small">Future camera integration: frames will feed the same preprocessing and OCR pipeline.</div></div>
          <div class="field"><label for="manual">Marking text (manual entry or correction)</label><textarea id="manual" rows="3" placeholder="e.g. SN74HC02N"></textarea></div>
          <button class="btn" id="ident-btn"><i data-lucide="search"></i>Identify from text</button>
        </div></section>
      <section class="panel c-8"><div class="panel-head"><h3><i data-lucide="layers"></i>Preprocessing stages</h3><span class="meta" id="img-meta"></span></div>
        <div class="panel-body" id="stages"><div class="empty"><i data-lucide="image"></i><b>No image yet</b>Upload a photo of the package top.</div></div></section>
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="type"></i>Extracted text</h3><span class="meta" id="conf"></span></div><div class="panel-body" id="ocr"></div></section>
      <section class="panel c-6"><div class="panel-head"><h3><i data-lucide="fingerprint"></i>Identification</h3><span class="meta" id="id-badge"></span></div><div class="panel-body" id="ident"></div></section>
      <section class="panel c-8"><div class="panel-head"><h3><i data-lucide="git-compare"></i>Reference image comparison</h3></div><div class="panel-body" id="compare"></div></section>
      <section class="panel c-4"><div class="panel-head"><h3><i data-lucide="list-checks"></i>Inspection parameters</h3></div>
        <div class="panel-body"><ul class="small" style="margin:0;padding-left:18px">${CHECKS.map((c) => `<li>${c}</li>`).join("")}</ul>
          <p class="tiny muted">Readability, part-number consistency and reference similarity are computed; the rest are assessed by the operator during an inspection. Visual checks reveal visible inconsistencies — they cannot prove electrical genuineness.</p>
          <button class="btn btn-primary btn-block" id="use" disabled><i data-lucide="arrow-right"></i>Use in a new inspection</button></div></section>
    </div>`;

  function paint() {
    const o = st.o;
    if (o) {
      const stages = [["original", "Original"], ["body", "Package body"], ["contrast", "Contrast"], ["otsu", "Otsu threshold"], ["adaptive", "Adaptive threshold"]];
      $("#img-meta", view).innerHTML = o.synthetic ? `<span class="sim-tag">SYNTHETIC SAMPLE</span>` : "Uploaded photo";
      $("#stages", view).innerHTML = `<div class="seg stage-tabs" id="stg">${stages.map(([k, l]) => `<button data-k="${k}" class="${k === st.stage ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class="img-frame ${st.stage === "original" ? "" : "light"}"><img src="/api/images/${esc(o.image_id)}/${st.stage}" alt="${esc(st.stage)}"></div>
        <p class="tiny muted" style="margin:8px 0 0">Grayscale → CLAHE contrast enhancement → bilateral denoise → Otsu and adaptive thresholds. The package body is located first so leads and background do not feed OCR noise.</p>`;
      $$("#stg button", view).forEach((b) => b.onclick = () => { st.stage = b.dataset.k; paint(); });
      $("#conf", view).textContent = o.mean_conf != null ? `mean confidence ${num(o.mean_conf, 0)} (engine-reported)` : "";
      $("#ocr", view).innerHTML = (o.available === false ? ocrUnavailableHTML(o) : `<pre class="mono" style="margin:0 0 10px;white-space:pre-wrap">${esc(o.text || "No legible text")}</pre>${wordsHTML(o)}
        <details style="margin-top:10px"><summary class="small muted">OCR passes (${(o.attempts || []).length})</summary><table class="t compact"><thead><tr><th>Image</th><th>PSM</th><th class="num">Conf</th><th class="num">DB match</th><th>Text</th></tr></thead><tbody>
        ${(o.attempts || []).map((a) => `<tr><td>${esc(a.variant)}</td><td class="num">${a.psm}</td><td class="num">${a.mean_conf ?? "—"}</td><td class="num">${a.db_match ?? "—"}</td><td class="mono small">${esc((a.text || a.error || "").replace(/\n/g, " / "))}</td></tr>`).join("")}</tbody></table></details>`);
    }
    $("#id-badge", view).innerHTML = st.ident ? identStatusBadge(st.ident) : "";
    $("#ident", view).innerHTML = st.ident ? `<p class="small" style="margin-top:0">${esc(st.ident.message)}</p><div id="cands">${candidatesHTML(st.ident, st.refId)}</div>
      ${st.ident.manufacturer_hints.length ? `<p class="small muted">Manufacturer hints: ${st.ident.manufacturer_hints.map((h) => `${esc(h.name)} (${esc(h.evidence)})`).join(", ")}</p>` : ""}
      ${st.ident.date_codes.length ? `<p class="small muted">Possible date code: ${st.ident.date_codes.map((d) => `${d.code} (${d.year} week ${d.week})`).join(", ")}</p>` : ""}`
      : `<div class="muted small">Waiting for marking text.</div>`;
    $$("#cands .cand", view).forEach((el) => el.onclick = () => { st.refId = el.dataset.ref; paint(); compare(); });
    const r = refs.find((x) => x.id === st.refId);
    $("#use", view).disabled = !(st.o || st.ident);
    const c = st.compare;
    $("#compare", view).innerHTML = !st.o ? `<div class="muted small">Load an image to compare.</div>`
      : c === null ? `<div class="loading"><i data-lucide="loader-2" class="spin"></i>Comparing…</div>`
        : !c ? `<div class="callout info"><i data-lucide="image-off"></i><div>No reference image is stored for ${r ? esc(r.part_number) : "this part"}. Record a known-good sample during an inspection to store one. Similarity is reported as not assessed — never as a pass.</div></div>`
          : `<div class="grid"><div class="c-6"><div class="label">Inspection image</div><div class="img-frame"><img src="/api/images/${esc(st.o.image_id)}" alt="Inspection"></div></div>
            <div class="c-6"><div class="label">Reference image (${esc(c.provenance)})</div><div class="img-frame">${c.reference_url ? `<img src="${esc(c.reference_url)}" alt="Reference">` : `<span class="muted">stored reference</span>`}</div></div>
            <div class="c-12 row"><span class="badge ${c.ssim >= 0.75 ? "b-pass" : c.ssim >= 0.55 ? "b-warn" : "b-fail"} num">SSIM ${num(c.ssim, 2)}</span>
              <span class="small muted">${esc(c.method)} · ${c.aligned ? `${c.inlier_matches} inlier feature matches` : "alignment failed — unaligned comparison"}. A similar image does not prove authenticity.</span></div></div>`;
    icons(view);
  }

  async function compare() {
    if (!st.o) return;
    st.compare = null; paint();
    const r = refs.find((x) => x.id === st.refId);
    try {
      st.compare = await api.post("/api/optical/compare", { image_id: st.o.image_id, reference_id: st.refId, profile_id: r ? r.profile_id : null,
        sim_profile_id: st.o.synthetic ? st.o.sim_profile_id : null }) || false;
    } catch (e) { st.compare = false; }
    paint();
  }

  async function run(fn) {
    $("#stages", view).innerHTML = `<div class="loading"><i data-lucide="loader-2" class="spin"></i>Preprocessing and running OCR…</div>`;
    icons(view);
    try {
      st.o = await fn();
      st.ident = st.o.identification;
      st.text = st.o.text || "";
      $("#manual", view).value = st.text;
      const top = st.ident && st.ident.candidates[0];
      st.refId = top && st.ident.status !== "ambiguous" ? top.reference_id : (st.o.sim_profile && st.ident.status !== "ambiguous" ? st.o.sim_profile.reference_id : null);
      paint(); compare();
    } catch (e) { toast(e.message, "error"); }
  }

  $("#file", view).onchange = (e) => { const f = e.target.files[0]; if (f) run(() => ocrUpload(f)); };
  if ($("#sample-btn", view)) $("#sample-btn", view).onclick = () => run(() => ocrSample($("#sample", view).value));
  $("#ident-btn", view).onclick = async () => {
    st.text = $("#manual", view).value;
    try { st.ident = await reidentify(st.text); const top = st.ident.candidates[0]; st.refId = top && st.ident.status !== "ambiguous" ? top.reference_id : null; paint(); if (st.o) compare(); }
    catch (e) { toast(e.message, "error"); }
  };
  $("#use", view).onclick = () => {
    const r = refs.find((x) => x.id === st.refId);
    const handoff = { ocr: st.o ? { ...st.o, identification: st.ident } : null, image: st.o ? imageRecord(st.o) : null, text: st.text, ident: st.ident,
      refId: st.refId, profileId: r ? r.profile_id : (st.o && st.o.sim_profile ? st.o.sim_profile.component_profile_id : null),
      simId: st.o && st.o.sim_profile_id };
    sessionStorage.setItem("icv.handoff", JSON.stringify(handoff));
    location.hash = "#/inspection";
  };
  paint();
}
