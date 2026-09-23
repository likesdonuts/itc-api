@echo off
rem The one thing to double-click. Starts the tracker and opens it in your
rem browser; if it is already running, just opens another browser tab.
rem Keep this window open while you use the app; close it to stop.
cd /d "%~dp0"
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1', 8765)).Close(); exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    start "" "http://127.0.0.1:8765/site/index.html"
    exit /b 0
)
title ITC Tracker
python cli.py serve
pause
