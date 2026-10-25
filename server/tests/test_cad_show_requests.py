"""Fusion history is navigation to an existing request, never submission."""
from __future__ import annotations

import json
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
    record = {"schemaVersion": 1, "requestId": "request-1", "at": "2026-10-25T12:00:00Z", **overrides}
    path = directory / "request-1.json"
    path.write_text(json.dumps(record))
    return path, record


def test_reads_publishes_once_and_deletes(tmp_path):
    path, record = pointer(tmp_path, jobId="outdated-or-unrelated")
    assert read_show_requests(tmp_path) == [(path, record)]
    events = []
    publish_show_request(record, {"id": "latest-job"}, events.append)
    remove_show_request(path, record)
    assert events == [{"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1", "jobId": "latest-job"}]
    assert not path.exists()
    assert read_show_requests(tmp_path) == []


def test_send_is_an_operation_pointer_and_a_later_press_survives(tmp_path):
    path, record = pointer(tmp_path)
    events = []
    publish_show_request(record, None, events.append)
    assert events[0] == {"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1"}
    later = {**record, "at": "2026-10-25T12:00:01Z"}
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
    path, _ = pointer(tmp_path)
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
        assert queue.get_nowait() == {"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": "request-1", "jobId": "current-job"}
        await job_shims.run_delivery_pass(ctx)
        assert queue.empty() and calls == ["request-1"]
    asyncio.run(run())
