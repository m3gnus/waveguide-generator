from __future__ import annotations

import json

from server.solver.beat_runtime import inspection, paths, registry
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
