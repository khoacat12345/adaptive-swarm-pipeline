@echo off
setlocal
REM Single-run viewer only. Example:
REM   dashboard.cmd --run-id v2_20261112T161142_045703 --port 8501 --open
python "%~dp0.agents\v2\swarm_cli.py" ui %*
exit /b %ERRORLEVEL%
