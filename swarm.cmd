@echo off
setlocal
python "%~dp0.agents\v2\swarm_cli.py" %*
exit /b %ERRORLEVEL%
