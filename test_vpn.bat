@echo off
cd /d "%~dp0"
python\python.exe -W ignore vpn_connect.py --check
python\python.exe -W ignore vpn_connect.py
pause
