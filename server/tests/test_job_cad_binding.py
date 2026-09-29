"""Binding a prepared CAD solve to its request: one transaction (S4-E2).

A CAD solve WG accepted is a ``preparing`` job holding an intent. Binding freezes the
exact ``SolveRequest`` into it, writes what the jobs system stores of a request (its
metadata, its mesh artifact, its run number) and queues it -- ``JobStore.bind_preparing_job``.
These tests are about that one write: it is all or nothing, it is decided against a stop,
and it hands out run numbers that have no gaps. They run the real submission checks
(``JobRuntime._compose_job``) on a complete ingestion record.

The scenarios of the operations backend that these replace are named on the
``old_only`` marks of ``test_cad_preparation.py`` (a bound request left behind by a
crash, a job created and its answer lost, a dismissal racing the submission).
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.cad_intent import BIND_BLOCKED, BIND_STOPPED, BOUND, CadSolveIntent
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore

from types import SimpleNamespace

from test_imported_jobs import _AlwaysRegistry, _request
from test_imported_jobs import _runtime_fixture as _imported_runtime


async def _runtime_fixture(tmp_path: Path) -> tuple[JobRuntime, str, dict[str, Any]]:
    """A runtime over a complete ingestion record, started (its store is initialised)."""

    runtime, ingest_id, record = await _imported_runtime(tmp_path)
    # Every submission may ask the registry for an engine (the fixture's answers once).
    runtime.engine_registry = _AlwaysRegistry(SimpleNamespace(name="metal"))  # type: ignore[assignment]
    await runtime.start()
    return runtime, ingest_id, record


class _Kill(BaseException):
    """The process dying: nothing after it runs, and no handler in the write catches it."""


def _intent(name: str = "cmd-1") -> CadSolveIntent:
    return CadSolveIntent(
        operation_id=name,
        bundle_path="wgreturn/speaker.wgreturn",
        manifest_sha256="sha256:" + "4" * 64,
        return_id="wgr_1",
    )


def _preparing(runtime: JobRuntime, name: str = "cmd-1", **intent_fields: Any) -> str:
    """A job accepted and held by a lane (``started_at`` set), as binding finds it."""

    record = runtime._preparing_record(
        CadSolveIntent(**{**_intent(name).__dict__, **intent_fields})
    )
    runtime.store.create_job(record)
    runtime.store.claim_preparing_job(
        record["id"], stage="validating", stage_message="Checking the return", progress=0.02
    )
    return str(record["id"])


def _provenance(name: str = "cmd-1") -> dict[str, Any]:
    return {"operation_id": name}


def _table_counts(runtime: JobRuntime, job_id: str) -> dict[str, int]:
    with closing(sqlite3.connect(runtime.store.db_path)) as conn:
        return {
            "identity": conn.execute(
                "SELECT COUNT(*) FROM job_identity WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
            "artifacts": conn.execute(
                "SELECT COUNT(*) FROM simulation_artifacts WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
            "queued_events": conn.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = ? AND event_type = 'queued'",
                (job_id,),
            ).fetchone()[0],
        }


def _quiet(runtime: JobRuntime) -> None:
    """Keep a bound job queued: these tests look at what binding wrote, not at a solve."""

    runtime._ensure_scheduler = lambda: None  # type: ignore[method-assign]


def test_binding_queues_the_job_with_its_request_run_number_and_mesh(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime)
            before = runtime.store.get_job_row(job_id)
            assert before["status"] == "preparing" and before["run_number"] is None
            assert before["config_json"]["type"] == "cad_intent"

            outcome = await runtime._bind_cad_job(job_id, _request(ingest_id), _provenance())

            assert outcome == BOUND
            row = runtime.store.get_job_row(job_id)
            assert (row["status"], row["stage"], row["run_number"]) == ("queued", "queued", 1)
            # The request the jobs system stores, exactly as ``submit`` would have.
            assert row["config_json"]["geometry"]["ingest_id"] == ingest_id
            assert "type" not in row["config_json"]
            assert row["task_metadata"]["cad"] == _provenance()
            assert row["task_metadata"]["imported_geometry"]["ingest_id"] == ingest_id
            assert row["has_mesh_artifact"] is True
            assert runtime.store.get_mesh_artifact(job_id).startswith("$MeshFormat")
            assert list(runtime._queue) == [job_id]
            assert _table_counts(runtime, job_id) == {
                "identity": 1, "artifacts": 1, "queued_events": 1,
            }
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_an_unheld_preparing_job_cannot_bind(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime)
            runtime.store.hand_back_preparing_job(
                job_id, stage="received", stage_message="Waiting to prepare this solve"
            )
            result = runtime.store.bind_preparing_job(
                job_id, config=_request(ingest_id).model_dump(mode="json"),
                config_summary={}, task_metadata={}, mesh_artifact="$MeshFormat\n",
                mesh_stats=None, script_snapshot=None, label=None, parent_job_id=None,
                initial_event=("queued", {"status": "queued", "progress": 0.0}),
            )
            assert result is None
            assert runtime.store.get_job_row(job_id)["run_number"] is None
            assert _table_counts(runtime, job_id) == {
                "identity": 0, "artifacts": 0, "queued_events": 0,
            }
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_binding_is_one_transaction_a_kill_before_commit_leaves_preparing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write of the binding has been made when the process dies; none of them stays."""

    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime)
            written: list[str] = []
            real_append = JobStore._append_event

            def die_at_the_event(self: JobStore, conn: sqlite3.Connection, *args: Any) -> Any:
                # After the UPDATE, the identity and the mesh artifact, in the transaction.
                inside = {
                    "identity": conn.execute(
                        "SELECT COUNT(*) FROM job_identity WHERE job_id = ?", (job_id,)
                    ).fetchone()[0],
                    "artifact": conn.execute(
                        "SELECT COUNT(*) FROM simulation_artifacts WHERE job_id = ?", (job_id,)
                    ).fetchone()[0],
                    "status": conn.execute(
                        "SELECT status FROM simulation_jobs WHERE id = ?", (job_id,)
                    ).fetchone()[0],
                }
                written.append(repr(inside))
                assert inside == {"identity": 1, "artifact": 1, "status": "queued"}
                raise _Kill()

            monkeypatch.setattr(JobStore, "_append_event", die_at_the_event)
            with pytest.raises(_Kill):
                await runtime._bind_cad_job(job_id, _request(ingest_id), _provenance())
            monkeypatch.setattr(JobStore, "_append_event", real_append)
            assert written, "the kill was never reached: the test proves nothing"

            # What a new process finds: the job is as it was accepted.
            row = runtime.store.get_job_row(job_id)
            assert row["status"] == "preparing"
            assert row["run_number"] is None
            assert row["config_json"]["type"] == "cad_intent"
            assert row["has_mesh_artifact"] is False
            assert row["task_metadata"] == {"cad": {"operation_id": "cmd-1"}}
            assert _table_counts(runtime, job_id) == {
                "identity": 0, "artifacts": 0, "queued_events": 0,
            }
            assert list(runtime._queue) == []
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_binding_is_one_transaction_a_kill_after_commit_leaves_queued_with_the_mesh(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        job_id = _preparing(runtime)

        def die_after_the_commit() -> None:
            raise _Kill()

        # The bind committed; the process dies before it can queue the job in memory.
        runtime._ensure_scheduler = die_after_the_commit  # type: ignore[method-assign]
        with pytest.raises(_Kill):
            await runtime._bind_cad_job(job_id, _request(ingest_id), _provenance())
        await runtime.shutdown()

        # A new process: the row, its run number and its mesh are all there.
        reopened = JobRuntime(
            JobStore(tmp_path / "jobs.db"), cadlink_store=CadLinkStore(tmp_path / "cadlink.db")
        )
        _quiet(reopened)
        try:
            await reopened.start()
            row = reopened.store.get_job_row(job_id)
            assert (row["status"], row["run_number"]) == ("queued", 1)
            assert reopened.store.get_mesh_artifact(job_id).startswith("$MeshFormat")
            assert row["config_json"]["geometry"]["ingest_id"] == ingest_id
            # And it is queued again by start-up recovery: nothing asks for a new press.
            assert list(reopened._queue) == [job_id]
        finally:
            await reopened.shutdown()

    asyncio.run(scenario())


def test_a_stop_and_the_binding_race_and_exactly_one_wins(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            # Stop first: the binding writes nothing, and says so.
            stopped = _preparing(runtime, "cmd-stopped")
            await runtime.stop(stopped)
            assert await runtime._bind_cad_job(stopped, _request(ingest_id), _provenance()) == BIND_STOPPED
            row = runtime.store.get_job_row(stopped)
            assert (row["status"], row["run_number"]) == ("cancelled", None)
            assert _table_counts(runtime, stopped) == {"identity": 0, "artifacts": 0, "queued_events": 0}

            # Bind first: the stop finds a queued job and cancels that.
            bound = _preparing(runtime, "cmd-bound")
            assert await runtime._bind_cad_job(bound, _request(ingest_id), _provenance()) == BOUND
            assert (await runtime.stop(bound))["status"] == "cancelled"
            row = runtime.store.get_job_row(bound)
            assert (row["status"], row["run_number"]) == ("cancelled", 1)
            assert list(runtime._queue) == []
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_a_stop_that_saw_preparing_but_lost_the_race_reads_the_job_again(tmp_path: Path) -> None:
    """The compare-and-set itself: a stop names the status it saw, and only that one is stopped."""

    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime)
            real_get = runtime._require_job
            calls: list[str] = []

            def read_then_bind(job: str) -> Any:
                row = real_get(job)
                calls.append(row["status"])
                if len(calls) == 1:
                    # The binding commits between the stop's read and its write.
                    assert row["status"] == "preparing"
                    runtime.store.bind_preparing_job(
                        job_id,
                        config=_request(ingest_id).model_dump(mode="json"),
                        config_summary={}, task_metadata={}, mesh_artifact="$MeshFormat\n",
                        mesh_stats=None, script_snapshot=None, label=None, parent_job_id=None,
                        initial_event=("queued", {"status": "queued", "progress": 0.0}),
                    )
                return row

            runtime._require_job = read_then_bind  # type: ignore[method-assign]
            result = await runtime.stop(job_id)

            assert result["status"] == "cancelled"
            assert calls == ["preparing", "queued"]  # it read again, and answered for a queued job
            assert runtime.store.get_job_row(job_id)["status"] == "cancelled"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_an_approved_restart_blocks_the_binding_and_writes_nothing(tmp_path: Path) -> None:
    class Latch:
        pending = "0.3.4"

        def refusal(self) -> str:
            return "restarting"

        def remaining(self) -> None:
            return None

        def admit(self, _start: Any) -> tuple[bool, None]:
            return False, None

        def add_release_listener(self, _listener: Any) -> None:
            return None

    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        runtime.restart_approval = Latch()  # type: ignore[assignment]
        try:
            job_id = _preparing(runtime)
            assert await runtime._bind_cad_job(job_id, _request(ingest_id), _provenance()) == BIND_BLOCKED
            assert runtime.store.get_job_row(job_id)["status"] == "preparing"
            assert _table_counts(runtime, job_id) == {"identity": 0, "artifacts": 0, "queued_events": 0}
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_a_request_the_jobs_system_refuses_binds_nothing(tmp_path: Path) -> None:
    """The submission checks run at binding, as they do in ``submit``: a refusal changes no row."""

    from server.jobs.runtime import ImportedSolveRefusal

    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime)
            request = _request(ingest_id, skipped_source_ids=["nope"])
            with pytest.raises(ImportedSolveRefusal):
                await runtime._bind_cad_job(job_id, request, _provenance())
            row = runtime.store.get_job_row(job_id)
            assert (row["status"], row["run_number"]) == ("preparing", None)
            assert row["config_json"]["type"] == "cad_intent"
            assert _table_counts(runtime, job_id) == {"identity": 0, "artifacts": 0, "queued_events": 0}
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_run_numbers_have_no_gaps_whatever_became_of_the_solves_between(tmp_path: Path) -> None:
    """Refused, stopped, killed before the commit and blocked solves consume no run number."""

    async def scenario() -> None:
        runtime, ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            bound: list[str] = []

            async def bind(name: str) -> str:
                job_id = _preparing(runtime, name)
                assert await runtime._bind_cad_job(job_id, _request(ingest_id), _provenance(name)) == BOUND
                bound.append(job_id)
                return job_id

            await bind("cmd-a")
            # Refused after preparation.
            refused = _preparing(runtime, "cmd-refused")
            assert runtime.store.refuse_preparing_job(
                refused, code="findings_need_review", message="Review the findings."
            )
            await bind("cmd-b")
            # Stopped while preparing.
            stopped = _preparing(runtime, "cmd-stopped")
            await runtime.stop(stopped)
            # Killed before the commit: rolled back, and the number it would have taken is free.
            killed = _preparing(runtime, "cmd-killed")
            real_append = JobStore._append_event

            def die(self: JobStore, *_args: Any) -> Any:
                raise _Kill()

            JobStore._append_event = die  # type: ignore[method-assign]
            try:
                with pytest.raises(_Kill):
                    await runtime._bind_cad_job(killed, _request(ingest_id), _provenance("cmd-killed"))
            finally:
                JobStore._append_event = real_append  # type: ignore[method-assign]
            await bind("cmd-c")
            # And one more that is still waiting for a lane.
            waiting = runtime._preparing_record(_intent("cmd-waiting"))
            runtime.store.create_job(waiting)
            await bind("cmd-d")

            numbers = [runtime.store.get_job_row(job_id)["run_number"] for job_id in bound]
            assert numbers == [1, 2, 3, 4]
            for job_id in (refused, stopped, killed, waiting["id"]):
                assert runtime.store.get_job_row(job_id)["run_number"] is None
            # They are listed, and a page of the list says the same.
            listed, total = runtime.store.list_jobs(limit=50)
            assert total == 8
            assert sorted(row["run_number"] for row in listed if row["run_number"] is not None) == [1, 2, 3, 4]
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())

    # A restart's identity backfill never numbers what was never a run.
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.backfill_job_identity()
    listed, _total = store.list_jobs(limit=50)
    assert sorted(row["run_number"] for row in listed if row["run_number"] is not None) == [1, 2, 3, 4]
    assert sum(1 for row in listed if row["run_number"] is None) == 4
    store.close()


def test_a_job_that_is_still_being_prepared_or_refused_lists_streams_and_serializes_without_a_number(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, _ingest_id, _record = await _runtime_fixture(tmp_path)
        _quiet(runtime)
        try:
            job_id = _preparing(runtime, parent_job_id="job-parent")
            item = runtime._serialize_job(runtime.store.get_job_row(job_id))
            assert item["run_number"] is None
            assert item["parent_job_id"] == "job-parent"  # its lineage is in its intent
            assert item["status"] == "preparing"
            jobs, cursor = runtime.store.snapshot_jobs()
            assert [row["id"] for row in jobs] == [job_id] and cursor >= 1
            assert runtime.store.get_job_row(job_id)["run_number"] is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
