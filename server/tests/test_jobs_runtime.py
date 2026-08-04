from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from server.jobs.models import SolveRequest
from server.jobs.runtime import JobConflictError, JobNotFoundError, JobRuntime
from server.jobs.store import JobStore


def _request(*, delay_ms: int = 2, count: int = 5) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {"f1": 250, "f2": 8000, "num_frequencies": count},
            },
            "options": {"engine": "dryrun", "stage_delay_ms": delay_ms},
        }
    )


async def _wait_stage(store: JobStore, job_id: str, stage: str) -> None:
    async def wait_loop() -> None:
        while True:
            row = store.get_job_row(job_id)
            if row and row["stage"] == stage:
                return
            if row and row["status"] in {"complete", "error", "cancelled"}:
                raise AssertionError(f"job ended in {row['status']} before stage {stage}")
            await asyncio.sleep(0.002)

    await asyncio.wait_for(wait_loop(), 3)


def test_fifo_order_and_strong_scheduler_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        ids = [await runtime.submit(_request(delay_ms=5)) for _ in range(3)]
        assert runtime.background_tasks
        await runtime.wait_idle()
        completed = [
            event["jobId"]
            for event in runtime.store.replay_events(0)
            if event["type"] == "completed"
        ]
        assert completed == ids
        statuses = [(await runtime.get_job(job_id))["status"] for job_id in ids]
        assert statuses == ["complete", "complete", "complete"]
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["mesh", "assemble", "solve", "postprocess"])
def test_cancellation_is_acknowledged_at_every_stage(
    stage: str, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / f"{stage}.db"))
        job_id = await runtime.submit(_request(delay_ms=100))
        await _wait_stage(runtime.store, job_id, stage)
        response = await runtime.stop(job_id)
        assert response["status"] == "cancelling"
        await runtime.wait_idle()
        row = await runtime.get_job(job_id)
        assert row["status"] == "cancelled"
        assert row["stage"] == "cancelled"
        assert row["cancellation_requested"] is False
        assert runtime.store.get_results(job_id) is None
        await runtime.shutdown()

    asyncio.run(scenario())


def test_startup_recovery_fails_running_orphan_and_requeues_fifo(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    request_dump = _request(delay_ms=1).model_dump(mode="json")
    now = datetime.now().isoformat()

    def record(job_id: str, status: str) -> dict[str, Any]:
        return {
            "id": job_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "started_at": now if status == "running" else None,
            "progress": 0.3 if status == "running" else 0.0,
            "stage": "solve" if status == "running" else "queued",
            "config_json": request_dump,
            "config_summary_json": {"formula_type": "OSSE"},
            "task_metadata": {},
        }

    store.create_job(record("orphan-running", "running"))
    store.create_job(record("orphan-queued", "queued"))

    async def scenario() -> None:
        runtime = JobRuntime(store)
        await runtime.start()
        await runtime.wait_idle()
        running = await runtime.get_job("orphan-running")
        queued = await runtime.get_job("orphan-queued")
        assert running["status"] == "error"
        assert running["error_message"] == "Server restarted during execution"
        assert queued["status"] == "complete"
        recovery = [
            event
            for event in store.replay_events(0)
            if event["jobId"] == "orphan-running" and event["type"] == "failed"
        ]
        assert recovery[0]["payload"]["recovered"] is True
        await runtime.shutdown()

    asyncio.run(scenario())


def test_result_db_write_failure_transitions_to_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        runtime = JobRuntime(store)

        def fail_complete(*args: Any, **kwargs: Any) -> None:
            raise sqlite_error("disk full")

        job_id = await runtime.submit(_request(delay_ms=1))
        monkeypatch.setattr(store, "complete_job", fail_complete)
        await runtime.wait_idle()
        row = await runtime.get_job(job_id)
        assert row["status"] == "error"
        assert "persistence failed" in row["error_message"]
        assert store.get_results(job_id) is None
        await runtime.shutdown()

    class sqlite_error(RuntimeError):
        pass

    asyncio.run(scenario())


def test_queued_stop_active_delete_and_terminal_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        first = await runtime.submit(_request(delay_ms=100))
        second = await runtime.submit(_request(delay_ms=1))
        response = await runtime.stop(second)
        assert response["status"] == "cancelled"
        with pytest.raises(JobConflictError):
            await runtime.delete(first)
        await runtime.delete(second)
        with pytest.raises(JobNotFoundError):
            await runtime.get_job(second)
        await runtime.stop(first)
        await runtime.wait_idle()
        await runtime.shutdown()

    asyncio.run(scenario())


def test_delete_and_clear_failed_race_has_one_winner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        runtime = JobRuntime(store)
        job_id = await runtime.submit(_request(delay_ms=1))
        await runtime.wait_idle()
        store.update_job(job_id, status="error", stage="error")

        async def delete_one() -> str:
            try:
                await runtime.delete(job_id)
                return "delete"
            except JobNotFoundError:
                return "missing"

        delete_result, cleared = await asyncio.gather(delete_one(), runtime.clear_failed())
        assert (delete_result == "delete" and cleared == []) or (
            delete_result == "missing" and cleared == [job_id]
        )
        assert store.get_job_row(job_id) is None
        await runtime.shutdown()

    asyncio.run(scenario())


def test_dryrun_results_are_deterministic_and_plausibly_shaped(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        ids = [await runtime.submit(_request(delay_ms=0, count=7)) for _ in range(2)]
        await runtime.wait_idle()
        first, second = [await runtime.get_results(job_id) for job_id in ids]
        first["metadata"].pop("solve_wall_time_seconds")
        second["metadata"].pop("solve_wall_time_seconds")
        assert first == second
        assert len(first["frequencies"]) == 7
        assert len(first["spl_on_axis"]["spl"]) == 7
        assert len(first["directivity"]["horizontal"]) == 7
        assert len(first["directivity"]["vertical"][0]) == 37
        assert len(first["impedance"]["real"]) == 7
        assert first["metadata"]["synthetic"] is True
        await runtime.shutdown()

    asyncio.run(scenario())


def test_solve_creation_atomically_persists_snapshot_revision_label_and_polar_grid(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "r0": {"value": 12.7, "raw": "6.35*2"},
                "simulation": {"f1": 250, "f2": 500, "num_frequencies": 2},
            },
            "options": {
                "engine": "dryrun",
                "stage_delay_ms": 0,
                "polar_config": {
                    "angle_range": [0, 180, 26],
                    "angle_step": 7,
                },
            },
            "label": "  atomic design  ",
            "design_revision": 42,
            "design_snapshot": {
                "version": 1,
                "design": {
                    "formula": "OSSE",
                    "r0": {"value": 12.7, "raw": "6.35*2"},
                    "simulation": {"f1": 250, "f2": 500, "num_frequencies": 2},
                },
            },
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "atomic.db"))
        job_id = await runtime.submit(request)
        job = await runtime.get_job(job_id)
        assert job["label"] == "atomic design"
        assert job["design_revision"] == 42
        assert job["script_snapshot"]["version"] == 1
        assert job["script_snapshot"]["design"]["r0"] == {
            "value": 12.7,
            "raw": "6.35*2",
        }
        assert job["polar_grid"] == {
            "start": 0.0,
            "end": 180.0,
            "count": 26,
            "requested_step": 7.0,
            "resolved_step": 7.2,
        }
        await runtime.wait_idle()
        results = await runtime.get_results(job_id)
        assert results["metadata"]["design_revision"] == 42
        assert results["metadata"]["polar_grid"] == job["polar_grid"]
        await runtime.shutdown()

    asyncio.run(scenario())
