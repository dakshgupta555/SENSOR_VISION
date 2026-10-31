#!/usr/bin/env sh
cd "$(dirname "$0")"
python3 -c "import flask, serial, cv2, pytesseract, reportlab, matplotlib, pandas" 2>/dev/null || python3 -m pip install -r requirements.txt
python3 run.py "$@"
