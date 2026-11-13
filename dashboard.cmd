@echo off
REM Double-click this file to open the Unified War Room.
REM It starts the local server (loopback only) and opens the browser.
cd /d "%~dp0"
python .agents\dashboard.py --root . --port 8501 --open
if errorlevel 1 pause
