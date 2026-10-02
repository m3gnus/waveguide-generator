"""Actual retained-object logger races and foreground progress with a stuck sink."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX native diagnostics", allow_module_level=True)

from scripts.tests.test_installer_helper import ROOT, helper, wait_for
from scripts.tests.test_installer_journal import native_logger, sandbox
from scripts.tests.test_linux_bundle_install_update import InstallerFamily


def logger(path, source=None):
    if source is None:
        return native_logger()
    source_path = path / "logger.c"
    source_path.write_text(source)
    binary = path / "logger"
    compiled = subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
        "-o", str(binary), str(source_path)], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    return binary


def start_logger(binary, path):
    record = path / "writer"
    record.write_text("")
    proc = subprocess.Popen([str(binary), "--writer-record", str(record)],
        env={**os.environ, "WG_INSTALLER_LOG": str(path / "install.log")},
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    record.write_text(str(proc.pid) + "\n")
    return proc, InstallerFamily(proc)


@pytest.mark.parametrize("leaf", ["install.log", "install.log.1"])
@pytest.mark.parametrize("kind", ["dangling-symlink", "fifo"])
def test_capture_after_preparation_never_creates_foreign_referent_or_blocks(tmp_path, leaf, kind):
    # Real admission and a later capture are separate opens. Substitute the
    # nominated slot between them, then exercise the actual native capture.
    active = tmp_path / "install.log"
    subprocess.run([str(native_logger()), "--check"],
        env={**os.environ, "WG_INSTALLER_LOG": str(active)}, check=True, timeout=10)
    candidate = tmp_path / leaf
    candidate.unlink()
    foreign = tmp_path / "must-not-be-created"
    if kind == "dangling-symlink":
        candidate.symlink_to(foreign)
    else:
        os.mkfifo(candidate)
    identity = candidate.lstat()
    proc, family = start_logger(native_logger(), tmp_path)
    try:
        started = time.monotonic()
        proc.communicate(b"OUTPUT\n", timeout=8)
        assert proc.returncode == 72 and time.monotonic() - started < 2
        assert not foreign.exists()
        after = candidate.lstat()
        assert (after.st_dev, after.st_ino, after.st_mode) == (identity.st_dev, identity.st_ino, identity.st_mode)
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        family.stop()


@pytest.mark.parametrize("leaf", ["install.log", "install.log.1"])
def test_substitution_inside_rollover_only_truncates_retained_owned_object(tmp_path, leaf):
    ready, release = tmp_path / "rollover-ready", tmp_path / "rollover-release"
    source = (ROOT / "launchers/installer-log.c").read_text()
    key = '    if (ftruncate(fd, 0) != 0 || lseek(fd, 0, SEEK_SET) < 0) return -1;\n'
    assert source.count(key) == 1
    comparison = "active" if leaf == "install.log" else "previous"
    boundary = f'''    if (fd == {comparison}) {{
        int marker = open({json.dumps(str(ready))}, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (marker < 0) return -1;
        close(marker);
        while (access({json.dumps(str(release))}, F_OK) != 0) {{
            struct timespec pause = {{0, 10000000}}; nanosleep(&pause, NULL);
        }}
    }}
'''
    binary = logger(tmp_path, source.replace(key, boundary + key, 1))
    (tmp_path / "install.log").write_bytes(b"[WG installer output]\nOLD-ACTIVE\n" + b"x" * 260000)
    (tmp_path / "install.log.1").write_bytes(b"[WG installer output]\nOLDER-ROTATION\n")
    foreign = tmp_path / "precious"
    foreign.write_bytes(b"precious foreign diagnostic contents")
    foreign_identity = foreign.stat()
    proc, family = start_logger(binary, tmp_path)
    try:
        proc.stdin.write(b"FLOOD:" + b"x" * 8192 + b"\n")
        proc.stdin.flush()
        wait_for(ready.exists, proc)
        candidate = tmp_path / leaf
        retained = tmp_path / "retained-owned-slot"
        candidate.rename(retained)
        retained_identity = retained.stat()
        candidate.symlink_to(foreign)
        release.touch()
        proc.stdin.close()
        assert proc.wait(timeout=8) == 72
        assert candidate.is_symlink() and foreign.read_bytes() == b"precious foreign diagnostic contents"
        assert (foreign.stat().st_dev, foreign.stat().st_ino) == (foreign_identity.st_dev, foreign_identity.st_ino)
        assert (retained.stat().st_dev, retained.stat().st_ino) == (retained_identity.st_dev, retained_identity.st_ino)
        assert retained.stat().st_size <= 262144
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        release.touch()
        family.stop()


def test_stopped_sink_cannot_block_foreground_native_flood_or_change_verdict(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    ready, release, finished = (tmp_path / name for name in ("ready", "release", "finished"))
    (payload / "install.sh").write_text(f'''#!/bin/bash
trap - PIPE
printf 'SINK-CAPTURED\n'
touch {shlex.quote(str(ready))}
while [ ! -e {shlex.quote(str(release))} ]; do sleep .01; done
for ((row=0; row<80; row++)); do printf '%8192s\n' '' || exit 99; done
touch {shlex.quote(str(finished))}
exit 0
''')
    proc, family, recovery, work = helper(root, payload, env, tmp_path)
    try:
        sink_record = work / "lock/sink"
        wait_for(lambda: ready.exists() and sink_record.exists() and sink_record.read_text().strip().isdigit(), proc)
        wait_for(lambda: b"SINK-CAPTURED" in (work / "install.log").read_bytes(), proc)
        sink = int(sink_record.read_text())
        family.live()
        os.kill(sink, signal.SIGSTOP)
        started = time.monotonic()
        release.touch()
        assert proc.wait(timeout=10) == 0 and time.monotonic() - started < 9
        assert finished.exists()
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "installed" and "output could not be retained" in outcome["reason"]
        assert json.loads((work / "install.log.error").read_text())["kind"] == "installer_log_error"
        with pytest.raises(ProcessLookupError):
            os.kill(sink, 0)
        assert not recovery.exists() and not (work / "lock").exists()
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        release.touch()
        family.stop()


def test_live_sink_survives_dead_recorded_owners_and_refuses_a_second_recovery(tmp_path):
    from scripts.tests.test_installer_journal import stage_recovery
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    recovery, work = stage_recovery(root, env, tmp_path)
    lock = work / "lock"
    lock.mkdir()
    for name in ("pid", "logger"):
        (lock / name).write_text("999999999\n")
    os.mkfifo(lock / "output")
    proc, family = start_logger(native_logger(), lock)
    sink = None
    try:
        proc.stdin.write(b"CAPTURED-BEFORE-OWNER-DEATH\n")
        proc.stdin.flush()
        sink_record = lock / "sink"
        wait_for(lambda: sink_record.exists() and sink_record.read_text().strip().isdigit(), proc)
        wait_for(lambda: (lock / "install.log").exists() and b"CAPTURED-BEFORE-OWNER-DEATH" in (lock / "install.log").read_bytes(), proc)
        sink = int(sink_record.read_text())
        family.live()
        os.kill(sink, signal.SIGSTOP)
        proc.kill()
        assert proc.wait(timeout=5) == -signal.SIGKILL
        proc.stdin.close()
        before = {p.name: p.read_bytes() for p in lock.glob("install.log*")}
        refused = subprocess.run(["/bin/sh", str(recovery / "recover.sh"), str(root)],
            env=env, capture_output=True, text=True, timeout=10)
        assert refused.returncode == 3 and "helper is live or its lock is ambiguous" in refused.stderr
        assert {p.name: p.read_bytes() for p in lock.glob("install.log*")} == before
        assert not (recovery / "recovering").exists() and not (work / "outcome.json").exists()
        os.kill(sink, 0)  # the actual surviving sink caused this refusal
    finally:
        if sink is not None:
            try:
                os.kill(sink, signal.SIGKILL)
            except ProcessLookupError:
                pass
        family.stop()


def test_packaged_logger_is_built_before_recovery_manifest_and_hashes_binary(tmp_path):
    from scripts.build_bundle import write_recovery_layer, LINUX_PLATFORM
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    recovery = write_recovery_layer(tmp_path, repo_root=ROOT, runtime_root=runtime, platform_name=LINUX_PLATFORM)
    manifest = json.loads((recovery / "RECOVERY-MANIFEST.json").read_text())
    import hashlib
    assert manifest["installerLogger"] == "installer-log"
    assert manifest["installerLoggerSha256"] == hashlib.sha256((recovery / "installer-log").read_bytes()).hexdigest()
    proc, family = start_logger(recovery / "installer-log", tmp_path)
    try:
        proc.communicate(b"ACTUAL-PACKAGED-NATIVE-ENTRY\n", timeout=10)
        assert proc.returncode == 0 and b"ACTUAL-PACKAGED-NATIVE-ENTRY" in (tmp_path / "install.log").read_bytes()
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        family.stop()


@pytest.mark.parametrize("owner", ["helper", "recovery"])
@pytest.mark.parametrize("failure", ["supervisor-death", "sink-not-settled"])
def test_logger_failure_preserves_actual_surviving_sink_claim(tmp_path, owner, failure):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "waveguide-generator").write_text("#!/bin/sh\nexit 0\n")
    (root / "waveguide-generator").chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    ready, release, finished = (tmp_path / name for name in ("ready", "release", "finished"))
    (payload / "install.sh").write_text(f'''#!/bin/bash
trap - PIPE
printf 'SUPERVISOR-FAILURE-BOUNDARY\n'
touch {shlex.quote(str(ready))}
while [ ! -e {shlex.quote(str(release))} ]; do sleep .01; done
for ((row=0; row<80; row++)); do printf '%8192s\n' '' || exit 99; done
touch {shlex.quote(str(finished))}
exit 0
''')
    prepare = None
    if owner == "recovery":
        from scripts.tests.test_installer_helper import native_layout
        from scripts.tests.test_linux_bundle_install_update import run, installed_dir
        assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
        root = installed_dir(env)
        old_identity = (root.stat().st_dev, root.stat().st_ino)
        payload = native_layout(tmp_path, env, "new")
        native = payload / "install.sh"
        key = '    STATE[i]=displaced\n'
        assert native.read_text().count(key) == 1
        native.write_text(native.read_text().replace(key, key + '    [ "$i" -ne 0 ] || kill -KILL $$\n', 1))
        def prepare(recovery, _work):
            entry = recovery / "recover.sh"
            key = 'exec 1>"$log_fifo" 2>&1\n'
            assert entry.read_text().count(key) == 1
            body = f"""printf 'SUPERVISOR-FAILURE-BOUNDARY\n'
touch {shlex.quote(str(ready))}
while [ ! -e {shlex.quote(str(release))} ]; do sleep .01; done
row=0
while [ "$row" -lt 80 ]; do printf '%8192s\n' '' || exit 99; row=$((row + 1)); done
touch {shlex.quote(str(finished))}
"""
            entry.write_text(entry.read_text().replace(key, key + body, 1))
    if failure == "sink-not-settled":
        source = (ROOT / "launchers/installer-log.c").read_text()
        key = "            kill(child, SIGKILL);\n"
        assert source.count(key) == 2
        # Interpose only the captured-child signal, simulating a kill request
        # that has not settled blocking file I/O. All drain/reap code is real.
        binary = logger(tmp_path, source.replace(key, "            (void)child; /* injected unsettled kill */\n"))
        original_prepare = prepare
        def prepare(recovery, work):
            import shutil
            shutil.copyfile(binary, recovery / "log")
            (recovery / "log").chmod(0o700)
            if original_prepare is not None:
                original_prepare(recovery, work)
    proc, family, recovery, work = helper(root, payload, env, tmp_path, prepare=prepare)
    sink = None
    try:
        lock = work / "lock" if owner == "helper" else recovery / "recovering"
        wait_for(lambda: ready.exists() and (lock / "sink").exists() and (lock / "sink").read_text().strip().isdigit(), proc)
        wait_for(lambda: b"SUPERVISOR-FAILURE-BOUNDARY" in (work / "install.log").read_bytes(), proc)
        sink, supervisor = int((lock / "sink").read_text()), int((lock / "writer").read_text())
        identity = lock.stat()
        family.live()
        os.kill(sink, signal.SIGSTOP)
        if failure == "supervisor-death":
            os.kill(supervisor, signal.SIGKILL)
        started = time.monotonic()
        release.touch()
        assert proc.wait(timeout=10) == 0 and finished.exists()
        assert time.monotonic() - started < 9
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == ("installed" if owner == "helper" else "failed")
        if owner == "helper":
            assert "output could not be retained" in outcome["reason"]
        else:
            assert outcome["reason"] == "An interrupted installer was recovered."
        assert json.loads((work / "install.log.error").read_text())["kind"] == "installer_log_error"
        if owner == "recovery":
            assert outcome["previousKept"] is True
            assert (root.stat().st_dev, root.stat().st_ino) == old_identity
            assert (root / "version.txt").read_text() == "old"
        lock = work / "lock"
        assert not recovery.exists()
        assert (lock.stat().st_dev, lock.stat().st_ino) == (identity.st_dev, identity.st_ino)
        assert int((lock / "sink").read_text()) == sink
        os.kill(sink, 0)
        assert set(p.name for p in lock.iterdir()) == {"pid", "logger", "writer", "sink", "output"}
        # A second helper refuses before it can alter logs or spawn a writer.
        before = (work / "install.log").read_bytes()
        refused = subprocess.run(["/bin/sh", str(ROOT / "launchers/installer-helper.sh"),
            "linux-x86_64", "unused", "unused", str(root), "999999999", str(work), "unused", "unused", "unused", str(recovery)],
            env=env, capture_output=True, text=True, timeout=5)
        assert refused.returncode == 4 and (work / "install.log").read_bytes() == before
    finally:
        release.touch()
        if sink is not None:
            try: os.kill(sink, signal.SIGKILL)
            except ProcessLookupError: pass
        family.stop()


def test_unsettled_sink_after_eof_has_bounded_reap_and_keeps_record(tmp_path):
    source = (ROOT / "launchers/installer-log.c").read_text()
    key = "            kill(child, SIGKILL);\n"
    assert source.count(key) == 2
    binary = logger(tmp_path, source.replace(key, "            (void)child; /* injected unsettled kill */\n"))
    proc, family = start_logger(binary, tmp_path)
    sink = None
    try:
        proc.stdin.write(b"ACTUAL-SINK-CAPTURED\n")
        proc.stdin.flush()
        wait_for(lambda: (tmp_path / "install.log").exists() and
            b"ACTUAL-SINK-CAPTURED" in (tmp_path / "install.log").read_bytes(), proc)
        sink_record = tmp_path / "sink"
        sink = int(sink_record.read_text())
        family.live()
        os.kill(sink, signal.SIGSTOP)
        started = time.monotonic()
        proc.stdin.close()
        assert proc.wait(timeout=8) == 72
        assert time.monotonic() - started < 7
        assert int(sink_record.read_text()) == sink
        os.kill(sink, 0)
    finally:
        if sink is not None:
            try: os.kill(sink, signal.SIGKILL)
            except ProcessLookupError: pass
        family.stop()


def test_admitted_recovery_never_runs_synchronous_diagnostic_capture(tmp_path):
    from scripts.tests.test_installer_journal import stage_recovery
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    original = (root.stat().st_dev, root.stat().st_ino)
    recovery, work = stage_recovery(root, env, tmp_path)
    checked = tmp_path / "diagnostic-check-ran"
    entry = recovery / "log"
    entry.write_text(f'''#!/bin/sh
if [ "$1" = --check ]; then
    touch {shlex.quote(str(checked))}
    sleep 60
    exit 72
fi
exec {shlex.quote(str(native_logger()))} "$@"
''')
    entry.chmod(0o700)
    proc = subprocess.Popen(["/bin/sh", str(recovery / "recover.sh"), str(root)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    family = InstallerFamily(proc)
    try:
        started = time.monotonic()
        stdout, stderr = proc.communicate(timeout=8)
        assert proc.returncode == 0, (stdout, stderr)
        assert time.monotonic() - started < 5 and not checked.exists()
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "failed" and outcome["previousKept"] is True
        assert (root.stat().st_dev, root.stat().st_ino) == original
        assert not recovery.exists() and not (work / "lock").exists()
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        family.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual packaged Mach-O deployment floor")
def test_packaged_logger_and_launcher_match_advertised_macos_floor(tmp_path):
    from scripts.build_bundle import write_recovery_layer, write_launcher_stub, MACOS_PLATFORM
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    recovery = write_recovery_layer(tmp_path, repo_root=ROOT, runtime_root=runtime, platform_name=MACOS_PLATFORM)
    launcher = tmp_path / "Waveguide Generator"
    write_launcher_stub(launcher, repo_root=ROOT)
    for binary in (recovery / "installer-log", launcher):
        inspected = subprocess.run(["vtool", "-show-build", str(binary)], capture_output=True, text=True, timeout=10)
        assert inspected.returncode == 0, inspected.stderr
        minimums = [row.strip().split()[1] for row in inspected.stdout.splitlines() if row.strip().startswith("minos ")]
        assert minimums == ["11.0"], inspected.stdout


def test_early_checksum_failure_releases_owned_lock_before_logging_starts(tmp_path):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    reached = tmp_path / "native-reached"
    (payload / "install.sh").write_text(f"#!/bin/sh\ntouch {shlex.quote(str(reached))}\n")
    def prepare(_recovery, _work):
        # Replace only the expected digest comparison: failure occurs before
        # prepare_log/start_logger and exercises the real early cleanup trap.
        entry = tmp_path / "wrong-digest-helper.sh"
        text = (ROOT / "launchers/installer-helper.sh").read_text()
        key = '[ "$actual_sha" = "$expected_sha" ] || fail\n'
        assert text.count(key) == 1
        entry.write_text(text.replace(key, '[ "$actual_sha" = impossible ] || fail\n', 1))
    proc, family, _recovery, work = helper(root, payload, env, tmp_path,
        prepare=prepare, script_path=tmp_path / "wrong-digest-helper.sh")
    try:
        assert proc.wait(timeout=5) == 1
        assert not reached.exists() and not (work / "lock").exists()
        assert not (root.parent / ".waveguide-generator.install.lock").exists()
        assert json.loads((work / "outcome.json").read_text())["result"] == "failed"
        wait_for(lambda: not family.live(), seconds=2)
    finally:
        family.stop()


def test_logger_source_changes_shared_runtime_recipe_and_runtime_id(tmp_path):
    from scripts import build_bundle
    from shared.runtime_id import compute_runtime_id
    for name, content in ((build_bundle.WINDOWS_NATIVE_SOURCE, b"windows native"),
                          (build_bundle.WINDOWS_NATIVE_HOOK_SOURCE, b"windows hook"),
                          ("launchers/installer-log.c", b"first POSIX logger")):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    first = build_bundle.runtime_recipe(tmp_path)
    (tmp_path / "launchers/installer-log.c").write_bytes(b"changed POSIX logger")
    second = build_bundle.runtime_recipe(tmp_path)
    assert first != second
    def identity(recipe):
        return compute_runtime_id(python_version="3.13.12", runtime_requirements=b"requirements",
            pinned_requirements=b"pins", locked_requirements=b"lock", python_build=build_bundle.PYTHON_BUILD, runtime_recipe=recipe)
    assert identity(first) != identity(second)


def test_posix_runtime_build_archives_logger_and_manifest_before_bundle_assembly(tmp_path):
    from scripts import build_bundle
    import hashlib
    import zipfile
    def runner(command, **kwargs):
        if command[0] == "cc":
            return subprocess.run(command, **kwargs)
        if command[:3] == ["uv", "python", "install"]:
            installed = Path(command[command.index("--install-dir") + 1]) / "managed"
            (installed / "bin").mkdir(parents=True)
            (installed / "bin/python3.13").write_text("#!/bin/sh\nexit 0\n")
            (installed / "BUILD").write_text(build_bundle.PYTHON_BUILD)
        return subprocess.CompletedProcess(command, 0, b"", b"")
    builder = build_bundle.BundleBuilder(ROOT, runner=runner, system=lambda: "Linux", machine=lambda: "x86_64")
    runtime = tmp_path / "scratch/runtime"
    runtime.parent.mkdir()
    builder.build_runtime(runtime, python_version="3.13.12", python_build=build_bundle.PYTHON_BUILD,
        runtime_recipe=build_bundle.runtime_recipe(ROOT), runtime_id="new-source-bound-runtime",
        requirements=b"runtime", pins=b"pins", lock=b"lock", platform_name=build_bundle.LINUX_PLATFORM)
    archive = tmp_path / "runtime.zip"
    build_bundle.deterministic_zip(runtime, archive)
    with zipfile.ZipFile(archive) as zipped:
        binary = zipped.read("bin/wg-installer-log")
        manifest = json.loads(zipped.read("RUNTIME-MANIFEST.json"))
        assert manifest["installerLogger"] == "bin/wg-installer-log"
        assert manifest["installerLoggerSha256"] == hashlib.sha256(binary).hexdigest()
        assert zipped.getinfo("bin/wg-installer-log").external_attr >> 16 & 0o111
        assert binary == (runtime / "bin/wg-installer-log").read_bytes()
