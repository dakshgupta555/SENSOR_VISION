"""Optical checks, manufacturer-reference verification and the weighted trust-score engine.

S = (wE·Se + wO·Sv + wM·Sm) / (sum of weights of the dimensions that produced a score)

With every dimension available this is exactly S = 0.60·Se + 0.25·Sv + 0.15·Sm. A dimension that
was not performed or lacks evidence is excluded (never counted as a pass) and the evidence
coverage drops; low coverage or missing electrical evidence makes the result INCONCLUSIVE.
"""
from . import evaluation
from .identify import clean

CLASSES = {
    "consistent": "Reference-consistent",
    "investigate": "Investigation recommended",
    "deviation": "Significant deviation detected",
    "inconclusive": "Inconclusive",
}


# ------------------------------------------------------------------ optical
def build_optical_checks(ins, profile, manual, compare):
    """manual: {key: {"status": pass|warn|fail|not_assessed, "note": str}} from the operator (or simulation profile)."""
    ocr = ins.get("ocr") or {}
    ident = ocr.get("identification") or {}
    has_image = bool((ins.get("image") or {}).get("image_id"))
    checks = []
    for c in evaluation.OPTICAL_CHECKS:
        item = {"key": c["key"], "label": c["label"], "weight": c["weight"], "auto": c["auto"],
                "status": "not_assessed", "observed": "", "reference": "", "note": ""}
        if c["key"] == "marking_readability":
            if not has_image:
                item["note"] = "No image"
            elif not ocr.get("available", False) and not ocr.get("edited_text"):
                item["note"] = "OCR engine unavailable"
            else:
                conf = ocr.get("mean_conf")
                item["observed"] = f"Tesseract mean word confidence {conf:.0f}" if conf is not None else "No words recognised"
                item["reference"] = "Identifiable part number, confidence ≥ 70"
                if conf is not None and conf >= 70 and ident.get("status") in ("identified", "ambiguous"):
                    item["status"] = "pass"
                else:
                    item["status"] = "warn"
                    item["note"] = "Marking only partly machine-readable" if ident.get("candidates") else "Marking not machine-readable"
        elif c["key"] == "part_number_consistency":
            claimed = (profile or {}).get("base_part") or ""
            cands = ident.get("candidates") or []
            item["reference"] = f"Configured profile: {claimed or 'none'}"
            if not cands:
                item["note"] = "Part number not identified from marking" if has_image else "No image"
            elif not claimed:
                item["note"] = "No claimed part to compare with"
            else:
                top = cands[0]
                item["observed"] = f'Marking "{top["matched_token"]}" → {top["part_number"]}'
                if clean(top.get("base_part") or "") == clean(claimed):
                    item["status"] = "pass" if top["score"] >= 0.8 else "warn"
                    item["note"] = f"{top['match_type']} match"
                else:
                    item["status"] = "fail"
                    item["note"] = f"Marking identifies {top['base_part']}, inspection configured for {claimed}"
        elif c["key"] == "reference_image_similarity":
            if compare and compare.get("ssim") is not None:
                s = compare["ssim"]
                item["observed"] = f"SSIM {s:.2f}" + (" (aligned)" if compare.get("aligned") else " (unaligned)")
                item["reference"] = f"Reference image ({compare.get('provenance', 'stored')})"
                item["status"] = "pass" if s >= 0.75 else ("warn" if s >= 0.55 else "fail")
                item["note"] = "Indicative only — lighting and angle affect similarity"
            else:
                item["note"] = "No reference image stored for this part" if has_image else "No image"
        else:
            m = (manual or {}).get(c["key"]) or {}
            st = m.get("status", "not_assessed")
            item["status"] = st if st in ("pass", "warn", "fail") else "not_assessed"
            item["note"] = m.get("note", "")
            item["observed"] = m.get("note", "")
            item["reference"] = "Operator visual assessment" + (" (simulated)" if m.get("simulated") else "")
        checks.append(item)
    return checks


# ------------------------------------------------------------------ manufacturer verification
def manufacturer_verification(ins, reference, evals):
    comp = ins.get("component") or {}
    ident = (ins.get("ocr") or {}).get("identification") or {}
    out = {"score": None, "status": "reference_unavailable", "factors": [], "reasons": [], "notes": []}
    if not reference:
        out["reasons"] = ["Reference unavailable — no matching manufacturer record in the local database"]
        return out
    ds = reference.get("datasheet") or {}
    factors = []
    # 1. datasheet record
    if ds.get("url"):
        factors.append({"key": "datasheet", "label": "OEM datasheet record", "points": 25, "max": 25,
                        "detail": f"{ds.get('title')} ({ds.get('doc_id')}, {ds.get('revision')})"})
    else:
        factors.append({"key": "datasheet", "label": "OEM datasheet record", "points": 10, "max": 25,
                        "detail": "Record has no datasheet URL"})
    # 2. part-number match between marking and record
    cand = next((c for c in ident.get("candidates", []) if c["reference_id"] == reference["id"]), None)
    if cand:
        pts = {"exact": 30, "ocr-normalised": 28, "contained": 25, "contained, ocr-normalised": 23, "fuzzy": 15}.get(cand["match_type"], 15)
        factors.append({"key": "part_number", "label": "Part number on marking", "points": pts, "max": 30,
                        "detail": f'"{cand["matched_token"]}" → {cand["matched_pattern"]} ({cand["match_type"]})'})
    elif ident.get("candidates"):
        factors.append({"key": "part_number", "label": "Part number on marking", "points": 0, "max": 30,
                        "detail": f"Marking matched {ident['candidates'][0]['part_number']}, not this record"})
    else:
        factors.append({"key": "part_number", "label": "Part number on marking", "points": 15, "max": 30,
                        "detail": "Operator-selected; not confirmed from a marking image"})
    # 3. manufacturer identity
    hints = {h["id"] for h in ident.get("manufacturer_hints", [])}
    if reference.get("manufacturer_id") in hints:
        factors.append({"key": "manufacturer", "label": "Manufacturer identity", "points": 20, "max": 20,
                        "detail": "Manufacturer marking consistent with record"})
    elif hints:
        factors.append({"key": "manufacturer", "label": "Manufacturer identity", "points": 0, "max": 20,
                        "detail": "Marking suggests a different manufacturer: " + ", ".join(sorted(hints))})
    else:
        factors.append({"key": "manufacturer", "label": "Manufacturer identity", "points": 10, "max": 20,
                        "detail": "Confirmed by operator (no manufacturer text read from marking)"})
    # 4. provenance of the reference values actually used
    used = [e["reference"] for e in evals if e.get("reference") and e["reference"].get("source_type") in ("oem", "oem_derived")]
    if used:
        frac = sum(1 for r in used if r.get("verified")) / len(used)
        factors.append({"key": "provenance", "label": "Reference provenance", "points": round(15 * frac, 1), "max": 15,
                        "detail": f"{sum(1 for r in used if r.get('verified'))} of {len(used)} datasheet values used are verified"})
    else:
        st = (reference.get("verification") or {}).get("status", "unverified")
        pts = {"verified_datasheet": 15, "partially_verified": 8}.get(st, 3)
        factors.append({"key": "provenance", "label": "Reference provenance", "points": pts, "max": 15,
                        "detail": f"Record status: {st.replace('_', ' ')}"})
    # 5. type number currency / legacy
    legacy = cand.get("legacy_type") if cand else None
    if not legacy:
        for lt in reference.get("legacy_types", []):
            if clean(lt["type"]) == clean(comp.get("part_number") or ""):
                legacy = lt
    if legacy:
        dcs = ident.get("date_codes") or []
        dc_year = dcs[0]["year"] if dcs else None
        if dc_year and dc_year <= legacy["removed_year"]:
            pts, detail = 10, f"Legacy type {legacy['type']} (removed {legacy['removed_year']}); date code {dcs[0]['code']} predates removal"
        elif dc_year:
            pts, detail = 0, f"Date code {dcs[0]['code']} is after {legacy['type']} was removed from the datasheet ({legacy['removed_year']})"
            out["notes"].append({"severity": "warning", "message": detail})
        else:
            pts, detail = 5, f"Legacy type {legacy['type']} (removed {legacy['removed_year']}); no date code read"
        factors.append({"key": "type_currency", "label": "Type number currency", "points": pts, "max": 10, "detail": detail})
    else:
        factors.append({"key": "type_currency", "label": "Type number currency", "points": 10, "max": 10,
                        "detail": "Type number listed in the referenced datasheet record"})
    out["factors"] = factors
    out["score"] = round(sum(f["points"] for f in factors), 1)
    out["status"] = "evaluated"
    out["notes"].append({"severity": "info", "message": "This score measures the availability and consistency of reference "
                                                         "data. It is not a rating of the manufacturer."})
    return out


# ------------------------------------------------------------------ overall
def compute_scores(ins, settings):
    cfg = ins.get("config", {})
    w = settings["weights"]
    sc = settings["scoring"]
    dims = []
    flags = []
    el = ins.get("electrical") or {}
    op = ins.get("optical") or {}
    mf = ins.get("manufacturer") or {}

    def dim(key, label, enabled, summary):
        score = summary.get("score") if enabled else None
        status = summary.get("status") if enabled else "not_performed"
        return {"key": key, "label": label, "enabled": bool(enabled), "weight": float(w.get(key, 0.0)),
                "score": score, "status": status, "coverage": summary.get("coverage"),
                "reasons": summary.get("reasons", []) if enabled else ["Not part of this inspection mode"]}

    dims.append(dim("electrical", "Electrical fingerprinting", cfg.get("electrical"), el.get("summary") or {}))
    dims.append(dim("optical", "Optical inspection", cfg.get("optical"), op.get("summary") or {}))
    dims.append(dim("manufacturer", "Manufacturer verification", cfg.get("manufacturer"), mf))

    for e in el.get("evaluations", []) if cfg.get("electrical") else []:
        if e.get("critical"):
            flags.append({"severity": "critical", "dimension": "electrical", "message": f"{e['label']}: {e['remarks']}"})
        elif e["status"] == "FAIL":
            flags.append({"severity": "warning", "dimension": "electrical", "message": f"{e['label']}: {e['remarks']}"})
    if cfg.get("optical"):
        for c in op.get("checks", []):
            if c["status"] == "fail":
                flags.append({"severity": "warning", "dimension": "optical", "message": f"{c['label']}: {c['note'] or c['observed']}"})
    for n in mf.get("notes", []) if cfg.get("manufacturer") else []:
        if n["severity"] != "info":
            flags.append({"severity": n["severity"], "dimension": "manufacturer", "message": n["message"]})

    avail = [d for d in dims if d["score"] is not None]
    w_avail = sum(d["weight"] for d in avail)
    w_total = sum(float(v) for v in w.values()) or 1.0
    coverage = w_avail / w_total
    overall = round(sum(d["weight"] * d["score"] for d in avail) / w_avail, 1) if w_avail > 0 else None
    for d in dims:
        d["contribution"] = round(d["weight"] * d["score"], 2) if d["score"] is not None else None
        d["normalized_contribution"] = round(d["weight"] * d["score"] / w_avail, 2) if d["score"] is not None and w_avail else None

    terms = " + ".join(f"{d['weight']:.2f}×{d['score']:.1f}" for d in avail) or "—"
    formula = f"S = ({terms}) / {w_avail:.2f} = {overall:.1f}" if overall is not None else "S cannot be computed — no dimension produced a score"

    reasons = []
    el_dim = dims[0]
    if el_dim["status"] == "invalid":
        cls = "inconclusive"
        reasons.append("Electrical test conditions were invalid: " + "; ".join(el_dim["reasons"]))
    elif sc.get("require_electrical", True) and el_dim["score"] is None:
        cls = "inconclusive"
        reasons.append("Electrical evidence is required for a screening result: " + ("; ".join(el_dim["reasons"]) or "not available"))
    elif coverage < sc.get("min_coverage", 0.6):
        cls = "inconclusive"
        reasons.append(f"Evidence coverage {coverage * 100:.0f} % is below the {sc.get('min_coverage', 0.6) * 100:.0f} % minimum")
    elif any(f["severity"] == "critical" for f in flags):
        cls = "deviation"
        reasons.append("Critical finding overrides the score threshold")
    elif overall >= sc.get("threshold_consistent", 80):
        cls = "consistent"
        reasons.append(f"Score {overall:.1f} ≥ {sc.get('threshold_consistent', 80)}")
    elif overall >= sc.get("threshold_investigate", 50):
        cls = "investigate"
        reasons.append(f"Score {overall:.1f} between {sc.get('threshold_investigate', 50)} and {sc.get('threshold_consistent', 80)}")
    else:
        cls = "deviation"
        reasons.append(f"Score {overall:.1f} < {sc.get('threshold_investigate', 50)}")
    for d in dims:
        if d["enabled"] and d["score"] is None and d["key"] != "electrical":
            reasons.append(f"{d['label']} inconclusive: " + ("; ".join(d["reasons"]) or d["status"]))
    return {
        "weights": {k: float(v) for k, v in w.items()}, "dimensions": dims, "overall": overall,
        "coverage": round(coverage, 3), "formula": formula, "classification_key": cls,
        "classification": CLASSES[cls], "reasons": reasons, "flags": flags,
        "thresholds": {"consistent": sc.get("threshold_consistent", 80), "investigate": sc.get("threshold_investigate", 50),
                       "min_coverage": sc.get("min_coverage", 0.6), "require_electrical": sc.get("require_electrical", True)},
        "data_source": ins.get("data_source"),
        "label": "Screening result" + (" — SIMULATED" if ins.get("data_source") == "simulated" else ""),
    }
