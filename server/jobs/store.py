"""SQLite persistence for simulation jobs and durable job events.

The three simulation tables deliberately retain the v1 names and columns from
``server/db.py:41-99`` so Phase 6 can copy rows without transforming them.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator, Mapping, Sequence

from server.platform.paths import data_paths


ALLOWED_STATUSES = frozenset({"queued", "running", "complete", "error", "cancelled"})
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
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS simulation_artifacts (
      job_id TEXT PRIMARY KEY,
      msh_text TEXT,
      FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
    )""",
    """CREATE INDEX IF NOT EXISTS idx_simulation_jobs_status_created
      ON simulation_jobs(status, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS job_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      job_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_job_events_id ON job_events(id)",
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
    ) -> None:
        self.db_path = Path(db_path)
        self.event_retention = max(1, int(event_retention))
        self.job_logs_dir = (
            Path(job_logs_dir) if job_logs_dir is not None else self.db_path.parent / "job-logs"
        )
        self._lock = threading.RLock()

    @classmethod
    def for_data_dir(cls, data_dir: str | Path, **kwargs: Any) -> "JobStore":
        """Place the DB in WG2's namespaced ``db/`` directory."""

        paths = data_paths(data_dir)
        return cls(
            paths.db / "simulations.db",
            job_logs_dir=paths.logs / "jobs",
            **kwargs,
        )

    def initialize(self) -> None:
        """Create the exact v1 migration-target tables plus v2 job events."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._transaction() as conn:
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
            # Keep the v1 schema marker. job_events is an additive v2 transport table.
            conn.execute("PRAGMA user_version = 4")

    def create_job(
        self,
        job: Mapping[str, Any],
        *,
        initial_event: tuple[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Insert a queued job, optionally atomically with its first event.

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
        visible. ``expected_log_size`` makes that append idempotent: if SQLite
        commit fails after the append, retry recognizes the exact durable batch
        instead of writing it twice. A mismatched file suffix fails closed.

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
            if (
                row is None
                or row["status"] not in {"queued", "running"}
            ):
                return False, []

            cancellation_requested = bool(row["cancellation_requested"])
            if cancellation_requested:
                # A cancellation stage already owns the visible state. Logs
                # that raced with the request are still durable, but stale
                # progress must not restore the prior solve stage.
                values = {}
                stage_payload = None

            event_lines: list[str] = []
            if full_lines:
                path = self._job_log_path(job_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                batch = "".join(f"{line}\n" for line in full_lines).encode("utf-8")
                expected = (
                    int(expected_log_size)
                    if expected_log_size is not None
                    else (path.stat().st_size if path.exists() else 0)
                )
                actual = path.stat().st_size if path.exists() else 0
                if actual < expected:
                    raise OSError(
                        f"Job log shrank from expected offset {expected} to {actual}"
                    )
                already_appended = False
                if actual > expected:
                    with path.open("rb") as handle:
                        handle.seek(expected)
                        suffix = handle.read()
                    if suffix != batch:
                        raise OSError(
                            "Job log changed outside the buffered runtime writer"
                        )
                    already_appended = True
                if not already_appended:
                    with path.open("ab") as handle:
                        handle.write(batch)
                        handle.flush()
                        os.fsync(handle.fileno())

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
            row = conn.execute("SELECT * FROM simulation_jobs WHERE id = ?", (job_id,)).fetchone()
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
            where = f"WHERE status IN ({placeholders})"
            args.extend(statuses)
        with self._lock, self._connection() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) AS c FROM simulation_jobs {where}", args).fetchone()["c"]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM simulation_jobs {where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                [*args, int(limit), int(offset)],
            ).fetchall()
        return [self._row_to_job(row) for row in rows], total

    def snapshot_jobs(self) -> tuple[list[dict[str, Any]], int]:
        """Read the WS snapshot and matching durable cursor consistently."""

        with self._lock, self._connection() as conn:
            conn.execute("BEGIN")
            cursor = self._event_cursor(conn)
            rows = conn.execute(
                "SELECT * FROM simulation_jobs ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_job(row) for row in rows], cursor

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

    def append_job_log(self, job_id: str, line: str) -> None:
        """Append to the complete per-job log kept outside bounded WS metadata."""

        path = self._job_log_path(job_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(str(line).replace("\r", "") + "\n")

    def get_job_log(self, job_id: str) -> str:
        with self._lock:
            try:
                return self._job_log_path(job_id).read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""

    def get_results(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT results_json FROM simulation_results WHERE job_id = ?", (job_id,)
            ).fetchone()
            return json.loads(row["results_json"]) if row else None

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

    def delete_job_with_event(self, job_id: str) -> tuple[bool, dict[str, Any] | None]:
        """Delete one row and retain a terminal deleted event."""

        with self._lock, self._transaction() as conn:
            cur = conn.execute("DELETE FROM simulation_jobs WHERE id = ?", (job_id,))
            if cur.rowcount <= 0:
                return False, None
            event = self._append_event(conn, job_id, "deleted", {})
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
            conn.execute(f"DELETE FROM simulation_jobs WHERE id IN ({id_placeholders})", ids)
            events = [self._append_event(conn, job_id, "deleted", {}) for job_id in ids]
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
                "SELECT * FROM simulation_jobs WHERE status = 'queued' ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_job(row) for row in queued], failed_events

    def prune_terminal_jobs(self, retention_days: int = 30, max_terminal_jobs: int = 1000) -> int:
        """Apply v1's age/count retention policy (``server/db.py:316-347``)."""

        cutoff = (datetime.now() - timedelta(days=int(retention_days))).isoformat()
        removed_ids: list[str] = []
        with self._lock, self._transaction() as conn:
            aged = conn.execute(
                """SELECT id FROM simulation_jobs
                   WHERE status IN ('complete', 'error', 'cancelled')
                     AND COALESCE(completed_at, updated_at, created_at) < ?""",
                (cutoff,),
            ).fetchall()
            removed_ids.extend(str(row["id"]) for row in aged)
            cur = conn.execute(
                """
                DELETE FROM simulation_jobs
                WHERE status IN ('complete', 'error', 'cancelled')
                  AND COALESCE(completed_at, updated_at, created_at) < ?
                """,
                (cutoff,),
            )
            deleted = int(cur.rowcount or 0)
            rows = conn.execute(
                """
                SELECT id FROM simulation_jobs
                WHERE status IN ('complete', 'error', 'cancelled')
                ORDER BY COALESCE(completed_at, updated_at, created_at) DESC
                """
            ).fetchall()
            overflow = [row["id"] for row in rows[int(max_terminal_jobs) :]]
            if overflow:
                removed_ids.extend(str(value) for value in overflow)
                placeholders = ",".join("?" for _ in overflow)
                cur = conn.execute(
                    f"DELETE FROM simulation_jobs WHERE id IN ({placeholders})", overflow
                )
                deleted += int(cur.rowcount or 0)
        self._delete_job_logs(removed_ids)
        return deleted

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
        conn.execute(
            """
            INSERT INTO simulation_results (job_id, results_json) VALUES (?, ?)
            ON CONFLICT(job_id) DO UPDATE SET results_json = excluded.results_json
            """,
            (job_id, json.dumps(results)),
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
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _job_log_path(self, job_id: str) -> Path:
        safe = _SAFE_LOG_NAME.sub("_", str(job_id)).strip("._") or "job"
        return self.job_logs_dir / f"{safe}.log"

    def _delete_job_logs(self, job_ids: Sequence[str]) -> None:
        for job_id in dict.fromkeys(job_ids):
            try:
                self._job_log_path(job_id).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove retained log for job %s: %s", job_id, exc)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

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
        finally:
            conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        """Decode rows exactly as v1 ``server/db.py:364-395``."""

        columns = set(row.keys())
        result: dict[str, Any] = {
            "id": row["id"],
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
