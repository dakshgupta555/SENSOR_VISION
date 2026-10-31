"""Live-mode serial path test using a pseudo-terminal.

A small in-process stand-in for the firmware's JSON protocol is attached to a pty so that the
real SerialManager / hub / inspection runner code paths can be exercised without hardware.
It reports device "ICV-PTY-TEST" and sensor "TEST" and exists only inside this test — it is not
a tool for producing live-looking data. Linux/macOS only (uses os.openpty).

    python -m tests.test_serial_link
"""
import json
import os
import tty
import sys
import tempfile
import threading
import time

os.environ.setdefault("ICV_DATA_DIR", tempfile.mkdtemp(prefix="icv-serial-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app  # noqa: E402


class PtyFirmware(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)  # no echo / line discipline on the test pty
        self.path = os.ttyname(self.slave)
        self.rate = 10.0
        self.stream = True
        self.alive = True
        self.levels = {}

    def send(self, obj):
        os.write(self.master, (json.dumps(obj) + "\n").encode())

    def run(self):
        buf = b""
        last = 0.0
        os.set_blocking(self.master, False)
        self.send({"type": "info", "device": "ICV-PTY-TEST", "firmware": "test", "protocol": 1, "sensor": "TEST", "shunt_ohm": 0.1})
        os.write(self.master, b"garbage line that is not json\n")
        while self.alive:
            try:
                buf += os.read(self.master, 1024)
            except BlockingIOError:
                pass
            except OSError:
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    cmd = json.loads(line)
                except ValueError:
                    continue
                self.handle(cmd)
            now = time.time()
            if self.stream and now - last >= 1.0 / self.rate:
                last = now
                self.send({"type": "measurement", "t": int(now * 1000) % 10 ** 8, "bus_v": 3.3, "shunt_mv": 0.0, "current_ma": 0.0,
                           "power_mw": 0.0, "sensor": "TEST", "ovf": False})
            time.sleep(0.005)

    def handle(self, cmd):
        c = cmd.get("command")
        if c == "GET_INFO":
            os.write(self.master, b"ets Jun  8 2016 boot noise, not JSON\n")
            self.send({"type": "info", "device": "ICV-PTY-TEST", "firmware": "test", "protocol": 1, "sensor": "TEST", "shunt_ohm": 0.1})
            return
        if c == "SET_RATE":
            self.rate = float(cmd["hz"])
        elif c == "START_TEST" and cmd.get("test") == "truth_table":
            for g in cmd["gates"]:
                for v in cmd["vectors"]:
                    obs = int(not (v[0] or v[1]))  # behaves as a NOR gate
                    self.send({"type": "functional_result", "test": "truth_table", "gate": g["name"], "vector": v[:2],
                               "expected": v[2], "observed": obs, "v_out_mv": None, "pass": obs == v[2]})
            self.send({"type": "functional_done", "test": "truth_table"})
            return
        if c:
            self.send({"type": "ack", "command": c})


def wait(pred, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.1)
    return False


def main():
    app = create_app()
    c = app.test_client()
    hub = app.extensions["icv"]["hub"]
    fw = PtyFirmware()
    fw.start()
    assert c.post("/api/mode", json={"mode": "live"}).get_json()["ok"]
    assert hub.device["state"] == "disconnected", hub.device
    r = c.post("/api/serial/connect", json={"port": fw.path, "baud": 115200}).get_json()
    assert r["ok"], r
    assert wait(lambda: hub.device["state"] == "streaming"), hub.device
    s = hub.latest()
    assert s["src"] == "live" and s["v"] == 3.3
    assert hub.device["device_id"] == "ICV-PTY-TEST" and hub.device["invalid_lines"] >= 1
    print("ok  connect → streaming, invalid line counted, samples tagged live")

    c.put("/api/settings", json={"fixture": {"wiring_verified": True}})
    ins = c.post("/api/inspections", json={"profile_id": "nor-74hc02-dip14", "config": {"duration_s": 3}}).get_json()["data"]
    iid = ins["id"]
    v = c.get(f"/api/inspections/{iid}/validate").get_json()["data"]
    assert not v["ok"] and any("preconditions" in e for e in v["errors"]), v
    c.put(f"/api/inspections/{iid}", json={"config": {"confirmations": {"supply_path": True, "inputs_defined": True, "series_resistors": True}}})
    v = c.get(f"/api/inspections/{iid}/validate").get_json()["data"]
    assert v["ok"], v
    assert c.post(f"/api/inspections/{iid}/start").get_json()["ok"]
    assert wait(lambda: c.get(f"/api/inspections/{iid}").get_json()["data"]["status"] in ("measured", "error"), 30)
    ins = c.get(f"/api/inspections/{iid}").get_json()["data"]
    assert ins["status"] == "measured", ins["run"]
    assert ins["data_source"] == "live" and ins["functional"]["status"] == "completed"
    tt = next(e for e in ins["electrical"]["evaluations"] if e["key"] == "functional")
    assert tt["status"] == "PASS", tt
    print("ok  live inspection: preconditions enforced, truth table over serial, data_source=live")

    # Start a second run, then cut the link mid-test.
    ins2 = c.post("/api/inspections", json={"profile_id": "nor-74hc02-dip14", "config": {"duration_s": 8,
                  "confirmations": {"supply_path": True, "inputs_defined": True, "series_resistors": True}}}).get_json()["data"]
    assert c.post(f"/api/inspections/{ins2['id']}/start").get_json()["ok"]
    time.sleep(2.5)
    fw.stream = False   # firmware goes silent (e.g. hung or unplugged)
    assert wait(lambda: hub.device["state"] == "stale", 6), hub.device
    last_seq = hub.latest()["seq"]
    assert wait(lambda: c.get(f"/api/inspections/{ins2['id']}").get_json()["data"]["status"] == "error", 10)
    err = c.get(f"/api/inspections/{ins2['id']}").get_json()["data"]["run"]["errors"][0]
    assert "No simulated values were substituted" in err, err
    time.sleep(1.0)
    assert hub.latest()["seq"] == last_seq, "no new samples may appear while the link is silent"
    print("ok  silent link → state stale, run stopped with real last-sample time, nothing substituted")

    fw.alive = False
    os.close(fw.master)
    assert wait(lambda: hub.device["state"] in ("disconnected", "reconnecting"), 8), hub.device
    print("ok  port closed → disconnected/reconnecting")
    c.post("/api/serial/disconnect")
    print("serial link tests passed")


if __name__ == "__main__":
    main()
