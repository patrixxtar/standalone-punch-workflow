@echo off
REM Removes Windows' "downloaded from the internet" mark from every file in this folder,
REM so Windows stops showing the "Open File - Security Warning (Run / Don't run)" prompt.
REM Run it ONCE after unzipping (it may show that prompt one last time itself).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File"
echo Done. Files in this folder are unblocked.
pause
