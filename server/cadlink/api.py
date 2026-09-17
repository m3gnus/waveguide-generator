"""HTTP transport for workspace-scoped CAD-return ingestion records."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
from typing import Any, Awaitable, Literal, Mapping

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from server.cadlink.identity import SaveIdentity, design_hash
from server.design.schema import DesignConfig
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.mesh.artifact import (
    ImportedMeshArtifactError,
    read_verified_import_mesh,
    read_verified_import_viewport_mesh,
)
from server.platform.process import background_process_kwargs
from server.updates.restart import UPDATE_RESTART_PENDING
from server.workspace.api import (
    CadWorkspaceState,
    WorkspaceState,
    _path_segments,
    _strictly_inside,
    open_folder_command,
)
from server.workspace.archive import (
    CAD_SUBDIRECTORY,
    archive_cad_document,
    archive_folder_slug,
    captured_cad_document,
    design_archive_folder,
    place_run_cad_document,
    reclaim_captured_documents,
)

from .addin_update import last_refresh, loaded_addin_identity, poll_activation
from .fusion_status import ADDIN_OUTDATED_MESSAGE, fusion_process_running, read_fusion_status
from .fusion_status import heartbeat_now, select_heartbeat, settleable_heartbeat
from .fusion_outcomes import FUSION_KINDS, settle_from_heartbeat
from .fusion_delivery import (
    advertise_fusion_delivery,
    expire_unstarted_insert_handoffs,
    ipc_folder,
    recover_staged_fusion_requests,
)
from .fusion_return import publish_return_request
from .live.api import mount_live
from .ingest import (
    IngestRefusal,
    build_deferred_viewport,
    deferred_viewport_lookup_key,
    get_ingestion_record,
    ingest_bundle,
    resolve_deferred_viewport,
)
from .manual_solve import (
    OperationConflict,
    SnapshotNotRetained,
    UnknownIngest,
    create_manual_solve,
    recover_manual_solve,
)
# The Onshape leg publishes its bundles under WG's own data directory rather
# than a user-chosen WGLink folder, so re-ingesting one has to be anchored to
# that directory. Only the location constant is needed here; the Onshape
# routes and their credentials stay in ``server/cadlink/onshape/``.
from .onshape.return_leg import RETURN_SUBDIRECTORY as ONSHAPE_RETURN_SUBDIRECTORY
from .operations import CANCELLED, PREPARE_AND_SOLVE, STATES, TERMINAL_STATES
from .preparation import (
    DismissalUnconfirmed,
    PreparationContext,
    PreparationInput,
    dismiss_operation,
    operation_summary,
    prepare_operation,
    recover_operations,
    run_delivery_pass,
)
from .project_setup import SOLVER_SELECTION, inventory_sha256
from .setup import setup_content, setup_digest, validate_setup
from .roles import canonical_source_role
from .store import CadLinkStore
from .wgreturn import WgReturnError, declared_domain_planes


logger = logging.getLogger(__name__)


_INGEST_ID = re.compile(r"^wgi_[0-9A-HJKMNP-TV-Z]{26}$")
_RETURN_INVENTORY_CACHE: dict[
    tuple[str, int, int], tuple[dict[str, Any], Mapping[str, Any] | None]
] = {}
_RETURN_INVENTORY_CACHE_LOCK = threading.Lock()

#: Display tessellations currently being built for an already-published record,
#: keyed by the record's viewport lookup key. This only answers "is it still
#: coming?" -- the artifact itself is content-addressed on disk, so a restart
#: that empties this map costs one rebuild, never a wrong answer.
_DEFERRED_VIEWPORTS: dict[str, asyncio.Task[Any]] = {}

#: asyncio keeps only a weak reference to a running task, so a fire-and-forget
#: task that nobody holds can be collected mid-flight. This is that holder.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

#: Captured CAD documents still being copied, keyed by return-state digest.
#: The run archive joins on this: a solve that finishes before its own capture
#: does must wait for the copy rather than find nothing to place.
_CAD_DOCUMENT_CAPTURES: dict[str, asyncio.Task[Any]] = {}

#: How long a run archive waits for an in-flight capture of its own model.
#: A Fusion archive is tens of megabytes on a possibly cloud-synced volume, so
#: this is generous; it is a bound against waiting forever, not an expectation.
_CAPTURE_WAIT_SECONDS = 120.0


def _spawn_background(coroutine: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coroutine)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _capture_key(return_state_hash: object) -> str:
    """The registry key one model state is tracked under while it is copying.

    The hex half of the digest, lowercased, so the key a capture registers
    under and the key a run archive looks up cannot differ over the ``sha256:``
    prefix alone.
    """

    text = str(return_state_hash or "").strip()
    _, _, hex_part = text.partition(":")
    return (hex_part or text).lower()


def _schedule_cad_document_capture(
    state: Any,
    store: CadLinkStore,
    bundle_path: Path,
    record: Mapping[str, Any],
) -> None:
    """Start the capture the ingestion response deliberately does not wait for.

    Registered by return state, because a fast solve can complete and archive
    itself while this is still copying tens of megabytes: the run archive
    joins the task rather than finding no document to place.
    """

    document = record.get("document")
    key = _capture_key(
        (document if isinstance(document, Mapping) else {}).get("return_state_hash")
    )
    task = _spawn_background(
        _archive_cad_document(
            getattr(state, "workspace", None),
            getattr(state, "cad_workspace", None),
            getattr(state, "jobs_runtime", None),
            store,
            bundle_path,
            record,
        )
    )
    if not key:
        return
    _CAD_DOCUMENT_CAPTURES[key] = task
    task.add_done_callback(
        lambda finished: _CAD_DOCUMENT_CAPTURES.pop(key, None)
        if _CAD_DOCUMENT_CAPTURES.get(key) is finished
        else None
    )


async def _await_cad_document_capture(return_state_hash: str) -> None:
    """Wait for this model state's capture when one is still in flight."""

    task = _CAD_DOCUMENT_CAPTURES.get(_capture_key(return_state_hash))
    if task is None or task.done():
        return
    if task.get_loop() is not asyncio.get_running_loop():
        # Not this loop's task, so there is nothing here that can be joined.
        # One process runs one loop, so this is the guard against a stale
        # registry entry rather than an expected state.
        return
    # ``wait`` joins the task without cancelling it and without re-raising its
    # exception: capture is advisory on both sides of this, and the caller
    # reports a missing copy on its own terms.
    await asyncio.wait({task}, timeout=_CAPTURE_WAIT_SECONDS)


def _schedule_deferred_viewport(record: Mapping[str, Any], data_dir: Path) -> None:
    """Start the display tessellation the ingestion response did not wait for."""

    lookup_key = deferred_viewport_lookup_key(record)
    if lookup_key is None or lookup_key in _DEFERRED_VIEWPORTS:
        return

    async def build() -> None:
        try:
            await asyncio.to_thread(build_deferred_viewport, record, data_dir)
        except Exception as exc:  # noqa: BLE001
            # Advisory by construction: the solve mesh is already on screen and
            # is what the solve uses. A failure here costs display fidelity.
            logger.warning(
                "Deferred CAD viewport tessellation failed for %s: %s",
                record.get("ingest_id"),
                exc,
            )
        finally:
            _DEFERRED_VIEWPORTS.pop(lookup_key, None)

    _DEFERRED_VIEWPORTS[lookup_key] = asyncio.create_task(build())


def _parse_return_manifest(path: Path) -> Mapping[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        raise ValueError("manifest inventory has the wrong shape")
    return parsed


class ImportedMeshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rigid_size_mm: float = Field(alias="rigidSizeMm", gt=0, allow_inf_nan=False)
    transition_mm: float = Field(alias="transitionMm", gt=0, allow_inf_nan=False)
    source_size_mm: dict[str, float] = Field(alias="sourceSizeMm")


class CadReturnIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    bundle_path: str = Field(alias="bundlePath", min_length=1)
    # Which permitted area ``bundlePath`` is relative to. A WGLink return lives
    # under the folder the user selected in Settings; an Onshape return is
    # written by the server under its own data directory and there is no
    # WGLink folder to select. The client states the origin, never a location:
    # the path stays relative for both, and the server owns each root. Default
    # "wglink" so every caller that predates the Onshape leg is unchanged.
    bundle_origin: Literal["wglink", "onshape"] = Field(
        default="wglink", alias="bundleOrigin"
    )
    mesh: ImportedMeshRequest
    skipped_source_ids: list[str] = Field(default_factory=list, alias="skippedSourceIds")
    area_drift_overrides: list[str] = Field(
        default_factory=list, alias="areaDriftOverrides"
    )
    expected_design_id: str | None = Field(default=None, alias="expectedDesignId")
    expected_instance_id: str | None = Field(
        default=None, alias="expectedInstanceId", min_length=1
    )
    symmetry_mode: Literal["auto", "full"] = Field(
        default="auto", alias="symmetryMode"
    )
    # Chord deviation each panel may have from the true CAD surface, in mm.
    # Omitted means the server default, IMPORTED_SURFACE_DEVIATION_MM = 0.15 mm.
    # This is the imported mesh's cost dial: it replaced a segments-per-2pi
    # control, which spent triangles in proportion to curvature radius and so
    # over-refined small fillets while under-refining large sweeps. Measured
    # across five geometries, the default is better than the rule it replaces on
    # peak, p95 and rms at a slightly lower triangle count, and raises the HF
    # wall frequency limit 3620 -> 5347 Hz on the reference return.
    #
    # The band stops at 0.35 rather than going higher because above it the
    # request is coarser than the user's rigid target, ``Mesh.MeshSizeMax``
    # sets the peak instead, and the field would stop controlling the quantity
    # its name promises.
    surface_deviation_mm: float | None = Field(
        default=None, alias="surfaceDeviationMm", ge=0.1, le=0.35
    )


class FusionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design: DesignConfig
    identity: SaveIdentity | None = None
    instance_id: str | None = Field(default=None, alias="instanceId", min_length=1)
    return_bundle_path: str | None = Field(default=None, alias="returnBundlePath")


class FusionReturnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design_id: str = Field(alias="designId", min_length=1)
    document_id: str = Field(alias="documentId", min_length=1)
    instance_id: str = Field(alias="instanceId", min_length=1)
    expected_return_state_hash: str | None = Field(
        default=None, alias="expectedReturnStateHash"
    )


class SolveCommandOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command_id: str = Field(alias="commandId", min_length=1)
    state: str = Field(pattern="^(accepted|refused|blocked)$")
    job_id: str | None = Field(default=None, alias="jobId")
    reason: str | None = None


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


def _realized_dimensions_payload(
    status: Mapping[str, Any],
    store: CadLinkStore,
    *,
    current_design_hash: str,
) -> dict[str, Any]:
    """Read the linked export's immutable CAD parameter contract."""

    link = status.get("link")
    if not isinstance(link, Mapping):
        return {
            # A closed/offline Fusion session cannot prove the design has never
            # been linked: there may be several documents and no active instance
            # from which to select the exact immutable export.
            "state": (
                "no_link" if status.get("state") == "not_linked" else "link_unavailable"
            ),
            "instanceId": None,
            "exportId": None,
            "parameters": [],
        }

    instance_id = link.get("instanceId")
    instance_id = instance_id if isinstance(instance_id, str) and instance_id else None
    export_id = link.get("exportId")
    export_id = export_id if isinstance(export_id, str) and export_id else None
    base = {
        "instanceId": instance_id,
        "exportId": export_id,
        "parameters": [],
    }
    if export_id is None:
        return {"state": "export_missing", **base}

    exported = store.get_export(export_id)
    if exported is None:
        return {"state": "export_missing", **base}
    try:
        manifest = json.loads(str(exported["manifest_json"]))
    except (KeyError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {"state": "unavailable", **base}
    if not isinstance(manifest, Mapping):
        return {"state": "unavailable", **base}

    raw_parameters = manifest.get("parameters")
    if not isinstance(raw_parameters, list) or not raw_parameters:
        return {"state": "not_captured", **base}

    parameters: list[dict[str, Any]] = []
    for raw in raw_parameters:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("name")
        value = raw.get("value")
        if (
            not isinstance(name, str)
            or not name
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            continue
        unit = raw.get("unit")
        role = raw.get("role")
        # Do not infer interface membership from a suffix list. The manifest role
        # is the API boundary, while a missing role keeps pre-role bundles aligned
        # with the existing CAD adapters' backwards-compatible interpretation.
        parameters.append(
            {
                "instanceId": instance_id,
                "name": name,
                "value": float(value),
                "unit": unit if isinstance(unit, str) and unit else None,
                "role": role if isinstance(role, str) and role else "interface",
            }
        )
    if not parameters:
        return {"state": "unavailable", **base}

    # The immutable export row, not the live Fusion freshness aggregate, says
    # whether these particular values describe the design currently on screen.
    payload_state = (
        "current"
        if exported.get("design_hash") == current_design_hash
        else "stale"
    )
    return {"state": payload_state, **base, "parameters": parameters}


def _return_inventory(
    workspace_root: Path,
) -> list[tuple[dict[str, Any], Mapping[str, Any] | None, Path]]:
    returns_root = workspace_root / "wgreturn"
    if not returns_root.is_dir():
        return []
    items: list[tuple[dict[str, Any], Mapping[str, Any] | None, Path]] = []
    active_cache_keys: set[tuple[str, int, int]] = set()
    for candidate in sorted(returns_root.glob("*.wgreturn"), key=lambda item: item.name.casefold()):
        try:
            resolved = candidate.resolve()
            _strictly_inside(resolved, workspace_root, "bundlePath")
            if not resolved.is_dir():
                continue
            candidate_stat = candidate.stat()
            modified_at = datetime.fromtimestamp(
                candidate_stat.st_mtime, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except (OSError, ValueError):
            continue
        item: dict[str, Any] = {
            "name": candidate.name,
            "bundlePath": f"wgreturn/{candidate.name}",
            "modifiedAt": modified_at,
            "readable": False,
            "documentName": None,
            "documentNativeId": None,
            "declaredCutPlanes": [],
            "requestId": None,
            "sourceCount": None,
            "instanceCount": None,
            "designIds": [],
            "solverAnchorInstanceId": None,
            "instances": [],
            "sources": [],
        }
        manifest: Mapping[str, Any] | None = None
        cache_key: tuple[str, int, int] | None = None
        try:
            manifest_path = resolved / "wgreturn.json"
            manifest_stat = manifest_path.stat()
            cache_key = (
                str(manifest_path),
                manifest_stat.st_mtime_ns,
                manifest_stat.st_size,
            )
            active_cache_keys.add(cache_key)
            with _RETURN_INVENTORY_CACHE_LOCK:
                cached = _RETURN_INVENTORY_CACHE.get(cache_key)
            if cached is not None:
                cached_item = dict(cached[0])
                cached_item["modifiedAt"] = modified_at
                items.append((cached_item, cached[1], resolved))
                continue
            manifest = _parse_return_manifest(manifest_path)
            document = manifest.get("document")
            sources = manifest.get("sources")
            instances = manifest.get("instances")
            coordinates = manifest.get("coordinate_system")
            scope = manifest.get("scope")
            if (
                not isinstance(document, dict)
                or not isinstance(sources, list)
                or not isinstance(instances, list)
            ):
                raise ValueError("manifest inventory has the wrong shape")
            coordinates = coordinates if isinstance(coordinates, dict) else {}
            scope = scope if isinstance(scope, dict) else {}
            included = scope.get("included")
            included = included if isinstance(included, list) else []
            source_summaries = []
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError("manifest source has the wrong shape")
                raw_suggested_resolution = source.get("suggested_resolution_mm")
                suggested_resolution = (
                    None
                    if raw_suggested_resolution is None
                    else float(raw_suggested_resolution)
                )
                if suggested_resolution is not None and (
                    not math.isfinite(suggested_resolution)
                    or suggested_resolution <= 0
                ):
                    raise ValueError("manifest source suggestion is not finite and positive")
                source_summaries.append(
                    {
                        "id": str(source["id"]),
                        "role": canonical_source_role(
                            str(source.get("role") or "source")
                        ),
                        "instanceId": (
                            str(source["instance_id"])
                            if source.get("instance_id")
                            else None
                        ),
                        "required": bool(source.get("required", True)),
                        "suggestedResolutionMm": suggested_resolution,
                        "defaultDriveChannelId": str(
                            source["default_drive_channel_id"]
                        ),
                    }
                )
            instance_summaries = []
            for instance in instances:
                if not isinstance(instance, dict):
                    raise ValueError("manifest instance has the wrong shape")
                instance_id = str(instance["instance_id"])
                body_evidence = instance.get("body_evidence")
                observed = (
                    body_evidence.get("observed_fingerprint")
                    if isinstance(body_evidence, Mapping)
                    else None
                )
                matrix = instance.get("assembly_from_link")
                source_records = [
                    source
                    for source in source_summaries
                    if source["instanceId"] == instance_id
                ]
                body_object_ids = sorted(
                    str(body["object_id"])
                    for body in included
                    if isinstance(body, dict)
                    and body.get("wglink_instance_id") == instance_id
                    and body.get("object_id")
                )
                instance_summaries.append(
                    {
                        "instanceId": instance_id,
                        "designId": str(instance.get("design_id") or "") or None,
                        "occurrencePath": (
                            str(instance["occurrence_path"])
                            if instance.get("occurrence_path")
                            else None
                        ),
                        "bodyObjectIds": body_object_ids,
                        "bodyFingerprintHash": (
                            "sha256:"
                            + hashlib.sha256(
                                json.dumps(
                                    observed,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest()
                            if isinstance(observed, Mapping)
                            else None
                        ),
                        "transformHash": (
                            "sha256:"
                            + hashlib.sha256(
                                json.dumps(
                                    matrix,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest()
                            if isinstance(matrix, list)
                            else None
                        ),
                        "sourceIds": sorted(
                            str(source["id"]) for source in source_records
                        ),
                        "driveChannelIds": sorted(
                            {str(source["defaultDriveChannelId"]) for source in source_records}
                        ),
                    }
                )
            item.update(
                {
                    "readable": True,
                    "documentName": str(document.get("name") or candidate.stem),
                    # The CAD document this is a return of, when the adapter
                    # recorded it: a newer return of the same document is the
                    # only one that may supersede a solve request made for it.
                    "documentNativeId": (
                        str(document.get("native_id"))
                        if document.get("native_id")
                        else None
                    ),
                    "requestId": (
                        str(document.get("request_id"))
                        if document.get("request_id")
                        else None
                    ),
                    "sourceCount": len(sources),
                    "instanceCount": len(instances),
                    # The domain the CAD author declared, so the panel can say
                    # which model is about to be solved before anyone presses
                    # anything. Empty for a full model, which is every bundle
                    # written before the declaration existed.
                    "declaredCutPlanes": list(declared_domain_planes(manifest)),
                    "solverAnchorInstanceId": (
                        str(coordinates["solver_anchor_instance_id"])
                        if coordinates.get("solver_anchor_instance_id")
                        else None
                    ),
                    "designIds": sorted({
                        str(instance["design_id"])
                        for instance in instances
                        if isinstance(instance, dict) and instance.get("design_id")
                    }),
                    "instances": sorted(
                        instance_summaries, key=lambda value: value["instanceId"]
                    ),
                    "sources": source_summaries,
                }
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            item["reason"] = str(exc) or "Manifest is unreadable"
        if cache_key is not None:
            with _RETURN_INVENTORY_CACHE_LOCK:
                _RETURN_INVENTORY_CACHE[cache_key] = (dict(item), manifest)
        items.append((item, manifest, resolved))
    root_prefix = str(returns_root.resolve()) + "/"
    with _RETURN_INVENTORY_CACHE_LOCK:
        stale = [
            key
            for key in _RETURN_INVENTORY_CACHE
            if key[0].startswith(root_prefix) and key not in active_cache_keys
        ]
        for key in stale:
            del _RETURN_INVENTORY_CACHE[key]
    return sorted(items, key=lambda entry: entry[0]["modifiedAt"], reverse=True)


def _return_listing(workspace_root: Path) -> list[dict[str, Any]]:
    return [item for item, _manifest, _path in _return_inventory(workspace_root)]


def _manifest_matches_return(
    manifest: Mapping[str, Any], design_id: str | None, instance_id: str | None
) -> bool:
    instances = manifest.get("instances")
    if design_id is None and instance_id is None:
        return True
    return isinstance(instances, list) and any(
        isinstance(instance, Mapping)
        and (design_id is None or instance.get("design_id") == design_id)
        and (instance_id is None or instance.get("instance_id") == instance_id)
        for instance in instances
    )


def _resolve_return_bundle(
    workspace_root: Path,
    requested_path: str | None,
    design_id: str | None,
    instance_id: str | None,
) -> tuple[Path | None, Mapping[str, Any] | None]:
    inventory = _return_inventory(workspace_root)
    if requested_path:
        selected_return = (workspace_root / requested_path).resolve()
        _strictly_inside(selected_return, workspace_root, "returnBundlePath")
        if (
            selected_return.is_symlink()
            or not selected_return.is_dir()
            or selected_return.suffix != ".wgreturn"
        ):
            raise ValueError("Selected CAD return is unavailable.")
        selected = next(
            (entry for entry in inventory if entry[2] == selected_return), None
        )
        if selected is None or not selected[0].get("readable") or selected[1] is None:
            raise ValueError("Selected CAD return is unavailable.")
        if _manifest_matches_return(selected[1], design_id, instance_id):
            return selected_return, selected[1]

    if design_id is not None:
        for item, manifest, candidate_path in inventory:
            if (
                item.get("readable")
                and manifest is not None
                and _manifest_matches_return(manifest, design_id, instance_id)
            ):
                return candidate_path, manifest
    return None, None


@router.get("/returns")
async def list_returns(request: Request) -> dict[str, Any]:
    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    if selected is None:
        return {"items": [], "cadFolderConfigured": False}
    workspace_root = selected.resolve()
    return {
        "items": await asyncio.to_thread(_return_listing, workspace_root),
        "cadFolderConfigured": True,
    }


@router.get("/designs")
async def list_designs(request: Request) -> dict[str, Any]:
    """Expose recent CAD-linked projects as a local project picker.

    Each lineage contributes only its newest head and reports the archive
    folder its runs and captured CAD documents share, so a project can be
    opened, revealed and counted from one listing rather than from three that
    could disagree.
    """

    store: CadLinkStore = request.app.state.cadlink_store

    def read_projects() -> tuple[
        list[dict[str, Any]], dict[str, str], dict[str, Any], list[dict[str, Any]]
    ]:
        rows = store.list_projects()
        stems = {
            str(row["lineage_id"]): _project_archive_stem(
                store, str(row["lineage_id"]), str(row["filename"])
            )
            for row in rows
        }
        documents = {
            str(row["lineage_id"]): (
                store.get_lineage_cad_names(str(row["lineage_id"])) or {}
            ).get("document_name")
            for row in rows
        }
        return rows, stems, documents, store.list_cad_document_projects()

    rows, stems, documents, cad_only = await asyncio.to_thread(read_projects)
    items = [
        {
            "designId": str(row["design_id"]),
            "lineageId": str(row["lineage_id"]),
            "editVersion": int(row["edit_version"]),
            "designHash": str(row["design_hash"]),
            "filename": str(row["filename"]),
            "archiveStem": stems.get(str(row["lineage_id"])) or None,
            "documentName": documents.get(str(row["lineage_id"])),
            "branchedFromDesignId": row.get("branched_from_design_id"),
            "branchedFromEditVersion": row.get("branched_from_edit_version"),
            "exportCount": int(row.get("export_count") or 0),
            "lastExportedAt": row.get("last_exported_at"),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }
        for row in rows
    ]
    # A project that exists only in CAD has no design row to list, but it is a
    # project all the same: runs, an archive folder and a history hang off it.
    # It carries no designId because there is no snapshot to open.
    items.extend(
        {
            "designId": None,
            "lineageId": str(row["lineage_id"]),
            "editVersion": None,
            "designHash": None,
            "filename": None,
            "archiveStem": str(row.get("archive_stem") or "").strip()
            or str(row.get("document_name") or "").strip()
            or None,
            "documentName": row.get("document_name"),
            "branchedFromDesignId": None,
            "branchedFromEditVersion": None,
            "exportCount": 0,
            "lastExportedAt": None,
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }
        for row in cad_only
    )
    items.sort(key=lambda item: str(item["updatedAt"]), reverse=True)
    return {"items": items}


@router.get("/designs/{design_id}")
async def get_design(design_id: str, request: Request) -> dict[str, Any]:
    """Return the registry's exact current snapshot for one linked design."""

    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_design, design_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CAD-linked design not found")
    return {
        "designId": str(row["design_id"]),
        "lineageId": str(row["lineage_id"]),
        "editVersion": int(row["edit_version"]),
        "filename": str(row["filename"]),
        "updatedAt": str(row["updated_at"]),
        "text": str(row["snapshot_text"]),
    }


@router.post("/fusion-status")
async def fusion_status(
    payload: FusionStatusRequest, request: Request
) -> dict[str, Any]:
    """Report whether the design on screen is open and current in Fusion."""

    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    workspace_root = selected.resolve() if selected is not None else None
    returned_bundle: Path | None = None
    returned_manifest: Mapping[str, Any] | None = None
    if payload.return_bundle_path and workspace_root is None:
        raise HTTPException(
            status_code=422, detail="No WGLink folder has been selected."
        )
    if workspace_root is not None and (
        payload.return_bundle_path or payload.identity is not None
    ):
        try:
            returned_bundle, returned_manifest = await asyncio.to_thread(
                _resolve_return_bundle,
                workspace_root,
                payload.return_bundle_path,
                payload.identity.design_id if payload.identity else None,
                payload.instance_id,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    current_hash = design_hash(payload.design)
    store: CadLinkStore = request.app.state.cadlink_store
    await asyncio.to_thread(
        expire_unstarted_insert_handoffs,
        Path(request.app.state.data_dir),
        record_expired=lambda operation_id: _record_expired_insert(store, operation_id),
    )
    # One selection at one instant for this answer: settlement and the status
    # below see the same heartbeat, whichever transport it came by, even if the
    # file changes or the heartbeat ages out while settlement runs.
    checked_at = heartbeat_now()
    selected_heartbeat = await asyncio.to_thread(
        select_heartbeat, Path(request.app.state.data_dir), checked_at
    )
    heartbeat = settleable_heartbeat(selected_heartbeat)
    if heartbeat is not None:
        await asyncio.to_thread(
            settle_from_heartbeat,
            store,
            heartbeat,
            ipc_folder(Path(request.app.state.data_dir)),
        )
    status = await asyncio.to_thread(
        read_fusion_status,
        Path(request.app.state.data_dir),
        current_design_hash=current_hash,
        current_formula=payload.design.root.formula,
        design_id=payload.identity.design_id if payload.identity else None,
        instance_id=payload.instance_id,
        process_running=await asyncio.to_thread(fusion_process_running),
        returned_bundle=returned_bundle,
        returned_manifest=returned_manifest,
        now=checked_at,
        heartbeat=selected_heartbeat,
    )
    # WGLink activation waits for Fusion to close, and while WG runs this poll
    # is what finishes it (server/cadlink/addin_update.py). Once WG has decided,
    # the report goes with every state, so the UI can name a pending, activated,
    # superseded or failed activation anywhere; an outdated add-in always gets
    # it, to name the remedy. Activation turned off is said only there.
    await poll_activation(
        fusion_open=bool(status.get("processRunning") or status.get("running")),
        data_dir=Path(request.app.state.data_dir),
    )
    report = last_refresh()
    if status.get("state") == "addin_outdated" or (
        report is not None and report.get("verdict") != "disabled"
    ):
        # What the add-in reported loading in its live session, now -- not the
        # snapshot the activation pass stored when it decided.
        status["addinRefresh"] = report
        if report is not None:
            report["loadedIdentity"] = loaded_addin_identity(Path(request.app.state.data_dir))
    status["cadFolderConfigured"] = selected is not None
    status["cadFolderPath"] = str(selected) if selected is not None else None
    status["cadConnectionIssue"] = None
    if selected is not None and status.get("running"):
        reported_root = status.get("workspaceRoot")
        if not status.get("adapterVersion"):
            status["cadConnectionIssue"] = "addin_upgrade_required"
        elif not reported_root:
            status["cadConnectionIssue"] = "folder_unreadable"
        else:
            try:
                if Path(str(reported_root)).expanduser().resolve() != selected.resolve():
                    status["cadConnectionIssue"] = "folder_mismatch"
            except (OSError, ValueError):
                status["cadConnectionIssue"] = "folder_mismatch"
    status["realizedDimensions"] = await asyncio.to_thread(
        _realized_dimensions_payload,
        status,
        store,
        current_design_hash=current_hash,
    )
    return status


def _record_expired_insert(store: CadLinkStore, operation_id: str) -> None:
    row = store.get_operation(operation_id)
    if row is None:
        return
    store.record_outcome(
        operation_id,
        int(row["attempt_generation"]),
        CANCELLED,
        reason="expired",
        outcome={"message": "The unstarted Fusion insert expired after 30 minutes."},
    )


@router.post("/request-fusion-return")
async def request_fusion_return(
    payload: FusionReturnRequest, request: Request
) -> dict[str, str]:
    """Ask the connected add-in to export the active Fusion document to WG."""

    status = await asyncio.to_thread(
        read_fusion_status,
        Path(request.app.state.data_dir),
        current_design_hash="",
        current_formula="",
        design_id=payload.design_id,
        instance_id=payload.instance_id,
        process_running=await asyncio.to_thread(fusion_process_running),
    )
    session_id = str(status.get("sessionId") or "")
    if not status.get("running") or not session_id:
        raise HTTPException(
            status_code=409,
            detail="Fusion is running, but WGLink is offline. Restart WGLink in Fusion first.",
        )
    if status.get("state") == "addin_outdated":
        raise HTTPException(status_code=409, detail=ADDIN_OUTDATED_MESSAGE)
    link = status.get("link")
    if (
        status.get("documentId") != payload.document_id
        or not isinstance(link, dict)
        or link.get("designId") != payload.design_id
        or link.get("instanceId") != payload.instance_id
    ):
        raise HTTPException(
            status_code=409,
            detail="The active Fusion document changed. Refresh CAD Link and try again.",
        )
    if not payload.expected_return_state_hash:
        # The add-in exports only the exact model WG displayed, which it proves
        # against this token; without it there is nothing to prove.
        raise HTTPException(
            status_code=409,
            detail=(
                "Fusion has not reported the model's state yet. Refresh CAD Link, "
                "wait for Fusion to report the model, and try again."
            ),
        )
    _marker, request_id = await asyncio.to_thread(
        publish_return_request,
        Path(request.app.state.data_dir),
        getattr(request.app.state, "cadlink_store", None)
        or CadLinkStore.for_data_dir(Path(request.app.state.data_dir)),
        session_id=session_id,
        design_id=payload.design_id,
        document_id=payload.document_id,
        instance_id=payload.instance_id,
        expected_return_state_hash=payload.expected_return_state_hash,
    )
    return {
        "status": "requested",
        "requestId": request_id,
        "documentName": str(status.get("documentName") or "Untitled"),
    }


@router.get("/solve-command")
async def get_solve_command(request: Request) -> dict[str, Any]:
    """Nothing pending, always: the backend is the one consumer of solve commands.

    A page from a build before the backend owned solves (v0.3.2,
    v0.3.3-rc.1) polls this route and acts on what it hands out, and it can
    still be open in a browser tab across an update restart. This answer is
    the one it reads as "nothing pending": the route claims no delivery,
    records no outcome and hands out no command such a page could start a
    solve from (docs/architecture/CAD-OPERATIONS.md, "Solve-command
    compatibility").
    """

    return {"command": None}


@router.post("/solve-command/outcome")
async def post_solve_command_outcome(
    payload: SolveCommandOutcome, request: Request
) -> dict[str, Any]:
    """Answered and ignored: a solve command's outcome is the backend's to record.

    The same older page reports here after its own Solve or Dismiss. A 2xx
    answer lets it drop its copy; an error would leave its Dismiss card stuck,
    or report a failure after a job that exists. Nothing is recorded: a job
    such a page submitted under ``cad-solve:<commandId>`` is the operation's
    outcome, and the backend finds it through that key.
    """

    return {"commandId": payload.command_id, "recorded": False, "cleared": True}


def _ingest_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IngestRefusal):
        detail: str | dict[str, Any] = str(exc)
        if exc.area_drift_sources:
            detail = {
                "message": str(exc),
                "area_drift_sources": list(exc.area_drift_sources),
            }
        return HTTPException(status_code=409 if exc.corruption else 422, detail=detail)
    if isinstance(exc, (WgReturnError, ValueError, TypeError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (ImportError, RuntimeError)):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=f"CAD-return ingestion failed: {exc}")


def _onshape_return_root(request: Request) -> Path:
    """The one directory the Onshape leg publishes its return bundles into."""

    return (Path(request.app.state.data_dir).resolve() / ONSHAPE_RETURN_SUBDIRECTORY).resolve()


def _resolve_ingest_bundle(payload: CadReturnIngestRequest, request: Request) -> Path:
    """Resolve the requested bundle inside the area its origin permits.

    Both origins are containment-checked, and both are checked the same way:
    the request names a *relative* path, the server picks the root, and the
    resolved result must land strictly inside that root. What differs is only
    which root -- the selected WGLink folder, or WG's own Onshape return
    directory. Never accept an absolute path for either: a caller that can
    state a filesystem location can name one outside the permitted area, and
    the relative-path rule is the whole of what prevents that.
    """

    if payload.bundle_origin == "onshape":
        # No WGLink folder is involved: the Onshape leg writes into WG's data
        # directory, so requiring a selected folder here would refuse a valid
        # Onshape-only setup outright.
        root = _onshape_return_root(request)
        label = "WG's Onshape return directory"
    else:
        workspace: WorkspaceState = request.app.state.cad_workspace
        selected = workspace.selected_path()
        if selected is None:
            raise HTTPException(
                status_code=409,
                detail="No WGLink folder has been selected. Choose one in Settings → CAD Link first.",
            )
        root = selected.resolve()
        label = "the selected workspace"
    try:
        segments = _path_segments(payload.bundle_path, "bundlePath")
        if payload.bundle_origin != "onshape" and (
            not segments or segments[0].casefold() != "wgreturn"
        ):
            raise ValueError("bundlePath must be under the selected workspace's wgreturn/ directory")
        if not segments[-1].endswith(".wgreturn"):
            raise ValueError("bundlePath must name a .wgreturn bundle directory")
        bundle_path = root.joinpath(*segments).resolve()
        _strictly_inside(bundle_path, root, "bundlePath")
    except ValueError as exc:
        # ``_strictly_inside`` is shared with the workspace routes and says
        # "the selected workspace". Name the area this request was actually
        # measured against instead, rather than a folder it never used; for
        # the WGLink origin the label is that same phrase, so nothing moves.
        detail = str(exc).replace("the selected workspace", label)
        raise HTTPException(status_code=422, detail=detail) from exc
    return bundle_path


@router.post("/ingest")
async def post_ingest(payload: CadReturnIngestRequest, request: Request) -> dict[str, Any]:
    bundle_path = _resolve_ingest_bundle(payload, request)
    store: CadLinkStore = request.app.state.cadlink_store
    mesh = {
        "rigid_size_mm": payload.mesh.rigid_size_mm,
        "transition_mm": payload.mesh.transition_mm,
        "source_size_mm": payload.mesh.source_size_mm,
    }
    data_dir = Path(request.app.state.data_dir)
    try:
        # Deliberately not on the gmsh worker: every mesh this runs happens in
        # a disposable child process, so holding the one in-process gmsh
        # session here would queue parametric previews behind a CAD ingestion
        # for the whole of its wall clock without doing any gmsh work.
        record = await asyncio.to_thread(
            ingest_bundle,
            bundle_path,
            mesh,
            payload.skipped_source_ids,
            store,
            data_dir,
            # Only carry the deviation override when it was actually asked
            # for: prep_options is part of the mesh cache key, so writing an
            # explicit null would invalidate every mesh cached before this
            # option existed.
            #
            # This dict is an allowlist by construction, and that is load-
            # bearing rather than incidental: ``build_imported_mesh`` also
            # accepts ``sizing_rule``, ``sagitta_grid_samples`` and
            # ``measure_deviation``, which are measurement scaffolding for
            # ``scripts/measure_imported_mesh_deviation.py``. ``sizing_rule``
            # would silently restore the superseded sizing rule and
            # ``measure_deviation`` costs about half a build again. None of
            # them may become reachable from a request; keep this literal, and
            # never splat a payload into it.
            prep_options={
                "area_drift_overrides": payload.area_drift_overrides,
                "symmetry_mode": payload.symmetry_mode,
                **(
                    {"surface_deviation_mm": payload.surface_deviation_mm}
                    if payload.surface_deviation_mm is not None
                    else {}
                ),
            },
            expected_design_id=payload.expected_design_id,
            expected_instance_id=payload.expected_instance_id,
            defer_viewport=True,
            # A model authored in CAD is shown in its project's confirmed
            # solver frame; the request never names one (solver_frame.py).
            resolve_confirmed_frame=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _ingest_error(exc) from exc
    _schedule_deferred_viewport(record, data_dir)
    # Filing the captured document is the user's archive, not this response's
    # subject, and it copies tens of megabytes out of a possibly cloud-synced
    # folder. Answering first is what puts the geometry on screen sooner.
    _schedule_cad_document_capture(request.app.state, store, bundle_path, record)
    return record


def pending_operation_return_states(store: CadLinkStore | None) -> list[str]:
    """Model states an unfinished CAD solve operation's preparation still names.

    Cleanup never removes what a pending operation references
    (CAD-OPERATIONS.md, "Retention"): its retained snapshot is never pruned,
    and the captured document its preparation was made from is kept here.
    """

    if store is None:
        return []
    states: list[str] = []
    for row in store.list_operations(kind="prepare_and_solve", states=_PENDING_STATES, limit=1000):
        preparation = str(row.get("preparation_id") or "")
        ingest = store.get_ingest(preparation) if preparation else None
        if ingest is None:
            continue
        try:
            document = json.loads(str(ingest["record_json"])).get("document") or {}
        except (TypeError, ValueError, AttributeError):
            continue
        digest = str(document.get("return_state_hash") or "") if isinstance(document, Mapping) else ""
        if digest:
            states.append(digest)
    return states


async def _retained_return_states(
    jobs: Any, stem: str, store: CadLinkStore | None = None
) -> list[str]:
    """Model states this project's unfinished runs and operations have not released.

    A queued or running run has not written its archive, and a complete run
    without ``archived_at`` has not either; both still have to be given the
    exact document they were solved from. So does a pending CAD operation's
    preparation. Advisory, like everything else on this path: if a registry
    cannot be read, the capture goes ahead and keeps only the newest state,
    which is the behaviour that predates this.
    """

    pending: list[str] = []
    try:
        pending = await asyncio.to_thread(pending_operation_return_states, store)
    except Exception:  # noqa: BLE001 - retention is advisory, like the rest
        logger.warning("Could not read which CAD model states operations still need", exc_info=True)
    if jobs is None:
        return pending
    try:
        rows = await jobs.unreleased_cad_return_states()
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not read which CAD model states runs still need", exc_info=True
        )
        return pending
    folder = archive_folder_slug(stem, "design")
    retained: list[str] = []
    for row in rows:
        row_stem = str(row.get("archive_stem") or "")
        # A run whose archive stem was never recorded is retained rather than
        # guessed out of its own document.
        if row_stem and archive_folder_slug(row_stem, "design") != folder:
            continue
        digest = str(row.get("return_state_hash") or "")
        if digest:
            retained.append(digest)
    return [*retained, *pending]


async def _archive_cad_document(
    runs: WorkspaceState | None,
    cad_workspace: CadWorkspaceState | None,
    jobs: Any,
    store: CadLinkStore,
    bundle_path: Path,
    record: Mapping[str, Any],
) -> None:
    """File a captured CAD document in the design's run archive, advisorily.

    The document is the user's own copy of the geometry a run was solved from,
    not evidence WG depends on, so a failure here must never cost them an
    otherwise good ingestion. It takes app state rather than the request
    because it outlives the response it used to block.
    """

    if runs is None:
        return
    if cad_workspace is not None and cad_workspace.capture_mode == "off":
        return
    anchor = record.get("anchor")
    design_id = str((anchor or {}).get("design_id") or "") if isinstance(anchor, Mapping) else ""
    stem = str((record.get("document") or {}).get("name") or "")
    # Ingestion already claimed the one folder this project's runs and captured
    # documents share -- including for a CAD-authored return, which has no
    # design to anchor to and so used to reach neither branch below and be
    # filed under a raw document name no run ever wrote to.
    project = record.get("project")
    claimed = (
        str(project.get("archive_stem") or "").strip()
        if isinstance(project, Mapping)
        else ""
    )
    if claimed:
        stem = claimed
    elif design_id:
        design_row = await asyncio.to_thread(store.get_design, design_id)
        lineage_id = str((design_row or {}).get("lineage_id") or "")
        if lineage_id:
            # The same claim the run archive uses, so the document and the runs
            # solved from it always land in one project folder.
            stem = await asyncio.to_thread(
                store.claim_archive_stem, lineage_id, preferred=stem
            ) or stem
    if not stem:
        return
    retained = await _retained_return_states(jobs, stem, store)
    try:
        relative = await asyncio.to_thread(
            archive_cad_document, bundle_path, record, runs.path(), stem, retained
        )
    except OSError as exc:
        logger.warning("Could not archive the CAD document for %s: %s", bundle_path.name, exc)
        return
    if relative is not None:
        logger.info("Archived the CAD document for %s as %s", stem, relative)


@router.get("/ingest/{ingest_id}")
async def get_ingest(ingest_id: str, request: Request) -> dict[str, Any]:
    if _INGEST_ID.fullmatch(ingest_id) is None:
        raise HTTPException(status_code=422, detail="ingest_id must be a wgi_ ULID")
    store: CadLinkStore = request.app.state.cadlink_store
    record = await asyncio.to_thread(get_ingestion_record, store, ingest_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion record {ingest_id}")
    return record


@router.get("/ingest/{ingest_id}/mesh", response_class=PlainTextResponse)
async def get_ingest_mesh(ingest_id: str, request: Request) -> PlainTextResponse:
    """Serve the exact ingested solve mesh for diagnostics and fallback."""

    if _INGEST_ID.fullmatch(ingest_id) is None:
        raise HTTPException(status_code=422, detail="ingest_id must be a wgi_ ULID")
    store: CadLinkStore = request.app.state.cadlink_store
    record = await asyncio.to_thread(get_ingestion_record, store, ingest_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion record {ingest_id}")
    try:
        msh_text = await asyncio.to_thread(read_verified_import_mesh, record)
    except ImportedMeshArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PlainTextResponse(msh_text, media_type="text/plain; charset=utf-8")


@router.get("/ingest/{ingest_id}/viewport-mesh", response_class=PlainTextResponse)
async def get_ingest_viewport_mesh(
    ingest_id: str, request: Request
) -> PlainTextResponse:
    """Serve the independently tessellated full-domain CAD display artifact."""

    if _INGEST_ID.fullmatch(ingest_id) is None:
        raise HTTPException(status_code=422, detail="ingest_id must be a wgi_ ULID")
    store: CadLinkStore = request.app.state.cadlink_store
    record = await asyncio.to_thread(get_ingestion_record, store, ingest_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion record {ingest_id}")
    viewport = record.get("viewport_mesh")
    if isinstance(viewport, dict) and viewport.get("available") is True:
        try:
            msh_text = await asyncio.to_thread(
                read_verified_import_viewport_mesh, record
            )
        except ImportedMeshArtifactError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return PlainTextResponse(msh_text, media_type="text/plain; charset=utf-8")

    lookup_key = deferred_viewport_lookup_key(record)
    if lookup_key is not None:
        data_dir = Path(request.app.state.data_dir)
        artifact = await asyncio.to_thread(resolve_deferred_viewport, record, data_dir)
        if artifact is not None:
            return PlainTextResponse(
                str(artifact["msh_text"]), media_type="text/plain; charset=utf-8"
            )
        if lookup_key in _DEFERRED_VIEWPORTS:
            # Still being tessellated. The caller already has the solve mesh on
            # screen, so this is "ask again", not a failure.
            return PlainTextResponse(
                "", status_code=202, media_type="text/plain; charset=utf-8"
            )
        # Nothing in flight and nothing on disk: the build failed, or this
        # process was restarted since the record was published. Either way the
        # inputs are all still there, so start it again rather than answering
        # with a permanent 404 for an artifact that is merely absent.
        _schedule_deferred_viewport(record, data_dir)
        return PlainTextResponse(
            "", status_code=202, media_type="text/plain; charset=utf-8"
        )

    raise HTTPException(
        status_code=404,
        detail="This ingestion record has no independent CAD viewport artifact",
    )


_RETURN_STATE_HASH = re.compile(r"[A-Za-z0-9:._-]{1,128}")


def _project_archive_stem(store: CadLinkStore, lineage_id: str, filename: str) -> str:
    """The archive folder a project owns, without claiming one.

    Read-only on purpose: listing projects must not decide a name that the next
    ingestion would then be stuck with. ``claim_archive_stem`` does the writing,
    at the one moment a document actually has to be filed.
    """

    names = store.get_lineage_cad_names(lineage_id) or {}
    return (
        str(names.get("archive_stem") or "").strip()
        or str(names.get("bundle_stem") or "").strip()
        or Path(str(filename or "")).stem
        or str(names.get("document_name") or "").strip()
    )


def _runs_root(request: Request) -> Path:
    runs: WorkspaceState | None = getattr(request.app.state, "workspace", None)
    if runs is None:
        raise HTTPException(status_code=409, detail="No run archive folder is available.")
    try:
        return runs.path()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _project_folder(request: Request, lineage_id: str) -> tuple[Path, str]:
    """This project's archive folder, resolved and confined to the runs root."""

    store: CadLinkStore = request.app.state.cadlink_store

    def resolve() -> str:
        row = store.find_latest_design_for_lineage(lineage_id)
        if row is not None:
            return _project_archive_stem(store, lineage_id, str(row["filename"]))
        # A CAD-authored project has no design row; its folder is named by the
        # stem the lineage claimed when its document was first captured.
        names = store.get_lineage_cad_names(lineage_id)
        stem = _project_archive_stem(store, lineage_id, "") if names else ""
        if not stem:
            raise HTTPException(status_code=404, detail="CAD-linked project not found")
        return stem

    stem = await asyncio.to_thread(resolve)
    root = _runs_root(request).resolve()
    folder = design_archive_folder(root, stem).resolve()
    try:
        _strictly_inside(folder, root, "project folder")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return folder, stem


async def _project_documents(folder: Path) -> list[dict[str, Any]]:
    """Every captured CAD document in one project, newest capture first."""

    directory = folder / CAD_SUBDIRECTORY

    def read() -> list[dict[str, Any]]:
        if not directory.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for sidecar in directory.glob("*.json"):
            if sidecar.is_symlink() or not sidecar.is_file():
                continue
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            document = next(
                (
                    candidate
                    for candidate in sorted(directory.glob(f"{sidecar.stem}.*"))
                    if candidate.suffix != ".json"
                    and candidate.is_file()
                    and not candidate.is_symlink()
                ),
                None,
            )
            items.append(
                {
                    "returnStateHash": str(payload.get("returnStateHash") or ""),
                    "documentName": payload.get("documentName"),
                    "ingestId": payload.get("ingestId"),
                    "returnId": payload.get("returnId"),
                    "capturedAt": payload.get("capturedAt"),
                    "filename": document.name if document else None,
                    "bytes": document.stat().st_size if document else None,
                }
            )
        items.sort(key=lambda item: str(item.get("capturedAt") or ""), reverse=True)
        return items

    return await asyncio.to_thread(read)


@router.get("/projects/{lineage_id}/documents")
async def list_project_documents(lineage_id: str, request: Request) -> dict[str, Any]:
    """The captured CAD documents this project's runs were solved from."""

    folder, stem = await _project_folder(request, lineage_id)
    return {
        "archiveStem": stem,
        "folder": str(folder),
        "items": await _project_documents(folder),
    }


@router.get("/projects/{lineage_id}/documents/{return_state_hash}", response_model=None)
async def download_project_document(
    lineage_id: str, return_state_hash: str, request: Request
) -> Response:
    """Hand back the Fusion document one geometry version was captured from."""

    if _RETURN_STATE_HASH.fullmatch(return_state_hash) is None:
        raise HTTPException(status_code=422, detail="Malformed return state hash")
    folder, stem = await _project_folder(request, lineage_id)
    document = await asyncio.to_thread(
        captured_cad_document, folder.parent, stem, return_state_hash
    )
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No CAD document was archived for this version. It was captured "
                "before the setting was turned on, or the file has been removed."
            ),
        )
    return FileResponse(
        document,
        media_type="application/octet-stream",
        filename=document.name,
    )


@router.post("/projects/{lineage_id}/reveal")
async def reveal_project_folder(lineage_id: str, request: Request) -> dict[str, str]:
    """Open this project's archive folder in the desktop file manager."""

    folder, _stem = await _project_folder(request, lineage_id)
    if not folder.is_dir():
        raise HTTPException(
            status_code=404,
            detail="This project has no archive folder yet. Solve a run to create one.",
        )
    try:
        subprocess.Popen(open_folder_command(folder), **background_process_kwargs())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
    return {"status": "opened", "path": str(folder)}


class ArchiveRunDocumentRequest(BaseModel):
    """Where the caller wrote the run folder this document belongs beside.

    The run-folder naming rule lives in the frontend, which has just used it to
    write the folder; asking the server to re-derive it would be a third
    implementation of one name rule and a third chance for them to disagree.
    """

    model_config = ConfigDict(extra="forbid")

    #: Relative to the run archive root, as passed to ``write-export``.
    subdirectory: str = Field(min_length=1, max_length=512)
    #: The run's file stem, shared by every export it produced.
    runStem: str = Field(min_length=1, max_length=200)
    archiveStem: str = Field(min_length=1, max_length=200)
    returnStateHash: str = Field(min_length=1, max_length=128)


@router.post("/runs/archive-document")
async def archive_run_document(
    payload: ArchiveRunDocumentRequest, request: Request
) -> dict[str, Any]:
    """File a run's CAD document beside the run, when the mode asks for it.

    Advisory throughout: the run archive is already written by the time this is
    called, and a missing convenience copy must never make a good run look
    failed. But it must not go missing quietly either -- the answer says why a
    copy is absent and whether asking again can still produce it, and the
    caller reports what it could not file.
    """

    cad_workspace: CadWorkspaceState | None = getattr(
        request.app.state, "cad_workspace", None
    )
    mode = cad_workspace.capture_mode if cad_workspace is not None else "run"
    if mode != "run":
        return {
            "placed": False,
            "retryable": False,
            "reason": f"Capture mode is {mode}.",
        }
    if _RETURN_STATE_HASH.fullmatch(payload.returnStateHash) is None:
        raise HTTPException(status_code=422, detail="Malformed return state hash")
    root = _runs_root(request)
    # A run can outrun the background capture of the model it was solved from,
    # so join that capture before deciding the document is not there.
    await _await_cad_document_capture(payload.returnStateHash)
    # The subdirectory is `<project>/<run>`; the placement helper takes the
    # project from the stem, so only the run segment travels on from here.
    segments = _path_segments(payload.subdirectory, "subdirectory")
    try:
        relative = await asyncio.to_thread(
            place_run_cad_document,
            root,
            payload.archiveStem,
            segments[-1],
            payload.runStem,
            payload.returnStateHash,
        )
    except OSError as exc:
        logger.warning(
            "Could not place the CAD document for %s: %s", payload.runStem, exc
        )
        return {"placed": False, "retryable": True, "reason": str(exc)}
    if relative is None:
        source = await asyncio.to_thread(
            captured_cad_document, root, payload.archiveStem, payload.returnStateHash
        )
        missing = source is None
        logger.warning(
            "No CAD document was filed for run %s from model state %s",
            payload.runStem,
            payload.returnStateHash,
        )
        return {
            "placed": False,
            "relativePath": None,
            # A capture that has not arrived can still arrive; a run folder
            # already holding a different file under that name cannot resolve
            # itself, so asking again would only repeat the refusal.
            "retryable": missing,
            "reason": (
                "The CAD model this run was solved from is not in the project archive."
                if missing
                else "A different file already occupies the run's CAD document name."
            ),
        }
    await _reclaim_captured_documents(request, root, payload.archiveStem)
    return {"placed": True, "relativePath": relative}


async def _reclaim_captured_documents(request: Request, root: Path, stem: str) -> None:
    """Let go of superseded captures now that one more run has its own copy.

    The other half of the retention rule: a model state is kept in the project
    folder while runs still have to be archived from it, and dropped once they
    have been, so the project keeps showing one current model instead of
    accumulating every state a run ever referenced.
    """

    jobs = getattr(request.app.state, "jobs_runtime", None)
    store = getattr(request.app.state, "cadlink_store", None)
    retained = await _retained_return_states(jobs, stem, store)
    try:
        await asyncio.to_thread(reclaim_captured_documents, root, stem, retained)
    except OSError as exc:
        logger.warning(
            "Could not reclaim superseded CAD documents for %s: %s", stem, exc
        )


# -- Backend-owned CAD solves ---------------------------------------------------
#
# docs/architecture/CAD-OPERATIONS.md, "Setup revisions" and "Preparation". The
# operation, not the browser, owns a solve: its setup revision, its fenced
# preparation stages, its approvals and its bound request. The UI issues and
# observes; a reconnecting client reads these routes for the authoritative state.


class SetupRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup: dict[str, Any]


class ManualSolveOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation_id: str = Field(alias="operationId", min_length=1)
    ingest_id: str = Field(alias="ingestId", min_length=1)


class CadOperationSnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manifest_sha256: str | None = Field(alias="manifestSha256")
    document_name: str | None = Field(alias="documentName")
    project_lineage_id: str | None = Field(alias="projectLineageId")


class CadOperationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation_id: str = Field(alias="operationId")
    kind: str
    state: str
    stage: str | None
    reason: str | None
    message: str | None
    job_id: str | None = Field(alias="jobId")
    attempt_generation: int = Field(alias="attemptGeneration")
    setup_revision_id: str | None = Field(alias="setupRevisionId")
    preparation_id: str | None = Field(alias="preparationId")
    snapshot: CadOperationSnapshotSummary | None
    legacy: bool
    created_at: str | None = Field(alias="createdAt")
    updated_at: str | None = Field(alias="updatedAt")


class ManualSolveOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: CadOperationSummary


class OperationApprovalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preparation_id: str = Field(alias="preparationId", min_length=1)
    finding_ids: list[str] = Field(alias="findingIds", min_length=1)


class PrepareOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    setup_revision_id: str | None = Field(default=None, alias="setupRevisionId")
    submit: bool = True
    #: Blocking findings the user reviewed on a preparation. They are approved
    #: only if this preparation resumes that one: the same snapshot and setup.
    approvals: OperationApprovalsRequest | None = None
    #: Answer with the prepared or submitted state instead of at once.
    wait: bool = False


_PENDING_STATES = frozenset(STATES - TERMINAL_STATES)


def _selected_workspace_root(state: Any) -> Path | None:
    workspace = getattr(state, "cad_workspace", None)
    selected = workspace.selected_path() if workspace is not None else None
    return selected.resolve() if selected is not None else None


_UNRESOLVED = object()


def _preparation_context(state: Any, *, workspace_root: Any = _UNRESOLVED) -> PreparationContext:
    """What a preparation needs from this application.

    ``workspace_root``, when given, is the WGLink folder already resolved off
    the event loop (the delivery loop resolves it in a thread every pass).
    """

    from server.jobs.runtime import (
        EngineUnavailableError,
        ImportedSolveRefusal,
        SymmetryValidationError,
        UnknownEngineError,
    )

    if workspace_root is _UNRESOLVED:
        workspace_root = _selected_workspace_root(state)
    runtime = getattr(state, "jobs_runtime", None)
    job_store = getattr(runtime, "store", None)
    restart = getattr(state, "update_restart", None)
    loop = asyncio.get_running_loop()

    def publish(summary: Mapping[str, Any]) -> None:
        # Called after the state change is committed, often from a worker
        # thread: the broker's queues belong to the event loop.
        events = getattr(runtime, "events", None)
        if events is None:
            return
        message = {"v": 1, "kind": "cadOperation", "operation": dict(summary)}
        loop.call_soon_threadsafe(events.publish, message)

    def job_for_submission(key: str) -> str | None:
        # A read of the jobs database; it never starts the jobs runtime.
        try:
            return job_store.job_for_submission_key(key)
        except sqlite3.OperationalError as exc:
            # A jobs database the runtime has not created yet holds no job.
            if "no such table" in str(exc):
                return None
            raise

    return PreparationContext(
        store=state.cadlink_store,
        data_dir=Path(state.data_dir),
        workspace_root=workspace_root,
        submit=runtime.submit if runtime is not None else None,
        job_for_submission=job_for_submission if job_store is not None else None,
        publish=publish,
        # A submission-key conflict is not among them: that key already made
        # a job, and preparation reconciles to it.
        submission_refusals=(
            UnknownEngineError,
            SymmetryValidationError,
            ImportedSolveRefusal,
            EngineUnavailableError,
        ),
        submission_blocked=restart.refusal if restart is not None else None,
    )


def _operation_detail(store: CadLinkStore, row: Mapping[str, Any]) -> dict[str, Any]:
    detail = operation_summary(row)
    approvals = json.loads(row["approvals_json"]) if row.get("approvals_json") else []
    detail["approvals"] = approvals
    preparation = (
        store.get_preparation(str(row["preparation_id"])) if row.get("preparation_id") else None
    )
    detail["preparation"] = (
        {
            "preparationId": preparation["preparation_id"],
            "ingestId": preparation["ingest_id"],
            "snapshotSha256": preparation["snapshot_sha256"],
            "setupRevisionId": preparation["setup_revision_id"],
            "reportSha256": preparation["report_sha256"],
            "blockingFindingIds": json.loads(preparation["blocking_findings_json"]),
            "attemptGeneration": preparation["attempt_generation"],
        }
        if preparation is not None
        else None
    )
    return detail


@router.post("/setup-revisions")
async def post_setup_revision(payload: SetupRevisionRequest, request: Request) -> dict[str, Any]:
    """Store an immutable setup revision; identical content is one revision."""

    try:
        setup = validate_setup(payload.setup)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(
        store.create_setup_revision, setup_content(setup), setup_digest(setup)
    )
    return {
        "revisionId": row["revision_id"],
        "contentSha256": row["content_sha256"],
        "createdAt": row["created_at"],
    }


@router.get("/setup-revisions/{revision_id}")
async def get_setup_revision(revision_id: str, request: Request) -> dict[str, Any]:
    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_setup_revision, revision_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown setup revision {revision_id}")
    return {
        "revisionId": row["revision_id"],
        "contentSha256": row["content_sha256"],
        "setup": json.loads(row["setup_json"]),
        "createdAt": row["created_at"],
    }


@router.get("/operations")
async def list_cad_operations(
    request: Request,
    pending: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """CAD operations, the unfinished ones by default: the authoritative state."""

    store: CadLinkStore = request.app.state.cadlink_store
    rows = await asyncio.to_thread(
        store.list_operations, states=_PENDING_STATES if pending else None, limit=limit
    )
    return {"operations": [operation_summary(row) for row in rows]}


@router.post(
    "/operations",
    response_model=ManualSolveOperationResponse,
    responses={
        404: {"model": ErrorEnvelope, "description": "CAD ingest not found"},
        409: {
            "model": ErrorEnvelope,
            "description": (
                "Update restart pending, operation id conflict, or retained snapshot unavailable"
            ),
        },
    },
)
async def post_cad_operation(
    payload: ManualSolveOperationRequest, request: Request
) -> ManualSolveOperationResponse | JSONResponse:
    """Create or recover a manual solve from an immutable retained CAD ingest."""

    state = request.app.state
    try:
        recovered, _ingest, _record, _inputs = await asyncio.to_thread(
            recover_manual_solve,
            state.cadlink_store,
            payload.operation_id,
            payload.ingest_id,
        )
    except UnknownIngest as exc:
        return JSONResponse(
            status_code=404,
            content=error_envelope(
                code="unknown_ingest",
                stage="submission",
                message=str(exc),
                retryable=False,
            ),
        )
    except OperationConflict as exc:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="operation_conflict",
                stage="submission",
                message=str(exc),
                retryable=False,
            ),
        )
    if recovered is not None:
        return ManualSolveOperationResponse(operation=operation_summary(recovered))

    restart = getattr(state, "update_restart", None)
    refusal = restart.refusal() if restart is not None else None
    if refusal is not None:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code=UPDATE_RESTART_PENDING,
                stage="submission",
                message=refusal,
                retryable=True,
            ),
        )
    try:
        row, _outcome = await asyncio.to_thread(
            create_manual_solve,
            state.cadlink_store,
            Path(state.data_dir),
            payload.operation_id,
            payload.ingest_id,
        )
    except UnknownIngest as exc:  # an ingest deleted between the two reads
        return JSONResponse(
            status_code=404,
            content=error_envelope(
                code="unknown_ingest",
                stage="submission",
                message=str(exc),
                retryable=False,
            ),
        )
    except OperationConflict as exc:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="operation_conflict",
                stage="submission",
                message=str(exc),
                retryable=False,
            ),
        )
    except SnapshotNotRetained as exc:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="snapshot_not_retained",
                stage="submission",
                message=str(exc),
                retryable=False,
            ),
        )
    return ManualSolveOperationResponse(operation=operation_summary(row))


@router.get("/operations/{operation_id}")
async def get_cad_operation(operation_id: str, request: Request) -> dict[str, Any]:
    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown CAD operation {operation_id}")
    return await asyncio.to_thread(_operation_detail, store, row)


@router.post(
    "/operations/{operation_id}/reconcile",
    response_model=ManualSolveOperationResponse,
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
async def post_reconcile_cad_operation(
    operation_id: str, request: Request
) -> ManualSolveOperationResponse | JSONResponse:
    """Re-read Fusion evidence for an unsettled WG-produced request."""

    state = request.app.state
    store: CadLinkStore = state.cadlink_store
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None:
        return JSONResponse(
            status_code=404,
            content=error_envelope(
                code="unknown_operation",
                stage="reconciliation",
                message=f"Unknown CAD operation {operation_id}",
                retryable=False,
            ),
        )
    if row.get("kind") not in FUSION_KINDS:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="operation_not_reconcilable",
                stage="reconciliation",
                message="This operation is not a Fusion request.",
                retryable=False,
            ),
        )
    if row.get("state") not in {"processing", "recovery_required"}:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code="operation_not_reconcilable",
                stage="reconciliation",
                message=(
                    "Only a processing or recovery-required Fusion request can be reconciled."
                ),
                retryable=False,
            ),
        )
    heartbeat = settleable_heartbeat(await asyncio.to_thread(select_heartbeat, Path(state.data_dir)))
    if heartbeat is not None:
        await asyncio.to_thread(
            settle_from_heartbeat,
            store,
            heartbeat,
            ipc_folder(Path(state.data_dir)),
            only_operation_id=operation_id,
        )
    current = await asyncio.to_thread(store.get_operation, operation_id)
    assert current is not None
    return ManualSolveOperationResponse(operation=operation_summary(current))


def _track(state: Any, task: asyncio.Task[Any]) -> None:
    running = getattr(state, "cad_preparations", None)
    if running is None:
        running = set()
        state.cad_preparations = running
    running.add(task)

    def done(finished: asyncio.Task[Any]) -> None:
        running.discard(finished)
        if not finished.cancelled() and finished.exception() is not None:
            logger.error(
                "A CAD preparation failed unexpectedly.", exc_info=finished.exception()
            )

    task.add_done_callback(done)


@router.post("/operations/{operation_id}/prepare")
async def post_prepare_cad_operation(
    operation_id: str, payload: PrepareOperationRequest, request: Request
) -> dict[str, Any]:
    """Prepare a solve operation from its retained snapshot, and submit it when asked.

    A preparation already running for the operation is taken over: its next
    write is refused, and this one's result stands. While an update restart is
    approved nothing starts: 409 ``update_restart_pending``, with the envelope
    every latched route uses, and the operation stays as it is.
    """

    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown CAD operation {operation_id}")
    if row["kind"] != PREPARE_AND_SOLVE:
        raise HTTPException(status_code=409, detail=f"CAD operation {operation_id} is not a solve")
    if payload.setup_revision_id is not None and (
        await asyncio.to_thread(store.get_setup_revision, payload.setup_revision_id)
    ) is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown setup revision {payload.setup_revision_id}"
        )
    approvals = payload.approvals
    context = _preparation_context(request.app.state)
    refusal = context.submission_blocked() if context.submission_blocked is not None else None
    if refusal is not None:
        return JSONResponse(
            status_code=409,
            content=error_envelope(
                code=UPDATE_RESTART_PENDING,
                stage="submission",
                message=refusal,
                retryable=True,
            ),
        )
    task = asyncio.create_task(
        prepare_operation(
            context,
            operation_id,
            PreparationInput(
                setup_revision_id=payload.setup_revision_id,
                submit=payload.submit,
                approve_preparation_id=approvals.preparation_id if approvals else None,
                approve_finding_ids=tuple(approvals.finding_ids) if approvals else (),
            ),
        )
    )
    _track(request.app.state, task)
    if payload.wait:
        try:
            return {"operation": await asyncio.shield(task)}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"operation": operation_summary(row)}


@router.post("/operations/{operation_id}/approvals")
async def post_cad_operation_approvals(
    operation_id: str, payload: OperationApprovalsRequest, request: Request
) -> dict[str, Any]:
    """Acknowledge blocking findings of one preparation. A new preparation needs its own.

    The next preparation of the same snapshot and setup revision resumes that
    preparation, so these approvals apply to it.
    """

    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown CAD operation {operation_id}")
    try:
        updated = await asyncio.to_thread(
            store.add_approvals, operation_id, payload.preparation_id, payload.finding_ids
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=409, detail="This CAD operation is finished.")
    return await asyncio.to_thread(_operation_detail, store, updated)


@router.post("/operations/{operation_id}/cancel")
async def post_cancel_cad_operation(operation_id: str, request: Request) -> dict[str, Any]:
    """Dismiss an operation: at once when idle, at the next step when running.

    A solve is reconciled with the jobs store first: one whose job already
    exists follows the job, and one that may have a job is not dismissed
    while the jobs store cannot be read (409).
    """

    context = _preparation_context(request.app.state)
    try:
        row = await asyncio.to_thread(dismiss_operation, context, operation_id)
    except DismissalUnconfirmed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown CAD operation {operation_id}")
    if context.publish is not None:
        context.publish(operation_summary(row))
    return operation_summary(row)


class SourceInventoryItem(BaseModel):
    """One source of a return, as its manifest states it."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    role: str
    required: StrictBool


class ProjectSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    lineage_id: str = Field(alias="lineageId", min_length=1)
    #: The sources the setup is for, with canonical roles, as the returns listing states them.
    inventory: list[SourceInventoryItem] = Field(min_length=1)
    setup: dict[str, Any]


class SolverSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(min_length=1)

    @field_validator("engine")
    @classmethod
    def normalized_engine(cls, value: str) -> str:
        # As SolveOptions normalises it, so "Metal" and "metal" are one choice.
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("engine must not be empty")
        return normalized


@router.put("/project-setups")
async def put_project_setup(payload: ProjectSetupRequest, request: Request) -> dict[str, Any]:
    """Record a project's solve settings for its sources (CAD-OPERATIONS.md, "Project setups").

    A solve Fusion sends for that project is prepared from them, whatever
    project the editor has open. The latest recording is the project's.
    """

    try:
        setup = validate_setup(payload.setup)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store: CadLinkStore = request.app.state.cadlink_store
    inventory = inventory_sha256([item.model_dump() for item in payload.inventory])

    def record() -> dict[str, Any]:
        revision = store.create_setup_revision(setup_content(setup), setup_digest(setup))
        store.record_project_setup(payload.lineage_id, inventory, str(revision["revision_id"]))
        return revision

    revision = await asyncio.to_thread(record)
    return {
        "lineageId": payload.lineage_id,
        "inventorySha256": inventory,
        "revisionId": revision["revision_id"],
    }


@router.put("/solver-selection")
async def put_solver_selection(
    payload: SolverSelectionRequest, request: Request
) -> dict[str, Any]:
    """Record the engine selected in WG's solver selector; CAD Link never chooses one."""

    store: CadLinkStore = request.app.state.cadlink_store
    await asyncio.to_thread(store.set_setting, SOLVER_SELECTION, {"engine": payload.engine})
    return {"engine": payload.engine}


#: ``WG2_CAD_DELIVERY=0`` turns the backend's solve-command consumer off; the
#: test suite sets it, so no test run collects a real delivery.
CAD_DELIVERY_ENV = "WG2_CAD_DELIVERY"
_DELIVERY_INTERVAL_S = 1.0


def _deliver_solve_commands(application: FastAPI):
    async def start_cad_delivery() -> None:
        # The backend is the one consumer of Fusion's solve commands
        # (CAD-OPERATIONS.md, "Delivery"); the UI only issues and observes.
        if os.environ.get(CAD_DELIVERY_ENV, "") == "0":
            return
        state = application.state
        running: set[str] = set()

        def spawn(operation_id: str, coroutine: Awaitable[Any]) -> None:
            task = asyncio.ensure_future(coroutine)
            running.add(operation_id)
            task.add_done_callback(lambda _done: running.discard(operation_id))
            _track(state, task)

        async def deliver() -> None:
            failures = 0
            reported: str | None = None
            while True:
                try:
                    workspace_root = await asyncio.to_thread(_selected_workspace_root, state)
                    await run_delivery_pass(
                        _preparation_context(state, workspace_root=workspace_root),
                        spawn=spawn,
                        running=running,
                    )
                    failures, reported = 0, None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the next pass tries again
                    failures += 1
                    error = f"{type(exc).__name__}: {exc}"
                    if error != reported:
                        # Each distinct failure once, not every second.
                        logger.warning("Delivering CAD solve commands failed.", exc_info=True)
                        reported = error
                # Backs off to half a minute while a failure persists.
                await asyncio.sleep(min(_DELIVERY_INTERVAL_S * 2 ** min(failures, 5), 30.0))

        state.cad_delivery_task = asyncio.create_task(deliver())

    return start_cad_delivery


def _recover_on_startup(application: FastAPI):
    async def recover_cad_operations_on_startup() -> None:
        # Settles what a backend that stopped left: a job its submission key
        # made is the outcome; an attempt it held waits for the user. It only
        # reads the jobs database; starting the jobs runtime stays its own.
        state = application.state
        try:
            changed = await asyncio.to_thread(recover_operations, _preparation_context(state))
        except Exception:  # noqa: BLE001 - recovery must never stop the app starting
            logger.warning("Could not recover CAD operations at startup.", exc_info=True)
            return
        if changed:
            logger.info("Recovered %d CAD operation(s) at startup.", changed)

    return recover_cad_operations_on_startup


def _abandon_preparations_on_shutdown(application: FastAPI):
    async def abandon_cad_preparations_on_shutdown() -> None:
        delivery = getattr(application.state, "cad_delivery_task", None)
        if delivery is not None:
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)
        # A preparation still running is abandoned, not waited for: the next
        # start takes its operation over, and it waits as interrupted.
        running = list(getattr(application.state, "cad_preparations", None) or ())
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    return abandon_cad_preparations_on_shutdown


def mount_cadlink(application: FastAPI) -> None:
    application.include_router(router)
    # An unlinked model's solver frame: preview and confirmation.
    from .solver_frame_api import router as solver_frame_router

    application.include_router(solver_frame_router)

    async def advertise_fusion_delivery_on_startup() -> None:
        # Removes what a WG older than delivery version 3 left for its add-in,
        # then advertises version 3.
        await asyncio.to_thread(advertise_fusion_delivery, Path(application.state.data_dir))
        await asyncio.to_thread(
            recover_staged_fusion_requests,
            Path(application.state.data_dir),
            lookup_operation=application.state.cadlink_store.get_operation,
        )
        heartbeat = settleable_heartbeat(
            await asyncio.to_thread(select_heartbeat, Path(application.state.data_dir))
        )
        if heartbeat is not None:
            await asyncio.to_thread(
                settle_from_heartbeat,
                application.state.cadlink_store,
                heartbeat,
                ipc_folder(Path(application.state.data_dir)),
            )
        await asyncio.to_thread(
            expire_unstarted_insert_handoffs,
            Path(application.state.data_dir),
            record_expired=lambda operation_id: _record_expired_insert(
                application.state.cadlink_store, operation_id
            ),
        )

    application.router.add_event_handler("startup", advertise_fusion_delivery_on_startup)
    # After the capability file that advertises ``liveProtocol``: the live
    # session's registry, and its endpoint file when the launcher named a port.
    mount_live(application)
    application.router.add_event_handler("startup", _recover_on_startup(application))
    application.router.add_event_handler("startup", _deliver_solve_commands(application))
    application.router.add_event_handler(
        "shutdown", _abandon_preparations_on_shutdown(application)
    )


__all__ = [
    "CadReturnIngestRequest",
    "FusionStatusRequest",
    "ImportedMeshRequest",
    "list_returns",
    "mount_cadlink",
    "router",
]
