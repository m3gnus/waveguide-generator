"""A one-way schema upgrade has to name the install that caused it.

One user account has one data directory, but can have several install roots
pointed at it. The first build to open the database raises ``user_version`` in
place, and every older install then refuses to start -- permanently, with no
downgrade path. Observed in the field: nine failed starts over two days, all
telling the user to "upgrade Waveguide Generator" when they already had the
upgrade, in a folder the message never named.
"""

from __future__ import annotations

import sqlite3

import pytest

from server.jobs.store import SUPPORTED_SCHEMA_VERSION, JobStore


def _store(tmp_path):
    return JobStore(
        tmp_path / "db" / "simulations.db",
        job_logs_dir=tmp_path / "logs",
        field_traces_dir=tmp_path / "traces",
    )


def test_opening_records_which_install_owns_the_schema(tmp_path):
    store = _store(tmp_path)
    store.initialize()

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT build, app_root, schema_version FROM wg_install_provenance"
        ).fetchone()

    assert row is not None
    assert row["schema_version"] == SUPPORTED_SCHEMA_VERSION
    assert row["build"]
    assert row["app_root"]


def test_a_newer_schema_names_the_culprit_install(tmp_path):
    """The actionable facts are *which* build locked the file and *where* it
    lives -- not the bare instruction to upgrade."""

    store = _store(tmp_path)
    store.initialize()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA_VERSION + 1}")
        conn.execute(
            "UPDATE wg_install_provenance SET build = ?, app_root = ?",
            ("9.9.9+gfeedface", r"C:\waveguide-generator-next"),
        )

    with pytest.raises(RuntimeError) as excinfo:
        _store(tmp_path).initialize()

    message = str(excinfo.value)
    assert "9.9.9+gfeedface" in message
    assert r"C:\waveguide-generator-next" in message
    # And a way out that does not require deleting anything.
    assert "WG2_DATA_DIR" in message


def test_a_newer_schema_still_refuses_without_a_provenance_row(tmp_path):
    """Databases written before provenance existed have no row. The refusal
    must still be a refusal, and must still explain the shared-directory
    cause."""

    store = _store(tmp_path)
    store.initialize()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TABLE wg_install_provenance")
        conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError) as excinfo:
        _store(tmp_path).initialize()

    assert "WG2_DATA_DIR" in str(excinfo.value)


def test_reopening_refreshes_the_owner(tmp_path):
    """The row describes the build that owns the schema *now*, so a later open
    by a different install replaces it rather than accumulating rows."""

    store = _store(tmp_path)
    store.initialize()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE wg_install_provenance SET build = 'stale'")

    _store(tmp_path).initialize()

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT build FROM wg_install_provenance").fetchall()

    assert len(rows) == 1
    assert rows[0]["build"] != "stale"
