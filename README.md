# IC VISION — Multimodal Semiconductor Fingerprinting & Counterfeit Screening

*Every chip leaves an electrical signature.*

IC VISION is a laptop dashboard plus ESP32 test controller that screens a semiconductor on three dimensions:

| Dimension | What it does | Weight |
|---|---|---|
| Electrical fingerprinting | INA219/INA226 supply monitoring, current stability, functional tests (truth table, MCU heartbeat + signature, op-amp follower), optional output levels and pin I-V signature | 0.60 |
| Optical inspection | OpenCV preprocessing + Tesseract OCR of the marking, part-number consistency, reference-image similarity, operator checklist | 0.25 |
| Manufacturer verification | Local OEM datasheet database, part-number and manufacturer match, provenance of the reference values used | 0.15 |

The output is a **screening result** (Reference-consistent / Investigation recommended / Significant deviation detected / Inconclusive), never a genuine/counterfeit verdict.

---

## 1. Quick start

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
python run.py            # opens http://127.0.0.1:5000
```

Windows: double-click `start_windows.bat`. macOS/Linux: `./start_mac_linux.sh`.

**Install the Tesseract OCR engine** (the Python package alone is not enough):

| OS | Command |
|---|---|
| Windows | UB Mannheim installer from <https://github.com/UB-Mannheim/tesseract/wiki>. The app finds `C:\Program Files\Tesseract-OCR\tesseract.exe` automatically, or set `TESSERACT_CMD`. |
| macOS | `brew install tesseract` |
| Ubuntu/Debian | `sudo apt install tesseract-ocr` |

If Tesseract is missing the app says so and shows the install instruction; manual marking entry still works. It never substitutes canned OCR text.

Everything runs offline: Chart.js, Lucide icons and IBM Plex fonts are bundled in `frontend/static/vendor/`. Data lives in `data/` (SQLite database, uploads, reports, CSV logs). Delete `data/` to start fresh.

---

## 2. Two clearly separated modes

**Live hardware** — only readings received from the ESP32 over USB serial are shown. If the link drops or goes quiet for longer than the stale timeout (default 2 s), values are withdrawn, the last real timestamp is shown, and any running inspection stops with an error. Simulated values are never substituted.

**Simulation** — a generated stream and an emulated test controller. Every simulated value carries a hatched `SIMULATED` tag in the UI, `source=sim` in the database and CSV, and a `SIMULATED DATA` watermark on every PDF page. A simulated known-good baseline is only ever used for simulated inspections.

Switch with the toggle in the header. An inspection created in one mode cannot run in the other.

### Simulation library

| Profile | Scenario | Expected screening result |
|---|---|---|
| NOR gate — reference-consistent | TI SN74HC02N, 3.3 V | Reference-consistent |
| NOR gate — suspicious electrical deviation | marked SN74HC02N, draws ~2.7 mA (bipolar-like), low VOH, resurfaced top | Significant deviation detected |
| Microcontroller — reference-consistent | ATmega328P-PU, 5 V, 8 MHz RC, heartbeat + signature | Reference-consistent |
| Microcontroller — abnormal supply current | ~11.6 mA rising, above the 9 mA datasheet row | Investigation recommended |
| NAND gate — reference-consistent | NXP-marked 74HC00N (legacy DIP, date code 2010) | Reference-consistent |
| Operational amplifier — reference-consistent | TI LM358P, follower test | Reference-consistent |
| Unknown component — missing reference | unreadable marking, no database match | Inconclusive |

Each has a synthetic package image (rendered, labelled SYNTHETIC) on which **real Tesseract OCR runs**. On Live Monitoring the stream behaviour can be overridden: normal, abnormal current, unstable current. Pause, resume and reset work without restarting.

---

## 3. Seven-step demo for the jury (≈ 4 minutes)

Tick **Guided demo narration** on the New Inspection page: each step shows one sentence explaining what the jury is looking at.

1. **Insert / select component** — Simulation mode, load *NOR gate — suspicious electrical deviation*.
2. **OCR identification** — show the preprocessing tabs (package body → contrast → threshold), the recognised words with engine confidences, and the `SN74HCO2N → SN74HC02N (ocr-normalised)` match. Edit the text to show manual correction.
3. **OEM reference lookup** — TI SCLS076G record, VIEW OFFICIAL DATASHEET, provenance badges.
4. **Electrical test** — start; supply current appears beside “≤ 20 µA (ICC, datasheet)”, the truth table fills in live, the DIP diagram lights up.
5. **Analysis** — 2.6 mA = 130× the datasheet maximum (critical), VOH 2.6 V fails the derived limit, truth table still passes (a remarked bipolar part *works* logically — that is why electrical fingerprinting matters).
6. **Trust score** — `S = (0.60×Se + 0.25×Sv + 0.15×Sm) / Σw`, the critical override, findings.
7. **Report** — generate the PDF; point at the SIMULATED watermark and the provenance legend.

Then run *Unknown component* to show **Inconclusive** (missing reference is not called counterfeit), and switch to Live hardware with the board unplugged to show that nothing is invented.

---

## 4. Live hardware

### 4.1 Firmware

`firmware/icvision_esp32/icvision_esp32.ino` — Arduino IDE, board **ESP32 Dev Module**, Arduino-ESP32 core 2.x or 3.x.
Libraries (Library Manager): **ArduinoJson**, **Adafruit SSD1306**, **Adafruit GFX Library**.
The INA219/INA226 is accessed with raw I2C registers (no sensor library) and auto-detected. Set `SHUNT_OHMS` if your breakout does not use the stock 0.1 Ω (R100) shunt. Set `USE_OLED 0` if you have no display.

> The firmware has not been compiled in the build environment used to produce this project (no ESP32 toolchain access there). Compile it once in the Arduino IDE before the event and fix any core-version warnings.

Serial: 115200 baud, one JSON object per line. Messages: `status`, `info`, `measurement`, `test_status`, `functional_result`, `functional_done`, `iv_point`, `iv_done`, `ack`, `error`. Commands: `PING`, `GET_INFO`, `READ_SENSOR`, `SET_RATE`, `START_STREAM`, `STOP_STREAM`, `SET_MODE`, `PREPARE`, `RELEASE`, `STOP_TEST`, `START_TEST`, `IV_SWEEP`, `OLED`. The spec's short form `{"type":"measurement","voltage":5.0,"current_ma":2.1,"power_mw":10.5}` is also accepted.

### 4.2 Supply and sensor wiring (high-side)

```
 ESP32 3V3 (or 5V) ──► INA VIN+ ──[shunt]──► INA VIN− ──► DUT VCC
 ESP32 GND ─────────── INA GND ───────────────────────── DUT GND
 ESP32 GPIO21 (SDA) ── INA SDA ── OLED SDA
 ESP32 GPIO22 (SCL) ── INA SCL ── OLED SCL
 INA VCC, OLED VCC ── ESP32 3V3
```
The INA bus-voltage reading is the DUT supply. The pre-flight check refuses to test if it differs from the selected rail by more than 10 %.

### 4.3 Physical test case 1 — 74HC02 NOR gate (DIP-14)

Recommended at **3.3 V** so every DUT output is safe for ESP32 inputs. Put a **1 kΩ resistor in series on every line**.

| DUT pin | 74HC02 | ESP32 GPIO | | DUT pin | 74HC02 | ESP32 GPIO |
|---|---|---|---|---|---|---|
| 1 | 1Y | 32 | | 8 | 3A | 18 |
| 2 | 1A | 33 | | 9 | 3B | 19 |
| 3 | 1B | 27 | | 10 | 3Y | 23 |
| 4 | 2Y | 14 | | 11 | 4A | 17 |
| 5 | 2A | 13 | | 12 | 4B | 16 |
| 6 | 2B | 4 | | 13 | 4Y | 26 |
| 7 | GND | GND | | 14 | VCC | INA VIN− |

The map is editable in **Device Settings → Breadboard fixture map**. The NAND (74HC00) profile uses the *same physical positions* with a different pinout — the software applies each profile's own pin functions, so a different IC never inherits a NOR assumption.
Then tick *Wiring matches this map* in Device Settings and confirm the three pre-run checks on the inspection.

What the INA219 can and cannot tell you here: 74HC ICC is ≤ 20 µA, below the INA219's ~0.1 mA resolution with a 0.1 Ω shunt, so the current check is an **upper bound** (it still catches mA-level parts such as remarked bipolar logic). An INA226 resolves ~25 µA.

### 4.4 Physical test case 2 — ATmega328P (DIP-28)

1. Program the DUT with `firmware/dut_atmega328p_heartbeat` (e.g. MiniCore, *internal 8 MHz*). It toggles PB5 at 10 Hz and prints `ICV:SIG:<signature bytes>` at 9600 baud.
2. Wire VCC (7) and AVCC (20) to INA VIN−, GND (8, 22) to ground, 10 kΩ pull-up on RESET (1).
3. PB5 (pin 19) → GPIO36; TXD (pin 3) → GPIO16. **At 5 V use dividers** (10 kΩ / 20 kΩ) on both lines and tick *level protection* in Device Settings.
4. In the inspection choose rail 5 V and clock *8 MHz internal RC* to compare against the datasheet's 8 MHz / 5 V active-current row; other conditions need a known-good baseline. 16 MHz at 3.3 V is rejected (outside the speed grade).

Supply current alone is not treated as functional evidence; the heartbeat frequency and the silicon signature (expected `1E 95 0F`) are.

### 4.5 Optional extras

* **ADC output levels** — tick *Read DUT output levels* to have the ESP32 ADC read VOH/VOL during the truth table (ADC-capable GPIOs only; ±0.1 V tolerance).
* **Pin I-V signature** — DAC GPIO25 → 10 kΩ → probe on a DUT pin, ADC GPIO34 on the pin node, GPIO35 on the DAC node. The DUT is unpowered with VCC tied to GND; the app prompts the operator before sweeping (max 3.2 V, ≤ 0.3 mA).
* **Known-good baselines** — after inspecting a verified genuine part, *Record as known-good baseline* stores its supply current, I-V curve and image as empirical references.

---

## 5. Scoring rules (transparent, configurable on the Trust Score page)

* Parameters that are not measured, have no reference or do not apply are **excluded** — never counted as passes.
* Invalid samples (sensor overflow, out of range) are dropped before statistics.
* A mandatory parameter without evidence makes the electrical dimension inconclusive; an invalid supply condition makes the result inconclusive.
* Critical findings override the thresholds: truth-table mismatch, wrong device signature, supply current far above the datasheet maximum (×10 for logic, ×2.5 for the MCU, ×5 for the op-amp).
* Electrical evidence is required for a screening result (configurable); coverage below 60 % gives Inconclusive.
* Drift is judged with a robust (Theil–Sen) trend and only counted when it exceeds the noise.

## 6. Reference data provenance

`seed/references.json` holds values transcribed from the official datasheets (TI SCLS076G, SCLS181H, SLOS068AB; Nexperia 74HC_HCT02 Rev. 9 and 74HC_HCT00 Rev. 11; Espressif ESP32 Series Datasheet v5.3; Microchip/Atmel ATmega328P, Atmel-42735B). Every specification row carries `source_type` (oem / oem_derived / empirical / simulated) and `verified`. The ATmega328P 8 MHz / 5 V active-current row and signature bytes are marked **unverified** — confirm them against the datasheet before the event and click *Mark verified* (user records) or edit the seed. Datasheet links point only to manufacturer sites. No partnerships or proprietary datasets are implied.

## 7. Project layout

```
backend/   app.py (REST + SSE), acquisition.py (hub, stats, stale watchdog), serial_manager.py (PySerial),
           simulation.py, inspection.py (workflow engine), evaluation.py, scoring.py,
           ocr_engine.py (OpenCV + Tesseract), identify.py, report.py (ReportLab), database.py (SQLite)
frontend/  templates/index.html, static/js (vanilla ES modules, Chart.js), static/css
seed/      component profiles, OEM references, manufacturers, simulation profiles
firmware/  ESP32 controller + ATmega328P heartbeat DUT sketch
tests/     test_scoring.py, test_pipeline.py (7 profiles end to end), test_serial_link.py (live path over a pty)
```

Real-time path: ESP32 → USB serial → `SerialManager` → `AcquisitionHub` (timestamp, validate, stats, SQLite batch) → Server-Sent Events `/api/stream` → Chart.js. Target 5–10 samples/s, configurable 1–20 Hz.

## 8. Tests

```bash
python -m tests.test_scoring       # missing-data and override rules
python -m tests.test_pipeline      # OCR → reference → electrical → score → PDF for all 7 simulation profiles
python -m tests.test_serial_link   # live serial path: streaming, preconditions, stale link, disconnect (Linux/macOS)
```
