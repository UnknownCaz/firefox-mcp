@echo off
REM ---------------------------------------------------------------------------
REM Launch Firefox with the WebDriver BiDi remote agent so firefox-mcp can
REM attach to it (ws://127.0.0.1:9222/session).
REM
REM IMPORTANT: fully quit any running Firefox FIRST. If Firefox is already
REM running on this profile, this command just opens a new tab in the existing
REM instance and the remote agent will NOT start.
REM
REM The guard check below is NOT optional. The debug flag makes Firefox's Remote
REM Agent write ~108 automation prefs into this profile, reverted only on a
REM clean shutdown - see ensure-profile-guard.ps1 for the full explanation. The
REM guard lives in the profile, so a profile reset silently removes it; that is
REM why this is checked on every launch instead of assumed.
REM ---------------------------------------------------------------------------

echo Checking the profile guard...
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0ensure-profile-guard.ps1"
if errorlevel 1 (
    echo.
    echo Guard check FAILED - not launching Firefox.
    echo Launching with the debug flag against an unguarded profile is what
    echo poisons it. Fix the problem above, then run this again.
    pause
    exit /b 1
)

echo.
echo Make sure Firefox is fully closed, then press a key to launch it in debug mode.
pause >nul

start "" "C:\Program Files\Mozilla Firefox\firefox.exe" --remote-debugging-port 9222

echo Firefox launched with --remote-debugging-port 9222.
echo Leave it running; firefox-mcp will attach to it.
