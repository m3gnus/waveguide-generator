"""Ask the active WGLink session to publish its current Fusion geometry."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid


RETURN_REQUEST_FILENAME = ".fusion-return-request.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"


def publish_return_request(
    data_dir: Path,
    *,
    session_id: str,
    design_id: str,
    document_id: str,
    instance_id: str,
    expected_return_state_hash: str | None,
) -> tuple[Path, str]:
    if not session_id:
        raise ValueError("Fusion return request requires an active WGLink session.")
    folder = data_dir.resolve() / IPC_SUBDIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / RETURN_REQUEST_FILENAME
    if not design_id or not document_id or not instance_id:
        raise ValueError("Fusion return request requires an exact document and link target.")
    request_id = str(uuid.uuid4())
    payload = {
        "schemaVersion": 1,
        "target": "fusion360",
        "requestId": request_id,
        "sessionId": session_id,
        "designId": design_id,
        "documentId": document_id,
        "instanceId": instance_id,
        "expectedReturnStateHash": expected_return_state_hash,
        "requestedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{RETURN_REQUEST_FILENAME}.", dir=folder
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)
    return marker, request_id


__all__ = ["RETURN_REQUEST_FILENAME", "publish_return_request"]
