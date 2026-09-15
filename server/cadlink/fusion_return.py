"""Ask the active WGLink session to publish its current Fusion geometry."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import uuid

from .fusion_delivery import IPC_SUBDIRECTORY, RETURN_REQUESTS, publish_fusion_request
from .operations import CANCELLED, REQUEST_RETURN, request_digest
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
]
