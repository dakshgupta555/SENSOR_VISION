/*
  IC VISION — ESP32 test-controller firmware (protocol 1)
  -----------------------------------------------------------------------------
  Board   : any classic ESP32 dev board (ESP32-WROOM-32), Arduino-ESP32 core 2.x or 3.x
  Sensor  : INA219 or INA226 at I2C 0x40, auto-detected (raw register access, no sensor library)
  Display : SSD1306 128x64 OLED at I2C 0x3C (optional — firmware runs without it)
  Link    : USB serial, newline-delimited JSON, 115200 baud

  Libraries (Arduino Library Manager):
    ArduinoJson (v6 or v7), Adafruit SSD1306, Adafruit GFX Library

  Wiring (high-side current sense):
    ESP32 3V3 or 5V rail ──► INA VIN+ ──[shunt]──► INA VIN− ──► DUT VCC
    INA GND, DUT GND, ESP32 GND common.   INA SDA → GPIO21, SCL → GPIO22.
    OLED SDA → GPIO21, SCL → GPIO22.
    Fixture lines (see Device Settings → fixture map) through 1 kΩ series resistors.

  Messages sent (one JSON object per line):
    {"type":"status",...}  {"type":"info",...}  {"type":"measurement",...}
    {"type":"test_status","stage":"functional","progress":50}
    {"type":"functional_result",...}  {"type":"functional_done",...}
    {"type":"iv_point",...}  {"type":"iv_done",...}  {"type":"ack",...}  {"type":"error",...}

  Commands received:
    PING, GET_INFO, READ_SENSOR, SET_RATE{hz}, START_STREAM, STOP_STREAM, SET_MODE{mode},
    PREPARE{drive_low[]}, RELEASE, STOP_TEST, START_TEST{test,...}, IV_SWEEP{...}, OLED{text}

  This firmware only reports what the sensor and pins actually read. It never invents values.
*/
#include <Arduino.h>
#include <Wire.h>
#include <ArduinoJson.h>

#define USE_OLED 1
#if USE_OLED
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#endif

// ----------------------------------------------------------------- configuration
#define FW_VERSION   "1.0.0"
#define PROTOCOL     1
#define DEVICE_ID    "ICV-ESP32-01"
#define SERIAL_BAUD  115200
#define I2C_SDA      21
#define I2C_SCL      22
#define INA_ADDR     0x40
#define SHUNT_OHMS   0.1f      // stock R100 on INA219/INA226 breakout boards; change if you fit another shunt
#define OLED_ADDR    0x3C
#define STATUS_MS    2000

#if ARDUINOJSON_VERSION_MAJOR >= 7
#define JSON_DOC(name) JsonDocument name
#else
#define JSON_DOC(name) StaticJsonDocument<2048> name
#endif

enum SensorType { SENSOR_NONE, SENSOR_INA219, SENSOR_INA226 };
SensorType sensor = SENSOR_NONE;
const char *sensorName() { return sensor == SENSOR_INA219 ? "INA219" : sensor == SENSOR_INA226 ? "INA226" : "NONE"; }

float rateHz = 10.0f;
bool streaming = true;
bool abortTest = false;
const char *testState = "idle";
char modeName[16] = "idle";
char oledMsg[24] = "";
unsigned long lastSampleMs = 0, lastStatusMs = 0, lastOledMs = 0;
uint32_t seq = 0;
String inLine;

struct Reading { bool ok; float busV; float shuntmV; float currentmA; float powermW; bool ovf; };
Reading lastReading = {false, 0, 0, 0, 0, false};

#if USE_OLED
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;
#endif

// GPIOs used by the current fixture test (released on RELEASE / STOP_TEST)
int8_t drivenPins[24];
uint8_t drivenCount = 0;

// ----------------------------------------------------------------- pin safety
bool gpioUsable(int g) {
  if (g < 0 || g > 39) return false;
  if (g == 0 || g == 1 || g == 3 || (g >= 6 && g <= 11) || g == I2C_SDA || g == I2C_SCL) return false;
  if (g == 20 || g == 24 || (g >= 28 && g <= 31)) return false;   // not bonded out on ESP32
  return true;
}
bool gpioOutputCapable(int g) { return gpioUsable(g) && g < 34; }

void rememberDriven(int g) {
  for (uint8_t i = 0; i < drivenCount; i++) if (drivenPins[i] == g) return;
  if (drivenCount < sizeof(drivenPins)) drivenPins[drivenCount++] = g;
}
void releaseAll() {
  for (uint8_t i = 0; i < drivenCount; i++) pinMode(drivenPins[i], INPUT);
  drivenCount = 0;
}

// ----------------------------------------------------------------- I2C register access
bool i2cWrite16(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(INA_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}
bool i2cRead16(uint8_t reg, uint16_t &val) {
  Wire.beginTransmission(INA_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)INA_ADDR, (uint8_t)2) != 2) return false;
  val = ((uint16_t)Wire.read() << 8) | Wire.read();
  return true;
}

bool detectSensor() {
  sensor = SENSOR_NONE;
  Wire.beginTransmission(INA_ADDR);
  if (Wire.endTransmission() != 0) return false;
  uint16_t mid = 0;
  if (i2cRead16(0xFE, mid) && mid == 0x5449) {          // "TI" manufacturer ID → INA226
    sensor = SENSOR_INA226;
    // AVG=16, VBUSCT=1.1 ms, VSHCT=1.1 ms, continuous shunt+bus
    i2cWrite16(0x00, 0x4527);
    return true;
  }
  i2cWrite16(0x00, 0x8000);                              // INA219 reset
  delay(2);
  uint16_t cfg = 0;
  if (i2cRead16(0x00, cfg) && cfg == 0x399F) {            // INA219 power-on default
    sensor = SENSOR_INA219;
    // BRNG=32 V, PG=/1 (±40 mV → ±400 mA with 0.1 Ω), 16-sample averaging, continuous
    i2cWrite16(0x00, 0x2667);
    return true;
  }
  return false;
}

Reading readSensor() {
  Reading r = {false, 0, 0, 0, 0, false};
  uint16_t raw;
  if (sensor == SENSOR_INA219) {
    if (!i2cRead16(0x01, raw)) return r;
    r.shuntmV = (int16_t)raw * 0.01f;                    // 10 µV LSB
    if (!i2cRead16(0x02, raw)) return r;
    r.ovf = raw & 0x0001;
    r.busV = (raw >> 3) * 0.004f;                        // 4 mV LSB
  } else if (sensor == SENSOR_INA226) {
    if (!i2cRead16(0x01, raw)) return r;
    r.shuntmV = (int16_t)raw * 0.0025f;                  // 2.5 µV LSB
    if (!i2cRead16(0x02, raw)) return r;
    r.busV = raw * 0.00125f;                             // 1.25 mV LSB
    uint16_t me;
    if (i2cRead16(0x06, me)) r.ovf = me & 0x0004;
  } else {
    return r;
  }
  r.currentmA = r.shuntmV / SHUNT_OHMS;
  r.powermW = r.busV * r.currentmA;
  r.ok = true;
  return r;
}

// ----------------------------------------------------------------- output helpers
void sendLine(const char *s) { Serial.println(s); }

void sendMeasurement() {
  Reading r = readSensor();
  char buf[220];
  if (!r.ok) {
    snprintf(buf, sizeof(buf), "{\"type\":\"error\",\"message\":\"sensor read failed\",\"sensor\":\"%s\",\"t\":%lu}", sensorName(), millis());
    sendLine(buf);
    return;
  }
  lastReading = r;
  snprintf(buf, sizeof(buf),
           "{\"type\":\"measurement\",\"t\":%lu,\"seq\":%lu,\"bus_v\":%.4f,\"shunt_mv\":%.4f,\"current_ma\":%.4f,\"power_mw\":%.4f,\"sensor\":\"%s\",\"ovf\":%s}",
           millis(), (unsigned long)(++seq), r.busV, r.shuntmV, r.currentmA, r.powermW, sensorName(), r.ovf ? "true" : "false");
  sendLine(buf);
}

void sendStatus(const char *type) {
  char buf[300];
  snprintf(buf, sizeof(buf),
           "{\"type\":\"%s\",\"device\":\"%s\",\"connected\":true,\"firmware\":\"%s\",\"protocol\":%d,\"sensor\":\"%s\","
           "\"sensor_ok\":%s,\"shunt_ohm\":%.4f,\"rate_hz\":%.1f,\"streaming\":%s,\"test\":\"%s\",\"oled\":%s}",
           type, DEVICE_ID, FW_VERSION, PROTOCOL, sensorName(), sensor != SENSOR_NONE ? "true" : "false", SHUNT_OHMS, rateHz,
           streaming ? "true" : "false", testState,
#if USE_OLED
           oledOk ? "true" : "false"
#else
           "false"
#endif
  );
  sendLine(buf);
}

void sendAck(const char *cmd) {
  char buf[96];
  snprintf(buf, sizeof(buf), "{\"type\":\"ack\",\"command\":\"%s\"}", cmd);
  sendLine(buf);
}

void sendError(const char *msg) {
  char buf[200];
  snprintf(buf, sizeof(buf), "{\"type\":\"error\",\"message\":\"%s\"}", msg);
  sendLine(buf);
}

void sendProgress(int pct) {
  char buf[80];
  snprintf(buf, sizeof(buf), "{\"type\":\"test_status\",\"stage\":\"functional\",\"progress\":%d}", pct);
  sendLine(buf);
}

// ----------------------------------------------------------------- OLED
void drawOled() {
#if USE_OLED
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("IC VISION ");
  oled.print(sensorName());
  oled.drawFastHLine(0, 10, 128, SSD1306_WHITE);
  oled.setCursor(0, 14);
  if (sensor == SENSOR_NONE) {
    oled.print("NO SENSOR ON I2C");
  } else {
    oled.printf("V %7.3f V\n", lastReading.busV);
    oled.printf("I %7.3f mA\n", lastReading.currentmA);
    oled.printf("P %7.2f mW\n", lastReading.powermW);
  }
  oled.setCursor(0, 46);
  oled.printf("Test: %s", testState);
  oled.setCursor(0, 56);
  oled.print(oledMsg[0] ? oledMsg : (streaming ? "Streaming" : "Stream paused"));
  oled.display();
#endif
}

// ----------------------------------------------------------------- background service (also used inside tests)
void serviceStream() {
  unsigned long now = millis();
  unsigned long period = (unsigned long)(1000.0f / rateHz);
  if (streaming && sensor != SENSOR_NONE && now - lastSampleMs >= period) {
    lastSampleMs = now;
    sendMeasurement();
  }
  if (now - lastStatusMs >= STATUS_MS) {
    lastStatusMs = now;
    sendStatus("status");
  }
  if (now - lastOledMs >= 250) {
    lastOledMs = now;
    drawOled();
  }
}

// While a test runs: keep streaming, and honour STOP_TEST / PING.
void pollDuringTest() {
  serviceStream();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (inLine.indexOf("STOP_TEST") >= 0) abortTest = true;
      else if (inLine.indexOf("PING") >= 0) sendLine("{\"type\":\"pong\"}");
      else if (inLine.length()) sendError("busy: test in progress");
      inLine = "";
    } else if (c != '\r' && inLine.length() < 1024) {
      inLine += c;
    }
  }
}

void waitMs(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms && !abortTest) { pollDuringTest(); delay(1); }
}

int readMilliVoltsAvg(int pin, int n = 8) {
  long sum = 0;
  for (int i = 0; i < n; i++) sum += analogReadMilliVolts(pin);
  return (int)(sum / n);
}

// ----------------------------------------------------------------- tests
void testTruthTable(JsonVariant cmd) {
  JsonArray gates = cmd["gates"].as<JsonArray>();
  JsonArray vectors = cmd["vectors"].as<JsonArray>();
  int settle = cmd["settle_us"] | 200;
  bool adc = cmd["adc"] | false;
  // validate every pin before touching any of them
  for (JsonObject g : gates) {
    for (JsonVariant p : g["in"].as<JsonArray>()) if (!gpioOutputCapable(p.as<int>())) { sendError("truth_table: input pin not output-capable"); return; }
    if (!gpioUsable(g["out"].as<int>())) { sendError("truth_table: output pin not usable"); return; }
  }
  for (JsonObject g : gates) {
    for (JsonVariant p : g["in"].as<JsonArray>()) { pinMode(p.as<int>(), OUTPUT); digitalWrite(p.as<int>(), LOW); rememberDriven(p.as<int>()); }
    pinMode(g["out"].as<int>(), INPUT);
  }
  int total = gates.size() * vectors.size(), done = 0;
  for (JsonObject g : gates) {
    JsonArray ins = g["in"].as<JsonArray>();
    int out = g["out"].as<int>();
    for (JsonArray v : vectors) {
      if (abortTest) break;
      int a = v[0].as<int>(), b = v[1].as<int>(), expected = v[2].as<int>();
      digitalWrite(ins[0].as<int>(), a ? HIGH : LOW);
      digitalWrite(ins[1].as<int>(), b ? HIGH : LOW);
      delayMicroseconds(settle);
      int observed = digitalRead(out);
      int vout = -1;
      if (adc && digitalPinToAnalogChannel(out) >= 0) {
        vout = readMilliVoltsAvg(out, 4);
        pinMode(out, INPUT);
      }
      char buf[260];
      if (vout >= 0)
        snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"truth_table\",\"gate\":\"%s\",\"vector\":[%d,%d],\"expected\":%d,\"observed\":%d,\"v_out_mv\":%d,\"pass\":%s}",
                 g["name"].as<const char *>(), a, b, expected, observed, vout, observed == expected ? "true" : "false");
      else
        snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"truth_table\",\"gate\":\"%s\",\"vector\":[%d,%d],\"expected\":%d,\"observed\":%d,\"v_out_mv\":null,\"pass\":%s}",
                 g["name"].as<const char *>(), a, b, expected, observed, observed == expected ? "true" : "false");
      sendLine(buf);
      sendProgress(100 * (++done) / (total ? total : 1));
      pollDuringTest();
    }
    digitalWrite(ins[0].as<int>(), LOW);
    digitalWrite(ins[1].as<int>(), LOW);
  }
  char buf[120];
  snprintf(buf, sizeof(buf), "{\"type\":\"functional_done\",\"test\":\"truth_table\",\"total\":%d,\"aborted\":%s}", done, abortTest ? "true" : "false");
  sendLine(buf);
}

volatile uint32_t hbEdges = 0;
void IRAM_ATTR hbIsr() { hbEdges++; }

void testHeartbeat(JsonVariant cmd) {
  int pin = cmd["pin"] | -1;
  unsigned long window = cmd["window_ms"] | 3000;
  if (!gpioUsable(pin)) { sendError("heartbeat: pin not usable"); return; }
  pinMode(pin, INPUT);
  hbEdges = 0;
  attachInterrupt(digitalPinToInterrupt(pin), hbIsr, RISING);
  unsigned long t0 = millis();
  while (millis() - t0 < window && !abortTest) {
    pollDuringTest();
    sendProgress((int)(100 * (millis() - t0) / window));
    waitMs(200);
  }
  detachInterrupt(digitalPinToInterrupt(pin));
  unsigned long el = millis() - t0;
  char buf[200];
  snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"heartbeat\",\"edges\":%lu,\"window_ms\":%lu,\"freq_hz\":%.3f}",
           (unsigned long)hbEdges, el, el ? hbEdges * 1000.0f / el : 0.0f);
  sendLine(buf);
  sendLine("{\"type\":\"functional_done\",\"test\":\"heartbeat\"}");
}

void testUartCapture(JsonVariant cmd) {
  int rx = cmd["rx_pin"] | -1;
  long baud = cmd["baud"] | 9600;
  unsigned long window = cmd["window_ms"] | 4000;
  const char *match = cmd["match"] | "";
  if (!gpioUsable(rx)) { sendError("uart_capture: rx pin not usable"); return; }
  Serial2.begin(baud, SERIAL_8N1, rx, -1);
  String line, found;
  unsigned long bytes = 0, t0 = millis();
  while (millis() - t0 < window && !abortTest && !found.length()) {
    while (Serial2.available()) {
      char c = Serial2.read();
      bytes++;
      if (c == '\n' || c == '\r') {
        if (line.length() && strlen(match) && line.indexOf(match) >= 0) { found = line; break; }
        line = "";
      } else if (c >= 32 && c < 127 && line.length() < 160) {
        line += c;
      }
    }
    pollDuringTest();
    delay(2);
  }
  Serial2.end();
  found.replace("\"", "'");
  char buf[320];
  if (found.length())
    snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"uart_capture\",\"found\":true,\"line\":\"%s\",\"bytes\":%lu,\"baud\":%ld}", found.c_str(), bytes, baud);
  else
    snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"uart_capture\",\"found\":false,\"line\":null,\"bytes\":%lu,\"baud\":%ld}", bytes, baud);
  sendLine(buf);
  sendLine("{\"type\":\"functional_done\",\"test\":\"uart_capture\"}");
}

void testAnalogBuffer(JsonVariant cmd) {
  int dac = cmd["dac_pin"] | -1, inAdc = cmd["in_adc"] | -1, outAdc = cmd["out_adc"] | -1;
  int settle = cmd["settle_ms"] | 30;
  if ((dac != 25 && dac != 26) || digitalPinToAnalogChannel(inAdc) < 0 || digitalPinToAnalogChannel(outAdc) < 0) {
    sendError("analog_buffer: DAC must be GPIO25/26 and sense pins ADC-capable"); return;
  }
  JsonArray pts = cmd["points_mv"].as<JsonArray>();
  int n = pts.size(), k = 0;
  for (JsonVariant p : pts) {
    if (abortTest) break;
    int setmv = p.as<int>();
    int code = constrain((int)lroundf(setmv * 255.0f / 3300.0f), 0, 255);
    dacWrite(dac, code);
    waitMs(settle);
    int vin = readMilliVoltsAvg(inAdc, 16), vout = readMilliVoltsAvg(outAdc, 16);
    char buf[180];
    snprintf(buf, sizeof(buf), "{\"type\":\"functional_result\",\"test\":\"analog_buffer\",\"set_mv\":%d,\"vin_mv\":%d,\"vout_mv\":%d}", setmv, vin, vout);
    sendLine(buf);
    sendProgress(100 * (++k) / (n ? n : 1));
  }
  dacWrite(dac, 0);
  sendLine("{\"type\":\"functional_done\",\"test\":\"analog_buffer\"}");
}

void ivSweep(JsonVariant cmd) {
  int dac = cmd["dac_pin"] | 25, node = cmd["node_adc"] | 34, src = cmd["src_adc"] | 35;
  float r = cmd["r_ohm"] | 10000.0f;
  int steps = constrain((int)(cmd["steps"] | 32), 4, 128);
  if ((dac != 25 && dac != 26) || digitalPinToAnalogChannel(node) < 0 || digitalPinToAnalogChannel(src) < 0 || r < 1000) {
    sendError("iv_sweep: invalid probe configuration"); sendLine("{\"type\":\"iv_done\",\"points\":0}"); return;
  }
  int maxCode = (int)(3200.0f * 255.0f / 3300.0f);   // stop at ~3.2 V
  int k;
  for (k = 0; k <= steps && !abortTest; k++) {
    dacWrite(dac, maxCode * k / steps);
    waitMs(15);
    int vs = readMilliVoltsAvg(src, 16), vn = readMilliVoltsAvg(node, 16);
    float iua = (vs - vn) / r * 1000.0f;   // mV / Ω = mA → µA
    char buf[160];
    snprintf(buf, sizeof(buf), "{\"type\":\"iv_point\",\"vsrc_mv\":%d,\"vnode_mv\":%d,\"i_ua\":%.2f}", vs, vn, iua);
    sendLine(buf);
  }
  dacWrite(dac, 0);
  char buf[60];
  snprintf(buf, sizeof(buf), "{\"type\":\"iv_done\",\"points\":%d}", k);
  sendLine(buf);
}

// ----------------------------------------------------------------- command dispatch
void handleCommand(const String &line) {
  JSON_DOC(doc);
  DeserializationError err = deserializeJson(doc, line);
  if (err) { sendError("invalid JSON command"); return; }
  const char *c = doc["command"] | "";
  if (!strcmp(c, "PING")) { sendLine("{\"type\":\"pong\"}"); }
  else if (!strcmp(c, "GET_INFO")) { sendStatus("info"); }
  else if (!strcmp(c, "READ_SENSOR")) { if (sensor == SENSOR_NONE) { detectSensor(); } sendMeasurement(); }
  else if (!strcmp(c, "SET_RATE")) { rateHz = constrain((float)(doc["hz"] | 10.0f), 1.0f, 50.0f); sendAck(c); }
  else if (!strcmp(c, "START_STREAM")) { streaming = true; sendAck(c); }
  else if (!strcmp(c, "STOP_STREAM")) { streaming = false; sendAck(c); }
  else if (!strcmp(c, "SET_MODE")) { strncpy(modeName, doc["mode"] | "idle", sizeof(modeName) - 1); sendAck(c); }
  else if (!strcmp(c, "OLED")) { strncpy(oledMsg, doc["text"] | "", sizeof(oledMsg) - 1); sendAck(c); }
  else if (!strcmp(c, "PREPARE")) {
    for (JsonVariant p : doc["drive_low"].as<JsonArray>()) {
      int g = p.as<int>();
      if (gpioOutputCapable(g)) { pinMode(g, OUTPUT); digitalWrite(g, LOW); rememberDriven(g); }
    }
    sendAck(c);
  }
  else if (!strcmp(c, "RELEASE")) { releaseAll(); sendAck(c); }
  else if (!strcmp(c, "STOP_TEST")) { abortTest = true; releaseAll(); testState = "idle"; sendAck(c); }
  else if (!strcmp(c, "START_TEST")) {
    const char *t = doc["test"] | "";
    abortTest = false;
    testState = t;
    if (!strcmp(t, "truth_table")) testTruthTable(doc.as<JsonVariant>());
    else if (!strcmp(t, "heartbeat")) testHeartbeat(doc.as<JsonVariant>());
    else if (!strcmp(t, "uart_capture")) testUartCapture(doc.as<JsonVariant>());
    else if (!strcmp(t, "analog_buffer")) testAnalogBuffer(doc.as<JsonVariant>());
    else sendError("unsupported test");
    testState = "idle";
  }
  else if (!strcmp(c, "IV_SWEEP")) { abortTest = false; testState = "iv_sweep"; ivSweep(doc.as<JsonVariant>()); testState = "idle"; }
  else { sendError("unknown command"); }
}

// ----------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
#if USE_OLED
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (oledOk) { oled.clearDisplay(); oled.display(); }
#endif
  detectSensor();
  sendLine("");   // terminate any boot noise on the line
  sendStatus("info");
  if (sensor == SENSOR_NONE) sendError("no INA219/INA226 found at 0x40 — check I2C wiring");
}

void loop() {
  serviceStream();
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') {
      if (inLine.length()) handleCommand(inLine);
      inLine = "";
    } else if (ch != '\r' && inLine.length() < 2048) {
      inLine += ch;
    }
  }
  static unsigned long lastProbe = 0;
  if (sensor == SENSOR_NONE && millis() - lastProbe > 3000) {   // hot-plug the sensor
    lastProbe = millis();
    if (detectSensor()) sendStatus("info");
  }
}
