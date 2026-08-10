"""v1 → v2 migration: safety, idempotence, and provable rollback.

The migration copies rows rather than transforming them, so the tests worth
having are not about field mapping. They are about the guarantees around the
copy: a backup exists before anything is written, re-running imports nothing
twice, an existing v2 job is never overwritten, content survives byte-for-byte,
and rollback returns the database to exactly its previous state (R1-P0-6).
"""

from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import importlib.util
import json
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

InstanceLock = migrate_v1.InstanceLock
ensure_data_layout = migrate_v1.ensure_data_layout


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
    # closing(), not a bare `with`: a sqlite3 connection used as a context
    # manager commits without closing, and the leaked handle stops Windows
    # deleting or replacing the file the migration is about to rewrite.
    with closing(sqlite3.connect(database)) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM simulation_jobs")}


def _content_digest(database: Path) -> str:
    with closing(sqlite3.connect(database)) as conn:
        rows = sorted(conn.execute("SELECT job_id, results_json FROM simulation_results"))
        meshes = sorted(conn.execute("SELECT job_id, msh_text FROM simulation_artifacts"))
    return hashlib.sha256(repr((rows, meshes)).encode("utf-8")).hexdigest()


def _assert_instance_lock_available(data_dir: Path) -> None:
    paths = ensure_data_layout(data_dir)
    lock = InstanceLock(paths.locks)
    lock.acquire(3100)
    lock.release()


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
    with closing(sqlite3.connect(data_dir / "db" / "simulations.db")) as conn:
        identities = conn.execute(
            "SELECT job_id, run_number FROM job_identity ORDER BY run_number"
        ).fetchall()
    assert identities == [
        ("v1-job-0", 1),
        ("v1-job-1", 2),
        ("v1-job-2", 3),
    ]


def test_discovers_v1_database_in_same_or_sibling_checkout(tmp_path: Path) -> None:
    v2_root = tmp_path / "renamed-v2-checkout"
    v2_root.mkdir()
    in_place = tmp_path / "in-place-upgrade"
    _make_v1(in_place, tag="in-place")
    sibling = tmp_path / "my unusually named old checkout"
    _make_v1(sibling, tag="sibling")

    assert migrate_v1.discover_v1_roots(in_place, environ={}) == [in_place, sibling]
    assert migrate_v1.discover_v1_roots(v2_root, environ={}) == [in_place, sibling]


def test_explicit_v1_root_is_authoritative(tmp_path: Path) -> None:
    configured = tmp_path / "v1 elsewhere"
    _make_v1(configured)
    checkout = tmp_path / "v2"
    checkout.mkdir()

    assert migrate_v1.discover_v1_roots(
        checkout, environ={migrate_v1.V1_ROOT_ENV: str(configured)}
    ) == [configured]

    with pytest.raises(migrate_v1.MigrationError, match="no compatible v1 database"):
        migrate_v1.discover_v1_roots(
            checkout, environ={migrate_v1.V1_ROOT_ENV: str(tmp_path / "missing")}
        )


def test_automatic_migration_runs_under_launcher_lock_and_only_once(
    v1_root: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "v2-checkout"
    checkout.mkdir()
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    lock = InstanceLock(paths.locks)
    lock.acquire(3100)
    source = v1_root / migrate_v1.V1_DATABASE_RELATIVE
    source_before = source.read_bytes()
    try:
        reports = migrate_v1.auto_migrate_v1(checkout, data_dir, lock, environ={})
        backups_after_first = sorted(paths.root.joinpath("backups").iterdir())
        repeated = migrate_v1.auto_migrate_v1(checkout, data_dir, lock, environ={})
    finally:
        lock.release()

    assert len(reports) == 1
    assert reports[0].imported["simulation_jobs"] == 3
    assert reports[0].hash_checked == 6
    assert reports[0].hash_mismatches == []
    assert repeated == []
    assert sorted(paths.root.joinpath("backups").iterdir()) == backups_after_first
    assert _job_ids(paths.db / "simulations.db") == {f"v1-job-{index}" for index in range(3)}
    assert source.read_bytes() == source_before


def test_automatic_migration_refuses_to_run_without_launcher_lock(
    v1_root: Path, tmp_path: Path
) -> None:
    checkout = tmp_path / "v2-checkout"
    checkout.mkdir()
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)

    with pytest.raises(migrate_v1.MigrationError, match="requires the v2 instance lock"):
        migrate_v1.auto_migrate_v1(
            checkout, data_dir, InstanceLock(paths.locks), environ={}
        )


def test_dry_run_writes_nothing(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    report = migrate_v1.migrate(v1_root, data_dir, dry_run=True)

    assert report.dry_run
    assert report.backup_dir is None
    assert not (data_dir / "db" / "simulations.db").exists()


def test_failed_backup_removes_its_incomplete_restore_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = ensure_data_layout(tmp_path / "v2data")
    (paths.workspace / "live.txt").write_text("live", encoding="utf-8")

    def fail_workspace_copy(_source: Path, destination: Path):
        destination.mkdir()
        raise OSError("injected copy failure")

    monkeypatch.setattr(migrate_v1.shutil, "copytree", fail_workspace_copy)
    with pytest.raises(migrate_v1.MigrationError, match="injected copy failure"):
        migrate_v1.take_backup(paths, "test-stamp")

    assert list((paths.root / "backups").iterdir()) == []


def test_running_instance_blocks_migration_before_any_data_mutation(
    v1_root: Path, tmp_path: Path
):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    sentinel = paths.workspace / "keep.txt"
    sentinel.write_text("live workspace", encoding="utf-8")
    lock = InstanceLock(paths.locks)
    lock.acquire(3100)
    try:
        with pytest.raises(migrate_v1.MigrationError, match="already running"):
            migrate_v1.migrate(v1_root, data_dir)
    finally:
        lock.release()

    assert not (paths.db / "simulations.db").exists()
    assert sentinel.read_text(encoding="utf-8") == "live workspace"
    assert not (paths.root / "backups").exists()


def test_dry_run_remains_available_while_instance_lock_is_held(
    v1_root: Path, tmp_path: Path
):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    lock = InstanceLock(paths.locks)
    lock.acquire(3100)
    try:
        report = migrate_v1.migrate(v1_root, data_dir, dry_run=True)
    finally:
        lock.release()

    assert report.imported == dict.fromkeys(migrate_v1.JOB_TABLES, 3)
    assert not (paths.db / "simulations.db").exists()


def test_migration_releases_instance_lock_after_failure(
    v1_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_dir = tmp_path / "v2data"

    def fail_backup(*_args, **_kwargs):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(migrate_v1, "take_backup", fail_backup)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        migrate_v1.migrate(v1_root, data_dir)

    _assert_instance_lock_available(data_dir)


def test_instance_lock_filesystem_error_is_a_clean_cli_error(
    v1_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    class BrokenInstanceLock:
        def __init__(self, _locks_dir: Path):
            pass

        def acquire(self, _port: int):
            raise migrate_v1.InstanceLockError("injected lock filesystem error")

    monkeypatch.setattr(migrate_v1, "InstanceLock", BrokenInstanceLock)

    exit_code = migrate_v1.main(
        ["--v1-root", str(v1_root), "--data-dir", str(tmp_path / "v2data")]
    )

    assert exit_code == 2
    assert "ERROR: injected lock filesystem error" in capsys.readouterr().err


def test_rerunning_imports_nothing_twice(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    first = _job_ids(data_dir / "db" / "simulations.db")

    second_report = migrate_v1.migrate(v1_root, data_dir)

    assert second_report.imported["simulation_jobs"] == 0
    assert _job_ids(data_dir / "db" / "simulations.db") == first


def test_rerun_can_copy_workspace_omitted_by_first_run(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    first_report = migrate_v1.migrate(v1_root, data_dir, include_workspace=False)

    second_report = migrate_v1.migrate(v1_root, data_dir, include_workspace=True)

    assert first_report.workspace_copied == 0
    assert second_report.imported == dict.fromkeys(migrate_v1.JOB_TABLES, 0)
    assert second_report.workspace_copied == 1
    assert (data_dir / "workspace" / "project-a" / "waveguide.project.v1.json").is_file()


def test_dry_run_honours_same_source_marker_when_a_target_row_is_missing(
    v1_root: Path, tmp_path: Path
):
    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    database = data_dir / "db" / "simulations.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(
            "DELETE FROM simulation_artifacts WHERE job_id = ?",
            ("v1-job-0",),
        )

    dry_report = migrate_v1.migrate(v1_root, data_dir, dry_run=True)
    real_report = migrate_v1.migrate(v1_root, data_dir)

    expected_counts = {
        "simulation_jobs": 3,
        "simulation_results": 3,
        "simulation_artifacts": 2,
    }
    assert dry_report.before == real_report.before == expected_counts
    assert dry_report.after == real_report.after == expected_counts
    assert dry_report.imported == real_report.imported == dict.fromkeys(
        migrate_v1.JOB_TABLES, 0
    )
    assert "already migrated (3 jobs)" in " ".join(dry_report.warnings)


def test_copy_failure_preserves_original_exception_and_rolls_back(
    v1_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_dir = tmp_path / "v2data"
    source_database = v1_root / "server" / "data" / "simulations.db"
    source_before = source_database.read_bytes()
    real_table_columns = migrate_v1._table_columns

    def fail_after_first_insert(
        conn: sqlite3.Connection, table: str, schema: str = "main"
    ) -> list[str]:
        if table == "simulation_results" and schema == "main":
            raise OSError("simulated copy failure")
        return real_table_columns(conn, table, schema)

    monkeypatch.setattr(migrate_v1, "_table_columns", fail_after_first_insert)

    with pytest.raises(OSError, match="simulated copy failure"):
        migrate_v1.migrate(v1_root, data_dir)

    assert source_database.read_bytes() == source_before
    assert _job_ids(data_dir / "db" / "simulations.db") == set()


def test_existing_v2_job_is_never_overwritten(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    database = data_dir / "db" / "simulations.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(
            "UPDATE simulation_results SET results_json = ? WHERE job_id = ?",
            ('{"spl": ["edited in v2"]}', "v1-job-0"),
        )
    # A second source database carrying the same ids with different content.
    other = tmp_path / "other v1"
    _make_v1(other, tag="v1")

    report = migrate_v1.migrate(other, data_dir)

    assert "v1-job-0" in report.skipped_existing
    with closing(sqlite3.connect(database)) as conn:
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


def test_rollback_rejects_unrelated_empty_directory_before_target_mutation(
    tmp_path: Path,
):
    backup_dir = tmp_path / "not-a-backup"
    backup_dir.mkdir()
    data_dir = tmp_path / "v2data"

    with pytest.raises(migrate_v1.MigrationError, match="workspace directory"):
        migrate_v1.rollback(backup_dir, data_dir)

    assert not data_dir.exists()
    assert list(backup_dir.iterdir()) == []


def test_rollback_rejects_incomplete_new_backup_before_target_mutation(tmp_path: Path):
    backup_dir = tmp_path / "incomplete-backup"
    (backup_dir / "workspace").mkdir(parents=True)
    (backup_dir / migrate_v1.BACKUP_INCOMPLETE_NAME).write_text(
        "backup is being created\n", encoding="utf-8"
    )
    data_dir = tmp_path / "v2data"

    with pytest.raises(migrate_v1.MigrationError, match="backup was not completed"):
        migrate_v1.rollback(backup_dir, data_dir)

    assert not data_dir.exists()


def test_rollback_rejects_backup_inside_live_workspace_before_mutation(tmp_path: Path):
    data_dir = tmp_path / "v2data"
    live_workspace = data_dir / "workspace"
    backup_dir = live_workspace / "nested-restore-point"
    (backup_dir / "workspace").mkdir(parents=True)
    (backup_dir / "workspace" / "saved.txt").write_text("saved", encoding="utf-8")
    live_db = data_dir / "db" / "simulations.db"
    live_db.parent.mkdir(parents=True)
    live_db.write_bytes(b"live database sentinel")

    with pytest.raises(migrate_v1.MigrationError, match="overlaps the live data root"):
        migrate_v1.rollback(backup_dir, data_dir)

    assert live_db.read_bytes() == b"live database sentinel"
    assert (backup_dir / "workspace" / "saved.txt").read_text(encoding="utf-8") == "saved"
    assert not (data_dir / "locks").exists()
    assert not (data_dir / "backups").exists()


def test_rollback_accepts_legacy_database_backup_outside_data_root(tmp_path: Path):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    (paths.workspace / "live.txt").write_text("live", encoding="utf-8")
    backup_dir = tmp_path / "legacy-restore-point"
    (backup_dir / "workspace").mkdir(parents=True)
    (backup_dir / "workspace" / "saved.txt").write_text("saved", encoding="utf-8")
    saved_database = b"saved database sentinel"
    (backup_dir / "simulations.db").write_bytes(saved_database)

    replaced = migrate_v1.rollback(backup_dir, data_dir)

    assert replaced is not None
    assert (paths.db / "simulations.db").read_bytes() == saved_database
    assert not (paths.workspace / "live.txt").exists()
    assert (paths.workspace / "saved.txt").read_text(encoding="utf-8") == "saved"


def test_rollback_accepts_legacy_empty_prestate_backup(tmp_path: Path):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    live_db = paths.db / "simulations.db"
    with closing(sqlite3.connect(live_db)) as conn, conn:
        conn.execute("CREATE TABLE live_state (value TEXT)")
        conn.execute("INSERT INTO live_state VALUES ('keep in safety backup')")
    (paths.workspace / "live.txt").write_text("live", encoding="utf-8")
    backup_dir = tmp_path / "legacy-empty-prestate"
    (backup_dir / "workspace").mkdir(parents=True)

    replaced = migrate_v1.rollback(backup_dir, data_dir)

    assert replaced is not None
    assert not live_db.exists()
    assert list(paths.workspace.iterdir()) == []


def test_running_instance_blocks_rollback_before_any_data_mutation(tmp_path: Path):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    live_db = paths.db / "simulations.db"
    live_db.write_bytes(b"live database sentinel")
    live_workspace = paths.workspace / "keep.txt"
    live_workspace.write_text("live workspace", encoding="utf-8")
    backup_dir = tmp_path / "restore-point"
    (backup_dir / "workspace").mkdir(parents=True)
    (backup_dir / "simulations.db").write_bytes(b"saved database")
    (backup_dir / "workspace" / "restored.txt").write_text("saved", encoding="utf-8")

    lock = InstanceLock(paths.locks)
    lock.acquire(3100)
    try:
        with pytest.raises(migrate_v1.MigrationError, match="already running"):
            migrate_v1.rollback(backup_dir, data_dir)
    finally:
        lock.release()

    assert live_db.read_bytes() == b"live database sentinel"
    assert live_workspace.read_text(encoding="utf-8") == "live workspace"
    assert not (paths.root / "backups").exists()


def test_rollback_releases_instance_lock_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_dir = tmp_path / "v2data"
    paths = ensure_data_layout(data_dir)
    (paths.workspace / "keep.txt").write_text("live", encoding="utf-8")
    backup_dir = tmp_path / "restore-point"
    (backup_dir / "workspace").mkdir(parents=True)

    def fail_backup(*_args, **_kwargs):
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(migrate_v1, "take_backup", fail_backup)
    with pytest.raises(RuntimeError, match="injected rollback failure"):
        migrate_v1.rollback(backup_dir, data_dir)

    _assert_instance_lock_available(data_dir)


def test_reports_which_migrated_jobs_can_be_reopened_and_which_cannot(
    v1_root: Path, tmp_path: Path
):
    """The design half of the migration is the half that is not a byte copy.

    The report used to state that *no* imported job could be reopened. That was
    true of the reader, not of the data: v1 stores the design twice, and both
    copies survive the copy. The evidence now has to be the measured verdict,
    per job, produced by the same code the server runs.
    """

    report = migrate_v1.migrate(v1_root, tmp_path / "v2data", dry_run=True)

    assert report.jobs_without_snapshot == 1
    assert report.designs == {"recovered_from_design_state": 2, "no_stored_design": 1}
    assert sum(report.designs.values()) == report.source["simulation_jobs"]
    assert report.unrecoverable_job_ids == ["v1-job-2"]
    warning = " ".join(report.warnings)
    assert "1 of 3 imported jobs cannot be reopened" in warning
    assert "no stored design" in warning
    # The jobs that *can* be reopened must never be described as losses.
    assert "recovered from design state" not in warning
    # The report is the G6 artifact, so it has to survive JSON.
    assert json.loads(json.dumps(report.as_dict()))["designs"] == report.designs


def test_report_calls_a_missing_snapshot_a_snapshot_not_a_missing_design(
    v1_root: Path, tmp_path: Path
):
    database = v1_root / "server" / "data" / "simulations.db"
    config = {"options": {"mesh": {"waveguide_params": {"formula_type": "OSSE"}}}}
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(
            "UPDATE simulation_jobs SET config_json = ? WHERE id = ?",
            (json.dumps(config), "v1-job-2"),
        )

    report_path = tmp_path / "migration-report.json"
    exit_code = migrate_v1.main(
        [
            "--v1-root",
            str(v1_root),
            "--data-dir",
            str(tmp_path / "v2data"),
            "--dry-run",
            "--report",
            str(report_path),
        ]
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["designs"]["recovered_from_mesher_payload"] == 1
    assert payload["jobs_without_snapshot"] == 1
    assert "jobs_without_design" not in payload


def test_a_freeform_v1_job_is_reported_as_needing_re_entry(v1_root: Path, tmp_path: Path):
    database = v1_root / "server" / "data" / "simulations.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute(
            "UPDATE simulation_jobs SET script_snapshot_json = ? WHERE id = ?",
            ('{"params": {"type": "FREEFORM"}}', "v1-job-0"),
        )
        conn.commit()

    report = migrate_v1.migrate(v1_root, tmp_path / "v2data", dry_run=True)

    assert report.designs["freeform_legacy_design"] == 1
    assert "v1-job-0" in report.unrecoverable_job_ids
    assert "freeform legacy design" in " ".join(report.warnings)


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


def _current_user_sid() -> str:
    """The SID of the process token, which is not always the named user.

    getpass.getuser() reads the environment, so under a service, container or
    sandboxed runner it can name a different account than the token actually
    running the test. Denying that account leaves the directory writable and the
    test fails for a reason that has nothing to do with the migration.
    """

    completed = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip().rsplit(",", 1)[-1].strip().strip('"')


@contextmanager
def _unwritable(directory: Path) -> Iterator[None]:
    """Stop the current user creating entries in ``directory``, then restore it.

    chmod is not that on Windows: it only toggles the read-only attribute, which
    directories ignore for creation, so the backup would quietly succeed and the
    test would assert nothing. A deny ACE is the equivalent instrument there.
    """

    if sys.platform == "win32":
        account = f"*{_current_user_sid()}"
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


def test_rollback_saves_the_state_it_replaces(v1_root: Path, tmp_path: Path):
    """Rollback was the one destructive path in a tool built to be safe.

    Restoring the wrong backup -- or restoring into the default data directory
    because --data-dir was omitted -- overwrote live jobs with nothing to
    recover from. Now the replaced state is itself backed up first.
    """

    data_dir = tmp_path / "v2data"
    migrate_v1.migrate(v1_root, data_dir)
    database = data_dir / "db" / "simulations.db"
    live = _job_ids(database)
    assert live, "the fixture must leave jobs to be destroyed"

    empty_backup = tmp_path / "empty-backup"
    (empty_backup / "workspace").mkdir(parents=True)

    replaced = migrate_v1.rollback(empty_backup, data_dir)

    # Existence first: sqlite3.connect creates a missing file, so probing the
    # other way round would manufacture the empty database it claims to find.
    assert not database.is_file() or _job_ids(database) == set()
    assert replaced is not None and replaced.is_dir()
    assert _job_ids(replaced / "simulations.db") == live


def test_repeated_backups_in_the_same_second_do_not_collide(v1_root: Path, tmp_path: Path):
    data_dir = tmp_path / "v2data"
    other = tmp_path / "other v1"
    _make_v1(other, jobs=1, tag="other")

    first = migrate_v1.migrate(v1_root, data_dir)
    second = migrate_v1.migrate(other, data_dir)

    assert first.backup_dir != second.backup_dir
    assert Path(first.backup_dir).is_dir() and Path(second.backup_dir).is_dir()
