# Gate 7 preparation: SmartScreen and the real first-run experience.
#
# This script does NOT decide gate 7. It verifies the box is capable of
# deciding it, stamps the installer with mark-of-the-web, and then stops.
# The gate itself is decided by a human looking at the screen, because the
# thing under test is a shell dialog and any automated launch path either
# bypasses it (CreateProcess) or blocks invisibly on it (ShellExecute).
#
# Usage:  .\gate7-prepare.ps1 -Setup path\to\Waveguide.Generator-<ver>-windows-x86_64-setup.exe

param([Parameter(Mandatory)][string]$Setup)

$fail = $false

# --- Precondition 1: UAC must be on, or every result is untrustworthy ------
$lua = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System').EnableLUA
if ($lua -ne 1) {
    Write-Output "BLOCKED  EnableLUA=$lua. Gate 7 cannot be decided here. Enable UAC and reboot."
    $fail = $true
} else {
    Write-Output "ok       EnableLUA=1"
}

# --- Precondition 2: this process must not be elevated ---------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$elevated = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if ($elevated) {
    Write-Output "BLOCKED  running elevated. A first-time user is not. Re-run from a normal shell."
    $fail = $true
} else {
    Write-Output "ok       not elevated"
}

# --- Precondition 3: SmartScreen must not be disabled by policy ------------
$policyBad = $false
foreach ($p in 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System',
               'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer') {
    if (Test-Path $p) {
        $v = Get-ItemProperty $p
        if ($null -ne $v.EnableSmartScreen -and $v.EnableSmartScreen -eq 0) {
            Write-Output "BLOCKED  SmartScreen disabled by policy at $p"
            $policyBad = $true
        }
        if ($v.SmartScreenEnabled -eq 'Off') {
            Write-Output "BLOCKED  SmartScreenEnabled=Off at $p"
            $policyBad = $true
        }
    }
}
# Reported on its own, not gated on the earlier checks: a missing line here
# would read as "not checked", which is the wrong thing to infer.
if ($policyBad) { $fail = $true } else { Write-Output "ok       no SmartScreen policy override" }

# --- Precondition 4: the installer exists ----------------------------------
if (-not (Test-Path $Setup)) {
    Write-Output "BLOCKED  no such file: $Setup"
    $fail = $true
} else {
    $f = Get-Item $Setup
    Write-Output "ok       $($f.Name)  $([math]::Round($f.Length/1MB,1)) MiB"
}

if ($fail) { Write-Output ""; Write-Output "Gate 7 NOT READY."; exit 1 }

# --- Stamp mark-of-the-web -------------------------------------------------
# Without ZoneId=3 the file looks locally-authored and SmartScreen never
# fires. A gate 7 run on an unmarked file is vacuous in exactly the same way
# a run on a UAC-disabled box is.
$ads = "$((Get-Item $Setup).FullName):Zone.Identifier"
Set-Content -Path $ads -Value "[ZoneTransfer]`r`nZoneId=3" -Encoding ascii
Write-Output "ok       stamped ZoneId=3 (mark-of-the-web)"
Write-Output ""
Write-Output "READY. Now decide the gate by hand:"
Write-Output ""
Write-Output "  1. Open Explorer at the installer and DOUBLE-CLICK it."
Write-Output "     Do not launch it from a shell. Start-Process -NoNewWindow uses"
Write-Output "     CreateProcess, which skips the SmartScreen shell dialog entirely"
Write-Output "     and would hand you a false pass."
Write-Output "  2. Record, verbatim, what appears before any installer UI:"
Write-Output "       - a full-screen 'Windows protected your PC' block?"
Write-Output "       - an 'unknown publisher' UAC prompt?"
Write-Output "       - nothing at all?"
Write-Output "  3. Screenshot each dialog. The screenshot IS the evidence."
Write-Output "  4. Count the clicks from double-click to the first installer page."
