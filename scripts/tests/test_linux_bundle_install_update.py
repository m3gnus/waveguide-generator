"""bundle-install.sh --update: replace an existing installation, never create one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    assert result.returncode != 0
    assert "no Waveguide Generator installation" in result.stdout
    assert not installed_dir(env).exists()


def test_update_refuses_a_directory_that_is_not_our_install(tmp_path: Path, env: dict[str, str]) -> None:
    foreign = installed_dir(env)
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_text("mine", encoding="utf-8")
    result = run(make_tarball(tmp_path / "v2", "two"), env, "--update")
    assert result.returncode != 0
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
    assert result.returncode != 0
    assert (installed_dir(env) / "version.txt").read_text(encoding="utf-8") == "one"
    assert sorted(p.name for p in installed_dir(env).parent.glob(".waveguide-generator.*")) == []
