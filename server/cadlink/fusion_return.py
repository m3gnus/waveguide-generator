"""Ask the active WGLink session to publish its current Fusion geometry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping
import uuid

from fastapi import APIRouter, HTTPException, Request

from .fusion_delivery import (
    DELIVERY_VERSION, IPC_SUBDIRECTORY, RETURN_REQUESTS,
    addin_delivery_version, publish_fusion_request,
)
from .fusion_outcomes import adapter_outcome
from .operations import CANCELLED, REQUEST_RETURN, TERMINAL_STATES, request_digest
from .store import CadLinkStore


# Each request is its own file here, under its request id (see fusion_delivery).
RETURN_REQUESTS_DIRECTORY = RETURN_REQUESTS.directory


def publish_return_request(
    data_dir: Path,
    store: CadLinkStore,
    *,
    session_id: str,
    design_id: str,
    document_id: str,
    instance_id: str,
    expected_return_state_hash: str | None,
    request_id: str | None = None,
) -> tuple[Path, str]:
    """Publish one return request; return its file and its request id.

    It names an exact document and link, and the model state WG displayed,
    which the add-in checks against the live document before exporting.
    """

    if not session_id:
        raise ValueError("Fusion return request requires an active WGLink session.")
    if not design_id or not document_id or not instance_id:
        raise ValueError("Fusion return request requires an exact document and link target.")
    if not expected_return_state_hash:
        raise ValueError(
            "Fusion has not reported the model's state yet, so WG cannot ask for the "
            "exact model it displayed. Refresh CAD Link and try again."
        )
    request_id = request_id or str(uuid.uuid4())
    target = {
        "document_id": document_id,
        "design_id": design_id,
        "instance_id": instance_id,
        "expected_baseline": {
            "kind": "document_signature_hash",
            "value": expected_return_state_hash,
        },
    }
    payload = {
        "target": "fusion360",
        "sessionId": session_id,
        "designId": design_id,
        "documentId": document_id,
        "instanceId": instance_id,
        "expectedReturnStateHash": expected_return_state_hash,
        "requestedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }

    def accept(_document: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        row, result = store.accept_operation(
            request_id,
            REQUEST_RETURN,
            request_digest(REQUEST_RETURN, target, {}),
            target,
            {},
        )
        if result == "conflict":
            raise ValueError(f"Fusion request id {request_id!r} names another operation.")
        return row, result

    def publication_failed(receipt: object, _exc: BaseException) -> None:
        if not isinstance(receipt, tuple) or receipt[1] != "created":
            return
        row = receipt[0]
        store.record_outcome(
            request_id,
            int(row["attempt_generation"]),
            CANCELLED,
            reason="publication_failed",
            outcome={"message": "The Fusion return request file could not be published."},
        )

    def session_changed(operation_id: str) -> None:
        row = store.get_operation(operation_id)
        if row is not None:
            store.record_outcome(
                operation_id,
                int(row["attempt_generation"]),
                CANCELLED,
                reason="session_changed",
                outcome={
                    "message": (
                        f"Fusion session changed before request {request_id} was published."
                    )
                },
            )

    published = publish_fusion_request(
        data_dir,
        RETURN_REQUESTS,
        payload,
        request_id,
        # The add-in runs only requests that name its own session, and a
        # request names the session current when it is made. Requests for any
        # other session can never run.
        withdraw=lambda existing: existing.get("sessionId") != session_id,
        before_publish=accept,
        publish_failed=publication_failed,
        after_withdraw=session_changed,
    )
    return published.path, request_id


__all__ = [
    "IPC_SUBDIRECTORY",
    "RETURN_REQUESTS_DIRECTORY",
    "publish_return_request",
    "read_return_request",
    "router",
]


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


def read_return_request(data_dir: Path, store: CadLinkStore, request_id: str) -> dict[str, Any] | None:
    """Read one file-transport result by its exact request id.

    Geometry success is proved by the returns listing, never by an adapter's
    completion report. The bounded status file can prove refusal for this exact
    correlation id. Persist it before replying so another command overwriting
    the diagnostic slot cannot erase the result. Old command-time reports are
    allowed: they carry an exact id and never claim current document presence.
    The five-second future tolerance is deliberately tighter than
    fusion_status.FUSION_STATUS_MAX_SKEW (one minute): command outcomes need no
    presence grace period. This exact command evidence remains usable after the
    presence heartbeat has aged out.
    """
    row = store.get_operation(request_id)
    if row is None or row["kind"] != REQUEST_RETURN:
        return None
    if row["state"] not in TERMINAL_STATES:
        marker = data_dir / IPC_SUBDIRECTORY / ".fusion-status.json"
        try:
            if marker.is_symlink() or marker.stat().st_size > 256 * 1024:
                raise ValueError("Unsafe Fusion status file")
            report = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("Invalid Fusion status")
            updated = datetime.fromisoformat(str(report.get("updatedAt", "")).replace("Z", "+00:00"))
            if (report.get("schemaVersion") != 1 or report.get("cadApplication") != "fusion360"
                    or (addin_delivery_version(report) or 0) < DELIVERY_VERSION
                    or updated.tzinfo is None or updated > datetime.now(timezone.utc) + timedelta(seconds=5)):
                raise ValueError("Unsupported Fusion status")
            diagnostics = report.get("diagnostics")
            if not isinstance(diagnostics, dict):
                raise ValueError("Invalid Fusion diagnostics")
            recent = diagnostics.get("recentOutcomes", [])
            candidates = [item for item in recent if isinstance(item, dict)
                          and item.get("channel") == "returnRequest" and item.get("requestId") == request_id] if isinstance(recent, list) else []
            last = diagnostics.get("lastRequest")
            if isinstance(last, dict) and last.get("channel") == "returnRequest" and last.get("correlationId") == request_id:
                candidates.append(last)
            if candidates:
                outcome = candidates[-1].get("outcome")
                if outcome in {"refused", "failed", "superseded", "discarded", "applied"}:
                    state, reason, recorded = adapter_outcome(row, outcome)
                    changed = store.record_outcome(request_id, int(row["attempt_generation"]), state,
                                                   reason=reason, outcome=recorded)
                    if changed is not None:
                        store.make_durable()
                    row = store.get_operation(request_id) or row
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
            pass  # Missing or invalid evidence never implies refusal or success.
    outcome = json.loads(row.get("outcome_json") or "{}")
    return {"requestId": request_id, "state": row["state"], "reason": row.get("reason"),
            "message": outcome.get("message")}


@router.get("/return-requests/{request_id}")
async def get_return_request(request_id: str, request: Request) -> dict[str, Any]:
    state = request.app.state
    result = await asyncio.to_thread(read_return_request, Path(state.data_dir), state.cadlink_store, request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown Fusion return request")
    return result
