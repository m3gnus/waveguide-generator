"""Fusion mutation recovery notices, independent of the live transport.

Dismissal hides a named notice; it neither cancels nor repeats a mutation and
never records that Fusion was repaired. The additive table preserves that
choice across restarts without changing cadlink.db's reader format.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .fusion_outcomes import RECOVERY_NOTICE_SCHEMA, settle_from_heartbeat
from .store import CadLinkStore
from .wglink_protocol import FUSION_REQUEST_DELIVERY_VERSION

router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


class RecoveryNotice(BaseModel):
    id: str
    kind: Literal["insert_link", "update_link"]
    document_id: str | None
    document_name: str | None
    created_at: str
    sequence: int
    active_document: bool


class RecoveryNoticesResponse(BaseModel):
    notices: list[RecoveryNotice]


class RecoveryDismissalResponse(BaseModel):
    id: str
    dismissed: bool = True
    repaired: bool = False


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _json(value: object) -> Mapping[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
        return result if isinstance(result, Mapping) else {}
    except (ValueError, TypeError):
        return {}


def file_evidence(data_dir: Path) -> Mapping[str, Any] | None:
    """Only the add-in's fresh, supported status file can establish presence.

    Keep this reader local: fusion_status imports the live registry, which is
    removed in S5-8. A missing/stale report never erases durable recovery.
    """
    path = data_dir / "ipc" / "wglink" / ".fusion-status.json"
    try:
        if path.is_symlink() or path.stat().st_size > 256 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            return None
        version = value.get("deliveryVersion")
        if (value.get("schemaVersion") != 1 or value.get("cadApplication") != "fusion360"
                or type(version) is not int or version < FUSION_REQUEST_DELIVERY_VERSION):
            return None
        updated = datetime.fromisoformat(str(value.get("updatedAt")).replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - updated
        if age > timedelta(seconds=20) or age < timedelta(minutes=-1):
            return None
        return value
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def recovery_notices(store: CadLinkStore, data_dir: Path) -> RecoveryNoticesResponse:
    heartbeat = file_evidence(data_dir)
    store.initialize()
    # Use the operation store's lock for evidence, metadata, and dismissal:
    # binding and an overlapping dismissal cannot revive the same request.
    with store._lock:
        if heartbeat is not None:
            settle_from_heartbeat(store, heartbeat, data_dir / "ipc" / "wglink", mutations_only=True)
        document = heartbeat.get("document") if heartbeat else None
        document = document if isinstance(document, Mapping) else {}
        active_id = _text(document.get("id"))
        applying = document.get("applyingOperation")
        applying = applying if isinstance(applying, Mapping) else {}
        notices = []
        with store._transaction() as conn:
            conn.execute(RECOVERY_NOTICE_SCHEMA)
            # No capped operation listing: recovery remains discoverable even
            # with a large backlog. Acceptance rowid gives a stable tie order.
            rows = conn.execute("SELECT rowid AS sequence, * FROM cad_operations "
                                "WHERE state = 'recovery_required' "
                                "AND kind IN ('insert_link', 'update_link') ORDER BY rowid").fetchall()
            for raw in rows:
                row = dict(raw)
                notice_id = row["operation_id"]
                target = _json(row.get("target_json"))
                snapshot = _json(row.get("snapshot_json"))
                document_id = _text(target.get("document_id"))
                document_name = _text(snapshot.get("document_name"))
                if applying.get("operationId") == notice_id and active_id:
                    # Insert has no document id until Fusion reports its mark.
                    # An update's declared destination cannot be rebound.
                    if document_id is None or document_id == active_id:
                        document_id = active_id
                        document_name = _text(document.get("name")) or document_name
                conn.execute("INSERT INTO cad_recovery_notices(notice_id, document_id, document_name) "
                             "VALUES (?, ?, ?) ON CONFLICT(notice_id) DO UPDATE SET "
                             "document_id = COALESCE(cad_recovery_notices.document_id, excluded.document_id), "
                             "document_name = COALESCE(excluded.document_name, cad_recovery_notices.document_name)",
                             (notice_id, document_id, document_name))
                saved = conn.execute("SELECT * FROM cad_recovery_notices WHERE notice_id = ?", (notice_id,)).fetchone()
                if saved["dismissed_at"] is not None:
                    continue
                notices.append(RecoveryNotice(
                    id=notice_id, kind=row["kind"], document_id=saved["document_id"],
                    document_name=saved["document_name"], created_at=row["created_at"], sequence=row["sequence"],
                    active_document=bool(active_id and active_id == saved["document_id"]),
                ))
    return RecoveryNoticesResponse(notices=notices)


def dismiss_recovery_notice(store: CadLinkStore, notice_id: str) -> RecoveryDismissalResponse:
    store.initialize()
    with store._lock, store._transaction() as conn:
        conn.execute(RECOVERY_NOTICE_SCHEMA)
        # A tombstone may precede operation binding; it must survive that race.
        conn.execute("INSERT INTO cad_recovery_notices(notice_id, dismissed_at) VALUES (?, ?) "
                     "ON CONFLICT(notice_id) DO UPDATE SET "
                     "dismissed_at = COALESCE(cad_recovery_notices.dismissed_at, excluded.dismissed_at)",
                     (notice_id, datetime.now(timezone.utc).isoformat()))
    return RecoveryDismissalResponse(id=notice_id)


@router.get("/recovery-notices", response_model=RecoveryNoticesResponse)
async def get_recovery_notices(request: Request) -> RecoveryNoticesResponse:
    state = request.app.state
    return await asyncio.to_thread(recovery_notices, state.cadlink_store, Path(state.data_dir))


@router.post("/recovery-notices/{notice_id}/dismiss", response_model=RecoveryDismissalResponse)
async def post_dismiss_recovery_notice(notice_id: str, request: Request) -> RecoveryDismissalResponse:
    return await asyncio.to_thread(dismiss_recovery_notice, request.app.state.cadlink_store, notice_id)
