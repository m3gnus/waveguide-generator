"""Damaged accepted CAD intents settle once without holding up other work."""

from __future__ import annotations

import sqlite3

import pytest

from cad_backends import JobsHarness
from server.jobs.cad_intent import CadSolveIntent, cad_of
from server.jobs.cad_preparation import run_cad_preparation
from test_cad_preparation import _received, _revision, _setup


@pytest.fixture
def h(tmp_path):
    harness = JobsHarness(tmp_path)
    yield harness
    harness.close()


def _damaged_job(h, value):
    record = h.runtime._preparing_record(CadSolveIntent(
        operation_id="damaged", bundle_path="wgreturn/speaker.wgreturn",
        manifest_sha256="sha256:" + "1" * 64, return_id="return",
    ))
    record["config_json"]["bundle_path"] = value
    record["task_metadata"]["cad"]["snapshot"] = {"diagnostic": "keep me"}
    h.jobs_store.create_job(record)
    return record


@pytest.mark.parametrize("value", [None, "", 42])
def test_startup_refuses_damaged_intent_once_and_prepares_valid_neighbor(h, value):
    damaged = _damaged_job(h, value)
    _received(h)
    revision = _revision(h.store, _setup(engine="metal"))
    valid_id = h._loop.run(h.runtime.accept_cad_solve(
        h._intent("cmd-1", setup_revision_id=revision), "cad-solve:cmd-1", prepare=False,
    ))
    old_runtime = h.runtime
    h.restart()
    assert h.runtime is not old_runtime
    h._loop.run(h.runtime.wait_cad_preparations())

    refused = h.jobs_store.get_job_row(damaged["id"])
    assert refused["status"] == "error"
    assert refused["completed_at"]
    assert refused["config_json"] == damaged["config_json"]
    assert cad_of(refused)["snapshot"] == {"diagnostic": "keep me"}
    refusal = cad_of(refused)["refusal"]
    assert refusal["code"] == "preparation_failed"
    # WG's calm words; the validation detail went to the developer diagnostic.
    assert refusal["message"] == "Waveguide Generator could not prepare this model. This has been recorded for the developers."
    assert refused["error_message"] == refusal["message"]
    assert h.jobs_store.get_job_row(valid_id)["status"] == "queued"
    assert len(h.ingest.calls) == 1
    events = [event for event in h.jobs_store.replay_events(0) if event["jobId"] == damaged["id"]]
    assert len(events) == 1
    assert events[0]["type"] == "failed"
    assert events[0]["payload"] == {"stage": "error", "message": refusal["message"], "reason": "preparation_failed"}
    pushed = []
    while not h._queue.empty():
        pushed.append(h._queue.get_nowait())
    assert any(event["jobId"] == damaged["id"] and event["type"] == "failed"
               for event in pushed)

    h.restart()
    h._loop.run(h.runtime.wait_cad_preparations())
    assert damaged["id"] not in h.jobs_store.unheld_preparing_job_ids()
    assert h.jobs_store.get_job_row(damaged["id"])["status"] == "error"
    assert [event for event in h.jobs_store.replay_events(0) if event["jobId"] == damaged["id"]] == events
    assert len(h.ingest.calls) == 1


def test_damaged_intent_waits_for_approved_restart_before_refusal(h):
    damaged = _damaged_job(h, None)
    h.blocked = "An update restart is approved"
    h.restart()
    h._loop.run(h.runtime.wait_cad_preparations())
    assert h.jobs_store.get_job_row(damaged["id"])["status"] == "preparing"
    h.release_latch()
    assert h.jobs_store.get_job_row(damaged["id"])["status"] == "error"


@pytest.mark.parametrize("method", ["get_job_row", "refuse_preparing_job"])
def test_storage_failure_remains_a_storage_failure(h, monkeypatch, method):
    damaged = _damaged_job(h, None)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(h.jobs_store, method, unavailable)
        with pytest.raises(sqlite3.OperationalError, match="storage unavailable"):
            h._loop.run(run_cad_preparation(h.runtime._cad_port, h.runtime._cad_host, damaged["id"]))
    assert h.jobs_store.get_job_row(damaged["id"])["status"] == "preparing"


def test_cancel_after_read_fences_damaged_intent_refusal(h, monkeypatch):
    damaged = _damaged_job(h, None)
    original = h.jobs_store.get_job_row

    def read_then_cancel(job_id):
        row = original(job_id)
        event = h.jobs_store.cancel_job_from(
            job_id, "preparing",
            {"status": "cancelled", "cancellation_requested": True, "stage": "cancelled"},
            "cancelled", {"message": "Solve cancelled"},
        )
        assert event is not None
        return row

    with monkeypatch.context() as patch:
        patch.setattr(h.jobs_store, "get_job_row", read_then_cancel)
        outcome = h._loop.run(run_cad_preparation(
            h.runtime._cad_port, h.runtime._cad_host, damaged["id"],
        ))
    assert outcome == "fenced"
    cancelled = h.jobs_store.get_job_row(damaged["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["config_json"] == damaged["config_json"]
    assert "refusal" not in cad_of(cancelled)
    assert [event["type"] for event in h.jobs_store.replay_events(0)] == ["cancelled"]


def test_damaged_intent_calm_words_are_written_off_the_event_loop(h, monkeypatch):
    """The calm message records an incident: it is built in the worker thread, never on the loop."""

    import asyncio

    import server.jobs.cad_preparation as preparation

    damaged = _damaged_job(h, None)
    original = preparation.calm_refusal_message
    on_loop = []

    def calm(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return original(*args, **kwargs)

    monkeypatch.setattr(preparation, "calm_refusal_message", calm)
    outcome = h._loop.run(run_cad_preparation(h.runtime._cad_port, h.runtime._cad_host, damaged["id"]))
    assert outcome == "refused"
    assert on_loop == [False]
