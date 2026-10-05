from __future__ import annotations

import errno
import json
import os

import pytest

from server.solver.beat_runtime import cleanup, inspection, ipc, paths, registry
from server.solver.beat_runtime.client import HostedWorker


def test_inspection_reads_and_stops_only_the_effective_registry(launch, monkeypatch, tmp_path):
    key, directory, children = launch
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    worker = HostedWorker(key, directory=directory)
    worker.ensure_started()
    worker.detach()
    outside = tmp_path / "other-session"
    outside.mkdir()
    foreign = outside / "foreign.json"
    foreign.write_text("other session")
    refused = inspection.inspect_hosts(outside, stop=True)
    assert refused["contained"] is False and refused["verified"] == []
    assert foreign.read_text() == "other session"
    assert children[0].poll() is None
    report = inspection.inspect_hosts(directory)
    assert report["contained"] and report["refused"] == []
    assert report["verified"][0]["host_pid"] == children[0].pid
    assert children[0].poll() is None
    stopped = inspection.inspect_hosts(directory, stop=True)
    assert stopped["refused"] == []
    assert stopped["verified"][0]["still_alive"] is False
    children[0].wait(timeout=3)
    assert registry.read_record(registry.key_id(key), directory) is None


def test_foreign_and_unauthenticated_records_are_retained(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    worker = HostedWorker(key, directory=directory)
    worker.ensure_started()
    worker.detach()
    record_path = registry.record_path(registry.key_id(key), directory)
    original = record_path.read_bytes()
    try:
        raw = json.loads(original)
        for field, value in [("token", registry.new_token()), ("provider", "hornlab-beat-bem")]:
            altered = dict(raw, **{field: value})
            record_path.write_text(json.dumps(altered))
            report = inspection.inspect_hosts(directory, stop=True)
            assert report["verified"] == [] and len(report["refused"]) == 1
            assert children[0].poll() is None
            assert json.loads(record_path.read_bytes()) == altered
    finally:
        record_path.write_bytes(original)


def test_missing_registry_inspection_does_not_create_it(tmp_path, monkeypatch):
    directory = tmp_path / "absent"
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    assert inspection.inspect_hosts(directory, stop=True)["verified"] == []
    assert not directory.exists()


@pytest.mark.parametrize("error", [errno.ENOENT, errno.ECONNREFUSED])
@pytest.mark.parametrize("interleaving", ["gone", "dead", "reused", "live", "unverifiable", "successor"])
def test_connection_failure_rechecks_record_under_spawn_lock(tmp_path, monkeypatch, error, interleaving):
    directory = tmp_path / "registry"
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    record = registry.HostRecord(registry.host_key({}), os.getpid(), registry.new_token(),
                                 ipc.Endpoint("tcp", port=12345), pid_start="original-start")
    path = registry.write_record(record, directory)

    def unavailable(*args, **kwargs):
        raise OSError(error, "fixture endpoint unavailable")

    def race(*args, **kwargs):
        if interleaving == "gone":
            path.unlink()
        elif interleaving == "successor":
            successor = registry.HostRecord(record.key, record.pid, registry.new_token(),
                                            record.endpoint, pid_start=record.pid_start)
            registry.write_record(successor, directory)
        unavailable()

    monkeypatch.setattr(inspection, "connect_client", race)
    monkeypatch.setattr(ipc.Endpoint, "connect", unavailable)
    monkeypatch.setattr(cleanup, "pid_alive", lambda pid: interleaving != "dead")
    start = None if interleaving == "unverifiable" else (
        "new-start" if interleaving == "reused" else record.pid_start)
    monkeypatch.setattr(cleanup, "process_start_identity", lambda pid: start)
    prune = inspection.cleanup_host
    pruned = []

    def checked_prune(*args, **kwargs):
        assert kwargs["lock"].held and kwargs["prune_only"]
        pruned.append(prune(*args, **kwargs))

    monkeypatch.setattr(inspection, "cleanup_host", checked_prune)
    report = inspection.inspect_hosts(directory, stop=True)
    assert report["verified"] == []
    if interleaving in {"gone", "dead", "reused"}:
        assert report["refused"] == [] and not path.exists()
        assert pruned == ([] if interleaving == "gone" else [True])
    else:
        assert len(report["refused"]) == 1 and path.exists()
        retained = registry.read_record(record.identifier, directory)
        assert (retained.token != record.token) == (interleaving == "successor")


def test_idle_exit_between_record_read_and_connect_is_skipped(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    worker = HostedWorker(key, directory=directory, idle_timeout=0.2)
    worker.ensure_started()
    worker.detach()
    connect = inspection.connect_client

    def after_idle_exit(record, root, **kwargs):
        children[0].wait(timeout=3)
        return connect(record, root, **kwargs)

    monkeypatch.setattr(inspection, "connect_client", after_idle_exit)
    report = inspection.inspect_hosts(directory, stop=True)
    assert report["verified"] == report["refused"] == []
    assert registry.read_record(registry.key_id(key), directory) is None


def test_killed_host_record_is_pruned_without_signalling(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    worker = HostedWorker(key, directory=directory)
    worker.ensure_started()
    worker.detach()
    children[0].kill()  # Only this test's recorded child.
    children[0].wait(timeout=3)
    assert registry.read_record(registry.key_id(key), directory) is not None
    report = inspection.inspect_hosts(directory, stop=True)
    assert report["verified"] == report["refused"] == []
    assert registry.read_record(registry.key_id(key), directory) is None


def test_inspection_record_filename_filter_ignores_ready_and_other_json(tmp_path, monkeypatch):
    directory = registry.private_directory(tmp_path / "registry")
    monkeypatch.setattr(paths, "worker_dir", lambda: directory)
    names = ["0123456789abcdef.ready.json", "0123456789abcdef.key.json",
             "0123456789ABCDEf.json", "short.json", "notes.json"]
    for name in names:
        (directory / name).write_text("not a host record")
    monkeypatch.setattr(registry, "read_record", lambda *args: pytest.fail("read a non-record"))
    report = inspection.inspect_hosts(directory, stop=True)
    assert report["verified"] == report["refused"] == []
    assert sorted(p.name for p in directory.iterdir()) == sorted(names)
