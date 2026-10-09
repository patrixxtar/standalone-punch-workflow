@echo off
REM Full flow (VPN + sign-in + navigate) in a visible browser, does NOT click Punch.
cd /d "%~dp0scripts"
python\python.exe -W ignore punch_workflow.py --dry-run
pause
