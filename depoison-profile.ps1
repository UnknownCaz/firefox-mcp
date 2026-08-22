# depoison-profile.ps1
#
# PURPOSE
#   Strip Firefox Remote Agent "recommended preferences" that leaked into a real
#   user profile, and install a user.js that stops them from ever coming back.
#
# WHY THIS EXISTS
#   Launching Firefox with --remote-debugging-port activates the Remote Agent,
#   which calls RecommendedPreferences.applyPreferences(). That writes ~108
#   automation prefs to the USER branch (Services.prefs.setBoolPref etc.), which
#   is the branch that persists to prefs.js. They are only reverted on the
#   "xpcom-shutdown" observer - i.e. a CLEAN exit. Any force-kill, crash, or
#   power loss bakes them in permanently.
#
#   Leaked prefs disable Safe Browsing, extension updates, the add-on blocklist,
#   password saving, and the New Tab page, and repoint Firefox Accounts /
#   remote settings / telemetry at unresolvable "%(server)s" placeholder hosts.
#
# THE PERMANENT FIX is the user.js line, not the cleanup: setting
#   remote.prefs.recommended = false
# makes applyPreferences() a no-op, so the debug flag stops touching prefs at
# all. firefox-mcp still attaches fine - those prefs are test-harness
# conveniences, not a BiDi requirement.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -File depoison-profile.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File depoison-profile.ps1
#
#   Firefox MUST be fully closed. Firefox holds prefs in memory and rewrites
#   prefs.js wholesale on exit, so editing it under a live Firefox is silently
#   undone.

param(
    # Defaults to the single *.default-release* profile if there is exactly one.
    # Pass it explicitly when you have several - guessing wrong would strip prefs
    # from a profile you never open, which looks like success and fixes nothing.
    [string]$Profile,
    # Print the plan and change nothing.
    [switch]$DryRun,
    # Skip the prefs.js cleanup and only install user.js.
    [switch]$UserJsOnly
)

$ErrorActionPreference = 'Stop'

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "   [XX] $m" -ForegroundColor Red }

# ------------------------------------------------------------------ #
Step "Checking Firefox is closed"
$ff = Get-Process -Name firefox -ErrorAction SilentlyContinue
if ($ff) {
    Fail "Firefox is running (PID(s): $($ff.Id -join ', '))."
    Fail "Close it fully first - Firefox rewrites prefs.js from memory on exit,"
    Fail "so any edit made now is discarded when it quits."
    exit 1
}
Ok "No Firefox process found."

if (-not $Profile) {
    $profilesDir = Join-Path $env:APPDATA 'Mozilla\Firefox\Profiles'
    $candidates = @(Get-ChildItem -LiteralPath $profilesDir -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like '*.default-release*' })
    if ($candidates.Count -eq 1) {
        $Profile = $candidates[0].FullName
    } elseif ($candidates.Count -eq 0) {
        Fail "No *.default-release* profile found under $profilesDir."
        Fail "Pass the profile directory explicitly: -Profile <path>"
        exit 1
    } else {
        Fail "Found $($candidates.Count) release profiles; refusing to guess."
        $candidates | ForEach-Object { Fail "  $($_.Name)" }
        Fail "Pass the one you actually use: -Profile <path>"
        exit 1
    }
}

if (-not (Test-Path $Profile)) { Fail "Profile not found: $Profile"; exit 1 }
Ok "Profile: $Profile"

# ------------------------------------------------------------------ #
# The automation prefs, verbatim from
# chrome/remote/content/shared/RecommendedPreferences.sys.mjs (Firefox 153.0.4).
# Name -> the value the Remote Agent sets. A pref is only removed when its
# CURRENT value still equals this - anything you deliberately set to the same
# name but a different value is left strictly alone.
$Automation = @{
    'app.normandy.api_url' = '""'
    'app.update.disabledForTesting' = 'true'
    'apz.axis_lock.mode' = '0'
    'apz.content_response_timeout' = '60000'
    'browser.backup.enabled' = 'false'
    'browser.contentblocking.introCount' = '99'
    'browser.discovery.enabled' = 'false'
    'browser.dom.window.dump.enabled' = 'true'
    'browser.download.panel.shown' = 'true'
    'browser.http.blank_page_with_error_response.enabled' = 'true'
    'browser.ml.enable' = 'false'
    'browser.newtabpage.activity-stream.asrouter.userprefs.cfr.features' = 'false'
    'browser.newtabpage.activity-stream.showSponsoredTopSites' = 'false'
    'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds' = 'false'
    'browser.pagethumbnails.capturing_disabled' = 'true'
    'browser.region.network.url' = '""'
    'browser.safebrowsing.blockedURIs.enabled' = 'false'
    'browser.safebrowsing.downloads.enabled' = 'false'
    'browser.safebrowsing.malware.enabled' = 'false'
    'browser.safebrowsing.phishing.enabled' = 'false'
    'browser.search.update' = 'false'
    'browser.sessionstore.resume_from_crash' = 'false'
    'browser.shell.checkDefaultBrowser' = 'false'
    'browser.tabs.remote.unloadDelayMs' = '0'
    'browser.tabs.unloadOnLowMemory' = 'false'
    'browser.tabs.warnOnCloseOtherTabs' = 'false'
    'browser.tabs.warnOnOpen' = 'false'
    'browser.topsites.contile.enabled' = 'false'
    'browser.translations.enable' = 'false'
    'browser.urlbar.merino.endpointURL' = '""'
    'browser.urlbar.merino.ohttpConfigURL' = '""'
    'browser.urlbar.merino.ohttpRelayURL' = '""'
    'browser.urlbar.suggest.searches' = 'false'
    'browser.usedOnWindows10.introURL' = '""'
    'browser.warnOnQuit' = 'false'
    'datareporting.healthreport.documentServerURI' = '"http://%(server)s/dummy/healthreport/"'
    'datareporting.healthreport.logging.consoleEnabled' = 'false'
    'datareporting.healthreport.service.enabled' = 'false'
    'datareporting.healthreport.service.firstRun' = 'false'
    'datareporting.healthreport.uploadEnabled' = 'false'
    'datareporting.policy.dataSubmissionEnabled' = 'false'
    'datareporting.policy.dataSubmissionPolicyBypassNotification' = 'true'
    'datareporting.usage.uploadEnabled' = 'false'
    'dom.disable_open_during_load' = 'false'
    'dom.file.createInChild' = 'true'
    'dom.input_events.security.minNumTicks' = '0'
    'dom.input_events.security.minTimeElapsedInMS' = '0'
    'dom.ipc.processPriorityManager.enabled' = 'false'
    'dom.ipc.reportProcessHangs' = 'false'
    'dom.max_script_run_time' = '0'
    'dom.navigation.navigationRateLimit.count' = '0'
    'dom.permissions.testing.enabled' = 'true'
    'dom.push.connection.enabled' = 'false'
    'dom.successive_dialog_time_limit' = '0'
    'extensions.autoDisableScopes' = '0'
    'extensions.blocklist.detailsURL' = '"http://%(server)s/extensions-dummy/blocklistDetailsURL"'
    'extensions.blocklist.itemURL' = '"http://%(server)s/extensions-dummy/blocklistItemURL"'
    'extensions.enabledScopes' = '5'
    'extensions.formautofill.addresses.enabled' = 'false'
    'extensions.formautofill.creditCards.enabled' = 'false'
    'extensions.getAddons.cache.enabled' = 'false'
    'extensions.getAddons.discovery.api_url' = '"data:, "'
    'extensions.getAddons.get.url' = '"http://%(server)s/extensions-dummy/repositoryGetURL"'
    'extensions.getAddons.search.browseURL' = '"http://%(server)s/extensions-dummy/repositoryBrowseURL"'
    'extensions.hotfix.url' = '"http://%(server)s/extensions-dummy/hotfixURL"'
    'extensions.installDistroAddons' = 'false'
    'extensions.systemAddon.update.enabled' = 'false'
    'extensions.update.background.url' = '"http://%(server)s/extensions-dummy/updateBackgroundURL"'
    'extensions.update.enabled' = 'false'
    'extensions.update.notifyUser' = 'false'
    'extensions.update.url' = '"http://%(server)s/extensions-dummy/updateURL"'
    'focusmanager.testmode' = 'true'
    'general.useragent.updates.enabled' = 'false'
    'geo.prompt.open_system_prefs' = 'false'
    'geo.provider.network.url' = '""'
    'geo.provider.testing' = 'true'
    'geo.wifi.scan' = 'false'
    'identity.fxaccounts.auth.uri' = '"https://{server}/dummy/fxa"'
    'mousewheel.allow_scrolling_more_than_one_page' = 'true'
    'network.captive-portal-service.enabled' = 'false'
    'network.connectivity-service.enabled' = 'false'
    'network.manage-offline-status' = 'false'
    'network.sntp.pools' = '"%(server)s"'
    'remote.prefs.recommended.applied' = 'true'
    'security.certerrors.mitm.priming.enabled' = 'false'
    'security.fileuri.strict_origin_policy' = 'false'
    'security.notification_enable_delay' = '0'
    'security.remote_settings.intermediates.enabled' = 'false'
    'security.webauthn.related_origin_requests_mode' = '1'
    'services.settings.loglevel' = '"off"'
    'services.settings.server' = '"data:,#remote-settings-dummy/v1"'
    'signon.autofillForms' = 'false'
    'signon.management.page.breach-alerts.enabled' = 'false'
    'signon.management.page.vulnerable-passwords.enabled' = 'false'
    'signon.rememberSignons' = 'false'
    'startup.homepage_welcome_url' = '"about:blank"'
    'telemetry.fog.test.localhost_port' = '-1'
    'termsofuse.bypassNotification' = 'true'
    'toolkit.startup.max_resumed_crashes' = '-1'
    'toolkit.telemetry.server' = '"https://%(server)s/telemetry-dummy/"'
    'widget.windows.window_occlusion_tracking.enabled' = 'false'
}

# ------------------------------------------------------------------ #
if (-not $UserJsOnly) {
    Step "Scanning prefs.js for leaked automation prefs"
    $prefsPath = Join-Path $Profile 'prefs.js'
    if (-not (Test-Path $prefsPath)) { Fail "prefs.js not found."; exit 1 }

    $lines = Get-Content -LiteralPath $prefsPath -Encoding UTF8
    $keep = New-Object System.Collections.Generic.List[string]
    $removed = New-Object System.Collections.Generic.List[string]

    foreach ($line in $lines) {
        # Match only well-formed user_pref lines; anything else passes through
        # untouched so a malformed file is never made worse.
        $m = [regex]::Match($line, '^user_pref\("([^"]+)", (.*)\);\s*$')
        if ($m.Success -and $Automation.ContainsKey($m.Groups[1].Value) -and
            $Automation[$m.Groups[1].Value] -eq $m.Groups[2].Value) {
            $removed.Add($m.Groups[1].Value)
        } else {
            $keep.Add($line)
        }
    }

    Ok "$($removed.Count) leaked pref(s) found; $($keep.Count) line(s) kept."
    foreach ($r in $removed) { Write-Host "     - $r" -ForegroundColor DarkGray }

    if ($removed.Count -eq 0) {
        Ok "prefs.js is already clean."
    } elseif ($DryRun) {
        Warn "DryRun: prefs.js not modified."
    } else {
        # Timestamped backup, never a fixed name - a second run must not
        # overwrite the only good copy with an already-modified one.
        $backup = Join-Path $Profile ("prefs.js.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Copy-Item -LiteralPath $prefsPath -Destination $backup
        Ok "Backed up to $(Split-Path -Leaf $backup)"
        Set-Content -LiteralPath $prefsPath -Value $keep -Encoding UTF8
        Ok "prefs.js rewritten without the leaked prefs."
    }
}

# ------------------------------------------------------------------ #
Step "Installing user.js guard"
# Delegated to ensure-profile-guard.ps1 so there is exactly ONE implementation
# of "is this profile guarded" - the launchers run the same check on every
# start, and a second copy here would be the one that drifts.
$guardScript = Join-Path $PSScriptRoot 'ensure-profile-guard.ps1'
if (-not (Test-Path -LiteralPath $guardScript)) {
    Fail "ensure-profile-guard.ps1 not found next to this script."
    exit 1
}
if ($DryRun) {
    Warn "DryRun: would run ensure-profile-guard.ps1 against this profile."
} else {
    & powershell -ExecutionPolicy Bypass -NoProfile -File $guardScript -Profile $Profile
    if ($LASTEXITCODE -ne 0) {
        Fail "Guard installation failed - see above."
        exit 1
    }
}

# ------------------------------------------------------------------ #
Step "Done"
Write-Host "   Start Firefox normally. Verify in about:config:" -ForegroundColor White
Write-Host "     remote.prefs.recommended                -> false" -ForegroundColor White
Write-Host "     remote.prefs.recommended.applied        -> gone / default" -ForegroundColor White
Write-Host "     browser.safebrowsing.phishing.enabled   -> true (default)" -ForegroundColor White
Write-Host "     signon.rememberSignons                  -> true (default)" -ForegroundColor White
exit 0
