@echo off
REM Visible launcher for init.py - keeps CMD window open for logging
REM Runs the reporting pipeline script using the virtual environment interpreter directly

echo ========================================
echo Starting Reporting Pipeline
echo ========================================
echo.

REM Change to the script directory (important for Stream Deck launch)
cd /d "%~dp0"

echo [INFO] Working directory: %CD%
echo.

REM Path to your virtual environment (adjust if different)
set VENV_DIR=.\.venv

REM Check if virtual environment exists
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [WARNING] Virtual environment not found at %VENV_DIR%
    echo [INFO] Using system Python installation...
    echo.
    python init.py
) else (
    echo [INFO] Using virtual environment at %VENV_DIR%
    echo [INFO] Running script with venv Python...
    echo.
    REM Run the script with Python from the venv (no activation needed)
    "%VENV_DIR%\Scripts\python.exe" init.py
)

echo.
echo ========================================
echo Pipeline finished
echo ========================================
echo.
echo Press any key to close this window...
pause >nul
