"""dmg-install.command: the interactive install and the unattended --update mode.

These run the real script against a tiny fake bundle signed ad hoc with the real
codesign, into temporary folders. macOS only: the script's dependencies are
ditto, xattr, codesign and PlistBuddy.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "installers" / "macos" / "dmg-install.command"
BUNDLE_ID = "is.hornlab.waveguide-generator-v2"
APP = "Waveguide Generator.app"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS packaging tools")


def make_app(path: Path, *, version: str, bundle_id: str = BUNDLE_ID, sign: bool = True) -> Path:
    contents = path / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    (contents / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>\n"
        "<key>CFBundleExecutable</key><string>app</string>\n"
        "<key>CFBundlePackageType</key><string>APPL</string>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    launcher = contents / "MacOS" / "app"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    (contents / "Resources" / "version.txt").write_text(version, encoding="utf-8")
    if sign:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(path)], check=True, capture_output=True)
    return path


def version_of(app: Path) -> str:
    return (app / "Contents" / "Resources" / "version.txt").read_text(encoding="utf-8")


@pytest.fixture
def dmg(tmp_path: Path) -> Path:
    """A fake mounted disk image: the installer beside the new version's app."""
    root = tmp_path / "dmg"
    root.mkdir()
    shutil.copy2(SCRIPT, root / SCRIPT.name)
    app = make_app(root / APP, version="new")
    # What a downloaded disk image hands its contents: a real quarantine flag.
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;00000000;test;", str(app)], check=True)
    return root


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    folder = tmp_path / "Apps"
    folder.mkdir()
    return make_app(folder / APP, version="old")


def run(dmg: Path, *args: str, path_prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(dmg / SCRIPT.name), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )


def shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return directory


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(".Waveguide"))


def test_update_replaces_the_exact_app_and_clears_quarantine(dmg: Path, installed: Path) -> None:
    result = run(dmg, "--update", str(installed))
    assert result.returncode == 0, result.stdout + result.stderr
    assert version_of(installed) == "new"
    assert leftovers(installed.parent) == []
    verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], capture_output=True)
    assert verify.returncode == 0
    quarantine = subprocess.run(["xattr", "-r", str(installed)], capture_output=True, text=True)
    assert "com.apple.quarantine" not in quarantine.stdout
    # Not vacuous: the disk image's own copy does carry the flag.
    source = subprocess.run(["xattr", str(dmg / APP)], capture_output=True, text=True)
    assert "com.apple.quarantine" in source.stdout


def test_update_mode_starts_nothing(dmg: Path, installed: Path, tmp_path: Path) -> None:
    marker = tmp_path / "opened"
    bin_dir = shim(tmp_path / "bin", "open", f'touch "{marker}"\n')
    assert run(dmg, "--update", str(installed), path_prefix=bin_dir).returncode == 0
    assert not marker.exists()


def test_update_needs_exactly_one_path(dmg: Path) -> None:
    assert run(dmg, "--update").returncode == 2


@pytest.mark.parametrize("case", ("missing", "file", "foreign_bundle"))
def test_update_refuses_a_target_that_is_not_our_app(dmg: Path, tmp_path: Path, case: str) -> None:
    folder = tmp_path / "Apps"
    folder.mkdir()
    target = folder / APP
    if case == "file":
        target.write_text("not a bundle", encoding="utf-8")
    elif case == "foreign_bundle":
        make_app(target, version="old", bundle_id="com.example.other")
    result = run(dmg, "--update", str(target))
    assert result.returncode != 0
    assert leftovers(folder) == []
    if case == "foreign_bundle":
        assert version_of(target) == "old"


def test_update_refuses_a_relative_path(dmg: Path, installed: Path) -> None:
    assert run(dmg, "--update", "Apps/" + APP).returncode != 0
    assert version_of(installed) == "old"


def test_update_refuses_a_translocated_path(dmg: Path, tmp_path: Path) -> None:
    target = make_app(tmp_path / "AppTranslocation" / "ABC" / "d" / APP, version="old")
    result = run(dmg, "--update", str(target))
    assert result.returncode != 0
    assert "translocated" in result.stdout
    assert version_of(target) == "old"


def test_update_never_falls_back_when_the_parent_is_unwritable(dmg: Path, installed: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    installed.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        env_home = {**os.environ, "HOME": str(home)}
        result = subprocess.run(
            ["/bin/bash", str(dmg / SCRIPT.name), "--update", str(installed)],
            capture_output=True, text=True, env=env_home, stdin=subprocess.DEVNULL,
        )
    finally:
        installed.parent.chmod(stat.S_IRWXU)
    assert result.returncode != 0
    assert version_of(installed) == "old"
    assert not (home / "Applications").exists()


def test_update_rejects_a_tampered_staged_copy_and_does_not_resign(dmg: Path, installed: Path, tmp_path: Path) -> None:
    log = tmp_path / "codesign.log"
    real_ditto = shutil.which("ditto")
    real_codesign = shutil.which("codesign")
    bin_dir = shim(
        tmp_path / "bin",
        "ditto",
        f'"{real_ditto}" "$@" || exit $?\n'
        'echo tampered >> "${@: -1}/Contents/Resources/version.txt"\n',
    )
    shim(bin_dir, "codesign", f'echo "$@" >> "{log}"\nexec "{real_codesign}" "$@"\n')
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode != 0
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls and all(line.startswith("--verify") for line in calls), calls


def test_update_restores_the_old_app_when_the_final_rename_fails(dmg: Path, installed: Path, tmp_path: Path) -> None:
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin",
        "mv",
        'case "$1" in *".new."*) echo "injected failure" >&2; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode != 0
    assert "Restored the previous installation" in result.stdout
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_update_leaves_the_old_app_when_the_copy_fails(dmg: Path, installed: Path, tmp_path: Path) -> None:
    bin_dir = shim(tmp_path / "bin", "ditto", "exit 1\n")
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode != 0
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_interactive_install_still_installs_into_a_named_folder(dmg: Path, tmp_path: Path) -> None:
    folder = tmp_path / "Apps"
    folder.mkdir()
    result = run(dmg, str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    assert version_of(folder / APP) == "new"


def test_interactive_install_keeps_the_old_app_until_the_copy_is_verified(dmg: Path, installed: Path, tmp_path: Path) -> None:
    bin_dir = shim(tmp_path / "bin", "ditto", "exit 1\n")
    result = run(dmg, str(installed.parent), path_prefix=bin_dir)
    assert result.returncode != 0
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_interactive_install_replaces_an_existing_app(dmg: Path, installed: Path) -> None:
    result = run(dmg, str(installed.parent))
    assert result.returncode == 0, result.stdout + result.stderr
    assert version_of(installed) == "new"
    assert leftovers(installed.parent) == []


def test_update_refuses_to_run_as_root(dmg: Path, installed: Path, tmp_path: Path) -> None:
    bin_dir = shim(tmp_path / "bin", "id", "echo 0\n")
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1
    assert version_of(installed) == "old"


def failing_swap_shim(tmp_path: Path, *, restore_too: bool) -> Path:
    real_mv = shutil.which("mv")
    patterns = '*".new."*|*".previous."*' if restore_too else '*".new."*'
    return shim(
        tmp_path / "bin",
        "mv",
        f'case "$1" in {patterns}) echo "injected failure" >&2; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )


def test_incomplete_rollback_has_its_own_exit_status_and_names_the_backup(dmg: Path, installed: Path, tmp_path: Path) -> None:
    bin_dir = failing_swap_shim(tmp_path, restore_too=True)
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 3, result.stdout + result.stderr
    backups = [p for p in installed.parent.iterdir() if ".previous." in p.name]
    assert len(backups) == 1 and str(backups[0]) in result.stderr
    assert version_of(backups[0]) == "old"
    assert not installed.exists()


def test_a_clean_rollback_is_still_exit_status_1(dmg: Path, installed: Path, tmp_path: Path) -> None:
    result = run(dmg, "--update", str(installed), path_prefix=failing_swap_shim(tmp_path, restore_too=False))
    assert result.returncode == 1


def test_a_signal_between_the_two_renames_restores_the_old_app(dmg: Path, installed: Path, tmp_path: Path) -> None:
    paused = tmp_path / "paused"
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin",
        "mv",
        f'case "$1" in *".new."*) touch "{paused}"; sleep 60; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.Popen(
        ["/bin/bash", str(dmg / SCRIPT.name), "--update", str(installed)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.time() + 60
        while not paused.exists():
            assert proc.poll() is None and time.time() < deadline, "the script never reached the swap"
            time.sleep(0.05)
        # Between the renames: the old app is aside and the target is empty.
        assert not installed.exists()
        os.killpg(proc.pid, signal.SIGTERM)
        output, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    assert proc.returncode not in (0, 3), output
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_update_sweeps_stale_hidden_leftovers_but_not_symlinks(dmg: Path, installed: Path, tmp_path: Path) -> None:
    folder = installed.parent
    stale_new = make_app(folder / f".{APP}.new.111", version="junk", sign=False)
    stale_prev = make_app(folder / f".{APP}.previous.222", version="junk", sign=False)
    keep = tmp_path / "precious"
    keep.mkdir()
    link = folder / f".{APP}.new.333"
    link.symlink_to(keep)
    unrelated = folder / ".Other.app.new.444"
    unrelated.mkdir()
    assert run(dmg, "--update", str(installed)).returncode == 0
    assert not stale_new.exists() and not stale_prev.exists()
    assert link.is_symlink() and keep.exists()
    assert unrelated.exists()
