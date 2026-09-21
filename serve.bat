@echo off
rem Open the site with working Update / Fetch docs buttons. Leave this window
rem open while you use them; close it or press Ctrl+C to stop.
cd /d "%~dp0"
python cli.py serve
pause
