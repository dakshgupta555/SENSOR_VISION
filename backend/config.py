"""Paths, versions and default settings for IC VISION."""
import os
import copy

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("ICV_DATA_DIR", os.path.join(BASE_DIR, "data"))
SEED_DIR = os.path.join(BASE_DIR, "seed")
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_images")
FONT_DIR = os.path.join(BASE_DIR, "backend", "fonts")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

DB_PATH = os.path.join(DATA_DIR, "icvision.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
LOG_DIR = os.path.join(DATA_DIR, "logs")

APP_NAME = "IC VISION"
APP_VERSION = "1.0.0"
REFERENCE_DATASET_VERSION = "2026.10-r1"
EXPECTED_FIRMWARE_PROTOCOL = 1

for _d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# ESP32 GPIO numbers wired to each package position on the breadboard fixture.
# Keys are DUT pin numbers (or signal names for modules). "3s" = ADC sense on the pin-3 node.
DEFAULT_FIXTURE = {
    "DIP-14": {"1": 32, "2": 33, "3": 27, "4": 14, "5": 13, "6": 4,
               "8": 18, "9": 19, "10": 23, "11": 17, "12": 16, "13": 26},
    "DIP-8": {"1": 34, "3": 25, "3s": 35},
    "DIP-28": {"19": 36, "3": 16},
    "MODULE": {"TXD0": 16},
}

DEFAULT_SETTINGS = {
    "mode": "simulation",
    "operator": "Operator",
    "device_label": "ICV-ESP32-01",
    "serial": {"port": "", "baud": 115200, "auto_reconnect": True},
    "acquisition": {"rate_hz": 10, "stale_after_s": 2.0, "stats_window_s": 30, "chart_window_s": 60},
    "weights": {"electrical": 0.60, "optical": 0.25, "manufacturer": 0.15},
    "scoring": {"min_coverage": 0.60, "require_electrical": True,
                "threshold_consistent": 80, "threshold_investigate": 50,
                "min_electrical_coverage": 0.50, "min_optical_coverage": 0.40},
    "fixture": {
        "map": copy.deepcopy(DEFAULT_FIXTURE),
        "wiring_verified": False,
        "level_protection_5v": False,
        "adc_sense_enabled": False,
    },
    "probe": {"enabled": False, "r_ohm": 10000, "dac_pin": 25, "node_adc_pin": 34, "src_adc_pin": 35},
    "simulation": {"profile_id": "sim-nor-ok", "stream_override": "profile"},
    "csv_logging": False,
}

# GPIOs on a classic ESP32 that have an ADC channel (ADC2 usable because Wi-Fi is off).
ADC_CAPABLE_GPIO = {32, 33, 34, 35, 36, 39, 25, 26, 27, 14, 13, 4, 2, 15, 12, 0}
INPUT_ONLY_GPIO = {34, 35, 36, 39}

SENSOR_RESOLUTION_MA = {"INA219": 0.1, "INA226": 0.025, "SIM-INA219": 0.1}


def deep_merge(base, override):
    """Return base updated recursively with override (neither is modified)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
