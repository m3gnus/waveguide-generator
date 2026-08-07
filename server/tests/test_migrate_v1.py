"""v1 → v2 migration: safety, idempotence, and provable rollback.

The migration copies rows rather than transforming them, so the tests worth
having are not about field mapping. They are about the guarantees around the
copy: a backup exists before anything is written, re-running imports nothing
twice, an existing v2 job is never overwritten, content survives byte-for-byte,
and rollback returns the database to exactly its previous state (R1-P0-6).
"""

from __future__ import annotations

from contextlib import contextmanager
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "migrate_v1", REPO_ROOT / "scripts" / "migrate_v1.py"
)
migrate_v1 = importlib.util.module_from_spec(_spec)
sys.modules["migrate_v1"] = migrate_v1
_spec.loader.exec_module(migrate_v1)


V1_SCHEMA = """
CREATE TABLE simulation_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('queued','running','complete','error','cancelled')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, queued_at TEXT NOT NULL,
  started_at TEXT, completed_at TEXT, progress REAL NOT NULL DEFAULT 0.0,
  stage TEXT, stage_message TEXT, error_message TEXT,
  cancellation_requested INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL, config_summary_json TEXT NOT NULL,
  has_results INTEGER NOT NULL DEFAULT 0, has_mesh_artifact INTEGER NOT NULL DEFAULT 0,
  mesh_stats_json TEXT, label TEXT, script_snapshot_json TEXT, task_metadata_json TEXT
);
CREATE TABLE simulation_results (
  job_id TEXT PRIMARY KEY, results_json TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
);
CREATE TABLE simulation_artifacts (
  job_id TEXT PRIMARY KEY, msh_text TEXT,
  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
);
PRAGMA user_version = 4;
"""


def _make_v1(root: Path, jobs: int = 3, *, with_snapshot: int = 2, tag: str = "v1") -> Path:
    """Build a v1 checkout shaped the way the real one is."""

    database = root / "server" / "data" / "simulations.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.executescript(V1_SCHEMA)
    for index in range(jobs):
        job_id = f"{tag}-job-{index}"
        snapshot = '{"params": {"type": "OSSE"}}' if index < with_snapshot else None
        conn.execute(
            "INSERT INTO simulation_jobs (id, status, created_at, updated_at, queued_at,"
            " config_json, config_summary_json, has_results, has_mesh_artifact,"
            " script_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "complete", "2026-01-01", "2026-01-01", "2026-01-01",
             '{"sim_type": 2}', '{"formula_type": "OSSE"}', 1, 1, snapshot),
        )
        conn.execute(
            "INSERT INTO simulation_results (job_id, results_json) VALUES (?, ?)",
            (job_id, f'{{"spl": [{index}, {index + 1}]}}'),
        )
        conn.execute(
            "INSERT INTO simulation_artifacts (job_id, msh_text) VALUES (?, ?)",
            (job_id, f"$MeshFormat {index}"),
        )
    conn.commit()
    conn.close()

    workspace = root / "output" / "project-a"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "waveguide.project.v1.json").write_text('{"name": "a"}', encoding="utf-8")
    return database


def _job_ids(database: Path) -> set[str]:
    with sqlite3.connect(database) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM simulation_jobs")}


def _content_digest(database: Path) -> str:
    with sqlite3.connect(database) as conn:
        rows = sorted(conn.execute("SELECT job_id, results_json FROM simulation_results"))
        meshes = sorted(conn.execute("SELECT job_id, msh_text FROM simulation_artifacts"))
    return hashlib.sha256(repr((rows, meshes)).encode("utf-8")).hexdigest()


@pytest.fixture
def v1_root(tmp_path: Path) -> Path:
    root = tmp_path / "Waveguide Generator"  # the real one has a space; keep it
    _make_v1(root)
    return root


def test_migrates_jobs_results_artifacts_and_workspace(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    report = migrate_v1.migrate(v1_root, data_dir)

    assert report.imported["simulation_jobs"] == 3
    assert report.imported["simulation_results"] == 3
    assert report.imported["simulation_artifacts"] == 3
    assert report.hash_mismatches == []
    assert report.hash_checked == 6
    assert report.workspace_copied == 1
    assert (data_dir / "workspace" / "project-a" / "waveguide.project.v1.json").is_file()


def test_dry_run_writes_nothing(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    report = migrate_v1.migrate(v1_root, data_dir, dry_run=True)

    assert report.dry_run
    assert report.backup_dir is None
    assert not (data_dir / "db" / "simulations.db").exists()


def test_rerunning_imports_nothing_twice(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    first = _job_ids(data_dir / "db" / "simulations.db")

    second_report = migrate_v1.migrate(v1_root, data_dir)

    assert second_report.imported["simulation_jobs"] == 0
    assert _job_ids(data_dir / "db" / "simulations.db") == first


def test_existing_v2_job_is_never_overwritten(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    database = data_dir / "db" / "simulations.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE simulation_results SET results_json = ? WHERE job_id = ?",
            ('{"spl": ["edited in v2"]}', "v1-job-0"),
        )
    # A second source database carrying the same ids with different content.
    other = tmp_path / "other v1"
    _make_v1(other, tag="v1")

    report = migrate_v1.migrate(other, data_dir)

    assert "v1-job-0" in report.skipped_existing
    with sqlite3.connect(database) as conn:
        kept = conn.execute(
            "SELECT results_json FROM simulation_results WHERE job_id = ?", ("v1-job-0",)
        ).fetchone()[0]
    assert kept == '{"spl": ["edited in v2"]}'


def test_merges_into_a_populated_v2_database(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    existing = tmp_path / "existing v1"
    _make_v1(existing, jobs=2, tag="v2native")
    migrate_v1.migrate(existing, data_dir)

    report = migrate_v1.migrate(v1_root, data_dir)

    assert report.before["simulation_jobs"] == 2
    assert report.imported["simulation_jobs"] == 3
    assert report.after["simulation_jobs"] == 5
    assert report.hash_mismatches == []


def test_rollback_restores_the_previous_database_exactly(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    seed = tmp_path / "seed v1"
    _make_v1(seed, jobs=2, tag="seed")
    migrate_v1.migrate(seed, data_dir)
    database = data_dir / "db" / "simulations.db"
    before_ids, before_digest = _job_ids(database), _content_digest(database)

    report = migrate_v1.migrate(v1_root, data_dir)
    assert _job_ids(database) != before_ids

    migrate_v1.rollback(Path(report.backup_dir), data_dir)

    assert _job_ids(database) == before_ids
    assert _content_digest(database) == before_digest


def test_rollback_restores_the_workspace(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    report = migrate_v1.migrate(v1_root, data_dir)
    assert (data_dir / "workspace" / "project-a").is_dir()

    migrate_v1.rollback(Path(report.backup_dir), data_dir)

    assert not (data_dir / "workspace" / "project-a").exists()


def test_reports_that_no_migrated_job_can_be_reopened(v1_root: Path, tmp_path: Path):
    """Both causes must be stated: absent snapshots and v1-shaped ones."""

    report = migrate_v1.migrate(v1_root, tmp_path / "v2data", dry_run=True)

    assert report.jobs_without_design == 1
    warning = " ".join(report.warnings)
    assert "no design snapshot at all" in warning
    assert "v1's parameter shape" in warning


def test_missing_v1_database_is_a_clean_error(tmp_path: Path):
    with pytest.raises(migrate_v1.MigrationError, match="No v1 database"):
        migrate_v1.migrate(tmp_path / "nowhere", tmp_path / "v2data")


def test_custom_v1_workspace_location_is_honoured(v1_root: Path, tmp_path: Path):
    elsewhere = tmp_path / "Custom Output Folder"
    (elsewhere / "moved-project").mkdir(parents=True)
    # Serialise rather than interpolate: a Windows path interpolated into JSON
    # produces invalid \escapes, so the migration read a corrupt settings file
    # instead of the custom location this test is about.
    (v1_root / "server" / "data" / "workspace_settings.json").write_text(
        json.dumps({"path": str(elsewhere)}), encoding="utf-8"
    )
    data_dir = tmp_path / "v2data"

    migrate_v1.migrate(v1_root, data_dir)

    assert (data_dir / "workspace" / "moved-project").is_dir()
    assert not (data_dir / "workspace" / "project-a").exists()


@contextmanager
def _unwritable(directory: Path) -> Iterator[None]:
    """Stop the current user creating entries in ``directory``, then restore it.

    chmod is not that on Windows: it only toggles the read-only attribute, which
    directories ignore for creation, so the backup would quietly succeed and the
    test would assert nothing. A deny ACE is the equivalent instrument there.
    """

    if sys.platform == "win32":
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()
        subprocess.run(
            ["icacls", str(directory), "/deny", f"{account}:(WD,AD)"],
            check=True, capture_output=True,
        )
        try:
            yield
        finally:
            subprocess.run(
                ["icacls", str(directory), "/remove:d", account],
                check=True, capture_output=True,
            )
    else:
        directory.chmod(0o500)  # readable and traversable, not writable
        try:
            yield
        finally:
            directory.chmod(0o700)


def test_backup_failure_stops_before_writing(v1_root: Path, tmp_path: Path):
    """An unwritable backup location must abort before a single row moves."""

    data_dir = tmp_path / "v2data"
    seed = tmp_path / "seed v1"
    _make_v1(seed, jobs=1, tag="seed")
    migrate_v1.migrate(seed, data_dir)
    database = data_dir / "db" / "simulations.db"
    untouched = _job_ids(database)

    backups = data_dir / "backups"
    backups.mkdir(exist_ok=True)
    with _unwritable(backups):
        with pytest.raises(migrate_v1.MigrationError, match="Nothing has been changed"):
            migrate_v1.migrate(v1_root, data_dir)

    assert _job_ids(database) == untouched


def test_repeated_backups_in_the_same_second_do_not_collide(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    other = tmp_path / "other v1"
    _make_v1(other, jobs=1, tag="other")

    first = migrate_v1.migrate(v1_root, data_dir)
    second = migrate_v1.migrate(other, data_dir)

    assert first.backup_dir != second.backup_dir
    assert Path(first.backup_dir).is_dir() and Path(second.backup_dir).is_dir()
