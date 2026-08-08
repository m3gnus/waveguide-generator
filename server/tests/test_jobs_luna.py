"""Regression coverage for accepted Luna jobs/runtime/event findings."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from server.jobs import store as store_module
from server.jobs.api import create_jobs_router
from server.jobs.events import CLOSE_UNSUPPORTED_VERSION, JobsProtocol
from server.jobs.models import JobMetadataPatch, SolveOptions, SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.mesh.builder import _solver_mesher_config


def _record(job_id: str, status: str = "running") -> dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "id": job_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.1,
        "stage": status,
        "config_json": {"design": {"formula": "OSSE"}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


def _request() -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "mesh": {"quadrants": 1234}},
            "options": {"engine": "dryrun", "stage_delay_ms": 0},
        }
    )


@pytest.mark.parametrize("bound", [float("nan"), float("inf"), float("-inf")])
def test_frequency_range_rejects_every_nonfinite_bound(bound: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        SolveOptions(frequency_range=[bound, 1000.0])


@pytest.mark.parametrize(
    ("simulation", "message"),
    [
        ({"f1": "cos(p)", "f2": 1000}, "f1 must be a scalar"),
        (
            {"f1": {"value": 200, "raw": "200 + cos(p)"}, "f2": 1000},
            "f1 must be a scalar",
        ),
        ({"f1": 2000, "f2": 1000}, "positive and increasing"),
        ({"num_frequencies": "cos(p)"}, "num_frequencies must be a scalar"),
        ({"num_frequencies": 1.5}, "integer from 1 to 401"),
        ({"num_frequencies": 402}, "integer from 1 to 401"),
    ],
)
def test_solve_request_rejects_design_sweep_values_that_would_silently_default(
    simulation: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        SolveRequest.model_validate(
            {"design": {"formula": "OSSE", "simulation": simulation}}
        )


def test_solve_request_accepts_cached_parameterized_geometry_expression() -> None:
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "a": {"value": 45, "raw": "45 + cos(p)"},
            }
        }
    )
    assert request.design.root.a is not None
    assert request.design.root.a.execution_text() == "45 + cos(p)"


@pytest.mark.parametrize(
    ("design_patch", "field"),
    [
        ({"source": {"shape": {"value": 2, "raw": "2 - p"}}}, "source.shape"),
        ({"mesh": {"quadrants": {"value": 1, "raw": "1 + p"}}}, "mesh.quadrants"),
        (
            {"enclosure": {"depth": {"value": 80, "raw": "80 * p"}}},
            "enclosure.depth",
        ),
    ],
)
def test_solve_request_rejects_cached_parameterized_structural_controls(
    design_patch: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        SolveRequest.model_validate(
            {"design": {"formula": "OSSE", **design_patch}}
        )


def test_metadata_normalizes_v1_export_fields() -> None:
    patch = JobMetadataPatch(
        exported_files=[" first.csv ", "first.csv", "", " second.json "],
        auto_export_completed_at=" 2026-08-04T12:00:00Z ",
        raw_results_file=" results.json ",
        mesh_artifact_file="   ",
    )
    assert patch.exported_files == ["first.csv", "second.json"]
    assert patch.auto_export_completed_at == "2026-08-04T12:00:00Z"
    assert patch.raw_results_file == "results.json"
    assert patch.mesh_artifact_file is None


def test_mesh_artifact_database_read_runs_off_the_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("mesh", "complete"))
        store.store_mesh_artifact("mesh", "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        runtime = JobRuntime(store)
        runtime._started = True
        caller_thread = threading.get_ident()
        observed_threads: list[int] = []
        original = store.get_mesh_artifact

        def observed(job_id: str) -> str | None:
            observed_threads.append(threading.get_ident())
            return original(job_id)

        store.get_mesh_artifact = observed  # type: ignore[method-assign]
        assert (await runtime.get_mesh_artifact("mesh")).startswith("$MeshFormat")
        assert observed_threads and observed_threads[0] != caller_thread
        await runtime.shutdown()

    asyncio.run(scenario())


def test_initialize_schema_ddl_rolls_back_as_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        store_module,
        "_SCHEMA_STATEMENTS",
        ("CREATE TABLE partial (id INTEGER)", "THIS IS NOT SQL"),
    )
    store = JobStore(tmp_path / "jobs.db")
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial'"
        ).fetchone() is None


def test_complete_job_cannot_overwrite_committed_cancellation(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_record("race"))
    store.update_job("race", cancellation_requested=True)
    event = store.complete_job(
        "race",
        {"frequencies": [100.0]},
        {"status": "complete", "progress": 1.0},
        {"status": "complete"},
    )
    assert event is None
    assert store.get_job_row("race")["status"] == "running"
    assert store.get_results("race") is None

    store.create_job(_record("completion-wins"))
    completed = store.complete_job(
        "completion-wins",
        {"frequencies": [100.0]},
        {"status": "complete", "progress": 1.0},
        {"status": "complete"},
    )
    assert completed is not None
    cancellation = store.request_cancellation(
        "completion-wins",
        {"stage": "cancelling", "cancellation_requested": True},
        {"stage": "cancelling"},
    )
    assert cancellation is None
    assert store.get_job_row("completion-wins")["cancellation_requested"] is False


def test_shutdown_guard_prevents_scheduler_resurrection(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("first", "queued"))
        store.create_job(_record("second", "queued"))
        runtime = JobRuntime(store)
        runtime._started = True
        runtime._queue.extend(["first", "second"])
        entered = asyncio.Event()

        async def block(_job_id: str, _row: Any) -> None:
            entered.set()
            await asyncio.Future()

        runtime._run_job = block  # type: ignore[method-assign]
        runtime._ensure_scheduler()
        await entered.wait()
        await runtime.shutdown()
        await asyncio.sleep(0)
        assert runtime._shutting_down is True
        assert runtime._scheduler_task is None
        assert not runtime.background_tasks
        assert list(runtime._queue) == ["second"]

    asyncio.run(scenario())


def test_post_job_retention_runs_for_each_completed_work_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("one", "queued"))
        store.create_job(_record("expired", "complete"))
        store.update_job("expired", completed_at="2000-01-01T00:00:00")
        runtime = JobRuntime(store)
        runtime._started = True
        runtime._queue.append("one")
        subscriber = runtime.events.subscribe()
        calls = 0

        async def finish(_job_id: str, _row: Any) -> None:
            return None

        original = store.prune_terminal_jobs_with_events

        def observed(**kwargs: Any) -> tuple[list[str], list[dict[str, Any]]]:
            nonlocal calls
            calls += 1
            return original(**kwargs)

        runtime._run_job = finish  # type: ignore[method-assign]
        monkeypatch.setattr(store, "prune_terminal_jobs_with_events", observed)
        await runtime._drain_scheduler()
        assert calls == 1
        event = subscriber.get_nowait()
        assert event["type"] == "deleted"
        assert event["jobId"] == "expired"
        assert event["payload"] == {"reason": "retention"}

    asyncio.run(scenario())


def test_real_stage_task_exception_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("real"))
        runtime = JobRuntime(store)
        runtime._started = True

        async def fail_stage(*_args: Any) -> None:
            raise RuntimeError("stage write failed")

        runtime._report_real_stage = fail_stage  # type: ignore[method-assign]

        class Engine:
            name = "mock"

            async def run(self, _request: Any, *, cancel_cb: Any, stage_cb: Any) -> Any:
                stage_cb("solve", 0.5, "half")
                await asyncio.sleep(0)
                return SimpleNamespace(results={"ok": True}, msh_text=None, mesh_stats=None)

        with caplog.at_level(logging.ERROR):
            await runtime._run_real_engine("real", _request(), Engine())
        assert "Stage/progress persistence failed" in caplog.text
        assert "stage write failed" in caplog.text

    asyncio.run(scenario())


def test_full_job_log_is_on_disk_while_metadata_remains_tail_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore.for_data_dir(tmp_path)
        store.initialize()
        store.create_job(_record("log"))
        runtime = JobRuntime(store)
        runtime._started = True
        for index in range(225):
            await runtime._append_log("log", f"line-{index}")
        complete = await runtime.get_log("log")
        tail = store.get_job_row("log")["task_metadata"]["log_tail"]
        assert complete.startswith("line-0\n")
        assert complete.endswith("line-224\n")
        assert len(tail) == 200
        assert tail[0] == "line-25"
        assert store.job_logs_dir == tmp_path / "logs" / "jobs"

        endpoint = next(
            route.endpoint
            for route in create_jobs_router(runtime).routes
            if getattr(route, "path", None) == "/api/jobs/{job_id}/log"
        )
        response = await endpoint("log")
        assert response.body.decode().startswith("line-0\n")
        assert response.body.decode().endswith("line-224\n")

    asyncio.run(scenario())


def test_frequency_progress_and_logs_are_coalesced_until_stage_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore.for_data_dir(tmp_path)
        store.initialize()
        store.create_job(_record("coalesced"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        original = store.persist_runtime_update
        writes = 0

        def observed(*args: Any, **kwargs: Any) -> Any:
            nonlocal writes
            writes += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "persist_runtime_update", observed)
        for index in range(25):
            await runtime._report_real_stage(
                "coalesced",
                "frequency_solve",
                index / 24,
                f"frequency-{index}",
            )

        # The first stage boundary is immediate; the remaining 24 callbacks
        # are represented by one pending latest-value checkpoint.
        assert writes == 1
        assert store.get_job_row("coalesced")["progress"] == 0.35

        await runtime._report_real_stage(
            "coalesced", "finalizing", 1.0, "packaging-results"
        )
        assert writes == 3
        row = store.get_job_row("coalesced")
        assert row["stage"] == "postprocess"
        assert row["progress"] == pytest.approx(0.99)
        assert row["task_metadata"]["log_tail"][-2:] == [
            "frequency-24",
            "packaging-results",
        ]
        assert store.get_job_log("coalesced").count("frequency-") == 25

        events = store.replay_events(0)
        assert [event["type"] for event in events] == [
            "stage",
            "log",
            "stage",
            "log",
            "stage",
            "log",
        ]
        assert events[2]["payload"]["message"] == "frequency-24"
        assert events[3]["payload"]["chunk"] == "frequency-24"
        assert events[3]["payload"]["lines"] == [
            f"frequency-{index}" for index in range(1, 25)
        ]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_real_completion_flushes_latest_coalesced_progress_and_log(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("terminal"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True

        class Engine:
            name = "mock"

            async def run(self, _request: Any, *, cancel_cb: Any, stage_cb: Any) -> Any:
                for index in range(20):
                    stage_cb(
                        "frequency_solve",
                        index / 19,
                        f"terminal-frequency-{index}",
                    )
                await asyncio.sleep(0)
                return SimpleNamespace(
                    results={"metadata": {}}, msh_text=None, mesh_stats=None
                )

        await runtime._run_real_engine("terminal", _request(), Engine())
        row = store.get_job_row("terminal")
        assert row["status"] == "complete"
        assert row["task_metadata"]["log_tail"][-1] == "terminal-frequency-19"
        assert store.get_job_log("terminal").endswith("terminal-frequency-19\n")
        events = store.replay_events(0)
        assert events[-1]["type"] == "completed"
        assert events[-3]["type"] == "stage"
        assert events[-3]["payload"]["message"] == "terminal-frequency-19"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_graceful_shutdown_flushes_last_buffered_log(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("shutdown"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        await runtime._append_log("shutdown", "last-before-shutdown")

        await runtime.shutdown()

        row = store.get_job_row("shutdown")
        assert row["status"] == "running"
        assert row["task_metadata"]["log_tail"] == ["last-before-shutdown"]
        assert store.get_job_log("shutdown") == "last-before-shutdown\n"

    asyncio.run(scenario())


def test_forgotten_runtime_buffer_rejects_detached_callback_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("detached"))
        runtime = JobRuntime(store, persistence_interval_seconds=60.0)
        runtime._started = True
        detached = runtime._runtime_update_state("detached")
        await runtime._flush_runtime_update("detached", forget=True)
        assert detached.closed is True
        assert "detached" not in runtime._pending_updates

        monkeypatch.setattr(
            runtime, "_runtime_update_state", lambda _job_id: detached
        )
        await runtime._queue_runtime_update(
            "detached",
            stage="solve",
            progress=0.5,
            message="must-not-persist",
            log_message="must-not-persist",
        )

        assert detached.pending is False
        assert store.get_job_log("detached") == ""
        assert store.replay_events(0) == []
        await runtime.shutdown()

    asyncio.run(scenario())


class _Transport:
    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.closes: list[int] = []

    async def send_json(self, message: Any) -> None:
        self.json.append(dict(message))

    async def close(self, code: int) -> None:
        self.closes.append(code)


@pytest.mark.parametrize("raw", ["[]", "1", "true", '{"v": true}'])
def test_jobs_envelope_requires_object_and_strict_integer_version(
    raw: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        runtime = JobRuntime(store)
        runtime._started = True
        transport = _Transport()
        keep_open, cursor = await JobsProtocol(runtime, epoch=1)._handle_message(transport, raw)
        assert keep_open is False and cursor is None
        assert transport.closes == [CLOSE_UNSUPPORTED_VERSION]

    asyncio.run(scenario())


def test_jobs_envelope_recursion_error_closes_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        runtime = JobRuntime(store)
        runtime._started = True
        transport = _Transport()

        def explode(_raw: str) -> Any:
            raise RecursionError("nested too deeply")

        monkeypatch.setattr(json, "loads", explode)
        result = await JobsProtocol(runtime, epoch=1)._handle_message(transport, "{}")
        assert result == (False, None)
        assert transport.closes == [CLOSE_UNSUPPORTED_VERSION]

    asyncio.run(scenario())


def test_resume_handler_returns_authoritative_cursor_before_live_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_record("resume", "queued"), initial_event=("queued", {}))
        store.update_job_with_event("resume", {"progress": 0.5}, "progress", {})
        runtime = JobRuntime(store)
        runtime._started = True
        transport = _Transport()
        keep_open, cursor = await JobsProtocol(runtime, epoch=7)._handle_message(
            transport, {"v": 1, "kind": "resume", "epoch": 7, "cursor": 0}
        )
        assert keep_open is True
        assert cursor == 2
        assert [message["cursor"] for message in transport.json] == [1, 2]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("heartbeat", "limit"),
    [(0.0, 10), (-1.0, 10), (float("nan"), 10), (1.0, 0), (1.0, True)],
)
def test_jobs_protocol_rejects_invalid_timing_and_size_limits(
    heartbeat: float, limit: Any, tmp_path: Path
) -> None:
    runtime = JobRuntime(JobStore(tmp_path / "jobs.db"))
    with pytest.raises(ValueError):
        JobsProtocol(runtime, heartbeat_seconds=heartbeat, max_message_bytes=limit)


def test_frequency_options_defensively_reject_nonfinite_mutation() -> None:
    request = _request()
    request.options.frequency_range = [float("nan"), 1000.0]
    with pytest.raises(ValueError, match="finite"):
        JobRuntime._frequency_options(request)


def test_jobs_solver_path_keeps_digit_list_1234_not_bitmask_15() -> None:
    config = _solver_mesher_config(_request().design)
    assert config["mesh"]["quadrants"] == 1234
    assert config["mesh"]["quadrants"] != 15


@pytest.mark.parametrize(
    "frequencies, match",
    [
        ([], "at least one"),
        ([500.0, 500.0], "strictly ascending"),
        ([1000.0, 500.0], "strictly ascending"),
        ([0.0, 500.0], "positive"),
        ([-1.0], "positive"),
        ([float("nan")], "finite"),
        ([float("inf")], "finite"),
        ([float(index) for index in range(1, 403)], "at most 401"),
    ],
)
def test_explicit_frequencies_reject_unusable_lists(frequencies, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        SolveOptions(frequencies_hz=frequencies)


@pytest.mark.parametrize(
    "extra", [{"num_frequencies": 8}, {"frequency_range": [200.0, 20_000.0]}]
)
def test_explicit_frequencies_refuse_to_coexist_with_a_generated_grid(extra) -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        SolveOptions(frequencies_hz=[500.0, 1000.0], **extra)


def test_explicit_frequencies_accept_a_single_ascending_sweep() -> None:
    assert SolveOptions(frequencies_hz=[812.3]).frequencies_hz == [812.3]
    assert SolveOptions().frequencies_hz is None
