@echo off
rem Daily job: download today's IDS investigations file, rebuild every case
rem record from it, and rebuild the site. No EDIS token needed.
cd /d "%~dp0"
python cli.py sync --render
pause
