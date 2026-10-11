"""Durable Send receipts, beside the operation lifecycle.

The additive table gives terminal arrivals their own insertion order: operation
rowids cannot be a feed cursor because an earlier request may settle later.
It also retains refusals that have no operation. Older readers ignore the table;
no store-format or user_version change is needed. Payloads project existing
records, without ingesting, meshing or creating jobs.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
import uuid
import sqlite3
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .store import CadLinkStore


logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS cad_send_receipts (
  cursor INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL
)
"""


def record_operation(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    if row["kind"] != "receive_snapshot" or row["state"] not in ("accepted", "rejected"):
        return
    if conn.execute("SELECT 1 FROM cad_send_receipts WHERE receipt_id = ?",
                    (f"send:{row['operation_id']}",)).fetchone():
        return
    snapshot = json.loads(row.get("snapshot_json") or "{}")
    inputs = json.loads(row.get("inputs_json") or "{}")
    outcome = json.loads(row.get("outcome_json") or "{}")
    send_id = row["operation_id"]
    design_ids = snapshot.get("design_ids") or [
        item[0] for item in conn.execute(
            "SELECT design_id FROM designs WHERE lineage_id = ? ORDER BY design_id",
            (snapshot.get("project_lineage_id"),),
        )
    ]
    _insert(conn, {
        "receipt_id": f"send:{send_id}", "send_id": send_id,
        "send_sequence": row.get("accepted_seq"),
        "project_id": snapshot.get("project_lineage_id"),
        "design_ids": design_ids,
        "ingest_id": row.get("preparation_id"),
        "outcome": "accepted" if row["state"] == "accepted" else "refused",
        "reason": row.get("reason"), "message": outcome.get("message"),
        "created_at": row["updated_at"], "sent_at": row["created_at"],
        "bundle_path": inputs.get("bundle_path"),
        "manifest_sha256": snapshot.get("manifest_sha256") or inputs.get("manifest_sha256"),
        "document_name": snapshot.get("document_name"),
        "retained_copy": bool(snapshot.get("manifest_sha256")),
        "refusal": None,
    })


def _insert(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    if conn.execute("SELECT 1 FROM cad_send_receipts WHERE receipt_id = ?",
                    (payload["receipt_id"],)).fetchone():
        return
    conn.execute(
        "INSERT OR IGNORE INTO cad_send_receipts(receipt_id, payload_json) VALUES (?, ?)",
        (payload["receipt_id"], json.dumps(payload, sort_keys=True)),
    )


def initialize(conn: sqlite3.Connection) -> bool:
    conn.execute(SCHEMA)
    conn.execute("CREATE TABLE IF NOT EXISTS cad_receipt_meta (feed_id TEXT NOT NULL, last_refusal_at TEXT)")
    created = conn.execute("SELECT feed_id FROM cad_receipt_meta").fetchone() is None
    if created:
        conn.execute("INSERT INTO cad_receipt_meta(feed_id) VALUES (?)", (uuid.uuid4().hex,))
    # Only surviving claims need a replay record beyond the bounded health log.
    conn.execute("CREATE TABLE IF NOT EXISTS cad_receipt_claims "
                 "(claim_id TEXT PRIMARY KEY, refusal_json TEXT NOT NULL)")
    # Discover older-app settlements without decoding already projected Sends.
    for row in conn.execute(
        "SELECT rowid AS accepted_seq, * FROM cad_operations AS operation "
        "WHERE kind = 'receive_snapshot' AND state IN ('accepted', 'rejected') "
        "AND NOT EXISTS (SELECT 1 FROM cad_send_receipts AS receipt "
        "WHERE receipt.receipt_id = 'send:' || operation.operation_id) "
        "ORDER BY updated_at, rowid"
    ).fetchall():
        try:
            record_operation(conn, dict(row))
        except (ValueError, TypeError, AttributeError, KeyError):
            logger.warning("Skipping malformed terminal Send %s", row["operation_id"], exc_info=True)

    return created


def record_refusal(store: CadLinkStore, refusal: Mapping[str, Any], *,
                   claim_id: str | None = None) -> dict[str, Any]:
    """Durably retain and replay a claim's original refusal before any publication."""
    store.initialize()
    key = claim_id or json.dumps(dict(refusal), sort_keys=True)
    receipt_id = "intake:" + hashlib.sha256(key.encode()).hexdigest()
    with store._lock, store._transaction() as conn:
        saved = conn.execute("SELECT refusal_json FROM cad_receipt_claims WHERE claim_id = ?",
                             (claim_id,)).fetchone() if claim_id else None
        previous = conn.execute("SELECT payload_json FROM cad_send_receipts WHERE receipt_id = ?",
                                (receipt_id,)).fetchone()
        original = (json.loads(saved[0]) if saved else
                    json.loads(previous[0])["refusal"] if previous else dict(refusal))
        if claim_id and not saved:
            if not previous:
                # Health uses at/file/id as its key. Subsecond time separates
                # two distinct claims of the same delivery slot within a second.
                at = datetime.now(timezone.utc)
                last = conn.execute("SELECT last_refusal_at FROM cad_receipt_meta").fetchone()[0]
                if last:
                    at = max(at, datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(microseconds=1))
                original["at"] = at.isoformat().replace("+00:00", "Z")
                conn.execute("UPDATE cad_receipt_meta SET last_refusal_at = ?", (original["at"],))
            conn.execute("INSERT INTO cad_receipt_claims VALUES (?, ?)",
                         (claim_id, json.dumps(original, sort_keys=True)))
        # An evicted replay must not re-enter the bounded log with a new cursor.
        if not saved or previous:
            _insert(conn, {
                "receipt_id": receipt_id, "send_id": original.get("operationId"),
                "send_sequence": None,
                "project_id": None, "design_ids": [], "ingest_id": None,
                "outcome": "refused", "reason": original["reason"], "message": original["reason"],
                "created_at": original["at"], "sent_at": original["at"],
                "bundle_path": None, "manifest_sha256": None, "document_name": None,
                "retained_copy": False, "refusal": original,
            })
        conn.execute(
            "DELETE FROM cad_send_receipts WHERE cursor IN ("
            "SELECT cursor FROM cad_send_receipts WHERE receipt_id LIKE 'intake:%' "
            "ORDER BY cursor DESC LIMIT -1 OFFSET 20)"
        )
    store.make_durable()
    return original


def forget_claim(store: CadLinkStore, claim_id: str) -> None:
    with store._lock, store._transaction() as conn:
        conn.execute("DELETE FROM cad_receipt_claims WHERE claim_id IN (?, ?)",
                     (claim_id, claim_id + ":unreadable"))


def receipt_page(store: CadLinkStore, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
    if after < 0 or not 1 <= limit <= 200:
        raise ValueError("after must be non-negative and limit must be 1..200")
    store.initialize()
    # Another thread may observe initialized while its first checkpoint waits.
    if getattr(store, "_receipt_feed_needs_sync", False):
        store.make_durable()
        store._receipt_feed_needs_sync = False
    with store._lock, store._transaction() as conn:
        initialize(conn)
        feed_id = conn.execute("SELECT feed_id FROM cad_receipt_meta").fetchone()[0]
        rows = conn.execute(
            "SELECT cursor, payload_json FROM cad_send_receipts "
            "WHERE cursor > ? ORDER BY cursor LIMIT ?", (after, limit + 1),
        ).fetchall()
    items = [{**json.loads(row["payload_json"]), "cursor": row["cursor"]} for row in rows[:limit]]
    return {"feed_id": feed_id, "receipts": items,
            "cursor": items[-1]["cursor"] if items else after, "has_more": len(rows) > limit}


def with_source_outcome(receipt: dict[str, Any], *, configured: bool,
                        source: Mapping[str, Any] | None) -> dict[str, Any]:
    """Availability is read-time evidence, never permission to open WG's copy."""
    if receipt["outcome"] != "accepted":
        return receipt
    if not configured:
        return {**receipt, "outcome": "retained_copy", "reason": "folder_not_selected"}
    if source is None:
        return {**receipt, "outcome": "missing_source", "reason": "source_missing"}
    if (receipt.get("manifest_sha256") and source.get("manifest_sha256")
            and receipt["manifest_sha256"] != source["manifest_sha256"]):
        return {**receipt, "outcome": "unreadable", "reason": "source_changed"}
    if not source.get("readable"):
        return {**receipt, "outcome": "unreadable", "reason": source.get("reason") or "source_unreadable"}
    return {**receipt, "design_ids": source.get("designIds", receipt["design_ids"])}
