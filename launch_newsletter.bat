@echo off
REM Visible launcher for newsletter_pipeline.py — keeps CMD window open until
REM you've reviewed the final HTML / must-read line.
REM
REM Usage:
REM   launch_newsletter.bat                   - interactive (prompts for # and
REM                                              "press Enter" between steps)
REM   launch_newsletter.bat --newsletter 057     - pre-fills the newsletter #
REM   launch_newsletter.bat --no-skip-bootstrap  - let the pipeline kill+relaunch
REM                                                Chrome (default: bootstrap is
REM                                                skipped; bring :9222 up yourself
REM                                                via newsletter\bootstrap_chrome.bat)
REM   launch_newsletter.bat --debug              - verbose logs everywhere
REM
REM Full pipeline:
REM   1. Use Chrome already up on :9222 (bootstrap skipped by default; run
REM      newsletter\bootstrap_chrome.bat yourself, or pass --no-skip-bootstrap)
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
