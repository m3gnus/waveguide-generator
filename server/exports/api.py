"""FastAPI routes for revision-bound geometry file exports."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Literal, Mapping

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.preview.translate import design_to_mesher_config
from server.workspace.api import WorkspaceState, _path_segments

from .core import build_profiles, build_step, build_step_solid, build_stl


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, protected_namespaces=())

    design: DesignConfig
    design_revision: int = Field(alias="designRevision", ge=0)
    base_name: str = Field(default="waveguide", alias="baseName", max_length=240)
    model_name: str = Field(default="MWG Horn", alias="modelName", max_length=240)


class WgLinkExportRequest(ExportRequest):
    identity: SaveIdentity | None = None


router = APIRouter(prefix="/api/export", tags=["exports"])
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_KNOWN_DESIGN_EXTENSIONS = frozenset({".cfg", ".txt", ".mwg"})


def _base_name(value: str) -> str:
    leaf = Path(value.replace("\\", "/")).name
    path = Path(leaf)
    stem = path.stem if path.suffix.lower() in _KNOWN_DESIGN_EXTENSIONS else leaf
    stem = stem or "waveguide"
    return _UNSAFE_FILENAME.sub("_", stem).strip("._") or "waveguide"


def _headers(request: ExportRequest, filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Design-Revision": str(request.design_revision),
    }


def _export_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (ImportError, RuntimeError)):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Export failed: {exc}")


def _hash_value(value: Any) -> Any:
    """Turn resolved mesher geometry into stable JSON-compatible hash input."""

    if is_dataclass(value):
        return _hash_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _hash_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hash_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _hash_value(value.tolist())
    return value


def _geometry_hash(geometry: object, mesher_version: str) -> str:
    canonical = json.dumps(
        {
            "datum_schema": 1,
            "domain": "full",
            "geometry": _hash_value(geometry),
            "mesher_version": mesher_version,
            "open_throat": True,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _mesher_version() -> str:
    try:
        return version("hornlab-waveguide-mesher")
    except PackageNotFoundError:
        return "unknown"


def _app_version() -> str:
    version_path = Path(__file__).resolve().parents[2] / "shared" / "version.json"
    return str(json.loads(version_path.read_text(encoding="utf-8"))["version"])


def _replace_bundle(staged: Path, destination: Path, temporary_root: Path) -> None:
    """Publish a complete bundle while retaining the previous live bundle on failure."""

    backup = temporary_root / "previous.wglink"
    moved_previous = False
    try:
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError(f"CAD-link destination is not a bundle directory: {destination}")
            os.replace(destination, backup)
            moved_previous = True
        os.replace(staged, destination)
    except Exception:
        if moved_previous and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if moved_previous:
        shutil.rmtree(backup)


class _ExportIdentityConflict(Exception):
    pass


def _wglink_response(
    row: Mapping[str, object], wglink_root: Path, fallback_name: str
) -> dict[str, Any]:
    manifest = json.loads(str(row["manifest_json"]))
    stored_name = _base_name(
        str((manifest.get("design") or {}).get("name") or fallback_name)
    )
    return {
        "bundlePath": str(wglink_root / f"{stored_name}.wglink"),
        "bundleId": row["bundle_id"],
        "exportId": row["export_id"],
        "sequence": row["sequence"],
        "designHash": row["design_hash"],
        "geometryHash": row["geometry_hash"],
        "artifactSha256": row["artifact_sha256"],
    }


def _export_wglink_sync(
    request: WgLinkExportRequest,
    store: CadLinkStore,
    workspace_root: Path,
    idempotency_key: str,
) -> dict[str, Any]:
    from hornlab_mesher import WgLinkIdentity, write_wglink
    from hornlab_mesher.config_builder import resolve_geometry

    workspace_root = workspace_root.resolve()
    wglink_root = (workspace_root / "wglink").resolve()
    if workspace_root != wglink_root and workspace_root not in wglink_root.parents:
        raise HTTPException(status_code=422, detail="CAD-link destination resolves outside the selected workspace")
    if request.identity is None:
        raise HTTPException(
            status_code=409,
            detail="This design has never been saved. Save the design before sending it to CAD.",
        )
    retry = store.find_export_by_idempotency_key(idempotency_key)
    if retry is not None:
        return _wglink_response(retry, wglink_root, request.base_name)
    head = store.get_design(request.identity.design_id)
    save_first = "Save the design before sending it to CAD."
    if head is None:
        raise HTTPException(status_code=409, detail=save_first)
    if str(head["lineage_id"]) != request.identity.lineage_id:
        raise HTTPException(status_code=409, detail="Design identity does not match the saved design. " + save_first)
    if int(head["edit_version"]) != request.identity.base_edit_version:
        raise HTTPException(status_code=409, detail="The saved design identity is out of date. Save again before sending it to CAD.")
    current_design_hash = design_hash(request.design)
    if current_design_hash != str(head["design_hash"]):
        raise HTTPException(status_code=409, detail="The design has unsaved changes. Save it before sending it to CAD.")

    config = design_to_mesher_config(request.design)
    resolved = resolve_geometry(config)
    mesher_version = _mesher_version()
    geometry_hash = _geometry_hash(resolved.geometry, mesher_version)
    design_name = _base_name(request.base_name)
    try:
        _path_segments(f"{design_name}.wglink", "CAD-link bundle name")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    wglink_root.mkdir(parents=True, exist_ok=True)
    destination = wglink_root / f"{design_name}.wglink"

    def build(facts: Mapping[str, object]) -> Mapping[str, str]:
        if (
            int(facts["editVersion"]) != request.identity.base_edit_version
            or str(facts["designHash"]) != current_design_hash
        ):
            raise _ExportIdentityConflict(
                "The saved design changed while the CAD export was starting. "
                "Save again before sending it to CAD."
            )
        temporary_root = Path(tempfile.mkdtemp(prefix=".wg2-wglink-", dir=wglink_root))
        staged = temporary_root / "bundle.wglink"
        identity = WgLinkIdentity(
            bundle={"id": facts["bundleId"], "created_at": facts["createdAt"]},
            generator={
                "app": "waveguide-generator",
                "app_version": _app_version(),
                "mesher_version": mesher_version,
                "datum_schema": 1,
            },
            design={
                "id": facts["designId"],
                "lineage_id": request.identity.lineage_id,
                "edit_version": facts["editVersion"],
                "design_hash": facts["designHash"],
                "name": design_name,
                "formula": request.design.root.formula.lower(),
                "build_mode": resolved.mode,
            },
            export={
                "id": facts["exportId"],
                "sequence": facts["sequence"],
                "parent_export_id": facts["parentExportId"],
                "geometry_hash": geometry_hash,
                "domain": "full",
                "open_throat": True,
            },
        )
        try:
            product = write_wglink(
                resolved.geometry,
                staged,
                identity=identity,
                instance_slug=design_name,
                open_throat=True,
            )
            manifest_json = product.manifest_path.read_text(encoding="utf-8")
            artifact_sha256 = str(product.manifest["files"]["waveguide.step"]["sha256"])
            _replace_bundle(staged, destination, temporary_root)
            return {
                "manifest_json": manifest_json,
                "geometry_hash": geometry_hash,
                "artifact_sha256": artifact_sha256,
            }
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    try:
        row = store.allocate_export(
            design_id=request.identity.design_id,
            idempotency_key=idempotency_key,
            export_builder=build,
        )
    except _ExportIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _export_error(exc) from exc

    return _wglink_response(row, wglink_root, design_name)


@router.post("/step")
async def export_step(
    request: ExportRequest,
    body: Literal["solid", "surface"] = Query(default="solid"),
) -> Response:
    """Export STEP. ``solid`` is the manufacturable part; ``surface`` the bore."""

    try:
        if body == "solid":
            solid = await build_step_solid(request.design)
            content = solid.step_text
        else:
            content = await build_step(request.design)
    except Exception as exc:
        raise _export_error(exc) from exc
    return Response(
        content=content,
        media_type="model/step",
        headers=_headers(request, f"{_base_name(request.base_name)}.step"),
    )


@router.post("/stl")
async def export_stl(request: ExportRequest) -> Response:
    try:
        content = await build_stl(request.design, request.model_name)
    except Exception as exc:
        raise _export_error(exc) from exc
    return Response(
        content=content,
        media_type="application/sla",
        headers=_headers(request, f"{_base_name(request.base_name)}.stl"),
    )


@router.post("/profiles")
async def export_profiles(
    request: ExportRequest,
    kind: Literal["profiles", "slices"] = Query(default="profiles"),
) -> Response:
    try:
        content = await asyncio.to_thread(build_profiles, request.design, kind)
    except Exception as exc:
        raise _export_error(exc) from exc
    suffix = "profiles" if kind == "profiles" else "slices"
    return Response(
        content=content,
        media_type="text/csv",
        headers=_headers(request, f"{_base_name(request.base_name)}_{suffix}.csv"),
    )


@router.post("/wglink")
async def export_wglink(
    payload: WgLinkExportRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=240, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Write an identity-bearing CAD-link bundle into the selected workspace."""

    workspace: WorkspaceState = request.app.state.workspace
    selected = workspace.selected_path()
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail="No workspace folder has been selected. Choose a workspace folder first.",
        )
    store: CadLinkStore = request.app.state.cadlink_store
    return await run_on_gmsh_worker(
        _export_wglink_sync,
        payload,
        store,
        selected.resolve(),
        idempotency_key,
    )


def mount_exports(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["ExportRequest", "WgLinkExportRequest", "mount_exports", "router"]
