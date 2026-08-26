"""What the stores do with the journal mode SQLite actually granted.

``PRAGMA journal_mode = WAL`` is a request whose answer used to be discarded, so
a data directory on a filesystem without shared memory ran on a rollback journal
while the code believed it had WAL -- and kept ``synchronous = NORMAL``, whose
"cannot corrupt the database" guarantee only holds under WAL.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import sqlite3

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.store import JobStore
from server.platform.sqlite import (
    configure_connection,
    journal_mode_statuses,
    reset_journal_mode_statuses,
)


class _RefusesWal(sqlite3.Connection):
    """A real connection on a real file that answers WAL with ``delete``.

    Standing in for a network share: everything below the pragma is genuine
    SQLite, so the store is exercised on an actual rollback journal.
    """

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:  # type: ignore[override]
        if sql.strip().lower().startswith("pragma journal_mode"):
            return super().execute("PRAGMA journal_mode = DELETE")
        return super().execute(sql, *args)


@pytest.fixture(autouse=True)
def _clean_statuses():
    reset_journal_mode_statuses()
    yield
    reset_journal_mode_statuses()


@pytest.fixture
def refuse_wal(monkeypatch: pytest.MonkeyPatch):
    real_connect = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = _RefusesWal
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


def _mode(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()


def _synchronous(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA synchronous").fetchone()[0])


def test_granted_wal_keeps_the_measured_fast_path(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "simulations.db")
    store.initialize()
    try:
        status = store.journal_mode_status
        assert status is not None
        assert status.journal_mode == "wal"
        assert status.available is True
        # NORMAL is the trade WAL pays for: lose the last commits, never corrupt.
        assert _synchronous(store._connect()) == 1
    finally:
        store.close()

    assert _mode(tmp_path / "simulations.db") == "wal"
    reported = {item["name"]: item for item in journal_mode_statuses()}
    assert reported["Jobs database"]["available"] is True
    assert reported["Jobs database"]["journalMode"] == "wal"


def test_refused_wal_is_reported_and_raises_synchronous_back_to_full(
    tmp_path: Path, refuse_wal: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="wg.sqlite"):
        store = JobStore(tmp_path / "simulations.db")
        store.initialize()
        try:
            status = store.journal_mode_status
            assert status is not None
            assert status.journal_mode == "delete"
            assert status.available is False
            assert "WG2_DATA_DIR" in status.reason
            # synchronous=NORMAL on a rollback journal can corrupt the file, so
            # the setting the WAL guarantee justified is given back.
            assert _synchronous(store._connect()) == 2

            # Degraded, not broken: the store must still be fully usable.
            store.create_job(_job())
            assert store.get_job_row("job-1")["status"] == "queued"
        finally:
            store.close()

    assert _mode(tmp_path / "simulations.db") == "delete"
    assert any(
        record.levelno == logging.WARNING and "refused WAL" in record.getMessage()
        for record in caplog.records
    )
    reported = {item["name"]: item for item in journal_mode_statuses()}
    assert reported["Jobs database"]["available"] is False
    assert reported["Jobs database"]["journalMode"] == "delete"


def test_cadlink_store_reports_a_refused_wal_and_keeps_working(
    tmp_path: Path, refuse_wal: None
) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    store.initialize()
    try:
        status = store.journal_mode_status
        assert status is not None
        assert status.journal_mode == "delete"
        assert status.available is False
        assert _synchronous(store._connect()) == 2

        saved = store.save(
            requested=None,
            design_hash="sha256:" + hashlib.sha256(b"one").hexdigest(),
            filename="design.cfg",
            snapshot_builder=lambda _link: "; Parameter config\n",
            saved_at="2026-08-10T14:22:31Z",
        )
        assert store.get_design(saved["identity"].design_id) is not None
    finally:
        store.close()

    assert {item["name"] for item in journal_mode_statuses()} == {"CAD Link database"}


def test_an_in_memory_database_is_not_a_degraded_one() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        status = configure_connection(conn, db_path=":memory:", label="Scratch database")
    finally:
        conn.close()

    assert status.journal_mode == "memory"
    assert status.available is True
    assert "does not apply" in status.reason
    assert journal_mode_statuses()[0]["available"] is True


def test_one_warning_per_store_rather_than_one_per_thread_connection(
    tmp_path: Path, refuse_wal: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="wg.sqlite"):
        for _ in range(3):
            conn = sqlite3.connect(str(tmp_path / "simulations.db"))
            try:
                configure_connection(
                    conn, db_path=str(tmp_path / "simulations.db"), label="Jobs database"
                )
            finally:
                conn.close()

    warnings = [record for record in caplog.records if "refused WAL" in record.getMessage()]
    assert len(warnings) == 1


def _job() -> dict:
    now = "2026-08-10T14:22:31"
    return {
        "id": "job-1",
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": "queued",
        "stage_message": "queued",
        "config_json": {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }
