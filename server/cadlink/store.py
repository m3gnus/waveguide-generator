"""SQLite registry for durable design heads and immutable export snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Any

from server.platform.paths import data_paths

from .identity import (
    CadLink,
    SaveIdentity,
    mint_id,
    truncated_design_hash,
    utc_now,
)


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS designs (
      design_id TEXT PRIMARY KEY,
      lineage_id TEXT NOT NULL,
      edit_version INTEGER NOT NULL CHECK (edit_version >= 1),
      design_hash TEXT NOT NULL,
      snapshot_text TEXT NOT NULL,
      filename TEXT NOT NULL,
      branched_from_design_id TEXT,
      branched_from_edit_version INTEGER,
      branched_from_export_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS designs_by_hash ON designs(design_hash)",
    "CREATE INDEX IF NOT EXISTS designs_by_lineage ON designs(lineage_id)",
    """
    CREATE TABLE IF NOT EXISTS exports (
      export_id TEXT PRIMARY KEY,
      bundle_id TEXT NOT NULL UNIQUE,
      design_id TEXT NOT NULL REFERENCES designs(design_id),
      sequence INTEGER NOT NULL,
      parent_export_id TEXT,
      edit_version INTEGER NOT NULL,
      design_hash TEXT NOT NULL,
      geometry_hash TEXT NOT NULL,
      artifact_sha256 TEXT NOT NULL,
      manifest_json TEXT NOT NULL,
      snapshot_text TEXT NOT NULL,
      idempotency_key TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      UNIQUE (design_id, sequence)
    )
    """,
    "CREATE INDEX IF NOT EXISTS exports_by_artifact ON exports(artifact_sha256)",
)


class CadLinkStore:
    """Thread-safe, transaction-per-write CAD-link registry."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._initialized = False

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> CadLinkStore:
        return cls(data_paths(data_dir).db / "cadlink.db")

    def initialize(self) -> None:
        if self._initialized:
            return
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._transaction() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise RuntimeError(f"unsupported cadlink.db schema version {version}")
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
        self._initialized = True

    def get_design(self, design_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM designs WHERE design_id = ?", (design_id,)
        )

    def find_design_by_hash(self, value: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM designs WHERE design_hash = ? ORDER BY updated_at DESC LIMIT 1",
            (value,),
        )

    def get_export(self, export_or_bundle_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM exports WHERE export_id = ? OR bundle_id = ?",
            (export_or_bundle_id, export_or_bundle_id),
        )

    def find_export_by_artifact(self, artifact_sha256: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM exports WHERE artifact_sha256 = ? ORDER BY created_at DESC LIMIT 1",
            (artifact_sha256,),
        )

    def save(
        self,
        *,
        requested: SaveIdentity | None,
        design_hash: str,
        filename: str,
        snapshot_builder: Callable[[CadLink], str],
        saved_at: str | None = None,
    ) -> dict[str, Any]:
        """Commit a design head with CAS semantics, auto-forking on conflict."""

        self.initialize()
        now = saved_at or utc_now()
        with self._lock, self._transaction() as conn:
            head = None
            if requested is not None:
                head = conn.execute(
                    "SELECT * FROM designs WHERE design_id = ?", (requested.design_id,)
                ).fetchone()

            forked = False
            from_data: dict[str, object] | None = None
            branched_from_export_id: str | None = None
            if requested is None:
                design_id = mint_id("wgd_")
                lineage_id = mint_id("wgl_")
                edit_version = 1
                created_at = now
            elif head is None:
                design_id = requested.design_id
                lineage_id = requested.lineage_id
                edit_version = requested.base_edit_version + 1
                created_at = now
            elif int(head["edit_version"]) == requested.base_edit_version:
                if str(head["lineage_id"]) != requested.lineage_id:
                    raise ValueError("lineageId does not match the registered design")
                design_id = requested.design_id
                lineage_id = requested.lineage_id
                edit_version = requested.base_edit_version + 1
                created_at = str(head["created_at"])
            else:
                forked = True
                design_id = mint_id("wgd_")
                lineage_id = str(head["lineage_id"])
                edit_version = 1
                created_at = now
                latest = conn.execute(
                    "SELECT export_id FROM exports WHERE design_id = ? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (requested.design_id,),
                ).fetchone()
                branched_from_export_id = str(latest["export_id"]) if latest else None
                from_data = {
                    "designId": requested.design_id,
                    "editVersion": requested.base_edit_version,
                    "exportId": branched_from_export_id,
                }

            identity = CadLink(
                design_id=design_id,
                lineage_id=lineage_id,
                edit_version=edit_version,
                saved_at=now,
                saved_design_hash=truncated_design_hash(design_hash),
            )
            snapshot_text = snapshot_builder(identity)
            if head is not None and not forked:
                conn.execute(
                    """
                    UPDATE designs SET edit_version = ?, design_hash = ?, snapshot_text = ?,
                      filename = ?, updated_at = ? WHERE design_id = ?
                    """,
                    (edit_version, design_hash, snapshot_text, filename, now, design_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO designs (
                      design_id, lineage_id, edit_version, design_hash, snapshot_text, filename,
                      branched_from_design_id, branched_from_edit_version,
                      branched_from_export_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        design_id,
                        lineage_id,
                        edit_version,
                        design_hash,
                        snapshot_text,
                        filename,
                        requested.design_id if forked and requested else None,
                        requested.base_edit_version if forked and requested else None,
                        branched_from_export_id,
                        created_at,
                        now,
                    ),
                )
        return {
            "identity": identity,
            "text": snapshot_text,
            "forked": forked,
            "from": from_data,
        }

    def allocate_export(
        self,
        *,
        design_id: str,
        geometry_hash: str,
        artifact_sha256: str,
        idempotency_key: str,
        manifest_json: str | None = None,
        manifest_builder: Callable[[Mapping[str, object]], str] | None = None,
        bundle_id: str | None = None,
        parent_export_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Atomically allocate a per-design sequence and immutable snapshot."""

        self.initialize()
        now = created_at or utc_now()
        with self._lock, self._transaction() as conn:
            retry = conn.execute(
                "SELECT * FROM exports WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if retry is not None:
                return self._row(retry) or {}
            design = conn.execute(
                "SELECT * FROM designs WHERE design_id = ?", (design_id,)
            ).fetchone()
            if design is None:
                raise KeyError(f"unknown design_id {design_id}")
            latest = conn.execute(
                "SELECT export_id, sequence FROM exports WHERE design_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (design_id,),
            ).fetchone()
            sequence = (int(latest["sequence"]) if latest else 0) + 1
            resolved_parent = (
                parent_export_id
                if parent_export_id is not None
                else (str(latest["export_id"]) if latest else None)
            )
            export_id = mint_id("wge_")
            resolved_bundle_id = bundle_id or mint_id("wgb_")
            facts: dict[str, object] = {
                "exportId": export_id,
                "bundleId": resolved_bundle_id,
                "designId": design_id,
                "sequence": sequence,
                "parentExportId": resolved_parent,
                "editVersion": design["edit_version"],
                "designHash": design["design_hash"],
                "createdAt": now,
            }
            if (manifest_json is None) == (manifest_builder is None):
                raise ValueError("provide exactly one of manifest_json or manifest_builder")
            stored_manifest = (
                manifest_builder(facts) if manifest_builder is not None else manifest_json
            )
            assert stored_manifest is not None
            conn.execute(
                """
                INSERT INTO exports (
                  export_id, bundle_id, design_id, sequence, parent_export_id,
                  edit_version, design_hash, geometry_hash, artifact_sha256,
                  manifest_json, snapshot_text, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    resolved_bundle_id,
                    design_id,
                    sequence,
                    resolved_parent,
                    design["edit_version"],
                    design["design_hash"],
                    geometry_hash,
                    artifact_sha256,
                    stored_manifest,
                    design["snapshot_text"],
                    idempotency_key,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM exports WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return self._row(row) or {}

    def close(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for conn in connections:
            conn.close()
        self._local = threading.local()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = conn
        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def _read_one(self, sql: str, parameters: tuple[object, ...]) -> dict[str, Any] | None:
        """Read without creating a registry merely because a file was opened."""

        if not self._initialized and (
            str(self.db_path) == ":memory:" or not self.db_path.exists()
        ):
            return None
        with self._lock:
            try:
                row = self._connect().execute(sql, parameters).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return None
                raise
        return self._row(row)

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
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None


__all__ = ["CadLinkStore"]
