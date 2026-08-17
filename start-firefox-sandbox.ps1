# start-firefox-sandbox.ps1
#
# PURPOSE
#   Start a SECOND Firefox on a throwaway profile, alongside Tyler's real one,
#   so Claude can browse without touching his logged-in session.
#
#   main    port 9222  Tyler's profile   - his tabs, logins, extensions
#   sandbox port 9223  this profile      - signed in to nothing
#
#   Switch between them from the MCP with the `switch_browser` tool.
#
# WHY A SEPARATE PROFILE CANNOT SEE HIS TABS
#   A profile IS the tabs, cookies, logins, extensions and prefs - one
#   directory, one unit. This browser starts empty and stays empty. That is the
#   point: it is for work that should not reach his session. If you need to see
#   what Tyler actually has open, use the 'main' target instead.
#
# THE FLAGS, AND WHY EACH MATTERS
#   -profile <dir>  use this directory directly. Deliberately NOT -P <name>:
#                   -P registers the profile in profiles.ini and makes it show
#                   up in Tyler's profile manager. -profile keeps it invisible.
#   --no-remote     REQUIRED. Without it a second firefox.exe just hands its
#                   command line to the already-running instance and exits -
#                   you get a new tab in his window and no second browser.
#   --remote-debugging-port 9223
#                   separate port, so the two remote agents do not collide.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1
#   powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1 -Status
#   powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1 -Reset

param(
    # Report what is running and exit.
    [switch]$Status,
    # Delete the sandbox profile and recreate it empty on next start.
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

$FirefoxExe  = 'C:\Program Files\Mozilla Firefox\firefox.exe'
$SandboxPort = 9223
$MainPort    = 9222
# Lives outside the repo: it is disposable state, not source, and it would be a
# large accidental commit.
$ProfileDir  = Join-Path $env:LOCALAPPDATA 'firefox-mcp\sandbox-profile'

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "   [XX] $m" -ForegroundColor Red }

function Test-Port($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# ------------------------------------------------------------------ #
if ($Status) {
    Step "Firefox remote agents"
    $m = Test-Port $MainPort
    $s = Test-Port $SandboxPort
    Write-Host ("   main    (port {0}): {1}" -f $MainPort,    $(if ($m) { 'listening' } else { 'not listening' }))
    Write-Host ("   sandbox (port {0}): {1}" -f $SandboxPort, $(if ($s) { 'listening' } else { 'not listening' }))
    Write-Host "   sandbox profile: $ProfileDir"
    Write-Host ("   profile exists : {0}" -f (Test-Path -LiteralPath $ProfileDir))
    exit 0
}

# ------------------------------------------------------------------ #
if ($Reset) {
    Step "Resetting the sandbox profile"
    if (Test-Path -LiteralPath $ProfileDir) {
        if (Test-Port $SandboxPort) {
            Fail "The sandbox Firefox is running - close that window first."
            Fail "Refusing to delete a profile that is in use."
            exit 1
        }
        # Guard against a mis-set $ProfileDir nuking something real. The path is
        # constructed above, but a one-character edit to this script should not
        # be able to delete a home directory.
        if ($ProfileDir -notmatch 'firefox-mcp\\sandbox-profile$') {
            Fail "Refusing to delete an unexpected path: $ProfileDir"
            exit 1
        }
        Remove-Item -LiteralPath $ProfileDir -Recurse -Force
        Ok "Deleted $ProfileDir"
    } else {
        Ok "Nothing to delete."
    }
}

# ------------------------------------------------------------------ #
Step "Checking Firefox is installed"
if (-not (Test-Path -LiteralPath $FirefoxExe)) {
    Fail "Firefox not found at $FirefoxExe"
    exit 1
}
Ok $FirefoxExe

# ------------------------------------------------------------------ #
Step "Checking port $SandboxPort"
if (Test-Port $SandboxPort) {
    Ok "Sandbox is already running on port $SandboxPort - leaving it alone."
    Write-Host "   Use switch_browser('sandbox') from the MCP." -ForegroundColor White
    exit 0
}
Ok "Port $SandboxPort is free."

# ------------------------------------------------------------------ #
Step "Preparing the sandbox profile"
if (-not (Test-Path -LiteralPath $ProfileDir)) {
    New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
    Ok "Created $ProfileDir (Firefox will initialise it on first start)."
} else {
    Ok "Using existing $ProfileDir"
}

# Guard this profile too. The automation prefs would be survivable here - it is
# disposable - but they also switch Safe Browsing off, and this is the browser
# most likely to be pointed at pages nobody vouched for.
$guard = Join-Path $PSScriptRoot 'ensure-profile-guard.ps1'
if (Test-Path -LiteralPath $guard) {
    & powershell -ExecutionPolicy Bypass -NoProfile -File $guard -Profile $ProfileDir
    if ($LASTEXITCODE -ne 0) {
        Fail "Profile guard failed - refusing to launch."
        exit 1
    }
} else {
    Fail "ensure-profile-guard.ps1 not found next to this script."
    exit 1
}

# ------------------------------------------------------------------ #
Step "Launching the sandbox Firefox"
Start-Process -FilePath $FirefoxExe -ArgumentList @(
    '-profile', $ProfileDir,
    '--no-remote',
    '--remote-debugging-port', "$SandboxPort"
)

Write-Host "   Waiting for the remote agent to bind..."
$bound = $false
foreach ($i in 1..25) {
    Start-Sleep -Seconds 1
    # Poll the port: Start-Process returning proves a process started, not that
    # the agent is listening.
    if (Test-Port $SandboxPort) { $bound = $true; break }
}

if (-not $bound) {
    Fail "Port $SandboxPort never came up."
    Fail "Most likely cause: --no-remote was ignored and this handed off to the"
    Fail "running Firefox instead of starting a second one. Check for a new tab"
    Fail "in Tyler's window."
    exit 1
}

Ok "Sandbox Firefox is listening on port $SandboxPort."
Write-Host ""
Write-Host "   From the MCP: switch_browser('sandbox') to use it," -ForegroundColor White
Write-Host "                 switch_browser('main')    to go back to Tyler's." -ForegroundColor White
Write-Host "   This window is disposable - close it any time, or run -Reset to wipe it." -ForegroundColor White
exit 0
