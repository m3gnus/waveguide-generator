[CmdletBinding()]
param(
    [string] $CompilerPath,
    [string] $ExpectedVersion = "6.7.1"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($CompilerPath)) {
    # Do not recurse through the whole runner image. The Chocolatey package's
    # documented install locations are stable, and an unbounded search made a
    # missing (x86) environment lookup take minutes before failing.
    $roots = @(
        [Environment]::GetEnvironmentVariable("ProgramFiles(x86)"),
        [Environment]::GetEnvironmentVariable("ProgramFiles")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    foreach ($root in $roots) {
        $candidate = Join-Path -Path $root -ChildPath "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $CompilerPath = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($CompilerPath) -or
    -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
    throw "Inno Setup installed but ISCC.exe was not found in the Program Files locations."
}

# ISCC /? prints usage, not the compiler patch version. Some Inno Setup 6.7.1
# builds also report 0.0.0.0 in the ISCC.exe PE FileVersion resource, so keep
# that value as diagnostic context and obtain the exact version from the
# compiler engine while running the real compile probe below.
$fileVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($CompilerPath).FileVersion

# Compile a tiny valid script as a success-producing execution probe. The
# compiler's normal output contains "Compiler engine version: ..."; this is
# the version API exposed by the Inno compiler that will build the installer.
# `ISCC /?` prints usage and leaves a non-zero native exit code on Inno 6.7.1,
# while merely checking nonempty output would let an unrelated executable pass.
$probeParent = $env:RUNNER_TEMP
if ([string]::IsNullOrWhiteSpace($probeParent)) {
    $probeParent = [IO.Path]::GetTempPath()
}
$probeRoot = Join-Path $probeParent ("inno-probe-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $probeRoot | Out-Null
try {
    $probeScript = Join-Path $probeRoot "probe.iss"
    $probeOutput = Join-Path $probeRoot "output"
    New-Item -ItemType Directory -Path $probeOutput | Out-Null
    @"
[Setup]
AppName=HornLab Inno Setup probe
AppVersion=1.0.0
DefaultDirName={autopf}\HornLabInnoSetupProbe
Uninstallable=no
OutputDir="$probeOutput"
OutputBaseFilename=probe
"@ | Set-Content -LiteralPath $probeScript -Encoding utf8

    $probeOutputText = (& $CompilerPath $probeScript 2>&1 | Out-String)
    $probeExitCode = $LASTEXITCODE
    if ($probeExitCode -ne 0) {
        throw "The Inno Setup compiler probe exited $probeExitCode`: $($probeOutputText.Trim())"
    }
    $versionMatch = [regex]::Match(
        [string] $probeOutputText,
        '(?m)^\s*Compiler engine version:\s+.*?(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?:\.\d+)?\s*$'
    )
    if (-not $versionMatch.Success) {
        throw "The Inno Setup compiler probe did not report an engine version: $($probeOutputText.Trim())"
    }
    $actualVersion = "{0}.{1}.{2}" -f `
        $versionMatch.Groups["major"].Value,
        $versionMatch.Groups["minor"].Value,
        $versionMatch.Groups["patch"].Value
    if ($actualVersion -ne $ExpectedVersion) {
        throw "Expected the Inno Setup $ExpectedVersion compiler, got engine version $actualVersion (PE file version $fileVersion) at $CompilerPath"
    }
    $probeExecutable = Join-Path $probeOutput "probe.exe"
    if (-not (Test-Path -LiteralPath $probeExecutable -PathType Leaf)) {
        throw "The Inno Setup compiler probe exited successfully but produced no probe.exe."
    }
}
finally {
    Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Verified Inno Setup compiler $actualVersion (PE file version $fileVersion) at $CompilerPath"
[string] $CompilerPath
