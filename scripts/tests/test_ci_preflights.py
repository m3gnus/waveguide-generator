"""Regression checks for the small platform preflights used by bundle jobs."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
RC_WORKFLOW = ROOT / ".github" / "workflows" / "rc-build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PROPOSAL_WORKFLOW = ROOT / "docs" / "reference" / "main-build.workflow-proposal.yml"
INNO_PREFLIGHT_WORKFLOW = ROOT / ".github" / "workflows" / "windows-inno-preflight.yml"
INNO_VERIFIER = ROOT / "scripts" / "ci" / "verify_inno_setup.ps1"
LINUX_DEPENDENCIES = ROOT / "scripts" / "ci" / "install_linux_runtime_dependencies.sh"


LINUX_PACKAGES = (
    "libglu1-mesa",
    "libgl1",
    "libgomp1",
    "libfontconfig1",
    "libxrender1",
    "libxcursor1",
    "libxft2",
    "libxinerama1",
    "libxi6",
    "libxext6",
)


def _rc_steps(job: str) -> list[dict]:
    workflow = yaml.safe_load(RC_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"][job]["steps"]


@pytest.mark.skipif(sys.platform == "win32", reason="the helper requires a POSIX shell")
def test_linux_preflight_executes_the_documented_packages_and_propagates_failure(
    tmp_path: Path,
) -> None:
    """Run the real helper with a controlled apt boundary.

    The test does not install packages on the developer machine. Its fake
    ``sudo`` and ``apt-get`` are only the external boundary; the helper's
    actual shell, argument order, and ``set -e`` behavior are exercised in
    both the successful and failing paths.
    """

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "apt.log"
    (fake_bin / "sudo").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    (fake_bin / "apt-get").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$WG_TEST_APT_LOG\"\n"
        "if [[ \"${1:-}\" == install && \"${WG_TEST_APT_FAIL_INSTALL:-0}\" == 1 ]]; then exit 17; fi\n",
        encoding="utf-8",
    )
    for path in fake_bin.iterdir():
        path.chmod(0o755)

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "WG_TEST_APT_LOG": str(log),
    }
    passing = subprocess.run(
        [str(LINUX_DEPENDENCIES)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "update",
        "install --yes --no-install-recommends " + " ".join(LINUX_PACKAGES),
    ]

    log.unlink()
    failing_environment = {**environment, "WG_TEST_APT_FAIL_INSTALL": "1"}
    failing = subprocess.run(
        [str(LINUX_DEPENDENCIES)],
        cwd=ROOT,
        env=failing_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failing.returncode == 17
    assert log.read_text(encoding="utf-8").splitlines() == [
        "update",
        "install --yes --no-install-recommends " + " ".join(LINUX_PACKAGES),
    ]


def test_linux_preflight_precedes_the_rc_installed_package_gate() -> None:
    steps = _rc_steps("linux-bundle")
    dependency_step = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "scripts/ci/install_linux_runtime_dependencies.sh"
    )
    qualification_step = next(
        index
        for index, step in enumerate(steps)
        if "qualify_installed_cpu.py" in (step.get("run") or "")
    )
    assert dependency_step < qualification_step
    assert "--skip-checks" not in steps[qualification_step]["run"]


def test_inno_verifier_uses_bounded_compiler_version_probe_and_all_workflow_variants() -> None:
    verifier = INNO_VERIFIER.read_text(encoding="utf-8")
    assert '[Environment]::GetEnvironmentVariable("ProgramFiles(x86)")' in verifier
    assert "FileVersionInfo]::GetVersionInfo($CompilerPath).FileVersion" in verifier
    assert "ExpectedVersion = \"6.7.1\"" in verifier
    assert 'Compiler engine version:' in verifier
    assert "(?m)^\\s*Compiler engine version:" in verifier
    assert '(& $CompilerPath $probeScript 2>&1 | Out-String)' in verifier
    assert "$probeExitCode -ne 0" in verifier
    assert 'Test-Path -LiteralPath $probeExecutable -PathType Leaf' in verifier
    assert '[IO.Path]::GetTempPath()' in verifier
    assert "Get-ChildItem" not in verifier
    assert '"$env:ProgramFiles(x86)' not in verifier

    for workflow_path in (RC_WORKFLOW, RELEASE_WORKFLOW, PROPOSAL_WORKFLOW):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "choco install innosetup --version=6.7.1" in workflow
        assert 'if ($LASTEXITCODE -ne 0)' in workflow
        assert "./scripts/ci/verify_inno_setup.ps1" in workflow
        assert '$banner -notmatch "6\\.7\\.1"' not in workflow


def test_windows_inno_preflight_is_manual_read_only_and_uses_the_shared_gate() -> None:
    text = INNO_PREFLIGHT_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"ref"}
    assert "push:" not in text
    assert "schedule:" not in text
    assert "upload-artifact" not in text
    assert "gh release" not in text
    assert "git push" not in text

    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["verify-inno"]
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 10
    steps = job["steps"]
    assert len(steps) == 3
    checkout = steps[0]
    assert checkout["uses"] == (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    )
    assert checkout["with"] == {
        "ref": "${{ inputs.ref }}",
        "persist-credentials": False,
    }
    assert steps[1]["shell"] == "pwsh"
    assert "innosetup --version=6.7.1" in steps[1]["run"]
    assert "$LASTEXITCODE -ne 0" in steps[1]["run"]
    assert steps[2]["shell"] == "pwsh"
    assert "./scripts/ci/verify_inno_setup.ps1" in steps[2]["run"]
    assert "$LASTEXITCODE -ne 0" in steps[2]["run"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is only available on Windows runners")
def test_inno_verifier_rejects_a_missing_compiler_before_execution() -> None:
    result = subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoProfile",
            "-File",
            str(INNO_VERIFIER),
            "-CompilerPath",
            str(ROOT / "does-not-exist" / "ISCC.exe"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ISCC.exe was not found" in result.stderr


@pytest.mark.skipif(
    sys.platform != "win32"
    or shutil.which("pwsh") is None
    or os.environ.get("WG_TEST_PINNED_INNO") != "1",
    reason="the real compiler probe is opt-in after the pinned Windows setup",
)
def test_inno_verifier_executes_the_real_installed_compiler_probe() -> None:
    """Exercise the compiler engine version and successful compile on Windows.

    The RC workflow installs the pinned compiler before this same helper runs.
    A normal Windows checkout skips this environment test; set
    ``WG_TEST_PINNED_INNO=1`` only after installing the pinned package. When
    the compiler is present in that explicitly prepared environment, a drifted
    version is a real failure.
    """

    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    compiler = next(
        (
            Path(root) / "Inno Setup 6" / "ISCC.exe"
            for root in roots
            if root and (Path(root) / "Inno Setup 6" / "ISCC.exe").is_file()
        ),
        None,
    )
    if compiler is None:
        pytest.skip("the Windows host has no standard Inno Setup installation")

    environment = {**os.environ, "RUNNER_TEMP": tempfile.gettempdir()}
    result = subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoProfile",
            "-File",
            str(INNO_VERIFIER),
            "-CompilerPath",
            str(compiler),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified Inno Setup compiler 6.7.1" in result.stdout


def test_ci_compiles_the_windows_installer_script_on_every_run() -> None:
    """ISCC on the real .iss, stub payload, pinned 6.7.1: a refused compile fails CI."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["windows-installer-compile"]
    assert job["runs-on"] == "windows-latest"
    assert job["permissions"] == {"contents": "read"}
    assert "if" not in job, "the compile must run on every CI run"
    install, compile_step = job["steps"][1], job["steps"][2]
    assert "choco install innosetup --version=6.7.1" in install["run"]
    assert "$LASTEXITCODE -ne 0" in install["run"]
    assert "./scripts/ci/verify_inno_setup.ps1" in install["run"]
    assert "./scripts/ci/compile_inno_stub.ps1" in compile_step["run"]

    stub = (ROOT / "scripts" / "ci" / "compile_inno_stub.ps1").read_text(encoding="utf-8")
    assert "installers\\windows\\bundle-setup.iss" in stub
    assert "$code -ne 0" in stub and "throw" in stub
    assert "stub-setup.exe" in stub
    # Compile only: the produced setup is never started.
    assert "Start-Process" not in stub and "Invoke-Item" not in stub


@pytest.mark.parametrize("outcome", ["cpu", "gpu", "unavailable", "exception"])
def test_ci_opencl_qualification_owns_and_cleans_a_session(monkeypatch, tmp_path, outcome):
    from server.platform import temp_session
    from server.solver import bempp_opencl

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    step = next(step for step in workflow["jobs"]["server"]["steps"]
                if step.get("name") == "Install the pinned CPU OpenCL runtime for the CAD pipeline")
    program = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.setitem(sys.modules, "pyopencl", SimpleNamespace(get_platforms=lambda: []))
    monkeypatch.setattr(temp_session.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(temp_session, "_active_root", None)
    monkeypatch.setattr(temp_session, "_parent_root", None)
    sessions = []

    def qualify():
        # Exercise the probe's required-directory contract at the actual CI call.
        root = Path(temp_session.spawned_directory_root(required=True))
        assert (root / temp_session.OWNER_LOCK_NAME).is_file()
        sessions.append(root)
        if outcome == "exception":
            raise RuntimeError("qualification failed")
        return {"ok": outcome != "unavailable", "device": {"type": outcome}}

    monkeypatch.setattr(bempp_opencl, "qualified_opencl", qualify)
    if outcome == "cpu":
        exec(compile(program, ".github/workflows/ci.yml", "exec"), {})
    elif outcome == "exception":
        with pytest.raises(RuntimeError, match="qualification failed"):
            exec(compile(program, ".github/workflows/ci.yml", "exec"), {})
    else:
        with pytest.raises(SystemExit, match="failed WG's real compute qualification"):
            exec(compile(program, ".github/workflows/ci.yml", "exec"), {})
    assert len(sessions) == 1
    assert not sessions[0].exists()
    assert temp_session.temporary_directory_root() is None
    assert not list(tmp_path.iterdir())


def test_inno_compile_stub_supplies_every_required_payload_source() -> None:
    installer = (ROOT / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")
    files = installer.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    sources = re.findall(r'^Source: "\{#PayloadDir\}\\([^\"]+)"', files, re.MULTILINE)
    stub = (ROOT / "scripts/ci/compile_inno_stub.ps1").read_text(encoding="utf-8")
    payload = re.findall(r'Join-Path \$payload "([^\"]+)"', stub)
    assert sources
    for source in sources:
        if source.endswith("*"):
            prefix = source[:-1]
            assert any(path.startswith(prefix) and path != prefix for path in payload), source
        else:
            assert source in payload, source
