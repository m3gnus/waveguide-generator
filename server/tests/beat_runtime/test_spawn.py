from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from server.solver.beat_runtime import host, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import events, wait_until


def test_two_clients_race_one_launch_and_one_worker(launch):
    key, directory, children = launch
    key["environment"]["TEST_DELAY"] = "0.15"
    gate = threading.Barrier(2)

    def start():
        gate.wait(timeout=2)
        return spawn.start_host(key, directory)

    with ThreadPoolExecutor(2) as pool:
        records = list(pool.map(lambda _: start(), range(2)))
    assert records[0] == records[1]
    assert len(children) == 1
    assert len(events(key)) == 1
    assert r.read_record(records[0].identifier, directory) == records[0]
    assert not host.ready_path(records[0].identifier, directory).exists()


def test_separate_processes_race_single_host(launch, tmp_path):
    key, directory, _ = launch
    source = Path(__file__).resolve().parents[3]
    key_path = tmp_path / "client-key.json"
    key_path.write_text(json.dumps(key))
    gate = tmp_path / "go"
    code = (
        "import json,sys,time; from pathlib import Path; "
        "from server.solver.beat_runtime import spawn; "
        "spawn.HOST_MODULE='server.tests.beat_runtime.fake_host_main'; "
        "start_host=spawn.start_host; "
        "key=json.loads(Path(sys.argv[1]).read_text()); "
        "gate=Path(sys.argv[3]);\n"
        "while not gate.exists(): time.sleep(.01)\n"
        "record=start_host(key, Path(sys.argv[2])); print(record.pid)"
    )
    environment = {**os.environ, "PYTHONPATH": str(source)}
    clients = [subprocess.Popen([sys.executable, "-c", code, str(key_path), str(directory), str(gate)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=tmp_path, env=environment) for _ in range(2)]
    try:
        gate.touch()
        reports = [child.communicate(timeout=5) for child in clients]
        assert all(child.returncode == 0 for child in clients), reports
        assert len({out.strip() for out, _ in reports}) == 1
        assert len(events(key)) == 1
    finally:
        for child in clients:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=3)


def test_publication_occurs_under_parent_spawn_lock(launch, monkeypatch):
    key, directory, _ = launch
    original = r.write_record

    def write(record, directory):
        with pytest.raises(r.LockBusy):
            with r.SpawnLock(r.spawn_lock_path(record.identifier, directory), timeout=0):
                pass
        return original(record, directory)

    monkeypatch.setattr(r, "write_record", write)
    assert spawn.start_host(key, directory).key == key


def test_foreign_record_refused_without_launch_or_signal(launch, monkeypatch):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    path = r.record_path(record.identifier, directory)
    raw = record.as_dict()
    raw["provider"] = "hornlab-beat-bem"
    path.write_text(json.dumps(raw))
    try:
        with monkeypatch.context() as guard:
            guard.setattr(os, "kill", lambda *args: pytest.fail("Signalled foreign PID"))
            with pytest.raises(r.RecordRefused):
                spawn.start_host(key, directory)
            assert json.loads(path.read_text()) == raw
            assert len(children) == 1
    finally:
        path.write_text(json.dumps(record.as_dict()))


def test_live_unreachable_record_retained(launch, monkeypatch):
    key, directory, children = launch
    directory = r.private_directory(directory)
    with socket_closed_port() as endpoint:
        record = r.HostRecord(key, os.getpid(), r.new_token(), endpoint)
        r.write_record(record, directory)
        with pytest.raises(r.RecordRefused, match="Unverified live"):
            spawn.start_host(key, directory, timeout=0.25)
        assert not children
        assert r.read_record(record.identifier, directory) == record
        r.unlink_record(r.record_path(record.identifier, directory))


def socket_closed_port():
    import contextlib
    import socket

    @contextlib.contextmanager
    def closed():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            endpoint = ipc.Endpoint("tcp", port=sock.getsockname()[1])
        yield endpoint

    return closed()


@pytest.mark.parametrize("failure", ["constructor", "timeout"])
def test_failed_start_reaps_child_and_allows_retry(launch, failure):
    key, directory, children = launch
    key["environment"].update({"TEST_FAIL": "1"} if failure == "constructor" else {"TEST_DELAY": "1"})
    with pytest.raises((RuntimeError, TimeoutError)):
        spawn.start_host(key, directory, timeout=0.25 if failure == "timeout" else 3)
    assert children[0].returncode is not None
    assert r.read_record(r.key_id(key), directory) is None
    assert not host.ready_path(r.key_id(key), directory).exists()
    key["environment"].pop("TEST_FAIL", None)
    key["environment"].pop("TEST_DELAY", None)
    assert spawn.start_host(key, directory).pid != children[0].pid


def test_engine_import_optional_in_parent(launch):
    key, directory, _ = launch
    assert "beat_engine" not in sys.modules
    spawn.start_host(key, directory)
    assert "beat_engine" not in sys.modules


@pytest.mark.parametrize("field", ["provider", "protocol", "engine_fingerprint", "julia_identity"])
def test_incomplete_or_foreign_launch_identity_refused_before_writes(launch, field):
    key, directory, children = launch
    del key[field]
    with pytest.raises(r.RecordRefused):
        spawn.start_host(key, directory)
    assert not directory.exists()
    assert not children


def test_parent_abandons_bootstrap_child_exits_and_removes_own_ready(launch):
    key, directory, children = launch
    directory = r.private_directory(directory)
    r.write_launch_spec(key, directory)
    with r.SpawnLock(r.spawn_lock_path(r.key_id(key), directory)):
        spawn._launch(key, directory, 10, 0.2)
    wait_until(lambda: children[0].returncode is not None)
    assert children[0].returncode != 0
    assert [event["type"] for event in events(key)] == ["built", "terminated"]
    assert r.read_record(r.key_id(key), directory) is None
    assert not host.ready_path(r.key_id(key), directory).exists()
    assert "Parent did not publish" in r.log_path(r.key_id(key), directory).read_text()


def test_proven_reused_process_identity_pruned_before_new_host(launch):
    key, directory, children = launch
    directory = r.private_directory(directory)
    with socket_closed_port() as endpoint:
        stale = r.HostRecord(key, os.getpid(), "old-token", endpoint, "previous-occupant")
        r.write_record(stale, directory)
        record = spawn.start_host(key, directory)
    assert record.pid != stale.pid
    assert len(children) == 1
    assert r.read_record(record.identifier, directory) == record
