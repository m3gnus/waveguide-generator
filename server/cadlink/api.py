"""HTTP transport for workspace-scoped CAD-return ingestion records."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.workspace.api import WorkspaceState, _path_segments, _strictly_inside

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


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


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
                    "sourceCount": len(sources),
                    "instanceCount": len(instances),
                    "sources": source_summaries,
                }
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            item["reason"] = str(exc) or "Manifest is unreadable"
        items.append(item)
    return items


@router.get("/returns")
async def list_returns(request: Request) -> dict[str, Any]:
    workspace: WorkspaceState = request.app.state.workspace
    selected = workspace.selected_path()
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail="No workspace folder has been selected. Choose a workspace folder first.",
        )
    workspace_root = selected.resolve()
    return {"items": await asyncio.to_thread(_return_listing, workspace_root)}


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
    workspace: WorkspaceState = request.app.state.workspace
    selected = workspace.selected_path()
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail="No workspace folder has been selected. Choose a workspace folder first.",
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


def mount_cadlink(application: FastAPI) -> None:
    application.include_router(router)


__all__ = [
    "CadReturnIngestRequest",
    "ImportedMeshRequest",
    "list_returns",
    "mount_cadlink",
    "router",
]
