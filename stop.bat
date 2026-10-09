@echo off
wmic process where "name='pythonw.exe' and commandline like '%%scheduler.py%%'" delete >nul 2>&1
echo Scheduler stopped.
pause
