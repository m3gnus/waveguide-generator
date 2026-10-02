"""Persistent recovery evidence survives a killed POSIX installer."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import json
import shutil
from functools import lru_cache
import atexit
import tempfile

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX installer identity journals", allow_module_level=True)

from scripts.tests.test_linux_bundle_install_update import make_tarball, run, installed_dir, installer_process_signals, late_failure_env


def sandbox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "share")}


@lru_cache(maxsize=1)
def native_logger():
    from scripts.build_bundle import write_installer_logger, LINUX_PLATFORM
    folder = Path(tempfile.mkdtemp(prefix="wg-test-native-logger-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    binary = folder / "installer-log"
    write_installer_logger(binary, repo_root=Path(__file__).resolve().parents[2], platform_name=LINUX_PLATFORM)
    return binary


def stage_recovery(root, env, tmp_path):
    work = tmp_path / "data/update-install"
    work.mkdir(parents=True)
    recovery = root.parent / ("." + root.name + ".installer-recovery")
    recovery.mkdir()
    source = Path(__file__).resolve().parents[2] / "launchers/installer-recovery.sh"
    shutil.copyfile(source, recovery / "recover.sh")
    shutil.copy2(native_logger(), recovery / "log")
    info = root.stat()
    metadata = json.dumps({"from": "0.3.3", "to": "0.3.4", "log": str(work / "install.log")})[:-1]
    (recovery / "config").write_text("\n".join(["WG-INSTALL-RECOVERY-1", str(root),
        f"{info.st_dev}:{info.st_ino}", str(work), metadata, "linux-x86_64", env["HOME"], env["XDG_DATA_HOME"]]) + "\n")
    return recovery, work


def test_kill_after_displacement_preserves_ledger_and_refuses_rerun(tmp_path):
    env = sandbox(tmp_path)
    assert run(make_tarball(tmp_path / "v1", "old"), env, "--no-launch").returncode == 0
    new = make_tarball(tmp_path / "v2", "new")
    recovery, work = stage_recovery(installed_dir(env), env, tmp_path)
    paused = tmp_path / "paused"
    script = new / "install.sh"
    body = script.read_text().replace('    STATE[i]=displaced\n',
        f'    if [ "$i" = 0 ]; then touch "{paused}"; while :; do sleep 1; done; fi\n    STATE[i]=displaced\n', 1)
    script.write_text(body)
    proc = subprocess.Popen(["/bin/bash", str(script), "--skip-checks", "--update"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        start_new_session=True, preexec_fn=installer_process_signals)
    try:
        deadline = time.monotonic() + 20
        while not paused.exists():
            assert proc.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        root = installed_dir(env)
        journal = root.parent / ".waveguide-generator.install.lock.journal"
        rows = journal.read_text().splitlines()
        assert rows[:3] == ["WG-INSTALL-JOURNAL-1", str(root), "6"]
        assert len(rows) == 3 + 6 * 5
        backup = Path(rows[4])
        assert (backup / "version.txt").read_text() == "old"
        assert not root.exists()
        assert rows[6] == f"{backup.stat().st_dev}:{backup.stat().st_ino}"
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    # A new process must not silently delete the dead owner's backup or lock.
    again = run(make_tarball(tmp_path / "v3", "later"), env, "--update")
    assert again.returncode == 3 and "needs recovery" in again.stderr
    assert journal.exists() and (backup / "version.txt").read_text() == "old"
    recovered = subprocess.run(["/bin/sh", str(recovery / "recover.sh"), str(root)],
        env=env, capture_output=True, text=True, timeout=30)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert (root / "version.txt").read_text() == "old"
    outcome = json.loads((work / "outcome.json").read_text())
    assert outcome["result"] == "failed" and outcome["previousKept"] is True
    assert not journal.exists() and not journal.with_name(journal.name.removesuffix(".journal")).exists()
    assert list(journal.parent.glob(journal.name + ".recovered.*"))


def test_status_three_preserves_exact_recovery_ledger(tmp_path):
    env = sandbox(tmp_path)
    assert run(make_tarball(tmp_path / "v1", "old"), env, "--no-launch").returncode == 0
    result = run(make_tarball(tmp_path / "v2", "new"),
        late_failure_env(tmp_path, env, restore_failure="application"), "--update")
    assert result.returncode == 3
    root = installed_dir(env)
    journal = root.parent / ".waveguide-generator.install.lock.journal"
    rows = journal.read_text().splitlines()
    assert (Path(rows[4]) / "version.txt").read_text() == "old"
    assert str(journal) in result.stderr


def test_helper_lock_adoption_requires_identity_and_live_owner(tmp_path):
    env = sandbox(tmp_path)
    assert run(make_tarball(tmp_path / "v1", "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    lock = root.parent / ".waveguide-generator.install.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()) + "\n")
    identity = f"{lock.stat().st_dev}:{lock.stat().st_ino}"
    new = make_tarball(tmp_path / "v2", "new")
    refused = run(new, {**env, "WG_INSTALLER_HANDOFF_LOCK_ID": "0:0", "WG_INSTALLER_HANDOFF_OWNER_PID": str(os.getpid())}, "--update")
    assert refused.returncode == 4
    assert (root / "version.txt").read_text() == "old"
    accepted = run(new, {**env, "WG_INSTALLER_HANDOFF_LOCK_ID": identity, "WG_INSTALLER_HANDOFF_OWNER_PID": str(os.getpid())}, "--update")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert (root / "version.txt").read_text() == "new"
    assert not lock.exists()
    assert not lock.with_name(lock.name + ".journal").exists()


@pytest.mark.parametrize("owner", ["live", "unknown-lock", "foreign-root", "linked-config"])
def test_recovery_refuses_unproven_ownership_and_objects(tmp_path, owner):
    env = sandbox(tmp_path)
    root = tmp_path / "waveguide-generator"
    root.mkdir()
    (root / "previous.txt").write_text("retained")
    recovery, work = stage_recovery(root, env, tmp_path)
    lock = root.parent / ".waveguide-generator.install.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid() if owner == "live" else 999999999) + "\n")
    if owner == "unknown-lock":
        (lock / "foreign").write_text("preserve")
    elif owner == "foreign-root":
        root.rename(root.with_name("displaced-original"))
        root.mkdir()
        (root / "previous.txt").write_text("foreign")
    elif owner == "linked-config":
        config = recovery / "config"
        config.rename(recovery / "original-config")
        config.symlink_to(recovery / "original-config")
    before_root = (root.stat().st_dev, root.stat().st_ino)
    before_lock = sorted(path.name for path in lock.iterdir())
    result = subprocess.run(["/bin/sh", str(recovery / "recover.sh"), str(root)], env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 3
    assert (root.stat().st_dev, root.stat().st_ino) == before_root
    assert sorted(path.name for path in lock.iterdir()) == before_lock
    assert not (work / "outcome.json").exists()
    assert recovery.exists() and not (recovery / "recovering").exists()
