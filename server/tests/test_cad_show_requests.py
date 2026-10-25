"""Fusion history is navigation to an existing request, never submission."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.intake.show_requests import read_show_requests, publish_show_request, remove_show_request
from server.cadlink.ipc import ipc_folder
from server.cadlink.wglink_protocol import SHOW_REQUEST_CAPABILITY, SHOW_REQUEST_EVENT, SHOW_REQUESTS_DIRECTORY
from server.jobs.runtime.events import EventBroker


def pointer(tmp_path, **overrides):
    directory = ipc_folder(tmp_path) / SHOW_REQUESTS_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    record = {"schemaVersion": 1, "requestId": "request-1", "at": datetime.now(timezone.utc).isoformat(), **overrides}
    path = directory / "request-1.json"
    path.write_text(json.dumps(record))
    return path, record


def test_reads_publishes_once_and_deletes(tmp_path):
    path, record = pointer(tmp_path, jobId="outdated-or-unrelated")
    assert read_show_requests(tmp_path) == [(path, record)]
    events = []
    publish_show_request(record, {"id": "latest-job"}, lambda message, **kw: events.append(message))
    remove_show_request(path, record)
    assert events == [{"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1", "at": record["at"], "jobId": "latest-job"}]
    assert not path.exists()
    assert read_show_requests(tmp_path) == []


def test_send_is_an_operation_pointer_and_a_later_press_survives(tmp_path):
    path, record = pointer(tmp_path)
    events = []
    publish_show_request(record, None, lambda message, **kw: events.append(message))
    assert events[0] == {"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1", "at": record["at"]}
    later = {**record, "at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(later))
    remove_show_request(path, record)
    assert read_show_requests(tmp_path) == [(path, later)]


@pytest.mark.parametrize("overrides", [{"schemaVersion": True}, {"schemaVersion": 2}, {"requestId": "../outside"},
                                       {"requestId": "other"}, {"jobId": "../outside"}, {"at": None}])
def test_invalid_pointers_never_publish(tmp_path, overrides):
    pointer(tmp_path, **overrides)
    assert not read_show_requests(tmp_path)


def test_symlink_is_not_followed(tmp_path):
    path, _ = pointer(tmp_path)
    outside = tmp_path / "outside.json"
    path.rename(outside)
    path.symlink_to(outside)
    assert read_show_requests(tmp_path) == []
    assert outside.exists()


def test_capability_requires_the_delivery_consumer():
    assert capabilities()[SHOW_REQUEST_CAPABILITY] == 1
    assert SHOW_REQUEST_CAPABILITY not in capabilities(solve_delivery=False)


@pytest.mark.parametrize("workspace_selected", [True, False])
def test_delivery_defers_until_a_window_can_hear_then_consumes_without_resubmission(tmp_path, monkeypatch, workspace_selected):
    import asyncio
    from server.cadlink.joblane import job_shims
    path, record = pointer(tmp_path)
    events = EventBroker()
    calls = []
    def job(request_id):
        calls.append(request_id)
        return {"id": "current-job"}
    monkeypatch.setattr(job_shims, "settle_received_snapshots", lambda ctx: None)
    monkeypatch.setattr(job_shims, "collect_solve_deliveries", lambda *args, **kwargs: None)
    ctx = SimpleNamespace(data_dir=tmp_path, workspace_root=tmp_path if workspace_selected else None, runtime=SimpleNamespace(events=events),
                          job_store=SimpleNamespace(latest_cad_job=job), submission_blocked=None,
                          store=SimpleNamespace(operation_page=lambda **kwargs: []), refuse=None)
    async def run():
        await job_shims.run_delivery_pass(ctx)
        assert path.exists() and calls == []
        queue = events.subscribe()
        await job_shims.run_delivery_pass(ctx)
        assert not path.exists()
        assert queue.get_nowait() == {"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1", "at": record["at"], "jobId": "current-job"}
        await job_shims.run_delivery_pass(ctx)
        assert queue.empty() and calls == ["request-1"]
    asyncio.run(run())


@pytest.mark.parametrize("content", ["[" * 2000, "bad-json", "x" * 65537, "\xff", "null"])
def test_hostile_files_are_discarded_and_other_pointers_survive(tmp_path, content):
    path, _ = pointer(tmp_path)
    bad = path.with_name("hostile.json")
    bad.write_bytes(content.encode("latin1"))
    assert [p for p, _ in read_show_requests(tmp_path)] == [path]
    assert not bad.exists()


def test_fifo_and_directory_are_never_read_and_stale_crash_files_are_cleaned(tmp_path):
    import os
    path, _ = pointer(tmp_path)
    fifo = path.with_name("fifo.json")
    os.mkfifo(fifo)
    path.with_name("directory.json").mkdir()
    stale = path.with_name("request-1.json.crashed")
    stale.write_text("partial")
    os.utime(stale, (time.time() - 180, time.time() - 180))
    fresh = path.with_name("request-1.json.writing")
    fresh.write_text("partial")
    assert [p for p, _ in read_show_requests(tmp_path)] == [path]
    assert fifo.exists() and fresh.exists() and not stale.exists()


@pytest.mark.parametrize("age", [121, 3600, -60])
def test_old_or_future_pointers_are_discarded(tmp_path, age):
    path, _ = pointer(tmp_path, at=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat())
    assert not read_show_requests(tmp_path) and not path.exists()


@pytest.mark.parametrize("failure", ["reader", "job_lookup", "publish", "runtime"])
def test_show_request_failure_never_stops_delivery(tmp_path, monkeypatch, failure):
    import asyncio
    from server.cadlink.joblane import job_shims
    pointer(tmp_path)
    broker = EventBroker()
    broker.subscribe()
    calls = []
    def fail(*args, **kwargs):
        raise RecursionError("hostile input")
    if failure == "reader":
        monkeypatch.setattr(job_shims, "read_show_requests", fail)
    if failure == "publish":
        monkeypatch.setattr(broker, "publish_latest", fail)
    monkeypatch.setattr(job_shims, "settle_received_snapshots", lambda ctx: calls.append("delivery"))
    monkeypatch.setattr(job_shims, "collect_solve_deliveries", lambda *args, **kwargs: None)
    class BrokenRuntime:
        @property
        def events(self):
            return fail()
    ctx = SimpleNamespace(data_dir=tmp_path, workspace_root=tmp_path,
        runtime=BrokenRuntime() if failure == "runtime" else SimpleNamespace(events=broker),
        job_store=SimpleNamespace(latest_cad_job=fail if failure == "job_lookup" else lambda rid: None),
        submission_blocked=None, store=SimpleNamespace(operation_page=lambda **kwargs: []), refuse=None)
    asyncio.run(job_shims.run_delivery_pass(ctx))
    assert calls == ["delivery"]


def test_latest_pointer_replays_once_to_a_cold_window_and_expires(tmp_path, monkeypatch):
    import asyncio
    from server.jobs.runtime import events as broker_module
    path, record = pointer(tmp_path)
    async def run():
        broker = EventBroker()
        first = broker.subscribe()
        publish_show_request(record, {"id": "job"}, broker.publish_latest)
        assert first.get_nowait()["jobId"] == "job"
        remove_show_request(path, record)
        cold = broker.subscribe()
        assert cold.get_nowait()["operationId"] == "request-1"
        assert cold.empty()
        future = time.time() + 180
        monkeypatch.setattr(broker_module.time, "time", lambda: future)
        assert broker.subscribe().empty()
    asyncio.run(run())


def test_unreadable_pointer_is_deleted_without_hiding_other_files(tmp_path, monkeypatch):
    path, _ = pointer(tmp_path)
    unreadable = path.with_name("unreadable.json")
    unreadable.write_text("{}")
    original = type(path).open
    def open_file(candidate, *args, **kwargs):
        if candidate == unreadable:
            raise PermissionError("file in use")
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(type(path), "open", open_file)
    assert [p for p, _ in read_show_requests(tmp_path)] == [path]
    assert not unreadable.exists()



def test_replacement_pointer_is_also_size_bounded_during_removal(tmp_path):
    path, record = pointer(tmp_path)
    path.write_text("[" * 65537)
    remove_show_request(path, record)
    assert not read_show_requests(tmp_path)
    assert not path.exists()
