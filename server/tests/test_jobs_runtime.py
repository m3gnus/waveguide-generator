from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from server.engines.dryrun import DryRunEngine
from server.jobs.models import SolveRequest
from server.jobs.runtime import (
    JobConflictError,
    JobMeshDiscardedError,
    JobNotFoundError,
    JobResourceUnavailableError,
    JobRuntime,
    merge_provisional_results,
)
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


def _running_job(job_id: str) -> dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "id": job_id,
        "status": "running",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": now,
        "progress": 0.5,
        "stage": "solve",
        "stage_message": "Solving",
        "config_json": _request(delay_ms=0).model_dump(mode="json"),
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


def test_provisional_result_merge_appends_frequency_blocks_and_channels() -> None:
    first = {
        "frequencies": [200.0],
        "directivity": {"horizontal": [[[0.0, 0.0]]]},
        "spl_on_axis": {"frequencies": [200.0], "spl": [90.0]},
        "channels": {"hf": {"frequencies": [200.0]}},
        "metadata": {"provisional": {"completed_frequency_count": 1}},
    }
    second = {
        "frequencies": [400.0],
        "directivity": {"horizontal": [[[0.0, 0.0]]]},
        "spl_on_axis": {"frequencies": [400.0], "spl": [93.0]},
        "channels": {"hf": {"frequencies": [400.0]}},
        "metadata": {"provisional": {"completed_frequency_count": 2}},
    }
    merged = merge_provisional_results(first, second)
    assert merged["frequencies"] == [200.0, 400.0]
    assert len(merged["directivity"]["horizontal"]) == 2
    assert merged["spl_on_axis"]["spl"] == [90.0, 93.0]
    assert merged["channels"]["hf"]["frequencies"] == [200.0, 400.0]
    assert merged["metadata"]["provisional"]["completed_frequency_count"] == 2
    assert first["frequencies"] == [200.0]


def test_runtime_publishes_frequency_delta_and_keeps_full_process_snapshot(
    tmp_path: Path,
) -> None:
    runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
    runtime._running.add("live")
    queue = runtime.events.subscribe()

    runtime._accept_partial_result("live", 0, {"frequencies": [200.0]})
    runtime._accept_partial_result("live", 1, {"frequencies": [400.0]})

    assert queue.get_nowait() == {
        "v": 1,
        "kind": "partialResult",
        "jobId": "live",
        "revision": 1,
        "snapshot": False,
        "result": {"frequencies": [200.0]},
    }
    assert queue.get_nowait()["revision"] == 2
    assert runtime.partial_result_messages() == [
        {
            "v": 1,
            "kind": "partialResult",
            "jobId": "live",
            "revision": 2,
            "snapshot": True,
            "result": {"frequencies": [200.0, 400.0]},
        }
    ]


def test_runtime_snapshot_sorts_progressive_frequency_deltas() -> None:
    first = {
        "frequencies": [100.0],
        "spl_on_axis": {"frequencies": [100.0], "spl": [1.0]},
        "directivity": {"horizontal": [[[0.0, -1.0]]]},
    }
    high = {
        "frequencies": [700.0],
        "spl_on_axis": {"frequencies": [700.0], "spl": [7.0]},
        "directivity": {"horizontal": [[[0.0, -7.0]]]},
    }
    middle = {
        "frequencies": [400.0],
        "spl_on_axis": {"frequencies": [400.0], "spl": [4.0]},
        "directivity": {"horizontal": [[[0.0, -4.0]]]},
    }

    merged = merge_provisional_results(first, high)
    merged = merge_provisional_results(merged, middle)

    assert merged["frequencies"] == [100.0, 400.0, 700.0]
    assert merged["spl_on_axis"] == {
        "frequencies": [100.0, 400.0, 700.0],
        "spl": [1.0, 4.0, 7.0],
    }
    assert merged["directivity"]["horizontal"] == [
        [[0.0, -1.0]],
        [[0.0, -4.0]],
        [[0.0, -7.0]],
    ]


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


def test_completed_unrated_job_keeps_mesh_available_during_grace_window(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        job_id = await runtime.submit(_request(delay_ms=0))
        await runtime.wait_idle()

        job = await runtime.get_job(job_id)
        assert job["status"] == "complete"
        assert job["has_mesh_artifact"] is True
        assert job["has_results"] is True
        assert runtime.store.get_mesh_artifact(job_id).startswith("$MeshFormat")
        assert runtime.store.get_results(job_id) is not None
        await runtime.shutdown()

    asyncio.run(scenario())


def test_retry_replays_stored_options_and_parent_after_results_are_pruned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        request = SolveRequest.model_validate(
            {
                "design": {
                    "formula": "OSSE",
                    "L": 137,
                    "a": 41,
                    "simulation": {"f1": 250, "f2": 8000, "num_frequencies": 5},
                },
                "options": {
                    "engine": "dryrun",
                    "frequency_range": [333.0, 1777.0],
                    "num_frequencies": 7,
                    "frequency_spacing": "linear",
                    "verbose": True,
                    "mesh_validation_mode": "off",
                    "stage_delay_ms": 0,
                },
                "label": "Faithful source",
                "design_revision": 9,
            }
        )
        source_id = await runtime.submit(request)
        await runtime.wait_idle()
        source = runtime.store.get_job_row(source_id)
        stored_options = source["config_json"]["options"]
        runtime.store.update_job(
            source_id, completed_at="2000-01-01T00:00:00"
        )
        assert runtime.store.prune_terminal_jobs() == 1
        assert runtime.store.get_results(source_id) is None
        assert runtime.store.get_job_row(source_id) is not None

        retry_id = await runtime.retry(source_id)
        await runtime.wait_idle()
        replay = runtime.store.get_job_row(retry_id)

        assert replay["config_json"]["options"] == stored_options
        assert replay["config_json"]["geometry"] == source["config_json"]["geometry"]
        assert replay["parent_job_id"] == source_id
        assert replay["config_json"]["parent_job_id"] == source_id
        assert replay["run_number"] == source["run_number"] + 1
        serialized_source = await runtime.get_job(source_id)
        assert serialized_source["solve_options"] == stored_options
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
        if runtime.store.get_mesh_artifact(job_id) is None:
            with pytest.raises(JobResourceUnavailableError) as missing:
                await runtime.get_mesh_artifact(job_id)
            assert not isinstance(missing.value, JobMeshDiscardedError)
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


def test_second_runtime_cannot_recover_jobs_owned_by_live_runtime(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "jobs.db"
        first = JobRuntime(JobStore(database))
        second = JobRuntime(JobStore(database))
        await first.start()
        try:
            with pytest.raises(JobConflictError, match="already owns"):
                await second.start()
        finally:
            await first.shutdown()
        await second.start()
        await second.shutdown()

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


def test_runtime_log_retry_pins_batch_when_buffer_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_running_job("retry"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        original_update = store._update_job

        def fail_update(*_args: object, **_kwargs: object) -> bool:
            raise sqlite3.OperationalError("simulated commit path failure")

        await runtime._append_log("retry", "first")
        monkeypatch.setattr(store, "_update_job", fail_update)
        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            await runtime._flush_runtime_update("retry")
        assert store.get_job_log("retry") == "first\n"

        monkeypatch.setattr(store, "_update_job", original_update)
        await runtime._append_log("retry", "second")
        await runtime._flush_runtime_update("retry")
        await runtime._append_log("retry", "third")
        await runtime._flush_runtime_update("retry")

        assert store.get_job_log("retry") == "first\nsecond\nthird\n"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_metadata_patch_preserves_a_runtime_log_committed_after_its_stale_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old whole-blob writer deterministically lost ``during-patch`` here."""

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _running_job("metadata-race")
    record["task_metadata"] = {
        "log_tail": ["before"],
        "exported_files": ["a.step"],
    }
    store.create_job(record)
    runtime = JobRuntime(store)
    runtime._started = True

    stale_read_finished = threading.Event()
    runtime_log_committed = threading.Event()
    original_require_job = runtime._require_job
    failures: list[BaseException] = []

    def pause_after_stale_read(job_id: str) -> dict[str, Any]:
        row = original_require_job(job_id)
        stale_read_finished.set()
        if not runtime_log_committed.wait(timeout=2.0):
            raise AssertionError("runtime log update did not commit")
        return row

    monkeypatch.setattr(runtime, "_require_job", pause_after_stale_read)

    def patch_metadata() -> None:
        try:
            asyncio.run(
                runtime.patch_metadata(
                    "metadata-race",
                    {
                        "label": "Reference",
                        "script_snapshot": {"version": 1},
                        "rating": 5,
                        "exported_files": ["b.step"],
                    },
                )
            )
        except BaseException as exc:  # surfaced below on the pytest thread
            failures.append(exc)

    patch_thread = threading.Thread(target=patch_metadata, name="metadata-patch")
    patch_thread.start()
    assert stale_read_finished.wait(timeout=2.0)
    try:
        changed, events = store.persist_runtime_update(
            "metadata-race",
            {},
            log_lines=("during-patch",),
            expected_log_size=0,
        )
        assert changed is True
        assert [event["type"] for event in events] == ["log"]
    finally:
        runtime_log_committed.set()

    patch_thread.join(timeout=2.0)
    assert not patch_thread.is_alive()
    assert failures == []

    row = store.get_job_row("metadata-race")
    assert row["label"] == "Reference"
    assert row["script_snapshot"] == {"version": 1}
    assert row["task_metadata"]["rating"] == 5
    assert row["task_metadata"]["exported_files"] == ["a.step", "b.step"]
    assert row["task_metadata"]["log_tail"] == ["before", "during-patch"]
    metadata_event = store.replay_events(0)[-1]
    assert metadata_event["type"] == "metadata"
    assert metadata_event["payload"] == {
        "changed": {
            "label": "Reference",
            "script_snapshot": {"version": 1},
            "rating": 5,
            "exported_files": ["a.step", "b.step"],
        }
    }
    store.close()


def test_execution_metadata_mutation_preserves_other_metadata_and_missing_is_silent(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _running_job("execution-metadata")
    record["task_metadata"] = {
        "rating": 4,
        "log_tail": ["existing"],
    }
    store.create_job(record)
    runtime = JobRuntime(store)

    runtime._record_execution_metadata(
        "execution-metadata",
        {
            "solve_path": "axisymmetric-meridian",
            "axisymmetric_eligibility_reasons": ["eligible"],
            "solve_wall_time_seconds": 1.25,
        },
    )
    runtime._record_execution_metadata("missing", {})

    metadata = store.get_job_row("execution-metadata")["task_metadata"]
    assert metadata == {
        "rating": 4,
        "log_tail": ["existing"],
        "solve_path": "axisymmetric-meridian",
        "axisymmetric_eligibility_reasons": ["eligible"],
        "solve_wall_time_seconds": 1.25,
    }
    assert store.current_event_cursor() == 0
    store.close()


def test_failure_transition_survives_log_flush_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_running_job("failed"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        subscriber = runtime.events.subscribe()
        await runtime._append_log("failed", "last buffered line")
        pending = runtime._pending_updates["failed"]
        original_persist = store.persist_runtime_update

        def fail_flush(*_args: object, **_kwargs: object) -> None:
            raise OSError("log device unavailable")

        monkeypatch.setattr(store, "persist_runtime_update", fail_flush)
        try:
            await runtime._fail_job("failed", "solver exploded")

            row = store.get_job_row("failed")
            assert row is not None
            assert row["status"] == "error"
            assert row["stage"] == "error"
            durable = [
                event
                for event in store.replay_events(0)
                if event["jobId"] == "failed" and event["type"] == "failed"
            ]
            assert len(durable) == 1
            assert subscriber.qsize() == 1
            assert subscriber.get_nowait()["type"] == "failed"
            assert pending.closed is True
            assert "failed" not in runtime._pending_updates
        finally:
            monkeypatch.setattr(store, "persist_runtime_update", original_persist)
            await runtime.shutdown()

    asyncio.run(scenario())


def test_successful_solve_survives_final_log_flush_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        runtime = JobRuntime(store)
        original_persist = store.persist_runtime_update
        injected = False

        def fail_final_flush(*args: Any, **kwargs: Any) -> Any:
            nonlocal injected
            log_lines = tuple(kwargs.get("log_lines") or ())
            if not injected and "Dry-run result persistence ready" in log_lines:
                injected = True
                raise OSError("log device unavailable")
            return original_persist(*args, **kwargs)

        monkeypatch.setattr(store, "persist_runtime_update", fail_final_flush)
        job_id = await runtime.submit(_request(delay_ms=0))
        await runtime.wait_idle()

        row = await runtime.get_job(job_id)
        assert injected is True
        assert row["status"] == "complete"
        assert store.get_results(job_id) is not None
        terminal_types = [
            event["type"]
            for event in store.replay_events(0)
            if event["jobId"] == job_id
            and event["type"] in {"completed", "failed", "cancelled"}
        ]
        assert terminal_types == ["completed"]
        await runtime.shutdown()

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


@pytest.mark.parametrize(
    "resolved_engine", [DryRunEngine(), None], ids=["available", "unavailable"]
)
def test_queued_stop_wins_while_scheduler_resolves_engine(
    resolved_engine: DryRunEngine | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    class PausedRegistry:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def get_engine(self, _name: str) -> DryRunEngine | None:
            self.calls += 1
            if self.calls == 1:
                # Submission validates availability before the scheduler later
                # resolves the engine again. Only the scheduler lookup is the
                # race window under test.
                return DryRunEngine()
            self.entered.set()
            await self.release.wait()
            return resolved_engine

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        registry = PausedRegistry()
        runtime = JobRuntime(store, engine_registry=registry)  # type: ignore[arg-type]
        try:
            job_id = await runtime.submit(_request(delay_ms=0))
            await asyncio.wait_for(registry.entered.wait(), 1)

            assert store.get_job_row(job_id)["status"] == "queued"
            assert job_id in runtime.running_job_ids
            response = await runtime.stop(job_id)
            assert response["status"] == "cancelled"

            registry.release.set()
            await runtime.wait_idle()
            row = store.get_job_row(job_id)
            assert row is not None
            assert row["status"] == "cancelled"
            assert store.get_results(job_id) is None
            event_types = [
                event["type"]
                for event in store.replay_events(0) or []
                if event["jobId"] == job_id
            ]
            assert "started" not in event_types
            assert "completed" not in event_types
            assert "failed" not in event_types
        finally:
            registry.release.set()
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
        assert first["directivity_phase"] == {}
        assert len(first["impedance"]["real"]) == 7
        assert first["metadata"]["synthetic"] is True
        assert first["metadata"]["directivity"]["effective_distance_m"] == 2.0
        assert first["metadata"]["directivity"]["sound_speed_m_per_s"] == 343.0
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


def test_explicit_frequencies_run_verbatim_and_are_summarized_as_such(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole job path, end to end: request -> summary -> solved axis."""

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    sweep = [500.0, 812.3, 1000.0, 3150.0]

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
        job_id = await runtime.submit(
            SolveRequest.model_validate(
                {
                    "design": {
                        "formula": "OSSE",
                        "L": 120,
                        "a": 45,
                        # Deliberately different from the sweep: the list wins.
                        "simulation": {"f1": 250, "f2": 8000, "num_frequencies": 5},
                    },
                    "options": {
                        "engine": "dryrun",
                        "stage_delay_ms": 0,
                        "frequencies_hz": sweep,
                    },
                }
            )
        )
        await runtime.wait_idle()
        results = await runtime.get_results(job_id)
        assert results["frequencies"] == sweep
        assert len(results["spl_on_axis"]["spl"]) == len(sweep)
        assert results["metadata"]["frequency_spacing"] == "explicit"
        assert results["metadata"]["frequency_source"] == "explicit_list"
        summary = (await runtime.get_job(job_id))["config_summary"]
        assert summary["frequency_range"] == [sweep[0], sweep[-1]]
        assert summary["num_frequencies"] == len(sweep)
        assert summary["frequency_source"] == "explicit_list"
        await runtime.shutdown()

    asyncio.run(scenario())
