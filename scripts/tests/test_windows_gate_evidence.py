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


@pytest.mark.parametrize("expires_during_fingerprint", [False, True])
def test_wait_gate_probes_contender_before_hash_and_rechecks_liveness(
    pwsh, tmp_path, expires_during_fingerprint
):
    """Execute the real gate with an expiry injected inside its byte check."""
    fragment = SOURCE[SOURCE.index('$waitOutcome ='):SOURCE.index('# --- Gate 16:')]
    code = r'''
New-Item -ItemType Directory (Join-Path $installRoot 'app') | Out-Null
$script:expired = EXPIRES
$script:hashes = 0
$script:events = @()
$script:gateResults = @()
$layerBaseline = 'baseline'
$script:stand = [pscustomobject]@{ Id = 42; HasExited = $false }
$script:waiter = [pscustomobject]@{ Handle = 1; HasExited = $false; ExitCode = 0 }
$script:waiter | Add-Member ScriptMethod WaitForExit { param($timeout) return $this.HasExited }
$script:waiter | Add-Member ScriptMethod Refresh {}
$script:other = [pscustomobject]@{ Handle = 2; HasExited = $true; ExitCode = 1 }
$script:other | Add-Member ScriptMethod WaitForExit { param($timeout) return $true }
$script:other | Add-Member ScriptMethod Refresh {}
function Start-StandIn { return $script:stand }
function Start-SandboxedSetup {
    param($Executable, $ExtraArguments)
    if ($ExtraArguments -match '/WAITPID=') {
        Set-Content -LiteralPath $waitLog -Value '/WAITPID: waiting up to 120 s'
        return $script:waiter
    }
    if (-not $mutexPresent) { throw 'contender launch did not observe the setup mutex' }
    $script:events += 'contender'
    return $script:other
}
function Start-Sleep { param($Seconds, $Milliseconds) }
function LayerFingerprint {
    param($root)
    $script:hashes++
    if ($script:hashes -eq 2) {
        $script:events += 'in-wait-hash'
        if ($script:expired) {
            $script:waiter.HasExited = $true
            $script:waiter.ExitCode = 3
            Set-Content -LiteralPath $waitOutcome -Value '{"result":"failed"}'
        }
    }
    return 'baseline'
}
function Stop-Process {
    param($Id, [switch]$Force)
    if ($Id -ne $script:stand.Id) { throw 'unexpected process stop' }
    $script:stand.HasExited = $true
    $script:waiter.HasExited = $true
    if (-not $script:expired) {
        Set-Content -LiteralPath $waitOutcome -Value '{"result":"installed"}'
        Remove-Item -LiteralPath $waitSentinel
    }
}
function Gate {
    param($id, $name, $pass, $detail)
    $script:gateResults += @{ id = $id; pass = $pass }
}
$ownedMutex = [Threading.Mutex]::new($false, 'WaveguideGeneratorSetup')
try {
FRAGMENT
    if (($script:events -join ',') -ne 'contender,in-wait-hash') { throw 'hash consumed the wait before contender probe' }
    $expected = -not $script:expired
    if ($heldWhileAlive -ne $expected) { throw 'pre-hash liveness was incorrectly retained after expiry' }
    if ($script:gateResults.Count -ne 2) { throw 'missing gate verdicts' }
    foreach ($gate in $script:gateResults) {
        if ($gate.pass -ne $expected) { throw "wrong gate verdict: $($gate.id)" }
    }
} finally { $ownedMutex.Dispose() }
'''.replace("EXPIRES", "$true" if expires_during_fingerprint else "$false").replace("FRAGMENT", fragment)
    run_ps(pwsh, tmp_path, code)


@pytest.mark.parametrize("workflow_path", [
    ".github/workflows/rc-build.yml", ".github/workflows/rc-build.yml@refs/heads/dev",
])
@pytest.mark.parametrize("production_changed", [False, True])
def test_installer_replay_validates_actual_workflow_and_production_diff(
    pwsh, tmp_path, workflow_path, production_changed
):
    import yaml

    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    source = workflow['jobs']['windows-installer-replay']['steps'][1]['run']
    script = tmp_path / 'replay-check.ps1'
    script.write_text(r'''
$ErrorActionPreference = 'Stop'
$env:REPLAY_RUN = '123'
$env:GITHUB_REPOSITORY = 'example/fixture'
$env:GITHUB_ENV = Join-Path $pwd 'github-env.txt'
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'api') {
        return '{"path":"WORKFLOW_PATH","head_sha":"baseline"}'
    }
    New-Item -ItemType Directory replay-setup | Out-Null
    Set-Content replay-setup/fixture-setup.exe 'diagnostic fixture, never executed'
    $hash = (Get-FileHash replay-setup/fixture-setup.exe -Algorithm SHA256).Hash
    Set-Content replay-setup/fixture-setup.exe.sha256 ($hash + '  fixture-setup.exe')
}
function git {
    $global:LASTEXITCODE = 0
    return 'CHANGED_PATH'
}
'''.replace('WORKFLOW_PATH', workflow_path).replace(
        'CHANGED_PATH', 'server/app.py' if production_changed else 'installers/windows/gates.ps1'
    ) + source)
    result = subprocess.run([pwsh, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script)],
                            cwd=tmp_path, text=True, capture_output=True, timeout=25)
    if production_changed:
        assert result.returncode != 0
        assert 'Replay cannot qualify changed production input' in result.stderr
        assert not (tmp_path / 'replay-setup').exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'REPLAY_SETUP=' in (tmp_path / 'github-env.txt').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize("exit_code", [None, 1])
def test_replay_uses_powershell_script_success_not_unset_native_exit(pwsh, tmp_path, exit_code):
    import yaml

    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    source = workflow['jobs']['windows-installer-replay']['steps'][2]['run']
    setup = tmp_path / 'installers/windows'
    setup.mkdir(parents=True)
    (setup / 'gates.ps1').write_text(
        "param($Setup, $EvidenceDir)\n" + ("exit 1\n" if exit_code else "Write-Output 'summary: PASS=18 SKIP=5'\n")
    )
    script = tmp_path / 'invoke.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n$env:REPLAY_SETUP='fixture'\n$env:RUNNER_TEMP=$pwd\n" + source)
    result = subprocess.run([pwsh, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script)],
                            cwd=tmp_path, text=True, capture_output=True, timeout=25)
    if exit_code:
        assert result.returncode != 0
        assert 'Installer gate replay failed' in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'summary: PASS=18' in result.stdout
