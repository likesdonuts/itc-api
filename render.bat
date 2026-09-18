@echo off
rem UI only: rebuild site/ from the data already on disk. No network, no token.
cd /d "%~dp0"
python cli.py render
pause
