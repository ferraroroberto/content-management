@echo off
REM Visible launcher for init.py - keeps CMD window open for logging
REM Runs the reporting pipeline script using the virtual environment interpreter directly
REM
REM Usage:
REM   launcher.bat            - Interactive: prompts for date, asks to confirm Notion update
REM   launcher.bat auto       - Unattended: today's date, auto-confirms all prompts
REM   launcher.bat --auto     - Same as 'auto' (for scheduled tasks)
REM   launcher.bat /auto      - Same as 'auto'
REM   launcher.bat -y         - Same as 'auto'
REM   launcher.bat --yes      - Same as 'auto'
REM
REM In auto mode the window still stays open at the end so the output is visible
REM when you check on it.

set AUTO_MODE=0
if /I "%~1"=="auto"   set AUTO_MODE=1
if /I "%~1"=="--auto" set AUTO_MODE=1
if /I "%~1"=="/auto"  set AUTO_MODE=1
if /I "%~1"=="-y"     set AUTO_MODE=1
if /I "%~1"=="--yes"  set AUTO_MODE=1

echo ========================================
echo Starting Reporting Pipeline
if "%AUTO_MODE%"=="1" echo [AUTO MODE - no prompts]
echo ========================================
echo.

REM Change to the script directory (important for Stream Deck / scheduled task launch)
cd /d "%~dp0"

echo [INFO] Working directory: %CD%
echo.

set TARGET_DATE=
set YES_FLAG=

if "%AUTO_MODE%"=="1" goto AUTO_DATE
goto INTERACTIVE_DATE

:AUTO_DATE
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd')"') do set TARGET_DATE=%%i
set YES_FLAG=--yes
echo [INFO] Auto mode: using today's date with --yes
goto AFTER_DATE

:INTERACTIVE_DATE
echo Select Date for Reporting Pipeline
echo ----------------------------------
echo 0. Today (D) [Default - 3s timeout]
echo 1. Yesterday (D-1)
echo 2. 2 days ago (D-2)
echo 3. 3 days ago (D-3)
echo 4. Custom Date (YYYY-MM-DD)
echo.

REM choice command: /C keys, /N hide keys in prompt, /T timeout seconds, /D default key
choice /C 01234 /N /T 3 /D 0 /M "Enter selection (0-4): "
REM Calculate selection based on errorlevel (1-based index of choice)
set /a selection=%errorlevel%-1

if "%selection%"=="0" (
    for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd')"') do set TARGET_DATE=%%i
)
if "%selection%"=="1" (
    for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-1).ToString('yyyy-MM-dd')"') do set TARGET_DATE=%%i
)
if "%selection%"=="2" (
    for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-2).ToString('yyyy-MM-dd')"') do set TARGET_DATE=%%i
)
if "%selection%"=="3" (
    for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-3).ToString('yyyy-MM-dd')"') do set TARGET_DATE=%%i
)
if "%selection%"=="4" (
    set /p "TARGET_DATE=Enter date (YYYY-MM-DD): "
)
goto AFTER_DATE

:AFTER_DATE
if "%TARGET_DATE%"=="" (
    echo [ERROR] Invalid selection or date calculation failed.
    goto END
)

echo.
echo [INFO] Target Date: %TARGET_DATE%
echo.

REM Path to your virtual environment (adjust if different)
set VENV_DIR=.\.venv

REM Check if virtual environment exists
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [WARNING] Virtual environment not found at %VENV_DIR%
    echo [INFO] Using system Python installation...
    echo.
    python init.py --date %TARGET_DATE% %YES_FLAG%
) else (
    echo [INFO] Using virtual environment at %VENV_DIR%
    echo [INFO] Running script with venv Python...
    echo.
    REM Run the script with Python from the venv (no activation needed)
    "%VENV_DIR%\Scripts\python.exe" init.py --date %TARGET_DATE% %YES_FLAG%
)

echo.
echo ========================================
echo Pipeline finished
echo ========================================
echo.

:END
REM Keep the window open so the output can be inspected after the run.
echo Press any key to close this window...
pause >nul
