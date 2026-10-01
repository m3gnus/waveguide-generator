"""Faults around the installer swap: run shipped scripts and inspect real objects."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.tests import test_dmg_install_update as mac
from scripts.tests import test_linux_bundle_install_update as linux


@dataclass
class Install:
    platform: str
    script: Path
    target: Path
    env: dict[str, str]
    paths: list[Path]

    @property
    def command(self) -> list[str]:
        if self.platform == "linux":
            return ["/bin/bash", str(self.script), "--skip-checks", "--update"]
        return ["/bin/sh", str(self.script), "--update", str(self.target)]

    @property
    def lock(self) -> Path:
        return self.target.parent / f".{self.target.name}.install.lock"

    def run(self, env: dict[str, str] | None = None, *, interactive: bool = False):
        command = ["/bin/sh", str(self.script), str(self.target.parent)] if interactive else self.command
        return subprocess.run(command, env=env or self.env, preexec_fn=linux.installer_process_signals,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def version(self, path: Path | None = None) -> str:
        base = path or self.target
        return (base / ("version.txt" if self.platform == "linux" else "Contents/Resources/version.txt")).read_text()

    def hook(self, boundary: str, paused: Path, release: Path, row: int = 0) -> None:
        module = linux if self.platform == "linux" else mac
        module.instrument_boundaries(self.script, row, boundary, paused, release)
        if boundary == "locked":
            import re
            body = self.script.read_text()
            body = re.sub(r"^([ \t]*)acquire_lock$", r"\1acquire_lock\n\1state_boundary 0 locked", body, flags=re.MULTILINE)
            self.script.write_text(body)


@pytest.fixture(params=[
    pytest.param("linux", marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")),
    pytest.param("macos", marks=pytest.mark.skipif(sys.platform != "darwin", reason="macOS packaging tools")),
])
def install(request, tmp_path: Path) -> Install:
    platform = request.param
    if platform == "linux":
        home = tmp_path / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "share")}
        old = linux.make_tarball(tmp_path / "v1", "old")
        result = linux.run(old, env, "--no-launch")
        assert result.returncode == 0, result.stdout + result.stderr
        new = linux.make_tarball(tmp_path / "v2", "new")
        target = linux.installed_dir(env)
        return Install(platform, new / "install.sh", target, env, [target, *linux.integration_paths(env).values()])
    dmg = tmp_path / "dmg"
    dmg.mkdir()
    shutil.copy2(mac.SCRIPT, dmg / mac.SCRIPT.name)
    mac.make_app(dmg / mac.APP, version="new")
    target = mac.make_app(tmp_path / "Apps" / mac.APP, version="old")
    return Install(platform, dmg / mac.SCRIPT.name, target, dict(os.environ), [target])


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def move_shim(tmp_path: Path, install: Install, body: str) -> dict[str, str]:
    """The shim injects immediately before/after a real no-clobber move."""
    directory = tmp_path / "bin"
    directory.mkdir(exist_ok=True)
    real_mv = shutil.which("mv")
    wrapper = directory / "mv"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, subprocess, sys, time\n"
        "args = sys.argv[1:]\n"
        "source, destination = [a for a in args if not a.startswith('-')][-2:]\n"
        + body + f"\nos.execv({real_mv!r}, [{real_mv!r}, *args])\n"
    )
    wrapper.chmod(0o755)
    real_ln = shutil.which("ln")
    link_wrapper = directory / "ln"
    link_wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, subprocess, sys, time\n"
        "args = sys.argv[1:]\n"
        f"if '-P' not in args: os.execv({real_ln!r}, [{real_ln!r}, *args])\n"
        "source, destination = [a for a in args if not a.startswith('-')][-2:]\n"
        + body + f"\nos.execv({real_ln!r}, [{real_ln!r}, *args])\n"
    )
    link_wrapper.chmod(0o755)
    return {**install.env, "PATH": f"{directory}{os.pathsep}{install.env['PATH']}"}


def spawn(install: Install, env: dict[str, str] | None = None, *, stdin=subprocess.DEVNULL, interactive=False):
    command = ["/bin/sh", str(install.script), str(install.target.parent)] if interactive else install.command
    return subprocess.Popen(command, env=env or install.env, preexec_fn=linux.installer_process_signals,
                            stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)


def wait_marker(proc, marker: Path) -> None:
    deadline = time.monotonic() + 15
    while not marker.exists():
        assert proc.poll() is None, proc.communicate()[0]
        assert time.monotonic() < deadline, "intended operation never reached"
        time.sleep(0.01)


def stop(proc) -> None:
    # A shim can outlive its parent; stop only this test's recorded process group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.stdout is not None and proc.stdout.closed:
        proc.wait(timeout=10)
    else:
        proc.communicate(timeout=10)


def fail_app_install(tmp_path: Path, install: Install, extra: str = "") -> dict[str, str]:
    return move_shim(tmp_path, install, extra + f"""
if destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):
    sys.exit(1)
""")


@pytest.mark.slow
@pytest.mark.parametrize("interrupt", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
@pytest.mark.parametrize("entry", ("failure", "signal"))
def test_signal_at_cleanup_entry_keeps_recovery(install: Install, tmp_path: Path, interrupt, entry) -> None:
    paused, release = tmp_path / "paused", tmp_path / "release"
    original = identity(install.target)
    install.hook("cleanup_entry", paused, release)
    # The first signal enters EXIT cleanup; the injected second lands at its
    # cleanup entry, using the existing boundary hook.
    extra = "" if entry == "failure" else "    os.kill(os.getppid(), signal.SIGTERM)\n"
    env = move_shim(tmp_path, install, f"""
if destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):
{extra}    sys.exit(1)
""")
    proc = spawn(install, env)
    try:
        wait_marker(proc, paused)
        os.kill(proc.pid, interrupt)
        release.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == 1, output
        assert identity(install.target) == original and install.version() == "old"
        assert not install.lock.exists()
    finally:
        release.touch()
        stop(proc)


@pytest.mark.slow
def test_closed_output_pipe_still_rolls_back(install: Install, tmp_path: Path) -> None:
    paused, release = tmp_path / "paused", tmp_path / "release"
    original = identity(install.target)
    env = move_shim(tmp_path, install, f"""
if destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):
    pathlib.Path({str(paused)!r}).touch()
    while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)
    sys.exit(1)
""")
    proc = spawn(install, env)
    try:
        wait_marker(proc, paused)
        assert not install.target.exists(), "previous app must actually be displaced"
        proc.stdout.close()
        release.touch()
        assert proc.wait(timeout=15) == 1
        assert identity(install.target) == original and install.version() == "old"
        assert not install.lock.exists()
    finally:
        release.touch()
        stop(proc)


@pytest.mark.slow
def test_interactive_failure_restores_before_the_close_prompt(install: Install, tmp_path: Path) -> None:
    if install.platform != "macos":
        pytest.skip("Finder's interactive close prompt is macOS only")
    import pty

    original = identity(install.target)
    env = fail_app_install(tmp_path, install)
    master, slave = pty.openpty()
    proc = spawn(install, env, stdin=slave, interactive=True)
    os.close(slave)
    output = b""
    try:
        deadline = time.monotonic() + 15
        while b"Press Return to close..." not in output:
            assert time.monotonic() < deadline and proc.poll() is None, output
            if select.select([proc.stdout], [], [], .1)[0]:
                output += os.read(proc.stdout.fileno(), 4096)
        assert identity(install.target) == original and install.version() == "old", output
        assert output.index(b"Restored the previous installation") < output.index(b"Press Return")
        assert not install.lock.exists(), "cleanup should release its lock before waiting for the user"
        os.write(master, b"\n")
        proc.communicate(timeout=10)
        assert proc.returncode == 1
    finally:
        stop(proc)
        os.close(master)


@pytest.mark.parametrize("phase", ("install", "restore"))
def test_regular_file_racer_is_never_overwritten(install: Install, tmp_path: Path, phase: str) -> None:
    # Linux reproduction used a desktop entry; macOS's app path can also race
    # with a regular file. Refusal must preserve both the file and the old object.
    target = install.paths[1] if install.platform == "linux" else install.target
    original = identity(target)
    reached = tmp_path / "racer-created"
    fail = install.paths[2] if install.platform == "linux" else install.target
    env = move_shim(tmp_path, install, f"""
backup = '.backup.' in source or '.previous.' in source
if destination == {str(target)!r} and backup == {phase == 'restore'!r}:
    pathlib.Path(destination).write_text('precious racer')
    pathlib.Path({str(reached)!r}).touch()
if {phase == 'restore'!r} and destination == {str(fail)!r} and not backup:
    sys.exit(1)
""")
    result = install.run(env)
    output = result.stdout + result.stderr
    assert reached.exists(), output
    assert result.returncode == 3, output
    assert not target.is_symlink() and target.read_text() == "precious racer"
    prefix = "Its backup remains at: " if install.platform == "linux" else "The previous app is at: "
    backups = [Path(line.removeprefix(prefix)) for line in output.splitlines() if line.startswith(prefix)]
    assert any(identity(path) == original for path in backups), output
    assert not install.lock.exists()


def test_command_symlink_racer_is_never_replaced(install: Install, tmp_path: Path) -> None:
    if install.platform != "linux":
        pytest.skip("Linux command integration")
    command = install.paths[-1]
    original = identity(command)
    reached = tmp_path / "racer-created"
    env = move_shim(tmp_path, install, f"""
if '.link.new.' in source and destination == {str(command)!r}:
    pathlib.Path(destination).write_text('precious unrelated command')
    pathlib.Path({str(reached)!r}).touch()
""")
    result = install.run(env)
    output = result.stdout + result.stderr
    assert reached.exists(), output
    assert result.returncode == 3, output
    assert not command.is_symlink() and command.read_text() == "precious unrelated command"
    backups = list(command.parent.glob(".waveguide-generator.link.backup.*"))
    assert len(backups) == 1 and identity(backups[0]) == original
    assert install.version() == "old" and not install.lock.exists()


@pytest.mark.parametrize("occupant", ("stage_root", "staged_app", "staged_file", "after_install", "backup"))
def test_cleanup_preserves_foreign_occupants(install: Install, tmp_path: Path, occupant: str) -> None:
    if install.platform == "macos" and occupant in ("stage_root", "staged_file"):
        pytest.skip("Linux has a separate staging root and integration files")
    record = tmp_path / "foreign-path"
    # Preserve the recorded object under a distinct name, avoiding accidental
    # inode reuse, then put foreign contents at the path cleanup would delete.
    dest = install.paths[-1] if occupant == "staged_file" else install.target
    real_mv = shutil.which("mv")
    env = move_shim(tmp_path, install, f"""
incoming = ('.install.' in source or '.new.' in source)
if destination == {str(dest)!r} and incoming:
    path = pathlib.Path(source)
    if {occupant!r} == 'after_install':
        subprocess.run([{real_mv!r}, *args], check=True)
    elif {occupant!r} == 'backup':
        subprocess.run([{real_mv!r}, *args], check=True)
        path = next(pathlib.Path(destination).parent.glob('.*.previous.*'))
        path.rename(str(path) + '.saved')
    else:
        if {occupant!r} == 'stage_root': path = path.parent
        path.rename(str(path) + '.saved')
    path.mkdir()
    (path / 'precious.txt').write_text('foreign staging occupant')
    pathlib.Path({str(record)!r}).write_text(str(path))
    sys.exit(0 if {occupant!r} in ('after_install', 'backup') else 1)
""")
    result = install.run(env)
    output = result.stdout + result.stderr
    foreign = Path(record.read_text())
    assert (foreign / "precious.txt").read_text() == "foreign staging occupant", output
    assert "leaving foreign" in output or "leaving occupied staging" in output, output
    if occupant == "after_install":
        assert result.returncode == 3, output
        assert install.version() == "new"
    elif occupant == "backup":
        assert result.returncode == 0 and install.version() == "new", output
    else:
        assert result.returncode == 1 and install.version() == "old", output
    assert not install.lock.exists()


@pytest.mark.slow
@pytest.mark.parametrize("step", ("displace", "install"))
@pytest.mark.parametrize("interrupt", (None, signal.SIGTERM))
def test_blocked_forward_move_is_bounded(install: Install, tmp_path: Path, step: str, interrupt) -> None:
    record = tmp_path / "blocked-pid"
    original = identity(install.target)
    env = move_shim(tmp_path, install, f"""
blocked = (source == {str(install.target)!r} and '.previous.' in destination) if {step!r} == 'displace' else (destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source))
if blocked:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pathlib.Path({str(record)!r}).write_text(str(os.getpid()))
    time.sleep(60)
""")
    proc = spawn(install, env)
    try:
        wait_marker(proc, record)
        started = time.monotonic()
        if interrupt is not None:
            os.kill(proc.pid, interrupt)  # parent only: reproduce deferred foreground traps
        output, _ = proc.communicate(timeout=8)
        assert proc.returncode == 1, output
        assert time.monotonic() - started < 7
        assert identity(install.target) == original and install.version() == "old"
        with pytest.raises(ProcessLookupError):
            os.kill(int(record.read_text()), 0)
        assert not install.lock.exists()
    finally:
        stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize("boundary", ("displaced", "installed", "committed"))
def test_concurrent_installer_refuses_while_lock_is_held(install: Install, tmp_path: Path, boundary: str) -> None:
    paused, release = tmp_path / "paused", tmp_path / "release"
    install.hook(boundary, paused, release, row=2 if install.platform == "linux" and boundary == "installed" else 0)
    proc = spawn(install)
    try:
        wait_marker(proc, paused)
        assert install.lock.is_dir()
        assert int((install.lock / "pid").read_text()) == proc.pid
        # A second copy must fail before preflight, sweep, or an installation
        # check mistakes the temporarily absent target for a missing installation.
        second = install.script.with_name("second-install.sh")
        shutil.copy2(linux.SCRIPT if install.platform == "linux" else mac.SCRIPT, second)
        other = Install(install.platform, second, install.target, install.env, install.paths)
        result = other.run()
        assert result.returncode == 4, result.stdout + result.stderr
        assert "installer" in result.stderr and "lock" in result.stderr
        assert install.lock.exists()
        os.kill(proc.pid, signal.SIGTERM)
        release.touch()
        output, _ = proc.communicate(timeout=15)
        expected = "new" if boundary == "committed" else "old"
        assert proc.returncode == (0 if boundary == "committed" else 1), output
        assert install.version() == expected and not install.lock.exists()
    finally:
        release.touch()
        stop(proc)


@pytest.mark.parametrize("owner", ("missing", "empty", "invalid", "live", "other_user", "dead", "unexpected_contents"))
def test_unverifiable_lock_is_left_for_manual_inspection(install: Install, owner: str) -> None:
    install.lock.mkdir()
    if owner != "missing":
        if owner == "empty":
            pid = ""
        elif owner == "other_user":
            pid = "1"
        elif owner == "invalid":
            pid = "not-a-pid"
        elif owner == "live":
            pid = str(os.getpid())
        else:
            child = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
            child.wait()
            pid = str(child.pid)
            (install.lock / "precious.txt").write_text("keep")
        (install.lock / "pid").write_text(pid)
    original = identity(install.target)
    before = snapshot(install.lock)
    result = install.run()
    assert result.returncode == 4, result.stdout + result.stderr
    assert install.lock.exists() and identity(install.target) == original
    assert snapshot(install.lock) == before
    assert str(install.lock) in result.stderr
    if owner != "missing":
        assert pid in result.stderr
    if owner == "unexpected_contents":
        assert (install.lock / "precious.txt").read_text() == "keep"


def test_a_refused_run_prints_one_command_that_removes_the_lock(install: Install) -> None:
    """Exit 4 must be actionable: nothing changed, the lock, and the one command."""
    install.lock.mkdir()
    (install.lock / "pid").write_text("4242")
    original = identity(install.target)
    result = install.run()
    assert result.returncode == 4, result.stdout + result.stderr
    assert "Nothing was changed." in result.stderr
    assert "by process 4242." in result.stderr and "no installer window is open" in result.stderr
    assert identity(install.target) == original and install.lock.exists()
    command = [line.strip() for line in result.stderr.splitlines() if line.strip().startswith("rm -f ")]
    assert len(command) == 1, result.stderr
    subprocess.run(["/bin/sh", "-c", command[0]], check=True)
    assert not install.lock.exists()
    again = install.run()
    assert again.returncode == 0, again.stdout + again.stderr


@pytest.mark.slow
def test_sweep_keeps_the_backup_reported_by_an_exit_3(install: Install, tmp_path: Path) -> None:
    if install.platform != "macos":
        pytest.skip("macOS start-of-run sweep")
    original = identity(install.target)
    env = fail_app_install(tmp_path, install, f"""
if destination == {str(install.target)!r} and '.new.' in source:
    pathlib.Path(destination).mkdir()
    (pathlib.Path(destination) / 'precious.txt').write_text('racer')
""")
    first = install.run(env)
    assert first.returncode == 3, first.stdout + first.stderr
    prefix = "The previous app is at: "
    backup = Path(next(line.removeprefix(prefix) for line in first.stderr.splitlines() if line.startswith(prefix)))
    assert identity(backup) == original and install.version(backup) == "old"
    # Also keep directories containing an app bundle, not just a bundle root.
    nested = install.target.parent / f".{mac.APP}.new.123"
    mac.make_app(nested / "Recovered.app", version="keep")
    ditto = tmp_path / "bin" / "ditto"
    ditto.write_text("#!/bin/sh\nexit 1\n")
    ditto.chmod(0o755)
    subprocess.run(["/bin/sh", "-n", str(ditto)], check=True)
    second = install.run(env, interactive=True)
    assert second.returncode == 1, second.stdout + second.stderr
    assert identity(backup) == original and install.version(backup) == "old"
    assert (nested / "Recovered.app").exists()
    assert (install.target / "precious.txt").read_text() == "racer"
    assert not install.lock.exists()


def test_a_signal_to_the_installer_alone_ends_a_long_copy(install: Install, tmp_path: Path) -> None:
    """An updater's TERM goes to the installer's PID only; the copy must not outlast it."""
    directory = tmp_path / "bin"
    directory.mkdir(exist_ok=True)
    marker = tmp_path / "copying"
    tool = "cp" if install.platform == "linux" else "ditto"
    real = shutil.which(tool)
    shim = directory / tool
    long_copy = f'touch "{marker}"; exec sleep 30'
    if install.platform == "linux":
        shim.write_text(f'#!/bin/sh\ncase "$1" in -a) {long_copy};; esac\nexec "{real}" "$@"\n')
    else:
        shim.write_text(f"#!/bin/sh\n{long_copy}\n")
    shim.chmod(0o755)
    original = identity(install.target)
    proc = spawn(install, {**install.env, "PATH": f"{directory}{os.pathsep}{install.env['PATH']}"})
    try:
        wait_marker(proc, marker)
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        output, _ = proc.communicate(timeout=10)
        assert time.monotonic() - started < 5, output
        assert proc.returncode == 1, output
        assert "Could not copy" not in output and "Could not stage" not in output
        assert identity(install.target) == original and install.version() == "old"
        assert not install.lock.exists()
    finally:
        stop(proc)


def test_different_device_staging_refuses_before_displacement(install: Install, tmp_path: Path) -> None:
    directory = tmp_path / "bin"
    directory.mkdir()
    original = identity(install.target)
    real_stat = shutil.which("stat")
    wrapper = directory / "stat"
    wrapper.write_text(
        f"#!{sys.executable}\nimport subprocess, sys\n"
        f"result = subprocess.run([{real_stat!r}, *sys.argv[1:]], capture_output=True, text=True)\n"
        "output = result.stdout\n"
        "path = sys.argv[-1]\n"
        "if ('.install.' in path or '.new.' in path) and output.strip().count(':') == 1:\n"
        "    output = '999999999:' + output.strip().split(':')[1] + '\\n'\n"
        "sys.stdout.write(output)\nsys.exit(result.returncode)\n"
    )
    wrapper.chmod(0o755)
    result = install.run({**install.env, "PATH": f"{directory}{os.pathsep}{install.env['PATH']}"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "different devices" in result.stdout + result.stderr
    assert identity(install.target) == original and install.version() == "old"
    assert not install.lock.exists()


def test_identity_includes_the_device(install: Install, tmp_path: Path) -> None:
    # Ask the shipped same_object helper whether equal inode numbers on
    # different devices are the same object. A stat shim models the second FS.
    body = install.script.read_text()
    functions = body[body.index("object_id() {"):body.index("same_device() {")]
    directory = tmp_path / "bin"
    directory.mkdir()
    wrapper = directory / "stat"
    wrapper.write_text("#!/bin/sh\nprintf '22:12345\\n'\n")
    wrapper.chmod(0o755)
    subprocess.run(["/bin/sh", "-n", str(wrapper)], check=True)
    script = tmp_path / "identity.sh"
    script.write_text("STAT_STYLE=gnu\n" + functions + f'\nsame_object "{install.target}" "11:12345"\n')
    result = subprocess.run(["/bin/sh", str(script)], env={**install.env, "PATH": f"{directory}{os.pathsep}{install.env['PATH']}"})
    assert result.returncode == 1


def test_regular_file_backup_restore_refuses_a_racer(install: Install, tmp_path: Path) -> None:
    if install.platform != "macos":
        pytest.skip("Linux's integration-file restore is covered separately")
    # Interactive macOS installation can encounter a regular file at the app
    # name. Its displaced predecessor needs the same no-clobber protection.
    saved_app = tmp_path / "saved-old.app"
    install.target.rename(saved_app)
    install.target.write_text("original regular file")
    original = identity(install.target)
    reached = tmp_path / "restore-reached"
    env = move_shim(tmp_path, install, f"""
if destination == {str(install.target)!r} and '.new.' in source:
    sys.exit(1)
if destination == {str(install.target)!r} and '.previous.' in source:
    pathlib.Path(destination).write_text('precious racer')
    pathlib.Path({str(reached)!r}).touch()
""")
    result = install.run(env, interactive=True)
    assert reached.exists(), result.stdout + result.stderr
    assert result.returncode == 3, result.stdout + result.stderr
    assert install.target.read_text() == "precious racer"
    backups = list(install.target.parent.glob(f".{mac.APP}.previous.*"))
    assert len(backups) == 1 and identity(backups[0]) == original
    assert backups[0].read_text() == "original regular file"
    assert install.version(saved_app) == "old" and not install.lock.exists()


@pytest.mark.parametrize("boundary", ("acquiring", "preflight"))
def test_signal_releases_the_lock_before_staging(install: Install, boundary: str) -> None:
    body = install.script.read_text()
    if boundary == "acquiring":
        # A signal during owner initialization must be deferred until the lock
        # can be identified and then released, without a missing-PID residue.
        body = body.replace("    LOCK_HELD=1\n", "    LOCK_HELD=1\n    kill -TERM $$\n", 1)
    else:
        body = body.replace("    acquire_lock\n", "    acquire_lock\n    kill -TERM $$\n") if install.platform == "macos" else body.replace("\nacquire_lock\n", "\nacquire_lock\nkill -TERM $$\n", 1)
    install.script.write_text(body)
    original = identity(install.target)
    result = install.run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert identity(install.target) == original and not install.lock.exists()


@pytest.mark.parametrize("alias", ("old", "new"))
def test_file_transfer_reconciles_a_failed_source_unlink(install: Install, tmp_path: Path, alias: str) -> None:
    if install.platform != "linux":
        pytest.skip("Linux has regular-file integration rows")
    target = install.paths[1]
    original = identity(target)
    original_data = target.read_bytes()
    directory = tmp_path / "bin"
    directory.mkdir()
    real_rm = shutil.which("rm")
    marker = tmp_path / "unlink-failed"
    wrapper = directory / "rm"
    source_match = f'path == {str(target)!r}' if alias == "old" else "path.endswith('.desktop') and '.waveguide-generator.' in path"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, pathlib, sys\n"
        "path = sys.argv[-1]\n"
        f"if {source_match} and not pathlib.Path({str(marker)!r}).exists():\n"
        f"    pathlib.Path({str(marker)!r}).touch()\n    sys.exit(1)\n"
        f"os.execv({real_rm!r}, [{real_rm!r}, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    result = install.run({**install.env, "PATH": f"{directory}{os.pathsep}{install.env['PATH']}"})
    assert marker.exists(), result.stdout + result.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert identity(target) == original and target.read_bytes() == original_data
    assert install.version() == "old"
    assert not list(target.parent.glob("*.backup.*")) and not install.lock.exists()


def snapshot(root: Path) -> dict:
    """Names, identity, metadata and contents; atime may change from reading PID."""
    return {str(p.relative_to(root)): (identity(p), p.lstat().st_mode, p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() else os.readlink(p) if p.is_symlink() else None)
            for p in [root, *root.rglob('*')]}


def assert_no_staging(install: Install) -> None:
    parents = {p.parent for p in install.paths}
    leftovers = [p for parent in parents for p in parent.glob('.waveguide-generator.*')
                 if p.name != '.waveguide-generator.owner' and '.previous.' not in p.name and '.backup.' not in p.name]
    if install.platform == 'macos':
        leftovers += list(install.target.parent.glob(f'.{mac.APP}.new.*'))
    assert not leftovers, leftovers
    assert not install.lock.exists()


def assert_truthful(install: Install, code: int, output: str) -> None:
    assert code in (0, 1, 3), output
    if code in (0, 1):
        assert install.target.exists(), output
        assert install.version() == ('new' if code == 0 else 'old'), output
    else:
        prefix = 'Its backup remains at: ' if install.platform == 'linux' else 'The previous app is at: '
        backup = Path(next(line.removeprefix(prefix) for line in output.splitlines() if line.startswith(prefix)))
        assert backup.exists() and install.version(backup) == 'old', output
    assert_no_staging(install)


@pytest.mark.slow
def test_fifty_consecutive_cleanup_signal_bursts(install: Install, tmp_path: Path) -> None:
    # Adapt the reviewers' failed-install / 40-TERM burst. Vary the offset in
    # separate runs; the script stays unmodified, the real rename is intercepted.
    ready, go = tmp_path / 'ready', tmp_path / 'go'
    original = identity(install.target)
    env = move_shim(tmp_path, install, f'''
if destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):
    pathlib.Path({str(ready)!r}).touch()
    while not pathlib.Path({str(go)!r}).exists(): time.sleep(.001)
    sys.exit(1)
''')
    for run in range(50):
        ready.unlink(missing_ok=True)
        go.unlink(missing_ok=True)
        proc = spawn(install, env)
        try:
            wait_marker(proc, ready)
            go.touch()
            time.sleep(run * .0002)
            for _ in range(40):
                try:
                    os.kill(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    break
                time.sleep(.0005 + (run % 5) * .0001)
            output, _ = proc.communicate(timeout=15)
            assert_truthful(install, proc.returncode, output)
            assert proc.returncode == 1 and identity(install.target) == original, output
        finally:
            go.touch()
            stop(proc)


@pytest.mark.slow
def test_fifty_signal_burst_offsets_from_start_to_release(install: Install, tmp_path: Path) -> None:
    # Signal scheduling is external. A builtin marker immediately after the
    # first handler excludes OS delivery before shell startup, which no shell
    # script can handle. Offsets extend through staging, swaps and cleanup.
    started = tmp_path / 'started'
    body = install.script.read_text()
    trap_line = "trap 'INTERRUPTED=1' HUP INT TERM QUIT\n"
    assert body.count(trap_line) == 1, 'readiness hook must follow the installed handler'
    body = body.replace(trap_line, trap_line + f": > {str(started)!r}\n", 1)
    install.script.write_text(body)
    # Fail at the forward install so repeated successful runs cannot change
    # our baseline; this gives an observable restoration during later offsets.
    env = fail_app_install(tmp_path, install)
    for offset in range(50):
        started.unlink(missing_ok=True)
        proc = spawn(install, env)
        try:
            wait_marker(proc, started)
            time.sleep(offset * .012)
            for _ in range(40):
                try:
                    os.kill(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    break
                time.sleep(.004)
            output, _ = proc.communicate(timeout=15)
            assert_truthful(install, proc.returncode, output)
        finally:
            stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize('point', ('entry', 'guarded', 'status', 'release_before', 'release_after'))
@pytest.mark.parametrize('committed', (False, True))
def test_signals_on_cleanup_lines_compute_the_status(install: Install, tmp_path: Path, point: str, committed: bool) -> None:
    keys = {'entry': 'cleanup() {\n', 'guarded': '    CLEANING=1\n',
            'status': '    local status="$EXIT_STATUS" i\n' if install.platform == 'linux' else '    status="$EXIT_STATUS"\n',
            'release_before': '    release_lock\n', 'release_after': '    release_lock\n'}
    key = keys[point]
    body = install.script.read_text()
    assert body.count(key) == 1
    injection = '    kill -HUP $$\n    kill -INT $$\n    kill -TERM $$\n'
    body = body.replace(key, injection + key if point == 'release_before' else key + injection, 1)
    install.script.write_text(body)
    result = install.run() if committed else install.run(fail_app_install(tmp_path, install))
    assert result.returncode == (0 if committed else 1), result.stdout + result.stderr
    assert_truthful(install, result.returncode, result.stdout + result.stderr)


@pytest.mark.slow
def test_cleanup_is_safe_when_reentered(install: Install, tmp_path: Path) -> None:
    # Re-enter the real cleanup after its guard. Removing the guard causes
    # recursion and skips restoration, so this tests behavior, not its spelling.
    body = install.script.read_text()
    assert body.count('    CLEANING=1\n') == 1
    install.script.write_text(body.replace('    CLEANING=1\n', '    CLEANING=1\n    cleanup\n', 1))
    result = install.run(fail_app_install(tmp_path, install))
    assert result.returncode == 1, result.stdout + result.stderr
    assert_truthful(install, result.returncode, result.stdout + result.stderr)


@pytest.mark.slow
@pytest.mark.parametrize('outcome', (0, 1, 3))
def test_group_signals_during_lock_release_leave_no_lock(install: Install, tmp_path: Path, outcome: int) -> None:
    ready, go = tmp_path / 'rm-ready', tmp_path / 'rm-go'
    directory = tmp_path / 'bin'
    directory.mkdir()
    rm = directory / 'rm'
    real_rm = shutil.which('rm')
    rm.write_text(f'''#!{sys.executable}
import os, pathlib, sys, time
if sys.argv[-1] == {str(install.lock / 'pid')!r}:
    pathlib.Path({str(ready)!r}).touch()
    while not pathlib.Path({str(go)!r}).exists(): time.sleep(.001)
os.execv({real_rm!r}, [{real_rm!r}, *sys.argv[1:]])
''')
    rm.chmod(0o755)
    env = {**install.env, 'PATH': f"{directory}{os.pathsep}{install.env['PATH']}"}
    if outcome:
        extra = '' if outcome == 1 else f"\nif destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):\n    pathlib.Path(destination).mkdir()\n"
        env = fail_app_install(tmp_path, install, extra)
    proc = spawn(install, env)
    try:
        wait_marker(proc, ready)
        for _ in range(40):
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(.001)
        go.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == outcome, output
        assert_truthful(install, proc.returncode, output)
    finally:
        go.touch()
        stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize('point', ('lock_mkdir', 'lock_identity', 'stage_mkdir', 'stage_identity',
                                 'temporary_root', 'temporary_desktop', 'temporary_backup', 'stage_link'))
def test_group_signals_during_resource_registration(install: Install, tmp_path: Path, point: str) -> None:
    # Pause AFTER the real creation/stat but before its child returns. Killing
    # that child must not turn our successful acquisition into a foreign lock
    # refusal, or strand a staged object before its identity is registered.
    if point.startswith('temporary_') or point == 'stage_link':
        if install.platform != 'linux':
            pytest.skip('Linux temporary files and command link')
    command, match = {
        'lock_mkdir': ('mkdir', "path.endswith('.install.lock')"),
        'lock_identity': ('stat', "path.endswith('.install.lock')"),
        'stage_mkdir': ('mkdir', "('.install.' in path and path.endswith('/waveguide-generator')) or '.app.new.' in path"),
        'stage_identity': ('stat', "('.install.' in path and path.endswith('/waveguide-generator')) or '.app.new.' in path"),
        'temporary_root': ('mktemp', "'.install.XXXXXX' in path"),
        'temporary_desktop': ('mktemp', "path.endswith('.XXXXXX.desktop')"),
        'temporary_backup': ('mktemp', "'.previous.XXXXXX' in path"),
        'stage_link': ('ln', "'.link.new.' in path"),
    }[point]
    ready, go = tmp_path / 'created-ready', tmp_path / 'created-go'
    directory = tmp_path / 'bin'
    directory.mkdir()
    wrapper = directory / command
    real = shutil.which(command)
    wrapper.write_text(f'''#!{sys.executable}
import pathlib, subprocess, sys, time
result = subprocess.run([{real!r}, *sys.argv[1:]])
path = sys.argv[-1]
if result.returncode == 0 and ({match}) and not pathlib.Path({str(ready)!r}).exists():
    pathlib.Path({str(ready)!r}).touch()
    while not pathlib.Path({str(go)!r}).exists(): time.sleep(.001)
sys.exit(result.returncode)
''')
    wrapper.chmod(0o755)
    original = identity(install.target)
    proc = spawn(install, {**install.env, 'PATH': f"{directory}{os.pathsep}{install.env['PATH']}"})
    try:
        wait_marker(proc, ready)
        for _ in range(40):
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(.001)
        go.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == 1, output
        assert identity(install.target) == original and install.version() == 'old'
        assert_no_staging(install)
    finally:
        go.touch()
        stop(proc)


@pytest.mark.slow
def test_unpaused_simultaneous_starts_have_one_winner(install: Install) -> None:
    processes = [spawn(install) for _ in range(3)]
    try:
        results = [(p.communicate(timeout=20)[0], p.returncode) for p in processes]
        assert sorted(code for _, code in results) == [0, 4, 4], results
        assert install.version() == 'new'
        assert_no_staging(install)
        assert not list(install.target.parent.glob('.*.previous.*'))
    finally:
        for proc in processes:
            stop(proc)


@pytest.mark.parametrize('existing', (False, True))
def test_unwritable_parent_fails_before_attempting_lock(install: Install, existing: bool) -> None:
    if existing:
        install.lock.mkdir()
        (install.lock / 'pid').write_text('2147483647\n')
        (install.lock / 'precious').write_text('keep')
    before = snapshot(install.target.parent)
    install.target.parent.chmod(0o500)
    try:
        if os.access(install.target.parent, os.W_OK):
            pytest.skip('requires an account subject to directory write permissions')
        result = install.run()
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'cannot be written' in result.stdout + result.stderr
    finally:
        install.target.parent.chmod(before['.'][1] & 0o777)
    assert snapshot(install.target.parent) == before


@pytest.mark.slow
@pytest.mark.parametrize('row', range(1, 6))
@pytest.mark.parametrize('phase', ('displace', 'install'))
@pytest.mark.parametrize('after', (False, True))
def test_later_linux_forward_deadlines_restore_earlier_rows(install: Install, tmp_path: Path, row: int, phase: str, after: bool) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux integration rows')
    original = [identity(p) for p in install.paths]
    reached = tmp_path / 'timed-move'
    target = install.paths[row]
    env = move_shim(tmp_path, install, f'''
blocked = (source == {str(target)!r} and '.backup.' in destination) if {phase!r} == 'displace' else (destination == {str(target)!r} and '.backup.' not in source)
if blocked:
    if {after!r}:
        subprocess.run([{'/bin/ln'!r}, *args], check=True)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pathlib.Path({str(reached)!r}).touch()
    time.sleep(60)
''')
    # Exercise the actual watchdog with a shorter deadline for this 20-case matrix.
    install.script.write_text(install.script.read_text().replace('sleep 5 &', 'sleep 1 &'))
    proc = spawn(install, env)
    try:
        output, _ = proc.communicate(timeout=8)
        assert reached.exists(), output
        assert proc.returncode == 1, output
        assert [identity(p) for p in install.paths] == original
        assert_truthful(install, proc.returncode, output)
    finally:
        stop(proc)


def test_replaced_displacement_source_reports_the_moved_stranger(install: Install, tmp_path: Path) -> None:
    saved = tmp_path / 'saved-original'
    stranger = tmp_path / 'stranger'
    if install.platform == 'linux':
        shutil.copytree(install.target, stranger)
        (stranger / 'version.txt').write_text('stranger')
    else:
        mac.make_app(stranger, version='stranger')
    env = move_shim(tmp_path, install, f'''
if source == {str(install.target)!r} and '.previous.' in destination:
    pathlib.Path(source).rename({str(saved)!r})
    pathlib.Path({str(stranger)!r}).rename(source)
''')
    result = install.run(env)
    output = result.stdout + result.stderr
    assert result.returncode == 3, output
    prefix = 'The unrecognized displaced object is at: '
    path = Path(next(line.removeprefix(prefix) for line in output.splitlines() if line.startswith(prefix)))
    assert path.exists() and install.version(path) == 'stranger', output
    assert install.version(saved) == 'old'
    assert_no_staging(install)


def test_linux_foreign_backup_reservation_is_preserved(install: Install, tmp_path: Path) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux backup reservations')
    directory = tmp_path / 'bin'
    directory.mkdir()
    record = tmp_path / 'replaced-reservation'
    real_mktemp = shutil.which('mktemp')
    wrapper = directory / 'mktemp'
    wrapper.write_text(f'''#!{sys.executable}
import pathlib, subprocess, sys
r = subprocess.run([{real_mktemp!r}, *sys.argv[1:]], capture_output=True, text=True)
if r.returncode == 0 and '.desktop.backup.' in r.stdout:
    path = pathlib.Path(r.stdout.strip())
    path.rename(str(path) + '.saved')
    path.write_text('precious foreign reservation')
    pathlib.Path({str(record)!r}).write_text(str(path))
sys.stdout.write(r.stdout)
sys.exit(r.returncode)
''')
    wrapper.chmod(0o755)
    result = install.run({**install.env, 'PATH': f"{directory}{os.pathsep}{install.env['PATH']}"})
    assert record.exists(), result.stdout + result.stderr
    assert Path(record.read_text()).read_text() == 'precious foreign reservation'
    assert result.returncode == 1 and install.version() == 'old'
    assert not install.lock.exists()


def test_linux_missing_update_prefix_creates_nothing(install: Install, tmp_path: Path) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux --prefix')
    missing = tmp_path / 'missing' / 'nested'
    result = subprocess.run([*install.command, '--prefix', str(missing)], env=install.env,
                            preexec_fn=linux.installer_process_signals, capture_output=True, text=True)
    assert result.returncode == 1 and 'Nothing has been changed' in result.stdout
    assert not missing.parent.exists()


def test_linux_launched_application_has_default_sigpipe(install: Install, tmp_path: Path) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux application launch')
    marker = tmp_path / 'sigpipe'
    pipe = tmp_path / 'application-lifetime'
    os.mkfifo(pipe)
    reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    launcher = install.script.parent / 'waveguide-generator' / 'waveguide-generator'
    # Require a real launch and observe EOF after the application closes its
    # private descriptor. Absence of a marker before it has run proves nothing.
    launcher.write_text(f'#!/bin/sh\nexec 3> {str(pipe)!r}\nprintf started >&3\nkill -PIPE $$\nprintf survived > {str(marker)!r}\n')
    launcher.chmod(0o755)
    try:
        result = subprocess.run(['/bin/bash', str(install.script), '--skip-checks'], env=install.env,
                                preexec_fn=linux.installer_process_signals, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        started = False
        deadline = time.monotonic() + 10
        while True:
            assert time.monotonic() < deadline, 'application did not start and close its descriptor'
            try:
                chunk = os.read(reader, 4096)
            except BlockingIOError:
                chunk = None
            if chunk:
                started = True
            elif chunk == b'' and started:
                break
            time.sleep(.01)
        assert not marker.exists(), 'launched application inherited ignored SIGPIPE'
    finally:
        os.close(reader)


@pytest.mark.parametrize('row', (0, 1))
def test_move_children_have_default_sigpipe(install: Install, tmp_path: Path, row: int) -> None:
    if install.platform == 'macos' and row:
        pytest.skip('Linux integration-file child')
    target = install.paths[row]
    directory = tmp_path / 'bin'
    directory.mkdir()
    tool = 'mv' if row == 0 else 'ln'
    real = shutil.which(tool)
    wrapper = directory / tool
    reached = tmp_path / 'pipe-child-reached'
    survived = tmp_path / 'pipe-child-survived'
    wrapper.write_text(f'''#!/bin/sh
previous=""
for arg do source="$previous"; previous="$arg"; done
if [ "$source" = {str(target)!r} ]; then
    : > {str(reached)!r}
    kill -PIPE $$
    : > {str(survived)!r}
fi
exec {real!r} "$@"
''')
    wrapper.chmod(0o755)
    result = install.run({**install.env, 'PATH': f"{directory}{os.pathsep}{install.env['PATH']}"})
    assert reached.exists(), result.stdout + result.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert not survived.exists(), 'move child inherited ignored SIGPIPE'
    assert_truthful(install, result.returncode, result.stdout + result.stderr)


@pytest.mark.slow
@pytest.mark.parametrize('forced_failure', (False, True))
@pytest.mark.parametrize('batch', range(10))
def test_dense_group_bursts_during_rollback(install: Install, tmp_path: Path, forced_failure: bool, batch: int) -> None:
    # Reuse the reviewers' cleanup-burst and burst_capture models: fail the last
    # forward row, synchronize at a real restore child, vary the burst offset.
    # Each platform gets 100 recoverable + 100 genuinely failed restores. On
    # Linux all six rows have been displaced, so every recovery line is checked.
    ready, go = tmp_path / 'restore-ready', tmp_path / 'restore-go'
    burst_done = tmp_path / 'burst-done'
    body = install.script.read_text()
    assert body.count('    release_lock\n') == 1
    # Keep the parent alive through the whole burst, including status/recovery
    # printing. Signalling an already-exited group is outside cleanup and can
    # be denied by the macOS sandbox for reparented descendants.
    install.script.write_text(body.replace('    release_lock\n',
        f'    while [ ! -e {str(burst_done)!r} ]; do sleep 0.01; done\n    release_lock\n', 1))
    original = {path: identity(path) for path in install.paths}
    fail = install.paths[-1]
    env = move_shim(tmp_path, install, f'''
backup = '.previous.' in source or '.backup.' in source
if destination == {str(fail)!r} and not backup:
    sys.exit(1)
if backup:
    pathlib.Path({str(ready)!r}).touch()
    while not pathlib.Path({str(go)!r}).exists(): time.sleep(.001)
    if {forced_failure!r}: sys.exit(1)
''')
    prefix = 'Its backup remains at: ' if install.platform == 'linux' else 'The previous app is at: '
    # Keep each pytest item below the suite's 300-second hang backstop while
    # preserving 100 runs for each outcome/platform across ten batches.
    for local_run in range(10):
        run = batch * 10 + local_run
        burst_done.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
        go.unlink(missing_ok=True)
        proc = spawn(install, env)
        try:
            wait_marker(proc, ready)
            assert not install.target.exists(), 'burst must land during rollback'
            go.touch()
            time.sleep((run % 10) * .0002)
            for number in range(80):
                try:
                    os.killpg(proc.pid, (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT)[number % 4])
                except ProcessLookupError:
                    pytest.fail('installer exited before all cleanup burst signals arrived')
                time.sleep(.0005 + (run % 3) * .0002)
            burst_done.touch()
            output, _ = proc.communicate(timeout=15)
            assert proc.returncode == (3 if forced_failure else 1), output
            if forced_failure:
                recovery = [Path(line.removeprefix(prefix)) for line in output.splitlines() if line.startswith(prefix)]
                assert len(recovery) == len(original), output
                assert {identity(path) for path in recovery} == set(original.values()), output
                assert output.count('ERROR: could not restore') == len(original), output
                if install.platform == 'linux':
                    assert 'ERROR: rollback was incomplete;' in output
                # Restore these exact objects for the next run, after recording
                # and checking status and all printed paths (reviewer ordering).
                recovery_by_identity = {identity(p): p for p in recovery}
                for path, old_id in original.items():
                    backup = recovery_by_identity[old_id]
                    assert not path.exists() and not path.is_symlink(), output
                    backup.rename(path)
            else:
                assert {path: identity(path) for path in install.paths} == original, output
                assert sum(line.startswith('Restored the previous installation') for line in output.splitlines()) == 1, output
            assert install.version() == 'old'
            assert_no_staging(install)
        finally:
            go.touch()
            burst_done.touch()
            stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize('tool', ('copy', 'xattr', 'codesign'))
def test_term_ignoring_long_step_is_killed(install: Install, tmp_path: Path, tool: str) -> None:
    if install.platform == 'linux' and tool != 'copy':
        pytest.skip('macOS signature tools')
    tool = ('cp' if install.platform == 'linux' else 'ditto') if tool == 'copy' else tool
    marker = tmp_path / 'long-step'
    directory = tmp_path / 'bin'
    directory.mkdir()
    wrapper = directory / tool
    wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(60)
''')
    wrapper.chmod(0o755)
    original = {path: identity(path) for path in install.paths}
    proc = spawn(install, {**install.env, 'PATH': f'{directory}{os.pathsep}{install.env["PATH"]}'})
    try:
        wait_marker(proc, marker)
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        output, _ = proc.communicate(timeout=8)
        assert 4.5 <= time.monotonic() - started < 8, output
        assert proc.returncode == 1 and 'Installation interrupted.' in output, output
        assert {path: identity(path) for path in install.paths} == original
        assert install.version() == 'old'
        assert_no_staging(install)
    finally:
        stop(proc)


def test_fifo_lock_owner_refuses_promptly(install: Install) -> None:
    # Reuse fifo_lock.py: real FIFO, no writer, no command shim.
    install.lock.mkdir()
    fifo = install.lock / 'pid'
    os.mkfifo(fifo)
    before = (identity(install.lock), identity(fifo), fifo.lstat().st_mode)
    proc = spawn(install)
    try:
        output, _ = proc.communicate(timeout=3)
        assert proc.returncode == 4, output
        assert 'unreadable or missing' in output
        assert (identity(install.lock), identity(fifo), fifo.lstat().st_mode) == before
        assert install.version() == 'old'
    finally:
        stop(proc)


@pytest.mark.slow
def test_linux_preflight_pid_only_term_is_interruption(install: Install, tmp_path: Path) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux Bash 3.2 library preflight')
    marker = tmp_path / 'preflight-child'
    exe = install.script.parent / 'waveguide-generator/runtime/bin/python3.13'
    exe.parent.mkdir(exist_ok=True)
    exe.write_text(f'''#!{sys.executable}
import os, pathlib, time
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(60)
''')
    exe.chmod(0o755)
    original = {path: identity(path) for path in install.paths}
    capture_parent = tmp_path / 'captures'
    capture_parent.mkdir()
    proc = subprocess.Popen(['/bin/bash', str(install.script), '--update'],
                            env={**install.env, 'TMPDIR': str(capture_parent)},
                            preexec_fn=linux.installer_process_signals, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        wait_marker(proc, marker)
        os.kill(proc.pid, signal.SIGTERM)
        output, _ = proc.communicate(timeout=8)
        assert proc.returncode == 1 and 'Installation interrupted.' in output, output
        assert 'cannot load a library' not in output, output
        assert {path: identity(path) for path in install.paths} == original
        assert_no_staging(install)
        assert not list(capture_parent.iterdir())
    finally:
        stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize('interrupt', (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT))
def test_signal_just_after_commit_finishes_success(install: Install, tmp_path: Path, interrupt) -> None:
    ready, go = tmp_path / 'committed', tmp_path / 'commit-go'
    install.hook('committed', ready, go)
    proc = spawn(install)
    try:
        wait_marker(proc, ready)
        os.killpg(proc.pid, interrupt)
        go.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == 0 and f'Installed: {install.target}' in output, output
        assert install.version() == 'new'
        assert_no_staging(install)
        parents = {p.parent for p in install.paths}
        assert not [p for parent in parents for p in parent.iterdir() if '.backup.' in p.name or '.previous.' in p.name]
    finally:
        go.touch()
        stop(proc)


@pytest.mark.slow
def test_quit_during_swap_restores(install: Install, tmp_path: Path) -> None:
    ready, go = tmp_path / 'displaced', tmp_path / 'quit-go'
    original = {path: identity(path) for path in install.paths}
    install.hook('displaced', ready, go)
    proc = spawn(install)
    try:
        wait_marker(proc, ready)
        os.killpg(proc.pid, signal.SIGQUIT)
        go.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == 1 and 'Installation interrupted.' in output, output
        assert {path: identity(path) for path in install.paths} == original
        assert_no_staging(install)
    finally:
        go.touch()
        stop(proc)


@pytest.mark.parametrize('kind', ('file', 'symlink', 'directory', 'unexpected'))
def test_printed_lock_command_removes_each_kind(install: Install, tmp_path: Path, kind: str) -> None:
    # new_probes.py lock-command model, including quoted shell metacharacters.
    parent = install.target.parent
    quoted = parent.with_name(parent.name + "' $HOME `literal`")
    parent.rename(quoted)
    install.paths = [quoted / p.name if p.parent == parent else p for p in install.paths]
    install.target = quoted / install.target.name
    if install.platform == 'linux':
        install.env['XDG_DATA_HOME'] = str(quoted)
    if kind == 'file':
        install.lock.write_text('foreign file')
    elif kind == 'symlink':
        install.lock.symlink_to(tmp_path / 'absent')
    else:
        install.lock.mkdir()
        (install.lock / 'pid').write_text('2147483647\n')
        if kind == 'unexpected':
            (install.lock / 'precious').write_text('keep')
    before = snapshot(quoted)
    result = install.run()
    output = result.stdout + result.stderr
    assert result.returncode == 4 and snapshot(quoted) == before, output
    assert 'look inside a lock with unexpected contents' in output
    command = next(line.strip() for line in output.splitlines() if line.startswith('  rm '))
    removal = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True, timeout=3)
    if kind == 'unexpected':
        assert removal.returncode != 0 and (install.lock / 'precious').read_text() == 'keep'
    else:
        assert removal.returncode == 0, removal.stderr
        assert not install.lock.exists() and not install.lock.is_symlink()


@pytest.mark.slow
def test_cancelled_watchdog_has_bounded_reap(install: Install, tmp_path: Path) -> None:
    # A watchdog which receives cancellation but never finishes (the captured
    # hang's wait boundary). No unbounded wait is allowed even in that case.
    body = install.script.read_text()
    key = "trap 'timer_cancelled=1' USR1"
    assert body.count(key) == 1
    stuck = tmp_path / 'stuck-watchdog'
    install.script.write_text(body.replace(key, f"trap 'if [ ! -e \"{stuck}\" ]; then touch \"{stuck}\"; while :; do sleep 0.1; done; fi; timer_cancelled=1' USR1"))
    original = {path: identity(path) for path in install.paths}
    started = time.monotonic()
    proc = spawn(install, fail_app_install(tmp_path, install))
    try:
        output, _ = proc.communicate(timeout=15)
        assert time.monotonic() - started < 15
        assert proc.returncode == 1, output
        assert {path: identity(path) for path in install.paths} == original
        assert 'Restored the previous installation' in output
        assert_no_staging(install)
    finally:
        stop(proc)


@pytest.mark.slow
def test_signal_killed_restore_and_message_are_retried(install: Install, tmp_path: Path) -> None:
    killed_move, killed_print = tmp_path / 'move-killed', tmp_path / 'print-killed'
    original = identity(install.target)
    env = fail_app_install(tmp_path, install, f'''
if '.previous.' in source or '.backup.' in source:
    marker = pathlib.Path({str(killed_move)!r})
    count = int(marker.read_text()) if marker.exists() else 0
    if count < 3:
        marker.write_text(str(count + 1))
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)
''')
    body = install.script.read_text()
    body = body.replace('(command printf "$@")', f'''(
            case "$1" in 'Restored the previous installation'*)
                if [ ! -e '{killed_print}' ]; then
                    touch '{killed_print}'
                    exec '{sys.executable}' -c 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)'
                fi ;;
            esac
            command printf "$@"
        )''', 1)
    install.script.write_text(body)
    result = install.run(env)
    output = result.stdout + result.stderr
    assert result.returncode == 1 and identity(install.target) == original, output
    assert killed_move.exists() and killed_print.exists()
    assert sum(line.startswith('Restored the previous installation') for line in output.splitlines()) == 1, output
    assert_no_staging(install)


@pytest.mark.slow
def test_cleanup_ignores_parent_signals_for_rest_of_run(install: Install, tmp_path: Path) -> None:
    # Pause after cleanup has entered protection, then send every catchable
    # signal to the parent. The initial (failure-triggered) flag must stay clear.
    ready, go = tmp_path / 'cleanup-protected', tmp_path / 'protected-go'
    body = install.script.read_text()
    key = '    status="$EXIT_STATUS"\n' if install.platform == 'macos' else '    local status="$EXIT_STATUS" i\n'
    body = body.replace(key, key + f'''    touch '{ready}'
    while [ ! -e '{go}' ]; do sleep 0.01; done
    printf 'Cleanup interruption flag: %s\\n' "$INTERRUPTED"
''', 1)
    install.script.write_text(body)
    proc = spawn(install, fail_app_install(tmp_path, install))
    try:
        wait_marker(proc, ready)
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
            os.kill(proc.pid, sig)
            time.sleep(.01)
        go.touch()
        output, _ = proc.communicate(timeout=15)
        assert proc.returncode == 1, output
        assert 'Cleanup interruption flag: 0' in output, output
        assert_truthful(install, proc.returncode, output)
    finally:
        go.touch()
        stop(proc)


def test_cleanup_documentation_describes_hard_kill_staging(install: Install) -> None:
    from scripts.build_bundle import BundleBuilder

    header = install.script.read_text().split('INTERRUPTED=0')[0]
    readme = BundleBuilder.LINUX_TARBALL_INSTRUCTIONS if install.platform == 'linux' else BundleBuilder(Path('.')).dmg_readme()
    pattern = '.waveguide-generator.install.*' if install.platform == 'linux' else '.new.'
    for text in (header, readme):
        assert pattern in text
        assert 'safe to delete' in text
        assert 'unexpected contents' in ' '.join(text.split())
    assert 'a crash, power loss or a forced quit' in readme
    assert 'closed Terminal window' not in readme


@pytest.mark.slow
def test_cleanup_mover_inherits_ignored_signals(install: Install, tmp_path: Path) -> None:
    marker = tmp_path / 'restore-dispositions'
    env = fail_app_install(tmp_path, install, f'''
if '.previous.' in source or '.backup.' in source:
    pathlib.Path({str(marker)!r}).write_text(str([int(signal.getsignal(sig)) for sig in
        (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT)]))
''')
    result = install.run(env)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert marker.read_text() == '[1, 1, 1, 1]', output
    assert_truthful(install, result.returncode, output)


@pytest.mark.parametrize('interrupt', (signal.SIGHUP, signal.SIGTERM))
def test_linux_launched_application_keeps_hup_term_defaults(install: Install, tmp_path: Path, interrupt) -> None:
    if install.platform != 'linux':
        pytest.skip('Linux Bash 3.2 application launch')
    marker = tmp_path / 'application-signals'
    launcher = install.script.parent / 'waveguide-generator' / 'waveguide-generator'
    launcher.write_text(f'''#!{sys.executable}
import pathlib, signal
pathlib.Path({str(marker)!r}).write_text(str(int(signal.getsignal({int(interrupt)}))))
''')
    launcher.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(install.script), '--skip-checks'], env=install.env,
                            preexec_fn=linux.installer_process_signals, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    deadline = time.monotonic() + 5
    while not marker.exists():
        assert time.monotonic() < deadline, 'installed application did not run'
        time.sleep(.01)
    assert marker.read_text() == str(int(signal.SIG_DFL)), 'application inherited cleanup signal ignores'


@pytest.mark.slow
@pytest.mark.parametrize('scope', ('parent', 'group'))
@pytest.mark.parametrize('model', ('restore-stat', 'cleanup-rm', 'cleanup-rmdir', 'commit-rm', 'restore-mv', 'commit-cache'))
def test_cleanup_commit_blocked_commands_have_deadlines(install: Install, tmp_path: Path, model: str, scope: str) -> None:
    """Round-4 gated blockers, without releasing the gate to finish cleanup."""
    if model == 'commit-cache' and install.platform != 'linux':
        pytest.skip('Linux desktop cache refresh')
    ready, failed = tmp_path / 'blocked-command', tmp_path / 'failed-forward'
    original = {path: identity(path) for path in install.paths}
    rollback = not model.startswith('commit')
    env = install.env
    if rollback:
        env = fail_app_install(tmp_path, install, f'''
if destination == {str(install.target)!r} and ('.install.' in source or '.new.' in source):
    pathlib.Path({str(failed)!r}).touch()
''')
    tool = {'restore-stat': 'stat', 'cleanup-rm': 'rm', 'cleanup-rmdir': 'rmdir',
            'commit-rm': 'rm', 'restore-mv': 'mv', 'commit-cache': 'update-desktop-database'}[model]
    directory = tmp_path / 'bin'
    directory.mkdir(exist_ok=True)
    wrapper = directory / tool
    real = shutil.which(tool)
    if model == 'restore-stat':
        match = f"pathlib.Path({str(failed)!r}).exists() and any('.previous.' in x for x in sys.argv[1:])"
    elif model == 'restore-mv':
        match = "any('.previous.' in x for x in sys.argv[1:-1])"
    elif model == 'commit-rm':
        match = "any('.previous.' in x for x in sys.argv[1:])"
    elif model == 'cleanup-rmdir':
        # macOS has no separate staging root: block just one lock removal,
        # so its safe retry proves that lock release still completes.
        match = "not ready.exists() and any('.install.lock' in x or '.install.' in x for x in sys.argv[1:])"
    elif model == 'cleanup-rm':
        match = "'-rf' in sys.argv and any('.new.' in x or '.install.' in x for x in sys.argv[1:])"
    else:
        match = 'True'
    if model.startswith('cleanup'):
        match = f"pathlib.Path({str(failed)!r}).exists() and ({match})"
    forward_failure = ''
    if model == 'restore-mv':
        forward_failure = f'''
if sys.argv[-1] == {str(install.target)!r} and ('.install.' in sys.argv[-2] or '.new.' in sys.argv[-2]):
    pathlib.Path({str(failed)!r}).touch()
    sys.exit(1)
'''
    wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, signal, sys, time
ready = pathlib.Path({str(ready)!r})
{forward_failure}
if {match}:
    # Python installs default_int_handler on entry when SIGINT was SIG_DFL.
    dispositions = [0 if signal.getsignal(s) == signal.default_int_handler else int(signal.getsignal(s))
                    for s in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT)]
    for s in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT): signal.signal(s, signal.SIG_IGN)
    ready.write_text(str(os.getpid()) + '\\n' + repr(dispositions))
    while True: time.sleep(.01)
''' + (f'os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n' if real else 'sys.exit(0)\n'))
    wrapper.chmod(0o755)
    env = {**env, 'PATH': str(directory) + os.pathsep + env['PATH']}
    proc = spawn(install, env)
    try:
        wait_marker(proc, ready)
        start = time.monotonic()
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
            (os.kill if scope == 'parent' else os.killpg)(proc.pid, sig)
            time.sleep(.01)
        output, _ = proc.communicate(timeout=18)
        assert time.monotonic() - start < 18, output
        assert not install.lock.exists(), output
        assert proc.returncode == (3 if model in ('restore-stat', 'restore-mv') else 1 if rollback else 0), output
        if model in ('restore-stat', 'restore-mv'):
            backups = list(install.target.parent.glob('.*.previous.*'))
            assert len(backups) == 1 and identity(backups[0]) == original[install.target]
            assert str(backups[0]) in output, output
            assert 'could not restore' in output.lower(), output
        elif rollback:
            assert {path: identity(path) for path in install.paths} == original, output
            assert 'Restored the previous installation' in output, output
        else:
            assert install.version() == 'new' and 'Installed:' in output, output
        if model == 'commit-cache':
            assert ready.read_text().splitlines()[1] == '[0, 0, 0, 0]', 'optional step inherited ignored signals'
            assert time.monotonic() - start < 5, output
        if model == 'cleanup-rm' or model == 'commit-rm':
            assert 'could not remove staging/backup object:' in output, output
    finally:
        stop(proc)


@pytest.mark.slow
@pytest.mark.parametrize('phase', ('render-after-write', 'emit-before-write', 'emit-after-write'))
def test_printer_retries_preserve_complete_records(install: Install, tmp_path: Path, phase: str) -> None:
    killed = tmp_path / 'printer-killed'
    body = install.script.read_text()
    if phase == 'render-after-write':
        key = '(command printf "$@")'
        assert body.count(key) == 1
        body = body.replace(key, f'''(
            command printf "$@"
            case "$1" in 'Restored the previous installation'*)
                if [ ! -e '{killed}' ]; then
                    touch '{killed}'
                    exec '{sys.executable}' -c 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)'
                fi ;;
            esac
        )''', 1)
        install.script.write_text(body)
        env = fail_app_install(tmp_path, install)
    else:
        env = fail_app_install(tmp_path, install)
        real = shutil.which('cat')
        wrapper = tmp_path / 'bin' / 'cat'
        wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, signal, sys
contents = pathlib.Path(sys.argv[1]).read_bytes()
marker = pathlib.Path({str(killed)!r})
if contents.startswith(b'Restored the previous installation') and not marker.exists():
    marker.touch()
    if {phase == 'emit-after-write'!r}: os.write(1, contents)
    os.kill(os.getpid(), signal.SIGKILL)
os.execv({real!r}, [{real!r}, *sys.argv[1:]])
''')
        wrapper.chmod(0o755)
    result = install.run(env)
    output = result.stdout + result.stderr
    assert result.returncode == 1 and install.version() == 'old', output
    assert killed.exists(), 'printer fault injection did not execute'
    count = sum(line.startswith('Restored the previous installation') for line in output.splitlines())
    assert count == (2 if phase == 'emit-after-write' else 1), output
    assert 'parsers must tolerate a' in install.script.read_text()
    assert_no_staging(install)


@pytest.mark.slow
@pytest.mark.parametrize('interrupt', (signal.SIGINT, signal.SIGHUP))
@pytest.mark.parametrize('scope', ('parent', 'group'))
def test_close_prompt_cancellation_keeps_decided_status(install: Install, tmp_path: Path, interrupt, scope: str) -> None:
    if install.platform != 'macos':
        pytest.skip('Finder close prompt')
    import pty
    master, slave = pty.openpty()
    original = identity(install.target)
    proc = spawn(install, fail_app_install(tmp_path, install), stdin=slave, interactive=True)
    os.close(slave)
    output = b''
    try:
        deadline = time.monotonic() + 15
        while b'Press Return to close...' not in output:
            assert time.monotonic() < deadline and proc.poll() is None, output
            if select.select([proc.stdout], [], [], .1)[0]:
                output += os.read(proc.stdout.fileno(), 4096)
        assert not install.lock.exists() and identity(install.target) == original
        started = time.monotonic()
        (os.kill if scope == 'parent' else os.killpg)(proc.pid, interrupt)
        proc.communicate(timeout=2)
        assert time.monotonic() - started < 2 and proc.returncode == 1
    finally:
        stop(proc)
        os.close(master)


def test_gatekeeper_header_allows_both_approval_routes(install: Install) -> None:
    if install.platform != 'macos':
        pytest.skip('Gatekeeper')
    header = install.script.read_text().split('INTERRUPTED=0')[0]
    assert 'Both the app and this script' in header and 'Privacy & Security' in header
    assert 'clear the quarantine flag' in header
    assert 'app is not' not in header and 'nothing to attach an exception' not in header


@pytest.mark.slow
def test_cleanup_shared_budget_bounds_repeated_restore_timeouts(install: Install, tmp_path: Path) -> None:
    # Install all rows, then request rollback. The shared budget must cap a
    # series of five-second movers, rather than spending five seconds per row.
    body = install.script.read_text()
    assert body.count('exec sleep 20') == 1
    install.script.write_text(body.replace('exec sleep 20', 'exec sleep 2').replace('COMMITTED=1\n', 'exit 1\n', 1))
    ready = tmp_path / 'restore-blocked'
    original = {path: identity(path) for path in install.paths}
    env = move_shim(tmp_path, install, f'''
if '.previous.' in source or '.backup.' in source:
    pathlib.Path({str(ready)!r}).touch()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True: time.sleep(.01)
''')
    proc = spawn(install, env)
    try:
        wait_marker(proc, ready)
        started = time.monotonic()
        output, _ = proc.communicate(timeout=7)
        assert time.monotonic() - started < 7 and proc.returncode == 3, output
        assert not install.lock.exists(), output
        for path in install.paths:
            backups = [candidate for candidate in path.parent.iterdir()
                       if ('.previous.' in candidate.name or '.backup.' in candidate.name)
                       and identity(candidate) == original[path]]
            assert len(backups) == 1 and str(backups[0]) in output, output
    finally:
        stop(proc)


def test_fifo_owner_is_never_opened(install: Install, tmp_path: Path) -> None:
    # Keep the unchanged real-FIFO/no-shim timing reproduction above. Since cat
    # is now bounded too, additionally observe the forbidden open attempt itself.
    install.lock.mkdir()
    fifo = install.lock / 'pid'
    os.mkfifo(fifo)
    before = (identity(install.lock), identity(fifo), fifo.lstat().st_mode)
    opened = tmp_path / 'fifo-open-attempt'
    directory = tmp_path / 'bin'
    directory.mkdir()
    real_cat = shutil.which('cat')
    wrapper = directory / 'cat'
    wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, sys
if {str(fifo)!r} in sys.argv[1:]:
    pathlib.Path({str(opened)!r}).touch()
os.execv({real_cat!r}, [{real_cat!r}, *sys.argv[1:]])
''')
    wrapper.chmod(0o755)
    env = {**install.env, 'PATH': str(directory) + os.pathsep + install.env['PATH']}
    proc = spawn(install, env)
    try:
        output, _ = proc.communicate(timeout=3)
        assert proc.returncode == 4 and 'unreadable or missing' in output, output
        assert not opened.exists(), 'diagnostic attempted to open the FIFO'
        assert (identity(install.lock), identity(fifo), fifo.lstat().st_mode) == before
        assert install.version() == 'old'
    finally:
        stop(proc)
