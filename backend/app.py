"""Flask application: REST API, Server-Sent Events stream and the single-page dashboard."""
import io
import json
import os
import re
import time

import pandas as pd
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import config, database, identify, ocr_engine, report, samples
from .acquisition import get_hub
from .events import bus
from .inspection import InspectionEngine, InspectionError
from .serial_manager import SerialManager
from .simulation import OVERRIDES, SimulatedController, SimulationSource


def create_app():
    database.init_db()
    samples.ensure_samples()
    app = Flask(__name__, static_folder=os.path.join(config.FRONTEND_DIR, "static"),
                template_folder=os.path.join(config.FRONTEND_DIR, "templates"))
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    hub = get_hub()
    serial_mgr = SerialManager(hub)
    sim = SimulationSource(hub)
    sim_ctrl = SimulatedController(sim)
    engine = InspectionEngine(hub, serial_mgr, sim, sim_ctrl)
    st = database.get_settings()
    hub.set_mode(st.get("mode", "simulation"))
    try:
        sim.select(st["simulation"].get("profile_id") or "sim-nor-ok", st["simulation"].get("stream_override") or "profile")
    except ValueError:
        sim.select("sim-nor-ok", "profile")
    if hub.mode == "live":
        serial_mgr.apply_to_hub()
    app.extensions["icv"] = {"hub": hub, "serial": serial_mgr, "sim": sim, "engine": engine}

    def ok(data=None, **kw):
        out = {"ok": True}
        if data is not None:
            out["data"] = data
        out.update(kw)
        return jsonify(out)

    def fail(msg, code=400, **kw):
        return jsonify({"ok": False, "error": str(msg), **kw}), code

    @app.errorhandler(InspectionError)
    def _ins_err(e):
        return fail(e)

    @app.errorhandler(ValueError)
    def _val_err(e):
        return fail(e)

    def body():
        return request.get_json(silent=True) or {}

    # ------------------------------------------------------------ pages
    @app.route("/")
    def index():
        return send_from_directory(app.template_folder, "index.html")

    @app.route("/samples/<path:name>")
    def sample_file(name):
        return send_from_directory(config.SAMPLE_DIR, name)

    @app.route("/api/images/<image_id>")
    @app.route("/api/images/<image_id>/<stage>")
    def image_file(image_id, stage=None):
        if stage == "original":
            stage = None
        p = ocr_engine.image_path(image_id, stage)
        if not os.path.exists(p):
            abort(404)
        return send_file(p, mimetype="image/png", max_age=0)

    # ------------------------------------------------------------ status / SSE
    def status_payload():
        s = database.get_settings()
        return {"app": {"name": config.APP_NAME, "version": config.APP_VERSION,
                        "reference_dataset_version": config.REFERENCE_DATASET_VERSION},
                "mode": hub.mode, "device": hub.device_status(), "serial": serial_mgr.status(),
                "simulation": {"profile_id": sim.profile["id"] if sim.profile else None, "override": sim.override,
                               "paused": sim.paused, "overrides": OVERRIDES},
                "ocr": ocr_engine.tesseract_status(), "settings": s, "stats": hub.stats(),
                "active_inspection": next((iid for iid, r in engine.runners.items() if r.is_alive()), None)}

    @app.route("/api/status")
    def api_status():
        return ok(status_payload())

    @app.route("/api/stream")
    def api_stream():
        q = bus.subscribe()

        def gen():
            try:
                yield f"event: hello\ndata: {json.dumps({'device': hub.device_status()})}\n\n"
                yield from bus.stream(q)
            finally:
                bus.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    # ------------------------------------------------------------ settings / mode
    @app.route("/api/settings", methods=["GET", "PUT"])
    def api_settings():
        if request.method == "GET":
            return ok(database.get_settings())
        new = body()
        new.pop("mode", None)
        if "weights" in new:
            w = {k: float(v) for k, v in new["weights"].items()}
            if any(v < 0 for v in w.values()) or abs(sum(w.values()) - 1.0) > 0.001:
                return fail("Weights must be non-negative and sum to 1.00")
            new["weights"] = w
        if "acquisition" in new:
            r = float(new["acquisition"].get("rate_hz", 10))
            if not 1 <= r <= 20:
                return fail("Sample rate must be between 1 and 20 Hz")
        if "fixture" in new and "map" in new["fixture"]:
            for pkg, m in new["fixture"]["map"].items():
                for pin, g in m.items():
                    if g in ("", None):
                        continue
                    g = int(g)
                    if g in (0, 1, 3, 6, 7, 8, 9, 10, 11, 21, 22) or g > 39:
                        return fail(f"GPIO {g} cannot be used for the fixture (boot strap, flash, UART0 or I2C pin)")
        s = database.save_settings(new)
        hub.reload_settings()
        if "acquisition" in new and serial_mgr.is_open():
            serial_mgr.send({"command": "SET_RATE", "hz": s["acquisition"]["rate_hz"]})
        return ok(s)

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        mode = body().get("mode")
        if mode not in ("live", "simulation"):
            return fail("mode must be 'live' or 'simulation'")
        if any(r.is_alive() for r in engine.runners.values()):
            return fail("Finish or abort the running inspection before switching mode")
        database.save_settings({"mode": mode})
        hub.reload_settings()
        hub.set_mode(mode)
        if mode == "live":
            serial_mgr.apply_to_hub()
        else:
            if sim.profile:
                hub.update_device(state="paused" if sim.paused else "simulating", sim_profile=sim.profile["id"],
                                  sim_override=sim.override, sensor="SIM-INA219", resolution_ma=0.1, firmware="simulator",
                                  device_id="SIMULATED")
        return ok(status_payload())

    # ------------------------------------------------------------ serial
    @app.route("/api/serial/ports")
    def api_ports():
        return ok(SerialManager.list_ports(), pyserial=serial_mgr.status()["available"])

    @app.route("/api/serial/connect", methods=["POST"])
    def api_connect():
        b = body()
        port = b.get("port") or ""
        baud = int(b.get("baud") or 115200)
        if baud not in (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600):
            return fail("Unsupported baud rate")
        try:
            serial_mgr.connect(port, baud, b.get("auto_reconnect", True))
        except Exception as exc:
            hub.update_device(state="disconnected", message=f"Could not open {port}: {exc}")
            return fail(f"Could not open {port}: {exc}")
        database.save_settings({"serial": {"port": port, "baud": baud, "auto_reconnect": bool(b.get("auto_reconnect", True))}})
        return ok(serial_mgr.status())

    @app.route("/api/serial/disconnect", methods=["POST"])
    def api_disconnect():
        serial_mgr.disconnect(user=True)
        return ok(serial_mgr.status())

    @app.route("/api/serial/command", methods=["POST"])
    def api_command():
        cmd = body()
        name = str(cmd.get("command", "")).upper()
        if name not in ("PING", "GET_INFO", "READ_SENSOR", "SET_RATE", "STOP_TEST", "START_STREAM", "STOP_STREAM", "OLED"):
            return fail("Only diagnostic commands can be sent from the console")
        cmd["command"] = name
        if not serial_mgr.send(cmd):
            return fail("Serial port is not open")
        return ok()

    @app.route("/api/serial/log")
    def api_serial_log():
        return ok(list(serial_mgr.log))

    # ------------------------------------------------------------ simulation / acquisition
    @app.route("/api/sim/profiles")
    def api_sim_profiles():
        return ok(database.list_sim_profiles(), overrides=OVERRIDES)

    @app.route("/api/sim/select", methods=["POST"])
    def api_sim_select():
        b = body()
        if any(r.is_alive() for r in engine.runners.values()):
            return fail("An inspection is running")
        if b.get("profile_id"):
            sim.select(b["profile_id"], b.get("override"))
        elif b.get("override"):
            sim.set_override(b["override"])
        database.save_settings({"simulation": {"profile_id": sim.profile["id"], "stream_override": sim.override}})
        return ok({"profile_id": sim.profile["id"], "override": sim.override})

    @app.route("/api/sim/control", methods=["POST"])
    def api_sim_control():
        action = body().get("action")
        if action == "pause":
            sim.pause()
        elif action == "resume":
            sim.resume()
        elif action == "reset":
            sim.reset()
        else:
            return fail("action must be pause, resume or reset")
        return ok({"paused": sim.paused})

    @app.route("/api/acq/window")
    def api_window():
        secs = float(request.args.get("seconds", 60))
        return ok(hub.window(secs), stats=hub.stats(), device=hub.device_status())

    @app.route("/api/acq/reset", methods=["POST"])
    def api_acq_reset():
        hub.reset("user")
        return ok()

    @app.route("/api/acq/logging", methods=["POST"])
    def api_logging():
        en = bool(body().get("enabled"))
        hub.set_csv_logging(en)
        database.save_settings({"csv_logging": en})
        return ok({"enabled": en, "path": getattr(hub, "csv_path", None) if en else None})

    def csv_response(rows, header, name):
        buf = io.StringIO()
        pd.DataFrame(rows, columns=header).to_csv(buf, index=False)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.route("/api/acq/export.csv")
    def api_acq_export():
        scope = request.args.get("scope", "window")
        if scope == "session":
            rows = database.get_measurements(session_id=hub.session_id)
            data = [[r["iso"], r["source"], r["bus_v"], r["shunt_mv"], r["current_ma"], r["power_mw"], r["sensor"], r["valid"],
                     r["reason"] or "", r["inspection_id"] or "", r["phase"] or ""] for r in rows]
        else:
            secs = float(request.args.get("seconds", 60))
            data = [[s["iso"], s["src"], s["v"], s["shunt_mv"], s["i"], s["p"], s["sensor"], int(s["valid"]), s["why"] or "",
                     s["insp"] or "", s["phase"] or ""] for s in hub.window(secs)]
        name = f"icvision-{hub.mode}-{scope}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        return csv_response(data, ["timestamp_utc", "source", "bus_voltage_v", "shunt_voltage_mv", "current_ma", "power_mw",
                                   "sensor", "valid", "invalid_reason", "inspection_id", "phase"], name)

    # ------------------------------------------------------------ profiles
    @app.route("/api/profiles", methods=["GET", "POST"])
    def api_profiles():
        if request.method == "GET":
            return ok(database.list_profiles())
        b = body()
        return ok(database.save_profile(_custom_profile(b)))

    @app.route("/api/profiles/<pid>", methods=["GET", "DELETE"])
    def api_profile(pid):
        if request.method == "DELETE":
            p = database.get_profile(pid)
            if not p or p.get("builtin"):
                return fail("Built-in profiles cannot be deleted")
            database.delete_profile(pid)
            return ok()
        p = database.get_profile(pid)
        if not p:
            abort(404)
        return ok(p, baselines=database.list_baselines(pid))

    # ------------------------------------------------------------ references / manufacturers
    @app.route("/api/manufacturers", methods=["GET", "POST"])
    def api_mfrs():
        if request.method == "GET":
            return ok(database.list_manufacturers())
        b = body()
        name = (b.get("name") or "").strip()
        if not name:
            return fail("Manufacturer name is required")
        mid = re.sub(r"[^a-z0-9]+", "-", (b.get("id") or name).lower()).strip("-")
        aliases = [a.strip().upper() for a in (b.get("aliases") or "").split(",") if a.strip()] if isinstance(b.get("aliases"), str) else b.get("aliases", [])
        return ok(database.save_manufacturer({"id": mid, "name": name, "aliases": aliases, "website": b.get("website", ""),
                                              "notes": b.get("notes", "")}))

    @app.route("/api/references", methods=["GET", "POST"])
    def api_refs():
        if request.method == "GET":
            return ok(database.list_references())
        b = body()
        for f in ("manufacturer_id", "part_number", "base_part", "category"):
            if not b.get(f):
                return fail(f"{f.replace('_', ' ')} is required")
        if not database.get_manufacturer(b["manufacturer_id"]):
            return fail("Unknown manufacturer — add it first")
        url = (b.get("datasheet") or {}).get("url", "")
        if url and not re.match(r"^https?://", url):
            return fail("Datasheet URL must start with http:// or https://")
        rid = b.get("id") or "ref-" + re.sub(r"[^a-z0-9]+", "-", f"{b['manufacturer_id']}-{b['part_number']}".lower()).strip("-")
        rec = {"id": rid, "manufacturer_id": b["manufacturer_id"], "part_number": b["part_number"].strip(),
               "base_part": b["base_part"].strip().upper(), "category": b["category"], "description": b.get("description", ""),
               "package": b.get("package", ""), "profile_id": b.get("profile_id"), "datasheet": b.get("datasheet") or {},
               "match_patterns": b.get("match_patterns") or [b["part_number"].upper(), b["base_part"].upper()],
               "legacy_types": b.get("legacy_types", []), "functional_description": b.get("functional_description", ""),
               "test_conditions": b.get("test_conditions", ""), "specs": b.get("specs", []),
               "verification": b.get("verification") or {"status": "unverified", "checked_on": time.strftime("%Y-%m-%d"),
                                                          "note": "Entered by operator; not verified against the datasheet."},
               "dataset_version": "user"}
        return ok(database.save_reference(rec))

    @app.route("/api/references/<rid>", methods=["GET", "PUT", "DELETE"])
    def api_ref(rid):
        if request.method == "GET":
            r = database.get_reference(rid)
            if not r:
                abort(404)
            return ok(r)
        if request.method == "DELETE":
            database.delete_reference(rid)
            return ok()
        r = database.get_reference(rid)
        if not r:
            abort(404)
        b = body()
        for k in ("verification", "specs", "match_patterns", "datasheet", "description", "package", "legacy_types", "profile_id"):
            if k in b:
                r[k] = b[k]
        return ok(database.save_reference(r))

    @app.route("/api/references/restore", methods=["POST"])
    def api_ref_restore():
        database.restore_builtin_references()
        return ok()

    @app.route("/api/identify", methods=["POST"])
    def api_identify():
        text = body().get("text", "")
        return ok(identify.identify(text, database.list_references(), database.list_manufacturers()))

    @app.route("/api/baselines/<int:bid>", methods=["DELETE"])
    def api_baseline_del(bid):
        database.delete_baseline(bid)
        return ok()

    # ------------------------------------------------------------ OCR
    @app.route("/api/ocr/status")
    def api_ocr_status():
        return ok(ocr_engine.tesseract_status())

    def _ocr_payload(image_id, synthetic=False, sim_profile_id=None):
        refs, mfrs = database.list_references(), database.list_manufacturers()

        def scorer(text):
            r = identify.identify(text, refs, mfrs)
            return r["candidates"][0]["score"] if r["candidates"] else 0.0

        res = ocr_engine.run_ocr(image_id, scorer)
        res["identification"] = identify.identify(res.get("text", ""), refs, mfrs)
        res["synthetic"] = synthetic
        res["sim_profile_id"] = sim_profile_id
        res["image_url"] = f"/api/images/{image_id}"
        return res

    @app.route("/api/ocr", methods=["POST"])
    def api_ocr():
        f = request.files.get("image")
        if not f:
            return fail("Attach an image file")
        image_id = ocr_engine.save_upload(f)
        return ok(_ocr_payload(image_id))

    @app.route("/api/ocr/sample", methods=["POST"])
    def api_ocr_sample():
        sid = body().get("sim_profile_id")
        sp = database.get_sim_profile(sid)
        if not sp:
            return fail("Unknown simulation profile")
        path = os.path.join(config.SAMPLE_DIR, f"{sid}.png")
        image_id = ocr_engine.save_image_file(path)
        return ok(_ocr_payload(image_id, synthetic=True, sim_profile_id=sid), sim_profile=sp)

    @app.route("/api/optical/compare", methods=["POST"])
    def api_compare():
        b = body()
        ref_path, prov = ocr_engine.reference_image_for(b.get("reference_id"), b.get("profile_id"), b.get("sim_profile_id"))
        if not ref_path:
            return ok(None, reason="No reference image stored for this part")
        res = ocr_engine.compare_images(ocr_engine.image_path(b["image_id"]), ref_path)
        if res:
            res["provenance"] = prov
            res["reference_url"] = ("/samples/" + os.path.basename(ref_path)) if prov == "simulated" else None
        return ok(res)

    # ------------------------------------------------------------ inspections
    @app.route("/api/inspections", methods=["GET", "POST"])
    def api_inspections():
        if request.method == "POST":
            return ok(engine.create(body()))
        a = request.args
        rows = database.list_inspections(q=a.get("q"), profile_id=a.get("profile_id"), manufacturer=a.get("manufacturer"),
                                         status=a.get("status"), result=a.get("result"), date_from=a.get("from"),
                                         date_to=a.get("to"), include_drafts=a.get("drafts", "1") == "1")
        return ok(rows)

    @app.route("/api/inspections/export.csv")
    def api_ins_export():
        rows = database.list_inspections(include_drafts=False, limit=100000)
        out = []
        for r in rows:
            ins = database.get_inspection(r["id"]) or {}
            dims = {d["key"]: d for d in (ins.get("scores") or {}).get("dimensions", [])}
            st = ((ins.get("measurements") or {}).get("static") or {})
            out.append([r["id"], r["created_at"], r["data_source"], r["status"], r["component_name"], r["manufacturer"],
                        r["part_number"], r["mode"], (st.get("v") or {}).get("mean"), (st.get("i") or {}).get("mean"),
                        (st.get("i") or {}).get("std"), (dims.get("electrical") or {}).get("score"),
                        (dims.get("optical") or {}).get("score"), (dims.get("manufacturer") or {}).get("score"),
                        r["overall"], r["result"] or ""])
        return csv_response(out, ["inspection_id", "created_utc", "data_source", "status", "component", "manufacturer",
                                  "part_number", "mode", "supply_v_mean", "current_ma_mean", "current_ma_std",
                                  "electrical_score", "optical_score", "manufacturer_score", "overall_score", "screening_result"],
                            f"icvision-inspections-{time.strftime('%Y%m%d-%H%M%S')}.csv")

    @app.route("/api/inspections/<iid>", methods=["GET", "PUT", "DELETE"])
    def api_inspection(iid):
        if request.method == "GET":
            ins = engine.get(iid)
            ins["validation_preview"] = engine.validate(ins) if ins["status"] in ("draft", "error", "aborted") else None
            return ok(ins, report=database.get_report_for(iid))
        if request.method == "DELETE":
            if iid in engine.runners and engine.runners[iid].is_alive():
                return fail("Abort the inspection before deleting it")
            database.delete_inspection(iid)
            return ok()
        return ok(engine.update(iid, body()))

    @app.route("/api/inspections/<iid>/validate")
    def api_validate(iid):
        return ok(engine.validate(engine.get(iid)))

    @app.route("/api/inspections/<iid>/start", methods=["POST"])
    def api_start(iid):
        return ok(engine.start(iid))

    @app.route("/api/inspections/<iid>/abort", methods=["POST"])
    def api_abort(iid):
        engine.abort(iid)
        return ok()

    @app.route("/api/inspections/<iid>/prompt", methods=["POST"])
    def api_prompt(iid):
        engine.respond_prompt(iid, body().get("accept"))
        return ok()

    @app.route("/api/inspections/<iid>/finalize", methods=["POST"])
    def api_finalize(iid):
        b = body()
        return ok(engine.finalize(iid, b.get("optical_manual")))

    @app.route("/api/inspections/<iid>/baseline", methods=["POST"])
    def api_baseline(iid):
        return ok(engine.record_baseline(iid))

    @app.route("/api/inspections/<iid>/measurements.csv")
    def api_ins_meas(iid):
        rows = database.get_measurements(inspection_id=iid)
        data = [[r["iso"], r["source"], r["phase"], r["bus_v"], r["shunt_mv"], r["current_ma"], r["power_mw"], r["sensor"],
                 r["valid"], r["reason"] or ""] for r in rows]
        return csv_response(data, ["timestamp_utc", "source", "phase", "bus_voltage_v", "shunt_voltage_mv", "current_ma",
                                   "power_mw", "sensor", "valid", "invalid_reason"], f"{iid}-measurements.csv")

    @app.route("/api/inspections/<iid>/report", methods=["POST"])
    def api_report(iid):
        path, rid = report.build_report(iid)
        return ok({"report_id": rid, "url": f"/api/inspections/{iid}/report.pdf"})

    @app.route("/api/inspections/<iid>/report.pdf")
    def api_report_pdf(iid):
        rep = database.get_report_for(iid)
        if not rep or not os.path.exists(rep["path"]):
            path, _ = report.build_report(iid)
        else:
            path = rep["path"]
        dl = request.args.get("download") == "1"
        return send_file(path, mimetype="application/pdf", as_attachment=dl, download_name=os.path.basename(path), max_age=0)

    @app.route("/api/reports")
    def api_reports():
        reps = database.list_reports()
        for r in reps:
            ins = database.get_inspection(r["inspection_id"]) or {}
            r["component"] = (ins.get("component") or {}).get("name")
            r["part_number"] = (ins.get("component") or {}).get("part_number")
            r["result"] = (ins.get("scores") or {}).get("classification")
            r["overall"] = (ins.get("scores") or {}).get("overall")
            r["url"] = f"/api/inspections/{r['inspection_id']}/report.pdf"
        return ok(reps)

    return app


def _custom_profile(b):
    name = (b.get("part_number") or "").strip()
    if not name:
        raise ValueError("Part number is required")
    try:
        vmin = float(b.get("supply_min_v")) if b.get("supply_min_v") not in (None, "") else None
        vmax = float(b.get("supply_max_v")) if b.get("supply_max_v") not in (None, "") else None
    except ValueError:
        raise ValueError("Supply limits must be numbers")
    rails = [r for r in (3.3, 5.0) if (vmin is None or r >= vmin) and (vmax is None or r <= vmax)]
    if vmin is not None and vmax is not None and not rails:
        raise ValueError("Neither the 3.3 V nor the 5 V rail is inside the stated supply range")
    package = (b.get("package") or "DIP-14").upper()
    m = re.match(r"DIP-?(\d+)", package)
    n = int(m.group(1)) if m else 14
    pins = []
    spec = (b.get("pin_config") or "").strip()
    parsed = {}
    for part in re.split(r"[,\n;]+", spec):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                parsed[int(k.strip())] = v.strip()
            except ValueError:
                pass
    for k in range(1, n + 1):
        nm = parsed.get(k, str(k))
        role = "power" if nm.upper() in ("VCC", "VDD", "V+") else "ground" if nm.upper() in ("GND", "VSS", "V-") else "nc"
        pins.append({"pin": k, "name": nm, "role": role})
    vcc = [p["pin"] for p in pins if p["role"] == "power"] or [n]
    gnd = [p["pin"] for p in pins if p["role"] == "ground"] or [n // 2]
    pid = "custom-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return {
        "id": pid, "name": f"{name} (custom)", "short_name": name, "category": b.get("category") or "custom",
        "function": b.get("function", ""), "base_part": name.upper(), "logic_family": "",
        "default_reference_id": b.get("reference_id"), "package": f"DIP-{n}", "package_key": f"DIP-{n}",
        "physical_demo": False, "pins": pins,
        "supply": {"vcc_pins": vcc, "gnd_pins": gnd, "min_v": vmin, "max_v": vmax, "allowed_rails": rails or [3.3, 5.0],
                   "default_rail": (rails or [3.3])[0]},
        "functional_test": None,
        "supported_measurements": ["bus_voltage", "shunt_voltage", "current", "power", "current_stability"],
        "evaluation": [
            {"key": "supply_voltage", "label": "Supply voltage at DUT", "kind": "supply_condition", "unit": "V", "weight": 0.10,
             "mandatory": True, "spec": "vcc_range"},
            {"key": "supply_current", "label": "Supply current", "kind": "current_limit", "unit": "mA", "weight": 0.40,
             "mandatory": True, "spec_order": ["icc_static_max", "icc_package", "icc_max"]},
            {"key": "current_stability", "label": "Supply-current stability", "kind": "stability", "unit": "mA", "weight": 0.15,
             "mandatory": False, "limits": {"pass_abs_ma": 0.15, "warn_abs_ma": 0.40, "pass_cv": 0.05, "warn_cv": 0.12}},
        ],
        "preconditions": [{"key": "supply_path", "label": "VCC pin is fed through the current sensor"},
                          {"key": "rating_confirmed", "label": "Operator has confirmed the rail against the datasheet"}],
        "limitations": ["Custom profile: only supply measurements are available until a functional test is defined.",
                        "Supply current can only be compared once a reference record or a known-good baseline exists."],
        "custom": {"manufacturer": b.get("manufacturer", ""), "datasheet_url": b.get("datasheet_url", ""),
                   "pin_config": spec},
        "builtin": False,
    }
