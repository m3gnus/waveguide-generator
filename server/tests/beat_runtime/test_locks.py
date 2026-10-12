from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import locks, paths, state


_CHILD = """
import sys
from pathlib import Path
from server.solver.beat_runtime.locks import provisioning_lock
directory, backend = Path(sys.argv[1]), sys.argv[2]
def status(message):
    (directory / (backend + '.waiting')).write_text(message)
with provisioning_lock(directory, backend=backend, status_cb=status):
    (directory / (backend + '.acquired')).write_text('yes')
    sys.stdin.readline()
"""


@pytest.fixture
def children():
    processes = []

    def start(directory, backend):
        root = Path(__file__).resolve().parents[3]
        env = dict(os.environ, PYTHONPATH=str(root))
        process = subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(directory), backend], cwd=root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        processes.append(process)
        return process

    yield start
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def _wait_for(path, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        assert process.poll() is None, process.communicate(timeout=5)
        time.sleep(0.02)
    pytest.fail(f"Child {process.pid} did not create {path.name}")


@pytest.mark.parametrize("owner_dies", [False, True])
def test_two_process_exclusion_owner_death_and_persistent_inode(tmp_path, children, owner_dies):
    directory = tmp_path / "runtime"
    holder = children(directory, "metal")
    _wait_for(directory / "metal.acquired", holder)
    lock_path = directory / locks.LOCK_FILENAME
    inode = lock_path.stat().st_ino
    contender = children(directory, "cpu")
    _wait_for(directory / "cpu.waiting", contender)
    assert not (directory / "cpu.acquired").exists()
    assert "metal" in (directory / "cpu.waiting").read_text()
    if owner_dies:
        holder.kill()
        holder.communicate(timeout=5)
    else:
        holder.communicate(input="\n", timeout=5)
        assert holder.returncode == 0
    _wait_for(directory / "cpu.acquired", contender)
    contender.communicate(input="\n", timeout=5)
    assert contender.returncode == 0
    assert lock_path.stat().st_ino == inode
    with locks.provisioning_lock(directory, backend="cpu"):
        assert lock_path.stat().st_ino == inode
    assert lock_path.stat().st_ino == inode


def test_same_process_exclusion_and_release_on_exception(tmp_path):
    with pytest.raises(RuntimeError, match="failed"):
        with locks.provisioning_lock(tmp_path, backend="metal"):
            descriptor = os.open(tmp_path / locks.LOCK_FILENAME, os.O_RDWR)
            try:
                assert not locks._try_lock(descriptor)
            finally:
                os.close(descriptor)
            raise RuntimeError("provisioning failed")
    with locks.provisioning_lock(tmp_path, backend="cpu"):
        assert locks.lock_holder(tmp_path)["backend"] == "cpu"


def test_holder_diagnostics_are_optional_and_never_signal(tmp_path, monkeypatch):
    (tmp_path / locks.HOLDER_FILENAME).write_text(json.dumps({
        "provider": paths.PROVIDER_ID, "pid": 123456, "backend": "metal",
    }))

    def forbidden(*args):
        raise AssertionError("a holder PID was signaled")

    def refuse(*args):
        raise OSError("read-only diagnostics")

    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(state, "_atomic_write_json", refuse)
    with locks.provisioning_lock(tmp_path, backend="cpu"):
        pass
    assert (tmp_path / locks.LOCK_FILENAME).exists()
    for payload in ("{", "[]", '{"provider":"hbb","pid":123}'):
        (tmp_path / locks.HOLDER_FILENAME).write_text(payload)
        assert locks.lock_holder(tmp_path) == {}


def test_wait_message_once_and_callback_failure_cannot_break_lock(tmp_path, monkeypatch):
    results = iter([False, False, True])
    monkeypatch.setattr(locks, "_try_lock", lambda descriptor: next(results))
    monkeypatch.setattr(locks.time, "sleep", lambda seconds: None)
    calls = []

    def broken(message):
        calls.append(message)
        raise UnicodeEncodeError("cp1252", "\u2713", 0, 1, "cannot encode")

    with locks.provisioning_lock(tmp_path, backend="cpu", status_cb=broken):
        assert locks.lock_holder(tmp_path)["pid"] == os.getpid()
    assert len(calls) == 1


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("error", [errno.EACCES, errno.EAGAIN, errno.ENOLCK, errno.EINVAL])
def test_only_contention_is_retried(tmp_path, monkeypatch, windows, error):
    def fail(*args):
        raise OSError(error, "lock operation failed")

    module = (SimpleNamespace(locking=fail, LK_NBLCK=1) if windows else
              SimpleNamespace(flock=fail, LOCK_EX=2, LOCK_NB=4))
    monkeypatch.setattr(locks, "_WINDOWS", windows)
    monkeypatch.setitem(sys.modules, "msvcrt" if windows else "fcntl", module)
    descriptor = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if error in (errno.EACCES, errno.EAGAIN):
            assert locks._try_lock(descriptor) is False
        else:
            with pytest.raises(OSError, match="lock operation failed"):
                locks._try_lock(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("initial", [b"", b"persistent contents"])
def test_windows_byte_zero_initialization_seek_unlock_and_no_truncation(tmp_path, monkeypatch, initial):
    lock_path = tmp_path / locks.LOCK_FILENAME
    lock_path.write_bytes(initial)
    inode = lock_path.stat().st_ino
    calls = []

    def locking(descriptor, mode, length):
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0
        assert os.fstat(descriptor).st_size >= 1
        calls.append((mode, length))
        os.lseek(descriptor, 1, os.SEEK_SET)  # Next call must seek back.

    monkeypatch.setattr(locks, "_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        locking=locking, LK_NBLCK=10, LK_UNLCK=11,
    ))
    for _ in range(2):
        with locks.provisioning_lock(tmp_path, backend="cpu"):
            pass
    assert calls == [(10, 1), (11, 1)] * 2
    assert lock_path.read_bytes() == (initial or b"\0")
    assert lock_path.stat().st_ino == inode


def test_acquisition_error_closes_descriptor_without_entering(tmp_path, monkeypatch):
    descriptors = []

    def refuse(descriptor):
        descriptors.append(descriptor)
        raise OSError(errno.ENOLCK, "unsupported filesystem")

    monkeypatch.setattr(locks, "_try_lock", refuse)
    with pytest.raises(OSError, match="unsupported filesystem"):
        with locks.provisioning_lock(tmp_path, backend="cpu"):
            pytest.fail("entered without a lock")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert (tmp_path / locks.LOCK_FILENAME).exists()


def test_windows_initializer_race_waits_for_competing_holder(tmp_path, monkeypatch):
    original_write = os.write
    attempts = []

    def competing_write(descriptor, content):
        original_write(descriptor, content)  # Another process fills and locks it.
        raise OSError(errno.EACCES, "byte locked by competing initializer")

    def locking(descriptor, mode, length):
        assert (os.lseek(descriptor, 0, os.SEEK_CUR), length) == (0, 1)
        attempts.append(mode)
        if len(attempts) == 1:
            raise OSError(errno.EAGAIN, "competing holder")

    monkeypatch.setattr(locks, "_WINDOWS", True)
    monkeypatch.setattr(os, "write", competing_write)
    monkeypatch.setattr(locks.time, "sleep", lambda seconds: None)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        locking=locking, LK_NBLCK=10, LK_UNLCK=11,
    ))
    messages = []
    with locks.provisioning_lock(tmp_path, backend="cpu", status_cb=messages.append):
        assert (tmp_path / locks.LOCK_FILENAME).read_bytes() == b"\0"
    assert attempts == [10, 10, 11]
    assert len(messages) == 1


def test_default_root_is_provider_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path))
    with locks.provisioning_lock(backend="cpu"):
        assert locks.lock_holder()["provider"] == paths.PROVIDER_ID
    assert (paths.runtime_dir() / locks.LOCK_FILENAME).exists()


@pytest.mark.parametrize("alias", [False, True])
def test_explicit_lock_directory_hbb_isolation_before_mutation(tmp_path, monkeypatch, alias):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_WORKER_DIR", str(legacy))
    root = tmp_path / "alias" if alias else legacy
    if alias:
        root.symlink_to(legacy, target_is_directory=True)
    with pytest.raises(paths.RootConflict):
        with locks.provisioning_lock(root, backend="cpu"):
            pytest.fail("entered HBB lock")
    assert list(legacy.iterdir()) == []


def test_symlinked_lock_file_cannot_touch_hbb(tmp_path, monkeypatch):
    root = tmp_path / "wg"
    root.mkdir()
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    keep = legacy / "lock"
    keep.write_bytes(b"HBB lock")
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    (root / locks.LOCK_FILENAME).symlink_to(keep)
    with pytest.raises(ValueError, match="Linked provisioning"):
        with locks.provisioning_lock(root, backend="cpu"):
            pytest.fail("opened linked lock")
    assert keep.read_bytes() == b"HBB lock"
    assert not (root / locks.HOLDER_FILENAME).exists()
