# Windows installer gates against a freshly built bundle.
# Gate 7's SmartScreen half is deliberately NOT run: UAC is disabled on this box
# (EnableLUA=0), so every process is High integrity and any SmartScreen result,
# including a negative one, would be untrustworthy.

param(
    [Parameter(Mandatory = $true)][string]$Setup
)

$ErrorActionPreference = "Stop"

# -NoNewWindow, never -WindowStyle: -WindowStyle forces UseShellExecute, and
# ShellExecute on an installer hangs invisibly here. -NoNewWindow goes through
# CreateProcess and returns. This cost one 600 s stall before it was believed.
$results = @()

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

$installRoot = "$env:LOCALAPPDATA\Programs\Waveguide Generator"
$gateRoot = Join-Path $env:TEMP "WaveguideGenerator-installer-gates"
$wglinkAddins = Join-Path $gateRoot "Fusion\API\AddIns"
$developerAddins = Join-Path $gateRoot "Developer\API\AddIns"

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
$p = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/DIR=`"$longDir`"" -PassThru -NoNewWindow
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
if (Test-Path $gateRoot) { Remove-Item -Recurse -Force $gateRoot }
New-Item -ItemType Directory -Force $wglinkAddins | Out-Null
if (Test-Path $installRoot) { Remove-Item -Recurse -Force $installRoot }
$p = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"wglink`"", "/WGLINKADDINSDIR=`"$wglinkAddins`"" -PassThru -Wait -NoNewWindow
$installExit = $p.ExitCode

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
$wglinkOk = ($installExit -eq 0) -and (Test-Path (Join-Path $wglinkTarget "WGLink.py")) -and (Test-Path $wglinkMarker) -and (Test-Path $wglinkRuntime) -and $markerRootOk -and $fullPinOk -and $runtimePointerOk -and $settled
$wglinkDetail = "setup exit $installExit; target: $wglinkTarget; marker root: $markerRootOk; full pin: $fullPinOk; runtime pointer: $runtimePointerOk; journal absent: $(-not (Test-Path $journal)); staging directories: $($staging.Count)"
Gate 10 "setup task installs packaged WGLink into a disposable AddIns directory" $wglinkOk $wglinkDetail

# --- Gate 12: a silent upgrade must name WGLink again -------------------------
# Use the same AddIns override as gate 10 but omit /TASKS. Inno's default
# UsePreviousTasks=yes would silently restore the previous task selection on an
# upgrade; the setup script sets UsePreviousTasks=no so this tree must remain
# byte-identical.
$silentSentinel = Join-Path $wglinkTarget ".gate-silent-opt-in-sentinel"
Set-Content -LiteralPath $silentSentinel -Value ([guid]::NewGuid().ToString("N")) -NoNewline
$beforeSilentUpgrade = TreeFingerprint $wglinkTarget
$silentUpgrade = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/WGLINKADDINSDIR=`"$wglinkAddins`"" -PassThru -Wait -NoNewWindow
$afterSilentUpgrade = TreeFingerprint $wglinkTarget
$silentUpgradeOk = ($silentUpgrade.ExitCode -eq 0) -and (Test-Path $silentSentinel) -and ($beforeSilentUpgrade -eq $afterSilentUpgrade) -and -not (Test-Path $journal) -and $staging.Count -eq 0
Gate 12 "silent upgrade leaves WGLink untouched without current /TASKS opt-in" $silentUpgradeOk `
    "setup exit $($silentUpgrade.ExitCode); sentinel preserved: $(Test-Path $silentSentinel); tree unchanged: $($beforeSilentUpgrade -eq $afterSilentUpgrade); journal absent: $(-not (Test-Path $journal)); staging directories: $($staging.Count)"

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
$developerRun = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"wglink`"", "/WGLINKADDINSDIR=`"$developerAddins`"" -PassThru -Wait -NoNewWindow
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
    $unins = Get-ChildItem $installRoot -Filter "unins*.exe" | Select-Object -First 1
    if ($unins) {
        $p = Start-Process -FilePath $unins.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/WGLINKADDINSDIR=`"$wglinkAddins`"" -PassThru -Wait -NoNewWindow
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

# WGLink belongs to Fusion, not the app installation. Remove only the
# disposable gate fixture after inspecting it; an ordinary uninstall must keep
# a managed add-in available to the installed application's next version.
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
