"""The ``preparing`` job status: schema, consumers and restart recovery.

``preparing`` is a CAD solve WG has accepted but not yet bound to a request.
Production accepts CAD intents in this status. These storage probes write rows
directly and check that they are read, listed, stopped and recovered safely, and
that no reader treats its intent as a ``SolveRequest``.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import shutil
import textwrap
from typing import Any

import pytest

from server.app import create_app
from server.jobs import store as store_module
from server.jobs.models import JobItem
from server.jobs.cad_intent import INTERRUPTED_MESSAGE as CAD_INTERRUPTED_MESSAGE
from server.jobs.runtime import JobConflictError, JobRuntime, RESTART_RECOVERY_MESSAGE
from server.jobs.store import (
    ACTIVE_STATUSES,
    ALLOWED_STATUSES,
    PREPARING_RECOVERY_MESSAGE,
    SUPPORTED_SCHEMA_VERSION,
    JobStore,
)

from _release_tags import CADLINK_ROLLBACK_TAGS
from test_cadlink_store_rollback import _released_server, _run_released
from test_jobs_api import _request

INTENT = {
    "type": "cad_intent",
    "operation_id": "op_1",
    "bundle_path": "wgreturn/speaker.wgreturn",
    "manifest_sha256": "sha256:" + "4" * 64,
    "return_id": "wgr_1",
}


def _job(job_id: str, status: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "id": job_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": status,
        "stage_message": status,
        "config_json": config if config is not None else {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {},
        "task_metadata": {},
    }


def _preparing(job_id: str = "prep-1") -> dict[str, Any]:
    return _job(job_id, "preparing", config=dict(INTENT))


def _held(job_id: str = "prep-1") -> dict[str, Any]:
    """A preparing job a lane was holding (``started_at`` set) when its process ended."""

    return {**_preparing(job_id), "started_at": datetime.now().isoformat()}


def _store(path: Path) -> JobStore:
    return JobStore(path / "db" / "simulations.db", job_logs_dir=path / "logs", field_traces_dir=path / "traces")


def _old_shaped_database(db_path: Path) -> None:
    """A database as the release before ``preparing`` wrote it."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        for statement in store_module._SCHEMA_STATEMENTS:
            conn.execute(statement.replace("'preparing',", ""))
        conn.execute("PRAGMA user_version = 5")
        conn.commit()


def _table_sql(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        return str(
            conn.execute("SELECT sql FROM sqlite_master WHERE name = 'simulation_jobs'").fetchone()[0]
        )


# --- the status is a member of every status set --------------------------------


def test_preparing_is_an_allowed_active_status_and_a_model_value() -> None:
    assert "preparing" in ALLOWED_STATUSES
    assert ACTIVE_STATUSES == {"preparing", "queued", "running"}
    assert "preparing" in JobItem.model_fields["status"].annotation.__args__
    assert JobRuntime.parse_status_filter("preparing,queued") == ["preparing", "queued"]
    with pytest.raises(ValueError, match="unsupported"):
        JobRuntime.parse_status_filter("preparing,nonsense")


# --- migration ------------------------------------------------------------------


def _fill_old_database(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for job_id, status in (("a", "complete"), ("b", "error"), ("c", "queued"), ("d", "cancelled")):
            now = "2026-09-01T10:00:00"
            conn.execute(
                "INSERT INTO simulation_jobs (id, status, created_at, updated_at, queued_at, "
                "config_json, config_summary_json, label, task_metadata_json, has_results) "
                "VALUES (?, ?, ?, ?, ?, '{}', '{}', ?, '{\"k\": 1}', 1)",
                (job_id, status, now, now, now, f"label {job_id}"),
            )
            conn.execute("INSERT INTO job_identity (job_id, parent_job_id) VALUES (?, NULL)", (job_id,))
            conn.execute("INSERT INTO simulation_results (job_id, results_json) VALUES (?, '{}')", (job_id,))
            conn.execute("INSERT INTO simulation_artifacts (job_id, msh_text) VALUES (?, 'mesh')", (job_id,))
            conn.execute(
                "INSERT INTO job_submissions (submission_key, request_sha256, job_id) VALUES (?, 'h', ?)",
                (f"key-{job_id}", job_id),
            )
        conn.execute(
            "INSERT INTO job_events (created_at, job_id, event_type, payload_json) "
            "VALUES ('2026-09-01T10:00:00', 'a', 'completed', '{}')"
        )
        conn.commit()


def _snapshot(db_path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "simulation_jobs",
                "job_identity",
                "simulation_results",
                "simulation_artifacts",
                "job_submissions",
                "job_events",
            )
        }


def test_an_old_database_upgrades_keeping_every_row_and_child(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    assert "'preparing'" not in _table_sql(db_path)
    before = _snapshot(db_path)

    store = _store(tmp_path)
    store.initialize()
    try:
        assert "'preparing'" in _table_sql(db_path)
        assert _snapshot(db_path) == before
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(simulation_jobs)")}
            assert {"idx_simulation_jobs_status_created", "idx_simulation_jobs_created"} <= indexes
        # Foreign keys are enforced again on the store's own connection, and a
        # cascade still reaches the children.
        assert store._connect().execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.create_job(_preparing()) is None
        assert store.get_job_row("prep-1")["status"] == "preparing"
        assert [row["id"] for row in store.list_jobs(statuses=["preparing"])[0]] == ["prep-1"]
        with store._transaction() as conn:
            conn.execute("DELETE FROM simulation_jobs WHERE id = 'a'")
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM simulation_results WHERE job_id = 'a'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM simulation_results WHERE job_id = 'b'").fetchone()[0] == 1
    finally:
        store.close()


def test_the_upgrade_runs_once_and_a_fresh_database_needs_none(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    for _ in range(2):
        store = _store(tmp_path)
        store.initialize()
        assert store._status_check_is_stale() is False
        store.close()
    fresh = _store(tmp_path / "fresh")
    fresh.initialize()
    try:
        assert fresh._status_check_is_stale() is False
        assert "'preparing'" in _table_sql(tmp_path / "fresh" / "db" / "simulations.db")
    finally:
        fresh.close()


def test_a_failed_rebuild_leaves_the_old_table_and_every_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    before = _snapshot(db_path)

    def broken(self: JobStore, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE simulation_jobs")
        raise RuntimeError("boom")

    monkeypatch.setattr(JobStore, "_rebuild_simulation_jobs", broken)
    store = _store(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        store.initialize()
    store.close()
    assert _snapshot(db_path) == before
    assert "'preparing'" not in _table_sql(db_path)


def test_a_rebuild_with_foreign_keys_enforced_is_refused_and_loses_no_child_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    before = _snapshot(db_path)
    # The pre-check and the in-transaction check disagreeing is what leaves
    # enforcement on while the table is dropped.
    monkeypatch.setattr(JobStore, "_status_check_is_stale", lambda self: False)
    store = _store(tmp_path)
    with pytest.raises(RuntimeError, match="foreign keys enforced"):
        store.initialize()
    store.close()
    assert _snapshot(db_path) == before
    assert "'preparing'" not in _table_sql(db_path)


def test_a_pre_existing_orphan_does_not_block_the_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO simulation_artifacts (job_id, msh_text) VALUES ('ghost', 'x')")
        conn.commit()
    before = _snapshot(db_path)
    store = _store(tmp_path)
    store.initialize()
    store.close()
    assert "'preparing'" in _table_sql(db_path)
    assert _snapshot(db_path) == before


def test_the_rebuild_replays_every_index_and_trigger_the_old_table_had(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE INDEX odd_name ON simulation_jobs(label)")
        conn.execute(
            "CREATE TRIGGER odd_trigger AFTER UPDATE ON simulation_jobs BEGIN SELECT 1; END"
        )
        conn.commit()
    store = _store(tmp_path)
    store.initialize()
    store.close()
    with closing(sqlite3.connect(db_path)) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name = 'simulation_jobs' "
                "AND type IN ('index', 'trigger')"
            )
        }
    assert {"odd_name", "odd_trigger", "idx_simulation_jobs_created"} <= names


def test_the_upgrade_raises_the_schema_version_with_a_snapshot(tmp_path: Path) -> None:
    """The older release opens the snapshot and clearly refuses the upgraded file."""

    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    store = _store(tmp_path)
    store.initialize()
    store.close()
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert SUPPORTED_SCHEMA_VERSION == 6
    assert store.rollback_snapshot_path.is_file()


_RELEASED_JOB_STORE_DRIVER = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, sys.argv[1])
    import server
    from server.jobs.store import JobStore

    store = JobStore(sys.argv[2])
    store.initialize()
    rows, total = store.list_jobs()
    row = store.get_job_row("a")
    store.close()
    print(json.dumps({
        "server": server.__file__,
        "total": total,
        "ids": sorted(item["id"] for item in rows),
        "label": row["label"] if row else None,
    }))
    """
)


@pytest.mark.parametrize("tag", CADLINK_ROLLBACK_TAGS)
def test_older_releases_refuse_intents_clearly_and_the_snapshot_restores(tmp_path: Path, tag: str) -> None:
    tree = _released_server(tag, tmp_path / f"release-{tag}")
    driver = tmp_path / "driver.py"
    driver.write_text(_RELEASED_JOB_STORE_DRIVER, encoding="utf-8")
    refusal_driver = tmp_path / "refusal.py"
    refusal_driver.write_text(textwrap.dedent("""\
        import json
        import sys
        sys.path.insert(0, sys.argv[1])
        import server
        from server.jobs.store import JobStore
        store = JobStore(sys.argv[2])
        try:
            store.initialize()
        except RuntimeError as exc:
            print(json.dumps({"refusal": str(exc), "server": server.__file__}))
        else:
            raise AssertionError("Older release opened schema 6")
        finally:
            store.close()
        """), encoding="utf-8")
    db_path = tmp_path / "db" / "simulations.db"
    _old_shaped_database(db_path)
    _fill_old_database(db_path)
    before = _snapshot(db_path)
    store = _store(tmp_path)
    store.initialize()
    snapshot = store.rollback_snapshot_path
    assert _snapshot(snapshot) == before
    store.create_job(_preparing())
    store.create_job(_job("refused-intent", "error", config=dict(INTENT)))
    assert store.get_job_row("refused-intent")["run_number"] is None
    store.checkpoint()
    store.close()
    upgraded = _snapshot(db_path)
    result = _run_released(tree, refusal_driver, str(db_path))
    assert "created by a newer version" in result["refusal"]
    assert "schema 6" in result["refusal"] and "supports schemas up to 5" in result["refusal"]
    assert "last opened by" in result["refusal"]
    assert _snapshot(db_path) == upgraded
    # With every connection closed, replace the main file and discard the newer
    # WAL/SHM. The pre-upgrade snapshot is standalone rollback material.
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    shutil.copy2(snapshot, db_path)
    result = _run_released(tree, driver, str(db_path))
    assert result["ids"] == ["a", "b", "c", "d"]
    assert result["label"] == "label a"
    assert _snapshot(db_path) == before


# --- every store consumer accepts the status --------------------------------------


def test_store_reads_and_writes_accept_preparing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.initialize()
    try:
        store.create_job(_job("q", "queued"))
        store.create_job(_preparing())
        assert store.update_job("q", status="preparing") is True
        assert store.get_job_row("q")["status"] == "preparing"
        rows, total = store.list_jobs(statuses=["preparing"])
        assert total == 2 and {row["id"] for row in rows} == {"q", "prep-1"}
        with pytest.raises(ValueError, match="Unsupported status"):
            store.list_jobs(statuses=["nonsense"])
        assert store.persist_runtime_update(
            "prep-1", {"progress": 0.4, "stage": "preparing-mesh"}, stage_payload={"stage": "preparing-mesh"}
        )
        assert store.get_job_row("prep-1")["stage"] == "preparing-mesh"
        # Retention and clearing never touch it.
        assert store.prune_terminal_jobs() == 0
        assert store.delete_jobs_by_status_with_events(["error"]) == ([], [])
        assert store.get_job_row("prep-1") is not None
        # It has no imported provenance yet, so it owes no archive copy.
        assert store.unreleased_cad_return_states() == []
    finally:
        store.close()


# --- restart recovery -----------------------------------------------------------


def test_a_preparing_row_left_at_startup_ends_as_a_named_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.initialize()
    try:
        store.create_job(_held("prep-1"))
        store.create_job(_preparing("prep-waiting"))
        store.create_job(_job("queued-1", "queued"))
        store.create_job(_job("running-1", "running"))
        store.create_job(_job("done-1", "complete"))

        queued, events = store.recover_on_startup(RESTART_RECOVERY_MESSAGE)

        prepared = store.get_job_row("prep-1")
        assert prepared["status"] == "error"
        assert prepared["stage"] == "error"
        assert prepared["error_message"] == PREPARING_RECOVERY_MESSAGE
        assert "restarted" in PREPARING_RECOVERY_MESSAGE and "Solve again" in PREPARING_RECOVERY_MESSAGE
        assert prepared["completed_at"]
        assert prepared["cancellation_requested"] in (0, False)
        # Ended as a refusal the operations know: ``interrupted``.
        assert prepared["task_metadata"]["cad"]["refusal"] == {
            "code": "interrupted", "message": PREPARING_RECOVERY_MESSAGE,
        }
        # A job no lane was holding is not ended: the lane takes it up.
        assert store.get_job_row("prep-waiting")["status"] == "preparing"
        assert store.unheld_preparing_job_ids() == ["prep-waiting"]
        by_job = {event["job_id"] if "job_id" in event else event.get("jobId"): event for event in events}
        assert by_job["prep-1"]["type"] == "failed"
        assert by_job["prep-1"]["payload"]["recovered"] is True
        assert by_job["prep-1"]["payload"]["message"] == PREPARING_RECOVERY_MESSAGE
        assert "prep-waiting" not in by_job
        # Nothing else changed: the running row reads as before, queued stays.
        assert store.get_job_row("running-1")["error_message"] == RESTART_RECOVERY_MESSAGE
        assert [row["id"] for row in queued] == ["queued-1"]
        assert store.get_job_row("done-1")["status"] == "complete"
        # And it is now an ordinary failed row: clearable.
        ids, _events = store.delete_jobs_by_status_with_events(["error"])
        assert "prep-1" in ids
    finally:
        store.close()


def test_the_runtime_start_settles_a_preparing_row_a_lane_held(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str | None]:
        seed = _store(tmp_path)
        seed.initialize()
        seed.create_job(_held())
        seed.close()
        runtime = JobRuntime(_store(tmp_path))
        try:
            job = await runtime.get_job("prep-1")
        finally:
            await runtime.shutdown()
        return job["status"], job["error_message"]

    status, message = asyncio.run(scenario())
    assert status == "error"
    # The words the operations' own start-up recovery uses for the same thing.
    assert message == CAD_INTERRUPTED_MESSAGE


# --- runtime and HTTP consumers ---------------------------------------------------


def test_a_preparing_row_lists_serializes_stops_and_deletes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        runtime: JobRuntime = app.state.jobs_runtime
        await runtime.start()
        try:
            runtime.store.create_job(_preparing("prep-1"))
            runtime.store.create_job(_preparing("prep-2"))

            status, raw = await _request(app, "GET", "/api/jobs", query="status=preparing")
            assert status == 200
            listed = json.loads(raw)
            assert listed["total"] == 2
            item = next(row for row in listed["items"] if row["id"] == "prep-1")
            assert item["status"] == "preparing"
            assert item["cad_intent"] == INTENT
            assert item["design_availability"]["reopenable"] is False
            assert item["design_availability"]["reason_code"] == "no_stored_design"
            assert item["has_results"] is False
            assert item["solve_options"]["engine"]

            status, raw = await _request(app, "GET", "/api/status/prep-1")
            assert status == 200
            assert json.loads(raw)["status"] == "preparing"

            # No request exists, so nothing may replay one.
            status, raw = await _request(app, "POST", "/api/jobs/prep-1/retry")
            assert status == 409 and b"still being prepared" in raw
            with pytest.raises(JobConflictError):
                await runtime.get_effective_request("prep-1")
            with pytest.raises(JobConflictError):
                await runtime.get_execution_request("prep-1")
            for path in ("/api/results/prep-1", "/api/jobs/prep-1/archive-snapshot"):
                status, _raw = await _request(app, "GET", path)
                assert status in {400, 404, 409}, path

            # Active: it is stopped before it is deleted, and clear-failed skips it.
            status, _raw = await _request(app, "DELETE", "/api/jobs/prep-1")
            assert status == 409
            status, raw = await _request(app, "DELETE", "/api/jobs/clear-failed")
            assert runtime.store.get_job_row("prep-1")["status"] == "preparing"
            status, raw = await _request(app, "POST", "/api/stop/prep-1")
            assert status == 200 and json.loads(raw)["status"] == "cancelled"
            assert runtime.store.get_job_row("prep-1")["status"] == "cancelled"
            status, _raw = await _request(app, "DELETE", "/api/jobs/prep-1")
            assert status == 200
            assert runtime.store.get_job_row("prep-1") is None
            assert runtime.store.get_job_row("prep-2")["status"] == "preparing"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_a_preparing_row_streams_in_the_jobs_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = JobRuntime(_store(tmp_path))
        await runtime.start()
        try:
            runtime.store.create_job(_preparing())
            items, total = await runtime.list_jobs(status=None, limit=50, offset=0)
            assert total == 1
            assert json.loads(json.dumps(items))[0]["status"] == "preparing"
            assert JobItem.model_validate(items[0]).status == "preparing"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_a_failed_snapshot_aborts_the_upgrade_without_changing_the_database(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _old_shaped_database(store.db_path)
    _fill_old_database(store.db_path)
    before = _snapshot(store.db_path)
    def fail_publish(*args):
        raise OSError("snapshot publish failed")
    with monkeypatch.context() as patch:
        patch.setattr(store_module.os, "replace", fail_publish)
        with pytest.raises(OSError, match="snapshot publish failed"):
            store.initialize()
    assert _snapshot(store.db_path) == before
    with closing(sqlite3.connect(store.db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert not store.rollback_snapshot_path.exists()
    store.initialize()
    assert _snapshot(store.rollback_snapshot_path) == before
    store.close()


def test_an_invalid_existing_snapshot_aborts_the_upgrade(tmp_path):
    store = _store(tmp_path)
    _old_shaped_database(store.db_path)
    _fill_old_database(store.db_path)
    before = _snapshot(store.db_path)
    store.rollback_snapshot_path.touch()
    with pytest.raises(RuntimeError, match="not restorable"):
        store.initialize()
    assert _snapshot(store.db_path) == before
    store.close()
