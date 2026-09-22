@echo off
rem Documents only: ask EDIS for the document lists of the cases you name and
rem download their public PDFs. Case information and parties are untouched.
cd /d "%~dp0"
set /p NUMBERS="Investigation number(s), space separated (blank = refresh what's already fetched): "
if "%NUMBERS%"=="" (
    python cli.py docs --existing --render
) else (
    python cli.py docs %NUMBERS% --render
)
pause
