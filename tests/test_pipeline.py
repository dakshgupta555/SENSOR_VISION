"""End-to-end pipeline test over all seven simulation profiles.

Runs: sample image → real Tesseract OCR → identification → inspection → simulated electrical run →
finalize → PDF report, and checks each screening result against the profile's expected outcome.

    python -m tests.test_pipeline            (uses a temporary data directory)
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("ICV_DATA_DIR", tempfile.mkdtemp(prefix="icv-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app  # noqa: E402


def wait_status(c, iid, want, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ins = c.get(f"/api/inspections/{iid}").get_json()["data"]
        if ins["status"] in want:
            return ins
        time.sleep(0.3)
    raise TimeoutError(f"{iid} stuck in {ins['status']}")


def run_profile(c, sp, duration=5, iv=True):
    r = c.post("/api/sim/select", json={"profile_id": sp["id"], "override": "profile"}).get_json()
    assert r["ok"], r
    ocr = c.post("/api/ocr/sample", json={"sim_profile_id": sp["id"]}).get_json()
    assert ocr["ok"], ocr
    o = ocr["data"]
    ident = o["identification"]
    top = ident["candidates"][0] if ident["candidates"] else None
    profile_id = top["profile_id"] if top else sp["component_profile_id"]
    if ident["status"] == "ambiguous":  # operator confirms the manufacturer
        top = next(cnd for cnd in ident["candidates"] if cnd["reference_id"] == sp["reference_id"])
    payload = {
        "profile_id": profile_id, "reference_id": top["reference_id"] if top else None, "sim_profile_id": sp["id"],
        "image": {"image_id": o["image_id"], "source": "sample", "synthetic": True, "sim_profile_id": sp["id"]},
        "ocr": {k: o.get(k) for k in ("engine", "available", "text", "lines", "words", "mean_conf", "identification", "image_id")},
        "component": {"identification_method": "ocr (synthetic sample image)", "date_code": (ident["date_codes"] or [{}])[0].get("code", "")},
        "config": {"duration_s": duration, "iv": iv},
    }
    ins = c.post("/api/inspections", json=payload).get_json()
    assert ins["ok"], ins
    iid = ins["data"]["id"]
    st = c.post(f"/api/inspections/{iid}/start").get_json()
    assert st["ok"], st
    ins = wait_status(c, iid, ("measured", "error", "aborted"))
    assert ins["status"] == "measured", ins["run"]
    fin = c.post(f"/api/inspections/{iid}/finalize", json={}).get_json()
    assert fin["ok"], fin
    rep = c.post(f"/api/inspections/{iid}/report").get_json()
    assert rep["ok"], rep
    pdf = c.get(f"/api/inspections/{iid}/report.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    return fin["data"], o


def main():
    app = create_app()
    c = app.test_client()
    sims = c.get("/api/sim/profiles").get_json()["data"]
    failures = 0
    for sp in sims:
        t0 = time.time()
        ins, o = run_profile(c, sp)
        sc = ins["scores"]
        dims = {d["key"]: d["score"] for d in sc["dimensions"]}
        good = sc["classification"] == sp["expected_result"]
        failures += 0 if good else 1
        print(f"{'OK ' if good else 'BAD'} {sp['id']:<20} {sc['classification']:<32} overall={sc['overall']} "
              f"E={dims['electrical']} O={dims['optical']} M={dims['manufacturer']} cov={sc['coverage']} "
              f"ocr={o['identification']['status']} ({time.time() - t0:.1f}s)")
        if not good:
            print("    expected:", sp["expected_result"], "| reasons:", sc["reasons"])
        for e in ins["electrical"]["evaluations"]:
            print(f"      {e['status']:<13} {e['label'][:38]:<38} {e['measured_text'][:40]:<40} {e['remarks'][:70]}")
    print("FAILURES:", failures)
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
