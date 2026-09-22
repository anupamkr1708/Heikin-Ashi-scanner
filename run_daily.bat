@echo off
REM One-click daily NSE EOD scan (Windows).
REM Double-click this file, or run it from a terminal: run_daily.bat
REM
REM First-time setup (run once):
REM   python -m venv .venv
REM   .venv\Scripts\activate
REM   pip install -e .
REM
REM This script assumes a virtual environment named .venv exists in this folder. If you manage
REM your environment differently, just run:  python scripts\run_daily.py

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON_EXE=.venv\Scripts\python.exe
) else (
    set PYTHON_EXE=python
)

echo Using %PYTHON_EXE%
"%PYTHON_EXE%" scripts\run_daily.py %*

echo.
echo Run finished with exit code %ERRORLEVEL%.
pause
endlocal
