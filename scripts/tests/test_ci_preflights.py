"""Regression checks for the small platform preflights used by bundle jobs."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
RC_WORKFLOW = ROOT / ".github" / "workflows" / "rc-build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PROPOSAL_WORKFLOW = ROOT / "docs" / "reference" / "main-build.workflow-proposal.yml"
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


def test_inno_verifier_uses_bounded_pe_metadata_and_all_workflow_variants() -> None:
    verifier = INNO_VERIFIER.read_text(encoding="utf-8")
    assert '[Environment]::GetEnvironmentVariable("ProgramFiles(x86)")' in verifier
    assert "FileVersionInfo]::GetVersionInfo($CompilerPath).FileVersion" in verifier
    assert "ExpectedVersion = \"6.7.1\"" in verifier
    assert "^\\s*(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)" in verifier
    assert '(& $CompilerPath "/Q" $probeScript 2>&1 | Out-String)' in verifier
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
    sys.platform != "win32" or shutil.which("pwsh") is None,
    reason="the real compiler probe runs on Windows only",
)
def test_inno_verifier_executes_the_real_installed_compiler_probe() -> None:
    """Exercise PE metadata and the successful ISCC compile on Windows.

    The RC workflow installs the pinned compiler before this same helper runs.
    A normal Windows checkout without Inno Setup skips this environment test;
    when the compiler is present, a drifted version is a real failure.
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
