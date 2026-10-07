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
                        r.new_token(), ipc.Endpoint("tcp", port=1234), "fixture-start")


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
    ("protocol_version", 1), ("protocol_version", True), ("pid", "123"),
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
    reason = "Unix host record unavailable on Windows" if os.name == "nt" else "outside"
    with pytest.raises(r.RecordRefused, match=reason):
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
        write_record(HostRecord(key, 12345, new_token(), Endpoint('tcp', port=1234), 'fixture-start'), root)
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
        with pytest.raises(r.LockBusy):
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
    security = types.SimpleNamespace(create_directory=lambda path: path.mkdir(exist_ok=True),
                                     check_path=lambda path: None)
    monkeypatch.setattr(r, "_windows_security", lambda: security)
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO")
def test_fifo_record_refused_before_blocking_open(tmp_path, record, monkeypatch):
    r.private_directory(tmp_path)
    path = r.record_path(record.identifier, tmp_path)
    os.mkfifo(path, 0o600)
    original = r.os.open

    def guarded_open(candidate, flags, *args):
        if Path(candidate) == path:
            pytest.fail("FIFO opened instead of rejected by lstat")
        return original(candidate, flags, *args)

    monkeypatch.setattr(r.os, "open", guarded_open)
    with pytest.raises(r.RecordRefused, match="regular"):
        r.read_record(record.identifier, tmp_path)
    assert path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX nonblocking open")
def test_regular_record_open_uses_nonblock_against_fifo_swap(tmp_path, record, monkeypatch):
    path = r.write_record(record, tmp_path)
    original = r.os.open

    def swapped(candidate, flags, *args):
        if Path(candidate) == path:
            assert flags & os.O_NONBLOCK
            path.unlink()
            os.mkfifo(path, 0o600)
        return original(candidate, flags, *args)

    monkeypatch.setattr(r.os, "open", swapped)
    with pytest.raises(r.RecordRefused, match="regular"):
        r.read_record(record.identifier, tmp_path)


@pytest.mark.parametrize("pid", [2**31, 2**32, 2**100, True, 0])
def test_oversized_pid_refused_without_overflow(tmp_path, record, pid):
    record.pid = pid
    with pytest.raises(r.RecordRefused, match="PID"):
        r.validate_record(record, record.key, tmp_path)
    assert not r.pid_alive(pid)
    assert r.process_start_identity(pid) is None


def test_record_json_recursion_refused_and_retained(tmp_path, record):
    path = r.write_record(record, tmp_path)
    content = '[' * 2000 + '0' + ']' * 2000
    path.write_text(content)
    with pytest.raises(r.RecordRefused):
        r.read_record(record.identifier, tmp_path)
    assert path.read_text() == content


@pytest.mark.parametrize("action", ["private_directory", "record_path", "spawn_lock_path", "launch_spec_path",
                                     "log_path", "read_record", "write_record", "write_launch_spec",
                                     "validate_record", "endpoint_for", "cleanup_host", "sweep_orphan_socket", "SpawnLock"])
def test_explicit_directory_checked_root_rejects_hbb_before_mutation(tmp_path, record, monkeypatch, action):
    from server.solver.beat_runtime import cleanup

    legacy = tmp_path / "legacy-hbb"
    monkeypatch.setenv("HORNLAB_BEAT_WORKER_DIR", str(legacy))
    with pytest.raises(paths.RootConflict):
        if action in {"private_directory"}:
            getattr(r, action)(legacy)
        elif action in {"record_path", "spawn_lock_path", "launch_spec_path", "log_path", "read_record"}:
            getattr(r, action)(record.identifier, legacy)
        elif action == "write_record":
            r.write_record(record, legacy)
        elif action == "write_launch_spec":
            r.write_launch_spec(record.key, legacy)
        elif action == "validate_record":
            r.validate_record(record, record.key, legacy)
        elif action == "endpoint_for":
            ipc.endpoint_for(record.identifier, legacy)
        elif action == "cleanup_host":
            cleanup.cleanup_host(record, record.key, legacy)
        elif action == "sweep_orphan_socket":
            cleanup.sweep_orphan_socket(record.identifier, legacy)
        else:
            with r.SpawnLock(legacy / "test.lock"):
                pytest.fail("Legacy lock acquired")
    assert not legacy.exists()


@pytest.mark.parametrize("state", ["Z", "Z+"])
def test_posix_zombie_liveness_is_dead_without_pid_signal(monkeypatch, state):
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(r, "_posix_process", lambda pid: (state, "start"))
    monkeypatch.setattr(r.os, "kill", lambda *args: pytest.fail("Zombie queried with kill"))
    assert not r.pid_alive(12345)


def test_linux_process_start_uses_stat_field_22_and_boot_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "posix"}))
    # The command name contains spaces and ')'; starttime is field 22.
    proc_stat = tmp_path / "stat"
    proc_stat.write_text('42 (a tricky) name) S ' + ' '.join([str(i) for i in range(4, 23)]))
    boot = tmp_path / "boot"
    boot.write_text("boot-identity\n")
    monkeypatch.setattr(r, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(r, "Path", lambda path: boot if "boot_id" in path else proc_stat)
    assert r.process_start_identity(42) == "linux:boot-identity:22"


def test_posix_ps_process_start_and_zombie_stat_with_fixed_locale(monkeypatch):
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(r, "sys", types.SimpleNamespace(platform="darwin"))
    calls = []

    def ps(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="Z+ Mon Oct  5 10:00:00 2026\n")

    monkeypatch.setattr(r.subprocess, "run", ps)
    assert r.process_start_identity(42) == "posix:Mon Oct  5 10:00:00 2026"
    assert not r.pid_alive(42)
    assert calls[0][0] == ["ps", "-o", "stat=", "-o", "lstart=", "-p", "42"]
    assert calls[0][1]["env"]["LC_ALL"] == "C"
    assert calls[0][1]["timeout"] <= 1


def test_windows_process_start_getprocesstimes_creation_identity(monkeypatch):
    import ctypes
    from ctypes import wintypes

    closed = []

    def get_times(handle, created, *args):
        value = ctypes.cast(created, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwHighDateTime, value.dwLowDateTime = 7, 11
        return True

    kernel = types.SimpleNamespace(OpenProcess=lambda *args: 1, GetProcessTimes=get_times,
                                  CloseHandle=lambda handle: closed.append(handle))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    assert r.process_start_identity(42) == f"windows:{(7 << 32) | 11}"
    assert closed == [1]


@pytest.mark.parametrize("target", ["root", "record"])
@pytest.mark.parametrize("failure", ["owner SID", "nonprivate DACL"])
def test_windows_root_and_record_security_refused(tmp_path, record, monkeypatch, target, failure):
    path = r.write_record(record, tmp_path)
    checks = []

    def check(candidate):
        checks.append(candidate)
        if candidate == (tmp_path if target == "root" else path):
            raise ValueError(failure)

    security = types.SimpleNamespace(create_directory=lambda path: path.mkdir(exist_ok=True), check_path=check)
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(r, "_windows_security", lambda: security)
    with pytest.raises(r.RecordRefused, match=failure):
        r.read_record(record.identifier, tmp_path)
    assert path.exists()
    assert checks


@pytest.mark.parametrize("target", ["root", "record"])
def test_windows_junction_reparse_point_refused(tmp_path, record, monkeypatch, target):
    path = r.write_record(record, tmp_path)
    security = types.SimpleNamespace(check_path=lambda path: None)
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(r, "_windows_security", lambda: security)
    monkeypatch.setattr(Path, "is_junction", lambda self: self == (tmp_path if target == "root" else path), raising=False)
    with pytest.raises(r.RecordRefused, match="reparse"):
        r.read_record(record.identifier, tmp_path)
    assert path.exists()


def test_windows_file_attribute_reparse_point_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    info = types.SimpleNamespace(st_file_attributes=0x400)
    with pytest.raises(r.RecordRefused, match="reparse"):
        r._check_path(tmp_path, info)


def test_windows_directory_created_with_private_security_then_validated(tmp_path, monkeypatch):
    events = []
    root = tmp_path / "private"

    def create(path):
        events.append(("create", path))
        path.mkdir()

    security = types.SimpleNamespace(create_directory=create, check_path=lambda path: events.append(("check", path)))
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(r, "_windows_security", lambda: security)
    assert r.private_directory(root) == root
    assert events == [("create", root), ("check", root)]


def test_windows_unix_record_refused_cleanly(tmp_path, record, monkeypatch):
    record.endpoint = ipc.Endpoint("unix", path=tmp_path / f"{record.identifier}.sock")
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    with pytest.raises(r.RecordRefused, match="Windows"):
        r.validate_record(record, record.key, tmp_path)


@pytest.mark.parametrize("operation", ["replace", "unlink"])
def test_windows_record_permission_retry_is_short_and_bounded(tmp_path, monkeypatch, operation):
    calls = []
    monkeypatch.setattr(r, "os", types.SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(r.time, "sleep", lambda duration: calls.append(("sleep", duration)))

    def busy(*args, **kwargs):
        calls.append((operation, args))
        raise PermissionError("scanner holds record")

    if operation == "replace":
        with pytest.raises(PermissionError):
            r._retry_permission(busy, "source", "destination")
    else:
        monkeypatch.setattr(Path, "unlink", busy)
        with pytest.raises(PermissionError):
            r.unlink_record(tmp_path / "record.json")
    assert sum(item[0] == operation for item in calls) == 4
    assert sum(item[1] for item in calls if item[0] == "sleep") <= 0.05


def test_nonce_hmac_wire_contract_and_shutdown_request_authentication(record):
    import hashlib
    import hmac

    hello = r.hello_message(record)
    expected = hmac.new(record.token.encode(),
                        f'{hello["nonce"]}{record.identifier}{record.pid}wg-beat-host:2:hello'.encode(),
                        hashlib.sha256).hexdigest()
    reply = r.auth_reply(record, hello)
    assert reply["proof"] == expected
    r.validate_hello(record, reply, hello["nonce"])
    shutdown = r.hello_message(record, operation="shutdown")
    reply = r.auth_reply(record, shutdown)
    r.validate_hello(record, reply, shutdown["nonce"], operation="shutdown")
    shutdown["proof"] = "echo"
    with pytest.raises(r.RecordRefused, match="Unauthenticated shutdown"):
        r.auth_reply(record, shutdown)
    assert "token" not in hello and "token" not in reply and "token" not in shutdown


def test_missing_process_start_identity_never_inferred_from_reused_pid(tmp_path, record, monkeypatch):
    path = r.write_record(record, tmp_path)
    raw = record.as_dict()
    raw.pop("pid_start")
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(r, "process_start_identity", lambda pid: pytest.fail("Old record's PID queried"))
    with pytest.raises(r.RecordRefused, match="process start identity"):
        r.read_record(record.identifier, tmp_path)
    assert json.loads(path.read_text()) == raw


@pytest.mark.parametrize("operation", [[], {}, None, 123])
def test_nonce_hmac_malformed_control_operation_refused(record, operation):
    request = {**r.hello_message(record), "op": operation}
    with pytest.raises(r.RecordRefused):
        r.auth_reply(record, request)
