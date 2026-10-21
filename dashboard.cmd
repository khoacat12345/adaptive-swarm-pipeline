@echo off
REM Double-click this file to open the Adaptive Swarm dashboard.
REM It starts the local server and opens the browser automatically.
cd /d "%~dp0"
python .agents\dashboard.py --root . --port 8080 --open
if errorlevel 1 pause
