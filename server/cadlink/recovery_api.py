"""Fusion mutation recovery notices, independent of the live transport.

Dismissal hides a named notice; it neither cancels nor repeats a mutation and
never records that Fusion was repaired. The additive table preserves that
choice across restarts without changing cadlink.db's reader format.
GET is a read projection with writes: fresh evidence may settle or claim
Insert/Update operations and persist their document binding before listing.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .fusion_outcomes import RECOVERY_NOTICE_SCHEMA, recovery_document_id, settle_from_heartbeat
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


def file_evidence(data_dir: Path, *, for_loudness: bool = False) -> Mapping[str, Any] | None:
    """Fresh evidence settles; the last command-driven report only stays loud.

    Freshness controls settlement; a missing/stale report never erases
    durable recovery.
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
        diagnostics = value.get("diagnostics")
        activation = diagnostics.get("activation") if isinstance(diagnostics, Mapping) else None
        command_driven = isinstance(activation, Mapping) and activation.get("automaticCoordination") is False
        if for_loudness and command_driven and age < timedelta(0):
            return None
        if age > timedelta(seconds=20):
            if not (for_loudness and command_driven):
                return None
        elif age < timedelta(minutes=-1):
            return None
        return value
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def recovery_notices(store: CadLinkStore, data_dir: Path) -> RecoveryNoticesResponse:
    heartbeat = file_evidence(data_dir)
    last_report = file_evidence(data_dir, for_loudness=True)
    store.initialize()
    # Use the operation store's lock for evidence, metadata, and dismissal:
    # binding and an overlapping dismissal cannot revive the same request.
    with store._lock:
        if heartbeat is not None:
            settle_from_heartbeat(store, heartbeat, data_dir / "ipc" / "wglink", mutations_only=True)
        document = last_report.get("document") if last_report else None
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
                snapshot = _json(row.get("snapshot_json"))
                document_id = recovery_document_id(store, row)
                document_name = _text(snapshot.get("document_name"))
                conn.execute("INSERT INTO cad_recovery_notices(notice_id, document_id, document_name) "
                             "VALUES (?, ?, ?) ON CONFLICT(notice_id) DO UPDATE SET "
                             "document_id = COALESCE(cad_recovery_notices.document_id, excluded.document_id), "
                             "document_name = COALESCE(cad_recovery_notices.document_name, excluded.document_name)",
                             (notice_id, document_id, document_name))
                saved = conn.execute("SELECT * FROM cad_recovery_notices WHERE notice_id = ?", (notice_id,)).fetchone()
                if saved["dismissed_at"] is not None:
                    continue
                notices.append(RecoveryNotice(
                    id=notice_id, kind=row["kind"], document_id=saved["document_id"],
                    document_name=saved["document_name"], created_at=row["created_at"], sequence=row["sequence"],
                    active_document=bool(active_id and active_id == saved["document_id"]
                                         and applying.get("operationId") == notice_id),
                ))
    return RecoveryNoticesResponse(notices=notices)


def dismiss_recovery_notice(store: CadLinkStore, notice_id: str) -> RecoveryDismissalResponse:
    store.initialize()
    with store._lock, store._transaction() as conn:
        conn.execute(RECOVERY_NOTICE_SCHEMA)
        operation = conn.execute("SELECT kind FROM cad_operations WHERE operation_id = ?", (notice_id,)).fetchone()
        if operation is None or operation["kind"] not in {"insert_link", "update_link"}:
            raise HTTPException(status_code=404, detail="Fusion mutation operation not found")
        # Binding may follow dismissal of an accepted mutation; acceptance
        # already supplies the identity, so arbitrary pre-acceptance ids are refused.
        conn.execute("INSERT INTO cad_recovery_notices(notice_id, dismissed_at) VALUES (?, ?) "
                     "ON CONFLICT(notice_id) DO UPDATE SET "
                     "dismissed_at = COALESCE(cad_recovery_notices.dismissed_at, excluded.dismissed_at)",
                     (notice_id, datetime.now(timezone.utc).isoformat()))
    store.make_durable()
    return RecoveryDismissalResponse(id=notice_id)


@router.get("/recovery-notices", response_model=RecoveryNoticesResponse)
async def get_recovery_notices(request: Request) -> RecoveryNoticesResponse:
    """List notices after fresh evidence may settle, claim, or bind mutations."""
    state = request.app.state
    return await asyncio.to_thread(recovery_notices, state.cadlink_store, Path(state.data_dir))


@router.post("/recovery-notices/{notice_id}/dismiss", response_model=RecoveryDismissalResponse,
             responses={404: {"description": "No accepted Fusion mutation has this id"}})
async def post_dismiss_recovery_notice(notice_id: str, request: Request) -> RecoveryDismissalResponse:
    return await asyncio.to_thread(dismiss_recovery_notice, request.app.state.cadlink_store, notice_id)
