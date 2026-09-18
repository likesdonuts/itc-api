@echo off
rem Targeted update: re-pull only the investigation numbers you enter.
cd /d "%~dp0"
set /p NUMBERS="Investigation number(s), space separated (blank = all): "
if "%NUMBERS%"=="" (
    python cli.py update --all --render
) else (
    python cli.py update %NUMBERS% --render
)
pause
