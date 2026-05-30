@echo off
REM ============================================================
REM bootstrap_chrome.bat — thin wrapper over the Python bootstrap.
REM
REM Launches the dedicated newsletter Chrome on :9222 WITHOUT
REM killing your everyday browser. All logic lives in
REM newsletter\bootstrap_chrome.py (single source of truth) so the
REM Streamlit app and this bat drive the exact same code.
REM
REM Behaviour: if :9222 is already up it reuses it (your tabs stay);
REM otherwise it kills only the Chrome bound to the newsletter
REM profile (if any) and relaunches with the debug port.
REM ============================================================

setlocal
cd /d "%~dp0.."

set VENV_PY=.\.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment not found at %VENV_PY%
    pause
    exit /b 1
)

"%VENV_PY%" -m newsletter.bootstrap_chrome
set RC=%ERRORLEVEL%

if not %RC%==0 (
    echo.
    echo [ERROR] bootstrap failed (exit %RC%)
    pause
)

endlocal & exit /b %RC%
