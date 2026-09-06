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

# ISCC /? prints usage, not the compiler patch version. Read the PE version
# resource from the executable that will actually compile the installer.
$fileVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($CompilerPath).FileVersion
$versionMatch = [regex]::Match(
    [string] $fileVersion,
    '^\s*(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?:\.\d+)?\s*$'
)
if (-not $versionMatch.Success) {
    throw "Inno Setup compiler at $CompilerPath has no usable PE file version: $fileVersion"
}
$actualVersion = "{0}.{1}.{2}" -f `
    $versionMatch.Groups["major"].Value,
    $versionMatch.Groups["minor"].Value,
    $versionMatch.Groups["patch"].Value
if ($actualVersion -ne $ExpectedVersion) {
    throw "Expected the Inno Setup $ExpectedVersion compiler, got PE file version $fileVersion at $CompilerPath"
}

# Compile a tiny valid script as a success-producing execution probe. `ISCC /?`
# prints usage and leaves a non-zero native exit code on the pinned compiler,
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

    $probeOutputText = (& $CompilerPath "/Q" $probeScript 2>&1 | Out-String)
    $probeExitCode = $LASTEXITCODE
    if ($probeExitCode -ne 0) {
        throw "The Inno Setup compiler probe exited $probeExitCode`: $($probeOutputText.Trim())"
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
