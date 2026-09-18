@echo off
rem New case discovery: check the RSS feed, then pull anything we don't track yet.
cd /d "%~dp0"
python cli.py discover --render
pause
