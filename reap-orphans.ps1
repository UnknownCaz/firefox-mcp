# reap-orphans.ps1
#
# PURPOSE
#   Kill firefox-mcp server processes whose Claude Code parent is GONE, and
#   nothing else. Safe to run at any time, including while Claude Code is open.
#
# WHY THIS EXISTS ALONGSIDE restart-firefox-mcp.ps1
#   restart-firefox-mcp.ps1 clears ALL firefox-mcp servers and therefore refuses
#   to run while Claude Code is up - with several sessions open, its sweep would
#   take the tools offline in every one of them. That is the correct behaviour
#   for a full reset, and useless for routine cleanup.
#
#   The distinguishing fact is parentage, not count. A server whose parent
#   claude.exe still exists is SERVING that session. A server whose parent is
#   dead is a true orphan: nothing will ever talk to it again, and if Firefox is
#   in debug mode it may still be holding the single BiDi session, which is what
#   makes the next start fail with "Maximum number of active sessions".
#
#   Measured 2026-08-16: 15 matching processes, 5 groups of 3, EVERY group with
#   a live claude.exe parent - i.e. five live sessions and zero orphans. A
#   count-based sweep would have killed all five sessions' servers.
#
# PARENT-CHAIN NOTE
#   Each session's server is a 3-process chain (firefox-mcp.exe shim -> python
#   -> python). Only the shim's parent is a claude.exe; the inner Python
#   processes are parented to the shim. So orphanhood is decided at the TOP of
#   the chain and applied to its descendants, rather than tested per process -
#   testing each one independently would spare the inner two.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -NoProfile -File reap-orphans.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -NoProfile -File reap-orphans.ps1
#   powershell -ExecutionPolicy Bypass -NoProfile -File reap-orphans.ps1 -Quiet
#
# EXIT CODES
#   0 = nothing to do, or reaped successfully
#   2 = orphans found but -DryRun was set (so a watcher can tell "found" from
#       "clean" without parsing text)

param(
    [switch]$DryRun,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

# Same allowlist as restart-firefox-mcp.ps1: match on the command line AND an
# interpreter name, never on the interpreter alone - a bare python match would
# happily kill an unrelated script, and a bare command-line match would catch
# any shell that merely mentions the module (including the one running this).
$ServerProcNames = @('python.exe', 'pythonw.exe', 'firefox-mcp.exe')

function Say($m)  { if (-not $Quiet) { Write-Host $m } }

$all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -in $ServerProcNames -and $_.CommandLine -and (
        $_.CommandLine -like '*firefox-mcp*' -or $_.CommandLine -like '*firefox_mcp*'
    )
})

if ($all.Count -eq 0) {
    Say "No firefox-mcp server processes at all."
    exit 0
}

# Index every live pid once; walking the chain per process would re-query WMI
# hundreds of times on a busy box.
$live = @{}
foreach ($p in (Get-CimInstance Win32_Process)) { $live[[int]$p.ProcessId] = $p }

function Get-ChainRoot($proc) {
    # Walk up while the parent is still one of our own server processes, so the
    # shim at the top is what gets judged. Bounded to avoid spinning on a pid
    # cycle after pid reuse.
    $cur = $proc
    for ($i = 0; $i -lt 10; $i++) {
        $par = $live[[int]$cur.ParentProcessId]
        if ($par -and $par.Name -in $ServerProcNames -and $par.CommandLine -and (
                $par.CommandLine -like '*firefox-mcp*' -or $par.CommandLine -like '*firefox_mcp*')) {
            $cur = $par
        } else {
            break
        }
    }
    return $cur
}

$orphans = @()
$served  = @()
$unknown = @()
foreach ($p in $all) {
    $root = Get-ChainRoot $p
    $owner = $live[[int]$root.ParentProcessId]
    if (-not $owner) {
        # The only condition that justifies killing: the parent pid no longer
        # exists, so nothing can ever speak to this server again.
        $orphans += [pscustomobject]@{ Proc = $p; Root = $root.ProcessId }
    } elseif ($owner.Name -like 'claude*') {
        $served += [pscustomobject]@{ Proc = $p; Owner = $owner.ProcessId }
    } else {
        # Alive, but not owned by Claude - a server started by hand from a
        # terminal for debugging looks exactly like this. Deliberately NOT
        # killed: "not a Claude child" is not the same claim as "abandoned",
        # and only the second one licenses a kill.
        $unknown += [pscustomobject]@{ Proc = $p; Owner = $owner.ProcessId; OwnerName = $owner.Name }
    }
}

Say "firefox-mcp processes: $($all.Count)  |  serving a live session: $($served.Count)  |  other live owner: $($unknown.Count)  |  orphaned: $($orphans.Count)"

if ($served.Count -gt 0 -and -not $Quiet) {
    $owners = ($served | ForEach-Object { $_.Owner } | Sort-Object -Unique) -join ', '
    Write-Host "   Leaving alone - live claude.exe parent(s): $owners" -ForegroundColor Green
}
if ($unknown.Count -gt 0 -and -not $Quiet) {
    foreach ($u in ($unknown | Sort-Object Owner -Unique)) {
        Write-Host "   Leaving alone - live non-Claude owner: pid $($u.Owner) [$($u.OwnerName)]" -ForegroundColor Green
    }
}

if ($orphans.Count -eq 0) {
    Say "Nothing to reap."
    exit 0
}

foreach ($o in $orphans) {
    Write-Host ("   ORPHAN pid={0} {1} (chain root {2}, parent gone)" -f `
        $o.Proc.ProcessId, $o.Proc.Name, $o.Root) -ForegroundColor Yellow
}

if ($DryRun) {
    Say "DryRun: nothing killed."
    exit 2
}

foreach ($o in $orphans) {
    try { Stop-Process -Id $o.Proc.ProcessId -Force -ErrorAction Stop }
    catch { Write-Host "   pid $($o.Proc.ProcessId): $($_.Exception.Message)" -ForegroundColor Yellow }
}
Start-Sleep -Seconds 2

# Re-check rather than trusting the kill: a process that survived is the whole
# reason the old failure mode was confusing.
$stillThere = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessId -in ($orphans | ForEach-Object { $_.Proc.ProcessId })
})
if ($stillThere.Count -gt 0) {
    Write-Host "   $($stillThere.Count) survived: $(($stillThere | ForEach-Object { $_.ProcessId }) -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "   Reaped $($orphans.Count) orphan(s), verified gone." -ForegroundColor Green
exit 0
