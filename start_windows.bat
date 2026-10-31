@echo off
cd /d "%~dp0"
python -c "import flask, serial, cv2, pytesseract, reportlab, matplotlib, pandas" 2>nul || (
  echo Installing Python dependencies...
  python -m pip install -r requirements.txt
)
python run.py
pause
