@echo off
REM Manual LIVE punch in a visible browser. Use this for the very first run
REM so you can type the UKG email code and get the device remembered.
cd /d "%~dp0"
python\python.exe -W ignore punch_workflow.py
pause
