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
import sqlite3
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .store import CadLinkStore


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


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    # Also discovers terminal Sends written while an older app had the store
    # open. Once projected, their identity and cursor never change.
    for row in conn.execute(
        "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE kind = 'receive_snapshot' "
        "AND state IN ('accepted', 'rejected') ORDER BY updated_at, rowid"
    ).fetchall():
        record_operation(conn, dict(row))


def record_refusal(store: CadLinkStore, refusal: Mapping[str, Any]) -> None:
    """Persist the same intake refusal health already presents; no second UI log."""
    store.initialize()
    # Same identity as cadDeliveryHealth: a push and recovery count once.
    key = f"{refusal['at']}|{refusal['file']}|{refusal.get('operationId') or ''}"
    receipt_id = "intake:" + hashlib.sha256(key.encode()).hexdigest()
    with store._lock, store._transaction() as conn:
        _insert(conn, {
            "receipt_id": receipt_id, "send_id": refusal.get("operationId"),
            "send_sequence": None,
            "project_id": None, "design_ids": [], "ingest_id": None,
            "outcome": "refused", "reason": refusal["reason"], "message": refusal["reason"],
            "created_at": refusal["at"], "sent_at": refusal["at"],
            "bundle_path": None, "manifest_sha256": None, "document_name": None,
            "retained_copy": False, "refusal": dict(refusal),
        })
        # Match delivery health's bounded intake log. Accepted/refused Sends
        # with operation identity remain in the cursor feed without a cap.
        conn.execute(
            "DELETE FROM cad_send_receipts WHERE cursor IN ("
            "SELECT cursor FROM cad_send_receipts WHERE receipt_id LIKE 'intake:%' "
            "ORDER BY cursor DESC LIMIT -1 OFFSET 20)"
        )


def receipt_page(store: CadLinkStore, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
    if after < 0 or not 1 <= limit <= 200:
        raise ValueError("after must be non-negative and limit must be 1..200")
    store.initialize()
    with store._lock:
        rows = store._connect().execute(
            "SELECT cursor, payload_json FROM cad_send_receipts "
            "WHERE cursor > ? ORDER BY cursor LIMIT ?", (after, limit + 1),
        ).fetchall()
    items = [{**json.loads(row["payload_json"]), "cursor": row["cursor"]} for row in rows[:limit]]
    return {"receipts": items, "cursor": items[-1]["cursor"] if items else after,
            "has_more": len(rows) > limit}


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
