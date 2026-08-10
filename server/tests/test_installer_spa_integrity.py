"""Installers must never turn a stale or unverified SPA into a successful install."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SHELL_INSTALLER = ROOT / "scripts" / "install.sh"
BATCH_INSTALLER = ROOT / "scripts" / "install.bat"
VERSION = "2.0.0"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _test_checkout(
    tmp_path: Path,
    stamp: dict[str, object] | None,
    *,
    simulate_default_fetch_failure: bool = True,
) -> Path:
    checkout = tmp_path / "Hornlab - Workspace (offline)" / "waveguide-generator-v2"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(SHELL_INSTALLER, checkout / "scripts" / "install.sh")
    fetch_spa_source = ROOT / "scripts" / "fetch_spa.py"
    if simulate_default_fetch_failure:
        shutil.copy2(fetch_spa_source, checkout / "scripts" / "fetch_spa_impl.py")
        _write(
            checkout / "scripts" / "fetch_spa.py",
            """import sys
from fetch_spa_impl import main

if '--check' in sys.argv:
    raise SystemExit(main())
print('ERROR: simulated release host outage', file=sys.stderr)
raise SystemExit(2)
""",
        )
    else:
        shutil.copy2(fetch_spa_source, checkout / "scripts" / "fetch_spa.py")

    _write(checkout / "shared" / "version.json", json.dumps({"version": VERSION}))
    _write(checkout / "launch" / "serve.py", "")
    _write(checkout / "launchers" / "statusapp" / "__main__.py", "")
    _write(checkout / "launchers" / "macos" / "launch-wg.command", "#!/bin/bash\nexit 0\n")
    _write(checkout / "launchers" / "linux" / "launch-wg.sh", "#!/bin/bash\nexit 0\n")
    _write(checkout / "server" / "requirements-lock.txt", "")
    _write(checkout / "server" / "requirements-pins.txt", "")
    _write(checkout / "scripts" / "check_backends.py", "raise SystemExit(0)\n")
    _write(
        checkout / "scripts" / "bootstrap.py",
        """from pathlib import Path
import os
import sys

target = Path('.venv/bin/python')
target.parent.mkdir(parents=True, exist_ok=True)
if not target.exists():
    os.symlink(sys.executable, target)
""",
    )

    if stamp is not None:
        dist = checkout / "frontend" / "dist"
        _write(dist / "index.html", "existing interface")
        _write(dist / ".wg2-spa.json", json.dumps(stamp))
    else:
        _write(checkout / "frontend" / "dist" / "index.html", "local interface")
    return checkout


def _run_installer(
    checkout: Path,
    *args: str,
    base_url: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "bash",
        str(checkout / "scripts" / "install.sh"),
        "--after-pull",
        "--no-launch",
    ]
    if base_url is not None:
        base_url.mkdir(exist_ok=True)
        command.extend(("--spa-base-url", base_url.as_uri()))
    command.extend(args)
    return subprocess.run(
        command,
        cwd=checkout,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


requires_shell_runtime = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("python3.13") is None,
    reason="the shell installer requires bash and its production Python 3.13 runtime",
)


@requires_shell_runtime
def test_shell_installer_accepts_matching_verified_spa_without_network(tmp_path: Path) -> None:
    checkout = _test_checkout(
        tmp_path, {"version": VERSION, "sha256": "a" * 64, "source": "previous release"}
    )

    result = _run_installer(checkout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SPA 2.0.0 is installed" in result.stdout
    assert "matching checksum-verified frontend/dist" in result.stdout
    assert "Install complete" in result.stdout


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        {"version": "1.9.9", "sha256": "a" * 64},
        {"version": VERSION},
        {"version": VERSION, "sha256": "invalid"},
    ],
    ids=("unstamped", "stale", "version-only", "invalid-digest"),
)
@requires_shell_runtime
def test_shell_installer_rejects_stale_or_unverified_offline_spa(
    tmp_path: Path, stamp: dict[str, object] | None
) -> None:
    checkout = _test_checkout(tmp_path, stamp)

    result = _run_installer(checkout)

    assert result.returncode == 1
    assert "not a checksum-verified copy of the requested version" in result.stderr
    assert "Install complete" not in result.stdout
    assert not (checkout / ".venv").exists()


@pytest.mark.parametrize("option", ("--force", "--spa-archive", "--spa-base-url"))
@requires_shell_runtime
def test_shell_explicit_replacement_failure_is_not_an_offline_success(
    tmp_path: Path, option: str
) -> None:
    checkout = _test_checkout(
        tmp_path, {"version": VERSION, "sha256": "a" * 64, "source": "previous release"}
    )
    args = [option]
    if option == "--spa-archive":
        args.append(str(tmp_path / "missing-spa.tar.gz"))
    elif option == "--spa-base-url":
        args.append((tmp_path / "missing-release").as_uri())

    result = _run_installer(checkout, *args)

    assert result.returncode == 1
    assert "requested SPA replacement could not be installed" in result.stderr
    assert "Install complete" not in result.stdout


@requires_shell_runtime
def test_shell_checksum_mismatch_never_falls_back_to_local_dist(tmp_path: Path) -> None:
    checkout = _test_checkout(tmp_path, None, simulate_default_fetch_failure=False)
    release = tmp_path / "bad-release"
    release.mkdir()
    name = f"waveguide-generator-v2-spa-{VERSION}.tar.gz"
    (release / name).write_bytes(b"corrupted archive")
    (release / f"{name}.sha256").write_text(f"{'0' * 64}  {name}\n", encoding="utf-8")

    result = _run_installer(checkout, base_url=release)

    assert result.returncode == 1
    assert "REFUSING TO EXTRACT" in result.stderr
    assert "requested SPA replacement could not be installed" in result.stderr
    assert "Install complete" not in result.stdout


@requires_shell_runtime
def test_shell_skip_spa_is_the_explicit_developer_opt_in(tmp_path: Path) -> None:
    checkout = _test_checkout(tmp_path, None)

    result = _run_installer(checkout, "--skip-spa")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped by request" in result.stdout
    assert "Install complete" in result.stdout


def test_both_installers_expose_skip_spa_and_fail_closed_after_fetch_errors() -> None:
    shell = SHELL_INSTALLER.read_text(encoding="utf-8")
    batch = BATCH_INSTALLER.read_text(encoding="utf-8")

    assert "--skip-spa) SKIP_SPA=1" in shell
    assert 'if /I "%~1"=="--skip-spa" goto arg_skip_spa' in batch

    shell_failure = shell[
        shell.index('    else\n        if [[ "$FORCE"'):shell.index("# ── The Python")
    ]
    assert "--check" in shell_failure
    assert 'spa_check_args+=(--version "$SPA_VERSION")' in shell_failure
    assert '[[ -f "$ROOT/frontend/dist/index.html" ]]' not in shell_failure
    assert '[[ "$FORCE" -eq 1 ]]' in shell_failure
    assert '[[ -n "$SPA_ARCHIVE" ]]' in shell_failure
    assert '[[ -n "$SPA_BASE_URL" ]]' in shell_failure

    batch_failure = batch[batch.index(":spa_failed"):batch.index(":spa_done")]
    assert 'set "SPA_CHECK_ARGS=--check"' in batch_failure
    assert 'SPA_CHECK_ARGS=%SPA_CHECK_ARGS% --version "%SPA_VERSION%"' in batch_failure
    assert 'if not exist "frontend\\dist\\index.html"' not in batch_failure
    for name in ("FORCE", "SPA_ARCHIVE", "SPA_BASE_URL"):
        assert f"if defined {name} goto spa_replacement_failed" in batch_failure


def test_windows_installer_keeps_crlf_after_fail_closed_changes() -> None:
    data = BATCH_INSTALLER.read_bytes()
    assert b"\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")
