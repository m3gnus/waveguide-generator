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


@pytest.fixture(autouse=True)
def check_generated_shell_shims(tmp_path: Path):
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and "bin" in path.parent.name:
            if path.read_bytes().startswith((b"#!/bin/sh\n", b"#!/bin/bash\n")):
                result = subprocess.run(["/bin/sh", "-n", str(path)], capture_output=True, text=True)
                assert result.returncode == 0, f"{path.name}: {result.stderr}"


def installer_process_signals() -> None:
    """Use catchable signals even when the suite broker started with SIG_IGN.

    POSIX sh cannot install a handler for a signal ignored on entry. Set the
    installer launch contract explicitly instead of inheriting the test runner.
    """
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)


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
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(path)], preexec_fn=installer_process_signals, check=True, capture_output=True)
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
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;00000000;test;", str(app)], preexec_fn=installer_process_signals, check=True)
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
        ["/bin/sh", str(dmg / SCRIPT.name), *args], preexec_fn=installer_process_signals,
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )


def shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    if name == "mv":
        # Match the source, preserving move options for the real rename underneath.
        body = 'source=""\nfor arg do case "$arg" in -*) ;; *) source="$arg"; break;; esac; done\n' + body.replace('case "$*" in', 'case "$source" in')
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
    verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], preexec_fn=installer_process_signals, capture_output=True)
    assert verify.returncode == 0
    quarantine = subprocess.run(["xattr", "-r", str(installed)], preexec_fn=installer_process_signals, capture_output=True, text=True)
    assert "com.apple.quarantine" not in quarantine.stdout
    # Not vacuous: the disk image's own copy does carry the flag.
    source = subprocess.run(["xattr", str(dmg / APP)], preexec_fn=installer_process_signals, capture_output=True, text=True)
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
            ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)], preexec_fn=installer_process_signals,
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
        f'case "$*" in *".new."*) touch "{paused}"; exec sleep 60;; esac\n'
        f'exec "{real_mv}" "$@"\n',
    )
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.Popen(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)], preexec_fn=installer_process_signals,
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
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    assert proc.returncode == 1, output
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_update_sweeps_empty_debris_but_keeps_application_bundles_and_symlinks(dmg: Path, installed: Path, tmp_path: Path) -> None:
    folder = installed.parent
    stale_new = make_app(folder / f".{APP}.new.111", version="junk", sign=False)
    stale_prev = make_app(folder / f".{APP}.previous.222", version="junk", sign=False)
    debris = folder / f".{APP}.new.000"
    debris.mkdir()
    keep = tmp_path / "precious"
    keep.mkdir()
    link = folder / f".{APP}.new.333"
    link.symlink_to(keep)
    unrelated = folder / ".Other.app.new.444"
    unrelated.mkdir()
    assert run(dmg, "--update", str(installed)).returncode == 0
    assert stale_new.exists() and stale_prev.exists()
    assert not debris.exists()
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
    create = (f'mkdir -p "{occupied}"; printf precious > "{occupied}/keep"'
              if kind == "directory" else f'ln -s "{precious}" "{occupied}"')
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
    if kind == "directory":
        assert (backup / "keep").read_text() == "precious"
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
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)], preexec_fn=installer_process_signals,
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


# Deterministic pauses use the same temporary-file/process-group mechanism as
# the earlier mv-shim tests. Instrument only the shipped copy, never the source.
BOUNDARIES = (
    "staged", "prepared", "displace_intent", "post_displace", "displaced",
    "install_intent", "post_install", "installed", "evacuate_intent",
    "post_evacuate", "evacuated", "restore_intent", "post_restore", "restored",
)
RECOVERY_BOUNDARIES = frozenset(BOUNDARIES[8:])


def instrument_boundaries(script: Path, row: int, boundary: str, paused: Path, release: Path, side: str = "after") -> None:
    import re

    body = script.read_text(encoding="utf-8")
    hook = (
        'state_boundary() {\n'
        f'    [ "$1:$2" = "{row}:{boundary}" ] || return 0\n'
        f'    touch "{paused}"\n'
        f'    while [ ! -e "{release}" ]; do sleep 0.01; done\n'
        '}\n'
    )
    body = body.replace("set -u\n", "set -u\n" + hook, 1)
    row_expr = '"$i"' if "STATE[i]=" in body else "0"
    def state_hook(m):
        if m[3] == "staged":
            return m[0]
        hook_line = f"{m[1]}state_boundary {row_expr} {m[3]}"
        state_line = f"{m[1]}{m[2]}"
        return f"{hook_line}\n{state_line}" if side == "before" else f"{state_line}\n{hook_line}"
    body = re.sub(
        r"^(\s*)(STATE(?:\[i\])?=([a-z_]+))$",
        state_hook,
        body, flags=re.MULTILINE,
    )
    needle = '    same_object "${STAGED[i]}" "${NEW_ID[i]}" || fail' if row_expr != "0" else 'same_object "$STAGED_PATH" "$NEW_ID" || fail'
    body = body.replace(needle, f'    state_boundary {row_expr} staged\n' + needle, 1)
    if boundary == "cleanup_entry":
        body = body.replace("cleanup() {\n", "cleanup() {\n    state_boundary 0 cleanup_entry\n", 1)
    if boundary in RECOVERY_BOUNDARIES:
        body = body.replace("COMMITTED=1\n", "exit 1\n", 1)
    elif boundary == "committed":
        body = body.replace("COMMITTED=1\n", "COMMITTED=1\nstate_boundary 0 committed\n", 1)
    script.write_text(body, encoding="utf-8")


def state_move_shim(bin_dir: Path, paths: list[Path], row: int, boundary: str, paused: Path, release: Path) -> None:
    """Pause after the real rename but before the installer can record its result."""
    bin_dir.mkdir(exist_ok=True)
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import pathlib, subprocess, sys, time\n'
        f'paths = {[str(p) for p in paths]!r}\n'
        'args = [arg for arg in sys.argv[1:] if not arg.startswith("-")]\n'
        'source, destination = args[-2:]\n'
        'phase, index = "", -1\n'
        'if source in paths:\n'
        '    index = paths.index(source)\n'
        '    phase = "post_displace" if (".backup." in destination or ".previous." in destination) else "post_evacuate"\n'
        'elif destination in paths:\n'
        '    index = paths.index(destination)\n'
        '    phase = "post_restore" if (".backup." in source or ".previous." in source) else "post_install"\n'
        f'result = subprocess.run([{real_mv!r}, *sys.argv[1:]])\n'
        f'if result.returncode == 0 and (index, phase) == ({row}, {boundary!r}):\n'
        f'    pathlib.Path({str(paused)!r}).touch()\n'
        f'    while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n'
        'sys.exit(result.returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)


def signal_paused_process(command: list[str], env: dict[str, str], paused: Path, release: Path,
                          interrupt: signal.Signals = signal.SIGTERM, *, burst: bool = False) -> tuple[int, str]:
    proc = subprocess.Popen(
        command, preexec_fn=installer_process_signals, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not paused.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "boundary never reached"
            time.sleep(0.01)
        os.killpg(proc.pid, interrupt)
        release.touch()
        if burst:
            # Keep the group-interruption probe, then spread further signals
            # across recovery without repeatedly killing its move children.
            for _ in range(39):
                try:
                    os.kill(proc.pid, interrupt)
                except ProcessLookupError:
                    break
                time.sleep(.0005)
        output, _ = proc.communicate(timeout=15)
    finally:
        release.touch()
        # Kill only the session we created, also cleaning any surviving shim child.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
    return proc.returncode, output


def noninteractive_move_shim(bin_dir: Path, log: Path, fail_source: str, cleanup_marker: Path) -> None:
    """Inspect actual argv/fd 0 while the installer itself receives a pipe."""
    bin_dir.mkdir()
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, signal, stat, subprocess, sys\n'
        'args = sys.argv[1:]\n'
        'fd = os.fstat(0)\n'
        'safe = "-n" in args and stat.S_ISCHR(fd.st_mode) and fd.st_rdev == os.stat("/dev/null").st_rdev\n'
        f'cleanup = pathlib.Path({str(cleanup_marker)!r}).exists()\n'
        'signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT) if cleanup else (signal.SIGHUP, signal.SIGTERM)\n'
        'safe = safe and all((signal.getsignal(sig) == signal.SIG_IGN) == cleanup for sig in signals)\n'
        f'with open({str(log)!r}, "a") as stream: stream.write(str(safe) + " " + repr(args) + (" cleanup" if cleanup else " forward") + "\\n")\n'
        'if not safe: sys.exit(91)\n'
        'source = [arg for arg in args if not arg.startswith("-")][-2]\n'
        f'if {fail_source!r} in source: sys.exit(1)\n'
        f'sys.exit(subprocess.run([{real_mv!r}, *args]).returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("side", ("before", "after"))
@pytest.mark.slow
def test_every_row_recovers_at_every_state_boundary(dmg: Path, installed: Path, tmp_path: Path, boundary: str, side: str) -> None:
    old_inode = installed.stat().st_ino
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(dmg / SCRIPT.name, 0, boundary, paused, release, side)
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, [installed], 0, boundary, paused, release)
    code, output = signal_paused_process(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)],
        {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, paused, release, burst=True,
    )
    assert code == 1, output
    assert version_of(installed) == "old"
    assert installed.stat().st_ino == old_inode
    assert leftovers(installed.parent) == [], output
    assert not list(installed.rglob(f".{APP}.*"))
    assert subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], preexec_fn=installer_process_signals, capture_output=True).returncode == 0


def test_signal_after_commit_keeps_the_complete_new_installation(dmg: Path, installed: Path, tmp_path: Path) -> None:
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(dmg / SCRIPT.name, 0, "committed", paused, release)
    code, output = signal_paused_process(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)], dict(os.environ), paused, release,
    )
    assert code == 0, output
    assert version_of(installed) == "new"
    assert not list(installed.rglob(f".{APP}.*"))
    assert subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], preexec_fn=installer_process_signals, capture_output=True).returncode == 0


@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
def test_signal_after_displacement_before_bookkeeping(dmg: Path, installed: Path, tmp_path: Path, interrupt: signal.Signals) -> None:
    paused, release = tmp_path / "paused", tmp_path / "release"
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, [installed], 0, "post_displace", paused, release)
    code, output = signal_paused_process(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)],
        {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, paused, release, interrupt,
    )
    assert code == 1, output
    assert version_of(installed) == "old"
    assert leftovers(installed.parent) == []


def test_every_move_is_no_clobber_and_has_null_stdin(dmg: Path, installed: Path, tmp_path: Path) -> None:
    log = tmp_path / "moves"
    bin_dir = tmp_path / "bin"
    cleanup_marker = tmp_path / "cleanup-started"
    script = dmg / SCRIPT.name
    body = script.read_text()
    assert body.count('    CLEANING=1\n') == 1
    script.write_text(body.replace('    CLEANING=1\n', f'    CLEANING=1\n    : > {str(cleanup_marker)!r}\n', 1))
    noninteractive_move_shim(bin_dir, log, ".new.", cleanup_marker)
    result = subprocess.run(
        ["/bin/sh", str(dmg / SCRIPT.name), "--update", str(installed)], preexec_fn=installer_process_signals,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        input="must never reach mv\n", capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 3 and all(s.startswith("True ") for s in calls), calls
    assert any(s.endswith(" forward") for s in calls) and any(s.endswith(" cleanup") for s in calls), calls
    assert version_of(installed) == "old"


@pytest.mark.parametrize("interrupt", (None, signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
@pytest.mark.parametrize("blocked_step", ("evacuate", "restore"))
@pytest.mark.slow
def test_blocked_recovery_has_a_deadline_and_names_the_real_backup(dmg: Path, installed: Path, tmp_path: Path, blocked_step: str, interrupt: signal.Signals | None) -> None:
    script = dmg / SCRIPT.name
    target = installed
    command = ["/bin/sh", str(script), "--update", str(target)]
    process_env = dict(os.environ)
    # Force rollback with all new rows installed, and accelerate the SAME watchdog.
    body = script.read_text().replace("COMMITTED=1\n", "exit 1\n", 1).replace("sleep 5 &", "sleep 1 &", 1)
    script.write_text(body)
    old_inode = target.stat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "blocked-pids"
    real_mv = shutil.which("mv")
    wrapper = bin_dir / "mv"
    wrapper.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, signal, subprocess, sys, time\n'
        'source, destination = [a for a in sys.argv[1:] if not a.startswith("-")][-2:]\n'
        f'blocked = ({blocked_step!r} == "restore" and destination == {str(target)!r} and (".backup." in source or ".previous." in source)) or ({blocked_step!r} == "evacuate" and source == {str(target)!r} and (".install." in destination or ".new." in destination))\n'
        'if blocked:\n'
        '    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, signal.SIG_IGN)\n'
        f'    with open({str(log)!r}, "a") as stream: stream.write(str(os.getpid()) + "\\n")\n'
        '    time.sleep(60)\n'
        f'sys.exit(subprocess.run([{real_mv!r}, *sys.argv[1:]]).returncode)\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    proc = subprocess.Popen(command, preexec_fn=installer_process_signals, env={**process_env, "PATH": f"{bin_dir}{os.pathsep}{process_env['PATH']}"},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    started = time.monotonic()
    try:
        if interrupt is not None:
            deadline = time.monotonic() + 5
            while not log.exists():
                assert proc.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            os.killpg(proc.pid, interrupt)
        output, _ = proc.communicate(timeout=10)
        assert proc.returncode == 3, output
        assert time.monotonic() - started < 8
        prefix = 'The previous app is at: '
        lines = [s.removeprefix(prefix) for s in output.splitlines() if s.startswith(prefix)]
        assert len(lines) == 1, output
        backup = Path(lines[0])
        assert backup.stat().st_ino == old_inode
        assert version_of(backup) == "old"
        assert len(log.read_text().splitlines()) == 2
        for child in map(int, log.read_text().splitlines()):
            with pytest.raises(ProcessLookupError):
                os.kill(child, 0)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()


def test_recreated_target_after_displacement_reports_the_real_backup(dmg: Path, installed: Path, tmp_path: Path) -> None:
    old_inode = installed.stat().st_ino
    real_mv = shutil.which("mv")
    bin_dir = shim(
        tmp_path / "bin", "mv",
        f'if [ "$source" = "{installed}" ]; then\n'
        f'"{real_mv}" "$@" || exit $?\n'
        f'mkdir -p "{installed}/Contents"\necho racer > "{installed}/keep.txt"\nexit 0\nfi\n'
        f'exec "{real_mv}" "$@"\n',
    )
    result = run(dmg, "--update", str(installed), path_prefix=bin_dir)
    assert result.returncode == 3, result.stdout + result.stderr
    lines = [s.removeprefix("The previous app is at: ") for s in result.stderr.splitlines()
             if s.startswith("The previous app is at: ")]
    assert len(lines) == 1, result.stderr
    backup = Path(lines[0])
    assert backup.stat().st_ino == old_inode and version_of(backup) == "old"
    assert (installed / "keep.txt").read_text().strip() == "racer"
    assert "Restored" not in result.stdout


@pytest.mark.parametrize("boundary", ("install_intent", "post_install", "installed"))
@pytest.mark.slow
def test_first_install_interruption_removes_the_new_app(dmg: Path, tmp_path: Path, boundary: str) -> None:
    folder = tmp_path / "Apps"
    folder.mkdir()
    target = folder / APP
    paused, release = tmp_path / "paused", tmp_path / "release"
    instrument_boundaries(dmg / SCRIPT.name, 0, boundary, paused, release)
    bin_dir = tmp_path / "bin"
    state_move_shim(bin_dir, [target], 0, boundary, paused, release)
    code, output = signal_paused_process(
        ["/bin/sh", str(dmg / SCRIPT.name), str(folder)],
        {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, paused, release,
    )
    assert code == 1, output
    assert not target.exists()
    assert leftovers(folder) == []
