"""Parameter-by-parameter evaluation of an inspection against its reference data.

Status vocabulary (shared with the UI and the report):
  PASS / WARN / FAIL      evaluated against a reference, scored 0–100
  NOT_MEASURED            the measurement was not taken (never counted as a pass)
  NO_REFERENCE            measured, but nothing applicable to compare with
  NOT_APPLICABLE          the parameter does not apply to this configuration
  INVALID                 the measurement exists but the test condition was wrong
"""
import math

from . import config

SCORED = ("PASS", "WARN", "FAIL")

UNIT_TO_MA = {"A": 1000.0, "mA": 1.0, "µA": 0.001, "uA": 0.001, "nA": 1e-6}
UNIT_TO_V = {"V": 1.0, "mV": 0.001}


def fmt(v, unit="", digits=3):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        if abs(v) >= 100:
            s = f"{v:.1f}"
        elif abs(v) >= 10:
            s = f"{v:.2f}"
        else:
            s = f"{v:.{digits}f}"
        return f"{s} {unit}".strip()
    return str(v)


def spec_by_key(reference, key):
    if not reference:
        return None
    for s in reference.get("specs", []):
        if s.get("key") == key:
            return s
    return None


def spec_ref(spec, reference, text):
    return {"text": text, "spec_key": spec.get("key"), "source_type": spec.get("source_type"),
            "verified": bool(spec.get("verified")), "conditions": spec.get("conditions", ""),
            "doc": (reference.get("datasheet") or {}).get("doc_id"), "reference_id": reference.get("id")}


def _entry(p, **kw):
    e = {"key": p["key"], "label": p["label"], "kind": p["kind"], "unit": p.get("unit", ""),
         "weight": p.get("weight", 0.0), "mandatory": bool(p.get("mandatory")), "measured": None,
         "measured_text": "—", "reference": None, "status": "NOT_MEASURED", "score": None, "remarks": "",
         "critical": False}
    e.update(kw)
    return e


def find_baseline(baselines, param, data_source, conditions):
    for b in baselines:
        if b["param"] != param or b["data_source"] != data_source:
            continue
        bc = b.get("conditions") or {}
        if bc.get("rail_v") is not None and conditions.get("rail_v") is not None and abs(bc["rail_v"] - conditions["rail_v"]) > 0.05:
            continue
        if bc.get("clock") and conditions.get("clock") and bc["clock"] != conditions["clock"]:
            continue
        return b
    return None


def evaluate_electrical(ins, profile, reference, baselines, settings):
    cfg = ins.get("config", {})
    meas = ins.get("measurements") or {}
    static = meas.get("static") or {}
    rail = cfg.get("rail_v")
    res_ma = (ins.get("device") or {}).get("resolution_ma") or config.SENSOR_RESOLUTION_MA.get(
        (ins.get("device") or {}).get("sensor") or "", 0.1)
    data_source = ins.get("data_source")
    conditions = {"rail_v": rail, "clock": cfg.get("clock")}
    functional = ins.get("functional") or {}
    out = []
    for p in profile.get("evaluation", []):
        kind = p["kind"]
        try:
            fn = EVALUATORS[kind]
        except KeyError:
            out.append(_entry(p, status="NOT_APPLICABLE", remarks=f"No evaluator for {kind}"))
            continue
        out.append(fn(p, ins=ins, profile=profile, reference=reference, static=static, rail=rail, res_ma=res_ma,
                      baselines=baselines, data_source=data_source, conditions=conditions, functional=functional,
                      settings=settings))
    return out


# ------------------------------------------------------------------ evaluators
def ev_supply_condition(p, static, rail, reference, **_):
    vstats = static.get("v")
    if not vstats:
        return _entry(p, remarks="No valid supply-voltage samples")
    m = vstats["mean"]
    e = _entry(p, measured=round(m, 4), measured_text=fmt(m, "V"))
    spec = spec_by_key(reference, p.get("spec"))
    lo = spec.get("min") if spec else None
    hi = spec.get("max") if spec else None
    rng_txt = f"; datasheet {fmt(lo, '', 1)}–{fmt(hi, 'V', 1)}" if spec and lo is not None and hi is not None else ""
    e["reference"] = (spec_ref(spec, reference, f"Selected rail {rail:.2f} V ±5 %{rng_txt}") if spec else
                      {"text": f"Selected rail {rail:.2f} V ±5 % (no datasheet range available)", "source_type": "test_condition",
                       "verified": False})
    if rail is None:
        e.update(status="INVALID", remarks="No supply rail configured")
        return e
    dev = (m - rail) / rail
    if spec and ((lo is not None and m < lo - 0.02) or (hi is not None and m > hi + 0.02)):
        e.update(status="INVALID", critical=True,
                 remarks=f"Measured {m:.3f} V is outside the datasheet operating range — test conditions invalid")
        return e
    if abs(dev) <= 0.05:
        e.update(status="PASS", score=100.0, remarks=f"{dev * 100:+.1f} % from selected rail")
    elif abs(dev) <= 0.10:
        e.update(status="WARN", score=70.0, remarks=f"{dev * 100:+.1f} % from selected rail — check supply wiring")
    else:
        e.update(status="INVALID", critical=True,
                 remarks=f"Measured {m:.3f} V differs from the selected {rail:.2f} V rail by {dev * 100:+.1f} % — test conditions invalid")
    return e


def _applicable_spec(p, reference, conditions):
    for key in p.get("spec_order", []):
        spec = spec_by_key(reference, key)
        if not spec:
            continue
        need = (p.get("spec_applies") or {}).get(key)
        if need:
            if need.get("rail_v") is not None and (conditions.get("rail_v") is None or abs(need["rail_v"] - conditions["rail_v"]) > 0.05):
                continue
            if need.get("clock") and need["clock"] != conditions.get("clock"):
                continue
        return spec
    return None


def _baseline_eval(e, m, b, res_ma):
    sigma = max(b.get("std") or 0.0, res_ma, 0.02 * abs(b["mean"]))
    z = abs(m - b["mean"]) / sigma
    src = "simulated demonstration baseline" if b["data_source"] == "simulated" else "empirical known-good sample"
    e["reference"] = {"text": f"{b['mean']:.3f} ± {b.get('std') or 0:.3f} mA (n = {b.get('n')}, {src})",
                      "source_type": "simulated" if b["data_source"] == "simulated" else "empirical",
                      "verified": b["data_source"] != "simulated", "baseline_id": b["id"],
                      "source_inspection_id": b.get("source_inspection_id")}
    if z <= 2:
        e.update(status="PASS", score=round(100 - 5 * z, 1), remarks=f"z = {z:.2f} against baseline")
    elif z <= 4:
        e.update(status="WARN", score=round(70 - 10 * (z - 2), 1), remarks=f"z = {z:.2f} against baseline")
    else:
        e.update(status="FAIL", score=round(max(0.0, 40 - 5 * (z - 4)), 1), remarks=f"z = {z:.2f} against baseline")
    return e


def ev_current_limit(p, static, reference, res_ma, baselines, data_source, conditions, **_):
    istats = static.get("i")
    if not istats:
        return _entry(p, remarks="No valid current samples")
    m = istats["mean"]
    e = _entry(p, measured=round(m, 4), measured_text=fmt(m, "mA"))
    spec = _applicable_spec(p, reference, conditions)
    if spec and spec.get("max") is not None:
        L = spec["max"] * UNIT_TO_MA.get(spec.get("unit", "mA"), 1.0)
        typ = spec.get("typ")
        txt = f"≤ {spec['max']:g} {spec['unit']}"
        if typ is not None:
            txt += f" (typ {typ:g} {spec['unit']})"
        txt += f" — {spec.get('conditions', '')}"
        e["reference"] = spec_ref(spec, reference, txt)
        margin = max(2 * res_ma, 0.01 * abs(m))
        notes = []
        if L < res_ma and m - margin <= L:
            notes.append(f"Upper-bound check only: sensor resolution {res_ma:g} mA exceeds the {L:g} mA limit")
        if not spec.get("verified"):
            notes.append("reference value not verified in this dataset build")
        if m - margin <= L:
            e.update(status="PASS", score=100.0)
        elif m - margin <= 1.10 * L:
            e.update(status="WARN", score=60.0)
            notes.insert(0, f"{(m / L - 1) * 100:.0f} % above the datasheet maximum, within 10 %")
        else:
            ratio = m / L if L > 0 else float("inf")
            e.update(status="FAIL", score=0.0)
            notes.insert(0, f"{ratio:.1f}× the datasheet maximum" if ratio < 1000 else f"{ratio:.0f}× the datasheet maximum")
            cm = p.get("critical_multiple")
            if cm and m - margin > cm * L:
                e["critical"] = True
        b = find_baseline(baselines, p["key"], data_source, conditions)
        if b:
            z = abs(m - b["mean"]) / max(b.get("std") or 0.0, res_ma, 0.02 * abs(b["mean"]))
            notes.append(f"baseline {b['mean']:.3f} mA, z = {z:.1f}")
        e["remarks"] = "; ".join(notes)
        return e
    b = find_baseline(baselines, p["key"], data_source, conditions)
    if b:
        return _baseline_eval(e, m, b, res_ma)
    why = "No datasheet limit applies to this test condition" if p.get("spec_order") and reference else "Reference unavailable"
    e.update(status="NO_REFERENCE", remarks=f"{why}; record a known-good sample to create a baseline")
    return e


def ev_current_range(p, static, reference, res_ma, conditions, baselines, data_source, **_):
    istats = static.get("i")
    if not istats:
        return _entry(p, remarks="No valid current samples")
    m = istats["mean"]
    e = _entry(p, measured=round(m, 4), measured_text=fmt(m, "mA"))
    spec = _applicable_spec(p, reference, conditions)
    if not spec:
        b = find_baseline(baselines, p["key"], data_source, conditions)
        if b:
            return _baseline_eval(e, m, b, res_ma)
        e.update(status="NO_REFERENCE", remarks="Reference unavailable")
        return e
    k = UNIT_TO_MA.get(spec.get("unit", "mA"), 1.0)
    lo, hi = spec["min"] * k, spec["max"] * k
    e["reference"] = spec_ref(spec, reference, f"{spec['min']:g}–{spec['max']:g} {spec['unit']} — {spec.get('conditions', '')}")
    margin = max(2 * res_ma, 0.01 * abs(m))
    if lo - margin <= m <= hi + margin:
        e.update(status="PASS", score=100.0, remarks="Within datasheet envelope")
    elif 0.9 * lo <= m <= 1.1 * hi:
        e.update(status="WARN", score=60.0, remarks="Within 10 % of the datasheet envelope")
    else:
        e.update(status="FAIL", score=0.0, remarks="Outside the datasheet envelope")
    return e


def ev_stability(p, static, res_ma, **_):
    istats = static.get("i")
    n = static.get("n", 0)
    if not istats or n < 10:
        return _entry(p, remarks="Fewer than 10 valid samples — stability not assessed")
    lim = p.get("limits", {})
    m, sd = istats["mean"], istats["std"]
    drift = static.get("drift_ma_per_min")
    pass_lim = max(lim.get("pass_abs_ma", 0.15), lim.get("pass_cv", 0.03) * abs(m))
    warn_lim = max(lim.get("warn_abs_ma", 0.4), lim.get("warn_cv", 0.10) * abs(m))
    cv = static.get("cv")
    sig = static.get("drift_significant")
    txt = f"σ {sd:.3f} mA" + (f", CV {cv * 100:.1f} %" if cv is not None and abs(m) > 5 * res_ma else "")
    if drift is not None:
        txt += f", drift {drift:+.2f} mA/min" + ("" if sig else " (n.s.)")
    if not sig:
        drift = None  # a trend smaller than the noise is not evidence of drift
    e = _entry(p, measured=round(sd, 4), measured_text=txt,
               reference={"text": f"σ ≤ {pass_lim:.3f} mA (pass), ≤ {warn_lim:.3f} mA (warn)"
                                  + (f"; |drift| ≤ {lim['drift_warn_ma_per_min']} mA/min" if lim.get("drift_warn_ma_per_min") else ""),
                          "source_type": "test_criterion", "verified": False})
    if sd <= pass_lim:
        status, score, rem = "PASS", 100.0, "Stable within criterion"
    elif sd <= warn_lim:
        status, score, rem = "WARN", 60.0, "Elevated variation"
    else:
        status, score, rem = "FAIL", 20.0, "Unstable supply current"
    if drift is not None and lim.get("drift_fail_ma_per_min") and abs(drift) > lim["drift_fail_ma_per_min"]:
        status, score, rem = "FAIL", 20.0, f"Supply current drifting {drift:+.2f} mA/min"
    elif drift is not None and lim.get("drift_warn_ma_per_min") and abs(drift) > lim["drift_warn_ma_per_min"] and status == "PASS":
        status, score, rem = "WARN", 60.0, f"Supply current drifting {drift:+.2f} mA/min"
    e.update(status=status, score=score, remarks=rem)
    return e


def _functional_missing(p, functional, profile):
    if not profile.get("functional_test"):
        return _entry(p, status="NOT_APPLICABLE", remarks="No functional test defined for this profile")
    st = functional.get("status")
    reason = functional.get("reason") or {
        None: "Functional test not run",
        "not_configured": "Functional test not configured",
        "skipped": "Functional test skipped",
        "timeout": "No response from the test controller",
        "error": "Functional test error",
    }.get(st, "Functional test not run")
    return _entry(p, status="NOT_MEASURED", remarks=reason)


def ev_truth_table(p, functional, profile, **_):
    ft = profile.get("functional_test") or {}
    if functional.get("status") != "completed" or functional.get("type") != "logic_truth_table":
        return _functional_missing(p, functional, profile)
    rows = functional.get("rows", [])
    total = len(ft.get("gates", [])) * len(ft.get("vectors", []))
    passed = sum(1 for r in rows if r.get("pass"))
    e = _entry(p, measured=passed, measured_text=f"{passed}/{len(rows)} vectors match",
               reference={"text": f"{ft.get('function')} truth table, {total} vectors", "source_type": "oem",
                          "verified": True, "conditions": "Logic function per datasheet"})
    if len(rows) < total:
        e.update(status="FAIL", score=0.0, remarks=f"Only {len(rows)} of {total} vectors returned")
        return e
    if passed == total:
        e.update(status="PASS", score=100.0, remarks="All gates match the expected logic function")
    else:
        bad = [r for r in rows if not r.get("pass")][:3]
        desc = "; ".join(f"{r['gate']} A={r['vector'][0]} B={r['vector'][1]} expected {r['expected']} got {r['observed']}" for r in bad)
        e.update(status="FAIL", score=0.0, remarks=desc, critical=bool(p.get("critical_on_fail")))
    return e


def _output_levels(functional):
    rows = [r for r in functional.get("rows", []) if r.get("v_out_mv") is not None]
    highs = [r["v_out_mv"] / 1000.0 for r in rows if r.get("observed") == 1]
    lows = [r["v_out_mv"] / 1000.0 for r in rows if r.get("observed") == 0]
    return (min(highs) if highs else None), (max(lows) if lows else None)


def ev_output_high(p, functional, reference, static, **_):
    if functional.get("status") != "completed":
        return _entry(p, remarks="Functional test not run")
    voh, _ = _output_levels(functional)
    if voh is None:
        return _entry(p, remarks="Output levels not sensed (ADC sensing disabled or pins not ADC-capable)")
    spec = spec_by_key(reference, p.get("spec"))
    vcc = (static.get("v") or {}).get("mean")
    e = _entry(p, measured=round(voh, 3), measured_text=f"min {voh:.3f} V")
    if not spec or vcc is None:
        e.update(status="NO_REFERENCE", remarks="No output-level reference")
        return e
    thr = vcc + spec.get("min_offset_from_vcc", -0.1)
    tol = p.get("adc_tolerance_v", 0.1)
    e["reference"] = spec_ref(spec, reference, f"≥ {thr:.2f} V (VCC − 0.1 V at {vcc:.2f} V, derived; ADC tolerance ±{tol:.2f} V)")
    if voh >= thr - tol:
        e.update(status="PASS", score=100.0, remarks="Output high level consistent")
    elif voh >= thr - 3 * tol:
        e.update(status="WARN", score=60.0, remarks="Output high level low")
    else:
        e.update(status="FAIL", score=0.0, remarks=f"Output high {voh:.2f} V is {thr - voh:.2f} V below the derived limit")
    return e


def ev_output_low(p, functional, reference, **_):
    if functional.get("status") != "completed":
        return _entry(p, remarks="Functional test not run")
    _, vol = _output_levels(functional)
    if vol is None:
        return _entry(p, remarks="Output levels not sensed (ADC sensing disabled or pins not ADC-capable)")
    spec = spec_by_key(reference, p.get("spec"))
    e = _entry(p, measured=round(vol, 3), measured_text=f"max {vol:.3f} V")
    if not spec:
        e.update(status="NO_REFERENCE", remarks="No output-level reference")
        return e
    tol = p.get("adc_tolerance_v", 0.1)
    lim = spec.get("max", 0.1)
    e["reference"] = spec_ref(spec, reference, f"≤ {lim:.2f} V (derived; ADC tolerance ±{tol:.2f} V)")
    if vol <= lim + tol:
        e.update(status="PASS", score=100.0, remarks="Output low level consistent")
    elif vol <= lim + 3 * tol:
        e.update(status="WARN", score=60.0, remarks="Output low level high")
    else:
        e.update(status="FAIL", score=0.0, remarks=f"Output low {vol:.2f} V exceeds the derived limit")
    return e


def ev_heartbeat(p, functional, profile, **_):
    hb_def = ((profile.get("functional_test") or {}).get("heartbeat")) or {}
    hb = (functional.get("results") or {}).get("heartbeat")
    if functional.get("status") not in ("completed", "partial") or not hb:
        return _functional_missing(p, functional, profile)
    exp, tol = hb_def.get("expected_hz", 10.0), hb_def.get("tolerance_pct", 10)
    f = hb.get("freq_hz") or 0.0
    e = _entry(p, measured=round(f, 3), measured_text=f"{f:.2f} Hz ({hb.get('edges', 0)} edges)",
               reference={"text": f"{exp:g} Hz ± {tol} % (heartbeat firmware; tolerance allows for internal RC oscillator)",
                          "source_type": "test_criterion", "verified": False})
    if f <= 0:
        e.update(status="FAIL", score=0.0, remarks="No heartbeat detected — firmware not running or wiring fault")
        return e
    dev = abs(f - exp) / exp * 100
    if dev <= tol:
        e.update(status="PASS", score=100.0, remarks=f"{dev:.1f} % from nominal")
    elif dev <= 2 * tol:
        e.update(status="WARN", score=60.0, remarks=f"{dev:.1f} % from nominal — clock out of expected range")
    else:
        e.update(status="FAIL", score=0.0, remarks=f"{dev:.1f} % from nominal")
    return e


def ev_uart_signature(p, functional, profile, reference, **_):
    sig_def = ((profile.get("functional_test") or {}).get("uart_signature")) or {}
    res = (functional.get("results") or {}).get("uart_signature")
    if functional.get("status") not in ("completed", "partial") or not res:
        return _functional_missing(p, functional, profile)
    expected = sig_def.get("expected", "").upper()
    spec = spec_by_key(reference, "device_signature")
    ref = (spec_ref(spec, reference, f"{spec.get('text')} ({profile.get('base_part')})") if spec else
           {"text": expected, "source_type": "test_criterion", "verified": False})
    if not res.get("found"):
        return _entry(p, status="FAIL", score=0.0, reference=ref, measured_text="no signature line",
                      remarks="No signature line received — firmware not running or TX not wired")
    line = res.get("line") or ""
    got = line.split(sig_def.get("prefix", "ICV:SIG:"))[-1].strip().upper().replace(" ", "")
    pretty = " ".join(got[k:k + 2] for k in range(0, len(got), 2))
    e = _entry(p, measured=got, measured_text=pretty or line, reference=ref)
    if got == expected:
        e.update(status="PASS", score=100.0, remarks="Signature matches the expected device")
    else:
        e.update(status="FAIL", score=0.0, critical=bool(p.get("critical_on_fail")),
                 remarks=f"Device reports {pretty}; expected {' '.join(expected[k:k + 2] for k in range(0, len(expected), 2))} — different silicon")
    return e


def ev_boot_log(p, functional, profile, **_):
    res = (functional.get("results") or {}).get("boot_log")
    if functional.get("status") not in ("completed", "partial") or not res:
        return _functional_missing(p, functional, profile)
    tok = (((profile.get("functional_test") or {}).get("uart")) or {}).get("token", "rst:0x")
    ref = {"text": f'ROM boot log containing "{tok}"', "source_type": "oem", "verified": True}
    if res.get("found"):
        return _entry(p, status="PASS", score=100.0, measured=res.get("line"), measured_text=(res.get("line") or "")[:60],
                      reference=ref, remarks="Boot log received")
    return _entry(p, status="FAIL", score=0.0, measured_text="no boot log", reference=ref,
                  remarks="No boot log received — check TX wiring and MTDO strap")


def ev_analog_buffer(p, functional, profile, **_):
    ft = profile.get("functional_test") or {}
    if functional.get("status") != "completed" or functional.get("type") != "analog_buffer":
        return _functional_missing(p, functional, profile)
    rows = functional.get("rows", [])
    if not rows:
        return _entry(p, status="FAIL", score=0.0, remarks="No points returned")
    errs = [abs(r["vout_mv"] - r["vin_mv"]) for r in rows]
    mx = max(errs)
    tol = ft.get("tolerance_mv", 60)
    e = _entry(p, measured=mx, measured_text=f"max |error| {mx:.0f} mV over {len(rows)} points",
               reference={"text": f"|VOUT − VIN| ≤ {tol} mV (follower; tolerance covers ESP32 ADC error)",
                          "source_type": "test_criterion", "verified": False})
    if mx <= tol:
        e.update(status="PASS", score=round(100 - 10 * mx / tol, 1), remarks="Follower tracks input")
    elif mx <= 2 * tol:
        e.update(status="WARN", score=55.0, remarks="Tracking error elevated")
    else:
        e.update(status="FAIL", score=0.0, remarks="Follower does not track input")
    return e


def ev_iv_signature(p, ins, **_):
    iv = ins.get("iv") or {}
    if iv.get("status") != "completed" or not iv.get("points"):
        return _entry(p, remarks=iv.get("reason") or "Pin I-V sweep not performed")
    ref = iv.get("reference")
    if not ref or not ref.get("points"):
        return _entry(p, status="NO_REFERENCE", measured_text=f"{len(iv['points'])} points",
                      remarks="No reference I-V curve for this profile; record a known-good sample")
    meas = iv["points"]
    refp = ref["points"]
    n = min(len(meas), len(refp))
    diffs = [(meas[k]["v"] - refp[k]["v"]) * 1000 for k in range(n) if refp[k].get("vsrc", 1.0) > 0.3]
    rms = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if diffs else 0.0
    src = ref.get("source", "reference")
    e = _entry(p, measured=round(rms, 1), measured_text=f"RMS node-voltage deviation {rms:.0f} mV",
               reference={"text": f"Reference curve ({src}); pass ≤ 40 mV, warn ≤ 100 mV",
                          "source_type": "simulated" if "simulated" in src else "empirical",
                          "verified": "simulated" not in src})
    if rms <= 40:
        e.update(status="PASS", score=round(100 - rms / 4, 1), remarks="Curve matches reference")
    elif rms <= 100:
        e.update(status="WARN", score=60.0, remarks="Curve shape differs from reference")
    else:
        e.update(status="FAIL", score=0.0, remarks="Pin protection structure differs from reference")
    return e


EVALUATORS = {
    "supply_condition": ev_supply_condition,
    "current_limit": ev_current_limit,
    "current_range": ev_current_range,
    "stability": ev_stability,
    "truth_table": ev_truth_table,
    "output_high": ev_output_high,
    "output_low": ev_output_low,
    "heartbeat": ev_heartbeat,
    "uart_signature": ev_uart_signature,
    "boot_log": ev_boot_log,
    "analog_buffer": ev_analog_buffer,
    "iv_signature": ev_iv_signature,
}


# ------------------------------------------------------------------ dimension summaries
def summarize_electrical(evals, settings):
    sc = settings["scoring"]
    applicable = [e for e in evals if e["status"] != "NOT_APPLICABLE"]
    scored = [e for e in applicable if e["status"] in SCORED and e["score"] is not None]
    w_total = sum(e["weight"] for e in applicable) or 1.0
    w_scored = sum(e["weight"] for e in scored)
    coverage = w_scored / w_total
    summary = {"coverage": round(coverage, 3), "score": None, "status": "inconclusive", "reasons": []}
    invalid = [e for e in applicable if e["status"] == "INVALID"]
    if invalid:
        summary["status"] = "invalid"
        summary["reasons"] = [f"{e['label']}: {e['remarks']}" for e in invalid]
        return summary
    missing_mand = [e for e in applicable if e["mandatory"] and e["status"] not in SCORED]
    if missing_mand:
        summary["reasons"] = [f"Mandatory evidence missing — {e['label']}: {e['remarks'] or e['status']}" for e in missing_mand]
        return summary
    if coverage < sc.get("min_electrical_coverage", 0.5):
        summary["reasons"] = [f"Only {coverage * 100:.0f} % of electrical evidence (by weight) was evaluated"]
        return summary
    summary["score"] = round(sum(e["weight"] * e["score"] for e in scored) / w_scored, 1)
    summary["status"] = "evaluated"
    return summary


OPTICAL_CHECKS = [
    {"key": "marking_readability", "label": "Marking readability", "weight": 0.10, "auto": True},
    {"key": "part_number_consistency", "label": "Part-number consistency", "weight": 0.20, "auto": True},
    {"key": "logo_consistency", "label": "Logo consistency", "weight": 0.10, "auto": False},
    {"key": "marking_placement", "label": "Marking placement and font", "weight": 0.10, "auto": False},
    {"key": "package_appearance", "label": "Package appearance", "weight": 0.10, "auto": False},
    {"key": "surface_irregularities", "label": "Surface irregularities (resurfacing, coating)", "weight": 0.15, "auto": False},
    {"key": "pin_alignment", "label": "Pin alignment and lead finish", "weight": 0.05, "auto": False},
    {"key": "physical_damage", "label": "Visible physical damage", "weight": 0.05, "auto": False},
    {"key": "reference_image_similarity", "label": "Similarity to reference image (indicative)", "weight": 0.15, "auto": True},
]
OPTICAL_STATUS_SCORE = {"pass": 100.0, "warn": 50.0, "fail": 0.0}


def summarize_optical(checks, settings):
    assessed = [c for c in checks if c["status"] in OPTICAL_STATUS_SCORE]
    w_all = sum(c["weight"] for c in checks) or 1.0
    w_ass = sum(c["weight"] for c in assessed)
    coverage = w_ass / w_all
    s = {"coverage": round(coverage, 3), "score": None, "status": "inconclusive", "reasons": []}
    if not assessed:
        s["status"] = "not_performed"
        s["reasons"] = ["No optical checks were assessed"]
        return s
    if coverage < settings["scoring"].get("min_optical_coverage", 0.4):
        s["reasons"] = [f"Only {coverage * 100:.0f} % of optical checks (by weight) were assessed"]
        return s
    s["score"] = round(sum(c["weight"] * OPTICAL_STATUS_SCORE[c["status"]] for c in assessed) / w_ass, 1)
    s["status"] = "evaluated"
    return s
