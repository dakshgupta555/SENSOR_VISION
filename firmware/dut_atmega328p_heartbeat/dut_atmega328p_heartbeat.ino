/*
  IC VISION — heartbeat firmware for an ATmega328P under test (the DUT, not the ESP32)

  Load this onto the ATmega328P before functional screening (ISP programmer or a bootloader).
  It gives the ESP32 two things it can check without trusting the marking:
    1. Heartbeat: PB5 (Arduino D13, DIP pin 19) toggles every 50 ms → 10 Hz square wave.
       The measured frequency also reflects the clock source's accuracy.
    2. Signature: every 500 ms TXD (DIP pin 3) prints "ICV:SIG:" + the three device-signature
       bytes read from the silicon's signature row, e.g. ICV:SIG:1E950F for an ATmega328P.
       A remarked part (ATmega328, 168, 328PB…) reports a different signature.

  Clock: the IC VISION profile assumes 8 MHz internal RC (e.g. MiniCore, "Internal 8 MHz") at 5 V.
  At 3.3 V do not run the chip from a 16 MHz crystal — it is outside the datasheet speed grade.
  At 5 V, put a divider (e.g. 10 kΩ / 20 kΩ) on PB5 and TXD before they reach the ESP32.
*/
#include <avr/boot.h>

const uint8_t HEARTBEAT_PIN = 13;   // PB5
unsigned long lastToggle = 0, lastSig = 0;
bool level = false;

void setup() {
  pinMode(HEARTBEAT_PIN, OUTPUT);
  Serial.begin(9600);
}

void loop() {
  unsigned long now = millis();
  if (now - lastToggle >= 50) {
    lastToggle += 50;
    level = !level;
    digitalWrite(HEARTBEAT_PIN, level);
  }
  if (now - lastSig >= 500) {
    lastSig = now;
    uint8_t s0 = boot_signature_byte_get(0x0000);
    uint8_t s1 = boot_signature_byte_get(0x0002);
    uint8_t s2 = boot_signature_byte_get(0x0004);
    char buf[24];
    snprintf(buf, sizeof(buf), "ICV:SIG:%02X%02X%02X", s0, s1, s2);
    Serial.println(buf);
  }
}
