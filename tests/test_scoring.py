"""Unit tests for the scoring engine's missing-data rules.  Run: python -m tests.test_scoring  (or pytest)"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config, evaluation, scoring  # noqa: E402

SETTINGS = copy.deepcopy(config.DEFAULT_SETTINGS)


def ins(el=None, op=None, mf=None, cfg=None):
    return {"data_source": "simulated",
            "config": {"optical": True, "electrical": True, "manufacturer": True, **(cfg or {})},
            "electrical": {"evaluations": el or [], "summary": evaluation.summarize_electrical(el or [], SETTINGS)},
            "optical": {"checks": op or [], "summary": evaluation.summarize_optical(op or [], SETTINGS) if op else {"score": None, "status": "not_performed"}},
            "manufacturer": mf or {"score": None, "status": "reference_unavailable", "notes": []}}


def ev(key, status, score, weight, mandatory=False, critical=False):
    return {"key": key, "label": key, "kind": "x", "unit": "", "weight": weight, "mandatory": mandatory, "measured": None,
            "measured_text": "", "reference": None, "status": status, "score": score, "remarks": "r", "critical": critical}


def checks(status="pass"):
    return [dict(c, status=status, observed="", reference="", note="") for c in evaluation.OPTICAL_CHECKS]


GOOD_EL = [ev("v", "PASS", 100, 0.1, True), ev("i", "PASS", 100, 0.35, True), ev("s", "PASS", 100, 0.15), ev("f", "PASS", 100, 0.3)]
MF = {"score": 90.0, "status": "evaluated", "notes": []}


def test_full_formula_matches_spec():
    sc = scoring.compute_scores(ins(GOOD_EL, checks("pass"), MF), SETTINGS)
    assert abs(sc["overall"] - (0.6 * 100 + 0.25 * 100 + 0.15 * 90)) < 1e-6
    assert sc["coverage"] == 1.0 and sc["classification"] == "Reference-consistent"


def test_missing_measurement_is_not_a_pass():
    el = [ev("v", "PASS", 100, 0.1, True), ev("i", "NOT_MEASURED", None, 0.35, True), ev("s", "PASS", 100, 0.15)]
    s = evaluation.summarize_electrical(el, SETTINGS)
    assert s["score"] is None and s["status"] == "inconclusive"
    sc = scoring.compute_scores(ins(el, checks("pass"), MF), SETTINGS)
    assert sc["classification"] == "Inconclusive"


def test_optional_missing_excluded_not_counted():
    el = GOOD_EL[:3] + [ev("f", "NOT_MEASURED", None, 0.3)]
    s = evaluation.summarize_electrical(el, SETTINGS)
    assert s["score"] == 100.0 and abs(s["coverage"] - 0.6 / 0.9) < 0.01


def test_not_applicable_leaves_denominator():
    el = GOOD_EL[:3] + [ev("f", "NOT_APPLICABLE", None, 0.3)]
    assert evaluation.summarize_electrical(el, SETTINGS)["coverage"] == 1.0


def test_invalid_condition_makes_result_inconclusive():
    el = [ev("v", "INVALID", None, 0.1, True)] + GOOD_EL[1:]
    sc = scoring.compute_scores(ins(el, checks("pass"), MF), SETTINGS)
    assert sc["classification"] == "Inconclusive" and "invalid" in sc["reasons"][0].lower()


def test_critical_overrides_threshold():
    el = GOOD_EL[:3] + [ev("f", "FAIL", 0, 0.3, critical=True)]
    sc = scoring.compute_scores(ins(el, checks("pass"), MF), SETTINGS)
    assert sc["overall"] >= 70 and sc["classification"] == "Significant deviation detected"


def test_unperformed_optical_gets_no_score():
    sc = scoring.compute_scores(ins(GOOD_EL, None, MF, {"optical": False}), SETTINGS)
    opt = next(d for d in sc["dimensions"] if d["key"] == "optical")
    assert opt["score"] is None and abs(sc["coverage"] - 0.75) < 1e-9
    assert sc["overall"] == round((0.6 * 100 + 0.15 * 90) / 0.75, 1)


def test_unassessed_optical_checks_do_not_score():
    s = evaluation.summarize_optical(checks("not_assessed"), SETTINGS)
    assert s["score"] is None


def test_electrical_required():
    sc = scoring.compute_scores(ins([], checks("pass"), MF, {"electrical": False}), SETTINGS)
    assert sc["classification"] == "Inconclusive"


def test_current_limit_below_resolution_is_bounded_pass():
    p = {"key": "supply_current", "label": "I", "kind": "current_limit", "weight": 0.35, "mandatory": True, "spec_order": ["icc"]}
    ref = {"id": "r", "datasheet": {}, "specs": [{"key": "icc", "max": 20, "unit": "µA", "verified": True, "source_type": "oem"}]}
    e = evaluation.ev_current_limit(p, static={"i": {"mean": 0.0, "std": 0.03}}, reference=ref, res_ma=0.1, baselines=[],
                                    data_source="live", conditions={})
    assert e["status"] == "PASS" and "Upper-bound" in e["remarks"]
    e = evaluation.ev_current_limit(p, static={"i": {"mean": 2.6, "std": 0.1}}, reference=ref, res_ma=0.1, baselines=[],
                                    data_source="live", conditions={})
    assert e["status"] == "FAIL" and "Upper-bound" not in e["remarks"]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(f"{n} scoring tests passed")
