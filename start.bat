@echo off
REM Starts the punch scheduler in the background (no window). Use stop.bat to stop it.
cd /d "%~dp0"
if not exist python\pythonw.exe ( echo Run setup.bat first. & pause & exit /b 1 )
if not exist config.json ( echo Copy config.example.json to config.json and fill it in. & pause & exit /b 1 )
start "" /B python\pythonw.exe -W ignore scheduler.py
