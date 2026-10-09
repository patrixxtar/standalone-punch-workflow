@echo off
cd /d "%~dp0"
echo --- Gmail forwarding confirmation code for your alias (if any) ---
python\python.exe -W ignore otp_mail.py --verify
echo.
echo --- Newest UKG code for your alias (if any) ---
python\python.exe -W ignore otp_mail.py --test
pause
