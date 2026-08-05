from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

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
    assert {"simulation_jobs", "simulation_results", "simulation_artifacts"} <= tables
    assert version == 4


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


def test_snapshot_cursor_and_rows_are_from_one_store_view(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("snapshot"), initial_event=("queued", {}))
    rows, cursor = store.snapshot_jobs()
    assert cursor == 1
    assert [row["id"] for row in rows] == ["snapshot"]
