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
    result: Mapping[str, object],
    *,
    expected_document_id: str | None = None,
    expected_instance_id: str | None = None,
    expected_return_state_hash: str | None = None,
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

    def supersedes(existing: Mapping[str, object]) -> bool:
        # Only an update of the same exact link: an insert, or an update of
        # another instance or document, is a separate request.
        return bool(expected_instance_id) and (
            existing.get("expectedDocumentId") == expected_document_id
            and existing.get("expectedInstanceId") == expected_instance_id
        )

    published = publish_fusion_request(
        data_dir, HANDOFFS, payload, str(uuid.uuid4()), withdraw=supersedes
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
