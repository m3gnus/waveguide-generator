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
        ["/bin/sh", str(dmg / SCRIPT.name), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )


def shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    if name == "mv":
        # Match the source, preserving -n for the real rename underneath.
        body = 'source="$1"\n[ "$source" = "-n" ] && source="$2"\n' + body.replace('case "$*" in', 'case "$source" in')
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
            ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)],
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
        'case "$*" in *".new."*) echo "injected failure" >&2; exit 1;; esac\n'
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
        f'case "$*" in {patterns}) echo "injected failure" >&2; exit 1;; esac\n'
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
        f'case "$*" in *".new."*) touch "{paused}"; sleep 60; exit 1;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.Popen(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)],
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
    assert proc.returncode == 1, output
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


@pytest.mark.parametrize("with_contents", (False, True))
def test_a_directory_appearing_during_the_final_rename_is_not_success(
    dmg: Path, installed: Path, tmp_path: Path, with_contents: bool,
) -> None:
    real_mv = shutil.which("mv")
    # Appear after the script's existence check, inside the mv test seam.
    # A Contents directory alone must not make the post-check accept nesting.
    create_contents = f'mkdir -p "{installed}/Contents"\n' if with_contents else ""
    bin_dir = shim(
        tmp_path / "bin", "mv",
        'case "$*" in *".new."*)\n'
        f'mkdir -p "{installed}"\n{create_contents}'
        f'echo racer > "{installed}/keep.txt"\n;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Installed:" not in result.stdout
    assert (installed / "keep.txt").read_text(encoding="utf-8").strip() == "racer"
    backups = list(installed.parent.glob(f".{APP}.previous.*"))
    assert len(backups) == 1
    assert version_of(backups[0]) == "old"
    assert str(backups[0]) in result.stderr


def test_a_no_clobber_rename_that_does_nothing_is_a_clean_failure(
    dmg: Path, installed: Path, tmp_path: Path,
) -> None:
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin", "mv",
        'case "$*" in *".new."*) exit 0;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


@pytest.mark.parametrize("kind", ("directory", "symlink"))
def test_an_occupied_backup_name_is_refused_before_displacing_the_app(
    dmg: Path, installed: Path, tmp_path: Path, kind: str,
) -> None:
    precious = make_app(tmp_path / "precious", version="precious", sign=False)
    backup_record = tmp_path / "backup-path"
    occupied = f'{installed.parent}/.{APP}.previous.$PPID'
    create = f'mkdir -p "{occupied}"' if kind == "directory" else f'ln -s "{precious}" "{occupied}"'
    # id runs before the sweep and knows its parent installer PID.
    bin_dir = shim(
        tmp_path / "bin", "id",
        f'{create}\nprintf "%s" "{occupied}" > "{backup_record}"\necho 501\n',
    )
    real_rm = shutil.which("rm")
    shim(bin_dir, "rm", 'case "$*" in *".previous."*) exit 1;; esac\n' f'exec "{real_rm}" "$@"\n')
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "still occupied" in result.stdout
    assert version_of(installed) == "old"
    backup = Path(backup_record.read_text(encoding="utf-8"))
    assert backup.exists()
    assert not (backup / APP).exists()
    assert version_of(precious) == "precious"


def test_failure_to_remove_the_old_backup_warns_after_a_successful_install(
    dmg: Path, installed: Path, tmp_path: Path,
) -> None:
    real_rm = shutil.which("rm")
    bin_dir = shim(
        tmp_path / "bin", "rm",
        'case "$*" in *".previous."*) exit 1;; esac\n'
        f'exec "{real_rm}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert version_of(installed) == "new"
    backups = list(installed.parent.glob(f".{APP}.previous.*"))
    assert len(backups) == 1 and version_of(backups[0]) == "old"
    assert "WARNING: installed successfully" in result.stderr
    assert str(backups[0]) in result.stderr


@pytest.mark.parametrize("device", ("/dev/disk42s1", "//user@host/My Share"))
def test_read_only_volume_parsing_preserves_spaces_and_unicode(
    dmg: Path, tmp_path: Path, device: str,
) -> None:
    mountpoint = tmp_path / "My  Disque é"
    installed = make_app(mountpoint / "Apps" / APP, version="old")
    bin_dir = shim(
        tmp_path / "bin", "df",
        "cat <<'DF'\nFilesystem 1024-blocks Used Available Capacity Mounted on\n"
        f"{device} 1000 100 900 10% {mountpoint}\nDF\n",
    )
    shim(bin_dir, "mount", f"printf '%s\\n' '{device} on {mountpoint} (smbfs, read-only, mounted by user)'\n")
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "read-only volume" in result.stdout
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_unknown_volume_output_refuses_without_touching_the_app(dmg: Path, installed: Path, tmp_path: Path) -> None:
    bin_dir = shim(tmp_path / "bin", "df", "echo unparseable\nexit 1\n")
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1
    assert "Could not determine the volume" in result.stdout
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_update_into_a_parent_with_spaces_and_unicode(dmg: Path, tmp_path: Path) -> None:
    installed = make_app(tmp_path / "Mes  Apps é" / APP, version="old")
    result = run(dmg, "--update", str(installed))
    assert result.returncode == 0, result.stdout + result.stderr
    assert version_of(installed) == "new"
    assert leftovers(installed.parent) == []


def test_a_directory_appearing_during_restore_reports_the_actual_backup_location(
    dmg: Path, installed: Path, tmp_path: Path,
) -> None:
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin", "mv",
        'case "$*" in\n*".new."*) exit 1;;\n*".previous."*)\n'
        f'mkdir -p "{installed}/Contents"\necho racer > "{installed}/keep.txt"\n;;\nesac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Restored the previous installation" not in result.stdout
    backups = list(installed.glob(f".{APP}.previous.*"))
    assert len(backups) == 1 and version_of(backups[0]) == "old"
    assert str(backups[0]) in result.stderr
    assert (installed / "keep.txt").read_text(encoding="utf-8").strip() == "racer"


def test_a_volume_missing_from_mount_output_refuses_without_touching_the_app(
    dmg: Path, installed: Path, tmp_path: Path,
) -> None:
    bin_dir = shim(
        tmp_path / "bin", "df",
        "printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on' "
        "'/dev/disk42s1 1000 100 900 10% /Volumes/Unknown'\n",
    )
    shim(bin_dir, "mount", "echo '/dev/disk1 on / (apfs, read-only)'\n")
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not identify the mounted volume" in result.stdout
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_a_backup_collision_during_verification_leaves_the_old_app_in_place(
    dmg: Path, installed: Path, tmp_path: Path,
) -> None:
    record = tmp_path / "backup-path"
    real_codesign = shutil.which("codesign")
    bin_dir = shim(
        tmp_path / "bin", "codesign",
        f'backup="{installed.parent}/.{APP}.previous.$PPID"\n'
        f'mkdir "$backup"\nprintf "%s" "$backup" > "{record}"\n'
        f'exec "{real_codesign}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "still occupied" in result.stdout
    assert version_of(installed) == "old"
    backup = Path(record.read_text(encoding="utf-8"))
    assert backup.is_dir() and list(backup.iterdir()) == []
    assert "Restored" not in result.stdout


@pytest.mark.parametrize("with_contents", (False, True))
def test_a_collision_during_displacement_reports_the_real_old_app(
    dmg: Path, installed: Path, tmp_path: Path, with_contents: bool,
) -> None:
    real_mv = shutil.which("mv")
    create_contents = 'mkdir -p "$3/Contents"\n' if with_contents else ''
    bin_dir = shim(
        tmp_path / "bin", "mv",
        'case "$*" in\n'
        '*".previous."*) exit 1;;\n'
        '*".new."*) exit 1;;\n'
        '*) mkdir -p "$3"\n' + create_contents + ';;\nesac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Could not move the existing installation aside" in result.stdout
    assert "Restored" not in result.stdout
    assert not installed.exists()
    backup_lines = [line for line in result.stderr.splitlines() if line.startswith("The previous app is at: ")]
    assert len(backup_lines) == 1, result.stderr
    backup = Path(backup_lines[0].removeprefix("The previous app is at: "))
    assert backup.name == APP and backup.parent.parent == installed.parent
    assert version_of(backup) == "old"


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_a_signal_during_cleanup_allows_the_old_app_to_be_restored(
    dmg: Path, installed: Path, tmp_path: Path, interrupt: signal.Signals,
) -> None:
    paused = tmp_path / "cleanup-paused"
    release = tmp_path / "cleanup-release"
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin", "mv",
        'case "$*" in\n*".new."*) exit 1;;\n*".previous."*)\n'
        f'touch "{paused}"\nwhile [ ! -e "{release}" ]; do sleep 0.05; done\n;;\nesac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    proc = subprocess.Popen(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "cleanup never reached"
            time.sleep(0.05)
        assert not installed.exists()
        os.killpg(proc.pid, interrupt)
        release.touch()
        output, _ = proc.communicate(timeout=15)
    finally:
        release.touch()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    assert proc.returncode == 1, output
    assert "Restored the previous installation" in output
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []
