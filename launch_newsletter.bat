@echo off
REM Visible launcher for the newsletter pipeline — keeps CMD window open until
REM you've reviewed the final HTML / must-read line.
REM
REM Runs the full interactive console sequence (newsletter_pipeline.py "all"):
REM   1. Bootstrap the dedicated newsletter Chrome on :9222 (targeted — does
REM      NOT kill your everyday browser; see newsletter\bootstrap_chrome.py)
REM   2. Wait for you to open the newsletter article tabs in that window
REM   3. Archive each tab to Notion
REM   4. normalize_names + normalize_url (last 14 days by default)
REM   5. Build the HTML at results\newsletter\N{NNN}.html, open it, prompt for
REM      the must-read topic, copy the composed line to the clipboard
REM
REM Usage:
REM   launch_newsletter.bat                    - full interactive sequence
REM   launch_newsletter.bat --newsletter 057   - pre-fill the newsletter #
REM   launch_newsletter.bat --days 7           - tighter normalise window
REM   launch_newsletter.bat --debug            - verbose logs everywhere
REM
REM To run a single step instead, call the subcommand directly, e.g.:
REM   .venv\Scripts\python.exe newsletter_pipeline.py bootstrap
REM   .venv\Scripts\python.exe newsletter_pipeline.py archive
REM   .venv\Scripts\python.exe newsletter_pipeline.py build --newsletter 057

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

"%VENV_DIR%\Scripts\python.exe" newsletter_pipeline.py all %*
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
