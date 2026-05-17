@echo off
REM Visible launcher for newsletter_pipeline.py — keeps CMD window open until
REM you've reviewed the final HTML / must-read line.
REM
REM Usage:
REM   launch_newsletter.bat                   - interactive (prompts for # and
REM                                              "press Enter" between steps)
REM   launch_newsletter.bat --newsletter 057  - pre-fills the newsletter #
REM   launch_newsletter.bat --skip-bootstrap  - reuse an already-up Chrome :9222
REM   launch_newsletter.bat --debug           - verbose logs everywhere
REM
REM Full pipeline:
REM   1. Bootstrap Chrome on :9222 (kill + relaunch on the dedicated profile)
REM   2. Wait for you to open the newsletter article tabs
REM   3. Archive each tab to Notion (newsletter.pipeline.run_batch)
REM   4. Wait, then normalize_names + normalize_url
REM   5. Build newsletter HTML at results\newsletter\N{NNN}.html, open in
REM      browser, prompt for must-read topic, copy line to clipboard

setlocal

cd /d "%~dp0"

set VENV_DIR=.\.venv
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at %VENV_DIR%
    pause
    exit /b 1
)

echo ========================================
echo Starting Newsletter Pipeline
echo ========================================
echo.

"%VENV_DIR%\Scripts\python.exe" newsletter_pipeline.py %*
set RC=%ERRORLEVEL%

echo.
echo ========================================
echo Newsletter pipeline finished (exit %RC%)
echo ========================================
echo.
echo Press any key to close this window...
pause >nul

endlocal
exit /b %RC%
