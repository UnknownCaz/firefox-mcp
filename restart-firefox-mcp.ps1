# restart-firefox-mcp.ps1
#
# PURPOSE
#   Clear stray firefox-mcp server processes so the next Claude Code start can
#   attach to Firefox again.
#
#   firefox-mcp runs as a stdio subprocess of the Claude Code client, and those
#   subprocesses outlive the client that spawned them. One survivor keeps holding
#   Firefox's single WebDriver BiDi session, so the next start fails with
#   "Maximum number of active sessions" and the server looks broken when it is
#   only locked out.
#
# ORDERING CONSTRAINT
#   Run this AFTER fully quitting Claude Code and BEFORE relaunching it. It
#   refuses to run while the client is up, because killing a live client's stdio
#   children just gets them respawned, which races the died-check and lands you
#   back at the same error with a more confusing log. Use -Force to override.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -File restart-firefox-mcp.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File restart-firefox-mcp.ps1
#   powershell -ExecutionPolicy Bypass -File restart-firefox-mcp.ps1 -RestartFirefox
#
#   -ExecutionPolicy Bypass is not optional for an unsigned script under a
#   restrictive policy.
#
# SEE ALSO
#   start-firefox-debug.bat - launches Firefox with the debug flag in the first
#   place. Keep the port and flags in these two files in sync.
#
# TODO: -DryRun is a homegrown spelling of PowerShell's -WhatIf. Converting to
# [CmdletBinding(SupportsShouldProcess)] would match the native idiom.

param(
    # Print the plan and change nothing.
    [switch]$DryRun,
    # Proceed even if Claude Code still appears to be running.
    [switch]$Force,
    # Also restart Firefox. Off by default: an already-listening Firefox is
    # left strictly alone, because it is the logged-in session this server exists
    # to attach to.
    [switch]$RestartFirefox
)

$ErrorActionPreference = 'Stop'
$FirefoxExe = 'C:\Program Files\Mozilla Firefox\firefox.exe'
$DebugPort  = 9222
$RepoName   = 'firefox-mcp'

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "   [ok] $msg"   -ForegroundColor Green }
function Warn($msg) { Write-Host "   [!!] $msg"   -ForegroundColor Yellow }
function Fail($msg) { Write-Host "   [XX] $msg"   -ForegroundColor Red }

# Match on the command line, never on the interpreter name. A bare
# "python*" match would happily kill an unrelated pytest run, a different MCP
# server, or one of Tyler's own scripts - this box runs several.
function Get-FirefoxMcpProcs {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like "*$RepoName*firefox-mcp.exe*" -or
                $_.CommandLine -like '*firefox_mcp*'
            )
        }
}

function Test-DebugPort {
    [bool](Get-NetTCPConnection -LocalPort $DebugPort -State Listen -ErrorAction SilentlyContinue)
}

# ------------------------------------------------------------------ #
Step "Checking Claude Code is closed"
$claude = Get-Process -Name 'Claude', 'claude' -ErrorAction SilentlyContinue
if ($claude) {
    if ($Force) {
        Warn "Claude Code is running (PID(s): $($claude.Id -join ', ')) - continuing because -Force was passed."
    } else {
        Fail "Claude Code is still running (PID(s): $($claude.Id -join ', '))."
        Fail "Quit it fully first. Killing a live client's servers just respawns them."
        Fail "Pass -Force to override."
        exit 1
    }
} else {
    Ok "No Claude Code process found."
}

# ------------------------------------------------------------------ #
Step "Clearing stray $RepoName server processes"
$procs = @(Get-FirefoxMcpProcs)
if ($procs.Count -eq 0) {
    Ok "None running - nothing to clear."
} else {
    # Print the target list BEFORE acting. This is most of the safety a
    # confirmation prompt would buy, at no friction cost.
    Write-Host "   About to kill $($procs.Count) process(es):"
    foreach ($p in $procs) {
        Write-Host "     PID $($p.ProcessId)  $($p.Name)"
        Write-Host "        $($p.CommandLine)"
    }
    if ($DryRun) {
        Warn "DryRun: nothing killed."
    } else {
        foreach ($p in $procs) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }
            catch { Warn "PID $($p.ProcessId): $($_.Exception.Message)" }
        }
        Start-Sleep -Seconds 2
        # Re-check rather than assuming the kill worked.
        $left = @(Get-FirefoxMcpProcs)
        if ($left.Count -gt 0) {
            Fail "$($left.Count) still alive: $($left.ProcessId -join ', '). Use Task Manager."
            exit 1
        }
        Ok "All cleared, and verified dead."
    }
}

# ------------------------------------------------------------------ #
Step "Checking Firefox on port $DebugPort"
$listening = Test-DebugPort

if ($listening -and -not $RestartFirefox) {
    Ok "Port $DebugPort is listening. Leaving Firefox alone."
} elseif (-not $listening -and -not $RestartFirefox) {
    Warn "Port $DebugPort is NOT listening, and -RestartFirefox was not passed."
    Warn "Firefox must be relaunched with --remote-debugging-port $DebugPort or"
    Warn "this server has nothing to attach to. Run start-firefox-debug.bat, or"
    Warn "re-run this script with -RestartFirefox."
} elseif ($DryRun) {
    Warn "DryRun: would restart Firefox with --remote-debugging-port $DebugPort."
} else {
    # Abort rather than launch blind. A Firefox that comes up on the WRONG
    # profile is NOT logged in, every tool still "works", and every result is
    # silently from an anonymous session - worse than the error being fixed.
    if (-not (Test-Path $FirefoxExe)) {
        Fail "Firefox not found at $FirefoxExe."
        Fail "Refusing to launch: a guessed binary could come up on a profile that is not signed in."
        exit 1
    }
    $ff = Get-Process -Name firefox -ErrorAction SilentlyContinue
    if ($ff) {
        Write-Host "   Closing Firefox gracefully so it saves the session..."
        foreach ($p in $ff) { try { $null = $p.CloseMainWindow() } catch {} }
        Start-Sleep -Seconds 6
        if (Get-Process -Name firefox -ErrorAction SilentlyContinue) {
            Warn "Graceful close incomplete - forcing."
            try { Stop-Process -Name firefox -Force -ErrorAction Stop } catch {}
            Start-Sleep -Seconds 3
        }
    }
    # No -P: this relies on the DEFAULT profile being the logged-in one, which
    # is how Tyler launches Firefox normally. If that ever stops being true,
    # pass the profile explicitly rather than trusting the default.
    Start-Process -FilePath $FirefoxExe -ArgumentList @('--remote-debugging-port', "$DebugPort")
    Write-Host "   Waiting for the remote agent to bind..."
    $bound = $false
    foreach ($i in 1..20) {
        Start-Sleep -Seconds 1
        # Poll the port. Start-Process returning proves only that a process
        # started, not that the debug agent is listening.
        if (Test-DebugPort) { $bound = $true; break }
    }
    if ($bound) {
        Ok "Port $DebugPort is listening."
    } else {
        Fail "Port $DebugPort never came up. Firefox may have attached to an existing instance instead of starting a new one."
        exit 1
    }
}

# ------------------------------------------------------------------ #
Step "Done"
Write-Host "   Relaunch Claude Code. The status tool is firefox_status" -ForegroundColor White
Write-Host "   (renamed in 0.3.0 from a generic name that collided with the" -ForegroundColor White
Write-Host "   in-app browser pane and with Chrome)." -ForegroundColor White
exit 0
