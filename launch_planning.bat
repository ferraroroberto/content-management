@echo off
REM Visible launcher for planning_pipeline.py — keeps CMD window open for the
REM final markdown summary.
REM
REM Usage:
REM   launch_planning.bat              - Dry-run (no posts scheduled), pauses at end.
REM   launch_planning.bat live         - LIVE run (schedules every WIP row), pauses at end.
REM   launch_planning.bat auto         - Dry-run, no pause at end (for scheduled tasks).
REM   launch_planning.bat live auto    - LIVE + no pause.
REM
REM The pipeline runs LinkedIn -> Instagram -> Twitter -> Threads. A failure
REM in one platform does NOT stop the others. The markdown summary lands at
REM results\planning\YYYY-MM-DD-HHMMSS-summary.md.

setlocal EnableDelayedExpansion
set AUTO_MODE=0
set LIVE_MODE=0

:PARSE_ARGS
if "%~1"=="" goto AFTER_ARGS
if /I "%~1"=="auto"      set AUTO_MODE=1
if /I "%~1"=="--auto"    set AUTO_MODE=1
if /I "%~1"=="/auto"     set AUTO_MODE=1
if /I "%~1"=="-y"        set AUTO_MODE=1
if /I "%~1"=="--yes"     set AUTO_MODE=1
if /I "%~1"=="live"      set LIVE_MODE=1
if /I "%~1"=="--live"    set LIVE_MODE=1
if /I "%~1"=="dry"       set LIVE_MODE=0
if /I "%~1"=="--dry-run" set LIVE_MODE=0
shift
goto PARSE_ARGS

:AFTER_ARGS
echo ========================================
echo Starting Planning Pipeline ^(LI -^> IG -^> TW -^> TH^)
if "%AUTO_MODE%"=="1" echo [AUTO MODE - no pause at end]
if "%LIVE_MODE%"=="1" (
    echo [LIVE - posts WILL be scheduled]
) else (
    echo [DRY-RUN - no posts will be scheduled]
)
echo ========================================
echo.

cd /d "%~dp0"

set VENV_DIR=.\.venv
set EXTRA_ARGS=
if "%LIVE_MODE%"=="1" (
    set EXTRA_ARGS=--live
) else (
    set EXTRA_ARGS=--dry-run
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at %VENV_DIR%
    goto END
)

echo [INFO] Running: python planning_pipeline.py %EXTRA_ARGS%
echo.
"%VENV_DIR%\Scripts\python.exe" planning_pipeline.py %EXTRA_ARGS%

:END
echo.
echo ========================================
echo Planning pipeline finished
echo ========================================
if "%AUTO_MODE%"=="1" goto EOL
echo.
echo Press any key to close this window...
pause >nul
:EOL
endlocal
