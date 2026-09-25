@echo off
rem Double-click to open the representation analytics: law firm and attorney
rem leaderboards, and who represented and opposed whom. A separate app from
rem ITC Tracker. The first time it is opened each day it rebuilds its data in
rem the background; if it is already running, this just opens another tab.
rem Keep this window open while you use the app; close it to stop.
cd /d "%~dp0"
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1', 8766)).Close(); exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    start "" "http://127.0.0.1:8766/"
    exit /b 0
)
title ITC Analytics
python cli.py analytics-serve
pause
