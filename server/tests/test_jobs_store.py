from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
import threading

import pytest

from server.jobs.store import JobStore


def _job(job_id: str, status: str = "queued", *, created_at: str | None = None) -> dict:
    now = created_at or datetime.now().isoformat()
    return {
        "id": job_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": status,
        "stage_message": status,
        "config_json": {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


EXPECTED_JOB_COLUMNS = [
    "id",
    "status",
    "created_at",
    "updated_at",
    "queued_at",
    "started_at",
    "completed_at",
    "progress",
    "stage",
    "stage_message",
    "error_message",
    "cancellation_requested",
    "config_json",
    "config_summary_json",
    "has_results",
    "has_mesh_artifact",
    "mesh_stats_json",
    "label",
    "script_snapshot_json",
    "task_metadata_json",
]


def test_v1_schema_columns_are_exact_and_live_under_wg2_data_dir(tmp_path: Path) -> None:
    store = JobStore.for_data_dir(tmp_path)
    store.initialize()
    assert store.db_path == tmp_path / "db" / "simulations.db"
    with sqlite3.connect(store.db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(simulation_jobs)")]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert columns == EXPECTED_JOB_COLUMNS
    assert {
        "simulation_jobs",
        "simulation_results",
        "simulation_artifacts",
        "job_identity",
    } <= tables
    assert version == 4


def test_newer_database_schema_is_refused_without_downgrading(tmp_path: Path) -> None:
    database = tmp_path / "newer.db"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA user_version = 5")

    store = JobStore(database)
    with pytest.raises(RuntimeError, match="created by a newer version.*schema 5"):
        store.initialize()
    store.close()

    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0] == 0


def test_created_jobs_get_consecutive_run_numbers(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("first"))
    store.create_job(_job("second"))

    assert store.get_job_row("first")["run_number"] == 1
    assert store.get_job_row("second")["run_number"] == 2


def test_radiation_impedance_artifact_round_trip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("cardioid"))

    store.store_radiation_impedance("cardioid", b"matrix-npz")

    assert store.get_radiation_impedance("cardioid") == b"matrix-npz"
    metadata = store.get_job_row("cardioid")["task_metadata"]
    assert metadata["has_radiation_impedance_artifact"] is True
    assert metadata["radiation_impedance_artifact_bytes"] == len(b"matrix-npz")

    assert store.delete_radiation_impedance("cardioid") is True
    assert store.get_radiation_impedance("cardioid") is None
    metadata = store.get_job_row("cardioid")["task_metadata"]
    assert metadata["has_radiation_impedance_artifact"] is False
    assert metadata["radiation_impedance_artifact_bytes"] is None


def test_retention_prunes_standalone_radiation_impedance_rows(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("orphan", "running"))
    store.store_radiation_impedance("orphan", b"matrix-npz")
    store.update_job(
        "orphan",
        status="error",
        completed_at=datetime.now().isoformat(),
    )

    assert store.prune_terminal_jobs(retention_days=30) == 0
    assert store.get_radiation_impedance("orphan") is None
    metadata = store.get_job_row("orphan")["task_metadata"]
    assert metadata["has_radiation_impedance_artifact"] is False
    assert metadata["radiation_impedance_artifact_bytes"] is None


def test_startup_recovery_removes_running_radiation_impedance(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("interrupted", "running"))
    store.store_radiation_impedance("interrupted", b"matrix-npz")

    store.recover_on_startup("restart")

    assert store.get_job_row("interrupted")["status"] == "error"
    assert store.get_radiation_impedance("interrupted") is None
    metadata = store.get_job_row("interrupted")["task_metadata"]
    assert metadata["has_radiation_impedance_artifact"] is False
    assert metadata["radiation_impedance_artifact_bytes"] is None


def test_run_numbers_are_not_reused_after_deleting_the_newest_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    for job_id in ("one", "two", "three"):
        store.create_job(_job(job_id))

    deleted, _event = store.delete_job_with_event("three")
    assert deleted is True
    store.create_job(_job("four"))

    assert store.get_job_row("four")["run_number"] == 4


def test_pruning_keeps_identity_and_lineage_tombstones(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    child = _job("child", "complete", created_at="2000-01-01T00:00:00")
    child["parent_job_id"] = "parent"
    child["completed_at"] = "2000-01-01T00:00:00"
    store.create_job(child)
    store.store_results("child", {"frequencies": [1000.0]})

    assert store.prune_terminal_jobs() == 1
    row = store.get_job_row("child")
    assert row is not None
    assert row["run_number"] == 1
    assert row["parent_job_id"] == "parent"
    assert row["has_results"] is False
    assert store.get_results("child") is None
    identity = store._connect().execute(
        "SELECT run_number, parent_job_id FROM job_identity WHERE job_id = ?",
        ("child",),
    ).fetchone()
    assert tuple(identity) == (1, "parent")


def test_identity_backfill_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    seeded = [
        ("late", "2026-01-02T00:00:00"),
        ("tie-b", "2026-01-01T00:00:00"),
        ("early", "2025-12-31T00:00:00"),
        ("tie-a", "2026-01-01T00:00:00"),
    ]
    with store._transaction() as conn:
        for job_id, created_at in seeded:
            job = _job(job_id, created_at=created_at)
            conn.execute(
                """INSERT INTO simulation_jobs
                   (id, status, created_at, updated_at, queued_at, progress,
                    stage, stage_message, cancellation_requested, config_json,
                    config_summary_json, has_results, has_mesh_artifact,
                    task_metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job["id"], job["status"], job["created_at"], job["updated_at"],
                    job["queued_at"], job["progress"], job["stage"],
                    job["stage_message"], 0, json.dumps(job["config_json"]),
                    json.dumps(job["config_summary_json"]), 0, 0, "{}",
                ),
            )

    store.backfill_job_identity()
    first = [
        tuple(row)
        for row in store._connect().execute(
            "SELECT job_id, run_number FROM job_identity ORDER BY run_number"
        )
    ]
    store.backfill_job_identity()
    second = [
        tuple(row)
        for row in store._connect().execute(
            "SELECT job_id, run_number FROM job_identity ORDER BY run_number"
        )
    ]

    assert first == second == [
        ("early", 1),
        ("tie-a", 2),
        ("tie-b", 3),
        ("late", 4),
    ]


def test_failed_create_rolls_back_its_identity_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()

    def fail_event(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected event failure")

    monkeypatch.setattr(store, "_append_event", fail_event)
    with pytest.raises(sqlite3.OperationalError, match="injected event failure"):
        store.create_job(_job("rolled-back"), initial_event=("queued", {}))

    conn = store._connect()
    assert conn.execute("SELECT COUNT(*) FROM simulation_jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job_identity").fetchone()[0] == 0


def test_concurrent_allocations_are_unique_and_gapless(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    workers = 8
    barrier = threading.Barrier(workers)

    def create(index: int) -> None:
        barrier.wait()
        store.create_job(_job(f"concurrent-{index}"))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(create, range(workers)))

    numbers = sorted(row["run_number"] for row in store.list_jobs(limit=workers)[0])
    assert numbers == list(range(1, workers + 1))


def test_run_number_stabilizes_list_pagination_and_snapshots(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    created_at = "2026-01-01T00:00:00"
    for job_id in ("one", "two", "three"):
        store.create_job(_job(job_id, created_at=created_at))

    page, total = store.list_jobs(limit=2, offset=1)
    snapshot, _cursor = store.snapshot_jobs()

    assert total == 3
    assert [row["run_number"] for row in page] == [2, 1]
    assert [row["run_number"] for row in snapshot] == [3, 2, 1]


def test_the_database_runs_in_wal_mode_with_a_busy_timeout(tmp_path: Path) -> None:
    """WAL and connection reuse are only fast together; both must be in place.

    Measured on Windows over 150 checkpoint-shaped transactions: 8.44 ms each
    with the rollback journal and a fresh connection per call, 10.51 ms with
    WAL but still reopening, 0.05 ms with WAL on a reused connection. A solve
    writes one of these every 150 ms.
    """

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    try:
        conn = store._connect()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        # Reused, not reopened: the measured win depends on it.
        assert store._connect() is conn
    finally:
        store.close()


def test_closing_the_store_releases_the_file_for_windows(tmp_path: Path) -> None:
    """An open handle stops Windows deleting or replacing the database.

    That is not hypothetical here: the v1 migration tool's rollback replaces
    this exact file, and every test fixture removes the directory holding it.
    """

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("held"))
    store.close()
    store.db_path.unlink()
    assert not store.db_path.exists()


def test_a_snapshot_leaves_no_transaction_open(tmp_path: Path) -> None:
    """A long-lived connection makes an unfinished read transaction permanent.

    It would pin the WAL so it could never be checkpointed, and the file would
    grow without bound.
    """

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("snap"))
    try:
        store.snapshot_jobs()
        assert not store._connect().in_transaction
        # A checkpoint is what a pinned WAL would block.
        store.checkpoint()
    finally:
        store.close()


def test_cancellation_state_reads_only_what_a_checkpoint_needs(tmp_path: Path) -> None:
    """The solver asks this once per frequency, from its own thread.

    ``get_job_row`` answers the same question by selecting every column and
    parsing four JSON blobs, two of which hold a whole copy of the design.
    """

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("cancel-me", "running"))
    try:
        assert store.cancellation_state("cancel-me") == ("running", False)
        assert store.cancellation_state("absent") is None
        store.request_cancellation(
            "cancel-me",
            {"stage": "cancelling", "cancellation_requested": True},
            {"stage": "cancelling"},
        )
        assert store.cancellation_state("cancel-me") == ("running", True)
    finally:
        store.close()


def test_results_text_is_the_stored_bytes_and_still_parses(tmp_path: Path) -> None:
    """The HTTP route hands these straight to the browser; parsing is waste."""

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("textual"))
    store.store_results("textual", {"frequencies": [100.0], "spl": [95.0]})
    try:
        text = store.get_results_text("textual")
        assert isinstance(text, str)
        assert json.loads(text) == store.get_results("textual")
        assert store.get_results_text("absent") is None
    finally:
        store.close()


def test_results_store_rejects_non_json_nan_instead_of_serving_invalid_json(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("nonfinite"))
    try:
        with pytest.raises(ValueError, match="JSON compliant"):
            store.store_results(
                "nonfinite", {"frequencies": [100.0], "spl": [float("nan")]}
            )
        assert store.get_results_text("nonfinite") is None
    finally:
        store.close()


def test_the_redundant_event_index_is_dropped_on_existing_installs(tmp_path: Path) -> None:
    """job_events.id is a rowid alias, so indexing it duplicated the table.

    The planner never used it and every insert and retention delete paid for
    it, so initialize() removes it rather than merely stopping creating it.
    """

    database = tmp_path / "jobs.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """CREATE TABLE job_events (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 created_at TEXT NOT NULL,
                 job_id TEXT NOT NULL,
                 event_type TEXT NOT NULL,
                 payload_json TEXT NOT NULL)"""
        )
        conn.execute("CREATE INDEX idx_job_events_id ON job_events(id)")

    store = JobStore(database)
    store.initialize()
    try:
        indexes = {
            row["name"]
            for row in store._connect().execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        store.close()
    assert "idx_job_events_id" not in indexes
    assert "idx_simulation_jobs_created" in indexes


def test_store_round_trips_jobs_results_artifacts_and_metadata(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("roundtrip")
    record.update(
        {
            "mesh_stats": {"triangle_count": 12},
            "script_snapshot": {"formula": "OSSE"},
            "task_metadata": {"rating": 4, "exported_files": ["one.csv"]},
        }
    )
    first = store.create_job(record, initial_event=("queued", {"progress": 0.0}))
    assert first and first["cursor"] == 1
    store.store_results("roundtrip", {"frequencies": [100.0], "spl": [95.0]})
    store.store_mesh_artifact("roundtrip", "$MeshFormat\n2.2 0 8\n")
    row = store.get_job_row("roundtrip")
    assert row is not None
    assert row["mesh_stats"] == {"triangle_count": 12}
    assert row["script_snapshot"] == {"formula": "OSSE"}
    assert row["task_metadata"]["rating"] == 4
    assert row["has_results"] and row["has_mesh_artifact"]
    assert store.get_results("roundtrip")["frequencies"] == [100.0]
    assert store.get_mesh_artifact("roundtrip").startswith("$MeshFormat")


def test_exported_files_metadata_is_an_ordered_set_union(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("exports")
    record["task_metadata"] = {"exported_files": ["a.step"]}
    store.create_job(record)

    changed, event = store.mutate_job_metadata(
        "exports", {"exported_files": ["b.step"]}
    )
    assert changed is True
    assert event is None
    assert store.get_job_row("exports")["task_metadata"]["exported_files"] == [
        "a.step",
        "b.step",
    ]

    store.mutate_job_metadata("exports", {"exported_files": ["a.step"]})
    store.mutate_job_metadata("exports", {"exported_files": None})
    assert store.get_job_row("exports")["task_metadata"]["exported_files"] == [
        "a.step",
        "b.step",
    ]


def test_auto_export_formats_metadata_is_a_shallow_dict_merge(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("formats")
    record["task_metadata"] = {
        "auto_export_formats": {"step": {"file": "a.step", "status": "done"}}
    }
    store.create_job(record)

    store.mutate_job_metadata(
        "formats",
        {"auto_export_formats": {"stl": {"file": "a.stl", "status": "done"}}},
    )
    store.mutate_job_metadata("formats", {"auto_export_formats": None})

    assert store.get_job_row("formats")["task_metadata"]["auto_export_formats"] == {
        "step": {"file": "a.step", "status": "done"},
        "stl": {"file": "a.stl", "status": "done"},
    }


def test_non_collection_metadata_keys_keep_replacement_semantics(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("replacement")
    record["task_metadata"] = {
        "rating": 2,
        "mesh_artifact_file": "old.msh",
    }
    store.create_job(record)

    store.mutate_job_metadata(
        "replacement",
        {"rating": 5, "mesh_artifact_file": "new.msh"},
    )

    metadata = store.get_job_row("replacement")["task_metadata"]
    assert metadata["rating"] == 5
    assert metadata["mesh_artifact_file"] == "new.msh"


def test_metadata_mutation_does_not_refresh_terminal_result_retention_age(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    old = "2000-01-01T00:00:00"
    record = _job("imported", "complete", created_at=old)
    record["task_metadata"] = {"imported_at": old}
    store.create_job(record)
    store.store_results("imported", {"job": "imported"})

    changed, _event = store.mutate_job_metadata(
        "imported",
        {"raw_results_file": "imported.json"},
    )

    assert changed is True
    assert store.get_job_row("imported")["updated_at"] > old
    assert store.prune_terminal_jobs(retention_days=30) == 1
    assert store.get_results("imported") is None


def test_metadata_mutation_returns_cleanly_when_job_is_missing(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()

    changed, event = store.mutate_job_metadata(
        "missing",
        {"rating": 5},
        column_fields={"label": "Reference"},
        event_type="metadata",
        payload={"changed": {"rating": 5, "label": "Reference"}},
    )

    assert changed is False
    assert event is None
    assert store.current_event_cursor() == 0
    assert store.list_jobs()[1] == 0


def test_metadata_event_reports_the_value_stored_after_a_policy_merge(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("event")
    record["task_metadata"] = {"exported_files": ["a.step"]}
    store.create_job(record)

    changed, event = store.mutate_job_metadata(
        "event",
        {"exported_files": ["b.step"], "rating": 4},
        column_fields={"label": "Reference"},
        event_type="metadata",
        payload={
            "changed": {
                "exported_files": ["b.step"],
                "rating": 4,
                "label": "Reference",
            }
        },
    )

    assert changed is True
    assert event is not None
    assert event["payload"] == {
        "changed": {
            "exported_files": ["a.step", "b.step"],
            "rating": 4,
            "label": "Reference",
        }
    }
    row = store.get_job_row("event")
    assert row["label"] == "Reference"
    assert row["task_metadata"]["exported_files"] == ["a.step", "b.step"]
    assert store.replay_events(0) == [event]


def test_coalesced_runtime_checkpoint_updates_state_log_tail_and_events(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("runtime", "running")
    record["task_metadata"] = {"rating": 4, "log_tail": ["existing"]}
    store.create_job(record)

    changed, events = store.persist_runtime_update(
        "runtime",
        {
            "stage": "solve",
            "stage_message": "frequency 3",
            "progress": 0.6,
        },
        stage_payload={"stage": "solve", "message": "frequency 3", "progress": 0.6},
        log_lines=("frequency 1", "frequency 2", "frequency 3"),
    )

    assert changed is True
    assert [event["type"] for event in events] == ["stage", "log"]
    assert events[1]["payload"] == {
        "chunk": "frequency 3",
        "lines": ["frequency 1", "frequency 2", "frequency 3"],
    }
    row = store.get_job_row("runtime")
    assert row["stage"] == "solve"
    assert row["progress"] == 0.6
    assert row["task_metadata"]["rating"] == 4
    assert row["task_metadata"]["log_tail"] == [
        "existing",
        "frequency 1",
        "frequency 2",
        "frequency 3",
    ]
    assert store.get_job_log("runtime") == (
        "frequency 1\nfrequency 2\nfrequency 3\n"
    )


def test_cancel_requested_checkpoint_keeps_logs_without_restoring_stale_stage(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("cancelling", "running")
    record.update(
        {
            "stage": "cancelling",
            "stage_message": "Cancellation requested",
            "cancellation_requested": True,
        }
    )
    store.create_job(record)

    changed, events = store.persist_runtime_update(
        "cancelling",
        {
            "stage": "solve",
            "stage_message": "late frequency",
            "progress": 0.75,
        },
        stage_payload={
            "stage": "solve",
            "message": "late frequency",
            "progress": 0.75,
        },
        log_lines=("late frequency",),
        expected_log_size=0,
    )

    assert changed is True
    assert [event["type"] for event in events] == ["log"]
    row = store.get_job_row("cancelling")
    assert row["stage"] == "cancelling"
    assert row["stage_message"] == "Cancellation requested"
    assert row["progress"] == 0.0
    assert row["task_metadata"]["log_tail"] == ["late frequency"]
    assert store.get_job_log("cancelling") == "late frequency\n"


def test_runtime_log_delta_preserves_lines_inside_multiline_messages(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("multiline", "running"))

    changed, events = store.persist_runtime_update(
        "multiline",
        {},
        log_lines=("first\nsecond", "third"),
        expected_log_size=0,
    )

    assert changed is True
    assert events[0]["payload"] == {
        "chunk": "third",
        "lines": ["first", "second", "third"],
    }
    assert store.get_job_row("multiline")["task_metadata"]["log_tail"] == [
        "first",
        "second",
        "third",
    ]
    assert store.get_job_log("multiline") == "first\nsecond\nthird\n"


def test_runtime_log_append_retry_is_idempotent_after_sql_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("retry", "running"))
    original_update = store._update_job

    def fail_update(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("simulated commit path failure")

    monkeypatch.setattr(store, "_update_job", fail_update)
    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        store.persist_runtime_update(
            "retry",
            {},
            log_lines=("only once",),
            expected_log_size=0,
        )
    assert store.get_job_log("retry") == "only once\n"
    assert store.get_job_row("retry")["task_metadata"] == {}

    monkeypatch.setattr(store, "_update_job", original_update)
    changed, events = store.persist_runtime_update(
        "retry",
        {},
        log_lines=("only once",),
        expected_log_size=0,
    )
    assert changed is True
    assert [event["type"] for event in events] == ["log"]
    assert store.get_job_log("retry") == "only once\n"
    assert store.get_job_row("retry")["task_metadata"]["log_tail"] == [
        "only once"
    ]


def test_runtime_log_mismatch_warns_resynchronizes_and_keeps_progressing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("mismatch", "running"))
    log_path = store._job_log_path("mismatch")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("external\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="server.jobs.store"):
        changed, events = store.persist_runtime_update(
            "mismatch",
            {},
            log_lines=("writer",),
            expected_log_size=0,
        )

    assert changed is True
    assert [event["type"] for event in events] == ["log"]
    assert "Job log integrity mismatch" in caplog.text
    assert store.get_job_log("mismatch") == "external\nwriter\n"
    assert store.get_job_row("mismatch")["task_metadata"]["log_tail"] == [
        "writer"
    ]

    expected = store.job_log_size("mismatch")
    changed, events = store.persist_runtime_update(
        "mismatch",
        {},
        log_lines=("after-resync",),
        expected_log_size=expected,
    )
    assert changed is True
    assert [event["type"] for event in events] == ["log"]
    assert store.get_job_log("mismatch") == "external\nwriter\nafter-resync\n"


def test_transactions_rollback_a_duplicate_create_without_extra_event(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("same"), initial_event=("queued", {}))
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job(_job("same"), initial_event=("queued", {}))
    rows, total = store.list_jobs()
    assert total == 1
    assert rows[0]["id"] == "same"
    assert store.current_event_cursor() == 1


def test_event_ids_are_persisted_monotonic_and_resume_or_snapshot(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db", event_retention=3)
    store.initialize()
    store.create_job(_job("cursor"), initial_event=("queued", {}))
    cursors = [1]
    for progress in (0.1, 0.2, 0.3, 0.4):
        _, event = store.update_job_with_event(
            "cursor", {"progress": progress}, "progress", {"progress": progress}
        )
        cursors.append(event["cursor"])
    assert cursors == sorted(set(cursors)) == [1, 2, 3, 4, 5]
    assert store.current_event_cursor() == 5
    assert store.replay_events(1) is None
    replay = store.replay_events(3)
    assert [event["cursor"] for event in replay] == [4, 5]

    reopened = JobStore(tmp_path / "jobs.db", event_retention=3)
    reopened.initialize()
    assert reopened.current_event_cursor() == 5


def test_live_retention_prune_persists_an_availability_event_for_every_pruned_result(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    for job_id in ("oldest", "newest"):
        store.create_job(_job(job_id), initial_event=("queued", {}))
        store.update_job(
            job_id, status="complete", completed_at="2000-01-01T00:00:00"
        )
        store.store_results(job_id, {"frequencies": [1000.0]})

    removed, events = store.prune_terminal_jobs_with_events(
        retention_days=30, max_terminal_jobs=1000
    )

    assert set(removed) == {"oldest", "newest"}
    assert [event["jobId"] for event in events] == removed
    assert [event["type"] for event in events] == ["metadata", "metadata"]
    assert all(
        event["payload"]
        == {"changed": {"has_results": False}, "reason": "retention"}
        for event in events
    )
    assert store.list_jobs()[1] == 2
    assert all(not store.get_job_row(job_id)["has_results"] for job_id in removed)
    assert all(
        store.get_job_row(job_id)["task_metadata"].get("results_discarded_at")
        for job_id in removed
    )
    assert [event["jobId"] for event in store.replay_events(2)] == removed


def test_result_age_pruning_deletes_only_logs_in_the_pruned_result_tier(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    old = "2000-01-01T00:00:00"
    cases = (
        ("aged", "complete", 0, old, True),
        ("running", "running", 0, old, True),
        ("queued", "queued", 0, old, True),
        ("rated", "complete", 4, old, True),
        ("surviving", "complete", 0, datetime.now().isoformat(), True),
    )
    for job_id, status, rating, completed_at, has_results in cases:
        record = _job(job_id, status, created_at=completed_at)
        record["completed_at"] = completed_at if status == "complete" else None
        record["task_metadata"] = {"rating": rating}
        store.create_job(record)
        if has_results:
            store.store_results(job_id, {"job": job_id})
        path = store._job_log_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"log-{job_id}\n", encoding="utf-8")

    assert store.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000) == 1

    assert not store._job_log_path("aged").exists()
    for job_id in ("running", "queued", "rated", "surviving"):
        assert store._job_log_path(job_id).read_text(encoding="utf-8") == f"log-{job_id}\n"


def test_retention_cleans_previously_orphaned_logs_but_not_unrelated_files(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    cases = (
        ("already-pruned", "complete", 0),
        ("running-pruned", "running", 0),
        ("queued-pruned", "queued", 0),
        ("rated-pruned", "complete", 5),
    )
    for job_id, status, rating in cases:
        record = _job(job_id, status, created_at="2000-01-01T00:00:00")
        record["task_metadata"] = {
            "rating": rating,
            "results_discarded_at": "2001-01-01T00:00:00",
        }
        store.create_job(record)
        path = store._job_log_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"log-{job_id}\n", encoding="utf-8")
    unrelated = store.job_logs_dir / "_not-a-job.log"
    unrelated.write_text("leave me\n", encoding="utf-8")

    assert store.prune_terminal_jobs() == 0

    assert not store._job_log_path("already-pruned").exists()
    for job_id in ("running-pruned", "queued-pruned", "rated-pruned"):
        assert store._job_log_path(job_id).read_text(encoding="utf-8") == f"log-{job_id}\n"
    assert unrelated.read_text(encoding="utf-8") == "leave me\n"


def test_terminal_mesh_is_pruned_after_download_or_grace_but_rated_mesh_is_exempt(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()

    now = datetime.now().isoformat()
    cases = (
        ("downloaded", 0, now, "downloaded.msh"),
        ("expired", 0, "2000-01-01T00:00:00", None),
        ("rated", 3, "2000-01-01T00:00:00", "rated.msh"),
    )
    run_numbers: dict[str, int] = {}
    for job_id, rating, completed_at, mesh_file in cases:
        record = _job(job_id, "running", created_at=completed_at)
        record["task_metadata"] = {"rating": rating}
        store.create_job(record)
        store.store_mesh_artifact(job_id, f"mesh-{job_id}")
        store.store_results(job_id, {"job": job_id})
        log_path = store._job_log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"log-{job_id}\n", encoding="utf-8")
        store.update_job(
            job_id,
            status="complete",
            completed_at=completed_at,
        )
        if mesh_file is not None:
            store.mutate_job_metadata(job_id, {"mesh_artifact_file": mesh_file})
        row = store.get_job_row(job_id)
        run_numbers[job_id] = row["run_number"]
        assert row["has_mesh_artifact"] is True
        assert store.get_mesh_artifact(job_id) == f"mesh-{job_id}"

    assert (
        store.prune_terminal_jobs(
            retention_days=99999,
            max_terminal_jobs=1000,
            mesh_grace_minutes=1,
        )
        == 0
    )

    for job_id in ("downloaded", "expired"):
        row = store.get_job_row(job_id)
        assert row["has_mesh_artifact"] is False
        assert row["task_metadata"].get("mesh_discarded_at")
        assert store.get_mesh_artifact(job_id) is None
        assert row["run_number"] == run_numbers[job_id]
        assert row["has_results"] is True
        assert store._job_log_path(job_id).read_text(encoding="utf-8") == f"log-{job_id}\n"
    assert store.get_job_row("rated")["has_mesh_artifact"] is True
    assert store.get_mesh_artifact("rated") == "mesh-rated"
    assert store.get_job_row("rated")["run_number"] == run_numbers["rated"]
    assert store.get_results("rated") == {"job": "rated"}
    assert store.get_job_row("rated")["has_results"] is True
    assert store._job_log_path("rated").read_text(encoding="utf-8") == "log-rated\n"


def test_result_count_pruning_keeps_job_rows_and_run_numbers(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    for job_id, completed_at in (
        ("older", "2026-08-08T00:00:00"),
        ("newer", "2026-08-09T00:00:00"),
    ):
        record = _job(job_id, "complete", created_at=completed_at)
        record["completed_at"] = completed_at
        store.create_job(record)
        store.store_results(job_id, {"job": job_id})
        log_path = store._job_log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"log-{job_id}\n", encoding="utf-8")

    assert store.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1) == 1
    assert store.get_results("older") is None
    assert store.get_results("newer") == {"job": "newer"}
    assert store.get_job_row("older")["run_number"] == 1
    assert store.get_job_row("newer")["run_number"] == 2
    assert store.list_jobs()[1] == 2
    assert not store._job_log_path("older").exists()
    assert store._job_log_path("newer").read_text(encoding="utf-8") == "log-newer\n"


def test_rating_after_terminal_transition_exempts_mesh_from_retention(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("late-rating", "running"))
    store.store_mesh_artifact("late-rating", "mesh")
    store.update_job("late-rating", status="complete")

    changed, _event = store.mutate_job_metadata("late-rating", {"rating": 5})
    store.prune_terminal_jobs(mesh_grace_minutes=0)

    assert changed is True
    assert store.get_job_row("late-rating")["task_metadata"]["rating"] == 5
    assert store.get_job_row("late-rating")["has_mesh_artifact"] is True
    assert store.get_mesh_artifact("late-rating") == "mesh"


def test_snapshot_cursor_and_rows_are_from_one_store_view(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("snapshot"), initial_event=("queued", {}))
    rows, cursor = store.snapshot_jobs()
    assert cursor == 1
    assert [row["id"] for row in rows] == ["snapshot"]
