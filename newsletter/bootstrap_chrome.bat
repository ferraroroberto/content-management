@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM bootstrap_chrome.bat — clean-slate Chrome launcher on :9222
REM for the newsletter-archive pipeline.
REM
REM Always kills every chrome.exe first (and logs what it killed).
REM A stray Chrome process — even one orphaned by an earlier
REM Playwright run — will silently swallow the
REM --remote-debugging-port flag, so a guaranteed clean slate is
REM the only reliable way.
REM
REM Then launches Chrome with the debug port + a dedicated
REM user-data-dir (Chrome 136+ refuses to bind the debug port
REM against the default profile dir for security reasons), and
REM polls until the port responds.
REM
REM The dedicated profile lives in newsletter\chrome_user_data\
REM (gitignored). First time you run this, you'll need to sign into
REM Gmail in the new Chrome window if you want to click newsletter
REM article links from email — the session will then persist.
REM ============================================================

echo === Chrome processes before bootstrap ===
tasklist /FI "IMAGENAME eq chrome.exe" /FO TABLE 2>NUL | findstr /I "chrome.exe"
if errorlevel 1 (
    echo (none)
) else (
    echo Stopping all chrome.exe...
    taskkill /F /IM chrome.exe >NUL 2>&1
    REM Wait for the OS to release the user-data-dir lock.
    timeout /t 2 /nobreak >NUL
    echo Done.
)
echo.

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo Chrome not found at the expected install path.
    echo Edit this file and set CHROME_EXE to your chrome.exe.
    pause
    exit /b 1
)

set "USER_DATA_DIR=%~dp0chrome_user_data"
if not exist "%USER_DATA_DIR%" mkdir "%USER_DATA_DIR%"

echo Launching Chrome with --remote-debugging-port=9222 ...
echo   user-data-dir: %USER_DATA_DIR%
start "" "%CHROME_EXE%" --remote-debugging-port=9222 --user-data-dir="%USER_DATA_DIR%" --no-first-run --no-default-browser-check

REM Poll the debug endpoint until it's ready (~10 s max).
set TRIES=0
:waitloop
set /a TRIES+=1
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/version' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if !errorlevel! equ 0 (
    echo.
    echo Chrome debug port is UP on http://127.0.0.1:9222
    echo Open your newsletter article tabs in the new Chrome window, then run:
    echo     .venv\Scripts\python -m newsletter.dry_run --first-non-gmail-tab --no-write
    exit /b 0
)
if !TRIES! geq 10 (
    echo.
    echo Chrome debug port did not respond after 10 tries.
    echo Check that no other chrome.exe is running and try again.
    pause
    exit /b 3
)
timeout /t 1 /nobreak >NUL
goto waitloop
