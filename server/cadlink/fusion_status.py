"""Read the short-lived document heartbeat published by WGLink in Fusion.

The heartbeat is presence information, not a source of CAD geometry or design
truth.  WG still computes the current design hash itself and only uses the
reported link identity to decide whether the active Fusion document contains
that exact design state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any, Mapping

from server.platform.process import background_process_kwargs


FUSION_STATUS_FILENAME = ".fusion-status.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
FUSION_STATUS_TTL = timedelta(seconds=20)
_MAX_STATUS_BYTES = 256 * 1024


def fusion_process_running(*, system: str | None = None) -> bool:
    """Detect Fusion without activating it or claiming that WGLink is alive."""

    resolved = platform.system() if system is None else system
    if resolved == "Darwin":
        pgrep = shutil.which("pgrep")
        command = [pgrep, "-x", "Autodesk Fusion"] if pgrep else None
    elif resolved == "Windows":
        tasklist = shutil.which("tasklist")
        command = (
            [tasklist, "/FI", "IMAGENAME eq Fusion360.exe", "/NH"]
            if tasklist
            else None
        )
    else:
        command = None
    if command is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            timeout=2,
            **background_process_kwargs(system=resolved),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if resolved == "Windows":
        return completed.returncode == 0 and b"Fusion360.exe" in completed.stdout
    return completed.returncode == 0


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


def _fingerprint_hash(value: object) -> str | None:
    if not isinstance(value, (Mapping, list)):
        return None
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _returned_fingerprint_hash(returned_bundle: Path | None, design_id: str | None) -> str | None:
    if returned_bundle is None or design_id is None:
        return None
    try:
        resolved = returned_bundle.expanduser().resolve()
        if resolved.is_symlink() or not resolved.is_dir() or resolved.suffix != ".wgreturn":
            return None
        manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
        instances = manifest.get("instances")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(instances, list):
        return None
    for instance in instances:
        if not isinstance(instance, Mapping) or instance.get("design_id") != design_id:
            continue
        evidence = instance.get("body_evidence")
        if isinstance(evidence, Mapping):
            return _fingerprint_hash(evidence.get("observed_fingerprint"))
    return None


def _returned_document_signature_hash(returned_bundle: Path | None) -> str | None:
    if returned_bundle is None:
        return None
    try:
        resolved = returned_bundle.expanduser().resolve()
        if resolved.is_symlink() or not resolved.is_dir() or resolved.suffix != ".wgreturn":
            return None
        manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
        assembly = manifest.get("assembly")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(assembly, Mapping):
        return None
    return _string(assembly.get("signature_hash"))


def _returned_document_summary(returned_bundle: Path | None) -> tuple[int | None, str | None]:
    if returned_bundle is None:
        return None, None
    try:
        resolved = returned_bundle.expanduser().resolve()
        if resolved.is_symlink() or not resolved.is_dir() or resolved.suffix != ".wgreturn":
            return None, None
        manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
        assembly = manifest.get("assembly")
        raw_sources = manifest.get("sources")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None, None
    body_count = assembly.get("n_bodies_expected") if isinstance(assembly, Mapping) else None
    if isinstance(body_count, bool) or not isinstance(body_count, int):
        body_count = None
    if not isinstance(raw_sources, list):
        return body_count, None
    sources = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            return body_count, None
        sources.append({
            "id": raw.get("id"),
            "role": raw.get("role"),
            "instance_id": raw.get("instance_id"),
            "expected_connected_components": raw.get("expected_connected_components"),
            "observed": raw.get("observed"),
        })
    return body_count, _fingerprint_hash(sources)


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
    document_body_count = value.get("documentBodyCount")
    if isinstance(document_body_count, bool) or not isinstance(document_body_count, int):
        document_body_count = 0
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
        "bodyFingerprintHash": _string(value.get("bodyFingerprintHash")),
        "documentSignatureHash": _string(value.get("documentSignatureHash")),
        "documentBodyCount": max(0, document_body_count),
        "sourceStateHash": _string(value.get("sourceStateHash")),
        "exportId": _string(value.get("exportId")),
        "exportSequence": _string(value.get("exportSequence")),
    }


def read_fusion_status(
    workspace_root: Path,
    *,
    current_design_hash: str,
    current_formula: str,
    design_id: str | None,
    process_running: bool = False,
    returned_bundle: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify the active Fusion document against the design on screen."""

    checked_at = now or datetime.now(timezone.utc)
    marker = workspace_root.resolve() / IPC_SUBDIRECTORY / FUSION_STATUS_FILENAME
    closed: dict[str, Any] = {
        "cadApplication": "fusion360",
        "state": "addin_offline" if process_running else "closed",
        "running": False,
        "processRunning": process_running,
        "updatedAt": None,
        "documentName": None,
        "documentId": None,
        "currentFormula": current_formula,
        "fusionFormula": None,
        "link": None,
        "wgChangesAvailable": False,
        "fusionChangesAvailable": False,
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
        "processRunning": True,
        "sessionId": _string(payload.get("sessionId")),
        "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
    }
    document = payload.get("document")
    if document is None:
        return {**base, "state": "no_document"}
    if not isinstance(document, Mapping):
        return closed
    document_name = _string(document.get("name"))
    document_id = _string(document.get("id"))
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
    base["documentId"] = document_id

    matching = [link for link in links if design_id and link.get("designId") == design_id]
    if not matching and design_id is None:
        matching = [link for link in links if link.get("designHash") == current_design_hash]
    if not matching:
        return {**base, "state": "not_linked"}

    link = matching[0]
    fusion_hash = link.get("designHash")
    current_body_hash = link.get("bodyFingerprintHash")
    returned_body_hash = _returned_fingerprint_hash(returned_bundle, design_id)
    current_document_hash = link.get("documentSignatureHash")
    returned_document_hash = _returned_document_signature_hash(returned_bundle)
    returned_body_count, returned_source_hash = _returned_document_summary(returned_bundle)
    linked_body_changed = (
        (
            link.get("localBodyState") == "modified"
            or bool(link.get("parameterDriftCount"))
        )
        and (current_body_hash is None or current_body_hash != returned_body_hash)
    )
    document_changed = bool(
        current_document_hash
        and returned_document_hash
        and current_document_hash != returned_document_hash
    )
    inventory_changed = bool(
        returned_body_count is not None
        and link.get("documentBodyCount")
        and link.get("documentBodyCount") != returned_body_count
    )
    source_changed = bool(
        returned_source_hash
        and link.get("sourceStateHash")
        and link.get("sourceStateHash") != returned_source_hash
    )
    fusion_changes_available = (
        linked_body_changed or document_changed or inventory_changed or source_changed
    )
    wg_changes_available = (
        fusion_hash != current_design_hash or link.get("configPresent") is not True
    )
    state = (
        "current"
        if (
            not wg_changes_available
            and not fusion_changes_available
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
        "wgChangesAvailable": wg_changes_available,
        "fusionChangesAvailable": fusion_changes_available,
    }


__all__ = [
    "FUSION_STATUS_FILENAME",
    "FUSION_STATUS_TTL",
    "fusion_process_running",
    "read_fusion_status",
]
