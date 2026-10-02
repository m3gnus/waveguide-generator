"""Real host-shell helper signals and native ownership/crash decisions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import shutil
import subprocess
import sys
import time

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX installer helper", allow_module_level=True)

from scripts.build_bundle import linux_launcher
from scripts.tests.test_installer_journal import sandbox, stage_recovery
from scripts.tests.test_linux_bundle_install_update import (
    InstallerFamily, installed_dir, installer_process_signals, late_failure_env, make_tarball, run,
)

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, proc=None, seconds=30):
    end = time.monotonic() + seconds
    while not predicate():
        if proc is not None:
            assert proc.poll() is None, "helper exited before the test boundary"
        assert time.monotonic() < end, "test boundary was not reached"
        time.sleep(.02)


def helper(root, payload, env, tmp_path, *, shell="/bin/sh", prepare=None, file_limit=None, script_path=None):
    recovery, work = stage_recovery(root, env, tmp_path)
    if prepare is not None:
        prepare(recovery, work)
    asset = tmp_path / "installer.tar.gz"
    asset.write_bytes(b"fixture verified archive")
    metadata = json.dumps({"from": "0.3.3", "to": "0.3.4", "log": str(work / "install.log")})[:-1]
    journal = root.parent / ("." + root.name + ".install.lock.journal")
    command = [shell, str(script_path or ROOT / "launchers/installer-helper.sh"), "linux-x86_64", str(asset),
        str(payload), str(root), "999999999", str(work), metadata,
        hashlib.sha256(asset.read_bytes()).hexdigest(), json.dumps(str(journal)), str(recovery)]
    def setup_child():
        installer_process_signals()
        if file_limit is not None:
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))

    proc = subprocess.Popen(command, env=env, cwd=tmp_path, start_new_session=True,
        preexec_fn=setup_child, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, InstallerFamily(proc), recovery, work


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
@pytest.mark.parametrize("name", ["HUP", "INT", "TERM"])
def test_foreground_installer_receives_catchable_signals(tmp_path, shell, name):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    launched = tmp_path / "launched"
    (root / "waveguide-generator").write_text(f'#!/bin/sh\ntouch {shlex.quote(str(launched))}\n')
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    ready, caught = tmp_path / "child", tmp_path / "caught"
    (payload / "install.sh").write_text(f'''#!/bin/bash
trap 'printf HUP > {shlex.quote(str(caught))}; exit 1' HUP
trap 'printf INT > {shlex.quote(str(caught))}; exit 1' INT
trap 'printf TERM > {shlex.quote(str(caught))}; exit 1' TERM
printf '%s' "$$" > {shlex.quote(str(ready))}
while :; do sleep .1; done
''')
    proc, family, recovery, work = helper(root, payload, env, tmp_path, shell=shell)
    try:
        wait_for(ready.exists, proc)
        os.kill(int(ready.read_text()), getattr(signal, "SIG" + name))
        assert proc.wait(timeout=10) == 0
        assert caught.read_text() == name
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "failed" and outcome["previousKept"] is True
        wait_for(launched.exists)
        wait_for(lambda: not family.live(), seconds=5)
        assert not recovery.exists()
    finally:
        family.stop()
        if proc.poll() is None:
            proc.wait(timeout=5)


def native_layout(tmp_path, env, version):
    payload = make_tarball(tmp_path / version, version)
    script = payload / "install.sh"
    script.write_text(script.read_text().replace("PREFLIGHT=1\n", "PREFLIGHT=0\n", 1))
    return payload


def set_native_entry(payload, started):
    app = payload / "waveguide-generator"
    (app / "waveguide-generator").write_text(linux_launcher())
    (app / "runtime/bin/python3.13").write_text(f'#!/bin/sh\ntouch {shlex.quote(str(started))}\n')


def set_bridge_app_prefix(app, started, *, platform):
    """Execute the actual B entry prefix, stopping before controller imports."""
    launchers = app / "launchers"
    launchers.mkdir(exist_ok=True)
    (launchers / "__init__.py").write_text("")
    shared = app / "shared"
    shared.mkdir(exist_ok=True)
    (shared / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "shared/release_assets.py", shared / "release_assets.py")
    shutil.copyfile(ROOT / "launchers/full_installer.py", launchers / "full_installer.py")
    desktop = (ROOT / "launchers/desktop.py").read_text()
    prefix = desktop[:desktop.index("from launchers.statusapp.__main__ import (")]
    (launchers / "desktop.py").write_text(prefix + f"\nPath({str(started)!r}).touch()\n")
    code = f"import sys,runpy; sys.platform={platform!r}; runpy.run_module('launchers.desktop',run_name='__main__')"
    runtime = app.parent / "runtime/bin/python3.13"
    runtime.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)}\n")



@pytest.mark.parametrize("phase", ["copy", "committed"])
def test_helper_only_sigkill_native_settles_and_next_start_records_decision(tmp_path, phase):
    env = sandbox(tmp_path)
    started = tmp_path / "started"
    old = native_layout(tmp_path, env, "old")
    set_native_entry(old, started)
    if phase == "copy":
        # The layer bridge retains this exact v0.3.2 root entry. Only the app
        # bytes introduce the first full installer's recovery guard.
        (old / "waveguide-generator/waveguide-generator").write_text(
            (ROOT / "scripts/tests/fixtures/full-installer-bridge-entry/linux-v032.sh").read_text())
        set_bridge_app_prefix(old / "waveguide-generator/app", started, platform="linux")
    assert run(old, env, "--no-launch").returncode == 0
    root = installed_dir(env)
    payload = native_layout(tmp_path, env, "new")
    set_native_entry(payload, started)
    boundary, release = tmp_path / "boundary", tmp_path / "release"
    script = payload / "install.sh"
    text = script.read_text()
    if phase == "copy":
        copy = tmp_path / "paused-copy.sh"
        copy.write_text(f'#!/bin/sh\ntouch {shlex.quote(str(boundary))}\nwhile :; do sleep .1; done\n')
        text = text.replace('run_interruptible cp -a -- "$SOURCE/." "$STAGED_TARGET"',
            f'run_interruptible /bin/sh {shlex.quote(str(copy))}', 1)
    else:
        text = text.replace('# Once committed, finish the success message',
            f'touch {shlex.quote(str(boundary))}\nwhile [ ! -e {shlex.quote(str(release))} ]; do sleep .1; done\n# Once committed, finish the success message', 1)
    script.write_text(text)
    proc, family, recovery, work = helper(root, payload, env, tmp_path)
    try:
        wait_for(boundary.exists, proc)
        family.live()  # retain child identities before losing the parent
        os.kill(proc.pid, signal.SIGKILL)  # kill only the external helper PID
        assert proc.wait(timeout=5) == -signal.SIGKILL
        release.touch()
        wait_for(lambda: not family.live(), seconds=30)
        expected = "old" if phase == "copy" else "new"
        assert (root / "version.txt").read_text() == expected
        assert recovery.exists() and not (work / "outcome.json").exists()
        result = subprocess.run(["/bin/sh", str(root / "waveguide-generator")], env=env,
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert started.exists()
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == ("failed" if phase == "copy" else "installed")
        assert outcome.get("previousKept", False) is (phase == "copy")
        assert not recovery.exists() and not (work / "lock").exists()
    finally:
        release.touch()
        family.stop()
        if proc.poll() is None:
            proc.wait(timeout=5)


def test_later_row_rollback_reports_retained_backup_not_restored_application(tmp_path):
    env = sandbox(tmp_path)
    assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    payload = native_layout(tmp_path, env, "new")
    failed_env = late_failure_env(tmp_path, env, restore_failure="desktop")
    proc, family, recovery, work = helper(root, payload, failed_env, tmp_path)
    try:
        assert proc.wait(timeout=30) == 3
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "rollback_incomplete" and not outcome.get("previousKept")
        assert (root / "version.txt").read_text() == "old"
        ledger = Path(outcome["journalPath"])
        rows = ledger.read_text().splitlines()
        assert not Path(rows[4]).exists()  # app was restored first
        assert outcome["backupPath"] == rows[9]  # actual retained desktop
        backup = Path(outcome["backupPath"])
        assert backup.exists()
        assert rows[11] == f"{backup.stat().st_dev}:{backup.stat().st_ino}"
        assert recovery.exists()
        logged = (work / "install.log").read_bytes()
        assert b"could not restore the previous desktop entry" in logged
        assert len(logged) <= 256 * 1024 and not (tmp_path / "-inf").exists()
    finally:
        family.stop()
        if proc.poll() is None:
            proc.wait(timeout=5)


@pytest.mark.parametrize("path_name", ["ordinary", "space ' quote \\ backslash"])
def test_log_rotation_is_bounded_bytes_at_exact_private_path(tmp_path, path_name):
    tmp_path = tmp_path / path_name
    tmp_path.mkdir()
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "install.sh").write_text("""#!/bin/bash
line=$(printf '%8192s' '')
for ((row=0; row<110; row++)); do printf 'ROW-%04d:%s\n' "$row" "$line"; done
printf 'FINAL-OUTPUT-MARKER\n'
exit 1
""")
    proc, family, recovery, work = helper(root, payload, env, tmp_path)
    try:
        assert proc.wait(timeout=10) == 0
        logged = (work / "install.log").read_bytes()
        assert len(logged) <= 256 * 1024
        rotated = (work / "install.log.1").read_bytes()
        assert len(rotated) <= 256 * 1024
        assert b"FINAL-OUTPUT-MARKER" in logged and b"Earlier output retained" in logged
        assert b"ROW-0109" in logged and b"ROW-0000" not in rotated
        assert b"ROW-" in rotated and b"FINAL-OUTPUT-MARKER" not in rotated
        assert set(p.name for p in work.glob("install.log*")) == {"install.log", "install.log.1"}
        assert not (tmp_path / "-inf").exists()
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["log"] == str(work / "install.log")
    finally:
        family.stop()
        if proc.poll() is None:
            proc.wait(timeout=5)


@pytest.mark.parametrize("leaf", ["install.log", "install.log.1"])
@pytest.mark.parametrize("kind", ["foreign", "symlink", "fifo", "oversized", "hardlink"])
def test_logger_preserves_foreign_leaves_before_native_mutation(tmp_path, leaf, kind):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    reached = tmp_path / "native-reached"
    (payload / "install.sh").write_text(f"#!/bin/bash\ntouch {shlex.quote(str(reached))}\nexit 0\n")
    foreign = tmp_path / "precious"
    foreign.write_bytes(b"foreign contents")
    saved = {}

    def prepare(_recovery, work):
        candidate = work / leaf
        if kind == "symlink":
            candidate.symlink_to(foreign)
        elif kind == "fifo":
            os.mkfifo(candidate)
        elif kind == "hardlink":
            foreign.write_bytes(b"[WG installer output]\nprecious aliased contents\n")
            os.link(foreign, candidate)
        else:
            candidate.write_bytes(b"foreign contents" if kind == "foreign" else b"[WG installer output]\n" + b"x" * 262144)
        saved["path"], saved["identity"] = candidate, candidate.lstat()

    proc, family, _recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    try:
        assert proc.wait(timeout=10) == 1
        assert not reached.exists()
        before, after = saved["identity"], saved["path"].lstat()
        assert (before.st_dev, before.st_ino, before.st_mode, before.st_size) == (after.st_dev, after.st_ino, after.st_mode, after.st_size)
        assert foreign.read_bytes() == (b"[WG installer output]\nprecious aliased contents\n" if kind == "hardlink" else b"foreign contents")
        if kind == "foreign":
            assert saved["path"].read_bytes() == b"foreign contents"
        assert json.loads((work / "outcome.json").read_text())["result"] == "failed"
        assert not (work / "lock").exists()
    finally:
        family.stop()


def test_actual_logger_write_failure_keeps_truthful_native_verdict(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    # Default PIPE and many post-failure writes prove continuous reader/discard
    # ownership; merely ignoring PIPE and returning zero could mask the bug.
    finished = tmp_path / "native-finished"
    (payload / "install.sh").write_text(f"""#!/bin/bash
trap - PIPE
for ((row=0; row<80; row++)); do printf '%8192s\\n' '' || exit 99; done
touch {shlex.quote(str(finished))}
exit 0
""")
    proc, family, recovery, work = helper(root, payload, env, tmp_path, file_limit=1024)
    try:
        assert proc.wait(timeout=15) == 0
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "installed" and finished.exists()
        assert outcome["reason"] == "Installer output could not be retained."
        assert json.loads((work / "install.log.error").read_text()) == {"schemaVersion": 1, "kind": "installer_log_error"}
        assert (work / "install.log").stat().st_size <= 1024
        assert not recovery.exists() and not (work / "lock").exists()
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        family.stop()


def test_owned_prior_active_and_rotation_are_adopted_with_one_new_rotation(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "install.sh").write_text("#!/bin/bash\nprintf '%8192s\\n' ''\nprintf NEW-OUTPUT\\n\nexit 1\n")

    def prepare(_recovery, work):
        (work / "install.log").write_bytes(b"[WG installer output]\nOLD-ACTIVE\n" + (b"x" * 8000 + b"\n") * 32)
        (work / "install.log.1").write_bytes(b"[WG installer output]\nOLDER-ROTATION\n")

    proc, family, _recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    try:
        assert proc.wait(timeout=10) == 0
        active, previous = (work / "install.log").read_bytes(), (work / "install.log.1").read_bytes()
        assert max(len(active), len(previous)) <= 262144
        assert b"NEW-OUTPUT" in active and b"OLD-ACTIVE" in previous
        assert b"OLDER-ROTATION" not in previous
        assert set(p.name for p in work.glob("install.log*")) == {"install.log", "install.log.1"}
    finally:
        family.stop()


def test_hard_killed_native_recovery_output_uses_same_rotating_cap(tmp_path):
    env = sandbox(tmp_path)
    assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    original = (root.stat().st_dev, root.stat().st_ino)
    payload = native_layout(tmp_path, env, "new")
    script = payload / "install.sh"
    key = '    STATE[i]=displaced\n'
    assert script.read_text().count(key) == 1
    script.write_text(script.read_text().replace(key, key + '    [ "$i" -ne 0 ] || kill -KILL $$\n', 1))

    def prepare(recovery, _work):
        entry = recovery / "recover.sh"
        body = entry.read_text()
        key = 'exec 1>"$log_fifo" 2>&1\n'
        assert body.count(key) == 1
        body = body.replace(key, key + '''line=$(printf '%8192s' '')
flood_row=0
while [ "$flood_row" -lt 110 ]; do
    printf 'RECOVERY-%04d:%s\\n' "$flood_row" "$line"
    flood_row=$((flood_row + 1))
done
printf 'FINAL-RECOVERY-MARKER\\n'
''', 1)
        entry.write_text(body)

    proc, family, recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    try:
        assert proc.wait(timeout=30) == 0
        assert (root / "version.txt").read_text() == "old"
        assert (root.stat().st_dev, root.stat().st_ino) == original
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "failed" and outcome["previousKept"] is True
        active, previous = (work / "install.log").read_bytes(), (work / "install.log.1").read_bytes()
        assert len(active) <= 262144 and len(previous) <= 262144
        assert b"FINAL-RECOVERY-MARKER" in active and b"RECOVERY-0109" in active
        assert b"RECOVERY-" in previous and b"RECOVERY-0000" not in previous
        assert not list(work.glob(".recovery-output.*"))
        assert not (work / "lock").exists() and not recovery.exists()
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        family.stop()


def test_logger_drain_has_deadline_and_reaps_its_exact_child(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "install.sh").write_text("#!/bin/bash\nprintf small-output\\n\nexit 0\n")
    observed = tmp_path / "logger-pid"
    def prepare(recovery, _work):
        wrapper = recovery / "log"
        wrapper.unlink()
        wrapper.write_text(f"#!{sys.executable}\nimport os,signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen({str(observed)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
        wrapper.chmod(0o755)
        # Let admission pass; the injected hang occurs in the actual writer.
        entry = tmp_path / "helper-hung-logger.sh"
        body = (ROOT / "launchers/installer-helper.sh").read_text()
        body = body.replace('    WG_INSTALLER_LOG="$log" "$recovery/log" --check\n', '    return 0\n', 1)
        entry.write_text(body)
    script = tmp_path / "helper-hung-logger.sh"
    proc, family, recovery, work = helper(root, payload, env, tmp_path, prepare=prepare, script_path=script)
    try:
        started = time.monotonic()
        assert proc.wait(timeout=12) == 0
        assert time.monotonic() - started < 10
        with pytest.raises(ProcessLookupError):
            os.kill(int(observed.read_text()), 0)
        assert json.loads((work / "outcome.json").read_text())["result"] == "installed"
        assert json.loads((work / "install.log.error").read_text())["kind"] == "installer_log_error"
        assert not (work / "lock").exists() and not recovery.exists()
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        family.stop()


def test_standalone_recovery_cannot_write_shared_log_while_helper_is_live(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    ready, release = tmp_path / "ready", tmp_path / "release"
    (payload / "install.sh").write_text(f'''#!/bin/bash
line=$(printf '%8192s' '')
for ((row=0; row<80; row++)); do printf '%s\\n' "$line"; done
printf 'SERIALIZED-LOG-MARKER\\n'
touch {shlex.quote(str(ready))}
while [ ! -e {shlex.quote(str(release))} ]; do sleep .05; done
exit 1
''')
    proc, family, recovery, work = helper(root, payload, env, tmp_path)
    try:
        wait_for(ready.exists, proc)
        wait_for(lambda: b"SERIALIZED-LOG-MARKER" in (work / "install.log").read_bytes(), proc)
        before = {p.name: p.read_bytes() for p in work.glob("install.log*")}
        refused = subprocess.run(["/bin/sh", str(recovery / "recover.sh"), str(root)],
            env=env, capture_output=True, text=True, timeout=10)
        assert refused.returncode == 3 and "installer is live" in refused.stderr
        assert {p.name: p.read_bytes() for p in work.glob("install.log*")} == before
        assert not (recovery / "recovering").exists()
        release.touch()
        assert proc.wait(timeout=10) == 0
    finally:
        release.touch()
        family.stop()


def test_two_standalone_recoveries_serialize_actual_rotating_writer(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    recovery, work = stage_recovery(root, env, tmp_path)
    ready, release = tmp_path / "ready", tmp_path / "release"
    entry = recovery / "recover.sh"
    key = 'exec 1>"$log_fifo" 2>&1\n'
    body = entry.read_text()
    assert body.count(key) == 1
    body = body.replace(key, key + f'''
line=$(printf '%8192s' '')
row=0
while [ "$row" -lt 80 ]; do printf '%s\\n' "$line"; row=$((row + 1)); done
printf 'EXCLUSIVE-RECOVERY-MARKER\\n'
touch {shlex.quote(str(ready))}
while [ ! -e {shlex.quote(str(release))} ]; do sleep .05; done
''', 1)
    entry.write_text(body)
    proc = subprocess.Popen(["/bin/sh", str(entry), str(root)], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    family = InstallerFamily(proc)
    try:
        wait_for(ready.exists, proc)
        wait_for(lambda: b"EXCLUSIVE-RECOVERY-MARKER" in (work / "install.log").read_bytes(), proc)
        before = {p.name: p.read_bytes() for p in work.glob("install.log*")}
        refused = subprocess.run(["/bin/sh", str(entry), str(root)], env=env,
            capture_output=True, text=True, timeout=10)
        assert refused.returncode == 3 and "installer is live" in refused.stderr
        assert {p.name: p.read_bytes() for p in work.glob("install.log*")} == before
        assert max(map(len, before.values())) <= 262144
        release.touch()
        assert proc.wait(timeout=10) == 0
        assert not recovery.exists()
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        release.touch()
        family.stop()


@pytest.mark.parametrize("record", ["logger", "writer"])
def test_pid_record_failure_reaps_actual_owned_child_and_preserves_foreign_record(tmp_path, record):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    finished = tmp_path / "native-finished"
    (payload / "install.sh").write_text(f'''#!/bin/bash
trap - PIPE
for ((row=0; row<80; row++)); do printf '%8192s\\n' '' || exit 99; done
touch {shlex.quote(str(finished))}
exit 0
''')
    observed = tmp_path / "owned-child"
    script = tmp_path / "helper-record-fault.sh"
    body = (ROOT / "launchers/installer-helper.sh").read_text()
    key = '    logger=$!\n' if record == "logger" else '        writer=$!\n'
    assert body.count(key) == 1
    body = body.replace(key, key + f'''        printf '%s' "${record}" > {shlex.quote(str(observed))}
        rm -f "$log_records/{record}"
        mkdir "$log_records/{record}"
        printf precious > "$log_records/{record}/precious"
''', 1)
    script.write_text(body)
    proc, family, _recovery, work = helper(root, payload, env, tmp_path, script_path=script)
    try:
        assert proc.wait(timeout=15) == (1 if record == "logger" else 0)
        assert finished.exists() is (record == "writer")
        with pytest.raises(ProcessLookupError):
            os.kill(int(observed.read_text()), 0)
        assert (work / "lock" / record / "precious").read_text() == "precious"
        assert json.loads((work / "outcome.json").read_text())["result"] == ("failed" if record == "logger" else "installed")
        assert json.loads((work / "install.log.error").read_text())["kind"] == "installer_log_error"
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        family.stop()


@pytest.mark.parametrize("failure", ["foreign-log", "writer-io", "fifo-create", "fifo-identity", "foreign-fifo"])
def test_killed_native_still_recovers_exact_old_objects_after_diagnostic_failure(tmp_path, failure):
    env = sandbox(tmp_path)
    assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    original = (root.stat().st_dev, root.stat().st_ino)
    payload = native_layout(tmp_path, env, "new")
    completed = tmp_path / "recovery-completed-output"
    foreign = tmp_path / "foreign-diagnostic"
    foreign.write_text("precious diagnostic occupant")

    def prepare(recovery, work):
        script = payload / "install.sh"
        key = '    STATE[i]=displaced\n'
        fault = ''
        if failure == "foreign-log":
            fault = f'rm -f {shlex.quote(str(work / "install.log"))}; ln -s {shlex.quote(str(foreign))} {shlex.quote(str(work / "install.log"))}; '
        assert script.read_text().count(key) == 1
        script.write_text(script.read_text().replace(key, key + f'    if [ "$i" -eq 0 ]; then {fault}kill -KILL $$; fi\n', 1))
        entry = recovery / "recover.sh"
        body = entry.read_text()
        if failure == "writer-io":
            key = '        "$recovery/log" --writer-record "$log_records/writer" <&6 &\n'
            assert body.count(key) == 1
            body = body.replace(key, '        ulimit -f 1\n' + key, 1)
        if failure in ("fifo-create", "fifo-identity", "foreign-fifo"):
            key = 'if prepare_log && mkfifo "$log_fifo" && log_fifo_id=$(object_id "$log_fifo"); then\n'
            assert body.count(key) == 1
            if failure == "fifo-create":
                replacement = 'if prepare_log && false; then\n'
            elif failure == "fifo-identity":
                replacement = 'if prepare_log && mkfifo "$log_fifo" && false; then\n'
            else:
                replacement = 'mkdir "$log_fifo"; printf precious > "$log_fifo/precious"\nif prepare_log && mkfifo "$log_fifo"; then\n'
            body = body.replace(key, replacement, 1)
        key = "move_owned() {\n"
        assert body.count(key) == 1
        body = body.replace(key, f'''line=$(printf '%8192s' '')
flood_row=0
while [ "$flood_row" -lt 80 ]; do
    printf '%s\\n' "$line" || exit 99
    flood_row=$((flood_row + 1))
done
touch {shlex.quote(str(completed))}
''' + key, 1)
        entry.write_text(body)

    proc, family, recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    try:
        assert proc.wait(timeout=30) == 0
        assert completed.exists()
        assert (root / "version.txt").read_text() == "old"
        assert (root.stat().st_dev, root.stat().st_ino) == original
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "failed" and outcome["previousKept"] is True
        assert json.loads((work / "install.log.error").read_text())["kind"] == "installer_log_error"
        if failure == "foreign-log":
            assert (work / "install.log").is_symlink() and foreign.read_text() == "precious diagnostic occupant"
        else:
            assert (work / "install.log").stat().st_size <= 262144
        assert not recovery.exists() and not (work / "lock").exists()
        archived = list(recovery.parent.glob(recovery.name + ".decided.*"))
        if failure in ("fifo-identity", "foreign-fifo"):
            assert len(archived) == 1
            output = archived[0] / "recovering/output"
            if failure == "fifo-identity":
                assert output.is_fifo()
            else:
                assert (output / "precious").read_text() == "precious"
        else:
            assert not archived
        wait_for(lambda: not family.live(), seconds=5)
    finally:
        family.stop()


def test_recovery_move_clocks_are_reaped_before_fifo_drain(tmp_path):
    env = sandbox(tmp_path)
    assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    payload = native_layout(tmp_path, env, "new")
    trace = tmp_path / "owned-recovery-children"
    barrier = tmp_path / "release-recovery-mover"

    def prepare(recovery, _work):
        script = payload / "install.sh"
        key = '    STATE[i]=displaced\n'
        assert script.read_text().count(key) == 1
        script.write_text(script.read_text().replace(key, key + '    [ "$i" -ne 0 ] || kill -KILL $$\n', 1))
        entry = recovery / "recover.sh"
        body = entry.read_text()
        key = '    mv -n "$source" "$destination" &\n'
        assert body.count(key) == 1
        body = body.replace(key, f'    (while [ ! -e {shlex.quote(str(barrier))} ]; do sleep .01; done; exec mv -n "$source" "$destination") &\n', 1)
        # Observation only: capture actual PID and actual OS parent while
        # each fork is still this recovery process's unreaped child.
        for variable in ("mover", "recovery_clock", "recovery_watchdog"):
            key = f'    {variable}=$!\n'
            assert body.count(key) == 1
            body = body.replace(key, key + f'''    printf '{variable} %s %s\\n' "${variable}" "$(ps -o ppid= -p "${variable}" | tr -d ' ')" >> {shlex.quote(str(trace))}
''', 1)
        key = '    recovery_watchdog=$!\n'
        # The observation was appended after capture; release only after it.
        boundary = body.index(' >> ' + shlex.quote(str(trace)), body.index(key))
        boundary = body.index("\n", boundary) + 1
        body = body[:boundary] + f"    touch {shlex.quote(str(barrier))}\n" + body[boundary:]
        entry.write_text(body)

    proc, family, recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    try:
        wait_for(lambda: trace.exists() and len(trace.read_text().splitlines()) == 3, proc)
        family.live()
        started = time.monotonic()
        assert proc.wait(timeout=4) == 0
        assert time.monotonic() - started < 4
        rows = [line.split() for line in trace.read_text().splitlines()]
        assert all(len(row) == 3 for row in rows)
        assert len({row[2] for row in rows}) == 1  # the actual same recovery parent
        for _name, pid, _parent in rows:
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid), 0)
        assert not (work / "install.log.error").exists()
        assert json.loads((work / "outcome.json").read_text())["previousKept"] is True
        assert not recovery.exists()
        wait_for(lambda: not family.live(), seconds=1)
    finally:
        family.stop()
