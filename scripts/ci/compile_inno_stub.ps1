# Compile installers/windows/bundle-setup.iss against a stub payload, so a
# change that ISCC refuses (a brace comment that ends early, a line starting
# with '#', a duplicate predefined identifier) fails CI on the commit that
# made it, not at the next release build. Nothing is installed or run: the
# setup.exe it produces is checked for existence and then deleted with the
# stub. The payload is the smallest tree the script's [Setup] and [Files]
# sections read at compile time: the icon, LICENSE, root native helper and one
# file in each layer. The helper placeholder is compile-only, never executed.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $CompilerPath
)

$ErrorActionPreference = "Stop"

$parent = $env:RUNNER_TEMP
if ([string]::IsNullOrWhiteSpace($parent)) {
    $parent = [IO.Path]::GetTempPath()
}
$root = Join-Path $parent ("inno-stub-" + [guid]::NewGuid().ToString("N"))
$payload = Join-Path $root "payload"
$output = Join-Path $root "output"
New-Item -ItemType Directory -Path (Join-Path $payload "app"), (Join-Path $payload "runtime"), (Join-Path $payload "recovery"), $output | Out-Null
try {
    Copy-Item -LiteralPath "LICENSE" -Destination (Join-Path $payload "app\LICENSE")
    python -c "import sys; from pathlib import Path; sys.path.insert(0, 'launchers/macos'); import generate_icon; generate_icon.build_ico(Path(sys.argv[1]))" (Join-Path $payload "WaveguideGenerator.ico")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not write the stub icon (exit $LASTEXITCODE)."
    }
    Set-Content -LiteralPath (Join-Path $payload "app\stub.txt") -Value "stub payload" -Encoding utf8
    Set-Content -LiteralPath (Join-Path $payload "runtime\stub.txt") -Value "stub runtime" -Encoding utf8
    Set-Content -LiteralPath (Join-Path $payload "recovery\stub.txt") -Value "stub recovery" -Encoding utf8
    Set-Content -LiteralPath (Join-Path $payload "Waveguide Generator.exe") -Value "compile-only native helper placeholder" -Encoding utf8

    $arguments = @(
        "/DAppVersion=0.0.0-ci",
        "/DVersionInfoVersion=0.0.0.0",
        "/DPayloadDir=$payload",
        "/DMaxPayloadDepth=40",
        "/DOutputDir=$output",
        "/DOutputBaseFilename=stub-setup",
        "installers\windows\bundle-setup.iss"
    )
    $text = (& $CompilerPath @arguments 2>&1 | Out-String)
    $code = $LASTEXITCODE
    Write-Host $text
    if ($code -ne 0) {
        throw "ISCC refused installers/windows/bundle-setup.iss (exit $code)."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $output "stub-setup.exe") -PathType Leaf)) {
        throw "ISCC exited 0 but produced no stub-setup.exe."
    }
    Write-Host "installers/windows/bundle-setup.iss compiles."
}
finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
