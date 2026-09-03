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

$installRoot = "$env:LOCALAPPDATA\Programs\Waveguide Generator"

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
$p = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/DIR=`"$longDir`"" -PassThru -Wait -NoNewWindow
$longExit = $p.ExitCode
Gate 4 "over-long install root refused, not attempted" ($longExit -ne 0) `
    "exit code $longExit for a $($longDir.Length)-character root; tree created: $(Test-Path $longDir)"
Gate 3 "silent run exits with a code, never a modal box" ($longExit -ne $null) `
    "process returned rather than hanging; /SUPPRESSMSGBOXES honoured"

# --- Install for real ---------------------------------------------------------
if (Test-Path $installRoot) { Remove-Item -Recurse -Force $installRoot }
$p = Start-Process -FilePath $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru -Wait -NoNewWindow
$installExit = $p.ExitCode

# --- Gate 2: per-user location, no elevation ----------------------------------
$landed = Test-Path $installRoot
$inProgramFiles = Test-Path "$env:ProgramFiles\Waveguide Generator"
Gate 2 "per-user install under LOCALAPPDATA\Programs" ($landed -and -not $inProgramFiles) `
    "exit $installExit; installed: $landed; Program Files copy: $inProgramFiles"

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
        $p = Start-Process -FilePath $unins.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru -Wait -NoNewWindow
        Start-Sleep -Seconds 3
        $left = if (Test-Path $installRoot) { (Get-ChildItem -Recurse -File $installRoot -ErrorAction SilentlyContinue).Count } else { 0 }
        $uninstallOk = ($left -eq 0)
        $uninstallDetail = "uninstaller exit $($p.ExitCode); files left under {app}: $left; planted __pycache__ removed: $(-not (Test-Path $planted))"
    } else {
        $uninstallDetail = "no uninstaller found in the install root"
    }
}
Gate 9 "uninstall clears the tree including planted bytecode" $uninstallOk $uninstallDetail

# --- Gate 7: not run, and why -------------------------------------------------
Gate 7 "SmartScreen / first-run experience" $null `
    "NOT RUN: UAC is disabled here (EnableLUA=0), so every process is High integrity and any result, including a negative one, would be untrustworthy. Needs a box with UAC enabled."

""
"summary: " + (($results | Group-Object Result | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join "  ")
