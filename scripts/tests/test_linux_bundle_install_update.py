"""bundle-install.sh --update: replace an existing installation, never create one."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "installers" / "linux" / "bundle-install.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")


def make_tarball(root: Path, version: str) -> Path:
    """The extracted tarball layout: the installer and uninstaller beside the app folder."""
    root.mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "install.sh")
    (root / "uninstall.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    app = root / "waveguide-generator"
    (app / "app").mkdir(parents=True)
    (app / "runtime").mkdir()
    (app / "app" / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    (app / "runtime" / "RUNTIME-MANIFEST.json").write_text("{}", encoding="utf-8")
    (app / "version.txt").write_text(version, encoding="utf-8")
    launcher = app / "waveguide-generator"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    (app / "waveguide-generator.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Waveguide Generator\n"
        "Exec=@INSTALL_DIR@/waveguide-generator\nIcon=waveguide-generator\n",
        encoding="utf-8",
    )
    (app / "waveguide-generator.png").write_bytes(b"png")
    return root


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "share")}


def run(tarball: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(tarball / "install.sh"), "--skip-checks", *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )


def installed_dir(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "waveguide-generator"


def test_update_replaces_an_existing_install_and_starts_nothing(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    (installed_dir(env) / "obsolete.txt").write_text("gone after the update", encoding="utf-8")
    launched = tmp_path / "launched"
    new = make_tarball(tmp_path / "v2", "two")
    (new / "waveguide-generator" / "waveguide-generator").write_text(f'#!/bin/sh\ntouch "{launched}"\n', encoding="utf-8")

    result = run(new, env, "--update")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "two"
    assert not (installed_dir(env) / "obsolete.txt").exists()
    assert sorted(p.name for p in installed_dir(env).parent.glob(".waveguide-generator.*")) == []
    assert not launched.exists()


def test_update_refuses_when_nothing_is_installed(tmp_path: Path, env: dict[str, str]) -> None:
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--update")
    assert result.returncode == 1
    assert "no Waveguide Generator installation" in result.stdout
    assert not installed_dir(env).exists()


def test_update_refuses_a_directory_that_is_not_our_install(tmp_path: Path, env: dict[str, str]) -> None:
    foreign = installed_dir(env)
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_text("mine", encoding="utf-8")
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--update")
    assert result.returncode == 1
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_update_rolls_back_when_the_commit_fails(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    shim = tmp_path / "bin"
    shim.mkdir()
    real_mv = shutil.which("mv")
    # Fail the rename that puts the staged tree into place, after the old one moved aside.
    (shim / "mv").write_text(
        '#!/bin/bash\ncase "$*" in *".install."*) echo injected >&2; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
        encoding="utf-8",
    )
    (shim / "mv").chmod(0o755)
    result = run(make_tarball(tmp_path / "v2", "two"), {**env, "PATH": f"{shim}{os.pathsep}{env['PATH']}"}, "--update")
    assert result.returncode == 1
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert sorted(p.name for p in installed_dir(env).parent.glob(".waveguide-generator.*")) == []


def integration_paths(env: dict[str, str]) -> dict[str, Path]:
    data = Path(env["XDG_DATA_HOME"])
    return {
        "desktop": data / "applications" / "waveguide-generator.desktop",
        "icon": data / "icons" / "hicolor" / "512x512" / "apps" / "waveguide-generator.png",
        "desktop_owner": data / "applications" / ".waveguide-generator.owner",
        "icon_owner": data / "icons" / "hicolor" / "512x512" / "apps" / ".waveguide-generator.owner",
        "link": Path(env["HOME"]) / ".local" / "bin" / "waveguide-generator",
    }


def late_failure_env(tmp_path: Path, env: dict[str, str], *, restore_failure: str | None = None) -> dict[str, str]:
    """Fail the final link creation, optionally fail one of the restore renames."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ln").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bin_dir / "ln").chmod(0o755)
    if restore_failure:
        patterns = {
            "application": ".previous.",
            "desktop": ".desktop.backup.",
            "icon": ".icon.backup.",
            "desktop_owner": "applications/.waveguide-generator.owner.backup.",
            "icon_owner": "apps/.waveguide-generator.owner.backup.",
            "link": ".link.backup.",
        }
        real_mv = shutil.which("mv")
        (bin_dir / "mv").write_text(
            '#!/bin/sh\n[ "$1" = "--" ] && shift\n'
            f'case "$1" in *"{patterns[restore_failure]}"*) exit 1;; esac\n'
            f'exec "{real_mv}" "$@"\n', encoding="utf-8",
        )
        (bin_dir / "mv").chmod(0o755)
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}


def test_a_late_failure_restores_the_app_and_all_desktop_integration(tmp_path: Path, env: dict[str, str]) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = integration_paths(env)
    # Distinct old contents expose a failed restore even if defaults would match.
    for name, path in paths.items():
        if name != "link":
            path.write_text(f"old {name}", encoding="utf-8")
    original_link = os.readlink(paths["link"])
    result = run(make_tarball(tmp_path / "v2", "two"), late_failure_env(tmp_path, env), "--update")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Restored the previous installation and desktop integration" in result.stdout
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    for name, path in paths.items():
        if name != "link":
            assert path.read_text(encoding="utf-8") == f"old {name}"
    assert os.readlink(paths["link"]) == original_link
    assert not list(Path(env["HOME"]).rglob("*.backup.*"))
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


@pytest.mark.parametrize("artifact", ("application", "desktop", "icon", "desktop_owner", "icon_owner", "link"))
def test_incomplete_rollback_returns_3_and_preserves_the_named_backup(
    tmp_path: Path, env: dict[str, str], artifact: str,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    paths = integration_paths(env)
    old_path = installed_dir(env) if artifact == "application" else paths[artifact]
    old_contents = None if artifact in ("application", "link") else old_path.read_bytes()
    old_link = os.readlink(old_path) if artifact == "link" else None
    result = run(
        make_tarball(tmp_path / "v2", "two"),
        late_failure_env(tmp_path, env, restore_failure=artifact), "--update",
    )
    assert result.returncode == 3, result.stdout + result.stderr
    backup_lines = [line for line in result.stderr.splitlines() if line.startswith("Its backup remains at: ")]
    assert len(backup_lines) == 1, result.stderr
    backup = Path(backup_lines[0].removeprefix("Its backup remains at: "))
    assert backup.parent == old_path.parent
    assert not old_path.exists() and not old_path.is_symlink()
    if artifact == "application":
        assert (backup / "version.txt").read_text(encoding="utf-8") == "one"
    elif artifact == "link":
        assert backup.is_symlink() and os.readlink(backup) == old_link
    else:
        assert backup.read_bytes() == old_contents
    assert "rollback was incomplete" in result.stderr
    assert "Restored the previous installation" not in result.stdout


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_a_signal_during_swap_returns_1_after_restoring_the_app(
    tmp_path: Path, env: dict[str, str], interrupt: signal.Signals,
) -> None:
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "two")
    paused = tmp_path / "paused"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    (bin_dir / "mv").write_text(
        f'#!/bin/sh\ncase "$*" in *".install."*) touch "{paused}"; sleep 60; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n', encoding="utf-8",
    )
    (bin_dir / "mv").chmod(0o755)
    proc = subprocess.Popen(
        ["/bin/bash", str(new / "install.sh"), "--skip-checks", "--update"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
        env={**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "swap never reached"
            time.sleep(0.05)
        assert not installed_dir(env).exists()
        os.killpg(proc.pid, interrupt)
        output, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    assert proc.returncode == 1, output
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert not list(installed_dir(env).parent.glob(".waveguide-generator.*"))


def test_update_with_unicode_and_escaped_desktop_paths(tmp_path: Path, env: dict[str, str]) -> None:
    prefix = tmp_path / 'Mes  Apps é "quoted" $dollar `tick` \\slash'
    assert run(make_tarball(tmp_path / "v1", "one"), env, "--prefix", str(prefix), "--no-launch").returncode == 0
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--prefix", str(prefix), "--update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (prefix / "waveguide-generator" / "version.txt").read_text(encoding="utf-8") == "two"
    entry = integration_paths(env)["desktop"].read_text(encoding="utf-8")
    assert 'Mes  Apps é' in entry
    assert '\\\\"quoted\\\\"' in entry and '\\\\$dollar' in entry and '\\\\`tick\\\\`' in entry
    assert '\\\\\\\\slash' in entry
