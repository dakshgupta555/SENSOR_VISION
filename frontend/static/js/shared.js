// Shared building blocks used by several pages: OCR calls, identification display, profile pickers.
import { api } from "./api.js";
import { store } from "./store.js";
import { esc, num, categoryLabel } from "./ui.js";

export async function ocrUpload(file) {
  const fd = new FormData();
  fd.append("image", file);
  return api.upload("/api/ocr", fd);
}

export async function ocrSample(simProfileId) {
  const j = await api.postFull("/api/ocr/sample", { sim_profile_id: simProfileId });
  return { ...j.data, sim_profile: j.sim_profile };
}

export async function reidentify(text) {
  return api.post("/api/identify", { text });
}

// The subset of an OCR result stored on the inspection record.
export function ocrRecord(o, editedText) {
  if (!o) return null;
  return {
    engine: o.engine, available: o.available, text: o.text, lines: o.lines, words: o.words, mean_conf: o.mean_conf,
    variant: o.variant, psm: o.psm, attempts: o.attempts, identification: o.identification, image_id: o.image_id,
    edited_text: editedText !== undefined && editedText !== o.text ? editedText : undefined,
    confidence_source: o.confidence_source, error: o.error, min_word_conf: o.min_word_conf,
  };
}

export function imageRecord(o) {
  if (!o) return null;
  return { image_id: o.image_id, source: o.synthetic ? "sample" : "upload", synthetic: !!o.synthetic, sim_profile_id: o.sim_profile_id || null };
}

export function identStatusBadge(ident) {
  if (!ident) return `<span class="badge b-muted">Not identified</span>`;
  const m = { identified: ["b-pass", "Identified"], ambiguous: ["b-warn", "Confirm manufacturer"], weak: ["b-warn", "Weak match"],
    no_match: ["b-fail", "No database match"], empty: ["b-muted", "No text"] }[ident.status] || ["b-muted", ident.status];
  return `<span class="badge ${m[0]}">${m[1]}</span>`;
}

export function candidatesHTML(ident, selectedRef) {
  if (!ident || !ident.candidates || !ident.candidates.length) {
    return `<div class="callout warn"><i data-lucide="search-x"></i><div>${esc(ident && ident.message || "No candidate part numbers.")}
      Select the component profile manually, or add the part to the manufacturer database.</div></div>`;
  }
  return ident.candidates.slice(0, 5).map((c) => `
    <label class="cand ${c.reference_id === selectedRef ? "on" : ""}" data-ref="${esc(c.reference_id)}">
      <input type="radio" name="cand" value="${esc(c.reference_id)}" ${c.reference_id === selectedRef ? "checked" : ""}>
      <div><div class="pn">${esc(c.part_number)}</div>
        <div class="tiny muted">${esc(c.manufacturer)} · ${esc(c.match_type)} on “${esc(c.matched_token)}”${c.legacy_type ? " · legacy type" : ""}</div></div>
      <span class="badge ${c.score >= 0.9 ? "b-pass" : c.score >= 0.8 ? "b-info" : "b-warn"} num">${num(c.score, 2)}</span>
    </label>`).join("");
}

export function wordsHTML(o) {
  if (!o || !o.words || !o.words.length) return `<span class="muted small">No words recognised.</span>`;
  return `<div class="words">${o.words.map((w) => `<span class="word ${w.kept ? "" : "low"}" title="Tesseract confidence ${w.conf}">${esc(w.text)}<small>${Math.round(w.conf)}</small></span>`).join("")}</div>`;
}

export function ocrUnavailableHTML(o) {
  return `<div class="callout error"><i data-lucide="alert-octagon"></i><div><b>OCR engine unavailable.</b> ${esc(o.error || "")}<br>
    <span class="small">${esc(o.install || "")}</span><br><span class="small">Manual identification below remains fully functional. No OCR text was substituted.</span></div></div>`;
}

export function profileOptions(profiles, selected, category) {
  return profiles.filter((p) => !category || p.category === category)
    .map((p) => `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.name)}${p.physical_demo ? " — physical demo" : ""}</option>`).join("");
}

export function categoryOptions(selected) {
  return [["", "All categories"], ["logic_ic", "Logic IC"], ["microcontroller", "Microcontroller"], ["op_amp", "Operational amplifier"], ["custom", "Custom / unidentified"]]
    .map(([v, l]) => `<option value="${v}" ${v === (selected || "") ? "selected" : ""}>${l}</option>`).join("");
}

export function referencesFor(refs, profile) {
  if (!profile) return [];
  return refs.filter((r) => r.profile_id === profile.id || (profile.base_part && r.base_part === profile.base_part));
}

export function simProfileFor(profileId) {
  return (store.simProfiles || []).find((s) => s.component_profile_id === profileId);
}

export { categoryLabel };
