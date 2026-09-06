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

# Keep a real execution probe as well. Inno deliberately returns non-zero for
# /?, so only empty output is an execution failure; the version decision above
# comes from PE metadata rather than this help text.
$banner = (& $CompilerPath /? 2>&1 | Out-String)
if ([string]::IsNullOrWhiteSpace($banner)) {
    throw "The Inno Setup compiler at $CompilerPath printed no help output."
}

Write-Host "Verified Inno Setup compiler $actualVersion (PE file version $fileVersion) at $CompilerPath"
[string] $CompilerPath
