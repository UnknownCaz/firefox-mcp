# ensure-profile-guard.ps1
#
# PURPOSE
#   Guarantee that the Firefox profile we are about to attach to has
#   `remote.prefs.recommended = false` pinned in its user.js, BEFORE anything
#   launches Firefox with --remote-debugging-port.
#
# WHY THIS MUST RUN EVERY LAUNCH
#   The debug flag starts Firefox's WebDriver Remote Agent, which calls
#   RecommendedPreferences.applyPreferences() and writes ~108 automation prefs
#   to the USER pref branch - the branch that persists to prefs.js. They are
#   only reverted by the "xpcom-shutdown" observer, so any force-kill, crash or
#   power loss bakes them in permanently. That is how this profile ended up with
#   Safe Browsing off, extension updates off, password saving off, a blank
#   Firefox Home, and Firefox Accounts pointed at a dummy host.
#
#   The guard makes applyPreferences() a no-op. firefox-mcp still attaches fine;
#   those prefs are test-harness conveniences, not a BiDi requirement.
#
#   The guard lives in the PROFILE, not in this repo - so a profile reset or a
#   new profile silently removes it and re-arms the whole problem. That already
#   happened once (2026-07-31, times.json "source":"reset"). Hence: check every
#   launch, cheaply, instead of trusting that a past fix is still in place.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -NoProfile -File ensure-profile-guard.ps1
#   powershell -ExecutionPolicy Bypass -NoProfile -File ensure-profile-guard.ps1 -Quiet
#   powershell -ExecutionPolicy Bypass -NoProfile -File ensure-profile-guard.ps1 -Profile <path>
#
# EXIT CODES
#   0 = guard is in place (already, or just installed)
#   1 = could not determine or write the profile; caller must NOT launch Firefox
#
# SEE ALSO
#   depoison-profile.ps1 - strips prefs that already leaked (needs Firefox closed).

param(
    # Profile directory. Auto-detected from profiles.ini when omitted.
    [string]$Profile,
    # Only speak up when something changed or broke.
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$GuardPref = 'remote.prefs.recommended'
$GuardLine = 'user_pref("remote.prefs.recommended", false);'

function Say($m)  { if (-not $Quiet) { Write-Host "   $m" -ForegroundColor Gray } }
function Ok($m)   { if (-not $Quiet) { Write-Host "   [ok] $m" -ForegroundColor Green } }
function Warn($m) { Write-Host "   [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "   [XX] $m" -ForegroundColor Red }

# ------------------------------------------------------------------ #
# Resolve the profile Firefox will actually use.
#
# profiles.ini is INI-ish but order matters: the [InstallXXXX] section's
# Default= is what a normal `firefox.exe` launch honours, and it wins over the
# legacy [ProfileN] Default=1 flag. Reading the wrong one would install the
# guard into a profile nothing opens - a silent no-op, which is worse than an
# error because it looks fixed.
# ------------------------------------------------------------------ #
function Resolve-DefaultProfile {
    $root = Join-Path $env:APPDATA 'Mozilla\Firefox'
    $ini  = Join-Path $root 'profiles.ini'
    if (-not (Test-Path -LiteralPath $ini)) { return $null }

    $section = ''
    $installDefault = $null
    $legacyDefault = $null
    $current = @{}
    $sections = @{}

    foreach ($line in (Get-Content -LiteralPath $ini)) {
        $t = $line.Trim()
        if ($t -match '^\[(.+)\]$') {
            if ($section) { $sections[$section] = $current }
            $section = $Matches[1]
            $current = @{}
        } elseif ($t -match '^([^=]+)=(.*)$') {
            $current[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    if ($section) { $sections[$section] = $current }

    foreach ($name in $sections.Keys) {
        $s = $sections[$name]
        if ($name -like 'Install*' -and $s.ContainsKey('Default')) {
            $installDefault = $s['Default']
        } elseif ($name -like 'Profile*' -and $s['Default'] -eq '1' -and $s.ContainsKey('Path')) {
            $legacyDefault = $s
        }
    }

    if ($installDefault) {
        return (Join-Path $root ($installDefault -replace '/', '\'))
    }
    if ($legacyDefault) {
        if ($legacyDefault['IsRelative'] -eq '0') { return $legacyDefault['Path'] }
        return (Join-Path $root ($legacyDefault['Path'] -replace '/', '\'))
    }
    return $null
}

if (-not $Profile) {
    $Profile = Resolve-DefaultProfile
    if (-not $Profile) {
        Fail "Could not determine the default Firefox profile from profiles.ini."
        Fail "Refusing to continue: launching with the debug flag against an"
        Fail "unguarded profile is what poisons it. Pass -Profile <path> explicitly."
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $Profile)) {
    Fail "Profile directory not found: $Profile"
    exit 1
}
Say "Profile: $Profile"

# ------------------------------------------------------------------ #
$userJs = Join-Path $Profile 'user.js'

# Match the pref NAME, not the exact line: a guard someone wrote by hand with
# different spacing still counts, and a stale `true` must be caught rather than
# appended to (duplicate user_pref lines resolve last-wins, which would silently
# defeat a guard appended above it).
$existing = @()
if (Test-Path -LiteralPath $userJs) {
    $existing = @(Get-Content -LiteralPath $userJs)
}
$hits = @($existing | Where-Object { $_ -match [regex]::Escape($GuardPref) -and $_ -notmatch '^\s*//' })

if ($hits.Count -gt 0) {
    $bad = @($hits | Where-Object { $_ -match 'true' })
    if ($bad.Count -gt 0) {
        Fail "user.js sets $GuardPref to TRUE - that re-enables the poisoning:"
        foreach ($b in $bad) { Fail "     $b" }
        Fail "Fix that line by hand, then re-run. Refusing to launch."
        exit 1
    }
    Ok "Guard already present."
    exit 0
}

try {
    $header = @(
        '',
        '// Added by firefox-mcp/ensure-profile-guard.ps1',
        '// Stops the WebDriver BiDi Remote Agent (--remote-debugging-port) from',
        '// writing ~108 automation prefs into this profile. Those prefs are only',
        '// reverted on a clean shutdown, so any force-kill made them permanent.',
        '// firefox-mcp attaches fine without them.',
        $GuardLine
    )
    Add-Content -LiteralPath $userJs -Value $header -Encoding utf8
} catch {
    Fail "Could not write $userJs : $($_.Exception.Message)"
    exit 1
}

Warn "Guard was MISSING and has been installed in user.js."
Warn "If this profile is new or was reset, it may already carry leaked prefs -"
Warn "run depoison-profile.ps1 (with Firefox closed) to strip them."
exit 0
