@echo off
REM Launcher for the Streamlit control panel (run_app.py).
cd /d "%~dp0"

REM Fail closed: reaching :END without a completed run (missing venv) exits
REM non-zero. Only the Python call overwrites this with its own exit code.
set RC=1
set VENV_DIR=.\.venv

REM A missing venv is a hard stop, never a fallback to system Python: the
REM system interpreter has none of this project's pinned dependencies.
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at %VENV_DIR%\Scripts\python.exe
    echo [ERROR] Recreate it with: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    goto END
)

REM Keep this call at top level, NOT inside an if/else: %ERRORLEVEL% inside a
REM parenthesised block is expanded at parse time and reads stale.
"%VENV_DIR%\Scripts\python.exe" run_app.py
REM Capture on the very next line — any intervening command overwrites it.
set RC=%ERRORLEVEL%

:END
pause
REM Propagate the app's exit code so a failed boot isn't recorded as success.
exit /b %RC%
