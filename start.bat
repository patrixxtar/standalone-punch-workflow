@echo off
REM Starts the punch scheduler in the background (no window). Use stop.bat to stop it.
cd /d "%~dp0scripts"
if not exist python\pythonw.exe ( echo The scripts\python folder is missing. & pause & exit /b 1 )
if not exist config.json ( echo Copy scripts\config.example.json to scripts\config.json and fill it in. & pause & exit /b 1 )
start "" /B python\pythonw.exe -W ignore scheduler.py
