"""Read the short-lived document heartbeat published by WGLink in Fusion.

The heartbeat is presence information, not a source of CAD geometry or design
truth.  WG still computes the current design hash itself and only uses the
reported link identity to decide whether the active Fusion document contains
that exact design state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping


FUSION_STATUS_FILENAME = ".fusion-status.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
FUSION_STATUS_TTL = timedelta(seconds=20)
_MAX_STATUS_BYTES = 256 * 1024


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _link_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    instance_id = _string(value.get("instanceId"))
    if instance_id is None:
        return None
    parameter_count = value.get("parameterCount")
    if isinstance(parameter_count, bool) or not isinstance(parameter_count, int):
        parameter_count = 0
    parameter_drift_count = value.get("parameterDriftCount")
    if isinstance(parameter_drift_count, bool) or not isinstance(parameter_drift_count, int):
        parameter_drift_count = 0
    return {
        "instanceId": instance_id,
        "bundlePath": _string(value.get("bundlePath")),
        "designId": _string(value.get("designId")),
        "lineageId": _string(value.get("lineageId")),
        "editVersion": _string(value.get("editVersion")),
        "designHash": _string(value.get("designHash")),
        "designName": _string(value.get("designName")),
        "formula": _string(value.get("formula")),
        "configPresent": value.get("configPresent") is True,
        "parameterCount": max(0, parameter_count),
        "parameterDriftCount": max(0, parameter_drift_count),
        "localBodyState": _string(value.get("localBodyState")) or "unknown",
        "exportId": _string(value.get("exportId")),
        "exportSequence": _string(value.get("exportSequence")),
    }


def read_fusion_status(
    workspace_root: Path,
    *,
    current_design_hash: str,
    current_formula: str,
    design_id: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify the active Fusion document against the design on screen."""

    checked_at = now or datetime.now(timezone.utc)
    marker = workspace_root.resolve() / IPC_SUBDIRECTORY / FUSION_STATUS_FILENAME
    closed: dict[str, Any] = {
        "cadApplication": "fusion360",
        "state": "closed",
        "running": False,
        "updatedAt": None,
        "documentName": None,
        "currentFormula": current_formula,
        "fusionFormula": None,
        "link": None,
    }
    try:
        if marker.is_symlink() or not marker.is_file():
            return closed
        if marker.stat().st_size > _MAX_STATUS_BYTES:
            return closed
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return closed
    if not isinstance(payload, Mapping):
        return closed
    if payload.get("schemaVersion") != 1 or payload.get("cadApplication") != "fusion360":
        return closed
    updated_at = _timestamp(payload.get("updatedAt"))
    # A future timestamp is not trusted either. A minute of clock skew is much
    # more than these two processes on one machine should ever need.
    if (
        updated_at is None
        or checked_at - updated_at > FUSION_STATUS_TTL
        or updated_at - checked_at > timedelta(minutes=1)
    ):
        return closed

    base = {
        **closed,
        "running": True,
        "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
    }
    document = payload.get("document")
    if document is None:
        return {**base, "state": "no_document"}
    if not isinstance(document, Mapping):
        return closed
    document_name = _string(document.get("name"))
    raw_links = document.get("links")
    links = (
        [
            link
            for item in raw_links
            if (link := _link_payload(item)) is not None
        ]
        if isinstance(raw_links, list)
        else []
    )
    base["documentName"] = document_name

    matching = [link for link in links if design_id and link.get("designId") == design_id]
    if not matching and design_id is None:
        matching = [link for link in links if link.get("designHash") == current_design_hash]
    if not matching:
        return {**base, "state": "not_linked"}

    link = matching[0]
    fusion_hash = link.get("designHash")
    state = (
        "current"
        if (
            fusion_hash == current_design_hash
            and link.get("configPresent") is True
            and link.get("parameterDriftCount") == 0
            and link.get("localBodyState") == "unmodified"
        )
        else "stale"
    )
    return {
        **base,
        "state": state,
        "fusionFormula": link.get("formula"),
        "link": link,
    }


__all__ = [
    "FUSION_STATUS_FILENAME",
    "FUSION_STATUS_TTL",
    "read_fusion_status",
]
