"""Publish the one-shot handoff that WGLink consumes inside Fusion.

The bundle is the durable CAD-link artifact.  This small marker is only the
delivery notification: it tells a running (or newly launched) Fusion add-in
which completed export the user just asked to open.  Keeping it beside the
bundles makes the protocol work for custom workspaces without another setting.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping


HANDOFF_FILENAME = ".fusion-handoff.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"


def publish_fusion_handoff(
    data_dir: Path,
    workspace_root: Path,
    result: Mapping[str, object],
    *,
    expected_document_id: str | None = None,
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
        "schemaVersion": 1,
        "target": "fusion360",
        "bundlePath": str(bundle_path),
        "bundleId": bundle_id,
        "exportId": export_id,
        "sequence": int(result.get("sequence") or 0),
        "designId": str(((result.get("identity") or {}) if isinstance(result.get("identity"), Mapping) else {}).get("designId") or "") or None,
        "expectedDocumentId": expected_document_id,
        "expectedReturnStateHash": expected_return_state_hash,
        "requestedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }

    ipc_root = data_dir.resolve() / IPC_SUBDIRECTORY
    ipc_root.mkdir(parents=True, exist_ok=True)
    marker_path = ipc_root / HANDOFF_FILENAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{HANDOFF_FILENAME}.", dir=ipc_root
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, marker_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return marker_path


__all__ = ["HANDOFF_FILENAME", "publish_fusion_handoff"]
