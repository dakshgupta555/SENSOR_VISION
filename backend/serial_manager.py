"""USB serial link to the ESP32 test controller (newline-delimited JSON).

Nothing here fabricates data. If the port closes or goes quiet, the hub is told and the
dashboard shows the last real timestamp instead of a value.
"""
import json
import threading
import time
from collections import deque

from .events import bus

try:
    import serial
    import serial.tools.list_ports
    HAVE_SERIAL = True
except ImportError:  # pyserial missing: live mode reports it instead of crashing
    serial = None
    HAVE_SERIAL = False

INA_LSB_UV = {"INA219": 10.0, "INA226": 2.5}
ALLOWED_COMMANDS = {"PING", "GET_INFO", "READ_SENSOR", "SET_RATE", "STOP_TEST", "SET_MODE", "START_STREAM",
                    "STOP_STREAM", "START_TEST", "PREPARE", "RELEASE", "IV_SWEEP", "OLED"}


class SerialManager:
    def __init__(self, hub):
        self.hub = hub
        self.ser = None
        self.port = None
        self.baud = 115200
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.reader = None
        self.reconnector = None
        self.user_closed = True
        self.listeners = []
        self.log = deque(maxlen=500)
        self.info = {}
        self.invalid_lines = 0
        self.last_line_ms = None
        self.opened_ms = None

    # ------------------------------------------------------------ ports
    @staticmethod
    def list_ports():
        if not HAVE_SERIAL:
            return []
        out = []
        for p in serial.tools.list_ports.comports():
            out.append({"device": p.device, "description": p.description or "", "hwid": p.hwid or ""})
        return out

    def is_open(self):
        with self.lock:
            return bool(self.ser and self.ser.is_open)

    # ------------------------------------------------------------ connect / disconnect
    def connect(self, port, baud=115200, auto_reconnect=True):
        if not HAVE_SERIAL:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")
        if not port:
            raise ValueError("Select a serial port")
        self.disconnect(user=True, publish=False)
        with self.lock:
            self.port, self.baud = port, int(baud)
            self.auto_reconnect = auto_reconnect
            self.user_closed = False
        self.hub.update_device(state="connecting", port=port, baud=int(baud), message="Opening port")
        self._open()
        return True

    def _open(self):
        s = serial.Serial()
        s.port = self.port
        s.baudrate = self.baud
        s.timeout = 0.25
        s.write_timeout = 1.0
        # Keep DTR/RTS de-asserted so typical ESP32 auto-reset circuits do not hold the board in reset.
        s.dtr = False
        s.rts = False
        s.open()
        with self.lock:
            self.ser = s
            self.invalid_lines = 0
            self.info = {}
            self.opened_ms = int(time.time() * 1000)
            self.last_line_ms = None
        self._log("sys", f"Opened {self.port} @ {self.baud}")
        self.hub.update_device(state="connected", port=self.port, baud=self.baud, invalid_lines=0,
                               message="Port open — waiting for data", sensor=None, firmware=None, device_id=None)
        self.reader = threading.Thread(target=self._read_loop, args=(s,), name="icv-serial-reader", daemon=True)
        self.reader.start()
        time.sleep(0.05)
        rate = self.hub.settings["acquisition"].get("rate_hz", 10)
        self.send({"command": "GET_INFO"})
        self.send({"command": "SET_RATE", "hz": rate})
        self.send({"command": "START_STREAM"})

    def disconnect(self, user=True, publish=True):
        with self.lock:
            if user:
                self.user_closed = True
            s, self.ser = self.ser, None
        if s:
            try:
                s.close()
            except Exception:
                pass
            self._log("sys", "Port closed")
        if publish and self.hub.mode == "live":
            self.hub.update_device(state="disconnected", message="Disconnected by operator" if user else "Port closed",
                                   rx_rate_hz=0.0)

    # ------------------------------------------------------------ I/O
    def send(self, cmd):
        name = str(cmd.get("command", "")).upper()
        if name not in ALLOWED_COMMANDS:
            raise ValueError(f"Command {name!r} is not permitted")
        line = json.dumps(cmd, separators=(",", ":")) + "\n"
        with self.lock:
            s = self.ser
        if not s or not s.is_open:
            return False
        try:
            with self.write_lock:
                s.write(line.encode("utf-8"))
                s.flush()
            self._log("tx", line.strip())
            return True
        except Exception as exc:
            self._log("err", f"Write failed: {exc}")
            self._handle_drop(f"Write failed: {exc}")
            return False

    def _read_loop(self, s):
        buf = b""
        while True:
            with self.lock:
                if self.ser is not s:  # port closed or replaced: this reader retires
                    return
            try:
                chunk = s.read(512)
            except Exception as exc:
                with self.lock:
                    current = self.ser is s
                if current:
                    self._handle_drop(f"Serial read error: {exc}")
                return
            if not chunk:
                self._check_quiet()
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line.strip(b"\r"))
            if len(buf) > 4096:  # runaway line without newline
                self._invalid(buf[:120].decode("utf-8", "replace"))
                buf = b""

    def _check_quiet(self):
        now = int(time.time() * 1000)
        ref = self.last_line_ms or self.opened_ms or now
        if self.hub.mode == "live" and self.hub.device.get("state") == "connected" and now - ref > 5000:
            self.hub.update_device(message="Port open but no valid data for 5 s — check baud rate and firmware")

    def _handle_line(self, raw):
        if not raw:
            return
        text = raw.decode("utf-8", "replace")
        self.last_line_ms = int(time.time() * 1000)
        if not text.startswith("{"):
            self._invalid(text)
            return
        try:
            msg = json.loads(text)
            if not isinstance(msg, dict) or "type" not in msg:
                raise ValueError("no type field")
        except Exception:
            self._invalid(text)
            return
        mtype = msg.get("type")
        msg["host_ms"] = self.last_line_ms
        if mtype == "measurement":
            v = msg.get("bus_v", msg.get("voltage"))
            self.hub.ingest({"v": v, "i": msg.get("current_ma"), "p": msg.get("power_mw"), "shunt_mv": msg.get("shunt_mv"),
                             "dev_ms": msg.get("t"), "sensor": msg.get("sensor"), "ovf": msg.get("ovf", False)}, "live")
            return
        self._log("rx", text[:300])
        if mtype in ("status", "info", "hello"):
            self._apply_info(msg)
        bus.publish("serial_msg", msg)
        for fn in list(self.listeners):
            try:
                fn(msg)
            except Exception:
                pass

    def _apply_info(self, msg):
        upd = {}
        for k_src, k_dst in (("device", "device_id"), ("device_id", "device_id"), ("firmware", "firmware"),
                             ("sensor", "sensor"), ("shunt_ohm", "shunt_ohm"), ("protocol", "protocol")):
            if msg.get(k_src) is not None:
                upd[k_dst] = msg[k_src]
        sensor = upd.get("sensor") or self.info.get("sensor")
        shunt = upd.get("shunt_ohm") or self.info.get("shunt_ohm")
        if sensor in INA_LSB_UV and shunt:
            upd["resolution_ma"] = round(INA_LSB_UV[sensor] / 1000.0 / float(shunt), 5)
        if msg.get("sensor_ok") is False:
            upd["message"] = "Firmware reports no current sensor on I2C"
        self.info.update(upd)
        if self.hub.mode == "live" and upd:
            self.hub.update_device(**upd)

    def _invalid(self, text):
        self.invalid_lines += 1
        self._log("bad", text[:200])
        if self.hub.mode == "live":
            with self.hub.lock:
                self.hub.device["invalid_lines"] = self.invalid_lines

    def _handle_drop(self, reason):
        with self.lock:
            s, self.ser = self.ser, None
            user_closed = self.user_closed
        if s:
            try:
                s.close()
            except Exception:
                pass
        self._log("err", reason)
        if self.hub.mode == "live":
            self.hub.update_device(state="disconnected", message=reason, rx_rate_hz=0.0)
        bus.publish("serial_msg", {"type": "link_lost", "reason": reason})
        for fn in list(self.listeners):
            try:
                fn({"type": "link_lost", "reason": reason})
            except Exception:
                pass
        if not user_closed and getattr(self, "auto_reconnect", True):
            if not self.reconnector or not self.reconnector.is_alive():
                self.reconnector = threading.Thread(target=self._reconnect_loop, name="icv-serial-reconnect", daemon=True)
                self.reconnector.start()

    def _reconnect_loop(self):
        attempt = 0
        while True:
            with self.lock:
                if self.user_closed or (self.ser and self.ser.is_open):
                    return
            attempt += 1
            if self.hub.mode == "live":
                self.hub.update_device(state="reconnecting", message=f"Reconnecting to {self.port} (attempt {attempt})")
            time.sleep(2.0)
            try:
                self._open()
                self._log("sys", f"Reconnected after {attempt} attempt(s)")
                return
            except Exception as exc:
                self._log("err", f"Reconnect attempt {attempt} failed: {exc}")

    def apply_to_hub(self):
        """Called when switching to live mode: show the real link state."""
        if self.is_open():
            self.hub.update_device(state="connected", port=self.port, baud=self.baud, message="Port open — waiting for data",
                                   **{k: v for k, v in self.info.items() if k in ("device_id", "firmware", "sensor", "shunt_ohm", "resolution_ma")})
            self.send({"command": "START_STREAM"})
        else:
            self.hub.update_device(state="disconnected", port=self.port, message="No serial connection")

    # ------------------------------------------------------------ listeners / log
    def add_listener(self, fn):
        self.listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def _log(self, kind, text):
        entry = {"t": int(time.time() * 1000), "kind": kind, "text": text}
        self.log.append(entry)
        bus.publish("serial_log", entry)

    def status(self):
        return {"available": HAVE_SERIAL, "open": self.is_open(), "port": self.port, "baud": self.baud,
                "info": self.info, "invalid_lines": self.invalid_lines, "last_line_ms": self.last_line_ms,
                "user_closed": self.user_closed}
