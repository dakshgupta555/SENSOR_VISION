"""Inspection workflow engine.

draft → (start) → running → measured → (finalize) → completed
                         ↘ error / aborted

The runner talks to a "controller": the ESP32 over serial in live mode, or the simulated
controller in simulation mode. Both answer the same JSON commands. In live mode a lost or
silent link stops the run; simulated values are never substituted.
"""
import queue
import threading
import time

from . import config, database, evaluation, ocr_engine, scoring, simulation
from .acquisition import compute_stats
from .events import bus

MODES = {
    "full": {"optical": True, "electrical": True, "manufacturer": True},
    "optical": {"optical": True, "electrical": False, "manufacturer": False},
    "electrical": {"optical": False, "electrical": True, "manufacturer": False},
    "manufacturer": {"optical": False, "electrical": False, "manufacturer": True},
}

STAGES = [
    ("preflight", "Pre-flight checks"),
    ("static", "Supply monitoring"),
    ("functional", "Functional test"),
    ("iv", "Pin I-V signature"),
    ("analysis", "Analysis"),
]


class InspectionError(Exception):
    pass


class InspectionEngine:
    def __init__(self, hub, serial_mgr, sim_source, sim_ctrl):
        self.hub = hub
        self.serial = serial_mgr
        self.sim = sim_source
        self.sim_ctrl = sim_ctrl
        self.runners = {}
        self.prompts = {}
        self.lock = threading.RLock()

    # ------------------------------------------------------------ create / update
    def create(self, payload):
        profile = database.get_profile(payload.get("profile_id"))
        if not profile:
            raise InspectionError("Select a component profile first")
        settings = database.get_settings()
        data_source = "live" if self.hub.mode == "live" else "simulated"
        ref_id = payload.get("reference_id", profile.get("default_reference_id"))
        reference = database.get_reference(ref_id) if ref_id else None
        mode = payload.get("mode", "full")
        cfg = {"mode": mode, **MODES.get(mode, MODES["full"]),
               "duration_s": 10, "rail_v": profile["supply"].get("default_rail"),
               "clock": profile.get("default_clock"), "functional": True, "iv": False,
               "conditions": {"ambient_c": None, "notes": ""}, "confirmations": {}}
        pcfg = {k: v for k, v in (payload.get("config") or {}).items() if v is not None}
        if pcfg.get("mode") in MODES:
            cfg.update(MODES[pcfg["mode"]])
        cfg.update(pcfg)
        comp = {"name": profile["name"], "part_number": reference["part_number"] if reference else "",
                "manufacturer": reference.get("manufacturer") if reference else "",
                "manufacturer_id": reference.get("manufacturer_id") if reference else None,
                "category": profile.get("category"), "package": profile.get("package"), "batch": "",
                "date_code": "", "datasheet_url": ((reference or {}).get("datasheet") or {}).get("url", ""),
                "identification_method": "manual"}
        comp.update({k: v for k, v in (payload.get("component") or {}).items() if v is not None})
        ins = {
            "id": database.next_inspection_id(), "created_at": database.now_iso(), "status": "draft",
            "data_source": data_source, "sim_profile_id": payload.get("sim_profile_id") if data_source == "simulated" else None,
            "profile_id": profile["id"], "reference_id": reference["id"] if reference else None,
            "operator": payload.get("operator") or settings.get("operator"), "component": comp,
            "image": payload.get("image"), "ocr": payload.get("ocr"), "config": cfg, "optical_manual": {},
            "run": {"stages": [], "progress": 0, "message": "", "errors": [], "warnings": []},
            "app_version": config.APP_VERSION, "reference_dataset_version": config.REFERENCE_DATASET_VERSION,
            "notes": payload.get("notes", ""),
        }
        if data_source == "simulated":
            ins["sim_profile_id"] = self._pick_sim_profile(ins)
            sp = database.get_sim_profile(ins["sim_profile_id"]) or {}
            ins["optical_manual"] = {k: dict(v, simulated=True) for k, v in (sp.get("optical") or {}).items()}
            if sp.get("clock") and profile.get("clock_options"):
                ins["config"]["clock"] = sp["clock"]
            if sp.get("rail_v") and not (payload.get("config") or {}).get("rail_v"):
                ins["config"]["rail_v"] = sp["rail_v"]
        database.save_inspection(ins)
        return ins

    def _pick_sim_profile(self, ins):
        requested = ins.get("sim_profile_id")
        sims = database.list_sim_profiles()
        by_id = {s["id"]: s for s in sims}
        if requested in by_id and by_id[requested]["component_profile_id"] == ins["profile_id"]:
            return requested
        cur = self.sim.profile["id"] if self.sim.profile else None
        if cur in by_id and by_id[cur]["component_profile_id"] == ins["profile_id"]:
            return cur
        for s in sims:
            if s["component_profile_id"] == ins["profile_id"]:
                return s["id"]
        return "sim-unknown"

    def update(self, iid, patch):
        ins = self.get(iid)
        if ins["status"] == "running":
            raise InspectionError("Inspection is running")
        if ins["status"] == "completed" and set(patch) - {"notes"}:
            ins["status"] = "measured" if ins.get("measurements") or not ins["config"].get("electrical") else "draft"
        for key in ("image", "ocr", "notes", "operator", "optical_manual"):
            if key in patch:
                ins[key] = patch[key]
        if "component" in patch:
            ins["component"] = {**(ins.get("component") or {}), **(patch["component"] or {})}
        if "config" in patch:
            cfg = dict(ins["config"])
            new = patch["config"] or {}
            if new.get("mode") in MODES:
                cfg.update(MODES[new["mode"]])
            cfg.update(new)
            if any(k in new for k in ("optical", "electrical", "manufacturer")) and "mode" not in new:
                cfg["mode"] = next((m for m, t in MODES.items() if all(cfg[k] == v for k, v in t.items())), "custom")
            ins["config"] = cfg
        if "profile_id" in patch and patch["profile_id"] != ins["profile_id"]:
            if ins["status"] != "draft":
                raise InspectionError("Profile can only change before the inspection runs")
            prof = database.get_profile(patch["profile_id"])
            if not prof:
                raise InspectionError("Unknown profile")
            ins["profile_id"] = prof["id"]
            ins["config"]["rail_v"] = prof["supply"].get("default_rail")
            ins["config"]["clock"] = prof.get("default_clock")
            ins["component"]["name"] = prof["name"]
            if ins["data_source"] == "simulated":
                ins["sim_profile_id"] = self._pick_sim_profile(ins)
        if "reference_id" in patch:
            ref = database.get_reference(patch["reference_id"]) if patch["reference_id"] else None
            ins["reference_id"] = ref["id"] if ref else None
            if ref:
                ins["component"].update(part_number=ins["component"].get("part_number") or ref["part_number"],
                                        manufacturer=ref.get("manufacturer"), manufacturer_id=ref.get("manufacturer_id"),
                                        datasheet_url=(ref.get("datasheet") or {}).get("url", ""))
            else:
                ins["component"].update(manufacturer_id=None, datasheet_url="")
        if "sim_profile_id" in patch and ins["data_source"] == "simulated" and ins["status"] == "draft":
            ins["sim_profile_id"] = patch["sim_profile_id"]
            ins["sim_profile_id"] = self._pick_sim_profile(ins)
        database.save_inspection(ins)
        self._publish(ins)
        return ins

    def get(self, iid):
        ins = database.get_inspection(iid)
        if not ins:
            raise InspectionError("Inspection not found")
        return ins

    # ------------------------------------------------------------ validation
    def validate(self, ins):
        profile = database.get_profile(ins["profile_id"])
        reference = database.get_reference(ins.get("reference_id"))
        settings = database.get_settings()
        cfg = ins["config"]
        errors, warnings, tests = [], [], {}
        sim = ins["data_source"] == "simulated"
        if ins["data_source"] == "live" and self.hub.mode != "live":
            errors.append("This inspection was created in live hardware mode; switch back to live mode or start a new inspection.")
        if sim and self.hub.mode != "simulation":
            errors.append("This inspection was created in simulation mode; switch back to simulation or start a new inspection.")
        if not any(cfg.get(k) for k in ("optical", "electrical", "manufacturer")):
            errors.append("Enable at least one inspection dimension.")
        if cfg.get("optical") and not (ins.get("image") or {}).get("image_id"):
            warnings.append("Optical inspection is enabled but no image was provided — only manual checks can be assessed.")
        if cfg.get("manufacturer") and not reference:
            warnings.append("No reference record selected — manufacturer verification will report 'Reference unavailable'.")
        if cfg.get("electrical"):
            rail = cfg.get("rail_v")
            sup = profile["supply"]
            if rail not in sup.get("allowed_rails", []):
                errors.append(f"Supply rail {rail} V is not permitted for this profile (allowed: {sup.get('allowed_rails')}).")
            lo, hi = sup.get("min_v"), sup.get("max_v")
            vspec = evaluation.spec_by_key(reference, "vcc_range")
            if vspec:
                lo = max(lo or 0, vspec.get("min") or 0)
                hi = min(hi or 99, vspec.get("max") or 99)
            if rail is not None and ((lo is not None and rail < lo) or (hi is not None and rail > hi)):
                errors.append(f"Supply rail {rail} V is outside the component rating ({lo}–{hi} V).")
            if lo is None and hi is None:
                warnings.append("No supply rating known for this component — the operator must confirm the rail is safe.")
            dur = cfg.get("duration_s", 10)
            if not (3 <= float(dur) <= 120):
                errors.append("Test duration must be between 3 and 120 seconds.")
            clock = cfg.get("clock")
            if clock and profile.get("clock_limits") and rail:
                mhz = float(clock.split()[0])
                allowed = max((c["max_mhz"] for c in profile["clock_limits"] if rail >= c["min_v"]), default=0)
                if mhz > allowed:
                    errors.append(f"{clock} exceeds the datasheet speed grade at {rail} V (max {allowed} MHz).")
            if not sim:
                st = self.hub.device.get("state")
                if st != "streaming":
                    errors.append("Live hardware is not streaming measurements — connect the ESP32 in Device Settings "
                                  "or switch to Simulation mode.")
                conf = cfg.get("confirmations") or {}
                missing = [p["label"] for p in profile.get("preconditions", []) if not conf.get(p["key"])]
                if missing:
                    errors.append("Confirm the test-fixture preconditions: " + "; ".join(missing))
            plan, reason = self.compose_functional(profile, settings, rail, sim)
            if not cfg.get("functional"):
                tests["functional"] = {"available": False, "reason": "Disabled for this inspection"}
            elif plan:
                tests["functional"] = {"available": True, "type": plan["type"],
                                       "note": "Simulated fixture" if sim else "Fixture map applied from Device Settings"}
            else:
                tests["functional"] = {"available": False, "reason": reason}
                warnings.append(f"Functional test not configured: {reason}. It will be reported as not measured.")
            if cfg.get("iv"):
                ivplan, ivreason = self.compose_iv(profile, settings, sim, ins)
                tests["iv"] = {"available": bool(ivplan), "reason": ivreason}
                if not ivplan:
                    warnings.append(f"Pin I-V sweep unavailable: {ivreason}.")
            unsupported = [e["label"] for e in profile.get("evaluation", []) if e["kind"] in ("output_high", "output_low")
                           and not sim and not settings["fixture"].get("adc_sense_enabled")]
            if unsupported:
                tests["output_levels"] = {"available": False, "reason": "ADC sensing disabled in Device Settings"}
        return {"ok": not errors, "errors": errors, "warnings": warnings, "tests": tests,
                "data_source": ins["data_source"]}

    def compose_functional(self, profile, settings, rail, sim):
        ft = profile.get("functional_test")
        if not ft:
            return None, "No functional test is defined for this component profile"
        fx = settings["fixture"]
        fmap = (config.DEFAULT_FIXTURE if sim else fx.get("map", {})).get(profile.get("package_key"), {})
        if not sim:
            if not fx.get("wiring_verified"):
                return None, "fixture wiring is not marked as verified in Device Settings"
            if rail and rail > 3.4 and not fx.get("level_protection_5v"):
                return None, "a 5 V test needs level protection on ESP32 sense lines (confirm in Device Settings)"

        def gpio(pin):
            g = fmap.get(str(pin))
            return int(g) if g not in (None, "") else None

        t = ft["type"]
        if t == "logic_truth_table":
            gates, missing, drive = [], [], []
            for g in ft["gates"]:
                ins_g = [gpio(p) for p in g["inputs"]]
                out_g = gpio(g["output"])
                for p, gg in zip(g["inputs"] + [g["output"]], ins_g + [out_g]):
                    if gg is None:
                        missing.append(str(p))
                if out_g is not None and None not in ins_g:
                    if any(x in config.INPUT_ONLY_GPIO for x in ins_g):
                        return None, f"{g['name']} input mapped to an input-only GPIO"
                    gates.append({"name": g["name"], "in": ins_g, "out": out_g, "pins": g["inputs"] + [g["output"]]})
                    drive += ins_g
            if missing:
                return None, f"fixture map has no GPIO for DUT pin(s) {', '.join(sorted(set(missing), key=int))}"
            adc = True if sim else bool(fx.get("adc_sense_enabled"))
            cmd = {"command": "START_TEST", "test": "truth_table", "gates": gates, "vectors": ft["vectors"],
                   "settle_us": ft.get("settle_us", 200), "adc": adc}
            return {"type": t, "commands": [("truth_table", cmd)], "prepare": {"command": "PREPARE", "drive_low": drive}}, None
        if t == "mcu_firmware":
            hb, us = ft["heartbeat"], ft["uart_signature"]
            hb_g, rx_g = gpio(hb["pin"]), gpio(us["pin"])
            cmds = []
            if hb_g is not None:
                cmds.append(("heartbeat", {"command": "START_TEST", "test": "heartbeat", "pin": hb_g, "window_ms": hb["window_ms"]}))
            if rx_g is not None:
                cmds.append(("uart_signature", {"command": "START_TEST", "test": "uart_capture", "rx_pin": rx_g, "baud": us["baud"],
                                                "window_ms": us["window_ms"], "match": us["prefix"]}))
            if not cmds:
                return None, "fixture map has no GPIO for the heartbeat (pin 19) or TXD (pin 3) lines"
            return {"type": t, "commands": cmds, "prepare": None}, None
        if t == "boot_log":
            u = ft["uart"]
            rx_g = gpio(u["pin"])
            if rx_g is None:
                return None, "fixture map has no GPIO for the module TXD0 line"
            return {"type": t, "commands": [("boot_log", {"command": "START_TEST", "test": "uart_capture", "rx_pin": rx_g,
                                                          "baud": u["baud"], "window_ms": u["window_ms"], "match": u["token"]})],
                    "prepare": None}, None
        if t == "analog_buffer":
            dac, s_in, s_out = gpio(ft["drive_pin"]), gpio(ft["sense_in_key"]), gpio(ft["sense_out_pin"])
            if dac not in (25, 26):
                return None, "the follower drive pin must map to a DAC GPIO (25 or 26)"
            if s_in is None or s_out is None:
                return None, "fixture map needs ADC sense GPIOs for the input node and OUT1"
            return {"type": t, "commands": [("analog_buffer", {"command": "START_TEST", "test": "analog_buffer", "dac_pin": dac,
                                                               "in_adc": s_in, "out_adc": s_out, "points_mv": ft["points_mv"],
                                                               "settle_ms": 30})], "prepare": None}, None
        return None, f"Functional test type {t} is not supported by this controller"

    def compose_iv(self, profile, settings, sim, ins):
        if not any(e["kind"] == "iv_signature" for e in profile.get("evaluation", [])):
            return None, "this profile defines no pin I-V signature"
        if sim:
            sp = database.get_sim_profile(ins.get("sim_profile_id")) or {}
            if not (sp.get("iv") or {}).get("model"):
                return None, "the simulation profile has no I-V model"
            pr = config.DEFAULT_SETTINGS["probe"]
        else:
            pr = settings["probe"]
            if not pr.get("enabled"):
                return None, "the I-V probe circuit is not enabled in Device Settings"
        return {"command": "IV_SWEEP", "dac_pin": pr["dac_pin"], "node_adc": pr["node_adc_pin"], "src_adc": pr["src_adc_pin"],
                "r_ohm": pr["r_ohm"], "steps": 32}, None

    # ------------------------------------------------------------ run control
    def start(self, iid):
        ins = self.get(iid)
        if ins["status"] == "running":
            raise InspectionError("Already running")
        v = self.validate(ins)
        if not v["ok"]:
            raise InspectionError(" ".join(v["errors"]))
        with self.lock:
            if any(r.is_alive() for r in self.runners.values()):
                raise InspectionError("Another inspection is running — wait for it to finish or abort it.")
        ins["status"] = "running"
        ins["run"] = {"started_at": database.now_iso(), "finished_at": None, "progress": 0, "message": "Starting",
                      "errors": [], "warnings": v["warnings"], "tests": v["tests"],
                      "stages": [{"key": k, "label": l, "status": "pending"} for k, l in STAGES]}
        ins["validation"] = v
        dev = self.hub.device_status()
        ins["device"] = {k: dev.get(k) for k in ("device_id", "firmware", "sensor", "shunt_ohm", "resolution_ma", "port", "baud")}
        if ins["data_source"] == "simulated":
            ins["device"].update(device_id="SIMULATED", firmware="simulator", sensor="SIM-INA219", resolution_ma=0.1)
        else:
            ins["device"]["label"] = database.get_settings().get("device_label")
        for key in ("measurements", "functional", "iv", "electrical", "optical", "manufacturer", "scores"):
            ins.pop(key, None)
        database.save_inspection(ins)
        runner = threading.Thread(target=self._run, args=(iid,), name=f"icv-run-{iid}", daemon=True)
        with self.lock:
            self.runners[iid] = runner
            self.aborts = getattr(self, "aborts", {})
            self.aborts[iid] = threading.Event()
        runner.start()
        self._publish(ins)
        return ins

    def abort(self, iid):
        ev = getattr(self, "aborts", {}).get(iid)
        if ev:
            ev.set()
        p = self.prompts.get(iid)
        if p:
            p["answer"] = False
            p["event"].set()

    def respond_prompt(self, iid, accept):
        p = self.prompts.get(iid)
        if not p:
            raise InspectionError("No pending prompt")
        p["answer"] = bool(accept)
        p["event"].set()

    # ------------------------------------------------------------ runner
    def _run(self, iid):
        ins = self.get(iid)
        profile = database.get_profile(ins["profile_id"])
        reference = database.get_reference(ins.get("reference_id"))
        settings = database.get_settings()
        cfg = ins["config"]
        sim = ins["data_source"] == "simulated"
        ctrl = self.sim_ctrl if sim else self.serial
        abort = self.aborts[iid]
        inbox = queue.Queue()
        listener = inbox.put
        ctrl.add_listener(listener)

        def stage(key, status, message=""):
            for s in ins["run"]["stages"]:
                if s["key"] == key:
                    s["status"] = status
                    s.setdefault("started", database.now_iso())
                    if status in ("done", "skipped", "error"):
                        s["finished"] = database.now_iso()
                    if message:
                        s["message"] = message
            ins["run"]["message"] = message or ins["run"]["message"]
            self._publish(ins)

        def check_link():
            if abort.is_set():
                raise InspectionError("Aborted by operator")
            if not sim and self.hub.device.get("state") != "streaming":
                ts = self.hub.device.get("last_sample_ms")
                raise InspectionError("Hardware link lost during the test (last real sample at "
                                      f"{time.strftime('%H:%M:%S', time.localtime(ts / 1000)) if ts else 'n/a'}). "
                                      "No simulated values were substituted.")

        try:
            if not cfg.get("electrical"):
                for k, _ in STAGES[:-1]:
                    stage(k, "skipped", "Electrical inspection not selected")
                ins["functional"] = {"status": "skipped", "reason": "Electrical inspection not selected"}
            else:
                # 1. pre-flight
                stage("preflight", "running", "Checking device and supply")
                if sim:
                    if not self.sim.profile or self.sim.profile["id"] != ins["sim_profile_id"]:
                        self.sim.select(ins["sim_profile_id"])
                    if self.sim.paused:
                        self.sim.resume()
                    time.sleep(1.0)
                else:
                    time.sleep(0.5)
                check_link()
                recent = [s for s in self.hub.window(1.5) if s["valid"]]
                if not recent:
                    raise InspectionError("No valid measurements received during pre-flight.")
                vmean = sum(s["v"] for s in recent) / len(recent)
                if abs(vmean - cfg["rail_v"]) / cfg["rail_v"] > 0.10:
                    raise InspectionError(f"Measured supply {vmean:.2f} V does not match the selected {cfg['rail_v']} V rail. "
                                          "Check the supply jumper before testing.")
                plan, reason = (self.compose_functional(profile, settings, cfg["rail_v"], sim) if cfg.get("functional")
                                else (None, "Disabled for this inspection"))
                if plan and plan.get("prepare"):
                    ctrl.send(plan["prepare"])
                    time.sleep(0.2)
                stage("preflight", "done", f"Supply {vmean:.3f} V on {cfg['rail_v']} V rail")
                # 2. static supply monitoring
                dur = float(cfg.get("duration_s", 10))
                stage("static", "running", f"Monitoring supply for {dur:.0f} s")
                self.hub.begin_capture(iid, "static")
                t0 = time.time()
                while time.time() - t0 < dur:
                    check_link()
                    time.sleep(0.25)
                    ins["run"]["progress"] = int(10 + 50 * (time.time() - t0) / dur)
                    self._progress(ins, "static")
                stage("static", "done", "Supply monitoring complete")
                # 3. functional
                if plan:
                    self.hub.set_phase(iid, "functional")
                    stage("functional", "running", "Running functional test")
                    ins["functional"] = self._run_functional(iid, ins, profile, plan, ctrl, inbox, check_link)
                    stage("functional", "done" if ins["functional"]["status"] == "completed" else "error",
                          ins["functional"].get("summary_text", ""))
                else:
                    ins["functional"] = {"status": "not_configured" if profile.get("functional_test") else "skipped",
                                         "reason": reason, "type": (profile.get("functional_test") or {}).get("type")}
                    stage("functional", "skipped", f"Functional test not configured: {reason}" if profile.get("functional_test")
                          else "No functional test for this profile")
                self.hub.set_phase(iid, None)
                if plan and not sim:
                    ctrl.send({"command": "RELEASE"})
                # 4. I-V
                if cfg.get("iv"):
                    ins["iv"] = self._run_iv(iid, ins, profile, settings, sim, ctrl, inbox, check_link, stage)
                else:
                    ins["iv"] = {"status": "skipped", "reason": "Pin I-V sweep not selected"}
                    stage("iv", "skipped", "Not selected")
            # 5. analysis
            stage("analysis", "running", "Evaluating measurements")
            samples = self.hub.end_capture(iid)
            self.hub.flush()
            self._analyse(ins, profile, reference, samples, settings)
            stage("analysis", "done", "Measurements evaluated")
            ins["status"] = "measured"
            ins["run"]["progress"] = 100
            ins["run"]["finished_at"] = database.now_iso()
            ins["run"]["message"] = "Measured — review optical checks and compute the screening result"
        except Exception as exc:
            self.hub.end_capture(iid)
            ins["status"] = "aborted" if abort.is_set() else "error"
            ins["run"]["errors"].append(str(exc))
            ins["run"]["message"] = str(exc)
            ins["run"]["finished_at"] = database.now_iso()
            for s in ins["run"]["stages"]:
                if s["status"] in ("running", "pending"):
                    s["status"] = "error" if s["status"] == "running" else "skipped"
            try:
                if not sim:
                    self.serial.send({"command": "STOP_TEST"})
                    self.serial.send({"command": "RELEASE"})
            except Exception:
                pass
        finally:
            ctrl.remove_listener(listener)
            database.save_inspection(ins)
            self._publish(ins)

    def _progress(self, ins, stage_key):
        bus.publish("inspection_progress", {"id": ins["id"], "stage": stage_key, "progress": ins["run"]["progress"]})

    def _wait_messages(self, inbox, until_types, timeout_s, check_link, on_msg):
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            check_link()
            try:
                msg = inbox.get(timeout=0.25)
            except queue.Empty:
                continue
            if msg.get("type") == "link_lost":
                raise InspectionError("Hardware link lost: " + msg.get("reason", ""))
            if msg.get("type") == "error":
                return "error", msg.get("message", "controller error")
            on_msg(msg)
            if msg.get("type") in until_types:
                return "done", None
        return "timeout", None

    def _run_functional(self, iid, ins, profile, plan, ctrl, inbox, check_link):
        ft = profile["functional_test"]
        result = {"type": ft["type"], "status": "completed", "rows": [], "results": {}, "simulated": ins["data_source"] == "simulated"}
        vec_map = {tuple(v[:-1]): v[-1] for v in ft.get("vectors", [])}
        statuses = []
        for key, cmd in plan["commands"]:
            while not inbox.empty():
                inbox.get_nowait()
            if not ctrl.send(cmd):
                statuses.append("error")
                result.setdefault("errors", []).append(f"{key}: command could not be sent")
                continue

            def on_msg(msg, key=key):
                if msg.get("type") == "functional_result":
                    if msg.get("test") == "truth_table":
                        vec = tuple(msg.get("vector", []))
                        exp = vec_map.get(vec)
                        row = {"gate": msg.get("gate"), "vector": list(vec), "expected": exp, "observed": msg.get("observed"),
                               "v_out_mv": msg.get("v_out_mv"), "pass": exp is not None and msg.get("observed") == exp}
                        result["rows"].append(row)
                        bus.publish("functional_row", {"id": iid, "row": row})
                    elif msg.get("test") == "analog_buffer":
                        row = {k: msg.get(k) for k in ("set_mv", "vin_mv", "vout_mv")}
                        result["rows"].append(row)
                        bus.publish("functional_row", {"id": iid, "row": row})
                    else:
                        result["results"][key] = {k: v for k, v in msg.items() if k not in ("type",)}
                        bus.publish("functional_row", {"id": iid, "result": key, "data": result["results"][key]})
                elif msg.get("type") == "test_status":
                    ins["run"]["progress"] = 60 + int(0.25 * (msg.get("progress") or 0))
                    self._progress(ins, "functional")

            window = (cmd.get("window_ms") or 0) / 1000.0
            st, err = self._wait_messages(inbox, ("functional_done",), 15 + window, check_link, on_msg)
            statuses.append(st)
            if err:
                result.setdefault("errors", []).append(f"{key}: {err}")
        if all(s == "done" for s in statuses):
            result["status"] = "completed"
        elif any(s == "done" for s in statuses):
            result["status"] = "partial"
        elif "timeout" in statuses:
            result["status"] = "timeout"
            result["reason"] = "No response from the test controller within the time limit"
        else:
            result["status"] = "error"
            result["reason"] = "; ".join(result.get("errors", [])) or "Functional test error"
        if ft["type"] == "logic_truth_table":
            ok = sum(1 for r in result["rows"] if r["pass"])
            result["summary_text"] = f"{ok}/{len(result['rows'])} vectors match"
        else:
            result["summary_text"] = f"{len(result['results']) + (1 if result['rows'] else 0)} test(s) returned"
        return result

    def _run_iv(self, iid, ins, profile, settings, sim, ctrl, inbox, check_link, stage):
        cmd, reason = self.compose_iv(profile, settings, sim, ins)
        if not cmd:
            stage("iv", "skipped", reason)
            return {"status": "skipped", "reason": f"Pin I-V sweep unavailable: {reason}"}
        probe_pin = next((e.get("probe_pin") for e in profile["evaluation"] if e["kind"] == "iv_signature"), None)
        if not sim:
            stage("iv", "waiting", "Waiting for operator")
            ev = threading.Event()
            self.prompts[iid] = {"event": ev, "answer": None}
            bus.publish("inspection_prompt", {
                "id": iid, "key": "iv_probe", "title": "Prepare the pin I-V sweep",
                "message": f"Disconnect DUT VCC from the current sensor, tie DUT VCC to GND, and place the I-V probe on pin {probe_pin}. "
                           "The sweep is limited to 3.2 V through the probe resistor."})
            ev.wait(timeout=180)
            answer = self.prompts.pop(iid, {}).get("answer")
            bus.publish("inspection_prompt_closed", {"id": iid})
            if not answer:
                stage("iv", "skipped", "Operator skipped the I-V sweep")
                return {"status": "skipped", "reason": "Operator skipped the pin I-V sweep"}
        stage("iv", "running", f"Sweeping pin {probe_pin}")
        while not inbox.empty():
            inbox.get_nowait()
        pts = []

        def on_msg(msg):
            if msg.get("type") == "iv_point":
                p = {"vsrc": msg["vsrc_mv"] / 1000.0, "v": msg["vnode_mv"] / 1000.0, "i_ua": msg["i_ua"]}
                pts.append(p)
                bus.publish("iv_point", {"id": iid, "point": p})

        ctrl.send(cmd)
        st, err = self._wait_messages(inbox, ("iv_done",), 20, check_link, on_msg)
        ref = None
        if sim:
            sp = database.get_sim_profile(ins["sim_profile_id"]) or {}
            rm = (sp.get("iv") or {}).get("reference_model")
            if rm:
                ref = {"points": simulation.iv_curve(rm, cmd["r_ohm"], cmd["steps"]), "source": "simulated reference curve"}
        else:
            b = evaluation.find_baseline(database.list_baselines(ins["profile_id"]), "pin_iv", "live", {"rail_v": None})
            if b and (b.get("extra") or {}).get("points"):
                ref = {"points": b["extra"]["points"], "source": f"empirical known-good sample {b.get('source_inspection_id')}"}
        status = "completed" if st == "done" and pts else ("error" if err else "timeout")
        stage("iv", "done" if status == "completed" else "error", f"{len(pts)} points")
        return {"status": status, "points": pts, "reference": ref, "pin": probe_pin, "r_ohm": cmd["r_ohm"],
                "reason": err or ("No I-V points returned" if not pts else None), "simulated": sim}

    # ------------------------------------------------------------ analysis
    def _analyse(self, ins, profile, reference, samples, settings):
        cfg = ins["config"]
        if cfg.get("electrical"):
            static = [s for s in samples if s.get("phase") == "static"]
            valid = [s for s in static if s["valid"]]
            invalid = {}
            for s in static:
                if not s["valid"]:
                    invalid[s["why"]] = invalid.get(s["why"], 0) + 1
            func = [s for s in samples if s.get("phase") == "functional" and s["valid"]]
            t0 = samples[0]["t"] if samples else 0
            step = max(1, len(samples) // 600)
            series = [{"t": round((s["t"] - t0) / 1000.0, 3), "v": s["v"], "i": s["i"], "p": s["p"], "phase": s["phase"],
                       "valid": s["valid"]} for s in samples[::step]]
            ins["measurements"] = {
                "static": compute_stats(valid, cfg.get("duration_s")), "functional": compute_stats(func) if func else None,
                "n_total": len(static), "n_valid": len(valid), "n_invalid": len(static) - len(valid), "invalid_reasons": invalid,
                "first_iso": samples[0]["iso"] if samples else None, "last_iso": samples[-1]["iso"] if samples else None,
                "series": series, "source": ins["data_source"],
                "method": ("Simulated INA219 stream (generated)" if ins["data_source"] == "simulated" else
                           f"{ins['device'].get('sensor') or 'INA2xx'} high-side supply monitor via ESP32, USB serial"),
            }
            baselines = database.list_baselines(ins["profile_id"])
            evals = evaluation.evaluate_electrical(ins, profile, reference, baselines, settings)
            ins["electrical"] = {"evaluations": evals, "summary": evaluation.summarize_electrical(evals, settings)}
        else:
            ins["electrical"] = {"evaluations": [], "summary": {"status": "not_performed", "score": None, "reasons": ["Not selected"]}}

    def finalize(self, iid, optical_manual=None):
        ins = self.get(iid)
        if ins["status"] not in ("measured", "completed"):
            if ins["status"] == "draft" and not ins["config"].get("electrical"):
                ins["run"] = {"stages": [], "progress": 100, "message": "", "errors": [], "warnings": []}
                ins["electrical"] = {"evaluations": [], "summary": {"status": "not_performed", "score": None, "reasons": ["Not selected"]}}
            else:
                raise InspectionError("Run the electrical test before computing the screening result")
        settings = database.get_settings()
        profile = database.get_profile(ins["profile_id"])
        reference = database.get_reference(ins.get("reference_id"))
        if optical_manual is not None:
            ins["optical_manual"] = optical_manual
        compare = None
        img = ins.get("image") or {}
        if ins["config"].get("optical") and img.get("image_id"):
            sim_ref = img.get("sim_profile_id") if img.get("synthetic") else None
            ref_path, prov = ocr_engine.reference_image_for(ins.get("reference_id"), ins.get("profile_id"), sim_ref)
            if ref_path:
                compare = ocr_engine.compare_images(ocr_engine.image_path(img["image_id"]), ref_path)
                if compare:
                    compare["provenance"] = prov
                    compare["reference_image"] = ref_path
        if ins["config"].get("optical"):
            checks = scoring.build_optical_checks(ins, profile, ins.get("optical_manual"), compare)
            ins["optical"] = {"checks": checks, "image_compare": compare,
                              "summary": evaluation.summarize_optical(checks, settings)}
        else:
            ins["optical"] = {"checks": [], "summary": {"status": "not_performed", "score": None}}
        evals = (ins.get("electrical") or {}).get("evaluations", [])
        ins["manufacturer"] = (scoring.manufacturer_verification(ins, reference, evals) if ins["config"].get("manufacturer")
                               else {"status": "not_performed", "score": None, "factors": [], "reasons": ["Not selected"], "notes": []})
        ins["reference_snapshot"] = reference
        ins["scores"] = scoring.compute_scores(ins, settings)
        ins["status"] = "completed"
        ins["finalized_at"] = database.now_iso()
        database.save_inspection(ins)
        self._publish(ins)
        return ins

    def record_baseline(self, iid):
        ins = self.get(iid)
        if ins["status"] not in ("measured", "completed") or not ins.get("measurements"):
            raise InspectionError("Run the electrical test first")
        st = ins["measurements"].get("static") or {}
        if not st.get("i"):
            raise InspectionError("No valid current samples to record")
        cond = {"rail_v": ins["config"].get("rail_v"), "clock": ins["config"].get("clock")}
        made = []
        base = {"profile_id": ins["profile_id"], "reference_id": ins.get("reference_id"), "conditions": cond,
                "data_source": ins["data_source"], "source_inspection_id": iid}
        made.append(database.add_baseline(dict(base, param="supply_current", mean=st["i"]["mean"], std=st["i"]["std"],
                                               n=st["n"], unit="mA")))
        iv = ins.get("iv") or {}
        if iv.get("status") == "completed" and iv.get("points"):
            made.append(database.add_baseline(dict(base, param="pin_iv", mean=None, std=None, n=len(iv["points"]), unit="",
                                                   extra={"points": iv["points"], "pin": iv.get("pin")})))
        img = ins.get("image") or {}
        if img.get("image_id") and not img.get("synthetic"):
            ocr_engine.store_reference_image(img["image_id"], ins.get("reference_id") or ins["profile_id"])
        return made

    def _publish(self, ins):
        bus.publish("inspection", {"id": ins["id"], "status": ins["status"], "run": ins.get("run"),
                                   "data_source": ins.get("data_source")})
