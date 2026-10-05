from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from server.solver.beat_runtime import ipc, paths, registry as r


@pytest.fixture
def record(tmp_path):
    return r.HostRecord(r.host_key({"backend": "cpu", "threads": 2}), 12345,
                        r.new_token(), ipc.Endpoint("tcp", port=1234))


def test_atomic_private_roundtrip_and_launch_spec(tmp_path, record, monkeypatch):
    monkeypatch.setenv(paths.WORKER_DIR_ENV, str(tmp_path))
    root = paths.worker_dir()
    original = os.replace
    seen = []

    def replace(source, destination):
        source = Path(source)
        seen.append(json.loads(source.read_text()))
        if os.name == "posix":
            assert source.stat().st_mode & 0o777 == 0o600
        original(source, destination)

    monkeypatch.setattr(r.os, "replace", replace)
    path = r.write_record(record)
    assert r.read_record(record.identifier) == record
    assert path.parent == root
    assert r.write_launch_spec(record.key).parent == root
    assert len(seen) == 2
    if os.name == "posix":
        assert root.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(root.glob("*.tmp"))


def test_failed_atomic_publish_preserves_old_record(tmp_path, record, monkeypatch):
    r.write_record(record, tmp_path)
    before = r.record_path(record.identifier, tmp_path).read_bytes()
    record.token = r.new_token()

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(r.os, "replace", fail)
    with pytest.raises(OSError):
        r.write_record(record, tmp_path)
    assert r.record_path(record.identifier, tmp_path).read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(("field", "value"), [
    ("provider", "hornlab-beat-bem"), ("protocol", "beat-worker"),
    ("protocol_version", 2), ("protocol_version", True), ("pid", "123"),
    ("pid", -1), ("token", ""), ("key_id", "0123456789abcdef"),
    ("key", {"provider": "other"}),
])
def test_foreign_or_malformed_records_refused_without_overwrite(tmp_path, record, field, value):
    path = r.write_record(record, tmp_path)
    raw = record.as_dict()
    raw[field] = value
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(r.RecordRefused):
        r.read_record(record.identifier, tmp_path)
    with pytest.raises(r.RecordRefused):
        r.write_record(record, tmp_path)
    assert path.read_bytes() == before


def test_missing_corrupt_linked_and_nonprivate_records(tmp_path, record):
    assert r.read_record(record.identifier, tmp_path) is None
    path = r.write_record(record, tmp_path)
    path.write_text("{")
    with pytest.raises(r.RecordRefused):
        r.read_record(record.identifier, tmp_path)
    if os.name == "posix":
        path.chmod(0o644)
        with pytest.raises(r.RecordRefused, match="owner-private"):
            r.read_record(record.identifier, tmp_path)
        path.unlink()
        foreign = tmp_path / "foreign"
        foreign.write_text("keep")
        path.symlink_to(foreign)
        with pytest.raises(r.RecordRefused, match="Linked"):
            r.read_record(record.identifier, tmp_path)
        assert foreign.read_text() == "keep"


def test_foreign_socket_and_key_cannot_be_used(tmp_path, record):
    record.endpoint = ipc.Endpoint("unix", path=tmp_path / "foreign.sock")
    with pytest.raises(r.RecordRefused, match="outside"):
        r.write_record(record, tmp_path)
    with pytest.raises(r.RecordRefused):
        r.validate_record(record, r.host_key({"threads": 9}), tmp_path)
    assert r.key_id(record.key) != r.key_id({**record.key, "provider": "foreign"})


_RACE = """
import sys, time
from pathlib import Path
from server.solver.beat_runtime.registry import *
from server.solver.beat_runtime.ipc import Endpoint
root = Path(sys.argv[1])
key = host_key({'backend': 'cpu'})
with SpawnLock(spawn_lock_path(key_id(key), root), timeout=3):
    if read_record(key_id(key), root) is None:
        time.sleep(0.05)
        write_record(HostRecord(key, 12345, new_token(), Endpoint('tcp', port=1234)), root)
        print('spawn')
    else:
        print('reuse')
"""


def test_two_process_spawn_race_rechecks_and_persistent_inode(tmp_path):
    processes = [subprocess.Popen([sys.executable, "-c", _RACE, str(tmp_path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    try:
        outputs = []
        for process in processes:
            out, err = process.communicate(timeout=5)
            assert process.returncode == 0, err
            outputs.append(out.strip())
        assert sorted(outputs) == ["reuse", "spawn"]
        path = r.spawn_lock_path(r.key_id(r.host_key({"backend": "cpu"})), tmp_path)
        inode = path.stat().st_ino
        with r.SpawnLock(path):
            assert path.stat().st_ino == inode
        assert path.stat().st_ino == inode
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_spawn_timeout_closes_handle_and_owner_death_releases(tmp_path):
    script = """
import sys, time
from pathlib import Path
from server.solver.beat_runtime.registry import SpawnLock
with SpawnLock(Path(sys.argv[1])):
    print('locked', flush=True)
    time.sleep(10)
"""
    path = tmp_path / "test.lock"
    process = subprocess.Popen([sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(TimeoutError):
            with r.SpawnLock(path, timeout=0.05):
                pytest.fail("lock acquired twice")
    finally:
        process.kill()
        process.communicate(timeout=5)
    with r.SpawnLock(path, timeout=0.5):
        assert path.exists()


def test_windows_lock_uses_fixed_byte_and_release(tmp_path, monkeypatch):
    calls = []
    fake = types.SimpleNamespace(LK_UNLCK=0, LK_NBLCK=1,
        locking=lambda fd, mode, size: calls.append((os.lseek(fd, 0, os.SEEK_CUR), mode, size)))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    with r.SpawnLock(tmp_path / "windows.lock"):
        pass
    assert calls == [(0, 1, 1), (0, 0, 1)]


@pytest.mark.parametrize(("error", "expected"), [
    (ProcessLookupError(), False), (PermissionError(), True), (OSError(), True),
])
def test_posix_liveness_only_queries_and_refuses_uncertainty(monkeypatch, error, expected):
    def query(pid, sig):
        assert (pid, sig) == (12345, 0)
        raise error

    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "posix", "kill": query}))
    assert r.pid_alive(12345) is expected
    assert not r.pid_alive(-1)


@pytest.mark.parametrize(("handle", "error", "wait", "expected"), [
    (0, 87, 0, False), (0, 5, 0, True), (1, 0, 0, False),
    (1, 0, 258, True), (1, 0, 0xffffffff, True),
])
def test_windows_liveness_uses_process_handle_without_signals(monkeypatch, handle, error, wait, expected):
    import ctypes

    closed = []
    kernel = types.SimpleNamespace(OpenProcess=lambda *args: handle,
                                  WaitForSingleObject=lambda *args: wait,
                                  CloseHandle=lambda value: closed.append(value))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt",
        "kill": lambda *args: pytest.fail("Windows liveness signalled a PID")}))
    assert r.pid_alive(12345) is expected
    assert closed == ([handle] if handle else [])
