"""Acquisition hub: the single path every measurement takes, live or simulated.

Responsibilities
- validate and timestamp samples, tag them with source (live/sim), inspection and phase
- keep a rolling buffer and compute window statistics
- watch for stale/disconnected hardware and never present old readings as live
- batch samples into SQLite and (optionally) a CSV log
- broadcast measurement/device events to the browser over SSE
"""
import csv
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

from . import config, database
from .events import bus


def iso_from_ms(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AcquisitionHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.buffer = deque(maxlen=12000)
        self.seq = 0
        self.session_id = datetime.now().strftime("S%Y%m%d-%H%M%S")
        self.mode = "simulation"
        self.settings = database.get_settings()
        self.device = {
            "mode": "simulation", "state": "simulating", "port": None, "baud": None, "sensor": None,
            "firmware": None, "device_id": None, "shunt_ohm": None, "resolution_ma": None,
            "last_rx_ms": None, "last_sample_ms": None, "rx_rate_hz": 0.0, "invalid_lines": 0,
            "message": "", "sim_profile": None, "paused": False,
        }
        self.captures = {}  # inspection_id -> {"phase": str, "samples": list}
        self._pending_db = []
        self._csv_file = None
        self._csv_writer = None
        self._rate_window = deque(maxlen=100)
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._db_writer, name="icv-db-writer", daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name="icv-watchdog", daemon=True)
        self._writer.start()
        self._watchdog.start()

    # ------------------------------------------------------------ settings / mode
    def reload_settings(self):
        self.settings = database.get_settings()

    def set_mode(self, mode):
        with self.lock:
            changed = mode != self.mode
            self.mode = mode
            self.device["mode"] = mode
            if changed:
                self._clear_locked()
                if mode == "live":
                    self.device.update(state="disconnected", sensor=None, firmware=None, device_id=None,
                                       last_rx_ms=None, last_sample_ms=None, rx_rate_hz=0.0, message="", paused=False)
                else:
                    self.device.update(state="simulating", port=None, message="", paused=False)
        self.publish_device()
        if changed:
            bus.publish("acq_reset", {"mode": mode, "reason": "mode_changed"})

    def update_device(self, **fields):
        with self.lock:
            self.device.update(fields)
        self.publish_device()

    def publish_device(self):
        bus.publish("device", self.device_status())

    def device_status(self):
        with self.lock:
            d = dict(self.device)
        d["mode"] = self.mode
        d["now_ms"] = int(time.time() * 1000)
        d["session_id"] = self.session_id
        d["csv_logging"] = self._csv_file is not None
        return d

    # ------------------------------------------------------------ ingest
    def ingest(self, raw, source):
        """raw: dict with v (bus V), i (mA), optional shunt_mv, p (mW), dev_ms, sensor, ovf."""
        now_ms = int(time.time() * 1000)
        with self.lock:
            if source == "live" and self.mode != "live":
                return None
            if source == "sim" and self.mode != "simulation":
                return None
            self.seq += 1
            v = _num(raw.get("v"))
            i = _num(raw.get("i"))
            p = _num(raw.get("p"))
            if p is None and v is not None and i is not None:
                p = v * i
            sample = {
                "seq": self.seq, "t": now_ms, "iso": iso_from_ms(now_ms), "src": source,
                "dev_ms": raw.get("dev_ms"), "v": v, "shunt_mv": _num(raw.get("shunt_mv")), "i": i, "p": p,
                "sensor": raw.get("sensor") or self.device.get("sensor"), "valid": True, "why": None,
                "insp": None, "phase": None,
            }
            ok, why = self._validate(sample, raw)
            sample["valid"], sample["why"] = ok, why
            for iid, cap in self.captures.items():
                if cap.get("phase"):
                    sample["insp"], sample["phase"] = iid, cap["phase"]
                    cap["samples"].append(dict(sample))
                    break
            self.buffer.append(sample)
            self._rate_window.append(now_ms)
            self.device["last_sample_ms"] = now_ms
            self.device["last_rx_ms"] = now_ms
            if source == "live" and self.device.get("state") in ("connected", "stale"):
                self.device["state"] = "streaming"
                self.device["message"] = ""
                state_changed = True
            else:
                state_changed = False
            if len(self._rate_window) >= 2:
                span = (self._rate_window[-1] - self._rate_window[0]) / 1000.0
                self.device["rx_rate_hz"] = round((len(self._rate_window) - 1) / span, 2) if span > 0 else 0.0
            self._pending_db.append(self._db_row(sample))
            if self._csv_writer:
                try:
                    self._csv_writer.writerow([sample["iso"], sample["src"], sample["v"], sample["shunt_mv"], sample["i"],
                                               sample["p"], sample["sensor"], int(sample["valid"]), sample["why"] or "",
                                               sample["insp"] or "", sample["phase"] or ""])
                except Exception:
                    pass
            stats = self._stats_locked(self.settings["acquisition"].get("stats_window_s", 30))
        if state_changed:
            self.publish_device()
        bus.publish("measurement", {"sample": sample, "stats": stats})
        return sample

    def _validate(self, s, raw):
        if raw.get("ovf"):
            return False, "sensor overflow flag"
        if s["v"] is None or s["i"] is None:
            return False, "missing field"
        if not (0.0 <= s["v"] <= 36.0):
            return False, "bus voltage out of sensor range"
        if abs(s["i"]) > 3200:
            return False, "current out of sensor range"
        return True, None

    def _db_row(self, s):
        return {"session_id": self.session_id, "inspection_id": s["insp"], "phase": s["phase"], "seq": s["seq"],
                "t_ms": s["t"], "iso": s["iso"], "source": s["src"], "dev_ms": s["dev_ms"], "bus_v": s["v"],
                "shunt_mv": s["shunt_mv"], "current_ma": s["i"], "power_mw": s["p"], "sensor": s["sensor"],
                "valid": int(s["valid"]), "reason": s["why"]}

    # ------------------------------------------------------------ statistics
    def _stats_locked(self, window_s):
        if not self.buffer:
            return None
        cutoff = self.buffer[-1]["t"] - window_s * 1000
        pts = [s for s in self.buffer if s["t"] >= cutoff and s["valid"]]
        return compute_stats(pts, window_s)

    def stats(self, window_s=None):
        with self.lock:
            return self._stats_locked(window_s or self.settings["acquisition"].get("stats_window_s", 30))

    def window(self, seconds=60):
        with self.lock:
            if not self.buffer:
                return []
            cutoff = self.buffer[-1]["t"] - seconds * 1000
            return [s for s in self.buffer if s["t"] >= cutoff]

    def latest(self):
        with self.lock:
            return self.buffer[-1] if self.buffer else None

    # ------------------------------------------------------------ captures for inspections
    def begin_capture(self, inspection_id, phase):
        with self.lock:
            cap = self.captures.setdefault(inspection_id, {"phase": None, "samples": []})
            cap["phase"] = phase

    def set_phase(self, inspection_id, phase):
        with self.lock:
            if inspection_id in self.captures:
                self.captures[inspection_id]["phase"] = phase

    def end_capture(self, inspection_id):
        with self.lock:
            cap = self.captures.pop(inspection_id, None)
        return cap["samples"] if cap else []

    # ------------------------------------------------------------ reset / logging
    def _clear_locked(self):
        self.buffer.clear()
        self._rate_window.clear()
        self.device["last_sample_ms"] = None

    def reset(self, reason="user"):
        with self.lock:
            self._clear_locked()
        bus.publish("acq_reset", {"mode": self.mode, "reason": reason})

    def set_csv_logging(self, enabled):
        with self.lock:
            if enabled and not self._csv_file:
                path = os.path.join(config.LOG_DIR, f"{self.session_id}-{datetime.now().strftime('%H%M%S')}.csv")
                self._csv_file = open(path, "w", newline="", encoding="utf-8")
                self._csv_writer = csv.writer(self._csv_file)
                self._csv_writer.writerow(["timestamp_utc", "source", "bus_voltage_v", "shunt_voltage_mv", "current_ma",
                                           "power_mw", "sensor", "valid", "invalid_reason", "inspection_id", "phase"])
                self.csv_path = path
            elif not enabled and self._csv_file:
                self._csv_file.close()
                self._csv_file = None
                self._csv_writer = None
        self.publish_device()

    # ------------------------------------------------------------ background threads
    def _db_writer(self):
        while not self._stop.is_set():
            time.sleep(1.0)
            with self.lock:
                rows, self._pending_db = self._pending_db, []
                if self._csv_file:
                    try:
                        self._csv_file.flush()
                    except Exception:
                        pass
            try:
                database.insert_measurements(rows)
            except Exception as exc:  # keep acquiring even if the disk hiccups
                print("[icv] measurement write failed:", exc)

    def flush(self):
        with self.lock:
            rows, self._pending_db = self._pending_db, []
        database.insert_measurements(rows)

    def _watch(self):
        while not self._stop.is_set():
            time.sleep(0.5)
            changed = False
            with self.lock:
                if self.mode == "live" and self.device.get("state") == "streaming":
                    stale_after = float(self.settings["acquisition"].get("stale_after_s", 2.0))
                    last = self.device.get("last_rx_ms") or 0
                    if time.time() * 1000 - last > stale_after * 1000:
                        self.device["state"] = "stale"
                        self.device["message"] = "No measurement received for {:.1f} s".format(stale_after)
                        self.device["rx_rate_hz"] = 0.0
                        changed = True
            if changed:
                self.publish_device()

    def stop(self):
        self._stop.set()


def _num(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def compute_stats(pts, window_s=None):
    """Statistics over valid samples (dicts with t, v, i, p)."""
    if not pts:
        return {"n": 0, "window_s": window_s}
    t = np.array([s["t"] for s in pts], dtype=float)
    out = {"n": len(pts), "window_s": window_s, "first_t": int(t[0]), "last_t": int(t[-1])}
    for key in ("v", "i", "p"):
        arr = np.array([s[key] for s in pts if s.get(key) is not None], dtype=float)
        if arr.size:
            out[key] = {"min": float(arr.min()), "max": float(arr.max()), "mean": float(arr.mean()),
                        "std": float(arr.std(ddof=1)) if arr.size > 1 else 0.0}
    i = out.get("i")
    if i:
        out["cv"] = (i["std"] / abs(i["mean"])) if abs(i["mean"]) > 1e-9 else None
        if len(pts) >= 10 and t[-1] > t[0]:
            ii = np.array([s["i"] for s in pts], dtype=float)
            minutes = (t - t[0]) / 60000.0
            slope, resid_sd = _theil_sen(minutes, ii)
            span_min = float(minutes[-1])
            change = abs(slope) * span_min
            out["drift_ma_per_min"] = float(slope)
            out["drift_change_ma"] = float(change)
            # A trend counts only if the change across the window clearly exceeds the residual noise.
            out["drift_significant"] = bool(change > 2.5 * max(resid_sd, 1e-6) and len(pts) >= 20)
    span = (t[-1] - t[0]) / 1000.0
    out["rate_hz"] = round((len(pts) - 1) / span, 2) if span > 0 else None
    return out


def _theil_sen(x, y, max_points=160):
    """Median-of-pairwise-slopes trend: robust to the current spikes a damaged part produces."""
    if len(x) > max_points:
        idx = np.linspace(0, len(x) - 1, max_points).astype(int)
        x, y = x[idx], y[idx]
    dx = x[None, :] - x[:, None]
    dy = y[None, :] - y[:, None]
    mask = np.triu(np.ones_like(dx, dtype=bool), 1) & (dx > 0)
    slopes = dy[mask] / dx[mask]
    if slopes.size == 0:
        return 0.0, float(np.std(y))
    slope = float(np.median(slopes))
    intercept = float(np.median(y - slope * x))
    resid = y - (slope * x + intercept)
    return slope, float(np.std(resid))


hub = None


def get_hub():
    global hub
    if hub is None:
        hub = AcquisitionHub()
    return hub
