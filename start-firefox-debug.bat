@echo off
REM ---------------------------------------------------------------------------
REM Launch Firefox with the WebDriver BiDi remote agent so firefox-mcp can
REM attach to it (ws://127.0.0.1:9222/session).
REM
REM IMPORTANT: fully quit any running Firefox FIRST. If Firefox is already
REM running on this profile, this command just opens a new tab in the existing
REM instance and the remote agent will NOT start.
REM ---------------------------------------------------------------------------

echo Make sure Firefox is fully closed, then press a key to launch it in debug mode.
pause >nul

start "" "C:\Program Files\Mozilla Firefox\firefox.exe" --remote-debugging-port 9222

echo Firefox launched with --remote-debugging-port 9222.
echo Leave it running; firefox-mcp will attach to it.
