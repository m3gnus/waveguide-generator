"""SQLite persistence for simulation jobs and durable job events.

The three simulation tables deliberately retain the v1 names and columns from
``server/db.py:41-99`` so Phase 6 can copy rows without transforming them.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence

from server.platform.paths import data_paths
from server.solver.field_traces_store import (
    ArtifactMissing,
    FieldTraceArtifact,
    FieldTraceBundle,
    StoredFieldTraceArtifact,
    field_trace_artifact_dir,
    load_field_trace_bundle as load_field_trace_bundle_artifact,
    load_field_traces as load_field_traces_artifact,
    remove_field_trace_artifact,
    write_field_traces,
)


ALLOWED_STATUSES = frozenset({"queued", "running", "complete", "error", "cancelled"})
MESH_ARTIFACT_GRACE_MINUTES = 60
SUPPORTED_SCHEMA_VERSION = 5
ALLOWED_JOB_UPDATE_FIELDS = frozenset(
    {
        "status",
        "updated_at",
        "started_at",
        "completed_at",
        "progress",
        "stage",
        "stage_message",
        "error_message",
        "cancellation_requested",
        "has_results",
        "has_mesh_artifact",
        "mesh_stats_json",
        "label",
        "script_snapshot_json",
        "task_metadata_json",
    }
)


MetadataMergeStrategy = Callable[[Any, Any], Any]


def _ordered_string_set_union(stored: Any, requested: Any) -> list[str]:
    if stored is None:
        stored_values: list[str] = []
    elif isinstance(stored, list) and all(isinstance(value, str) for value in stored):
        stored_values = stored
    else:
        raise ValueError("Stored exported_files metadata must be a list of strings")
    if requested is None:
        requested_values: list[str] = []
    elif isinstance(requested, list) and all(
        isinstance(value, str) for value in requested
    ):
        requested_values = requested
    else:
        raise ValueError("exported_files metadata must be a list of strings")

    merged: list[str] = []
    seen: set[str] = set()
    for value in (*stored_values, *requested_values):
        if value not in seen:
            seen.add(value)
            merged.append(value)
    return merged


def _shallow_dict_merge(stored: Any, requested: Any) -> dict[str, Any]:
    if stored is None:
        stored_values: Mapping[str, Any] = {}
    elif isinstance(stored, Mapping):
        stored_values = stored
    else:
        raise ValueError("Stored auto_export_formats metadata must be an object")
    if requested is None:
        requested_values: Mapping[str, Any] = {}
    elif isinstance(requested, Mapping):
        requested_values = requested
    else:
        raise ValueError("auto_export_formats metadata must be an object")
    return {**stored_values, **requested_values}


# Metadata merge behavior is keyed explicitly rather than inferred from values.
# New collection-valued fields (for example tags) can opt in with one entry.
METADATA_MERGE_POLICIES: Mapping[str, MetadataMergeStrategy] = {
    "exported_files": _ordered_string_set_union,
    "auto_export_formats": _shallow_dict_merge,
}
logger = logging.getLogger(__name__)
_SAFE_LOG_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS simulation_jobs (
      id TEXT PRIMARY KEY,
      status TEXT NOT NULL CHECK (status IN ('queued','running','complete','error','cancelled')),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      queued_at TEXT NOT NULL,
      started_at TEXT,
      completed_at TEXT,
      progress REAL NOT NULL DEFAULT 0.0,
      stage TEXT,
      stage_message TEXT,
      error_message TEXT,
      cancellation_requested INTEGER NOT NULL DEFAULT 0,
      config_json TEXT NOT NULL,
      config_summary_json TEXT NOT NULL,
      has_results INTEGER NOT NULL DEFAULT 0,
      has_mesh_artifact INTEGER NOT NULL DEFAULT 0,
      mesh_stats_json TEXT,
      label TEXT,
      script_snapshot_json TEXT,
      task_metadata_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS simulation_results (
      job_id TEXT PRIMARY KEY,
      results_json TEXT NOT NULL,
      results_sha256 TEXT,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS simulation_artifacts (
      job_id TEXT PRIMARY KEY,
      msh_text TEXT,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    # Per-channel complex pressure bases (compressed NPZ) for multi-channel
    # imported jobs. These are what make post-solve recombination possible;
    # the results JSON keeps only magnitude and wrapped phase.
    """CREATE TABLE IF NOT EXISTS simulation_channel_bases (
      job_id TEXT PRIMARY KEY,
      bases_npz BLOB NOT NULL,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS simulation_radiation_impedance (
      job_id TEXT PRIMARY KEY,
      matrix_npz BLOB NOT NULL,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS simulation_field_traces (
      job_id TEXT PRIMARY KEY,
      version INTEGER NOT NULL,
      bytes INTEGER NOT NULL CHECK (bytes >= 0),
      path TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE INDEX IF NOT EXISTS idx_simulation_jobs_status_created
      ON simulation_jobs(status, created_at DESC)""",
    # The index above only serves a *filtered* list. The unfiltered list and
    # the WS snapshot both order by created_at with no status predicate, and
    # were scanning the table and building a temporary sort every time.
    """CREATE INDEX IF NOT EXISTS idx_simulation_jobs_created
      ON simulation_jobs(created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS job_identity (
      run_number INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT NOT NULL UNIQUE,
      parent_job_id TEXT NULL
    )""",
    # The UNIQUE constraint above creates SQLite's lookup index for every
    # simulation_jobs.id -> job_identity.job_id join; another index would only
    # duplicate it.
    """CREATE TABLE IF NOT EXISTS job_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      job_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )""",
    # job_events.id is INTEGER PRIMARY KEY AUTOINCREMENT, which is an alias for
    # the rowid, so an index on it duplicates the table's own B-tree. The
    # planner never chose it; every event insert and every retention delete
    # paid to maintain it. Dropped rather than merely not created, so existing
    # installations stop paying too.
    "DROP INDEX IF EXISTS idx_job_events_id",
)


def _now_iso() -> str:
    return datetime.now().isoformat()


class JobStore:
    """Thread-safe, transaction-per-operation v1-compatible job store.

    Locking, connection ownership, row conversion, and status validation follow
    v1 ``server/db.py:30-35,101-199,349-395``. Each public mutation commits all
    of its related changes together or rolls them all back.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        event_retention: int = 2048,
        job_logs_dir: str | Path | None = None,
        field_traces_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.event_retention = max(1, int(event_retention))
        self.job_logs_dir = (
            Path(job_logs_dir) if job_logs_dir is not None else self.db_path.parent / "job-logs"
        )
        self.field_traces_dir = (
            Path(field_traces_dir)
            if field_traces_dir is not None
            else self.db_path.parent / "field-traces"
        )
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._closed = False

    @classmethod
    def for_data_dir(cls, data_dir: str | Path, **kwargs: Any) -> "JobStore":
        """Place the DB in WG's namespaced ``db/`` directory."""

        paths = data_paths(data_dir)
        return cls(
            paths.db / "simulations.db",
            job_logs_dir=paths.logs / "jobs",
            field_traces_dir=paths.root / "field-traces",
            **kwargs,
        )

    def initialize(self) -> None:
        """Create the v1 migration targets plus additive v2 tables and identities."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._transaction() as conn:
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > SUPPORTED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database {self.db_path} was created by a newer version of "
                    "Waveguide Generator "
                    f"(schema {schema_version}); this version supports schemas up to "
                    f"{SUPPORTED_SCHEMA_VERSION}. Upgrade Waveguide Generator to open it."
                )
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(simulation_jobs)").fetchall()
            }
            if "mesh_stats_json" not in columns:
                conn.execute("ALTER TABLE simulation_jobs ADD COLUMN mesh_stats_json TEXT")
            if "script_snapshot_json" not in columns:
                conn.execute("ALTER TABLE simulation_jobs ADD COLUMN script_snapshot_json TEXT")
            if "task_metadata_json" not in columns:
                conn.execute("ALTER TABLE simulation_jobs ADD COLUMN task_metadata_json TEXT")
            result_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(simulation_results)").fetchall()
            }
            if "results_sha256" not in result_columns:
                conn.execute("ALTER TABLE simulation_results ADD COLUMN results_sha256 TEXT")
            self._backfill_job_identity(conn)
            conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA_VERSION}")

    def backfill_job_identity(self) -> None:
        """Assign identities to unnumbered jobs deterministically and idempotently.

        Ordering by ``(created_at, id)`` is deterministic, not true chronology:
        existing timestamps are naive local strings and can be ambiguous across
        a daylight-saving transition.
        """

        with self._lock, self._transaction() as conn:
            self._backfill_job_identity(conn)

    def create_job(
        self,
        job: Mapping[str, Any],
        *,
        initial_event: tuple[str, Mapping[str, Any]] | None = None,
        mesh_artifact: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert a queued job, optionally atomically with its event and mesh.

        The simulation-column mapping is a direct port of v1
        ``server/db.py:101-135``.
        """

        with self._lock, self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO simulation_jobs (
                  id, status, created_at, updated_at, queued_at,
                  started_at, completed_at, progress, stage, stage_message,
                  error_message, cancellation_requested, config_json,
                  config_summary_json, has_results, has_mesh_artifact, mesh_stats_json, label,
                  script_snapshot_json, task_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["status"],
                    job["created_at"],
                    job["updated_at"],
                    job["queued_at"],
                    job.get("started_at"),
                    job.get("completed_at"),
                    float(job.get("progress", 0.0)),
                    job.get("stage"),
                    job.get("stage_message"),
                    job.get("error_message"),
                    1 if job.get("cancellation_requested") else 0,
                    json.dumps(job["config_json"]),
                    json.dumps(job["config_summary_json"]),
                    1 if job.get("has_results") else 0,
                    1 if job.get("has_mesh_artifact") else 0,
                    json.dumps(job["mesh_stats"]) if job.get("mesh_stats") is not None else None,
                    job.get("label"),
                    json.dumps(job.get("script_snapshot"))
                    if job.get("script_snapshot") is not None
                    else None,
                    json.dumps(job.get("task_metadata") or {}),
                ),
            )
            conn.execute(
                "INSERT INTO job_identity (job_id, parent_job_id) VALUES (?, ?)",
                (job["id"], job.get("parent_job_id")),
            )
            if mesh_artifact is not None:
                conn.execute(
                    "INSERT INTO simulation_artifacts (job_id, msh_text) VALUES (?, ?)",
                    (job["id"], mesh_artifact),
                )
            if initial_event is None:
                return None
            return self._append_event(conn, job["id"], *initial_event)

    def update_job(self, job_id: str, **fields: Any) -> bool:
        """Update allowed job columns, matching v1 ``server/db.py:137-161``."""

        if not fields:
            return False
        with self._lock, self._transaction() as conn:
            return self._update_job(conn, job_id, fields)

    def update_job_with_event(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]:
        """Persist a visible state transition and its cursor atomically."""

        with self._lock, self._transaction() as conn:
            changed = self._update_job(conn, job_id, fields)
            event = self._append_event(conn, job_id, event_type, payload) if changed else None
            return changed, event

    def mutate_job_metadata(
        self,
        job_id: str,
        changes: Mapping[str, Any],
        *,
        column_fields: Mapping[str, Any] | None = None,
        event_type: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Merge metadata and related columns in one locked transaction.

        Collection-valued metadata uses the explicit module-level policies;
        every other key is a last-write-wins replacement. When a metadata
        event carries the conventional ``changed`` object, policy-merged keys
        report the value that was actually stored rather than the stale value
        requested by the caller.
        """

        metadata_changes = dict(changes)
        values = dict(column_fields or {})
        if "task_metadata_json" in values:
            raise ValueError("task_metadata_json must be changed through metadata changes")

        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT task_metadata_json FROM simulation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False, None

            decoded = json.loads(row["task_metadata_json"] or "{}")
            if not isinstance(decoded, Mapping):
                raise ValueError("Stored task metadata must be a JSON object")
            metadata = dict(decoded)
            stored_changes: dict[str, Any] = {}
            for key, requested in metadata_changes.items():
                strategy = METADATA_MERGE_POLICIES.get(key)
                stored_value = (
                    strategy(metadata.get(key), requested)
                    if strategy is not None
                    else requested
                )
                metadata[key] = stored_value
                stored_changes[key] = stored_value

            if metadata_changes:
                values["task_metadata_json"] = json.dumps(metadata)
            if not values:
                return False, None

            changed = self._update_job(conn, job_id, values)
            if not changed:
                return False, None

            event: dict[str, Any] | None = None
            if event_type is not None:
                event_payload = dict(payload or {})
                requested_event_changes = event_payload.get("changed")
                if isinstance(requested_event_changes, Mapping):
                    actual_event_changes = dict(requested_event_changes)
                    for key in METADATA_MERGE_POLICIES:
                        if key in stored_changes:
                            actual_event_changes[key] = stored_changes[key]
                    event_payload["changed"] = actual_event_changes
                event = self._append_event(conn, job_id, event_type, event_payload)
            return True, event

    def start_job(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically transition a job from queued to running."""

        values = dict(fields)
        unsupported = sorted(set(values) - ALLOWED_JOB_UPDATE_FIELDS)
        if unsupported:
            raise ValueError(f"Unsupported job update field(s): {', '.join(unsupported)}")
        if values.get("status") != "running":
            raise ValueError("A started job must transition to status 'running'")
        values["updated_at"] = _now_iso()
        assignments = [f"{key} = ?" for key in values]
        params = [self._db_value(key, value) for key, value in values.items()]
        with self._lock, self._transaction() as conn:
            changed = conn.execute(
                f"""UPDATE simulation_jobs SET {', '.join(assignments)}
                    WHERE id = ? AND status = 'queued'""",
                [*params, job_id],
            ).rowcount
            if changed <= 0:
                return None
            return self._append_event(conn, job_id, "started", payload)

    def persist_runtime_update(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        *,
        stage_payload: Mapping[str, Any] | None = None,
        log_lines: Sequence[str] = (),
        expected_log_size: int | None = None,
        max_log_lines: int = 200,
        max_log_chars: int = 32_000,
        max_log_event_chars: int = 2_000,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Persist one coalesced runtime checkpoint and its visible events.

        Native solvers can report a callback for every frequency.  Combining
        the latest stage/progress values and all log lines in one transaction
        avoids opening SQLite and the log file for every callback while keeping
        the event cursor consistent with the durable job row.

        The log file is appended and fsynced before its metadata/event becomes
        visible. The caller keeps the batch paired with ``expected_log_size``
        unchanged until it observes success, so a retry can recognize those
        exact bytes on disk without appending them twice. An unexpected file
        size or suffix emits a warning and rebases the append at the current
        end of file instead of permanently blocking runtime persistence.

        Log event ``lines`` is the authoritative line-preserving delta. The
        single-line ``chunk`` remains for older clients and names the newest
        line, never a newline-joined pseudo-line.
        """

        values = dict(fields)
        full_lines = [
            logical_line
            for message in log_lines
            for logical_line in str(message).replace("\r", "").split("\n")
        ]
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                """SELECT status, cancellation_requested, task_metadata_json
                   FROM simulation_jobs WHERE id = ?""",
                (job_id,),
            ).fetchone()
            if row is None:
                return False, []

            active = row["status"] in {"queued", "running"}
            cancellation_requested = bool(row["cancellation_requested"])
            if not active:
                # A terminal transition owns the durable row and event cursor.
                # A failure handler may still be draining its lower-priority
                # file tail, but stale stage fields must not follow the
                # terminal event or rewrite terminal metadata.
                values = {}
                stage_payload = None
            elif cancellation_requested:
                # A cancellation stage already owns the visible state. Logs
                # that raced with the request are still durable, but stale
                # progress must not restore the prior solve stage.
                values = {}
                stage_payload = None

            event_lines: list[str] = []
            if full_lines:
                path = self._job_log_path(job_id)
                self._ensure_job_logs_dir()
                batch = "".join(f"{line}\n" for line in full_lines).encode("utf-8")
                # One stat, not the two-to-four that exists()+stat() pairs cost.
                # This runs every 150 ms during a solve, alongside an fsync that
                # is genuinely load-bearing, so the syscalls around it are worth
                # counting.
                try:
                    actual = path.stat().st_size
                except FileNotFoundError:
                    actual = 0
                expected = (
                    int(expected_log_size) if expected_log_size is not None else actual
                )
                already_appended = False
                mismatch = actual < expected
                if not mismatch and actual > expected:
                    try:
                        with path.open("rb") as handle:
                            handle.seek(expected)
                            suffix = handle.read()
                    except FileNotFoundError:
                        actual = 0
                        mismatch = True
                    else:
                        if suffix == batch:
                            already_appended = True
                        else:
                            mismatch = True
                if mismatch:
                    logger.warning(
                        "Job log integrity mismatch for job %s: expected batch "
                        "at offset %d, observed size %d; resynchronizing at "
                        "current EOF",
                        job_id,
                        expected,
                        actual,
                    )
                    try:
                        resynced_offset = path.stat().st_size
                    except FileNotFoundError:
                        resynced_offset = 0
                    already_appended = False
                    if resynced_offset >= len(batch):
                        try:
                            with path.open("rb") as handle:
                                handle.seek(resynced_offset - len(batch))
                                already_appended = handle.read() == batch
                        except FileNotFoundError:
                            resynced_offset = 0
                            already_appended = False
                if not already_appended:
                    try:
                        with path.open("ab") as handle:
                            handle.write(batch)
                            handle.flush()
                            # Load-bearing, not incidental: the durable file
                            # length is what makes a retry after a failed
                            # commit recognise its own batch instead of
                            # writing it twice.
                            os.fsync(handle.fileno())
                    except FileNotFoundError:
                        self._job_logs_dir_ready = False
                        raise

                if not active:
                    return False, []

                metadata = json.loads(row["task_metadata_json"] or "{}")
                tail = [str(line) for line in metadata.get("log_tail") or []]
                bounded = [line[-max_log_event_chars:] for line in full_lines]
                tail.extend(bounded)
                tail = tail[-max_log_lines:]
                tail_chars = sum(len(line) + 1 for line in tail)
                while tail and tail_chars > max_log_chars:
                    tail_chars -= len(tail.pop(0)) + 1
                metadata["log_tail"] = tail
                values["task_metadata_json"] = json.dumps(metadata)
                remaining = max_log_event_chars
                reversed_lines: list[str] = []
                for line in reversed(bounded):
                    if remaining <= 0:
                        break
                    if len(line) > remaining:
                        if reversed_lines:
                            break
                        line = line[-remaining:]
                    reversed_lines.append(line)
                    remaining -= len(line)
                event_lines = list(reversed(reversed_lines))

            if not values:
                return False, []
            changed = self._update_job(conn, job_id, values)
            if not changed:
                return False, []

            events: list[dict[str, Any]] = []
            if stage_payload is not None:
                events.append(self._append_event(conn, job_id, "stage", stage_payload))
            if event_lines:
                events.append(
                    self._append_event(
                        conn,
                        job_id,
                        "log",
                        {"chunk": event_lines[-1], "lines": event_lines},
                    )
                )
            return True, events

    def job_log_size(self, job_id: str) -> int:
        """Return the byte offset used to make the next batch append idempotent."""

        with self._lock:
            try:
                return self._job_log_path(job_id).stat().st_size
            except FileNotFoundError:
                return 0

    def request_cancellation(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically request cancellation only while a job remains running."""

        values = dict(fields)
        unsupported = sorted(set(values) - ALLOWED_JOB_UPDATE_FIELDS)
        if unsupported:
            raise ValueError(f"Unsupported job update field(s): {', '.join(unsupported)}")
        values["updated_at"] = _now_iso()
        assignments = [f"{key} = ?" for key in values]
        params = [self._db_value(key, value) for key, value in values.items()]
        with self._lock, self._transaction() as conn:
            changed = conn.execute(
                f"""UPDATE simulation_jobs SET {', '.join(assignments)}
                    WHERE id = ? AND status = 'running' AND cancellation_requested = 0""",
                [*params, job_id],
            ).rowcount
            if changed <= 0:
                return None
            return self._append_event(conn, job_id, "stage", payload)

    def get_job_row(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """SELECT simulation_jobs.*, job_identity.run_number,
                          job_identity.parent_job_id
                   FROM simulation_jobs
                   JOIN job_identity ON job_identity.job_id = simulation_jobs.id
                   WHERE simulation_jobs.id = ?""",
                (job_id,),
            ).fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs(
        self,
        statuses: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List newest first with v1 status filtering/pagination semantics."""

        where = ""
        args: list[Any] = []
        if statuses:
            invalid = sorted(set(statuses) - ALLOWED_STATUSES)
            if invalid:
                raise ValueError(f"Unsupported status: {invalid[0]}")
            placeholders = ",".join("?" for _ in statuses)
            where = f"WHERE simulation_jobs.status IN ({placeholders})"
            args.extend(statuses)
        with self._lock, self._connection() as conn:
            # The join mirrors the row query below. Counting without it would
            # let a job with no identity row inflate the total while never
            # appearing in a page, which reads as a pagination bug.
            total = int(
                conn.execute(
                    f"""SELECT COUNT(*) AS c
                        FROM simulation_jobs
                        JOIN job_identity ON job_identity.job_id = simulation_jobs.id
                        {where}""",
                    args,
                ).fetchone()["c"]
            )
            rows = conn.execute(
                f"""
                SELECT simulation_jobs.*, job_identity.run_number,
                       job_identity.parent_job_id
                FROM simulation_jobs
                JOIN job_identity ON job_identity.job_id = simulation_jobs.id
                {where}
                ORDER BY simulation_jobs.created_at DESC, job_identity.run_number DESC
                LIMIT ? OFFSET ?
                """,
                [*args, int(limit), int(offset)],
            ).fetchall()
        return [self._row_to_job(row) for row in rows], total

    def snapshot_jobs(self) -> tuple[list[dict[str, Any]], int]:
        """Read the WS snapshot and matching durable cursor consistently."""

        with self._lock, self._connection() as conn:
            # One read transaction, so the rows and the cursor are the same
            # view of the database. It must be ended explicitly: the connection
            # is now long-lived, and an open read transaction left behind would
            # pin the WAL and stop it ever being checkpointed.
            conn.execute("BEGIN")
            try:
                cursor = self._event_cursor(conn)
                rows = conn.execute(
                    """SELECT simulation_jobs.*, job_identity.run_number,
                              job_identity.parent_job_id
                       FROM simulation_jobs
                       JOIN job_identity ON job_identity.job_id = simulation_jobs.id
                       ORDER BY simulation_jobs.created_at DESC, job_identity.run_number DESC"""
                ).fetchall()
            finally:
                conn.rollback()
            return [self._row_to_job(row) for row in rows], cursor

    def cancellation_state(self, job_id: str) -> tuple[str, bool] | None:
        """Just the two columns a cancellation checkpoint needs.

        ``get_job_row`` is ``SELECT *`` and then parses four JSON columns, two
        of which hold a complete copy of the design. The solver calls this once
        per frequency from its own thread -- up to 401 times a sweep, each
        taking the store lock the event loop also wants -- to answer a
        question that is two booleans wide.
        """

        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT status, cancellation_requested FROM simulation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["status"]), bool(row["cancellation_requested"])

    def store_results(self, job_id: str, results: Mapping[str, Any]) -> None:
        """Upsert results and availability as in v1 ``server/db.py:201-221``."""

        with self._lock, self._transaction() as conn:
            if not self._job_exists(conn, job_id):
                return
            self._upsert_results(conn, job_id, results)

    def complete_job(
        self,
        job_id: str,
        results: Mapping[str, Any],
        fields: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Persist results before the complete transition, atomically.

        This preserves the v1 ordering at ``server/services/simulation_runner.py:540-567``
        while making a completed row with missing results impossible.
        """

        with self._lock, self._transaction() as conn:
            values = {**fields, "has_results": True, "updated_at": _now_iso()}
            unsupported = sorted(set(values) - ALLOWED_JOB_UPDATE_FIELDS)
            if unsupported:
                raise ValueError(f"Unsupported job update field(s): {', '.join(unsupported)}")
            if "status" in values and values["status"] not in ALLOWED_STATUSES:
                raise ValueError(f"Unsupported status: {values['status']}")
            assignments = [f"{key} = ?" for key in values]
            params = [self._db_value(key, value) for key, value in values.items()]
            changed = conn.execute(
                f"""UPDATE simulation_jobs SET {', '.join(assignments)}
                    WHERE id = ? AND status = 'running' AND cancellation_requested = 0""",
                [*params, job_id],
            ).rowcount
            if changed <= 0:
                return None
            self._upsert_results(conn, job_id, results)
            return self._append_event(conn, job_id, "completed", payload)

    def get_job_log(self, job_id: str) -> str:
        with self._lock:
            try:
                return self._job_log_path(job_id).read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""

    def get_results(self, job_id: str) -> dict[str, Any] | None:
        text = self.get_results_text(job_id)
        return json.loads(text) if text is not None else None

    def get_results_text(self, job_id: str) -> str | None:
        payload = self.get_results_payload(job_id)
        return payload[0] if payload is not None else None

    def get_results_payload(self, job_id: str) -> tuple[str, str] | None:
        """The stored results exactly as they were written, without parsing.

        A finished sweep's results run to megabytes, and the HTTP route only
        ever hands them straight back to the browser. Parsing them into Python
        objects so that FastAPI can validate them, re-serialise them and then
        JSON-encode them again is four full walks of the same data, all on the
        event loop -- long enough to stall the preview socket and the job
        events. The bytes in the database are already the answer.
        """

        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    """SELECT results_json, results_sha256
                       FROM simulation_results WHERE job_id = ?""",
                    (job_id,),
                ).fetchone()
            if row is None:
                return None

            text = str(row["results_json"])
            digest = row["results_sha256"]
            if digest is not None and '"field_plane_available"' in text:
                return text, str(digest)

            if '"field_plane_available"' not in text:
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, dict):
                    metadata = decoded.get("metadata")
                    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
                    metadata.update(
                        {
                            "field_plane_available": False,
                            "field_trace_bytes": None,
                            "unavailable_reason": "solve_predates_traces",
                        }
                    )
                    decoded["metadata"] = metadata
                    text = json.dumps(decoded)

            digest = sha256(text.encode("utf-8")).hexdigest()
            with self._transaction() as conn:
                conn.execute(
                    """UPDATE simulation_results
                       SET results_json = ?, results_sha256 = ?
                       WHERE job_id = ?""",
                    (text, digest, job_id),
                )
            return text, digest

    def store_mesh_artifact(self, job_id: str, msh_text: str) -> None:
        """Upsert original MSH text as in v1 ``server/db.py:233-253``."""

        with self._lock, self._transaction() as conn:
            if not self._job_exists(conn, job_id):
                return
            conn.execute(
                """
                INSERT INTO simulation_artifacts (job_id, msh_text) VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET msh_text = excluded.msh_text
                """,
                (job_id, msh_text),
            )
            conn.execute(
                "UPDATE simulation_jobs SET has_mesh_artifact = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), job_id),
            )

    def get_mesh_artifact(self, job_id: str) -> str | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT msh_text FROM simulation_artifacts WHERE job_id = ?", (job_id,)
            ).fetchone()
            return row["msh_text"] if row else None

    def store_channel_bases(self, job_id: str, bases_npz: bytes) -> None:
        with self._lock, self._transaction() as conn:
            if not self._job_exists(conn, job_id):
                return
            conn.execute(
                """
                INSERT INTO simulation_channel_bases (job_id, bases_npz) VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET bases_npz = excluded.bases_npz
                """,
                (job_id, bases_npz),
            )

    def get_channel_bases(self, job_id: str) -> bytes | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT bases_npz FROM simulation_channel_bases WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return bytes(row["bases_npz"]) if row else None

    def store_radiation_impedance(self, job_id: str, matrix_npz: bytes) -> None:
        with self._lock, self._transaction() as conn:
            if not self._job_exists(conn, job_id):
                return
            conn.execute(
                """
                INSERT INTO simulation_radiation_impedance (job_id, matrix_npz)
                VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET matrix_npz = excluded.matrix_npz
                """,
                (job_id, matrix_npz),
            )
            conn.execute(
                """UPDATE simulation_jobs
                   SET task_metadata_json = json_set(
                         COALESCE(task_metadata_json, '{}'),
                         '$.has_radiation_impedance_artifact', json('true'),
                         '$.radiation_impedance_artifact_bytes', ?
                       ),
                       updated_at = ?
                   WHERE id = ?""",
                (len(matrix_npz), _now_iso(), job_id),
            )

    def get_radiation_impedance(self, job_id: str) -> bytes | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT matrix_npz FROM simulation_radiation_impedance WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return bytes(row["matrix_npz"]) if row else None

    def delete_radiation_impedance(self, job_id: str) -> bool:
        """Delete a non-durable matrix and clear its advertised availability."""

        with self._lock, self._transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM simulation_radiation_impedance WHERE job_id = ?",
                (job_id,),
            ).rowcount
            conn.execute(
                """UPDATE simulation_jobs
                   SET task_metadata_json = json_set(
                         COALESCE(task_metadata_json, '{}'),
                         '$.has_radiation_impedance_artifact', json('false'),
                         '$.radiation_impedance_artifact_bytes', json('null')
                       ),
                       updated_at = ?
                   WHERE id = ?""",
                (_now_iso(), job_id),
            )
            return bool(deleted)

    def store_field_traces(
        self, job_id: str, artifact: FieldTraceArtifact
    ) -> StoredFieldTraceArtifact | None:
        """Publish a sidecar and register its small metadata row."""

        with self._lock, self._connection() as conn:
            if not self._job_exists(conn, job_id):
                return None
            existing = conn.execute(
                "SELECT 1 FROM simulation_field_traces WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if existing is not None:
            raise ValueError(f"field traces are already registered for job {job_id}")

        target = field_trace_artifact_dir(job_id, artifact_root=self.field_traces_dir)
        if target.exists():
            remove_field_trace_artifact(target)
        stored = write_field_traces(
            job_id,
            artifact,
            artifact_root=self.field_traces_dir,
        )
        relative_path = str(stored.path.relative_to(self.field_traces_dir.parent))
        try:
            with self._lock, self._transaction() as conn:
                if not self._job_exists(conn, job_id):
                    remove_field_trace_artifact(stored.path)
                    return None
                conn.execute(
                    """
                    INSERT INTO simulation_field_traces
                      (job_id, version, bytes, path, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        stored.version,
                        stored.bytes,
                        relative_path,
                        _now_iso(),
                    ),
                )
                conn.execute(
                    """UPDATE simulation_jobs
                       SET task_metadata_json = json_set(
                             COALESCE(task_metadata_json, '{}'),
                             '$.field_plane_available', json('true'),
                             '$.field_trace_bytes', ?,
                             '$.unavailable_reason', json('null')
                           ),
                           updated_at = ?
                       WHERE id = ?""",
                    (stored.bytes, _now_iso(), job_id),
                )
        except Exception:
            remove_field_trace_artifact(stored.path)
            raise
        return stored

    def get_field_trace_record(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """SELECT job_id, version, bytes, path, created_at
                   FROM simulation_field_traces WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def load_field_traces(
        self, job_id: str, frequency_index: int, channel_id: str
    ) -> tuple[Any, ...]:
        """Load one retained slice using the registered sidecar path."""

        record = self.get_field_trace_record(job_id)
        if record is None:
            raise ArtifactMissing(f"field-trace artifact is missing for job {job_id}")
        artifact_path = self._field_trace_path(str(record["path"]))
        return load_field_traces_artifact(
            job_id,
            frequency_index,
            channel_id,
            artifact_dir=artifact_path,
        )

    def load_field_trace_bundle(self, job_id: str) -> FieldTraceBundle:
        """Load every retained frequency and channel from the sidecar at once."""

        record = self.get_field_trace_record(job_id)
        if record is None:
            raise ArtifactMissing(f"field-trace artifact is missing for job {job_id}")
        return load_field_trace_bundle_artifact(
            job_id,
            artifact_dir=self._field_trace_path(str(record["path"])),
        )

    def delete_field_traces(self, job_id: str) -> bool:
        """Delete the metadata row first, then remove its sidecar directory."""

        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT path FROM simulation_field_traces WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            deleted = conn.execute(
                "DELETE FROM simulation_field_traces WHERE job_id = ?",
                (job_id,),
            ).rowcount
            conn.execute(
                """UPDATE simulation_jobs
                   SET task_metadata_json = json_set(
                         COALESCE(task_metadata_json, '{}'),
                         '$.field_plane_available', json('false'),
                         '$.field_trace_bytes', json('null'),
                         '$.unavailable_reason', 'artifact_pruned'
                       ),
                       updated_at = ?
                   WHERE id = ?""",
                (_now_iso(), job_id),
            )
        path = self._field_trace_path(str(row["path"])) if row is not None else None
        if path is not None:
            self._remove_field_trace_path(job_id, path)
        return bool(deleted)

    def delete_job_with_event(self, job_id: str) -> tuple[bool, dict[str, Any] | None]:
        """Delete one row and retain a terminal deleted event."""

        with self._lock, self._transaction() as conn:
            trace_rows = conn.execute(
                "SELECT path FROM simulation_field_traces WHERE job_id = ?",
                (job_id,),
            ).fetchall()
            conn.execute(
                "DELETE FROM simulation_field_traces WHERE job_id = ?",
                (job_id,),
            )
            cur = conn.execute("DELETE FROM simulation_jobs WHERE id = ?", (job_id,))
            if cur.rowcount <= 0:
                return False, None
            event = self._append_event(conn, job_id, "deleted", {})
        for row in trace_rows:
            self._remove_field_trace_path(job_id, self._field_trace_path(str(row["path"])))
        self._delete_job_logs([job_id])
        return True, event

    def delete_jobs_by_status_with_events(
        self, statuses: Sequence[str]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Atomically clear terminal rows, porting v1 ``server/db.py:270-292``."""

        normalized = list(dict.fromkeys(str(value).strip() for value in statuses if str(value).strip()))
        invalid = sorted(set(normalized) - ALLOWED_STATUSES)
        if invalid:
            raise ValueError(f"Unsupported status: {invalid[0]}")
        if not normalized:
            return [], []
        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self._transaction() as conn:
            rows = conn.execute(
                f"SELECT id FROM simulation_jobs WHERE status IN ({placeholders})", normalized
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if not ids:
                return [], []
            id_placeholders = ",".join("?" for _ in ids)
            trace_rows = conn.execute(
                f"SELECT job_id, path FROM simulation_field_traces "
                f"WHERE job_id IN ({id_placeholders})",
                ids,
            ).fetchall()
            conn.execute(
                f"DELETE FROM simulation_field_traces "
                f"WHERE job_id IN ({id_placeholders})",
                ids,
            )
            conn.execute(f"DELETE FROM simulation_jobs WHERE id IN ({id_placeholders})", ids)
            events = [self._append_event(conn, job_id, "deleted", {}) for job_id in ids]
        for row in trace_rows:
            self._remove_field_trace_path(
                str(row["job_id"]), self._field_trace_path(str(row["path"]))
            )
        self._delete_job_logs(ids)
        return ids, events

    def recover_on_startup(
        self, restart_error_message: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fail running orphans and return queued rows in FIFO order.

        This is the startup rule from v1 ``server/db.py:294-314`` and
        ``server/services/job_runtime.py:629-686``.
        """

        now = _now_iso()
        failed_events: list[dict[str, Any]] = []
        trace_rows: list[sqlite3.Row] = []
        with self._lock, self._transaction() as conn:
            running = conn.execute(
                "SELECT id FROM simulation_jobs WHERE status = 'running' ORDER BY created_at ASC"
            ).fetchall()
            conn.execute(
                """
                UPDATE simulation_jobs
                SET status = 'error', stage = 'error', stage_message = 'Simulation failed',
                    error_message = ?, completed_at = COALESCE(completed_at, ?), updated_at = ?
                WHERE status = 'running'
                """,
                (restart_error_message, now, now),
            )
            running_ids = [str(row["id"]) for row in running]
            if running_ids:
                placeholders = ",".join("?" for _ in running_ids)
                trace_rows = conn.execute(
                    f"SELECT job_id, path FROM simulation_field_traces "
                    f"WHERE job_id IN ({placeholders})",
                    running_ids,
                ).fetchall()
                conn.execute(
                    f"DELETE FROM simulation_field_traces "
                    f"WHERE job_id IN ({placeholders})",
                    running_ids,
                )
                conn.execute(
                    f"DELETE FROM simulation_channel_bases "
                    f"WHERE job_id IN ({placeholders})",
                    running_ids,
                )
                conn.execute(
                    f"DELETE FROM simulation_radiation_impedance "
                    f"WHERE job_id IN ({placeholders})",
                    running_ids,
                )
                conn.execute(
                    f"""UPDATE simulation_jobs
                        SET task_metadata_json = json_set(
                              COALESCE(task_metadata_json, '{{}}'),
                              '$.has_radiation_impedance_artifact', json('false'),
                              '$.radiation_impedance_artifact_bytes', json('null'),
                              '$.field_plane_available', json('false'),
                              '$.field_trace_bytes', json('null'),
                              '$.unavailable_reason', 'artifact_pruned'
                            )
                        WHERE id IN ({placeholders})""",
                    running_ids,
                )
            for row in running:
                failed_events.append(
                    self._append_event(
                        conn,
                        str(row["id"]),
                        "failed",
                        {"message": restart_error_message, "recovered": True},
                    )
                )
            queued = conn.execute(
                """SELECT simulation_jobs.*, job_identity.run_number,
                          job_identity.parent_job_id
                   FROM simulation_jobs
                   JOIN job_identity ON job_identity.job_id = simulation_jobs.id
                   WHERE simulation_jobs.status = 'queued'
                   ORDER BY simulation_jobs.created_at ASC, job_identity.run_number ASC"""
            ).fetchall()
            queued_jobs = [self._row_to_job(row) for row in queued]
        for row in trace_rows:
            self._remove_field_trace_path(
                str(row["job_id"]), self._field_trace_path(str(row["path"]))
            )
        return queued_jobs, failed_events

    def prune_terminal_jobs(
        self,
        retention_days: int = 30,
        max_terminal_jobs: int = 1000,
        *,
        mesh_grace_minutes: int = MESH_ARTIFACT_GRACE_MINUTES,
    ) -> int:
        """Prune result payloads by age/count while retaining every job record.

        This deliberately breaks v1 parity: records are the durable run index,
        while only the heavier result tier remains subject to retention.
        """

        removed_ids, _affected_ids, _events = self._prune_terminal_jobs(
            retention_days=retention_days,
            max_terminal_jobs=max_terminal_jobs,
            mesh_grace_minutes=mesh_grace_minutes,
            emit_events=False,
        )
        return len(removed_ids)

    def prune_terminal_jobs_with_events(
        self,
        retention_days: int = 30,
        max_terminal_jobs: int = 1000,
        *,
        mesh_grace_minutes: int = MESH_ARTIFACT_GRACE_MINUTES,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Prune results and retain matching WS availability events atomically.

        Startup uses :meth:`prune_terminal_jobs` before clients can subscribe.
        Runtime pruning, however, must publish every affected id so an already
        connected client can converge from events alone.
        """

        _removed_ids, affected_ids, events = self._prune_terminal_jobs(
            retention_days=retention_days,
            max_terminal_jobs=max_terminal_jobs,
            mesh_grace_minutes=mesh_grace_minutes,
            emit_events=True,
        )
        return affected_ids, events

    def _prune_terminal_jobs(
        self,
        *,
        retention_days: int,
        max_terminal_jobs: int,
        mesh_grace_minutes: int,
        emit_events: bool,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """Shared retention transaction for silent startup and live pruning."""

        cutoff = (datetime.now() - timedelta(days=int(retention_days))).isoformat()
        mesh_cutoff = (
            datetime.now() - timedelta(minutes=int(mesh_grace_minutes))
        ).isoformat()
        removed_ids: list[str] = []
        events: list[dict[str, Any]] = []
        removed_trace_rows: list[sqlite3.Row] = []
        with self._lock, self._transaction() as conn:
            aged = conn.execute(
                """SELECT id FROM simulation_jobs
                   WHERE status IN ('complete', 'error', 'cancelled')
                     AND has_results = 1
                     AND COALESCE(CAST(json_extract(task_metadata_json, '$.rating') AS INTEGER), 0) <= 0
                     AND COALESCE(
                       json_extract(task_metadata_json, '$.imported_at'),
                       completed_at, created_at
                     ) < ?""",
                (cutoff,),
            ).fetchall()
            removed_ids.extend(str(row["id"]) for row in aged)
            # Ask SQLite for the overflow rather than materialising every
            # retained result and slicing in Python. Rated payloads do not
            # count toward the cap because they are exempt from retention.
            overflow = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM simulation_jobs
                    WHERE status IN ('complete', 'error', 'cancelled')
                      AND has_results = 1
                      AND COALESCE(CAST(json_extract(task_metadata_json, '$.rating') AS INTEGER), 0) <= 0
                      AND COALESCE(
                        json_extract(task_metadata_json, '$.imported_at'),
                        completed_at, created_at
                      ) >= ?
                    ORDER BY COALESCE(
                      json_extract(task_metadata_json, '$.imported_at'),
                      completed_at, created_at
                    ) DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (cutoff, int(max_terminal_jobs)),
                ).fetchall()
            ]
            removed_ids.extend(str(value) for value in overflow)
            removed_ids = list(dict.fromkeys(removed_ids))
            failed_channel_base_rows = conn.execute(
                """SELECT simulation_jobs.id
                   FROM simulation_channel_bases
                   JOIN simulation_jobs
                     ON simulation_jobs.id = simulation_channel_bases.job_id
                   WHERE simulation_jobs.status IN ('error', 'cancelled')"""
            ).fetchall()
            channel_base_ids = list(
                dict.fromkeys(
                    [
                        *removed_ids,
                        *(str(row["id"]) for row in failed_channel_base_rows),
                    ]
                )
            )
            standalone_radiation_rows = conn.execute(
                """SELECT simulation_jobs.id
                   FROM simulation_radiation_impedance
                   JOIN simulation_jobs
                     ON simulation_jobs.id = simulation_radiation_impedance.job_id
                   WHERE simulation_jobs.status IN ('complete', 'error', 'cancelled')
                     AND COALESCE(CAST(json_extract(
                           simulation_jobs.task_metadata_json, '$.rating'
                         ) AS INTEGER), 0) <= 0
                     AND (
                       simulation_jobs.has_results = 0
                       OR COALESCE(
                         json_extract(
                           simulation_jobs.task_metadata_json, '$.imported_at'
                         ),
                         simulation_jobs.completed_at,
                         simulation_jobs.created_at
                       ) < ?
                     )""",
                (cutoff,),
            ).fetchall()
            removed_radiation_ids: list[str] = []
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                removed_radiation_ids = [
                    str(row["job_id"])
                    for row in conn.execute(
                        f"SELECT job_id FROM simulation_radiation_impedance "
                        f"WHERE job_id IN ({placeholders})",
                        removed_ids,
                    ).fetchall()
                ]
            radiation_ids = list(
                dict.fromkeys(
                    [
                        *removed_radiation_ids,
                        *(str(row["id"]) for row in standalone_radiation_rows),
                    ]
                )
            )
            trace_rows = conn.execute(
                """SELECT simulation_field_traces.job_id,
                          simulation_field_traces.path,
                          simulation_jobs.status,
                          simulation_jobs.has_results
                   FROM simulation_field_traces
                   JOIN simulation_jobs
                     ON simulation_jobs.id = simulation_field_traces.job_id"""
            ).fetchall()
            removed_set = set(removed_ids)
            removed_trace_rows = [
                row
                for row in trace_rows
                if str(row["job_id"]) in removed_set
                or str(row["status"]) in {"error", "cancelled"}
                or (
                    str(row["status"]) == "complete"
                    and not bool(row["has_results"])
                )
            ]
            trace_ids = list(
                dict.fromkeys(str(row["job_id"]) for row in removed_trace_rows)
            )
            mesh_rows = conn.execute(
                """SELECT id FROM simulation_jobs
                   WHERE status IN ('complete', 'error', 'cancelled')
                     AND has_mesh_artifact = 1
                     AND COALESCE(CAST(json_extract(task_metadata_json, '$.rating') AS INTEGER), 0) <= 0
                     AND (
                       json_extract(task_metadata_json, '$.mesh_artifact_file') IS NOT NULL
                       OR COALESCE(
                         json_extract(task_metadata_json, '$.imported_at'),
                         completed_at, created_at
                       ) < ?
                     )""",
                (mesh_cutoff,),
            ).fetchall()
            mesh_ids = [str(row["id"]) for row in mesh_rows]
            deleted_results = 0
            discarded_at = _now_iso()
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                cur = conn.execute(
                    f"DELETE FROM simulation_results WHERE job_id IN ({placeholders})",
                    removed_ids,
                )
                deleted_results = int(cur.rowcount or 0)
                conn.execute(
                    f"UPDATE simulation_jobs SET has_results = 0, "
                    "task_metadata_json = json_set(COALESCE(task_metadata_json, '{}'), "
                    "'$.results_discarded_at', ?) "
                    f"WHERE id IN ({placeholders})",
                    [discarded_at, *removed_ids],
                )
            if channel_base_ids:
                placeholders = ",".join("?" for _ in channel_base_ids)
                conn.execute(
                    f"DELETE FROM simulation_channel_bases "
                    f"WHERE job_id IN ({placeholders})",
                    channel_base_ids,
                )
            if radiation_ids:
                placeholders = ",".join("?" for _ in radiation_ids)
                conn.execute(
                    f"DELETE FROM simulation_radiation_impedance "
                    f"WHERE job_id IN ({placeholders})",
                    radiation_ids,
                )
                conn.execute(
                    f"""UPDATE simulation_jobs
                        SET task_metadata_json = json_set(
                              COALESCE(task_metadata_json, '{{}}'),
                              '$.has_radiation_impedance_artifact', json('false'),
                              '$.radiation_impedance_artifact_bytes', json('null')
                            )
                        WHERE id IN ({placeholders})""",
                    radiation_ids,
                )
            if trace_ids:
                placeholders = ",".join("?" for _ in trace_ids)
                conn.execute(
                    f"DELETE FROM simulation_field_traces "
                    f"WHERE job_id IN ({placeholders})",
                    trace_ids,
                )
                conn.execute(
                    f"""UPDATE simulation_jobs
                        SET task_metadata_json = json_set(
                              COALESCE(task_metadata_json, '{{}}'),
                              '$.field_plane_available', json('false'),
                              '$.field_trace_bytes', json('null'),
                              '$.unavailable_reason', 'artifact_pruned'
                            )
                        WHERE id IN ({placeholders})""",
                    trace_ids,
                )
            if mesh_ids:
                placeholders = ",".join("?" for _ in mesh_ids)
                conn.execute(
                    f"DELETE FROM simulation_artifacts WHERE job_id IN ({placeholders})",
                    mesh_ids,
                )
                conn.execute(
                    f"UPDATE simulation_jobs SET has_mesh_artifact = 0, "
                    "task_metadata_json = json_set(COALESCE(task_metadata_json, '{}'), "
                    "'$.mesh_discarded_at', ?) "
                    f"WHERE id IN ({placeholders})",
                    [discarded_at, *mesh_ids],
                )
            affected_ids = list(
                dict.fromkeys([*removed_ids, *radiation_ids, *trace_ids, *mesh_ids])
            )
            if emit_events and affected_ids:
                events = [
                    self._append_event(
                        conn,
                        job_id,
                        "metadata",
                        {
                            "changed": {
                                **({"has_results": False} if job_id in removed_ids else {}),
                                **(
                                    {"has_radiation_impedance_artifact": False}
                                    if job_id in radiation_ids
                                    else {}
                                ),
                                **(
                                    {
                                        "field_plane_available": False,
                                        "field_trace_bytes": None,
                                        "unavailable_reason": "artifact_pruned",
                                    }
                                    if job_id in trace_ids
                                    else {}
                                ),
                                **(
                                    {"has_mesh_artifact": False}
                                    if job_id in mesh_ids
                                    else {}
                                ),
                            },
                            "reason": "retention",
                        },
                    )
                    for job_id in affected_ids
                ]
        for row in removed_trace_rows:
            self._remove_field_trace_path(
                str(row["job_id"]), self._field_trace_path(str(row["path"]))
            )
        if deleted_results != len(removed_ids):  # pragma: no cover - SQLite rowcount invariant
            logger.warning(
                "Retention selected %d result ids but SQLite reported %d deletions",
                len(removed_ids),
                deleted_results,
            )
        self._delete_discarded_result_logs()
        return removed_ids, affected_ids, events

    def append_event(
        self, job_id: str, event_type: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock, self._transaction() as conn:
            return self._append_event(conn, job_id, event_type, payload)

    def current_event_cursor(self) -> int:
        with self._lock, self._connection() as conn:
            return self._event_cursor(conn)

    def replay_events(self, cursor: int) -> list[dict[str, Any]] | None:
        """Return retained events after cursor, or ``None`` when snapshot is required."""

        with self._lock, self._connection() as conn:
            current = self._event_cursor(conn)
            cursor = int(cursor)
            if cursor < 0 or cursor > current:
                return None
            row = conn.execute("SELECT MIN(id) AS first_id FROM job_events").fetchone()
            first_id = row["first_id"]
            if first_id is not None and cursor < int(first_id) - 1:
                return None
            rows = conn.execute(
                "SELECT * FROM job_events WHERE id > ? ORDER BY id ASC", (cursor,)
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def _update_job(
        self, conn: sqlite3.Connection, job_id: str, fields: Mapping[str, Any]
    ) -> bool:
        values = dict(fields)
        unsupported = sorted(set(values) - ALLOWED_JOB_UPDATE_FIELDS)
        if unsupported:
            raise ValueError(f"Unsupported job update field(s): {', '.join(unsupported)}")
        if "status" in values and values["status"] not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported status: {values['status']}")
        values["updated_at"] = _now_iso()
        assignments = [f"{key} = ?" for key in values]
        params = [self._db_value(key, value) for key, value in values.items()]
        cur = conn.execute(
            f"UPDATE simulation_jobs SET {', '.join(assignments)} WHERE id = ?",
            [*params, job_id],
        )
        return cur.rowcount > 0

    def _upsert_results(
        self, conn: sqlite3.Connection, job_id: str, results: Mapping[str, Any]
    ) -> None:
        # The HTTP response serves this exact text. Persist its digest alongside
        # it so repeated downloads of a multi-megabyte result stay O(1) in CPU.
        results_text = json.dumps(results, allow_nan=False)
        results_sha256 = sha256(results_text.encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO simulation_results (job_id, results_json, results_sha256)
            VALUES (?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
              results_json = excluded.results_json,
              results_sha256 = excluded.results_sha256
            """,
            # The HTTP route serves these bytes verbatim as application/json.
            # Python's default NaN/Infinity spelling is not valid JSON and makes
            # browser JSON.parse fail after an otherwise "complete" solve.
            (job_id, results_text, results_sha256),
        )
        conn.execute(
            "UPDATE simulation_jobs SET has_results = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), job_id),
        )

    def _append_event(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        created_at = _now_iso()
        cur = conn.execute(
            "INSERT INTO job_events (created_at, job_id, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (created_at, job_id, event_type, json.dumps(dict(payload))),
        )
        event_id = int(cur.lastrowid)
        conn.execute("DELETE FROM job_events WHERE id <= ?", (event_id - self.event_retention,))
        return {
            "v": 1,
            "kind": "event",
            "cursor": event_id,
            "jobId": job_id,
            "type": event_type,
            "payload": dict(payload),
        }

    @staticmethod
    def _event_cursor(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'job_events'"
        ).fetchone()
        return int(row["seq"]) if row else 0

    @staticmethod
    def _job_exists(conn: sqlite3.Connection, job_id: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM simulation_jobs WHERE id = ?", (job_id,)
        ).fetchone() is not None

    @staticmethod
    def _db_value(key: str, value: Any) -> Any:
        if key in {"cancellation_requested", "has_results", "has_mesh_artifact"}:
            return 1 if value else 0
        return value

    def _connect(self) -> sqlite3.Connection:
        """This thread's connection, opened and configured once.

        Both halves of this matter, and only together. Measured on Windows over
        150 transactions shaped like ``persist_runtime_update`` -- one UPDATE
        plus an event INSERT inside ``BEGIN IMMEDIATE``:

        ======================================  ==========  =========
        configuration                           mean        p95
        ======================================  ==========  =========
        rollback journal, connection per call     8.44 ms    10.00 ms
        rollback journal, connection reused       6.69 ms     8.49 ms
        WAL, connection per call                 10.51 ms    12.46 ms
        WAL + synchronous=NORMAL, reused          0.05 ms     0.10 ms
        ======================================  ==========  =========

        WAL *alone* is slower here, because reopening pays to re-establish the
        shared-memory index every time. Reuse alone barely helps, because the
        rollback journal still creates and deletes a file per transaction --
        two NTFS metadata operations that antivirus watches closely. Together
        they are 170x, and a solve writes a checkpoint every 150 ms.

        ``synchronous=NORMAL`` under WAL means a power cut can lose the last
        commits but cannot corrupt the database. For a local design tool whose
        durable product is the exported file, that is the right trade; the
        alternative was paying a device-cache flush every 150 ms for progress
        bars.

        Per thread rather than one shared connection because SQLite connection
        objects serialize internally and the solver thread, the event loop and
        the thread-pool workers all reach the store; ``check_same_thread=False``
        is retained so the existing RLock stays the ordering authority.
        """

        if self._closed:
            raise RuntimeError(
                "JobStore is closed; create a new JobStore to reopen the database"
            )
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        # Without this any contention raises "database is locked" immediately.
        # The RLock makes that unreachable today, which is precisely why it
        # should not be the only thing standing between us and that error.
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = conn
        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def close(self) -> None:
        """Close every connection this store opened, on every thread.

        Necessary on Windows, where an open handle blocks deleting or replacing
        the database file: tests use temporary directories, and the v1
        migration tool's rollback replaces the file wholesale.
        """

        with self._lock:
            self._closed = True
            with self._connections_lock:
                connections = tuple(self._connections)
                self._connections.clear()
            for conn in connections:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover - closing twice is harmless
                    pass
            self._local = threading.local()

    def checkpoint(self) -> None:
        """Fold the WAL back into the database file.

        Anything that copies, moves or replaces ``simulations.db`` as a single
        file -- backup, the v1 migration's rollback -- must call this first, or
        it captures a database missing every commit still living in the
        ``-wal`` sidecar.
        """

        with self._lock:
            conn = self._connect()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _field_trace_path(self, stored_path: str) -> Path:
        path = Path(stored_path)
        if path.is_absolute():
            candidate = path.resolve()
        else:
            candidate = (self.field_traces_dir.parent / path).resolve()
        root = self.field_traces_dir.resolve()
        if candidate.parent != root:
            raise ValueError("Stored field-trace path is outside the artifact root")
        return candidate

    def _remove_field_trace_path(self, job_id: str, path: Path) -> None:
        try:
            remove_field_trace_artifact(path)
        except OSError as exc:
            logger.warning(
                "Could not remove field-trace artifact for job %s: %s",
                job_id,
                exc,
            )

    def _job_log_path(self, job_id: str) -> Path:
        safe = _SAFE_LOG_NAME.sub("_", str(job_id)).strip("._") or "job"
        return self.job_logs_dir / f"{safe}.log"

    def _ensure_job_logs_dir(self) -> None:
        """Create the log directory once, not on every 150 ms checkpoint.

        ``mkdir(exist_ok=True)`` still costs a syscall that fails, and the
        directory cannot stop existing under a running server. If something
        removes it anyway, the flag is reset on the failure so the next flush
        recreates it rather than failing forever.
        """

        if getattr(self, "_job_logs_dir_ready", False):
            return
        self.job_logs_dir.mkdir(parents=True, exist_ok=True)
        self._job_logs_dir_ready = True

    def _delete_job_logs(self, job_ids: Sequence[str]) -> None:
        for job_id in dict.fromkeys(job_ids):
            try:
                self._job_log_path(job_id).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove retained log for job %s: %s", job_id, exc)

    def _delete_discarded_result_logs(self) -> None:
        """Remove result-tier logs, including leftovers from older sweeps.

        Only names which could have been produced by :meth:`_job_log_path` are
        considered. Active, rated, and result-bearing jobs are protected, as
        are terminal jobs whose results were never marked as discarded.
        """

        try:
            entries = tuple(self.job_logs_dir.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Could not scan retained job logs: %s", exc)
            return

        with self._lock, self._connection() as conn:
            kept_rows = conn.execute(
                """SELECT id FROM simulation_jobs
                   WHERE status NOT IN ('complete', 'error', 'cancelled')
                      OR COALESCE(
                           CAST(json_extract(task_metadata_json, '$.rating') AS INTEGER), 0
                         ) > 0
                      OR has_results = 1
                      OR json_extract(
                           task_metadata_json, '$.results_discarded_at'
                         ) IS NULL"""
            ).fetchall()
            kept_names = {
                self._job_log_path(str(row["id"])).name for row in kept_rows
            }

            for path in entries:
                if path.name in kept_names or self._job_log_path(path.stem).name != path.name:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except IsADirectoryError:
                    continue
                except OSError as exc:
                    logger.warning("Could not remove discarded result log %s: %s", path, exc)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        # The connection outlives the block now; ``close()`` owns its lifetime.
        yield self._connect()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _backfill_job_identity(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO job_identity (job_id, parent_job_id)
               SELECT simulation_jobs.id, NULL
               FROM simulation_jobs
               LEFT JOIN job_identity ON job_identity.job_id = simulation_jobs.id
               WHERE job_identity.job_id IS NULL
               ORDER BY simulation_jobs.created_at ASC, simulation_jobs.id ASC"""
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        """Decode rows exactly as v1 ``server/db.py:364-395``."""

        columns = set(row.keys())
        result: dict[str, Any] = {
            "id": row["id"],
            "run_number": int(row["run_number"]),
            "parent_job_id": row["parent_job_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "queued_at": row["queued_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "progress": float(row["progress"] or 0.0),
            "stage": row["stage"],
            "stage_message": row["stage_message"],
            "error_message": row["error_message"],
            "cancellation_requested": bool(row["cancellation_requested"]),
            "config_json": json.loads(row["config_json"] or "{}"),
            "config_summary_json": json.loads(row["config_summary_json"] or "{}"),
            "has_results": bool(row["has_results"]),
            "has_mesh_artifact": bool(row["has_mesh_artifact"]),
            "mesh_stats": json.loads(row["mesh_stats_json"])
            if row["mesh_stats_json"]
            else None,
            "label": row["label"],
        }
        result["script_snapshot"] = (
            json.loads(row["script_snapshot_json"])
            if "script_snapshot_json" in columns and row["script_snapshot_json"]
            else None
        )
        result["task_metadata"] = (
            json.loads(row["task_metadata_json"])
            if "task_metadata_json" in columns and row["task_metadata_json"]
            else {}
        )
        return result

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "v": 1,
            "kind": "event",
            "cursor": int(row["id"]),
            "jobId": row["job_id"],
            "type": row["event_type"],
            "payload": json.loads(row["payload_json"] or "{}"),
        }


__all__ = ["ALLOWED_STATUSES", "JobStore"]
