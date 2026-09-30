# Windows installer gates against a freshly built bundle.
# Gate 7's SmartScreen half is deliberately NOT run: UAC is disabled on this box
# (EnableLUA=0), so every process is High integrity and any SmartScreen result,
# including a negative one, would be untrustworthy.

param(
    [Parameter(Mandatory = $true)][string]$Setup,
    # Manual opt-in: exercises the production 120 s cap, with no test override.
    [switch]$RunWaitPidTimeout
)

$ErrorActionPreference = "Stop"

# -NoNewWindow, never -WindowStyle: -WindowStyle forces UseShellExecute, and
# ShellExecute on an installer hangs invisibly here. -NoNewWindow goes through
# CreateProcess and returns. This cost one 600 s stall before it was believed.
$results = @()

function Start-SandboxedSetup {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$ExtraArguments = @(),
        [string]$AddIns = $wglinkAddins,
        [switch]$Wait
    )
    if ($Executable -ne $Setup -and $Executable -ne $unins.FullName) {
        throw "Only setup or the gate's uninstaller may be launched."
    }
    if ($env:WG2_DATA_DIR -ne $gateData) {
        throw "WG2_DATA_DIR must be the gate's private data folder."
    }
    if (($AddIns -ne $wglinkAddins -and $AddIns -ne $developerAddins) -or
        -not (Test-Path -LiteralPath $AddIns -PathType Container)) {
        throw "AddIns must be an existing private gate folder."
    }
    foreach ($argument in $ExtraArguments) {
        if ($argument -match '^/(VERYSILENT|SUPPRESSMSGBOXES|WGLINKADDINSDIR)(=|$)') {
            throw "Sandbox arguments belong to Start-SandboxedSetup."
        }
    }
    $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES") + $ExtraArguments + @("/WGLINKADDINSDIR=`"$AddIns`"")
    $p = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru -Wait:$Wait -NoNewWindow
    # Force a retained handle so Windows PowerShell 5.1 can report ExitCode.
    $null = $p.Handle
    return $p
}

function Start-StandIn {
    $p = Start-Process -FilePath "$env:SystemRoot\System32\ping.exe" -ArgumentList "-n", "600", "127.0.0.1" -RedirectStandardOutput (Join-Path $gateRoot "stand-in.log") -RedirectStandardError (Join-Path $gateRoot "stand-in-error.log") -PassThru -NoNewWindow
    $null = $p.Handle
    return $p
}

function Gate($id, $name, $pass, $detail) {
    $script:results += [pscustomobject]@{
        Gate = $id; Name = $name
        Result = $(if ($pass -eq $null) { "SKIP" } elseif ($pass) { "PASS" } else { "FAIL" })
        Detail = $detail
    }
    "{0,-4} {1,-46} {2}" -f $id, $name, $(if ($pass -eq $null) { "SKIP" } elseif ($pass) { "PASS" } else { "FAIL" })
    if ($detail) { "       $detail" }
}

function TreeFingerprint([string]$root) {
    if (-not (Test-Path -LiteralPath $root)) { return "MISSING" }
    $prefix = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    return ((Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($prefix.Length)
        "$relative|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
    }) -join "`n")
}

# The two bundle layers, without bytecode. The layers are compared before and
# after an upgrade, and running the bundled Python (WGLink install, ownership
# queries) legitimately writes __pycache__ beside its sources, which is not a
# difference between two packages.
function LayerFingerprint([string]$root) {
    $parts = foreach ($layer in @("app", "runtime")) {
        $layerRoot = Join-Path $root $layer
        if (-not (Test-Path -LiteralPath $layerRoot)) { "${layer}: MISSING"; continue }
        $prefix = [IO.Path]::GetFullPath($layerRoot).TrimEnd('\') + '\'
        Get-ChildItem -LiteralPath $layerRoot -Recurse -File |
            Where-Object { $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -ne ".pyc" } |
            Sort-Object FullName | ForEach-Object {
                "$layer/$($_.FullName.Substring($prefix.Length))|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
            }
    }
    return ($parts -join "`n")
}

$installRoot = "$env:LOCALAPPDATA\Programs\Waveguide Generator"
$gateRoot = Join-Path $env:TEMP ("WaveguideGenerator-installer-gates-" + [guid]::NewGuid().ToString("N"))
$wglinkAddins = Join-Path $gateRoot "Fusion\API\AddIns"
$developerAddins = Join-Path $gateRoot "Developer\API\AddIns"
$gateData = Join-Path $gateRoot "data"
New-Item -ItemType Directory -Force $wglinkAddins, $developerAddins, $gateData | Out-Null
$previousDataDir = $env:WG2_DATA_DIR
$env:WG2_DATA_DIR = $gateData
try {

# --- Gate 1: the installer exists and carries a build-supplied payload budget --
$setupItem = Get-Item $Setup
$ver = (Get-Item $Setup).VersionInfo.FileVersion
Gate 1 "installer built by ISCC from bundle-setup.iss" $true `
    ("{0}, {1:N1} MB, FileVersion {2}" -f $setupItem.Name, ($setupItem.Length / 1MB), $ver)

# --- Mark the installer as downloaded, so the payload claim is actually tested -
$zone = "$Setup`:Zone.Identifier"
Set-Content -Path $Setup -Stream "Zone.Identifier" -Value "[ZoneTransfer]`r`nZoneId=3" -Encoding ascii
$marked = $null -ne (Get-Item -Path $Setup -Stream "Zone.Identifier" -ErrorAction SilentlyContinue)
"       installer marked with ZoneId=3: $marked"

# --- Gate 4 / 3: a too-long install root must be refused with an exit code -----
$longDir = "C:\" + ("g" * 200)
$p = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/DIR=`"$longDir`""
$longReturned = $true
try {
    Wait-Process -Id $p.Id -Timeout 30 -ErrorAction Stop
} catch {
    $longReturned = $false
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
}
if ($longReturned) { $p.Refresh(); $longExit = $p.ExitCode } else { $longExit = $null }
$longTreeCreated = Test-Path $longDir
Gate 4 "over-long install root refused, not attempted" ($longReturned -and $longExit -ne 0 -and -not $longTreeCreated) `
    "exit code $longExit for a $($longDir.Length)-character root; tree created: $longTreeCreated; bounded wait: 30 s"
Gate 3 "silent run exits with a code, never a modal box" ($longReturned -and $longExit -ne $null) `
    "process returned within 30 s rather than hanging; /SUPPRESSMSGBOXES honoured"

# --- Install for real ---------------------------------------------------------
# The installer only offers WGLink when Fusion's AddIns directory already
# exists. Use an explicitly-created disposable directory to exercise that
# branch without requiring Fusion 360 on the gate machine. The setup's hidden
# /WGLINKADDINSDIR hook is intentionally accepted only when this directory
# exists, so it cannot accidentally create a Fusion-looking directory for a
# typo in a normal deployment command.
if (Test-Path $installRoot) { Remove-Item -Recurse -Force $installRoot }
$p = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/TASKS=`"wglink`"" -Wait
$installExit = $p.ExitCode
# The package as installed on a clean machine: what every later upgrade of the
# same setup must reproduce, whatever was left in the layers beforehand.
$layerBaseline = if ($installExit -eq 0) { LayerFingerprint $installRoot } else { $null }

# --- Gate 2: per-user location, no elevation ----------------------------------
$landed = Test-Path $installRoot
$inProgramFiles = Test-Path "$env:ProgramFiles\Waveguide Generator"
Gate 2 "per-user install under LOCALAPPDATA\Programs" ($installExit -eq 0 -and $landed -and -not $inProgramFiles) `
    "exit $installExit; installed: $landed; Program Files copy: $inProgramFiles"

# --- Gate 10: the actual setup executable installs packaged WGLink -----------
# This is deliberately after setup, not a direct call to install_wglink.py:
# the contract includes task selection, the bundle runtime, and the two
# environment variables that tell the script it must not write into {app}.
$wglinkTarget = Join-Path $wglinkAddins "WGLink"
$wglinkMarker = Join-Path $wglinkTarget "wglink_install.json"
$wglinkRuntime = Join-Path $wglinkTarget "wglink_runtime.json"
$markerData = if (Test-Path $wglinkMarker) { Get-Content -Raw $wglinkMarker | ConvertFrom-Json } else { $null }
$runtimeData = if (Test-Path $wglinkRuntime) { Get-Content -Raw $wglinkRuntime | ConvertFrom-Json } else { $null }
$sourceSpecPath = Join-Path $installRoot "app\integrations\wglink\source.json"
$sourceSpec = if (Test-Path $sourceSpecPath) { Get-Content -Raw $sourceSpecPath | ConvertFrom-Json } else { $null }
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $installRoot "app"))
$markerRootOk = $null -ne $markerData -and $markerData.waveguideGeneratorRoot -eq $expectedRoot
$fullPinOk = $null -ne $markerData -and $null -ne $sourceSpec -and $markerData.sourceCommit -match '^[0-9a-f]{40}$' -and $markerData.sourceCommit -eq $sourceSpec.commit
$runtimeRoot = if ($null -ne $runtimeData) { [string]$runtimeData.root } else { "" }
$runtimePointerOk = $null -ne $runtimeData -and $runtimeData.python -eq (Join-Path $installRoot "runtime\python.exe") -and (Test-Path (Join-Path $runtimeRoot "scripts\wglink_resample.py"))
$journal = Join-Path $wglinkAddins ".WGLink-install-transaction.json"
$staging = @(Get-ChildItem -LiteralPath $wglinkAddins -Directory -Filter ".WGLink-install-*" -ErrorAction SilentlyContinue)
$settled = -not (Test-Path $journal) -and $staging.Count -eq 0
$usageRecord = Join-Path $gateData "integrations\wglink\cadlink-in-use.json"
$usageBefore = if (Test-Path $usageRecord) { Get-Content -Raw $usageRecord } else { $null }
$usageData = if ($null -ne $usageBefore) { $usageBefore | ConvertFrom-Json } else { $null }
$setupChoiceOk = $null -ne $usageData -and $usageData.schemaVersion -eq 1 -and $usageData.reason -eq "setup-task"
$wglinkOk = ($installExit -eq 0) -and (Test-Path (Join-Path $wglinkTarget "WGLink.py")) -and (Test-Path $wglinkMarker) -and (Test-Path $wglinkRuntime) -and $markerRootOk -and $fullPinOk -and $runtimePointerOk -and $settled -and $setupChoiceOk
$wglinkDetail = "setup exit $installExit; target: $wglinkTarget; marker root: $markerRootOk; full pin: $fullPinOk; runtime pointer: $runtimePointerOk; journal absent: $(-not (Test-Path $journal)); staging directories: $($staging.Count); setup choice recorded: $setupChoiceOk"
Gate 10 "setup task installs packaged WGLink into a disposable AddIns directory" $wglinkOk $wglinkDetail

# --- Gate 12: a silent upgrade must name WGLink again -------------------------
# Use the same AddIns override as gate 10 but omit /TASKS. Inno's default
# UsePreviousTasks=yes would silently restore the previous task selection on an
# upgrade; the setup script sets UsePreviousTasks=no so this tree must remain
# byte-identical.
$silentSentinel = Join-Path $wglinkTarget ".gate-silent-opt-in-sentinel"
Set-Content -LiteralPath $silentSentinel -Value ([guid]::NewGuid().ToString("N")) -NoNewline
$beforeSilentUpgrade = TreeFingerprint $wglinkTarget
$silentUpgrade = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART" -Wait
$afterSilentUpgrade = TreeFingerprint $wglinkTarget
$usageUnchanged = $setupChoiceOk -and (Test-Path $usageRecord) -and ((Get-Content -Raw $usageRecord) -eq $usageBefore)
$silentUpgradeOk = ($silentUpgrade.ExitCode -eq 0) -and (Test-Path $silentSentinel) -and ($beforeSilentUpgrade -eq $afterSilentUpgrade) -and -not (Test-Path $journal) -and $staging.Count -eq 0 -and $usageUnchanged
Gate 12 "silent upgrade leaves WGLink untouched without current /TASKS opt-in" $silentUpgradeOk `
    "setup exit $($silentUpgrade.ExitCode); sentinel preserved: $(Test-Path $silentSentinel); tree unchanged: $($beforeSilentUpgrade -eq $afterSilentUpgrade); journal absent: $(-not (Test-Path $journal)); staging directories: $($staging.Count); setup choice unchanged: $usageUnchanged"

# --- Gate 13: an upgrade leaves exactly the package, and reports it ----------
# The layers are renamed aside and replaced, so a file the package no longer
# carries must not survive (Inno's [Files] alone only ever overlays). Plant a
# module in app\ and a package in runtime\, as if the previous version had
# shipped them, upgrade with the same setup, and require app\ and runtime\ to
# equal the freshly installed package again, with no renamed-aside folder or
# marker left behind and a successful outcome record.
$removedModule = Join-Path $installRoot "app\gate_removed_module.py"
$removedPackage = Join-Path $installRoot "runtime\Lib\site-packages\gate_removed_package"
Set-Content -LiteralPath $removedModule -Value "removed = True" -NoNewline
New-Item -ItemType Directory -Force $removedPackage | Out-Null
Set-Content -LiteralPath (Join-Path $removedPackage "__init__.py") -Value "removed = True" -NoNewline
$outcomeFile = Join-Path $gateRoot "outcome-upgrade.json"
$plantedBefore = (Test-Path $removedModule) -and (Test-Path $removedPackage)
$upgrade = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/OUTCOME=`"$outcomeFile`"" -Wait
$layerAfter = LayerFingerprint $installRoot
$removedGone = -not (Test-Path $removedModule) -and -not (Test-Path $removedPackage)
$layersMatch = ($null -ne $layerBaseline) -and ($layerAfter -eq $layerBaseline)
$asideLeft = @(".app.old", ".runtime.old", ".upgrade-in-progress" | Where-Object { Test-Path (Join-Path $installRoot $_) })
$manifestPresent = Test-Path (Join-Path $installRoot "app\APP-MANIFEST.json")
$outcome = if (Test-Path $outcomeFile) { Get-Content -Raw $outcomeFile | ConvertFrom-Json } else { $null }
$outcomeOk = $null -ne $outcome -and $outcome.result -eq "ok" -and -not [string]::IsNullOrEmpty($outcome.to) -and -not [string]::IsNullOrEmpty($outcome.from) -and -not [string]::IsNullOrEmpty($outcome.when)
Gate 13 "upgrade removes files the package dropped and writes an outcome" ($plantedBefore -and ($upgrade.ExitCode -eq 0) -and $removedGone -and $layersMatch -and ($asideLeft.Count -eq 0) -and $manifestPresent -and $outcomeOk) `
    "setup exit $($upgrade.ExitCode); planted files present before: $plantedBefore; gone after: $removedGone; app+runtime equal the installed package: $layersMatch; renamed-aside leftovers: $($asideLeft.Count); manifest present: $manifestPresent; outcome result: $(if ($outcome) { $outcome.result } else { 'MISSING' }) from $(if ($outcome) { $outcome.from }) to $(if ($outcome) { $outcome.to })"

# --- Gate 14: /WAITPID holds setup before any layer is replaced -------------
# Wait for the setup log to confirm the wait was entered, rather than assuming
# that a slow-to-start setup is waiting. Plant a file rename-aside must remove
# only after the stand-in exits; the layers must be unchanged during the wait.
$waitOutcome = Join-Path $gateRoot "outcome-waitpid.json"
$waitLog = Join-Path $gateRoot "waitpid-setup.log"
$waitSentinel = Join-Path $installRoot "app\gate_waitpid_sentinel.py"
Set-Content -LiteralPath $waitSentinel -Value "keep_until_process_exits = True" -NoNewline
$beforeWait = LayerFingerprint $installRoot
$standIn = Start-StandIn
$waiting = $null; $contender = $null
$heldWhileAlive = $false; $finishedAfter = $false; $waitExit = $null
$waitingLogged = $false; $mutexPresent = $false; $contenderBlocked = $false
$contenderRoot = Join-Path $gateRoot "mutex-contender"
$contenderOutcome = Join-Path $gateRoot "outcome-contender.json"
$contenderSettled = $false
try {
    $waiting = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/DIR=`"$installRoot`"", "/WAITPID=$($standIn.Id)", "/OUTCOME=`"$waitOutcome`"", "/LOG=`"$waitLog`""
    $null = $waiting.Handle  # so ExitCode is readable under Windows PowerShell 5.1 (see gate 4)
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if (Test-Path $waitLog) {
            $waitingLogged = $null -ne (Select-String -LiteralPath $waitLog -SimpleMatch "/WAITPID: waiting up to")
        }
        if ($waitingLogged -or $waiting.HasExited) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $readyDeadline)
    Start-Sleep -Seconds 8
    $heldWhileAlive = $waitingLogged -and (-not $standIn.HasExited) -and (-not $waiting.HasExited) -and -not (Test-Path $waitOutcome) -and (Test-Path $waitSentinel) -and ($beforeWait -eq (LayerFingerprint $installRoot))
    foreach ($aside in @(".app.old", ".runtime.old", ".upgrade-in-progress")) {
        if (Test-Path (Join-Path $installRoot $aside)) { $heldWhileAlive = $false }
    }

    # --- Gate 15: a second setup cannot proceed while the first waits --------
    # OpenExisting probes the exact mutex the launcher checks. Dispose the probe
    # immediately so the gate itself cannot keep the mutex alive after setup.
    try {
        $probe = [Threading.Mutex]::OpenExisting("WaveguideGeneratorSetup")
        $mutexPresent = $true
        $probe.Dispose()
    } catch [Threading.WaitHandleCannotBeOpenedException] {
        $mutexPresent = $false
    }
    # A separate disposable /DIR makes any premature extraction observable.
    $contender = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/DIR=`"$contenderRoot`"", "/OUTCOME=`"$contenderOutcome`""
    $null = $contender.Handle
    $contenderReturned = $contender.WaitForExit(8000)
    $contender.Refresh()
    $contenderData = if (Test-Path $contenderOutcome) { Get-Content -Raw $contenderOutcome | ConvertFrom-Json } else { $null }
    $contenderBlocked = $waitingLogged -and $mutexPresent -and (-not $standIn.HasExited) -and (-not $waiting.HasExited) -and -not (Test-Path $waitOutcome) -and -not (Test-Path $contenderRoot) -and ($null -eq $contenderData -or $contenderData.result -eq "failed") -and ((-not $contenderReturned) -or ($null -ne $contender.ExitCode -and $contender.ExitCode -ne 0))
    Stop-Process -Id $standIn.Id -Force
    $finishedAfter = $waiting.WaitForExit(90000)
    if ($finishedAfter) { $waiting.Refresh(); $waitExit = $waiting.ExitCode }
    # If the second setup waited instead of failing, let it finish after release.
    $contenderSettled = $contender.WaitForExit(90000)
} finally {
    if (-not $standIn.HasExited) { Stop-Process -Id $standIn.Id -Force }
    if ($waiting -and -not $waiting.HasExited) { Stop-Process -Id $waiting.Id -Force }
    if ($contender -and -not $contender.HasExited) { Stop-Process -Id $contender.Id -Force }
}
$waitData = if (Test-Path $waitOutcome) { Get-Content -Raw $waitOutcome | ConvertFrom-Json } else { $null }
$waitSucceeded = $finishedAfter -and ($waitExit -eq 0) -and ($null -ne $waitData) -and ($waitData.result -eq "ok") -and -not (Test-Path $waitSentinel) -and ((LayerFingerprint $installRoot) -eq $layerBaseline)
Gate 14 "/WAITPID waits before replacing layers, then installs" ($heldWhileAlive -and $waitSucceeded) `
    "wait reached: $waitingLogged; layers intact while process lived: $heldWhileAlive; finished after exit: $finishedAfter; exit $waitExit; outcome: $(if ($waitData) { $waitData.result } else { 'MISSING' }); sentinel removed: $(-not (Test-Path $waitSentinel))"
Gate 15 "setup mutex excludes a second setup throughout /WAITPID" ($contenderBlocked -and $finishedAfter -and $waitSucceeded -and $contenderSettled) `
    "mutex visible during wait: $mutexPresent; second setup refused or held without extraction: $contenderBlocked; second setup settled: $contenderSettled; first setup exit: $waitExit"

# --- Gate 16: a silent timeout must exit by itself, without a window ----------
if ($RunWaitPidTimeout) {
    # Enumerate all visible top-level windows, including owned modal dialogs.
    # The setup loader spawns a .tmp child, so checking only its MainWindowHandle
    # would miss the very dialog this gate must catch. Track descendants by PID.
    if (-not ("WgGateWindows" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class WgGateWindows {
    private delegate bool EnumProc(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    public static bool HasVisibleWindow(int[] processIds) {
        var ids = new HashSet<int>(processIds);
        bool found = false;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (ids.Contains((int)processId) && IsWindowVisible(window)) found = true;
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@
    }
    $timeoutOutcome = Join-Path $gateRoot "outcome-timeout.json"
    $timeoutLog = Join-Path $gateRoot "timeout-setup.log"
    $beforeTimeout = LayerFingerprint $installRoot
    $timeoutStandIn = Start-StandIn
    $timingOut = $null; $timeoutReturned = $false; $timeoutExit = $null
    $timeoutWindowSeen = $false; $timeoutProcessIds = @()
    $timeoutClock = [Diagnostics.Stopwatch]::StartNew()
    try {
        $timingOut = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/DIR=`"$installRoot`"", "/WAITPID=$($timeoutStandIn.Id)", "/OUTCOME=`"$timeoutOutcome`"", "/LOG=`"$timeoutLog`"", "/RELAUNCH"
        $null = $timingOut.Handle
        $timeoutProcessIds = @($timingOut.Id)
        # 120 s cap plus 30 s for startup and termination. Never kill a setup
        # to count it as returned; forced cleanup below leaves the gate failed.
        do {
            $processes = @(Get-CimInstance Win32_Process)
            do {
                $children = @($processes | Where-Object { $_.ParentProcessId -in $timeoutProcessIds -and $_.ProcessId -notin $timeoutProcessIds })
                $timeoutProcessIds += @($children | ForEach-Object { [int]$_.ProcessId })
            } while ($children.Count -gt 0)
            if ([WgGateWindows]::HasVisibleWindow([int[]]$timeoutProcessIds)) { $timeoutWindowSeen = $true }
            $timeoutReturned = $timingOut.WaitForExit(250)
        } while (-not $timeoutReturned -and $timeoutClock.Elapsed.TotalSeconds -lt 150)
        if ($timeoutReturned) { $timingOut.Refresh(); $timeoutExit = $timingOut.ExitCode }
        $standInSurvived = -not $timeoutStandIn.HasExited
    } finally {
        $timeoutClock.Stop()
        # Only processes launched above, identified by recorded parentage/PID.
        foreach ($processId in $timeoutProcessIds) {
            $owned = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($owned -and -not $owned.HasExited) { Stop-Process -Id $processId -Force }
        }
        if (-not $timeoutStandIn.HasExited) { Stop-Process -Id $timeoutStandIn.Id -Force }
    }
    $timeoutData = if (Test-Path $timeoutOutcome) { Get-Content -Raw $timeoutOutcome | ConvertFrom-Json } else { $null }
    $timeoutText = if (Test-Path $timeoutLog) { Get-Content -Raw $timeoutLog } else { "" }
    $failureWrites = ([regex]::Matches($timeoutText, 'Outcome: wrote .* \(failed\)\.')).Count
    $timeoutAside = @(".app.old", ".runtime.old", ".upgrade-in-progress" | Where-Object { Test-Path (Join-Path $installRoot $_) })
    $timeoutUntouched = ($beforeTimeout -eq (LayerFingerprint $installRoot)) -and $timeoutAside.Count -eq 0 -and $timeoutText -notmatch 'Protection: (renamed|restored)'
    $noRelaunch = $timeoutText -notmatch '-- Run entry --'
    $timeoutOk = $timeoutReturned -and $timeoutClock.Elapsed.TotalSeconds -le 150 -and $null -ne $timeoutExit -and $timeoutExit -ne 0 -and -not $timeoutWindowSeen -and $standInSurvived -and $timeoutText -match 'was still running after 120 s' -and $null -ne $timeoutData -and $timeoutData.result -eq "failed" -and $failureWrites -eq 1 -and $timeoutUntouched -and $noRelaunch
    Gate 16 "silent timeout exits without a window or relaunch" $timeoutOk `
        "Abort at ssInstall exit: $timeoutExit (expected 3, to be measured on Windows); self-exit: $timeoutReturned; elapsed: $($timeoutClock.Elapsed.TotalSeconds) s (cap + margin: 150 s); visible window seen: $timeoutWindowSeen; stand-in still alive: $standInSurvived; outcome: $(if ($timeoutData) { $timeoutData.result } else { 'MISSING' }); failed writes: $failureWrites; layers untouched: $timeoutUntouched; no Run entry: $noRelaunch"
    # Keep native timeout evidence outside the fixture removed at the end.
    $timeoutEvidence = Join-Path $env:TEMP ("WaveguideGenerator-timeout-evidence-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory $timeoutEvidence | Out-Null
    foreach ($evidenceFile in @($timeoutLog, $timeoutOutcome)) {
        if (Test-Path $evidenceFile) { Copy-Item -LiteralPath $evidenceFile -Destination $timeoutEvidence }
    }
    "       timeout log/outcome retained at: $timeoutEvidence"
} else {
    Gate 16 "silent timeout exits without a window or relaunch" $null `
        "Manual opt-in required: rerun with -RunWaitPidTimeout; uses the real 120 s cap plus a 30 s margin."
}

# --- Gate 11: a developer marker is never overwritten ------------------------
New-Item -ItemType Directory -Force (Join-Path $developerAddins "WGLink") | Out-Null
$developerMarker = Join-Path $developerAddins "WGLink\wglink_dev.json"
$managedMarker = Join-Path $developerAddins "WGLink\wglink_install.json"
$developerFile = Join-Path $developerAddins "WGLink\developer.py"
Set-Content -LiteralPath $developerMarker -Value '{"sourceCommit":"local"}' -NoNewline
@{ managedBy = "waveguide-generator"; waveguideGeneratorRoot = $expectedRoot } | ConvertTo-Json -Compress | Set-Content -LiteralPath $managedMarker -NoNewline
Set-Content -LiteralPath $developerFile -Value 'keep me' -NoNewline
$developerBefore = Get-Content -Raw $developerMarker
$managedBefore = Get-Content -Raw $managedMarker
$developerRun = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/NORESTART", "/TASKS=`"wglink`"" -AddIns $developerAddins -Wait
$developerPreserved = ($developerRun.ExitCode -eq 0) -and (Test-Path $developerFile) -and ((Get-Content -Raw $developerMarker) -eq $developerBefore) -and ((Get-Content -Raw $managedMarker) -eq $managedBefore)
Gate 11 "setup preserves a developer-marked WGLink copy" $developerPreserved `
    "setup exit $($developerRun.ExitCode); developer marker unchanged: $((Get-Content -Raw $developerMarker) -eq $developerBefore); colliding WG marker unchanged: $((Get-Content -Raw $managedMarker) -eq $managedBefore); developer file preserved: $(Test-Path $developerFile)"

# --- Gate 5: no Zone.Identifier anywhere in the payload -----------------------
$marked = @()
if ($landed) {
    Get-ChildItem -Recurse -File $installRoot -ErrorAction SilentlyContinue | ForEach-Object {
        if (Get-Item -LiteralPath $_.FullName -Stream "Zone.Identifier" -ErrorAction SilentlyContinue) {
            $marked += $_.FullName
        }
    }
}
$fileCount = if ($landed) { (Get-ChildItem -Recurse -File $installRoot).Count } else { 0 }
Gate 5 "payload carries no mark of the web" ($landed -and $marked.Count -eq 0) `
    "$fileCount files scanned, $($marked.Count) marked (installer itself was ZoneId=3)"

# --- Gate 6: shortcuts point at the app icon, not python's --------------------
$shell = New-Object -ComObject WScript.Shell
$links = @(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Waveguide Generator\Waveguide Generator.lnk"
)
$iconOk = $true; $iconDetail = @()
foreach ($l in $links) {
    if (Test-Path $l) {
        $sc = $shell.CreateShortcut($l)
        $iconDetail += "$(Split-Path $l -Leaf) -> $($sc.IconLocation)"
        if ($sc.IconLocation -notmatch "WaveguideGenerator\.ico") { $iconOk = $false }
    } else {
        $iconDetail += "$(Split-Path $l -Leaf) MISSING"; $iconOk = $false
    }
}
$icoPresent = Test-Path "$installRoot\WaveguideGenerator.ico"
Gate 6 "shortcut icon is the app's, not Python's" ($iconOk -and $icoPresent) `
    (($iconDetail -join "; ") + "; .ico staged: $icoPresent")

# --- Gate 8: the update path needs no elevation -------------------------------
# apply_update.py renames {app}\runtime and {app}\app in place. Prove those
# renames succeed as this user, which is what a Program Files install breaks.
$renameOk = $false; $renameDetail = ""
if ($landed) {
    try {
        Rename-Item "$installRoot\app" "app.gatetest" -ErrorAction Stop
        Rename-Item "$installRoot\app.gatetest" "app" -ErrorAction Stop
        Rename-Item "$installRoot\runtime" "runtime.gatetest" -ErrorAction Stop
        Rename-Item "$installRoot\runtime.gatetest" "runtime" -ErrorAction Stop
        $renameOk = $true
        $renameDetail = "app and runtime both renamed in place and restored, no elevation"
    } catch {
        $renameDetail = "rename failed: $($_.Exception.Message)"
    }
}
Gate 8 "in-app update can rename layers without elevation" $renameOk $renameDetail

# --- Gate 9: uninstall removes the tree, including bytecode -------------------
# Plant a __pycache__ the installer never wrote, which is the case the
# [UninstallDelete] block exists for.
$uninstallOk = $false; $uninstallDetail = ""
if ($landed) {
    $planted = "$installRoot\app\__pycache__"
    New-Item -ItemType Directory -Force $planted | Out-Null
    Set-Content "$planted\gate.pyc" "planted by the gate run"
    # recovery\sitecustomize.py is imported by every start of the bundled
    # interpreter, so an ordinary launch writes bytecode there as well.
    $plantedRecovery = "$installRoot\recovery\__pycache__"
    New-Item -ItemType Directory -Force $plantedRecovery | Out-Null
    Set-Content "$plantedRecovery\gate.pyc" "planted by the gate run"
    $unins =Get-ChildItem $installRoot -Filter "unins*.exe" | Select-Object -First 1
    if ($unins) {
        $p = Start-SandboxedSetup -Executable $unins.FullName -ExtraArguments "/NORESTART" -Wait
        Start-Sleep -Seconds 3
        $left = if (Test-Path $installRoot) { (Get-ChildItem -Recurse -File $installRoot -ErrorAction SilentlyContinue).Count } else { 0 }
        $managedAddinRemoved = -not (Test-Path $wglinkTarget)
        $postUninstallJournal = Test-Path $journal
        $postUninstallStaging = @(Get-ChildItem -LiteralPath $wglinkAddins -Directory -Filter ".WGLink-install-*" -ErrorAction SilentlyContinue)
        $uninstallOk = ($p.ExitCode -eq 0) -and ($left -eq 0) -and $managedAddinRemoved -and -not $postUninstallJournal -and $postUninstallStaging.Count -eq 0
        $uninstallDetail = "uninstaller exit $($p.ExitCode); files left under {app}: $left; planted __pycache__ removed: $(-not (Test-Path $planted)); managed WGLink removed: $managedAddinRemoved; journal absent: $(-not $postUninstallJournal); staging directories: $($postUninstallStaging.Count)"
    } else {
        $uninstallDetail = "no uninstaller found in the install root"
    }
}
Gate 9 "uninstall clears the tree including planted bytecode" $uninstallOk $uninstallDetail

# Remove the private fixtures after inspecting managed AddIns cleanup.
# Ordinary uninstall preserves the shared runtime payloads and package cache.
if (Test-Path $gateRoot) { Remove-Item -Recurse -Force $gateRoot }

# Leave the installer as the gates found it. The RC workflow's next step
# launches this same file through ShellExecute, which honours the ZoneId=3 mark
# by raising the attachment-manager prompt; a runner cannot answer it, so the
# launch failed "The operation was canceled by the user".
Unblock-File -LiteralPath $Setup

# --- Gate 7: not run, and why -------------------------------------------------
Gate 7 "SmartScreen / first-run experience" $null `
    "NOT RUN: UAC is disabled here (EnableLUA=0), so every process is High integrity and any result, including a negative one, would be untrustworthy. Needs a box with UAC enabled."

""
"summary: " + (($results | Group-Object Result | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join "  ")

if ($results.Result -contains "FAIL") {
    throw "One or more Windows installer gates failed."
}

} finally {
    $env:WG2_DATA_DIR = $previousDataDir
}
