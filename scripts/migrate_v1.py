#!/usr/bin/env python3
"""Migrate Waveguide Generator v1 solve history and workspace into v2.

The v1 job tables were ported to v2 unchanged and remain the migration source
(WG-REBUILD-PLAN §42): v1 databases are at ``user_version = 4``, while the
current target adds only newer sidecar metadata tables, so this copies the v1
rows rather than transforming them.
What the tool actually owes you is the safety around that copy -- a backup taken
before anything is written, an idempotent merge, and a verification pass that
compares content hashes rather than trusting the copy.

    python scripts/migrate_v1.py --v1-root "../Waveguide Generator"
    python scripts/migrate_v1.py --v1-root "../Waveguide Generator" --dry-run
    python scripts/migrate_v1.py --rollback <backup-directory>

Imported designs are classified with the same recovery path the running v2
server uses. A missing ``script_snapshot_json`` is not itself a lost design:
v1 often retains the solved geometry in its mesher payload. The report separates
designs recovered from v1 state or that payload from legacy designs that really
cannot be reopened, such as FREEFORM profiles that require manual re-entry.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Mapping


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.paths import app_root, data_paths, ensure_data_layout  # noqa: E402
from server.platform.instance import (  # noqa: E402
    DEFAULT_PORT,
    InstanceLock,
    InstanceLockError,
)

REPO_ROOT = app_root()

JOB_TABLES = ("simulation_jobs", "simulation_results", "simulation_artifacts")
MARKER_TABLE = "v1_migrations"
BACKUP_MANIFEST_NAME = ".wg2-migration-backup.json"
BACKUP_INCOMPLETE_NAME = ".wg2-migration-backup.incomplete"
BACKUP_FORMAT_VERSION = 1
BACKUP_PRODUCER = "waveguide-generator-v2/migrate_v1.py"
V1_ROOT_ENV = "WG1_ROOT"
V1_DATABASE_RELATIVE = Path("server") / "data" / "simulations.db"

log = logging.getLogger("wg.v1_migration")


class MigrationError(RuntimeError):
    """Anything that should stop the migration with a message, not a traceback."""


@dataclass
class Report:
    """Everything G6 wants as evidence, in one serialisable place."""

    started_at: str
    v1_root: str
    data_dir: str
    backup_dir: str | None = None
    dry_run: bool = False
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    source: dict[str, int] = field(default_factory=dict)
    imported: dict[str, int] = field(default_factory=dict)
    skipped_existing: list[str] = field(default_factory=list)
    workspace_copied: int = 0
    workspace_skipped: int = 0
    hash_checked: int = 0
    hash_mismatches: list[str] = field(default_factory=list)
    jobs_without_snapshot: int = 0
    #: How each source job's design will read in v2, counted by the same code
    #: the server uses. This is the half of the migration that is *not* a byte
    #: copy, so it is the half that needs evidence.
    designs: dict[str, int] = field(default_factory=dict)
    unrecoverable_job_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def _file_uri(path: Path, *, read_only: bool = False) -> str:
    """Percent-encoded SQLite URI. Users' folders contain spaces.

    ATTACH only honours a URI when the connection was itself opened in URI
    mode, so every connection here uses one.
    """

    uri = path.absolute().as_uri()
    return f"{uri}?mode=ro" if read_only else uri


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(_file_uri(path, read_only=read_only), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    # The schema qualifies the pragma, not its argument: `PRAGMA v1.table_info(t)`.
    return [row["name"] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in JOB_TABLES:
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            counts[table] = 0
    return counts


def _row_keys(conn: sqlite3.Connection, table: str) -> set[str]:
    """Primary keys used by INSERT OR IGNORE, for an exact dry-run forecast."""

    column = "id" if table == "simulation_jobs" else "job_id"
    try:
        return {str(row[0]) for row in conn.execute(f"SELECT {column} FROM {table}")}
    except sqlite3.OperationalError:
        return set()


def _marker_state(conn: sqlite3.Connection, fingerprint: str) -> tuple[int, bool] | None:
    """Return the database count and workspace phase for a migrated source."""

    if not _table_columns(conn, MARKER_TABLE):
        return None
    row = conn.execute(
        f"SELECT jobs_imported, workspace_completed FROM {MARKER_TABLE} "
        "WHERE source_fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    return None if row is None else (int(row[0]), bool(row[1]))


def _marker_jobs_imported(conn: sqlite3.Connection, fingerprint: str) -> int | None:
    state = _marker_state(conn, fingerprint)
    return None if state is None else state[0]


def _acquire_mutation_lock(paths) -> InstanceLock:
    """Exclude a running server (or another migration) from mutating v2 data."""

    lock = InstanceLock(paths.locks)
    try:
        lock.acquire(DEFAULT_PORT)
    except InstanceLockError as exc:
        raise MigrationError(str(exc)) from exc
    return lock


def resolve_v1_sources(v1_root: Path) -> tuple[Path, Path | None]:
    """Return the v1 database and workspace directory.

    The workspace is ``output/`` beside the checkout unless the user pointed v1
    somewhere else, which it records in ``server/data/workspace_settings.json``.
    """

    database = v1_root / V1_DATABASE_RELATIVE
    if not database.is_file():
        raise MigrationError(
            f"No v1 database at {database}. Pass --v1-root pointing at the v1 checkout."
        )

    workspace = v1_root / "output"
    configured_workspace = False
    settings = v1_root / "server" / "data" / "workspace_settings.json"
    if settings.is_file():
        try:
            configured = json.loads(settings.read_text(encoding="utf-8")).get("path")
            if configured:
                workspace = Path(configured).expanduser()
                configured_workspace = True
        except (OSError, ValueError, AttributeError) as exc:
            raise MigrationError(f"Could not read {settings}: {exc}") from exc

    # Preserve an unavailable configured path so automatic migration can keep
    # the workspace phase incomplete and retry after a removable/network drive
    # returns. An absent default output directory simply means v1 had no
    # workspace to migrate.
    return database, workspace if configured_workspace or workspace.is_dir() else None


def _looks_like_v1_root(root: Path) -> bool:
    """Recognise the persisted v1 job schema without modifying the database."""

    database = root / V1_DATABASE_RELATIVE
    if not database.is_file():
        return False
    try:
        with closing(_connect(database, read_only=True)) as conn:
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 4:
                return False
            required = {
                "simulation_jobs": {"id", "config_json"},
                "simulation_results": {"job_id", "results_json"},
                "simulation_artifacts": {"job_id", "msh_text"},
            }
            return all(
                columns.issubset(_table_columns(conn, table))
                for table, columns in required.items()
            )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def discover_v1_roots(
    checkout_root: Path = REPO_ROOT,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    """Find legacy checkouts that can contain v1 runs.

    An update that changes an existing checkout from the v1 branch to v2 leaves
    the database below ``checkout_root``. Side-by-side installs leave it in a
    sibling checkout, whose folder name is user-controlled, so direct siblings
    are inspected by schema rather than by one hard-coded product name.

    ``WG1_ROOT`` is an escape hatch for a checkout stored elsewhere. An explicit
    path is authoritative and an invalid one is reported instead of silently
    falling back to a different database.
    """

    env = os.environ if environ is None else environ
    configured = env.get(V1_ROOT_ENV)
    if configured:
        root = Path(configured).expanduser().absolute()
        if not _looks_like_v1_root(root):
            raise MigrationError(
                f"{V1_ROOT_ENV} points at {root}, but no compatible v1 database exists at "
                f"{root / V1_DATABASE_RELATIVE}."
            )
        return [root]

    checkout = checkout_root.expanduser().absolute()
    # A v2 database deliberately retains v1's three core tables and schema
    # version, so sibling schema scanning cannot prove provenance. Only an
    # in-place upgrade is automatic; side-by-side installs must opt in through
    # WG1_ROOT (or the explicit CLI argument).
    candidates = [checkout]

    roots: list[Path] = []
    seen_databases: set[Path] = set()
    for candidate in candidates:
        if not _looks_like_v1_root(candidate):
            continue
        try:
            database = (candidate / V1_DATABASE_RELATIVE).resolve()
        except OSError:
            database = (candidate / V1_DATABASE_RELATIVE).absolute()
        if database in seen_databases:
            continue
        seen_databases.add(database)
        roots.append(candidate)
    return roots


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def take_backup(paths, stamp: str, *, prefix: str = "pre-v1-migration") -> Path:
    """Copy everything the migration can touch. Refuse to continue without it."""

    # Two runs inside the same second must not collide -- a second-resolution
    # name made a quick re-run abort with "File exists".
    base = paths.root / "backups" / f"{prefix}-{stamp}"
    backup_dir = base
    suffix = 1
    while backup_dir.exists():
        backup_dir = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    created = False
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        created = True
        incomplete = backup_dir / BACKUP_INCOMPLETE_NAME
        incomplete.write_text("backup is being created\n", encoding="utf-8")
        database = paths.db / "simulations.db"
        if database.is_file():
            # sqlite3's backup API copies a consistent snapshot even if another
            # process holds the database open; a file copy does not.
            #
            # closing() matters: a sqlite3 connection used as a context manager
            # commits, it does not close. The leaked handle keeps the file open,
            # and Windows then refuses to unlink or replace it -- which rollback
            # does immediately afterwards.
            with closing(_connect(database)) as source, closing(
                sqlite3.connect(str(backup_dir / "simulations.db"))
            ) as target:
                source.backup(target)
        # Copy it even when empty. "There was nothing here" is a state rollback
        # has to be able to restore. All production callers ensure the data
        # layout first; making this unconditional also prevents this function
        # from ever publishing a backup without its legacy provenance marker.
        shutil.copytree(paths.workspace, backup_dir / "workspace")
        manifest = {
            "formatVersion": BACKUP_FORMAT_VERSION,
            "producer": BACKUP_PRODUCER,
            "database": database.is_file(),
            "workspace": True,
        }
        manifest_path = backup_dir / BACKUP_MANIFEST_NAME
        manifest_temporary = manifest_path.with_suffix(".json.tmp")
        manifest_temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        manifest_temporary.replace(manifest_path)
        incomplete.unlink()
    except (OSError, sqlite3.Error) as exc:
        cleanup_note = ""
        if created:
            try:
                shutil.rmtree(backup_dir)
            except OSError as cleanup_exc:
                cleanup_note = (
                    f" The incomplete backup remains at {backup_dir} and will not be "
                    f"accepted for rollback: {cleanup_exc}."
                )
        raise MigrationError(
            f"Could not write the backup at {backup_dir}: {exc}. "
            "Nothing has been changed. Free some space or fix permissions, then retry."
            f"{cleanup_note}"
        ) from exc
    return backup_dir


def _ensure_marker(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {MARKER_TABLE} (
              source_fingerprint TEXT PRIMARY KEY,
              migrated_at TEXT NOT NULL,
              jobs_imported INTEGER NOT NULL,
              workspace_completed INTEGER NOT NULL DEFAULT 0
            )"""
    )
    columns = set(_table_columns(conn, MARKER_TABLE))
    if "workspace_completed" not in columns:
        conn.execute(
            f"ALTER TABLE {MARKER_TABLE} ADD COLUMN workspace_completed "
            "INTEGER NOT NULL DEFAULT 0"
        )


def _fingerprint(database: Path) -> str:
    """Hash the logical rows that migration copies, independent of DB layout."""

    digest = hashlib.sha256()
    with closing(_connect(database, read_only=True)) as conn:
        for table in JOB_TABLES:
            columns = _table_columns(conn, table)
            digest.update(json.dumps([table, columns], separators=(",", ":")).encode())
            key = "id" if table == "simulation_jobs" else "job_id"
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {key}"):
                values = []
                for value in row:
                    if isinstance(value, bytes):
                        values.append({"sha256": hashlib.sha256(value).hexdigest()})
                    else:
                        values.append(value)
                digest.update(
                    json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str).encode(
                        "utf-8"
                    )
                )
                digest.update(b"\n")
    return f"logical-v2:{digest.hexdigest()}"


def _row_hashes(conn: sqlite3.Connection, table: str, column: str) -> dict[str, str]:
    hashes = {}
    for row in conn.execute(f"SELECT job_id, {column} FROM {table}"):
        value = row[column]
        if value is None:
            continue
        payload = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        hashes[row["job_id"]] = hashlib.sha256(payload).hexdigest()
    return hashes


def _classify_designs(conn: sqlite3.Connection) -> tuple[dict[str, int], list[str]]:
    """Say, per source job, whether v2 will be able to reopen it.

    Deliberately the *same* code path the running server uses rather than a
    second opinion written for the report -- an evidence artifact that agrees
    with a re-implementation instead of with the product is worth nothing.
    """

    from server.jobs.legacy_design import resolve_job_design

    counts: dict[str, int] = {}
    unrecoverable: list[str] = []
    for row in conn.execute(
        "SELECT id, config_json, script_snapshot_json FROM simulation_jobs ORDER BY created_at"
    ):
        snapshot = json.loads(row["script_snapshot_json"]) if row["script_snapshot_json"] else None
        config = json.loads(row["config_json"] or "{}")
        verdict = resolve_job_design(snapshot, config)
        key = "native" if verdict.reason_code == "ok" else verdict.reason_code
        if verdict.reopenable and verdict.reason_code == "recovered":
            key = f"recovered_from_{verdict.source.removeprefix('v1-').replace('-', '_')}"
        counts[key] = counts.get(key, 0) + 1
        if not verdict.reopenable:
            unrecoverable.append(str(row["id"]))
    return counts, unrecoverable


def migrate(
    v1_root: Path,
    data_dir: Path | None,
    *,
    dry_run: bool = False,
    include_workspace: bool = True,
) -> Report:
    database, workspace = resolve_v1_sources(v1_root)
    paths = data_paths(data_dir) if dry_run else ensure_data_layout(data_dir)
    if dry_run:
        return _migrate_with_paths(
            v1_root, database, workspace, paths,
            dry_run=True, include_workspace=include_workspace,
        )
    with _acquire_mutation_lock(paths):
        return _migrate_with_paths(
            v1_root, database, workspace, paths,
            dry_run=False, include_workspace=include_workspace,
        )


def _migration_needed(
    database: Path, target_db: Path, *, workspace_required: bool = False
) -> bool:
    """Return whether this exact source snapshot lacks a completion marker."""

    if not target_db.is_file():
        return True
    fingerprint = _fingerprint(database)
    try:
        with closing(_connect(target_db, read_only=True)) as conn:
            state = _marker_state(conn, fingerprint)
            return state is None or (workspace_required and not state[1])
    except sqlite3.Error:
        # JobStore.initialize() inside the migration is the schema authority and
        # will produce the useful compatibility error if this is not a v2 DB.
        return True


def auto_migrate_v1(
    checkout_root: Path,
    data_dir: Path,
    instance_lock: InstanceLock,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[Report]:
    """Migrate every discovered v1 run database during application startup.

    The launcher must already own v2's process lock. Reusing that lock avoids a
    release/reacquire race between migration and server startup while retaining
    the manual command's guarantee that no v2 process can write concurrently.
    Sources already marked complete are opened read-only and skipped without a
    new backup, so the normal startup path stays cheap and does not accumulate
    one backup per launch.
    """

    paths = ensure_data_layout(data_dir)
    if not instance_lock.owned:
        raise MigrationError("Automatic v1 migration requires the v2 instance lock.")
    try:
        lock_directory = instance_lock.path.parent.resolve()
        expected_lock_directory = paths.locks.resolve()
    except OSError as exc:
        raise MigrationError(f"Could not validate the v2 instance lock: {exc}") from exc
    if lock_directory != expected_lock_directory:
        raise MigrationError(
            f"Automatic v1 migration holds the lock for {lock_directory}, not "
            f"the active data directory {expected_lock_directory}."
        )

    target_db = paths.db / "simulations.db"
    try:
        target_resolved = target_db.resolve()
    except OSError:
        target_resolved = target_db.absolute()

    reports: list[Report] = []
    for v1_root in discover_v1_roots(checkout_root, environ=environ):
        database, workspace = resolve_v1_sources(v1_root)
        try:
            source_resolved = database.resolve()
        except OSError:
            source_resolved = database.absolute()
        if source_resolved == target_resolved or not _migration_needed(
            database, target_db, workspace_required=workspace is not None
        ):
            continue

        log.warning("Found v1 run database at %s; migrating it into %s", database, target_db)
        report = _migrate_with_paths(
            v1_root,
            database,
            workspace,
            paths,
            dry_run=False,
            include_workspace=True,
        )
        if report.hash_mismatches:
            raise MigrationError(
                f"Automatic v1 migration failed content verification. The pre-migration "
                f"backup is at {report.backup_dir}; start was stopped so the failure is visible."
            )
        reports.append(report)
        log.warning(
            "v1 migration complete: %d runs imported, %d existing runs kept, "
            "%d result/artifact hashes verified; backup: %s",
            report.imported.get("simulation_jobs", 0),
            len(report.skipped_existing),
            report.hash_checked,
            report.backup_dir,
        )
        for warning in report.warnings:
            log.warning("v1 migration: %s", warning)
    return reports


def _migrate_with_paths(
    v1_root: Path,
    database: Path,
    workspace: Path | None,
    paths,
    *,
    dry_run: bool,
    include_workspace: bool,
) -> Report:
    stamp = _timestamp()
    report = Report(
        started_at=datetime.now().isoformat(timespec="seconds"),
        v1_root=str(v1_root),
        data_dir=str(paths.root),
        dry_run=dry_run,
    )

    target_db = paths.db / "simulations.db"
    with closing(_connect(database, read_only=True)) as source:
        report.source = _counts(source)
        report.jobs_without_snapshot = int(
            source.execute(
                "SELECT COUNT(*) FROM simulation_jobs WHERE script_snapshot_json IS NULL"
            ).fetchone()[0]
        )
        source_keys = {table: _row_keys(source, table) for table in JOB_TABLES}
        source_ids = source_keys["simulation_jobs"]
        source_results = _row_hashes(source, "simulation_results", "results_json")
        source_meshes = _row_hashes(source, "simulation_artifacts", "msh_text")
        report.designs, report.unrecoverable_job_ids = _classify_designs(source)

    total_jobs = report.source["simulation_jobs"]
    unrecoverable = len(report.unrecoverable_job_ids)
    if unrecoverable:
        # Not a migration defect, and not a count anyone should have to derive
        # from the others: these jobs keep their results, their mesh and their
        # solver settings, and lose only the ability to be run again.
        by_cause = ", ".join(
            f"{count} {cause.replace('_', ' ')}"
            for cause, count in sorted(report.designs.items())
            if count and cause != "native" and not cause.startswith("recovered_from_")
        )
        report.warnings.append(
            f"{unrecoverable} of {total_jobs} imported jobs cannot be reopened or rerun ({by_cause}). "
            f"Their results, mesh artifacts and solver settings migrate normally; only the design "
            f"behind them is gone. v2 states the cause on each job."
        )

    # v2's own schema creation is the only thing that should ever author this
    # database, so open it through the store rather than issuing CREATE here.
    from server.jobs.store import JobStore

    store = JobStore.for_data_dir(paths.root)
    if not dry_run:
        store.initialize()
        # The store keeps its connections open now, and it runs in WAL mode.
        # Both matter here: an open handle stops rollback unlinking or
        # replacing the file on Windows, and a checkpoint is what guarantees
        # the single .db file this tool copies is not missing commits that are
        # still only in the -wal sidecar.
        store.checkpoint()
        store.close()

    existing_keys = {table: set() for table in JOB_TABLES}
    already_imported: int | None = None
    if target_db.is_file():
        with closing(_connect(target_db, read_only=dry_run)) as conn:
            report.before = _counts(conn)
            existing_keys = {table: _row_keys(conn, table) for table in JOB_TABLES}
            if dry_run:
                already_imported = _marker_jobs_imported(conn, _fingerprint(database))
    else:
        # A first migration into a machine that has never run v2.
        report.before = dict.fromkeys(JOB_TABLES, 0)

    report.skipped_existing = sorted(source_ids & existing_keys["simulation_jobs"])
    if report.skipped_existing:
        report.warnings.append(
            f"{len(report.skipped_existing)} job ids already exist in v2 and were left untouched; "
            "the v2 row wins. Re-running is safe and imports nothing twice."
        )

    if dry_run:
        report.after = dict(report.before)
        if already_imported is not None:
            report.warnings.append(
                f"This v1 database was already migrated ({already_imported} jobs). Nothing to do."
            )
            report.imported = dict.fromkeys(JOB_TABLES, 0)
        else:
            report.imported = {
                table: len(source_keys[table] - existing_keys[table])
                for table in JOB_TABLES
            }
        return report

    report.backup_dir = str(take_backup(paths, stamp))

    with closing(_connect(target_db)) as conn, conn:
        _ensure_marker(conn)
        fingerprint = _fingerprint(database)
        already_imported = _marker_jobs_imported(conn, fingerprint)
        if already_imported is not None:
            report.warnings.append(
                f"This v1 database was already migrated ({already_imported} jobs); "
                "no database rows to import."
            )
            report.after = _counts(conn)
            report.imported = dict.fromkeys(JOB_TABLES, 0)
        else:
            conn.execute("ATTACH DATABASE ? AS v1", (_file_uri(database, read_only=True),))
            # The enclosing transaction context rolls back before closing() closes
            # the connection and releases the attached database. An explicit
            # DETACH in a finally block would run too early and could replace the
            # exception that caused the rollback.
            imported = {}
            accepted_ids = sorted(source_ids - existing_keys["simulation_jobs"])
            conn.execute("CREATE TEMP TABLE wg_import_job_ids (id TEXT PRIMARY KEY)")
            conn.executemany(
                "INSERT INTO wg_import_job_ids (id) VALUES (?)",
                ((job_id,) for job_id in accepted_ids),
            )
            for table in JOB_TABLES:
                target_columns = _table_columns(conn, table)
                source_columns = [
                    name
                    for name in _table_columns(conn, table, schema="v1")
                    if name in target_columns
                ]
                missing = sorted(set(target_columns) - set(source_columns))
                if missing:
                    report.warnings.append(
                        f"{table}: v1 has no {', '.join(missing)}; imported as NULL/default."
                    )
                columns = ", ".join(f'"{name}"' for name in source_columns)
                before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                # OR IGNORE is what makes a re-run idempotent at row level even
                # if the marker table is lost.
                source_key = "id" if table == "simulation_jobs" else "job_id"
                conn.execute(
                    f"INSERT INTO {table} ({columns}) SELECT {columns} FROM v1.{table} "
                    f"WHERE {source_key} IN (SELECT id FROM wg_import_job_ids)"
                )
                after = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                imported[table] = after - before
            imported_at = datetime.now().isoformat(timespec="seconds")
            for job_id in accepted_ids:
                row = conn.execute(
                    "SELECT task_metadata_json FROM simulation_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                metadata = json.loads(row[0]) if row and row[0] else {}
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["imported_at"] = imported_at
                conn.execute(
                    "UPDATE simulation_jobs SET task_metadata_json = ? WHERE id = ?",
                    (json.dumps(metadata, separators=(",", ":")), job_id),
                )
            conn.execute(
                f"INSERT INTO {MARKER_TABLE} "
                "(source_fingerprint, migrated_at, jobs_imported, workspace_completed) "
                "VALUES (?, ?, ?, 0)",
                (fingerprint, datetime.now().isoformat(timespec="seconds"),
                 imported["simulation_jobs"]),
            )
            conn.commit()
            report.imported = imported
            report.after = _counts(conn)

            # Verify by content, not by row count: a copy that lands the wrong
            # bytes still counts correctly.
            for table, column, expected in (
                ("simulation_results", "results_json", source_results),
                ("simulation_artifacts", "msh_text", source_meshes),
            ):
                actual = _row_hashes(conn, table, column)
                for job_id, digest in expected.items():
                    if job_id in report.skipped_existing:
                        continue
                    report.hash_checked += 1
                    if actual.get(job_id) != digest:
                        report.hash_mismatches.append(f"{table}:{job_id}")

    # Schema initialization necessarily precedes the byte-for-byte table copy.
    # Number imported jobs immediately afterward so one migration pass leaves
    # every job with a permanent identity, in deterministic (created_at, id)
    # order among rows that do not already have one.
    #
    # The store above was closed on purpose, to drop the handles that would
    # otherwise stop rollback replacing the file on Windows. A closed store
    # stays closed, so take a fresh one rather than reviving that instance.
    store = JobStore.for_data_dir(paths.root)
    store.backfill_job_identity()
    store.checkpoint()
    store.close()

    workspace_complete = not include_workspace or workspace is None
    if include_workspace and workspace is not None and workspace.is_dir():
        workspace_complete = True
        for entry in sorted(workspace.iterdir()):
            if entry.name.startswith("."):
                continue
            destination = paths.workspace / entry.name
            if destination.exists():
                report.workspace_skipped += 1
                continue
            try:
                if entry.is_dir():
                    shutil.copytree(entry, destination)
                else:
                    shutil.copy2(entry, destination)
                report.workspace_copied += 1
            except OSError as exc:
                workspace_complete = False
                report.warnings.append(f"Could not copy workspace entry {entry.name}: {exc}")
    elif include_workspace and workspace is not None:
        workspace_complete = False
        report.warnings.append(f"Legacy workspace is currently unavailable at {workspace}")

    if include_workspace and workspace_complete:
        with closing(_connect(target_db)) as conn, conn:
            _ensure_marker(conn)
            conn.execute(
                f"UPDATE {MARKER_TABLE} SET workspace_completed = 1 "
                "WHERE source_fingerprint = ?",
                (_fingerprint(database),),
            )

    return report


def rollback(backup_dir: Path, data_dir: Path | None) -> Path | None:
    """Restore a backup, after snapshotting whatever it is about to replace.

    Migration backs up before it writes; rollback did not, so it was the one
    destructive path in a tool whose whole point is being safe to run. Restoring
    the wrong backup, or restoring into the default data directory because
    --data-dir was omitted, overwrote live jobs with no way back. The safety copy
    costs a file copy and turns that into an inconvenience.
    """

    # Validation must precede even layout creation. Otherwise a typo here can
    # create directories in the target and, more importantly, an unrelated
    # empty directory can reach the destructive "no saved database" branch.
    paths = data_paths(data_dir)
    backup_dir = _validate_rollback_source(backup_dir, paths)
    paths = ensure_data_layout(paths.root)
    with _acquire_mutation_lock(paths):
        return _rollback_with_paths(backup_dir, paths)


def _validate_rollback_source(backup_dir: Path, paths) -> Path:
    """Return a canonical, safe migration backup before touching live data.

    Older backups predate the manifest, so the workspace directory remains the
    backward-compatible provenance marker: ``take_backup`` has always copied it,
    including when it was empty. New backups carry a manifest as stronger
    evidence and its claimed contents must agree with the directory.
    """

    try:
        source = backup_dir.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MigrationError(f"No backup directory at {backup_dir}: {exc}") from exc
    if not source.is_dir():
        raise MigrationError(f"No backup directory at {backup_dir}")

    target = paths.root.expanduser().resolve()
    backups_root = (target / "backups").resolve()
    try:
        source.relative_to(target)
    except ValueError:
        pass
    else:
        # Backups made by this tool normally live below the data root. They are
        # safe because rollback mutates db/, workspace/, and locks/, not the
        # distinct backups/ tree. Every other overlap is unsafe; notably, a
        # source below workspace/ would be deleted before it could be copied.
        try:
            relative_backup = source.relative_to(backups_root)
        except ValueError:
            relative_backup = None
        if relative_backup is None or not relative_backup.parts:
            raise MigrationError(
                f"Refusing rollback from {source}: the backup overlaps the live data root "
                f"{target}. Use a backup below {backups_root} or outside the data root."
            )

    saved_workspace = source / "workspace"
    if saved_workspace.is_symlink() or not saved_workspace.is_dir():
        raise MigrationError(
            f"Refusing rollback from {source}: it is not a Waveguide Generator "
            "migration backup (the workspace directory is missing or unsafe)."
        )
    saved_db = source / "simulations.db"
    if saved_db.is_symlink() or (saved_db.exists() and not saved_db.is_file()):
        raise MigrationError(
            f"Refusing rollback from {source}: simulations.db is not a regular file."
        )

    incomplete_path = source / BACKUP_INCOMPLETE_NAME
    if incomplete_path.exists() or incomplete_path.is_symlink():
        raise MigrationError(
            f"Refusing rollback from {source}: the backup was not completed."
        )
    manifest_path = source / BACKUP_MANIFEST_NAME
    if manifest_path.is_symlink() or (manifest_path.exists() and not manifest_path.is_file()):
        raise MigrationError(f"Refusing rollback from {source}: its backup manifest is unsafe.")
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MigrationError(
                f"Refusing rollback from {source}: its backup manifest is invalid: {exc}"
            ) from exc
        expected_database = saved_db.is_file()
        if (
            not isinstance(manifest, dict)
            or manifest.get("formatVersion") != BACKUP_FORMAT_VERSION
            or manifest.get("producer") != BACKUP_PRODUCER
            or manifest.get("workspace") is not True
            or manifest.get("database") is not expected_database
        ):
            raise MigrationError(
                f"Refusing rollback from {source}: its backup manifest does not match "
                "this migration tool or the saved contents."
            )
    return source


def _rollback_with_paths(backup_dir: Path, paths) -> Path | None:
    replaced = None
    if (paths.db / "simulations.db").is_file() or any(paths.workspace.iterdir()):
        replaced = take_backup(paths, _timestamp(), prefix="pre-rollback")
    saved_db = backup_dir / "simulations.db"
    live_db = paths.db / "simulations.db"
    # v2 runs the job database in WAL mode, so committed data can live in a
    # -wal sidecar rather than in the .db file. Replacing or removing the .db
    # while its sidecars survive is not merely incomplete, it is dangerous:
    # SQLite would recover that leftover WAL -- containing the state we are
    # rolling *back* -- on top of the restored database. The backup itself is
    # taken through sqlite3's backup API, which already folds the WAL in, so
    # there is nothing to preserve here.
    _discard_wal_sidecars(live_db)
    if saved_db.is_file():
        shutil.copy2(saved_db, live_db)
    elif live_db.is_file():
        # An empty pre-migration state is still a state worth restoring to.
        live_db.unlink()
    saved_workspace = backup_dir / "workspace"
    if saved_workspace.is_dir():
        if paths.workspace.is_dir():
            shutil.rmtree(paths.workspace)
        shutil.copytree(saved_workspace, paths.workspace)
    return replaced


def _discard_wal_sidecars(database: Path) -> None:
    """Remove a database's -wal and -shm companions, if any."""

    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as exc:
            raise MigrationError(
                f"Could not remove {sidecar}: {exc}. Close any running Waveguide "
                "Generator v2 instance and try again."
            ) from exc


def _print_report(report: Report) -> None:
    mode = "DRY RUN -- nothing was written" if report.dry_run else "Migration complete"
    print(f"\n{mode}")
    print(f"  v1 root   : {report.v1_root}")
    print(f"  v2 data   : {report.data_dir}")
    if report.backup_dir:
        print(f"  backup    : {report.backup_dir}")
    print("\n  table                    v1     v2 before   imported    v2 after")
    for table in JOB_TABLES:
        print(
            f"  {table:<22} {report.source.get(table, 0):>4} {report.before.get(table, 0):>11}"
            f" {report.imported.get(table, 0):>10} {report.after.get(table, 0):>11}"
        )
    if report.hash_checked:
        state = "all match" if not report.hash_mismatches else f"{len(report.hash_mismatches)} MISMATCH"
        print(f"\n  content hashes checked: {report.hash_checked} ({state})")
    if report.workspace_copied or report.workspace_skipped:
        print(
            f"  workspace: {report.workspace_copied} copied, "
            f"{report.workspace_skipped} already present"
        )
    if report.designs:
        print("\n  designs, as v2 will read them:")
        for cause, count in sorted(report.designs.items()):
            print(f"    {cause.replace('_', ' '):<34} {count:>4}")
    for warning in report.warnings:
        print(f"\n  ! {warning}")
    if report.hash_mismatches:
        print("\n  Content verification FAILED. Roll back with:")
        print(f"    python scripts/migrate_v1.py --rollback {report.backup_dir}")
    elif report.backup_dir:
        print("\n  To undo this migration:")
        print(f"    python scripts/migrate_v1.py --rollback {report.backup_dir}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--v1-root", type=Path, help="path to the v1 checkout")
    parser.add_argument("--data-dir", type=Path, default=None, help="v2 data directory override")
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    parser.add_argument(
        "--no-workspace", action="store_true", help="migrate the database but not saved projects"
    )
    parser.add_argument("--rollback", type=Path, help="restore from a backup directory")
    parser.add_argument("--report", type=Path, help="also write the report as JSON here")
    args = parser.parse_args(argv)

    try:
        if args.rollback is not None:
            replaced = rollback(args.rollback, args.data_dir)
            print(f"Restored from {args.rollback}")
            # Name the directory that was written to. Without --data-dir this is
            # the default one, which is not always the one the caller meant.
            print(f"  restored into: {ensure_data_layout(args.data_dir).root}")
            if replaced is not None:
                print(f"  the state it replaced was saved to: {replaced}")
            return 0
        if args.v1_root is None:
            parser.error("--v1-root is required unless --rollback is given")
        report = migrate(
            args.v1_root.expanduser(),
            args.data_dir,
            dry_run=args.dry_run,
            include_workspace=not args.no_workspace,
        )
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_report(report)
    if args.report:
        args.report.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        print(f"  report written to {args.report}\n")
    return 1 if report.hash_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
