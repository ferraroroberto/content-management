@echo off
REM Visible-console launcher for the self-healing scheduler skill.
REM Runs /schedule-autoheal headless in THIS window (live --verbose stream),
REM teeing output to results\planning\autoheal-<ts>.log. On a UI-drift failure
REM the agent self-heals end-to-end; otherwise it pings Slack and waits.
REM
REM Usage:
REM   launch_autoheal.bat                  - dry-run, all platforms
REM   launch_autoheal.bat live             - LIVE, all platforms
REM   launch_autoheal.bat twitter          - dry-run, twitter only
REM   launch_autoheal.bat twitter live     - LIVE, twitter only

setlocal EnableDelayedExpansion
REM Fail closed: a path reaching :END without a completed Python run (missing
REM venv) exits non-zero. Only the Python call overwrites this with its own code.
set RC=1
set SCOPE=all
set MODE=--dry-run

:PARSE_ARGS
if "%~1"=="" goto AFTER_ARGS
if /I "%~1"=="live"      set MODE=--live
if /I "%~1"=="--live"    set MODE=--live
if /I "%~1"=="dry"       set MODE=--dry-run
if /I "%~1"=="--dry-run" set MODE=--dry-run
if /I "%~1"=="linkedin"  set SCOPE=linkedin
if /I "%~1"=="instagram" set SCOPE=instagram
if /I "%~1"=="twitter"   set SCOPE=twitter
if /I "%~1"=="threads"   set SCOPE=threads
if /I "%~1"=="all"       set SCOPE=all
shift
goto PARSE_ARGS

:AFTER_ARGS
cd /d "%~dp0"
set VENV_DIR=.\.venv
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at %VENV_DIR%\Scripts\python.exe
    echo [ERROR] Recreate it with: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    goto END
)

echo ========================================
echo Self-healing scheduler: /schedule-autoheal %SCOPE% %MODE%
echo ========================================
echo.
REM Keep this call at top level, NOT inside an if/else: %ERRORLEVEL% inside a
REM parenthesised block is expanded at parse time and reads stale.
"%VENV_DIR%\Scripts\python.exe" app\autoheal_console.py --skill-cmd "/schedule-autoheal %SCOPE% %MODE%"
REM Capture on the very next line — any intervening command overwrites it.
set RC=%ERRORLEVEL%

:END
echo.
echo ========================================
echo Autoheal finished ^(exit %RC%^)
echo ========================================
echo.
echo Press any key to close this window...
pause >nul
REM Propagate the exit code so the scheduler records a crashed run as failed.
REM endlocal discards RC, so both commands must sit on ONE parsed line: %RC%
REM is substituted before endlocal executes.
endlocal & exit /b %RC%
