"""HTTP transport for workspace-scoped CAD-return ingestion records."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import SaveIdentity, design_hash
from server.design.schema import DesignConfig
from server.mesh.artifact import (
    ImportedMeshArtifactError,
    read_verified_import_mesh,
    read_verified_import_viewport_mesh,
)
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.workspace.api import WorkspaceState, _path_segments, _strictly_inside

from .fusion_status import fusion_process_running, read_fusion_status
from .fusion_return import publish_return_request
from .solve_command import (
    clear_solve_command,
    ledger_entry,
    read_solve_command,
    record_outcome,
)
from .ingest import IngestRefusal, get_ingestion_record, ingest_bundle
from .store import CadLinkStore
from .wgreturn import WgReturnError


_INGEST_ID = re.compile(r"^wgi_[0-9A-HJKMNP-TV-Z]{26}$")


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


class FusionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design: DesignConfig
    identity: SaveIdentity | None = None
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


def _return_listing(workspace_root: Path) -> list[dict[str, Any]]:
    returns_root = workspace_root / "wgreturn"
    if not returns_root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for candidate in sorted(returns_root.glob("*.wgreturn"), key=lambda item: item.name.casefold()):
        try:
            resolved = candidate.resolve()
            _strictly_inside(resolved, workspace_root, "bundlePath")
            if not resolved.is_dir():
                continue
            modified_at = datetime.fromtimestamp(
                candidate.stat().st_mtime, tz=timezone.utc
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
            "sources": [],
        }
        try:
            manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
            document = manifest.get("document")
            sources = manifest.get("sources")
            instances = manifest.get("instances")
            if not isinstance(document, dict) or not isinstance(sources, list) or not isinstance(instances, list):
                raise ValueError("manifest inventory has the wrong shape")
            source_summaries = []
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError("manifest source has the wrong shape")
                suggested_resolution = float(source["suggested_resolution_mm"])
                if not math.isfinite(suggested_resolution) or suggested_resolution <= 0:
                    raise ValueError("manifest source suggestion is not finite and positive")
                source_summaries.append(
                    {
                        "id": str(source["id"]),
                        "role": str(source.get("role") or "source"),
                        "required": bool(source.get("required", True)),
                        "suggestedResolutionMm": suggested_resolution,
                        "defaultDriveChannelId": str(
                            source["default_drive_channel_id"]
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
                    "sources": source_summaries,
                }
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            item["reason"] = str(exc) or "Manifest is unreadable"
        items.append(item)
    return sorted(items, key=lambda item: item["modifiedAt"], reverse=True)


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


@router.post("/fusion-status")
async def fusion_status(
    payload: FusionStatusRequest, request: Request
) -> dict[str, Any]:
    """Report whether the design on screen is open and current in Fusion."""

    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    workspace_root = selected.resolve() if selected is not None else None
    returned_bundle: Path | None = None
    if payload.return_bundle_path:
        try:
            if workspace_root is None:
                raise ValueError("No WGLink folder has been selected.")
            selected_return = (workspace_root / payload.return_bundle_path).resolve()
            _strictly_inside(selected_return, workspace_root, "returnBundlePath")
            if (
                selected_return.is_symlink()
                or not selected_return.is_dir()
                or selected_return.suffix != ".wgreturn"
            ):
                raise ValueError("Selected CAD return is unavailable.")
            manifest = json.loads(
                (selected_return / "wgreturn.json").read_text(encoding="utf-8")
            )
            instances = manifest.get("instances")
            if payload.identity is None or (
                isinstance(instances, list)
                and any(
                    isinstance(instance, dict)
                    and instance.get("design_id") == payload.identity.design_id
                    for instance in instances
                )
            ):
                returned_bundle = selected_return
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if returned_bundle is None and payload.identity is not None and workspace_root is not None:
        for candidate in _return_listing(workspace_root):
            if not candidate.get("readable"):
                continue
            candidate_path = workspace_root / str(candidate["bundlePath"])
            try:
                manifest = json.loads(
                    (candidate_path / "wgreturn.json").read_text(encoding="utf-8")
                )
                instances = manifest.get("instances")
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(instances, list) and any(
                isinstance(instance, dict)
                and instance.get("design_id") == payload.identity.design_id
                for instance in instances
            ):
                returned_bundle = candidate_path
                break
    current_hash = design_hash(payload.design)
    status = await asyncio.to_thread(
        read_fusion_status,
        Path(request.app.state.data_dir),
        current_design_hash=current_hash,
        current_formula=payload.design.root.formula,
        design_id=payload.identity.design_id if payload.identity else None,
        process_running=await asyncio.to_thread(fusion_process_running),
        returned_bundle=returned_bundle,
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
        # second submission.
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
        return {
            "command": command.payload(),
            "outcome": record_outcome(
                data_dir, command.command_id, state="refused", reason=str(exc),
            ),
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
        return {
            "command": command.payload(),
            "outcome": record_outcome(data_dir, command.command_id, state="refused", reason=reason),
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
        return await run_on_gmsh_worker(
            ingest_bundle,
            bundle_path,
            mesh,
            payload.skipped_source_ids,
            store,
            Path(request.app.state.data_dir),
            prep_options={"area_drift_overrides": payload.area_drift_overrides},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _ingest_error(exc) from exc


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
