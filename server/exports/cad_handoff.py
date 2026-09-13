"""Publish the one-shot handoff that WGLink consumes inside Fusion.

The bundle is the durable CAD-link artifact.  This small marker is only the
delivery notification: it tells a running (or newly launched) Fusion add-in
which completed export the user just asked to open.  Keeping it beside the
bundles makes the protocol work for custom workspaces without another setting.

Each handoff is published as its own file and in the legacy single slot, under
one request id (see ``server/cadlink/fusion_delivery.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
import uuid

from server.cadlink.fusion_delivery import HANDOFFS, IPC_SUBDIRECTORY, publish_fusion_request


HANDOFF_FILENAME = HANDOFFS.legacy_filename
HANDOFFS_DIRECTORY = HANDOFFS.directory


def publish_fusion_handoff(
    data_dir: Path,
    workspace_root: Path,
    result: Mapping[str, object],
    *,
    expected_document_id: str | None = None,
    expected_instance_id: str | None = None,
    expected_return_state_hash: str | None = None,
) -> Path:
    """Atomically announce one completed bundle to the Fusion add-in."""

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
    return publish_fusion_request(data_dir, HANDOFFS, payload, str(uuid.uuid4()))


__all__ = ["HANDOFFS_DIRECTORY", "HANDOFF_FILENAME", "IPC_SUBDIRECTORY", "publish_fusion_handoff"]
