#!/usr/bin/env python3
"""Migrate Waveguide Generator v1 solve history and workspace into v2.

The v1 job schema was ported to v2 unchanged and doubles as the migration
target (WG-REBUILD-PLAN §42): v1 databases are already at ``user_version = 4``
with v2's exact column set, so this copies rows rather than transforming them.
What the tool actually owes you is the safety around that copy -- a backup taken
before anything is written, an idempotent merge, and a verification pass that
compares content hashes rather than trusting the copy.

    python scripts/migrate_v1.py --v1-root "../Waveguide Generator"
    python scripts/migrate_v1.py --v1-root "../Waveguide Generator" --dry-run
    python scripts/migrate_v1.py --rollback <backup-directory>

Known limitation, and no migration can fix it: v1 never populated
``script_snapshot_json``, so imported jobs carry results, mesh artifacts and
solver settings but no design. They cannot be reopened or rerun.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from server.platform.paths import data_paths, ensure_data_layout  # noqa: E402

JOB_TABLES = ("simulation_jobs", "simulation_results", "simulation_artifacts")
MARKER_TABLE = "v1_migrations"


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
    jobs_without_design: int = 0
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


def resolve_v1_sources(v1_root: Path) -> tuple[Path, Path | None]:
    """Return the v1 database and workspace directory.

    The workspace is ``output/`` beside the checkout unless the user pointed v1
    somewhere else, which it records in ``server/data/workspace_settings.json``.
    """

    database = v1_root / "server" / "data" / "simulations.db"
    if not database.is_file():
        raise MigrationError(
            f"No v1 database at {database}. Pass --v1-root pointing at the v1 checkout."
        )

    workspace = v1_root / "output"
    settings = v1_root / "server" / "data" / "workspace_settings.json"
    if settings.is_file():
        try:
            configured = json.loads(settings.read_text(encoding="utf-8")).get("path")
            if configured:
                workspace = Path(configured).expanduser()
        except (OSError, ValueError, AttributeError) as exc:
            raise MigrationError(f"Could not read {settings}: {exc}") from exc

    return database, workspace if workspace.is_dir() else None


def take_backup(paths, stamp: str) -> Path:
    """Copy everything the migration can touch. Refuse to continue without it."""

    # Two runs inside the same second must not collide -- a second-resolution
    # name made a quick re-run abort with "File exists".
    base = paths.root / "backups" / f"pre-v1-migration-{stamp}"
    backup_dir = base
    suffix = 1
    while backup_dir.exists():
        backup_dir = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        database = paths.db / "simulations.db"
        if database.is_file():
            # sqlite3's backup API copies a consistent snapshot even if another
            # process holds the database open; a file copy does not.
            with _connect(database) as source, sqlite3.connect(
                str(backup_dir / "simulations.db")
            ) as target:
                source.backup(target)
        if paths.workspace.is_dir():
            # Copy it even when empty. "There was nothing here" is a state
            # rollback has to be able to restore, and skipping the empty case
            # left a first migration's imported projects behind on undo.
            shutil.copytree(paths.workspace, backup_dir / "workspace")
    except (OSError, sqlite3.Error) as exc:
        raise MigrationError(
            f"Could not write the backup at {backup_dir}: {exc}. "
            "Nothing has been changed. Free some space or fix permissions, then retry."
        ) from exc
    return backup_dir


def _ensure_marker(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {MARKER_TABLE} (
              source_fingerprint TEXT PRIMARY KEY,
              migrated_at TEXT NOT NULL,
              jobs_imported INTEGER NOT NULL
            )"""
    )


def _fingerprint(database: Path) -> str:
    """Identify a source database by size and its job ids, not by path."""

    with _connect(database, read_only=True) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM simulation_jobs ORDER BY id")]
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return f"{database.stat().st_size}:{digest[:32]}"


def _row_hashes(conn: sqlite3.Connection, table: str, column: str) -> dict[str, str]:
    hashes = {}
    for row in conn.execute(f"SELECT job_id, {column} FROM {table}"):
        value = row[column]
        if value is None:
            continue
        payload = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        hashes[row["job_id"]] = hashlib.sha256(payload).hexdigest()
    return hashes


def migrate(
    v1_root: Path,
    data_dir: Path | None,
    *,
    dry_run: bool = False,
    include_workspace: bool = True,
) -> Report:
    database, workspace = resolve_v1_sources(v1_root)
    paths = data_paths(data_dir) if dry_run else ensure_data_layout(data_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = Report(
        started_at=datetime.now().isoformat(timespec="seconds"),
        v1_root=str(v1_root),
        data_dir=str(paths.root),
        dry_run=dry_run,
    )

    target_db = paths.db / "simulations.db"
    with _connect(database, read_only=True) as source:
        report.source = _counts(source)
        report.jobs_without_design = int(
            source.execute(
                "SELECT COUNT(*) FROM simulation_jobs WHERE script_snapshot_json IS NULL"
            ).fetchone()[0]
        )
        source_ids = {row[0] for row in source.execute("SELECT id FROM simulation_jobs")}
        source_results = _row_hashes(source, "simulation_results", "results_json")
        source_meshes = _row_hashes(source, "simulation_artifacts", "msh_text")

    total_jobs = report.source["simulation_jobs"]
    legacy_shape = total_jobs - report.jobs_without_design
    if total_jobs:
        # Two separate causes, and neither is a migration defect. Say both, so
        # nobody reads "9 of 35" as "the other 26 are fine".
        report.warnings.append(
            f"No imported job can be reopened or rerun in v2. Results, mesh artifacts and solver "
            f"settings all migrate; the design behind them does not. "
            f"{report.jobs_without_design} of {total_jobs} jobs have no design snapshot at all "
            f"(v1 never populated it), and {legacy_shape} carry v1's parameter shape, which v2's "
            f"design schema does not accept."
        )

    # v2's own schema creation is the only thing that should ever author this
    # database, so open it through the store rather than issuing CREATE here.
    from server.jobs.store import JobStore

    store = JobStore.for_data_dir(paths.root)
    if not dry_run:
        store.initialize()

    existing_ids: set[str] = set()
    if target_db.is_file():
        with _connect(target_db) as conn:
            report.before = _counts(conn)
            try:
                existing_ids = {row[0] for row in conn.execute("SELECT id FROM simulation_jobs")}
            except sqlite3.OperationalError:
                existing_ids = set()
    else:
        # A first migration into a machine that has never run v2.
        report.before = dict.fromkeys(JOB_TABLES, 0)

    report.skipped_existing = sorted(source_ids & existing_ids)
    if report.skipped_existing:
        report.warnings.append(
            f"{len(report.skipped_existing)} job ids already exist in v2 and were left untouched; "
            "the v2 row wins. Re-running is safe and imports nothing twice."
        )

    if dry_run:
        report.after = dict(report.before)
        report.imported = {
            table: max(0, report.source[table] - report.before.get(table, 0))
            for table in JOB_TABLES
        }
        return report

    report.backup_dir = str(take_backup(paths, stamp))

    with _connect(target_db) as conn:
        _ensure_marker(conn)
        fingerprint = _fingerprint(database)
        already = conn.execute(
            f"SELECT jobs_imported FROM {MARKER_TABLE} WHERE source_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if already is not None:
            report.warnings.append(
                f"This v1 database was already migrated ({already[0]} jobs). Nothing to do."
            )
            report.after = _counts(conn)
            report.imported = dict.fromkeys(JOB_TABLES, 0)
            return report

        conn.execute("ATTACH DATABASE ? AS v1", (_file_uri(database, read_only=True),))
        try:
            imported = {}
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
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({columns}) SELECT {columns} FROM v1.{table}"
                )
                after = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                imported[table] = after - before
            conn.execute(
                f"INSERT INTO {MARKER_TABLE} (source_fingerprint, migrated_at, jobs_imported) "
                "VALUES (?, ?, ?)",
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
        finally:
            conn.execute("DETACH DATABASE v1")

    if include_workspace and workspace is not None:
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
                report.warnings.append(f"Could not copy workspace entry {entry.name}: {exc}")

    return report


def rollback(backup_dir: Path, data_dir: Path | None) -> None:
    if not backup_dir.is_dir():
        raise MigrationError(f"No backup directory at {backup_dir}")
    paths = ensure_data_layout(data_dir)
    saved_db = backup_dir / "simulations.db"
    if saved_db.is_file():
        shutil.copy2(saved_db, paths.db / "simulations.db")
    elif (paths.db / "simulations.db").is_file():
        # An empty pre-migration state is still a state worth restoring to.
        (paths.db / "simulations.db").unlink()
    saved_workspace = backup_dir / "workspace"
    if saved_workspace.is_dir():
        if paths.workspace.is_dir():
            shutil.rmtree(paths.workspace)
        shutil.copytree(saved_workspace, paths.workspace)


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
            rollback(args.rollback, args.data_dir)
            print(f"Restored from {args.rollback}")
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
