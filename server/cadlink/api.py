"""HTTP transport for workspace-scoped CAD-return ingestion records."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Literal, Mapping

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import SaveIdentity, design_hash
from server.design.schema import DesignConfig
from server.mesh.artifact import (
    ImportedMeshArtifactError,
    read_verified_import_mesh,
    read_verified_import_viewport_mesh,
)
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.platform.process import background_process_kwargs
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
    captured_cad_document,
    design_archive_folder,
    place_run_cad_document,
)

from .fusion_status import fusion_process_running, read_fusion_status
from .fusion_return import publish_return_request
from .solve_command import (
    clear_solve_command,
    ledger_entry,
    read_solve_command,
    record_outcome,
)
from .ingest import IngestRefusal, get_ingestion_record, ingest_bundle
from .roles import canonical_source_role
from .store import CadLinkStore
from .wgreturn import WgReturnError


logger = logging.getLogger(__name__)


_INGEST_ID = re.compile(r"^wgi_[0-9A-HJKMNP-TV-Z]{26}$")
_RETURN_INVENTORY_CACHE: dict[
    tuple[str, int, int], tuple[dict[str, Any], Mapping[str, Any] | None]
] = {}
_RETURN_INVENTORY_CACHE_LOCK = threading.Lock()


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
    # PARKED CANDIDATE field -- this branch is not the shipping one; the
    # shipped dial is ``curvatureSegments``. See IMPORTED_SURFACE_DEVIATION_MM
    # in ``server/mesh/imported.py`` for the measurements and for what would
    # have to happen before this could ship (including regenerating the OpenAPI
    # snapshot, which does not carry this alias).
    #
    # Chord deviation each panel may have from the true CAD surface, in mm.
    # Omitted means the server default, IMPORTED_SURFACE_DEVIATION_MM = 0.2 mm.
    # This is the imported mesh's cost dial: it replaced a segments-per-2pi
    # control, which spent triangles in proportion to curvature radius and so
    # over-refined small fillets while under-refining large sweeps. Measured,
    # it buys 14-21% of the triangles at matched quality.
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
                    "requestId": (
                        str(document.get("request_id"))
                        if document.get("request_id")
                        else None
                    ),
                    "sourceCount": len(sources),
                    "instanceCount": len(instances),
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
    )
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
    store: CadLinkStore = request.app.state.cadlink_store
    status["realizedDimensions"] = await asyncio.to_thread(
        _realized_dimensions_payload,
        status,
        store,
        current_design_hash=current_hash,
    )
    return status


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
        process_running=await asyncio.to_thread(fusion_process_running),
    )
    session_id = str(status.get("sessionId") or "")
    if not status.get("running") or not session_id:
        raise HTTPException(
            status_code=409,
            detail="Fusion is running, but WGLink is offline. Restart WGLink in Fusion first.",
        )
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
    _marker, request_id = await asyncio.to_thread(
        publish_return_request,
        Path(request.app.state.data_dir),
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


def _pending_solve_command(data_dir: Path, workspace_root: Path) -> dict[str, Any]:
    """The CAD-authored solve command, validated against what is on disk.

    A marker is only actionable when it names a bundle inside this workspace
    whose manifest still hashes to what the add-in recorded when it wrote the
    command. Anything else is reported as a refusal with its reason rather than
    silently ignored, because CAD is waiting on an answer either way.
    """

    command = read_solve_command(data_dir)
    if command is None:
        return {"command": None}
    recorded = ledger_entry(data_dir, command.command_id)
    if recorded is not None:
        # Terminal already: replay must surface the same answer, never a
        # second submission. Retire markers left by older server versions too.
        clear_solve_command(data_dir, command.command_id)
        return {"command": command.payload(), "outcome": recorded}
    try:
        segments = _path_segments(command.bundle_path, "bundlePath")
        if not segments or segments[0].casefold() != "wgreturn":
            raise ValueError("bundlePath must be under the selected workspace's wgreturn/ directory")
        if not segments[-1].endswith(".wgreturn"):
            raise ValueError("bundlePath must name a .wgreturn bundle directory")
        bundle_path = workspace_root.joinpath(*segments).resolve()
        _strictly_inside(bundle_path, workspace_root, "bundlePath")
        manifest = (bundle_path / "wgreturn.json").read_bytes()
    except (ValueError, OSError) as exc:
        outcome = record_outcome(
            data_dir, command.command_id, state="refused", reason=str(exc),
        )
        clear_solve_command(data_dir, command.command_id)
        return {
            "command": command.payload(),
            "outcome": outcome,
        }
    listing = _return_listing(workspace_root)
    if listing and listing[0]["bundlePath"] != command.bundle_path:
        reason = "Superseded by a newer return from Fusion."
        outcome = record_outcome(
            data_dir, command.command_id, state="refused", reason=reason,
        )
        clear_solve_command(data_dir, command.command_id)
        return {
            "command": command.payload(),
            "outcome": outcome,
        }
    observed = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
    expected = command.manifest_sha256
    if not expected.startswith("sha256:"):
        expected = f"sha256:{expected}"
    if observed != expected:
        reason = (
            "The return bundle changed after Fusion asked WG to solve it. "
            "Send it again from Fusion."
        )
        outcome = record_outcome(
            data_dir, command.command_id, state="refused", reason=reason,
        )
        clear_solve_command(data_dir, command.command_id)
        return {
            "command": command.payload(),
            "outcome": outcome,
        }
    return {"command": command.payload(), "outcome": None}


@router.get("/solve-command")
async def get_solve_command(request: Request) -> dict[str, Any]:
    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    if selected is None:
        return {"command": None}
    return await asyncio.to_thread(
        _pending_solve_command,
        Path(request.app.state.data_dir),
        selected.resolve(),
    )


@router.post("/solve-command/outcome")
async def post_solve_command_outcome(
    payload: SolveCommandOutcome, request: Request
) -> dict[str, Any]:
    """Record what happened to a CAD solve command and retire its marker.

    Only terminal outcomes are recorded. A blocked command keeps its marker so
    the user can satisfy the gate and run the same request.
    """

    data_dir = Path(request.app.state.data_dir)
    if payload.state == "blocked":
        return {"state": "blocked", "cleared": False}
    entry = await asyncio.to_thread(
        record_outcome,
        data_dir,
        payload.command_id,
        state=payload.state,
        job_id=payload.job_id,
        reason=payload.reason,
    )
    cleared = await asyncio.to_thread(clear_solve_command, data_dir, payload.command_id)
    return {**entry, "cleared": cleared}


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


@router.post("/ingest")
async def post_ingest(payload: CadReturnIngestRequest, request: Request) -> dict[str, Any]:
    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail="No WGLink folder has been selected. Choose one in Settings → CAD Link first.",
        )
    try:
        segments = _path_segments(payload.bundle_path, "bundlePath")
        if not segments or segments[0].casefold() != "wgreturn":
            raise ValueError("bundlePath must be under the selected workspace's wgreturn/ directory")
        if not segments[-1].endswith(".wgreturn"):
            raise ValueError("bundlePath must name a .wgreturn bundle directory")
        workspace_root = selected.resolve()
        bundle_path = workspace_root.joinpath(*segments).resolve()
        _strictly_inside(bundle_path, workspace_root, "bundlePath")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store: CadLinkStore = request.app.state.cadlink_store
    mesh = {
        "rigid_size_mm": payload.mesh.rigid_size_mm,
        "transition_mm": payload.mesh.transition_mm,
        "source_size_mm": payload.mesh.source_size_mm,
    }
    try:
        record = await run_on_gmsh_worker(
            ingest_bundle,
            bundle_path,
            mesh,
            payload.skipped_source_ids,
            store,
            Path(request.app.state.data_dir),
            # Only carry the curvature override when it was actually asked
            # for: prep_options is part of the mesh cache key, so writing an
            # explicit null would invalidate every mesh cached before this
            # option existed.
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
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _ingest_error(exc) from exc
    await _archive_cad_document(request, store, bundle_path, record)
    return record


async def _archive_cad_document(
    request: Request,
    store: CadLinkStore,
    bundle_path: Path,
    record: Mapping[str, Any],
) -> None:
    """File a captured CAD document in the design's run archive, advisorily.

    The document is the user's own copy of the geometry a run was solved from,
    not evidence WG depends on, so a failure here must never cost them an
    otherwise good ingestion.
    """

    runs: WorkspaceState | None = getattr(request.app.state, "workspace", None)
    if runs is None:
        return
    cad_workspace: CadWorkspaceState | None = getattr(
        request.app.state, "cad_workspace", None
    )
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
    try:
        relative = await asyncio.to_thread(
            archive_cad_document, bundle_path, record, runs.path(), stem
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
    if not isinstance(viewport, dict) or viewport.get("available") is not True:
        raise HTTPException(
            status_code=404,
            detail="This ingestion record has no independent CAD viewport artifact",
        )
    try:
        msh_text = await asyncio.to_thread(
            read_verified_import_viewport_mesh, record
        )
    except ImportedMeshArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PlainTextResponse(msh_text, media_type="text/plain; charset=utf-8")


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
    failed.
    """

    cad_workspace: CadWorkspaceState | None = getattr(
        request.app.state, "cad_workspace", None
    )
    mode = cad_workspace.capture_mode if cad_workspace is not None else "run"
    if mode != "run":
        return {"placed": False, "reason": f"Capture mode is {mode}."}
    if _RETURN_STATE_HASH.fullmatch(payload.returnStateHash) is None:
        raise HTTPException(status_code=422, detail="Malformed return state hash")
    root = _runs_root(request)
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
        return {"placed": False, "reason": str(exc)}
    return {"placed": relative is not None, "relativePath": relative}


def mount_cadlink(application: FastAPI) -> None:
    application.include_router(router)


__all__ = [
    "CadReturnIngestRequest",
    "FusionStatusRequest",
    "ImportedMeshRequest",
    "list_returns",
    "mount_cadlink",
    "router",
]
