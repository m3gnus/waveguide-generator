import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ALLOWED_STATUSES = {"queued", "running", "complete", "error", "cancelled"}


class SimulationDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._managed_connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_jobs (
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
                  label TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_results (
                  job_id TEXT PRIMARY KEY,
                  results_json TEXT NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_artifacts (
                  job_id TEXT PRIMARY KEY,
                  msh_text TEXT,
                  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_simulation_jobs_status_created
                  ON simulation_jobs(status, created_at DESC)
                """
            )
            conn.execute("PRAGMA user_version = 1")

    def create_job(self, job: Dict[str, Any]) -> None:
        with self._lock, self._managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO simulation_jobs (
                  id, status, created_at, updated_at, queued_at,
                  started_at, completed_at, progress, stage, stage_message,
                  error_message, cancellation_requested, config_json,
                  config_summary_json, has_results, has_mesh_artifact, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    job.get("label"),
                ),
            )

    def update_job(self, job_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        if "status" in fields and fields["status"] not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported status: {fields['status']}")

        fields = dict(fields)
        fields["updated_at"] = datetime.now().isoformat()

        assignments: List[str] = []
        values: List[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)

        values.append(job_id)
        with self._lock, self._managed_connection() as conn:
            cur = conn.execute(
                f"UPDATE simulation_jobs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def get_job_row(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._managed_connection() as conn:
            cur = conn.execute("SELECT * FROM simulation_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs(
        self,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = ""
        args: List[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where = f"WHERE status IN ({placeholders})"
            args.extend(statuses)

        with self._lock, self._managed_connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM simulation_jobs {where}",
                args,
            ).fetchone()["c"]

            paged_args = [*args, int(limit), int(offset)]
            rows = conn.execute(
                f"""
                SELECT * FROM simulation_jobs
                {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                paged_args,
            ).fetchall()

        return [self._row_to_job(row) for row in rows], int(total)

    def store_results(self, job_id: str, results: Dict[str, Any]) -> None:
        with self._lock, self._managed_connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM simulation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not exists:
                return
            conn.execute(
                """
                INSERT INTO simulation_results (job_id, results_json)
                VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  results_json = excluded.results_json
                """,
                (job_id, json.dumps(results)),
            )
            conn.execute(
                "UPDATE simulation_jobs SET has_results = 1, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), job_id),
            )

    def get_results(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._managed_connection() as conn:
            row = conn.execute(
                "SELECT results_json FROM simulation_results WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            return json.loads(row["results_json"])

    def store_mesh_artifact(self, job_id: str, msh_text: str) -> None:
        with self._lock, self._managed_connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM simulation_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not exists:
                return
            conn.execute(
                """
                INSERT INTO simulation_artifacts (job_id, msh_text)
                VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  msh_text = excluded.msh_text
                """,
                (job_id, msh_text),
            )
            conn.execute(
                "UPDATE simulation_jobs SET has_mesh_artifact = 1, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), job_id),
            )

    def get_mesh_artifact(self, job_id: str) -> Optional[str]:
        with self._lock, self._managed_connection() as conn:
            row = conn.execute(
                "SELECT msh_text FROM simulation_artifacts WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            return row["msh_text"]

    def delete_job(self, job_id: str) -> bool:
        with self._lock, self._managed_connection() as conn:
            cur = conn.execute("DELETE FROM simulation_jobs WHERE id = ?", (job_id,))
            return cur.rowcount > 0

    def delete_jobs_by_status(self, statuses: List[str]) -> List[str]:
        normalized = [str(status).strip() for status in statuses if str(status).strip()]
        if not normalized:
            return []
        for status in normalized:
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"Unsupported status: {status}")

        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self._managed_connection() as conn:
            rows = conn.execute(
                f"SELECT id FROM simulation_jobs WHERE status IN ({placeholders})",
                normalized,
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows if row and row["id"]]
            if not job_ids:
                return []
            id_placeholders = ",".join("?" for _ in job_ids)
            conn.execute(
                f"DELETE FROM simulation_jobs WHERE id IN ({id_placeholders})",
                job_ids,
            )
            return job_ids

    def recover_on_startup(self, restart_error_message: str) -> List[Dict[str, Any]]:
        now = datetime.now().isoformat()
        with self._lock, self._managed_connection() as conn:
            conn.execute(
                """
                UPDATE simulation_jobs
                SET status = 'error',
                    stage = 'error',
                    stage_message = 'Simulation failed',
                    error_message = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE status = 'running'
                """,
                (restart_error_message, now, now),
            )

            rows = conn.execute(
                "SELECT * FROM simulation_jobs WHERE status = 'queued' ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def prune_terminal_jobs(self, retention_days: int = 30, max_terminal_jobs: int = 1000) -> int:
        deleted = 0
        cutoff = (datetime.now() - timedelta(days=int(retention_days))).isoformat()

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM simulation_jobs
                WHERE status IN ('complete', 'error', 'cancelled')
                  AND COALESCE(completed_at, updated_at, created_at) < ?
                """,
                (cutoff,),
            )
            deleted += int(cur.rowcount or 0)

            term_rows = conn.execute(
                """
                SELECT id FROM simulation_jobs
                WHERE status IN ('complete', 'error', 'cancelled')
                ORDER BY COALESCE(completed_at, updated_at, created_at) DESC
                """
            ).fetchall()
            if len(term_rows) > int(max_terminal_jobs):
                overflow_ids = [row["id"] for row in term_rows[int(max_terminal_jobs):]]
                placeholders = ",".join("?" for _ in overflow_ids)
                cur = conn.execute(
                    f"DELETE FROM simulation_jobs WHERE id IN ({placeholders})",
                    overflow_ids,
                )
                deleted += int(cur.rowcount or 0)

        return deleted

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _managed_connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
        return {
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
            "label": row["label"],
        }
