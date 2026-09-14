from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from server.design.schema import Expr
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import ChannelCombineSpec, SolveRequest
from server.jobs.runtime import (
    CANCELLED_MESSAGE,
    JobConflictError,
    JobMeshDiscardedError,
    JobNotFoundError,
    JobResourceUnavailableError,
    JobRuntime,
    QUIT_INTERRUPTED_MESSAGE,
    QUIT_INTERRUPTED_STAGE_MESSAGE,
    RESTART_RECOVERY_MESSAGE,
    _apply_bempp_wall_default,
    _bempp_wall_adjustment_message,
    merge_provisional_results,
    resolve_submission,
)
from server.jobs.store import JobStore
from server.platform.shutdown_backstop import CLEANUP_RESERVE_SECONDS, ShutdownBackstop
from server.solver.base import EngineRunResult


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


def _bare_request(*, engine: str = "bempp", wall: float | None = 0) -> SolveRequest:
    mesh = {} if wall is None else {"wall_thickness": wall}
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "mesh": mesh,
                "enclosure": {"depth": 0},
                "simulation": {
                    "f1": 250,
                    "f2": 8000,
                    "num_frequencies": 2,
                    "sim_type": "freestanding",
                },
            },
            "options": {"engine": engine, "stage_delay_ms": 0},
        }
    )


@pytest.mark.parametrize("wall", [None, 0])
def test_bempp_materializes_ath_wall_default_without_mutating_input(
    wall: float | None,
) -> None:
    request = _bare_request(wall=wall)

    corrected, adjustment = _apply_bempp_wall_default(request, "bempp")

    assert adjustment is not None
    assert adjustment["requested"] == ("omitted" if wall is None else "explicit_zero")
    assert corrected is not request
    assert corrected.design.root.mesh.wall_thickness is not None
    assert corrected.design.root.mesh.wall_thickness.constant_value() == 5
    assert corrected.design_snapshot is not None
    snapshot_wall = corrected.design_snapshot.design.root.mesh.wall_thickness
    assert snapshot_wall is not None
    assert snapshot_wall.constant_value() == 5
    original_wall = request.design.root.mesh.wall_thickness
    assert (original_wall.constant_value() if original_wall is not None else None) == wall


def test_bempp_wall_default_leaves_closed_and_non_bempp_designs_unchanged() -> None:
    thick = _bare_request(wall=6)
    enclosed = _bare_request(wall=0)
    assert enclosed.design.root.enclosure is not None
    enclosed.design.root.enclosure.depth = Expr(value=200)

    bare = _bare_request(wall=0)
    for request, engine in ((thick, "bempp"), (enclosed, "bempp"), (bare, "metal")):
        unchanged, adjustment = _apply_bempp_wall_default(request, engine)
        assert unchanged is request
        assert adjustment is None


def test_auto_resolving_to_bempp_stores_the_corrected_five_mm_design(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "bempp-default.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: DryRunEngine(),
            ),
        )
        job_id = await runtime.submit(_bare_request(engine="auto", wall=0))
        await runtime.wait_idle()
        row = runtime.store.get_job_row(job_id)
        assert row is not None
        assert row["config_json"]["options"]["engine"] == "bempp"
        geometry = row["config_json"]["geometry"]
        assert geometry["design"]["mesh"]["wall_thickness"]["value"] == 5
        assert (
            geometry["design_snapshot"]["design"]["mesh"]["wall_thickness"]["value"]
            == 5
        )
        await runtime.shutdown()

    asyncio.run(scenario())


EXPECTED_WALL_ADJUSTMENT = {
    "kind": "bempp_wall_default",
    "effective_mm": 5.0,
    "reason_code": "bempp_free_standing_requires_closed_wall",
    "policy_version": 1,
}


def _bempp_registry() -> EngineRegistry:
    return EngineRegistry(
        detector=lambda: [EngineInfo("bempp", True, "test", "test")],
        factory=lambda _name: DryRunEngine(),
    )


@pytest.mark.parametrize(
    ("wall", "requested"), [(None, "omitted"), (0, "explicit_zero")]
)
def test_the_bempp_wall_default_is_reported_with_what_was_requested(
    wall: float | None, requested: str
) -> None:
    """An explicit 0 ("bare shell") is overridden too; it must not read as a default.

    The policy itself is unchanged: both requests still solve a 5 mm wall.
    """

    resolution = asyncio.run(
        resolve_submission(_bare_request(wall=wall), _bempp_registry())
    )

    assert resolution.symmetry_metadata["solver_plan"]["adjustments"] == [
        {**EXPECTED_WALL_ADJUSTMENT, "requested": requested}
    ]
    effective_wall = resolution.request.design.root.mesh.wall_thickness
    assert effective_wall is not None
    assert effective_wall.constant_value() == 5


def test_a_kept_bempp_wall_reports_no_adjustment() -> None:
    resolution = asyncio.run(
        resolve_submission(_bare_request(wall=6), _bempp_registry())
    )

    assert "adjustments" not in resolution.symmetry_metadata["solver_plan"]


@pytest.mark.parametrize(
    "stored", [{"requested": "omitted"}, {"requested": "explicit_zero", "effective_mm": "x"}]
)
def test_a_malformed_stored_wall_adjustment_still_logs_the_policy_value(
    stored: dict[str, Any],
) -> None:
    """The log line reads a stored row; a bad entry must not fail the job."""

    message = _bempp_wall_adjustment_message(
        {"kind": "bempp_wall_default", **stored}
    )

    assert message.startswith("BEMPP wall adjustment:")
    assert "5 mm" in message


class _CompletingBempp:
    name = "bempp"

    async def run(self, request: SolveRequest, *, cancel_cb: Any, stage_cb: Any) -> Any:
        cancel_cb()
        return EngineRunResult(
            results={
                "frequencies": [500.0],
                "directivity": {},
                "spl_on_axis": {
                    "frequencies": [500.0],
                    "spl": [90.0],
                    "phase_degrees": [0.0],
                },
                "impedance": {"frequencies": [500.0], "real": [1.0], "imaginary": [0.0]},
                "di": {"frequencies": [500.0], "di": {}},
                "metadata": {"engine": "fake-bempp"},
            },
            msh_text="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            mesh_stats={"vertex_count": 3, "triangle_count": 1},
        )


def test_a_submitted_bempp_job_stores_and_logs_the_wall_adjustment(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "bempp-report.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: _CompletingBempp(),
            ),
        )
        job_id = await runtime.submit(_bare_request(engine="bempp", wall=0))
        await runtime.wait_idle()
        expected = [{**EXPECTED_WALL_ADJUSTMENT, "requested": "explicit_zero"}]
        row = runtime.store.get_job_row(job_id)
        assert row is not None
        assert (
            row["task_metadata"]["symmetry"]["solver_plan"]["adjustments"]
            == expected
        )
        results = await runtime.get_results(job_id)
        assert results is not None
        assert (
            results["metadata"]["symmetry"]["solver_plan"]["adjustments"]
            == expected
        )
        reported = [
            line
            for line in runtime.store.get_job_log(job_id).splitlines()
            if line.startswith("BEMPP wall adjustment:")
        ]
        assert len(reported) == 1
        assert "explicit 0 mm wall thickness" in reported[0]
        assert "5 mm" in reported[0]
        await runtime.shutdown()

    asyncio.run(scenario())


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


def _streamed_response(
    index: int, frequency_hz: float, spl: float, *, impedance: bool = True
) -> dict[str, Any]:
    """One streamed frequency, shaped as ``build_provisional_frequency_response``.

    ``build_solver_response`` hands one ``frequencies`` list to the top level
    and to ``spl_on_axis``, ``impedance`` and ``di``; an imported channel with
    several sources then drops ``impedance``. The sharing is the point here.
    """

    frequency_values = [frequency_hz]
    response: dict[str, Any] = {
        "result_kind": "parametric",
        "frequencies": frequency_values,
        "directivity": {"horizontal": [[[0.0, spl]]]},
        "directivity_phase": {"horizontal": [[[0.0, 0.0]]]},
        "spl_on_axis": {
            "frequencies": frequency_values,
            "spl": [spl],
            "phase_degrees": [0.0],
        },
        "impedance": {"frequencies": frequency_values, "real": [1.0], "imaginary": [0.0]},
        "di": {"frequencies": frequency_values, "di": [3.0]},
        "metadata": {"provisional": {"completed_frequency_count": index + 1}},
    }
    if not impedance:
        response.pop("impedance")
    return response


def _metal_multi_channel_frame(index: int, frequency_hz: float) -> dict[str, Any]:
    """One streamed imported Metal frame: every channel for one frequency."""

    return {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "frequencies": [frequency_hz],
        "channels": {
            "hf": _streamed_response(index, frequency_hz, 90.0 + index, impedance=False),
            "lf": _streamed_response(index, frequency_hz, 80.0 + index),
        },
        "channel_order": ["hf", "lf"],
        "metadata": {"provisional": {"completed_frequency_count": index + 1}},
    }


_METAL_FRAME_FREQUENCIES = (200.0, 400.0, 800.0)


def _assert_channel_rows_match_frames(channel: dict[str, Any], label: str) -> None:
    count = len(_METAL_FRAME_FREQUENCIES)
    assert channel["frequencies"] == list(_METAL_FRAME_FREQUENCIES), label
    assert len(channel["directivity"]["horizontal"]) == count, label
    assert len(channel["directivity_phase"]["horizontal"]) == count, label
    for block_name in ("spl_on_axis", "impedance", "di"):
        for key, values in channel.get(block_name, {}).items():
            assert len(values) == count, (label, block_name, key)


def _assert_rows_match_frames(result: dict[str, Any]) -> None:
    assert result["frequencies"] == list(_METAL_FRAME_FREQUENCIES)
    for channel_id in ("hf", "lf"):
        _assert_channel_rows_match_frames(result["channels"][channel_id], channel_id)
    assert "impedance" not in result["channels"]["hf"]
    assert result["channels"]["lf"]["impedance"]["real"] == [1.0, 1.0, 1.0]


def test_provisional_merge_keeps_shared_frequency_lists_in_step_per_channel() -> None:
    frames = [
        _metal_multi_channel_frame(index, frequency)
        for index, frequency in enumerate(_METAL_FRAME_FREQUENCIES)
    ]

    merged: dict[str, Any] | None = None
    for frame in frames:
        merged = merge_provisional_results(merged, frame)

    assert merged is not None
    _assert_rows_match_frames(merged)
    assert merged["result_kind"] == "multi_channel"
    assert merged["channels"]["hf"]["result_kind"] == "parametric"
    assert merged["metadata"]["provisional"]["completed_frequency_count"] == 3
    assert frames[0]["channels"]["hf"]["frequencies"] == [200.0]
    assert frames[0]["channels"]["hf"]["spl_on_axis"]["frequencies"] == [200.0]


def test_runtime_reconnect_snapshot_keeps_multi_channel_rows_in_step(
    tmp_path: Path,
) -> None:
    runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
    runtime._running.add("live")
    frames = [
        _metal_multi_channel_frame(index, frequency)
        for index, frequency in enumerate(_METAL_FRAME_FREQUENCIES)
    ]

    for index, frame in enumerate(frames):
        runtime._accept_partial_result("live", index, frame)

    [message] = runtime.partial_result_messages()
    assert message["snapshot"] is True
    assert message["revision"] == 3
    _assert_rows_match_frames(message["result"])
    assert frames[0]["channels"]["lf"]["frequencies"] == [200.0]


def test_single_channel_provisional_rows_stay_in_step(tmp_path: Path) -> None:
    # Every parametric streamed solve (Metal, BEAT, bempp, circsym) sends the
    # builder's shape unwrapped, so its shared list sits at the top level.
    frames = [
        _streamed_response(index, frequency, 90.0 + index)
        for index, frequency in enumerate(_METAL_FRAME_FREQUENCIES)
    ]

    merged: dict[str, Any] | None = None
    for frame in frames:
        merged = merge_provisional_results(merged, frame)
    assert merged is not None
    _assert_channel_rows_match_frames(merged, "merge")

    runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
    runtime._running.add("live")
    for index, frame in enumerate(frames):
        runtime._accept_partial_result("live", index, frame)
    [message] = runtime.partial_result_messages()
    _assert_channel_rows_match_frames(message["result"], "snapshot")
    assert frames[0]["spl_on_axis"]["frequencies"] == [200.0]


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


def test_shutdown_waits_for_threaded_solver_cancellation_checkpoint(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "jobs.db"
        solve_started = threading.Event()
        solve_exited = threading.Event()
        release_solver = threading.Event()

        class CheckpointEngine:
            name = "checkpoint"

            async def run(
                self,
                _request: SolveRequest,
                *,
                cancel_cb: Any,
                stage_cb: Any,
            ) -> Any:
                del stage_cb

                def solve() -> Any:
                    solve_started.set()
                    try:
                        while not release_solver.wait(timeout=0.01):
                            cancel_cb()
                        return SimpleNamespace(
                            results={"metadata": {}},
                            msh_text=None,
                            mesh_stats=None,
                        )
                    finally:
                        solve_exited.set()

                return await asyncio.to_thread(solve)

        runtime = JobRuntime(
            JobStore(database),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: CheckpointEngine(),
            ),
        )
        job_id = await runtime.submit(_bare_request(engine="bempp"))
        assert await asyncio.to_thread(solve_started.wait, 2.0)
        try:
            await asyncio.wait_for(runtime.shutdown(), timeout=3.0)
            assert solve_exited.is_set()
        finally:
            release_solver.set()

        inspection = JobStore(database)
        inspection.initialize()
        try:
            row = inspection.get_job_row(job_id)
            assert row is not None
            assert row["status"] == "cancelled"
            assert row["cancellation_requested"] is False
            # Stopped by the app's own shutdown, not by the user.
            assert row["stage_message"] == QUIT_INTERRUPTED_STAGE_MESSAGE
            assert row["error_message"] == QUIT_INTERRUPTED_MESSAGE
        finally:
            inspection.close()

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


def test_startup_recovery_reads_a_quit_interrupted_job_as_interrupted_by_quit(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    request_dump = _request(delay_ms=1).model_dump(mode="json")
    now = datetime.now().isoformat()

    def running(job_id: str) -> dict[str, Any]:
        return {
            "id": job_id,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "started_at": now,
            "progress": 0.3,
            "stage": "solve",
            "config_json": request_dump,
            "config_summary_json": {"formula_type": "OSSE"},
            "task_metadata": {},
        }

    store.create_job(running("quit-interrupted"))
    store.create_job(running("crashed"))
    marked = store.request_cancellation(
        "quit-interrupted",
        {"stage": "cancelling", "cancellation_requested": True},
        {"stage": "cancelling", "message": "Shutdown requested"},
        interrupted_by_quit=True,
    )
    assert marked is not None

    async def scenario() -> None:
        runtime = JobRuntime(store)
        await runtime.start()
        await runtime.wait_idle()
        interrupted = await runtime.get_job("quit-interrupted")
        crashed = await runtime.get_job("crashed")
        # Not a failure and not requeued: the user quit on purpose.
        assert interrupted["status"] == "cancelled"
        assert interrupted["stage_message"] == QUIT_INTERRUPTED_STAGE_MESSAGE
        assert interrupted["error_message"] == QUIT_INTERRUPTED_MESSAGE
        assert interrupted["cancellation_requested"] is False
        # An orphan nothing marked is still what it was: a crash.
        assert crashed["status"] == "error"
        assert crashed["error_message"] == RESTART_RECOVERY_MESSAGE
        recovered = {
            event["jobId"]: event
            for event in store.replay_events(0)
            if (event.get("payload") or {}).get("recovered") is True
        }
        assert recovered["quit-interrupted"]["type"] == "cancelled"
        assert recovered["quit-interrupted"]["payload"]["message"] == QUIT_INTERRUPTED_MESSAGE
        assert recovered["crashed"]["type"] == "failed"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_a_stop_budget_bounds_the_checkpoint_wait_and_the_next_start_reads_the_quit(
    tmp_path: Path,
) -> None:
    recorded_exits: list[int] = []

    async def scenario() -> float:
        database = tmp_path / "jobs.db"
        solve_started = threading.Event()
        release_solver = threading.Event()

        class NeverCheckpoints:
            name = "never-checkpoints"

            async def run(
                self, _request: SolveRequest, *, cancel_cb: Any, stage_cb: Any
            ) -> Any:
                del cancel_cb, stage_cb

                def solve() -> Any:
                    # A native call with no cancellation point, like an OCC build.
                    solve_started.set()
                    release_solver.wait(30)
                    return SimpleNamespace(
                        results={"metadata": {}}, msh_text=None, mesh_stats=None
                    )

                return await asyncio.to_thread(solve)

        def registry() -> EngineRegistry:
            return EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: NeverCheckpoints(),
            )

        runtime = JobRuntime(JobStore(database), engine_registry=registry())
        job_id = await runtime.submit(_bare_request(engine="bempp"))
        assert await asyncio.to_thread(solve_started.wait, 5.0)
        backstop = ShutdownBackstop(
            CLEANUP_RESERVE_SECONDS + 0.5,
            exit_process=recorded_exits.append,
            flush=lambda: None,
        )
        backstop.activate()
        backstop.begin("a stop request")
        try:
            started = time.monotonic()
            await runtime.shutdown()
            elapsed = time.monotonic() - started
        finally:
            backstop.deactivate()
            release_solver.set()
            backstop.exit_now("test finished")

        # Cut off, not settled: the row is left for the next start to read.
        inspection = JobStore(database)
        inspection.initialize()
        try:
            left = inspection.get_job_row(job_id)
            assert left is not None
            assert left["status"] == "running"
        finally:
            inspection.close()

        restarted = JobRuntime(JobStore(database), engine_registry=registry())
        await restarted.start()
        row = await restarted.get_job(job_id)
        assert row["status"] == "cancelled"
        assert row["stage_message"] == QUIT_INTERRUPTED_STAGE_MESSAGE
        assert row["error_message"] == QUIT_INTERRUPTED_MESSAGE
        await restarted.shutdown()
        return elapsed

    elapsed = asyncio.run(scenario())
    # 0.5 s of budget was left for this wait; unbudgeted it is 10 s.
    assert elapsed < 3.0, f"shutdown waited {elapsed:.2f} s for a job past its budget"


def test_a_job_queued_at_quit_is_not_started_by_the_shutdown(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str, str, list[str], str]:
        database = tmp_path / "jobs.db"
        solves_started: list[int] = []
        first_started = threading.Event()

        class CheckpointEngine:
            name = "checkpoint"

            async def run(
                self, _request: SolveRequest, *, cancel_cb: Any, stage_cb: Any
            ) -> Any:
                del stage_cb

                def solve() -> Any:
                    solves_started.append(1)
                    first_started.set()
                    for _ in range(500):
                        cancel_cb()
                        time.sleep(0.01)
                    return SimpleNamespace(
                        results={"metadata": {}}, msh_text=None, mesh_stats=None
                    )

                return await asyncio.to_thread(solve)

        runtime = JobRuntime(
            JobStore(database),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: CheckpointEngine(),
            ),
        )
        running_id = await runtime.submit(_bare_request(engine="bempp"))
        assert await asyncio.to_thread(first_started.wait, 5.0)
        queued_id = await runtime.submit(_bare_request(engine="bempp"))
        await runtime.shutdown()

        inspection = JobStore(database)
        inspection.initialize()
        try:
            running_row = inspection.get_job_row(running_id)
            queued_row = inspection.get_job_row(queued_id)
            assert running_row is not None and queued_row is not None
            requeued, _events = inspection.recover_on_startup(
                RESTART_RECOVERY_MESSAGE,
                quit_stage_message=QUIT_INTERRUPTED_STAGE_MESSAGE,
                quit_error_message=QUIT_INTERRUPTED_MESSAGE,
            )
        finally:
            inspection.close()
        return (
            len(solves_started),
            str(running_row["status"]),
            str(queued_row["status"]),
            [str(row["id"]) for row in requeued],
            queued_id,
        )

    solves, running_status, queued_status, requeued, queued_id = asyncio.run(scenario())
    # Admission stopped: the queued job was neither started nor lost.
    assert solves == 1
    assert running_status == "cancelled"
    assert queued_status == "queued"
    assert requeued == [queued_id]


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


def test_cancellation_transition_survives_log_flush_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_running_job("cancelled"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        subscriber = runtime.events.subscribe()
        await runtime._append_log("cancelled", "last buffered line")
        pending = runtime._pending_updates["cancelled"]
        original_persist = store.persist_runtime_update

        def fail_flush(*_args: object, **_kwargs: object) -> None:
            raise OSError("log device unavailable")

        monkeypatch.setattr(store, "persist_runtime_update", fail_flush)
        try:
            await runtime._cancel_job("cancelled")

            row = store.get_job_row("cancelled")
            assert row is not None
            assert row["status"] == "cancelled"
            assert row["stage"] == "cancelled"
            durable = [
                event
                for event in store.replay_events(0)
                if event["jobId"] == "cancelled" and event["type"] == "cancelled"
            ]
            assert len(durable) == 1
            assert subscriber.qsize() == 1
            assert subscriber.get_nowait()["type"] == "cancelled"
            assert pending.closed is True
            assert "cancelled" not in runtime._pending_updates
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


def test_recombine_serialises_overlapping_edits_for_one_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crossover edit reverted before its reply must not stay persisted.

    Recombining is a read-modify-write of the stored results. Two overlapping
    requests for one job would each rebuild from the pre-edit results, and the
    one the database keeps would be whichever finished last rather than the
    crossover the rail was left showing.
    """

    import server.solver.recombine as recombine_module

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    now = datetime.now().isoformat()
    store.create_job(
        {
            "id": "live",
            "status": "complete",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "completed_at": now,
            "progress": 1.0,
            "stage": "complete",
            "stage_message": "complete",
            "config_json": {"design": {"formula": "OSSE", "L": 120}},
            "config_summary_json": {"formula_type": "OSSE"},
            "task_metadata": {},
        }
    )
    store.store_results("live", {"frequencies": [1000.0], "crossover_hz": 1000.0})
    store.store_channel_bases("live", b"bases-npz")

    reads: list[dict[str, Any]] = []
    entered = threading.Event()
    release = threading.Event()

    def fake_recombine(
        results: dict[str, Any],
        bases: bytes,
        spec: ChannelCombineSpec,
        request: SolveRequest,
    ) -> dict[str, Any]:
        reads.append(results)
        if not entered.is_set():
            entered.set()
            # Hold the first read-modify-write open so the second request has
            # to be ordered against it rather than racing it.
            release.wait(10)
        return {"frequencies": [1000.0], "crossover_hz": spec.crossovers_hz[0]}

    monkeypatch.setattr(recombine_module, "recombine_stored_results", fake_recombine)

    def spec(hz: float) -> ChannelCombineSpec:
        return ChannelCombineSpec.model_validate(
            {"id": "combined", "members": ["mf", "hf"], "crossovers_hz": [hz]}
        )

    async def scenario() -> None:
        runtime = JobRuntime(store)
        await runtime.start()
        try:
            edited = asyncio.create_task(runtime.recombine_results("live", spec(900.0)))
            assert await asyncio.to_thread(entered.wait, 10)
            reverted = asyncio.create_task(
                runtime.recombine_results("live", spec(1000.0))
            )
            await asyncio.sleep(0.2)
            assert len(reads) == 1, "the second edit read the results mid-write"

            release.set()
            assert (await edited)["crossover_hz"] == 900.0
            assert (await reverted)["crossover_hz"] == 1000.0

            # The revert rebuilt from what the edit stored, and is what the
            # job keeps: the durable result matches the crossover left on
            # screen, and no turnstile outlives the requests that needed it.
            assert reads[1]["crossover_hz"] == 900.0
            stored = await asyncio.to_thread(store.get_results, "live")
            assert stored["crossover_hz"] == 1000.0
            assert runtime._result_mutation == {}
        finally:
            release.set()
            await runtime.shutdown()

    asyncio.run(scenario())


def test_a_quit_marks_every_running_job_the_moment_it_begins(tmp_path: Path) -> None:
    """The reason is recorded before any shutdown wait, not when the runtime's
    own shutdown handler finally runs.

    Uvicorn drains connections for up to 3 s and other handlers run first, so
    a process that ends inside its budget -- the backstop, the launcher's kill,
    a force quit -- used to leave its running jobs unmarked, and the next start
    read them as "Server restarted during execution".
    """

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    request_dump = _request(delay_ms=1).model_dump(mode="json")
    now = datetime.now().isoformat()

    def row(job_id: str, status: str, **extra: Any) -> dict[str, Any]:
        return {
            "id": job_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "started_at": now,
            "progress": 0.3,
            "stage": "solve",
            "config_json": request_dump,
            "config_summary_json": {"formula_type": "OSSE"},
            "task_metadata": {"kept": "yes"},
            **extra,
        }

    store.create_job(row("running-a", "running"))
    store.create_job(row("running-b", "running"))
    store.create_job(row("user-cancelled", "running", cancellation_requested=True))
    store.create_job(row("queued", "queued"))
    store.create_job(row("done", "complete"))

    runtime = JobRuntime(store)
    marked = runtime.mark_running_interrupted_by_quit("status window requested quit")

    assert sorted(marked) == ["running-a", "running-b"]
    # Nothing a user sees moves until the runtime's own shutdown does it.
    assert store.get_job_row("running-a")["status"] == "running"
    assert store.get_job_row("running-a")["cancellation_requested"] in (0, False)
    store.close()

    reopened = JobStore(tmp_path / "jobs.db")
    reopened.initialize()

    async def scenario() -> None:
        next_start = JobRuntime(reopened)
        await next_start.start()
        await next_start.wait_idle()
        for job_id in ("running-a", "running-b"):
            job = await next_start.get_job(job_id)
            assert job["status"] == "cancelled", job
            assert job["stage_message"] == QUIT_INTERRUPTED_STAGE_MESSAGE
            assert job["error_message"] == QUIT_INTERRUPTED_MESSAGE
        # A job the user had already asked to stop keeps its own story: it
        # was stopped, not crashed by the restart, and not interrupted by
        # Quit either -- nothing marked it that way, because
        # `mark_running_interrupted_by_quit` leaves an already-cancellation-
        # requested row to its own request rather than also marking it.
        cancelled = await next_start.get_job("user-cancelled")
        assert cancelled["status"] == "cancelled", cancelled
        assert cancelled["error_message"] == CANCELLED_MESSAGE
        assert cancelled["error_message"] != QUIT_INTERRUPTED_MESSAGE
        await next_start.shutdown()

    asyncio.run(scenario())


def test_marking_at_quit_never_raises(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    runtime = JobRuntime(store)
    store.close()
    (tmp_path / "jobs.db").unlink()
    (tmp_path / "jobs.db").mkdir()

    assert runtime.mark_running_interrupted_by_quit("a stop request") == []


def test_a_stop_that_begins_during_an_approved_update_restart_is_marked_as_that(
    tmp_path: Path,
) -> None:
    """The begin-time mark names the update restart when one is approved (contract §4.3).

    A process cut off inside its budget before the runtime's own shutdown ran
    has only this mark, so it alone decides what the next start reads.
    """

    from server.jobs.runtime import UPDATE_RESTART_MESSAGE, UPDATE_RESTART_STAGE_MESSAGE
    from server.updates.restart import RestartApproval

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    now = datetime.now().isoformat()
    store.create_job(
        {
            "id": "running",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "started_at": now,
            "progress": 0.3,
            "stage": "solve",
            "config_json": _request(delay_ms=1).model_dump(mode="json"),
            "config_summary_json": {"formula_type": "OSSE"},
            "task_metadata": {},
        }
    )
    approval = RestartApproval()
    approval.approve("v2.0.1")

    runtime = JobRuntime(store, restart_approval=approval)
    assert runtime.mark_running_interrupted_by_quit("status window requested quit") == ["running"]
    store.close()

    reopened = JobStore(tmp_path / "jobs.db")
    reopened.initialize()

    async def scenario() -> None:
        next_start = JobRuntime(reopened)
        await next_start.start()
        await next_start.wait_idle()
        job = await next_start.get_job("running")
        assert job["status"] == "cancelled", job
        assert job["stage_message"] == UPDATE_RESTART_STAGE_MESSAGE, job
        assert job["error_message"] == UPDATE_RESTART_MESSAGE, job
        await next_start.shutdown()

    asyncio.run(scenario())


def test_no_queued_job_starts_once_a_stop_has_begun(tmp_path: Path) -> None:
    """A stop begins before the runtime's own shutdown -- up to Uvicorn's 3 s drain
    earlier. A queued job that started in between would carry no mark, and if
    the process were then cut off the next start would read it as a crash. So
    the begin-time mark closes admission first: the job stays queued for the
    next start.
    """

    async def scenario() -> tuple[int, str]:
        database = tmp_path / "jobs.db"
        solves_started: list[int] = []
        first_started = threading.Event()
        release = threading.Event()

        class HeldEngine:
            name = "held"

            async def run(
                self, _request: SolveRequest, *, cancel_cb: Any, stage_cb: Any
            ) -> Any:
                del stage_cb, cancel_cb

                def solve() -> Any:
                    solves_started.append(1)
                    first_started.set()
                    release.wait(30)
                    return SimpleNamespace(
                        results={"metadata": {}}, msh_text=None, mesh_stats=None
                    )

                return await asyncio.to_thread(solve)

        runtime = JobRuntime(
            JobStore(database),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: HeldEngine(),
            ),
        )
        try:
            await runtime.submit(_bare_request(engine="bempp"))
            assert await asyncio.to_thread(first_started.wait, 5.0)
            queued_id = await runtime.submit(_bare_request(engine="bempp"))

            # The backstop's thread, the moment the stop begins.
            await asyncio.to_thread(
                runtime.mark_running_interrupted_by_quit, "status window requested quit"
            )
            # The running job finishes during the drain; nothing may take its place.
            release.set()
            deadline = time.monotonic() + 3.0
            while len(solves_started) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            status = str(runtime.store.get_job_row(queued_id)["status"])
            return len(solves_started), status
        finally:
            release.set()
            await runtime.shutdown()

    solves, queued_status = asyncio.run(scenario())
    assert solves == 1, "a queued job started after the stop began"
    assert queued_status == "queued"


def test_a_job_that_starts_as_the_stop_begins_marks_itself(tmp_path: Path) -> None:
    """The stop begins after a queued job's last admission check but before its
    start: the stop's own marks miss it, so its start marks it.
    """

    database = tmp_path / "jobs.db"

    async def scenario() -> object:
        started = threading.Event()
        release = threading.Event()

        class HeldEngine:
            name = "held"

            async def run(
                self, _request: SolveRequest, *, cancel_cb: Any, stage_cb: Any
            ) -> Any:
                del stage_cb, cancel_cb

                def solve() -> Any:
                    started.set()
                    release.wait(30)
                    return SimpleNamespace(
                        results={"metadata": {}}, msh_text=None, mesh_stats=None
                    )

                return await asyncio.to_thread(solve)

        store = JobStore(database)
        runtime = JobRuntime(
            store,
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: HeldEngine(),
            ),
        )
        real_start = store.start_job

        def start_as_the_stop_begins(*args: Any, **kwargs: Any) -> Any:
            # Past the admission checks, before the row is marked running.
            runtime.mark_running_interrupted_by_quit("status window requested quit")
            return real_start(*args, **kwargs)

        store.start_job = start_as_the_stop_begins  # type: ignore[method-assign]
        try:
            job_id = await runtime.submit(_bare_request(engine="bempp"))
            assert await asyncio.to_thread(started.wait, 5.0)
            with sqlite3.connect(database) as conn:
                return conn.execute(
                    "SELECT json_extract(task_metadata_json, '$.interrupted_by_quit') "
                    "FROM simulation_jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()[0]
        finally:
            release.set()
            await runtime.shutdown()

    assert asyncio.run(scenario()) == 1
