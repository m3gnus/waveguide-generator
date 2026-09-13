"""Ask the active WGLink session to publish its current Fusion geometry."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from .fusion_delivery import IPC_SUBDIRECTORY, RETURN_REQUESTS, publish_fusion_request


# The legacy slot. Each request is also published as its own file in
# RETURN_REQUESTS_DIRECTORY, under the same request id (see fusion_delivery).
RETURN_REQUEST_FILENAME = RETURN_REQUESTS.legacy_filename
RETURN_REQUESTS_DIRECTORY = RETURN_REQUESTS.directory


def publish_return_request(
    data_dir: Path,
    *,
    session_id: str,
    design_id: str,
    document_id: str,
    instance_id: str,
    expected_return_state_hash: str | None,
) -> tuple[Path, str]:
    """Publish one return request; return the legacy slot's path and the request id."""

    if not session_id:
        raise ValueError("Fusion return request requires an active WGLink session.")
    if not design_id or not document_id or not instance_id:
        raise ValueError("Fusion return request requires an exact document and link target.")
    request_id = str(uuid.uuid4())
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
    marker = publish_fusion_request(
        data_dir,
        RETURN_REQUESTS,
        payload,
        request_id,
        # The add-in runs only requests that name its own session, and a
        # request names the session current when it is made. Requests for any
        # other session can never run.
        withdraw=lambda existing: existing.get("sessionId") != session_id,
    )
    return marker, request_id


__all__ = [
    "IPC_SUBDIRECTORY",
    "RETURN_REQUESTS_DIRECTORY",
    "RETURN_REQUEST_FILENAME",
    "publish_return_request",
]
