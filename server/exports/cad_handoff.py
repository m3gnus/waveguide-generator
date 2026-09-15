"""Publish the one-shot handoff that WGLink consumes inside Fusion.

The bundle is the durable CAD-link artifact.  This small request is only the
delivery notification: it tells a running (or newly launched) Fusion add-in
which completed export the user just asked to open.  Keeping it beside the
bundles makes the protocol work for custom workspaces without another setting.

A handoff is its own file (see ``server/cadlink/fusion_delivery.py``). One that
names an instance is an update of that exact link, and it must name the Fusion
document and the model state WG measured, which the add-in re-checks
immediately before it changes anything. One that names no instance is an
insert. A newer update for the same document and instance supersedes one the
add-in has not started (docs/architecture/CAD-OPERATIONS.md, "Ordering").
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Mapping
import uuid

from server.cadlink.fusion_delivery import (
    HANDOFFS,
    IPC_SUBDIRECTORY,
    PublishedRequest,
    publish_fusion_request,
)
from server.cadlink.fusion_status import read_fusion_status
from server.cadlink.operations import (
    CANCELLED,
    INSERT_LINK,
    UPDATE_LINK,
    request_digest,
)
from server.cadlink.store import CadLinkStore


HANDOFFS_DIRECTORY = HANDOFFS.directory
UPDATE_TARGET_REQUIRED = (
    "An update of a Fusion link must name the Fusion document and the model state "
    "WG measured, and Fusion has not reported that state yet. Refresh CAD Link, "
    "wait for Fusion to report the model, and send again."
)

logger = logging.getLogger(__name__)


def publish_fusion_handoff(
    data_dir: Path,
    workspace_root: Path,
    store: CadLinkStore,
    result: Mapping[str, object],
    *,
    expected_document_id: str | None = None,
    expected_instance_id: str | None = None,
    expected_return_state_hash: str | None = None,
    request_id: str | None = None,
) -> PublishedRequest:
    """Atomically announce one completed bundle to the Fusion add-in."""

    if expected_instance_id and not (expected_document_id and expected_return_state_hash):
        raise ValueError(UPDATE_TARGET_REQUIRED)
    bundle_root = (workspace_root / "wglink").resolve()
    bundle_path = Path(str(result.get("bundlePath") or "")).resolve()
    if bundle_path.parent != bundle_root:
        raise ValueError("CAD handoff bundle is outside the selected workspace.")
    if bundle_path.is_symlink() or not bundle_path.is_dir():
        raise ValueError("CAD handoff bundle is unavailable.")

    export_id = str(result.get("exportId") or "")
    bundle_id = str(result.get("bundleId") or "")
    if not export_id or not bundle_id:
        raise ValueError("CAD handoff is missing its export identity.")
    request_id = request_id or str(uuid.uuid4())
    destination = None
    if not expected_instance_id:
        status = read_fusion_status(
            data_dir, current_design_hash="", current_formula="", design_id=None
        )
        document_id = str(status.get("documentId") or "")
        destination = (
            {"kind": "document", "value": document_id}
            if status.get("running")
            and int(status.get("addinDeliveryVersion") or 0) >= 3
            and document_id
            else {"kind": "new_document", "value": request_id}
        )
    payload = {
        "target": "fusion360",
        "bundlePath": str(bundle_path),
        "bundleId": bundle_id,
        "exportId": export_id,
        "sequence": int(result.get("sequence") or 0),
        "designId": str(((result.get("identity") or {}) if isinstance(result.get("identity"), Mapping) else {}).get("designId") or "") or None,
        "expectedDocumentId": expected_document_id,
        "expectedInstanceId": expected_instance_id,
        "expectedReturnStateHash": expected_return_state_hash,
        "requestedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    if destination is not None:
        payload["destination"] = destination

    kind = UPDATE_LINK if expected_instance_id else INSERT_LINK
    target = (
        {
            "document_id": expected_document_id,
            "design_id": str(payload.get("designId") or ""),
            "instance_id": expected_instance_id,
            "expected_baseline": {
                "kind": "document_signature_hash",
                "value": expected_return_state_hash,
            },
        }
        if expected_instance_id
        else {"destination": destination, "export_id": export_id}
    )
    inputs = {"export_id": export_id} if expected_instance_id else {}

    def accept(_document: Mapping[str, object]) -> tuple[dict[str, object], str]:
        row, result = store.accept_operation(
            request_id,
            kind,
            request_digest(kind, target, inputs),
            target,
            inputs,
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
            outcome={"message": "The Fusion handoff request file could not be published."},
        )

    def superseded(operation_id: str) -> None:
        row = store.get_operation(operation_id)
        if row is not None:
            store.record_outcome(
                operation_id,
                int(row["attempt_generation"]),
                CANCELLED,
                reason="superseded",
                outcome={"message": f"Superseded by newer Fusion request {request_id}."},
            )

    def supersedes(existing: Mapping[str, object]) -> bool:
        # Only an update of the same exact link: an insert, or an update of
        # another instance or document, is a separate request.
        return bool(expected_instance_id) and (
            existing.get("expectedDocumentId") == expected_document_id
            and existing.get("expectedInstanceId") == expected_instance_id
        )

    published = publish_fusion_request(
        data_dir,
        HANDOFFS,
        payload,
        request_id,
        withdraw=supersedes,
        before_publish=accept,
        publish_failed=publication_failed,
        after_withdraw=superseded,
    )
    logger.info(
        "Fusion %s %s published for export %s%s.",
        "update" if expected_instance_id else "insert",
        published.request_id,
        export_id,
        f" of instance {expected_instance_id} in {expected_document_id}"
        if expected_instance_id
        else "",
    )
    for request_id in published.withdrawn:
        logger.info(
            "Fusion update %s was superseded by %s for the same link before the "
            "add-in started it.",
            request_id,
            published.request_id,
        )
    return published


__all__ = [
    "HANDOFFS_DIRECTORY",
    "IPC_SUBDIRECTORY",
    "UPDATE_TARGET_REQUIRED",
    "publish_fusion_handoff",
]
