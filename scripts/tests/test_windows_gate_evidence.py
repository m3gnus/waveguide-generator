"""Execute the actual PowerShell diagnostic helpers without launching an installer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "installers/windows/gates.ps1").read_text()
HELPERS = SOURCE[SOURCE.index("function Assert-EvidencePath"):SOURCE.index("function Start-StandIn")]


@pytest.fixture
def pwsh():
    command = os.environ.get("WG_TEST_PWSH") or shutil.which("pwsh")
    if not command:
        pytest.skip("PowerShell execution requires pwsh (native Windows CI supplies it)")
    return command


def run_ps(pwsh, tmp_path, code):
    # Resolve macOS /var aliases: the gate deliberately rejects linked parents.
    tmp_path = tmp_path.resolve()
    script = tmp_path / "test.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n" + HELPERS + "\n" + r'''
$env:TEMP = $env:WG_TEST_ROOT
$env:RUNNER_TEMP = $env:WG_TEST_ROOT
$evidenceRoot = New-GateEvidence (Join-Path $env:TEMP 'WG-inst-evidence-test')
$gateRoot = Join-Path $env:TEMP 'private-gate'
$installRoot = Join-Path $gateRoot 'i'
$gateData = Join-Path $gateRoot 'data'
$wglinkAddins = Join-Path $gateRoot 'AddIns'
$developerAddins = Join-Path $gateRoot 'Developer'
New-Item -ItemType Directory $installRoot, $gateData, $wglinkAddins, $developerAddins | Out-Null
$env:WG2_DATA_DIR = $gateData
$Setup = Join-Path $gateRoot 'setup.exe'
Set-Content -LiteralPath $Setup -Value 'fixture installer'
$setupSha256 = (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash
$script:setupEvidence = @()
''' + code)
    result = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                            env=os.environ | {"WG_TEST_ROOT": str(tmp_path)}, text=True,
                            capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path


def test_real_script_parses(pwsh, tmp_path):
    env = os.environ | {"WG_GATE_SOURCE": str(ROOT / "installers/windows/gates.ps1")}
    result = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-Command",
        "$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($env:WG_GATE_SOURCE,[ref]$tokens,[ref]$errors) | Out-Null; if($errors.Count){$errors | Out-String | Write-Error;exit 1}"],
        env=env, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_launcher_retains_failure_before_next_invocation(pwsh, tmp_path):
    root = run_ps(pwsh, tmp_path, r'''
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$PassThru, [switch]$Wait, [switch]$NoNewWindow)
    foreach ($required in @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')) {
        if ($required -notin $ArgumentList) { throw "launch dropped $required" }
    }
    # Simulate native/Inno failure plus its durable journal. The real launcher
    # function must collect this before returning to the next installer.
    Set-Content -LiteralPath (Join-Path $installRoot '.upgrade-in-progress') -Value 'failed native state'
    foreach ($arg in $ArgumentList) {
        if ($arg -match '^/(LOG|OUTCOME)="([^"]+)"$') { Set-Content -LiteralPath $Matches[2] -Value 'native prepare returned 3' }
    }
    $process = [pscustomobject]@{ Handle = 1; HasExited = $true; ExitCode = 3 }
    $process | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
    return $process
}
$p = Start-SandboxedSetup -Executable $Setup -ExtraArguments '/NORESTART' -Wait
if ($p.ExitCode -ne 3 -or $setupEvidence.Count -ne 1 -or -not $setupEvidence[0].complete) { throw 'exit swallowed or evidence not completed' }
Remove-Item -LiteralPath $gateRoot -Recurse -Force
''')
    folder, = [p for p in (root / "WG-inst-evidence-test").iterdir() if p.is_dir()]
    record = json.loads((folder / "invocation.json").read_text(encoding="utf-8-sig"))
    assert record["exitCode"] == 3 and record["status"] == "exited"
    assert len(record["sha256"]) == 64
    assert all(flag in record["arguments"] for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"))
    assert any(x.startswith('/WGLINKADDINSDIR="') for x in record["arguments"])
    assert (folder / "native-journal.txt").read_text().strip() == "failed native state"
    assert (folder / "setup.log").exists()
    assert not any(x.upper().startswith("/OUTCOME") for x in record["arguments"])
    assert not (folder / "outcome.json").exists()
    assert not (root / "private-gate").exists()


@pytest.mark.parametrize("kind", ["LOG", "WGLOG"])
def test_explicit_diagnostic_paths_preserved_and_copied(pwsh, tmp_path, kind):
    root = run_ps(pwsh, tmp_path, r'''
$log = Join-Path $gateRoot 'explicit.log'
$outcome = Join-Path $gateRoot 'explicit-outcome.json'
$entry = New-SetupEvidence -Executable $Setup -Arguments @(('/KIND="' + $log + '"'), ('/OUTCOME="' + $outcome + '"'))
Set-Content -LiteralPath $log -Value 'original log'
Set-Content -LiteralPath $outcome -Value 'original outcome'
$process = [pscustomobject]@{ HasExited = $true; ExitCode = 0 }
$process | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
$entry.process = $process
$script:setupEvidence += $entry
Complete-SetupEvidence
Remove-Item -LiteralPath $gateRoot -Recurse -Force
'''.replace("KIND", kind))
    folder, = [p for p in (root / "WG-inst-evidence-test").iterdir() if p.is_dir()]
    record = json.loads((folder / "invocation.json").read_text(encoding="utf-8-sig"))
    assert len(record["arguments"]) == 2
    assert record["exitCode"] == 0
    assert (folder / f"{kind}-explicit.log").read_text().strip() == "original log"
    assert (folder / "OUTCOME-explicit-outcome.json").read_text().strip() == "original outcome"


@pytest.mark.parametrize("arguments", [
    "@('/LOG')", "@('/LOG=outside.log')", "@(('/LOG=' + $Setup), ('/LOG=' + $Setup))",
    "@(('/LOG=' + $Setup), ('/WGLOG=' + $Setup))", "@('/OUTCOME=' + (Join-Path $gateRoot '../escape.json'))",
])
def test_unsafe_or_ambiguous_diagnostic_arguments_refused(pwsh, tmp_path, arguments):
    run_ps(pwsh, tmp_path, f'''
$refused = $false
try {{ New-SetupEvidence -Executable $Setup -Arguments ({arguments}) | Out-Null }} catch {{ $refused = $true }}
if (-not $refused) {{ throw 'unsafe diagnostic argument accepted' }}
''')


@pytest.mark.parametrize("path", [
    "$env:TEMP", "(Join-Path $env:TEMP 'ordinary-profile')", "$evidenceRoot",
    "(Join-Path $gateRoot 'WG-inst-evidence-nested')",
    "(Join-Path $env:TEMP '../WG-inst-evidence-escape')",
])
def test_evidence_directory_cannot_overwrite_or_escape(pwsh, tmp_path, path):
    run_ps(pwsh, tmp_path, f'''
$refused = $false
try {{ New-GateEvidence {path} | Out-Null }} catch {{ $refused = $true }}
if (-not $refused) {{ throw 'unsafe evidence path accepted' }}
''')


def test_reparse_evidence_ancestor_refused(pwsh, tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create symlinks")
    run_ps(pwsh, tmp_path, r'''
$env:RUNNER_TEMP = Join-Path $env:TEMP 'linked'
$refused = $false
try { New-GateEvidence (Join-Path $env:RUNNER_TEMP 'WG-inst-evidence-link') | Out-Null } catch { $refused = $true }
if (-not $refused) { throw 'linked evidence parent accepted' }
''')


def test_developer_fixture_collides_with_valid_production_marker(pwsh, tmp_path):
    from scripts.install_wglink import _valid_install_marker, is_managed_target

    marker = {"schema": 1, "managedBy": "waveguide-generator", "waveguideGeneratorRoot": str(tmp_path.resolve() / "private-gate/i/app"),
              "waveguideGeneratorVersion": "0.3.6", "sourceCommit": "a" * 40, "addinVersion": "0.3.0"}
    (tmp_path / "real-marker.json").write_text(json.dumps(marker))
    fragment = SOURCE[SOURCE.index('# --- Gate 11:'):SOURCE.index('$developerBefore =')]
    root = run_ps(pwsh, tmp_path, r'''
$wglinkMarker = Join-Path $env:TEMP 'real-marker.json'
$wglinkOk = $true
''' + fragment)
    target = root / "private-gate/Developer/WGLink"
    actual = json.loads((target / "wglink_install.json").read_text(encoding="utf-8-sig"))
    assert actual == marker
    assert _valid_install_marker(actual, Path(marker["waveguideGeneratorRoot"]))
    assert not is_managed_target(target, Path(marker["waveguideGeneratorRoot"]))
    (target / "wglink_dev.json").unlink()
    assert is_managed_target(target, Path(marker["waveguideGeneratorRoot"]))


@pytest.mark.parametrize("leaf", ["same.log", "invocation.json", "state.json"])
def test_explicit_evidence_names_cannot_collide(pwsh, tmp_path, leaf):
    root = run_ps(pwsh, tmp_path, r'''
$a = Join-Path $gateRoot 'a'
$b = Join-Path $gateRoot 'b'
New-Item -ItemType Directory $a, $b | Out-Null
$log = Join-Path $a 'LEAF'
$outcome = Join-Path $b 'LEAF'
$entry = New-SetupEvidence -Executable $Setup -Arguments @(('/LOG="' + $log + '"'), ('/OUTCOME="' + $outcome + '"'))
Set-Content -LiteralPath $log -Value 'original log bytes'
Set-Content -LiteralPath $outcome -Value 'original outcome bytes'
$process = [pscustomobject]@{ HasExited = $true; ExitCode = 3 }
$process | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
$entry.process = $process
$script:setupEvidence += $entry
Complete-SetupEvidence
Remove-Item -LiteralPath $gateRoot -Recurse -Force
'''.replace("LEAF", leaf))
    folder, = [p for p in (root / "WG-inst-evidence-test").iterdir() if p.is_dir()]
    assert (folder / f"LOG-{leaf}").read_text().strip() == "original log bytes"
    assert (folder / f"OUTCOME-{leaf}").read_text().strip() == "original outcome bytes"
    record = json.loads((folder / "invocation.json").read_text(encoding="utf-8-sig"))
    assert record["exitCode"] == 3 and record["status"] == "exited"
    assert isinstance(json.loads((folder / "state.json").read_text(encoding="utf-8-sig")), list)
