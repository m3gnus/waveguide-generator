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
_LEGACY_SIGNATURE_EXPLANATION = (
    "stale detection unavailable: this returned bundle predates wgreturn 1.1 "
    "and carries no document signature"
)
_SCOPED_SELECTION_EXPLANATION = (
    "stale detection unavailable: this return covers a selected assembly "
    "subtree, and Fusion reports its document signature for the whole root"
)


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


def _returned_fingerprint_hash(
    returned_bundle: Path | None,
    *,
    instance_id: str,
    design_id: str | None,
) -> str | None:
    """Read body evidence for one exact CAD instance.

    Older synthetic/preview manifests sometimes omitted ``instance_id``.  They
    remain usable only when the return has one instance.  A repeated design is
    never allowed to fall back to its first record: two placements can share
    every design/export field while carrying different bodies.
    """

    if returned_bundle is None:
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
    exact = [
        instance
        for instance in instances
        if isinstance(instance, Mapping)
        and instance.get("instance_id") == instance_id
    ]
    if len(exact) == 1:
        candidates = exact
    elif len(instances) == 1 and design_id is not None:
        only = instances[0]
        candidates = (
            [only]
            if isinstance(only, Mapping)
            and only.get("design_id") == design_id
            else []
        )
    else:
        candidates = []
    for instance in candidates:
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


def _returned_scope_selection(returned_bundle: Path | None) -> str | None:
    if returned_bundle is None:
        return None
    try:
        resolved = returned_bundle.expanduser().resolve()
        if resolved.is_symlink() or not resolved.is_dir() or resolved.suffix != ".wgreturn":
            return None
        manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
        scope = manifest.get("scope")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(scope, Mapping):
        return None
    return _string(scope.get("selection"))


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
    raw_drifted_parameters = value.get("driftedParameters")
    drifted_parameters = (
        sorted(set(raw_drifted_parameters))
        if (
            isinstance(raw_drifted_parameters, list)
            and all(isinstance(name, str) and bool(name) for name in raw_drifted_parameters)
        )
        else None
    )
    if drifted_parameters is not None:
        # New WGLink builds publish the names and derive the count from them.
        # Do the same at the trust boundary so a torn or hand-written heartbeat
        # cannot make the aggregate disagree with the fields WG will mark.
        parameter_drift_count = len(drifted_parameters)
    document_body_count = value.get("documentBodyCount")
    if isinstance(document_body_count, bool) or not isinstance(document_body_count, int):
        document_body_count = 0
    payload = {
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
    # Older add-ins publish only parameterDriftCount. Omitting the new member
    # retains that distinction so WG can keep the aggregate fallback instead
    # of pretending it knows which parameters changed.
    if drifted_parameters is not None:
        payload["driftedParameters"] = drifted_parameters
    return payload


def read_fusion_status(
    workspace_root: Path,
    *,
    current_design_hash: str,
    current_formula: str,
    design_id: str | None,
    instance_id: str | None = None,
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
        "adapterVersion": None,
        "workspaceRoot": None,
        "updatedAt": None,
        "documentName": None,
        "documentId": None,
        "currentFormula": current_formula,
        "fusionFormula": None,
        "link": None,
        "matchingLinks": [],
        "selectedInstanceId": None,
        "wgChangesAvailable": False,
        "fusionChangesAvailable": False,
        "documentChanged": False,
        "documentChangeDetectable": False,
        "staleDetectionExplanation": None,
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
        "adapterVersion": _string(payload.get("adapterVersion")),
        "workspaceRoot": _string(payload.get("workspaceRoot")),
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

    matching = sorted(matching, key=lambda candidate: str(candidate["instanceId"]))
    base["matchingLinks"] = matching
    selected = (
        [link for link in matching if link.get("instanceId") == instance_id]
        if instance_id is not None
        else matching
    )
    # One link retains the original zero-configuration path.  Repeated
    # instances of the same design require an exact choice, and a stale or
    # duplicated instance id fails closed instead of silently selecting the
    # first heartbeat record.
    if len(selected) != 1 or (instance_id is None and len(matching) != 1):
        return {**base, "state": "instance_selection_required"}

    link = selected[0]
    base["selectedInstanceId"] = link["instanceId"]
    fusion_hash = link.get("designHash")
    current_body_hash = link.get("bodyFingerprintHash")
    returned_body_hash = _returned_fingerprint_hash(
        returned_bundle,
        instance_id=str(link["instanceId"]),
        design_id=design_id,
    )
    current_document_hash = link.get("documentSignatureHash")
    returned_document_hash = _returned_document_signature_hash(returned_bundle)
    returned_scope_selection = _returned_scope_selection(returned_bundle)
    returned_body_count, returned_source_hash = _returned_document_summary(returned_bundle)
    # WGLink's heartbeat describes the document root. A scoped return describes
    # only the selected subtree, so none of the document-level aggregates can
    # be compared meaningfully. ``None`` retains compatibility for callers that
    # supply an incomplete synthetic manifest; validated bundles always carry
    # scope.selection.
    document_aggregates_detectable = returned_scope_selection in {None, "root"}
    linked_body_changed = (
        (
            link.get("localBodyState") == "modified"
            or bool(link.get("parameterDriftCount"))
        )
        and (current_body_hash is None or current_body_hash != returned_body_hash)
    )
    document_changed = bool(
        document_aggregates_detectable
        and current_document_hash
        and returned_document_hash
        and current_document_hash != returned_document_hash
    )
    document_change_detectable = bool(
        document_aggregates_detectable
        and current_document_hash
        and returned_document_hash
    )
    # A scoped return is not a legacy one: telling the user their bundle
    # predates wgreturn 1.1 when they simply picked a subtree in the Send
    # dialog sends them looking for the wrong problem.
    stale_detection_explanation: str | None = None
    if returned_bundle is not None:
        if not document_aggregates_detectable:
            stale_detection_explanation = _SCOPED_SELECTION_EXPLANATION
        elif returned_document_hash is None:
            stale_detection_explanation = _LEGACY_SIGNATURE_EXPLANATION
    inventory_changed = bool(
        document_aggregates_detectable
        and returned_body_count is not None
        and link.get("documentBodyCount")
        and link.get("documentBodyCount") != returned_body_count
    )
    source_changed = bool(
        document_aggregates_detectable
        and returned_source_hash
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
        "documentChanged": document_changed,
        "documentChangeDetectable": document_change_detectable,
        "staleDetectionExplanation": stale_detection_explanation,
    }


__all__ = [
    "FUSION_STATUS_FILENAME",
    "FUSION_STATUS_TTL",
    "fusion_process_running",
    "read_fusion_status",
]
