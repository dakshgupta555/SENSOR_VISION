"""Simulation mode: a generated measurement stream and an emulated test controller.

Every value produced here is tagged source="sim" by the hub and labelled SIMULATED in the UI,
the database, CSV exports and PDF reports. The emulated controller answers the same JSON
commands as the ESP32 firmware, so the inspection engine runs one code path for both modes.
"""
import math
import random
import threading
import time

from . import database

OVERRIDES = {
    "profile": "Profile default",
    "normal": "Normal current",
    "abnormal": "Abnormal current",
    "unstable": "Unstable current",
}


class SimulationSource:
    def __init__(self, hub):
        self.hub = hub
        self.profile = None
        self.override = "profile"
        self.paused = False
        self.lock = threading.RLock()
        self._reset_state()
        self._thread = threading.Thread(target=self._loop, name="icv-sim", daemon=True)
        self._thread.start()

    def _reset_state(self):
        self.t0 = time.time()
        self.wander = 0.0
        self.spike_until = 0.0
        self.spike_amp = 0.0
        self.bump_until = 0.0
        self.bump_ma = 0.0
        self.rng = random.Random()

    # ------------------------------------------------------------ control
    def select(self, profile_id, override=None):
        prof = database.get_sim_profile(profile_id)
        if not prof:
            raise ValueError(f"Unknown simulation profile {profile_id}")
        with self.lock:
            self.profile = prof
            if override:
                if override not in OVERRIDES:
                    raise ValueError("Unknown stream behaviour")
                self.override = override
            self._reset_state()
        self.hub.reset(reason="sim_profile_changed")
        fields = dict(sim_profile=prof["id"], sim_override=self.override)
        if self.hub.mode == "simulation":
            fields.update(state="paused" if self.paused else "simulating", sensor="SIM-INA219",
                          resolution_ma=prof["stream"].get("quantize_ma", 0.1), firmware="simulator", device_id="SIMULATED")
        self.hub.update_device(**fields)
        return prof

    def set_override(self, override):
        if override not in OVERRIDES:
            raise ValueError("Unknown stream behaviour")
        with self.lock:
            self.override = override
            self.wander = 0.0
        self.hub.update_device(sim_override=override)

    def pause(self):
        self.paused = True
        self.hub.update_device(paused=True, state="paused")

    def resume(self):
        self.paused = False
        self.hub.update_device(paused=False, state="simulating")

    def reset(self):
        with self.lock:
            self._reset_state()
        self.hub.reset(reason="sim_reset")

    def bump(self, ma, seconds):
        with self.lock:
            self.bump_ma = ma
            self.bump_until = time.time() + seconds

    # ------------------------------------------------------------ generator
    def params(self):
        p = dict(self.profile["stream"]) if self.profile else {}
        o = self.override
        if o == "normal":
            p.update(drift_ma_per_min=0.0, wander_ma=0.0, spike_rate_hz=0.0, i_noise=min(p.get("i_noise", 0.03), 0.04))
        elif o == "abnormal":
            p.update(i_ma=p.get("i_ma", 0.0) * 3.0 + 3.0, drift_ma_per_min=0.8)
        elif o == "unstable":
            p.update(wander_ma=0.08, spike_rate_hz=0.8, spike_ma=1.2, i_noise=p.get("i_noise", 0.03) * 3)
        return p

    def generate(self, rate_hz):
        p = self.params()
        now = time.time()
        t = now - self.t0
        rng = self.rng
        i = p.get("i_ma", 0.0) + p.get("drift_ma_per_min", 0.0) * t / 60.0
        w = p.get("wander_ma", 0.0)
        if w:
            self.wander = 0.97 * self.wander + rng.gauss(0, w)
            i += self.wander
        rr = p.get("ripple_hz")
        if rr:
            i += p.get("ripple_ma", 0.0) * (1 if math.sin(2 * math.pi * rr * t) >= 0 else -1)
        sr = p.get("spike_rate_hz", 0.0)
        if sr and now >= self.spike_until and rng.random() < sr / max(rate_hz, 1):
            self.spike_until = now + rng.uniform(0.2, 0.5)
            self.spike_amp = p.get("spike_ma", 0.0) * rng.uniform(0.6, 1.2)
        if now < self.spike_until:
            i += self.spike_amp
        if now < self.bump_until:
            i += self.bump_ma
        i += rng.gauss(0, p.get("i_noise", 0.0))
        q = p.get("quantize_ma", 0.0)
        if q:
            i = round(i / q) * q
        v = p.get("vbus", 3.3) + rng.gauss(0, p.get("vbus_noise", 0.0)) - max(i, 0) * 0.0001
        v = round(round(v / 0.004) * 0.004, 4)  # INA219 bus LSB is 4 mV
        return {"v": v, "i": round(i, 4), "p": round(v * i, 4), "shunt_mv": round(i * 0.1, 4),
                "sensor": "SIM-INA219", "dev_ms": int(t * 1000)}

    def _loop(self):
        next_t = time.time()
        while True:
            rate = float(self.hub.settings["acquisition"].get("rate_hz", 10) or 10)
            rate = min(max(rate, 1.0), 50.0)
            next_t += 1.0 / rate
            delay = next_t - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.time()
            if self.hub.mode != "simulation" or self.paused or not self.profile:
                continue
            with self.lock:
                sample = self.generate(rate)
            self.hub.ingest(sample, "sim")


class SimulatedController:
    """Answers firmware commands with simulated results drawn from the active simulation profile."""

    def __init__(self, source):
        self.source = source
        self.listeners = []
        self._stop = threading.Event()

    def is_open(self):
        return True

    def add_listener(self, fn):
        self.listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def _emit(self, msg):
        msg.setdefault("simulated", True)
        msg["host_ms"] = int(time.time() * 1000)
        for fn in list(self.listeners):
            try:
                fn(msg)
            except Exception:
                pass

    def send(self, cmd):
        name = cmd.get("command")
        if name in ("PREPARE", "RELEASE", "STOP_TEST", "PING", "GET_INFO", "SET_RATE", "START_STREAM"):
            self._emit({"type": "ack", "command": name})
            return True
        if name == "START_TEST":
            threading.Thread(target=self._run_test, args=(cmd,), daemon=True).start()
            return True
        if name == "IV_SWEEP":
            threading.Thread(target=self._run_iv, args=(cmd,), daemon=True).start()
            return True
        return False

    # ------------------------------------------------------------ tests
    def _run_test(self, cmd):
        prof = self.source.profile or {}
        fn = prof.get("functional", {})
        stream = prof.get("stream", {})
        test = cmd.get("test")
        rng = random.Random()
        if test == "truth_table":
            fails = {(f["gate"], f["vector"]) for f in fn.get("fail_vectors", [])}
            gates = cmd.get("gates", [])
            vectors = cmd.get("vectors", [])
            total = len(gates) * len(vectors)
            done = 0
            self.source.bump(stream.get("functional_bump_ma", 0.0), 0.15 * total + 0.5)
            for g in gates:
                for vi, vec in enumerate(vectors):
                    time.sleep(0.12)
                    expected = vec[-1]
                    observed = expected if (g["name"], vi) not in fails else 1 - expected
                    vout = None
                    if cmd.get("adc"):
                        level = fn.get("voh_v", 3.29) if observed else fn.get("vol_v", 0.01)
                        vout = int(round(level * 1000 + rng.gauss(0, 8)))
                    done += 1
                    self._emit({"type": "functional_result", "test": "truth_table", "gate": g["name"],
                                "vector": vec[:-1], "expected": expected, "observed": observed, "v_out_mv": vout,
                                "pass": observed == expected})
                    self._emit({"type": "test_status", "stage": "functional", "progress": int(100 * done / max(total, 1))})
            self._emit({"type": "functional_done", "test": "truth_table", "total": total})
        elif test == "heartbeat":
            window = cmd.get("window_ms", 3000)
            hz = fn.get("heartbeat_hz")
            for k in range(5):
                time.sleep(window / 5000.0)
                self._emit({"type": "test_status", "stage": "functional", "progress": 20 * (k + 1)})
            if hz is None:
                self._emit({"type": "functional_result", "test": "heartbeat", "edges": 0, "window_ms": window, "freq_hz": 0.0})
            else:
                edges = int(round(hz * window / 1000.0))
                self._emit({"type": "functional_result", "test": "heartbeat", "edges": edges, "window_ms": window,
                            "freq_hz": round(hz + rng.gauss(0, 0.01), 3), "high_ratio": 0.5})
            self._emit({"type": "functional_done", "test": "heartbeat"})
        elif test == "uart_capture":
            window = cmd.get("window_ms", 4000)
            time.sleep(min(window / 1000.0, 1.5))
            match = cmd.get("match", "")
            line = None
            if match.startswith("ICV:SIG:") and fn.get("signature"):
                line = "ICV:SIG:" + fn["signature"]
            elif match.startswith("rst:") and fn.get("boot_log", True) and prof.get("component_profile_id") == "mcu-esp32-module":
                line = "rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)"
            self._emit({"type": "functional_result", "test": "uart_capture", "found": line is not None, "line": line,
                        "bytes": 64 if line else 0, "baud": cmd.get("baud")})
            self._emit({"type": "functional_done", "test": "uart_capture"})
        elif test == "analog_buffer":
            off = fn.get("offset_mv", 2.0)
            gerr = fn.get("gain_error", 0.0)
            noise = fn.get("adc_noise_mv", 6)
            pts = cmd.get("points_mv", [])
            for k, sp in enumerate(pts):
                time.sleep(0.25)
                vin = sp + rng.gauss(0, 12)  # 8-bit DAC is coarse; the input node is measured, not assumed
                vout = vin * (1 + gerr) + off + rng.gauss(0, noise)
                self._emit({"type": "functional_result", "test": "analog_buffer", "set_mv": sp,
                            "vin_mv": int(round(vin)), "vout_mv": int(round(vout))})
                self._emit({"type": "test_status", "stage": "functional", "progress": int(100 * (k + 1) / len(pts))})
            self._emit({"type": "functional_done", "test": "analog_buffer"})
        else:
            self._emit({"type": "error", "message": f"Unsupported test {test}"})

    def _run_iv(self, cmd):
        prof = self.source.profile or {}
        model = (prof.get("iv") or {}).get("model")
        steps = int(cmd.get("steps", 32))
        r = float(cmd.get("r_ohm", 10000))
        rng = random.Random()
        if not model:
            self._emit({"type": "error", "message": "No I-V model in this simulation profile"})
            self._emit({"type": "iv_done", "points": 0})
            return
        for k in range(steps + 1):
            time.sleep(0.04)
            vsrc = 3.2 * k / steps
            vnode, i_a = iv_solve(vsrc, r, model)
            self._emit({"type": "iv_point", "vsrc_mv": int(vsrc * 1000), "vnode_mv": int(round(vnode * 1000 + rng.gauss(0, 4))),
                        "i_ua": round(i_a * 1e6 + rng.gauss(0, 0.4), 2)})
        self._emit({"type": "iv_done", "points": steps + 1})


def diode_current(v, model):
    """Exponential pin-protection model: 10 µA at the knee voltage vk."""
    return 1e-5 * math.exp((v - model["vk"]) / model["n_slope"])


def iv_solve(vsrc, r, model):
    lo, hi = 0.0, vsrc
    for _ in range(60):
        mid = (lo + hi) / 2
        if (vsrc - mid) / r > diode_current(mid, model):
            lo = mid
        else:
            hi = mid
    v = (lo + hi) / 2
    return v, (vsrc - v) / r


def iv_curve(model, r=10000.0, steps=32):
    pts = []
    for k in range(steps + 1):
        vsrc = 3.2 * k / steps
        v, i = iv_solve(vsrc, r, model)
        pts.append({"vsrc": round(vsrc, 4), "v": round(v, 4), "i_ua": round(i * 1e6, 3)})
    return pts
