"""A running solve's frequency count, read once by the server (``server/jobs/progress.py``).

The engines report it in their stage messages; the runtime reads it, keeps it
through setup chatter, stores it on the job and sends it with each stage event.
The jobs API, WG's progress display and a Fusion request's status file all read
that one value.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from server.jobs.models import JobItem
from server.jobs.progress import (
    advance_frequency_progress,
    checked_frequency_progress,
    frequency_progress,
    job_frequency_progress,
)
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


# -- reading one message (the cases WG's frontend parser had) ----------------------


@pytest.mark.parametrize("message, expected", [
    # Metal and BEMPP callbacks report completed frequencies; the printed count is done.
    ("Solving frequency 3/12 with Metal BEM", {"done": 3, "total": 12}),
    ("Solving frequency 1/12 with BEAT Engine", {"done": 1, "total": 12}),
    ("Solving frequency 12/12 with BEMPP BEM", {"done": 12, "total": 12}),
    # The BEAT FEM path, and the transducer adapter's own wording.
    ("Solving frequency 4/30 with FEM (400 Hz)", {"done": 4, "total": 30}),
    ("Solved 5 of 40 frequencies", {"done": 5, "total": 40}),
    # An imported message is a channel count, never a false overall count.
    ("Solving frequency 2/8 of drive channel 1/3 (hf) with BEAT Engine",
     {"done": 2, "total": 8, "channel": {"index": 1, "count": 3}}),
    ("Solving frequency 3/8 of drive channel 2/3 (mf) with BEMPP BEM",
     {"done": 3, "total": 8, "channel": {"index": 2, "count": 3}}),
    ("Solving frequency 8/8 of drive channel 3/3 (lf) with BEAT Engine",
     {"done": 8, "total": 8, "channel": {"index": 3, "count": 3}}),
    # A printed count past its total is clamped to it.
    ("Solving frequency 13/12", {"done": 12, "total": 12}),
])
def test_a_stage_message_with_a_count_reads_as_done_of_total(message: str, expected: dict[str, Any]) -> None:
    assert frequency_progress(message) == expected


@pytest.mark.parametrize("message", [
    "Configuring BEMPP BEM solve (cpu)", "Configuring FEM (cpu, float64)", "", None, "Solving frequency 0/0",
    # A channel past its count is not a count.
    "Solving frequency 2/8 of drive channel 4/3 (hf)",
])
def test_a_message_with_no_count_has_none(message: str | None) -> None:
    assert frequency_progress(message) is None


def test_only_the_solve_stage_sets_a_count_and_the_sweep_s_neighbours_keep_it() -> None:
    first = advance_frequency_progress(None, "solve", "Solving frequency 2/8 with BEAT Engine")
    assert first == {"done": 2, "total": 8}
    # BEAT's setup chatter between checkpoints, and a count-less solve line, keep it.
    assert advance_frequency_progress(first, "assemble", "Setting up BEAT solver context") == first
    assert advance_frequency_progress(first, "solve", "Initializing BEAT sweep worker") == first
    # Radiation impedance prints its own i/N, which is not a frequency count.
    assert advance_frequency_progress(
        first, "radiation_impedance", "Solving passive-cardioid radiation impedance 32/160 at 400 Hz",
    ) == first
    assert advance_frequency_progress(None, "radiation_impedance", "Solving … 32/160") is None
    # Any other stage ends it; nothing is kept before the sweep starts.
    assert advance_frequency_progress(first, "postprocess", "Packaging results") is None
    assert advance_frequency_progress(first, "cancelling", "Cancellation requested") is None
    assert advance_frequency_progress(None, "assemble", "Configuring BEAT Engine BEM solve") is None


def test_the_read_model_shows_a_stored_count_only_while_solving() -> None:
    stored = {"task_metadata": {"frequency_progress": {"done": 2, "total": 8}}}
    assert job_frequency_progress({**stored, "status": "running", "stage": "solve"}) == {"done": 2, "total": 8}
    assert job_frequency_progress({**stored, "status": "running", "stage": "assemble"}) == {"done": 2, "total": 8}
    assert job_frequency_progress({**stored, "status": "running", "stage": "postprocess"}) is None
    assert job_frequency_progress({**stored, "status": "complete", "stage": "solve"}) is None
    assert job_frequency_progress({"status": "running", "stage": "solve", "task_metadata": {}}) is None
    bad = {"status": "running", "stage": "solve", "task_metadata": {"frequency_progress": {"done": 9, "total": 8}}}
    assert job_frequency_progress(bad) is None


def test_one_check_serves_the_parsed_value_and_the_stored_one() -> None:
    assert checked_frequency_progress({"done": 2, "total": 8, "channel": {"index": 1, "count": 3}}) == {
        "done": 2, "total": 8, "channel": {"index": 1, "count": 3}}
    for bad in ({"done": 9, "total": 8}, {"done": 1, "total": 0}, {"done": True, "total": 2},
                {"done": 1, "total": 2, "channel": {"index": 3, "count": 2}},
                {"done": 1, "total": 2, "channel": "hf"}, None, "2/8"):
        assert checked_frequency_progress(bad) is None
    stored = {"status": "running", "stage": "solve",
              "task_metadata": {"frequency_progress": {"done": 1, "total": 2, "channel": {"index": 3, "count": 2}}}}
    assert job_frequency_progress(stored) is None


# -- the runtime stores it and sends it ----------------------------------------------


def _running(job_id: str) -> dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "id": job_id, "status": "running", "created_at": now, "updated_at": now, "queued_at": now,
        "progress": 0.1, "stage": "initializing", "config_json": {"design": {"formula": "OSSE"}},
        "config_summary_json": {"formula_type": "OSSE"}, "task_metadata": {},
    }


def test_the_runtime_keeps_the_count_through_setup_chatter_on_the_job_and_its_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore.for_data_dir(tmp_path)
        store.initialize()
        store.create_job(_running("sweep"))
        # Every update is persisted at once, as a stage change is.
        runtime = JobRuntime(store, persistence_interval_seconds=0.0)
        runtime._started = True
        cursor = store.current_event_cursor()

        def read() -> dict[str, Any] | None:
            return JobItem.model_validate(
                JobRuntime._serialize_job(store.get_job_row("sweep"))
            ).model_dump(mode="json").get("frequency_progress")

        await runtime._report_real_stage("sweep", "setup", 0.0, "Configuring BEAT Engine BEM solve (cpu)")
        assert read() is None
        await runtime._report_real_stage("sweep", "frequency_solve", 0.25, "Solving frequency 1/4 with BEAT Engine")
        assert read() == {"done": 1, "total": 4}
        await runtime._report_real_stage("sweep", "setup", 0.0, "Setting up BEAT solver context")
        assert store.get_job_row("sweep")["stage"] == "assemble"
        assert read() == {"done": 1, "total": 4}
        await runtime._report_real_stage("sweep", "frequency_solve", 0.5, "Solving frequency 2/4 with BEAT Engine")
        assert read() == {"done": 2, "total": 4}
        await runtime._report_real_stage("sweep", "finalizing", 1.0, "Packaging BEAT Engine solver results")
        assert read() is None

        stages = [event["payload"] for event in store.replay_events(cursor) if event["type"] == "stage"]
        assert [payload.get("frequency_progress") for payload in stages] == [
            None, {"done": 1, "total": 4}, {"done": 1, "total": 4}, {"done": 2, "total": 4}, None,
        ]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_a_log_only_flush_leaves_the_stored_count_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore.for_data_dir(tmp_path)
        store.initialize()
        store.create_job(_running("logs"))
        runtime = JobRuntime(store, persistence_interval_seconds=0.0)
        runtime._started = True
        await runtime._report_real_stage("logs", "frequency_solve", 0.5, "Solving frequency 3/6 with Metal BEM")
        await runtime._append_log("logs", "a line from the engine")
        await runtime._flush_runtime_update("logs")
        row = store.get_job_row("logs")
        assert row["task_metadata"]["log_tail"][-1] == "a line from the engine"
        assert job_frequency_progress(row) == {"done": 3, "total": 6}
        await runtime.shutdown()

    asyncio.run(scenario())


def test_a_count_less_line_coalesced_after_a_count_keeps_the_newer_count(tmp_path: Path) -> None:
    async def scenario() -> dict[str, Any] | None:
        store = JobStore.for_data_dir(tmp_path)
        store.initialize()
        store.create_job(_running("coalesced"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        await runtime._report_real_stage("coalesced", "frequency_solve", 0.25, "Solving frequency 1/4 with BEAT Engine")
        await runtime._report_real_stage("coalesced", "frequency_solve", 0.5, "Solving frequency 2/4 with BEAT Engine")
        await runtime._report_real_stage("coalesced", "frequency_solve", 0.5, "Refining acquisition")
        await runtime._flush_runtime_update("coalesced")
        found = job_frequency_progress(store.get_job_row("coalesced"))
        await runtime.shutdown()
        return found

    assert asyncio.run(scenario()) == {"done": 2, "total": 4}


@pytest.mark.parametrize("status", ["complete", "error", "cancelled"])
def test_a_job_that_stops_drops_its_stored_count(tmp_path: Path, status: str) -> None:
    store = JobStore.for_data_dir(tmp_path)
    store.initialize()
    store.create_job({**_running("stops"), "task_metadata": {"frequency_progress": {"done": 3, "total": 4}, "kept": 1}})
    assert store.update_job("stops", status=status)
    metadata = store.get_job_row("stops")["task_metadata"]
    assert "frequency_progress" not in metadata and metadata["kept"] == 1


def test_a_stop_that_rewrites_metadata_drops_the_count_too(tmp_path: Path) -> None:
    store = JobStore.for_data_dir(tmp_path)
    store.initialize()
    store.create_job({**_running("rewrite"), "task_metadata": {"frequency_progress": {"done": 3, "total": 4}}})
    assert store.update_job("rewrite", status="complete",
                            task_metadata_json=json.dumps({"frequency_progress": {"done": 4, "total": 4}, "x": 2}))
    assert store.get_job_row("rewrite")["task_metadata"] == {"x": 2}


def test_a_job_without_a_count_keeps_its_metadata_bytes_when_it_stops(tmp_path: Path) -> None:
    store = JobStore.for_data_dir(tmp_path)
    store.initialize()
    store.create_job({**_running("bytes"), "task_metadata": {"a": 1}})
    query = "SELECT task_metadata_json FROM simulation_jobs WHERE id = 'bytes'"
    before = store._connect().execute(query).fetchone()[0]
    assert store.update_job("bytes", status="complete")
    assert store._connect().execute(query).fetchone()[0] == before


@pytest.mark.parametrize("rewrite", [False, True])
def test_a_null_count_is_no_count_when_a_job_stops(tmp_path: Path, rewrite: bool) -> None:
    store = JobStore.for_data_dir(tmp_path)
    store.initialize()
    store.create_job({**_running("null"), "task_metadata": {"frequency_progress": None, "a": 1}})
    query = "SELECT task_metadata_json FROM simulation_jobs WHERE id = 'null'"
    fields: dict[str, Any] = {"status": "complete"}
    if rewrite:
        fields["task_metadata_json"] = json.dumps({"frequency_progress": None, "a": 2})
    before = fields.get("task_metadata_json") or store._connect().execute(query).fetchone()[0]
    assert store.update_job("null", **fields)
    assert store._connect().execute(query).fetchone()[0] == before
