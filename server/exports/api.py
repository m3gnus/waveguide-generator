"""FastAPI routes for revision-bound geometry file exports."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from server.design.schema import DesignConfig

from .core import build_profiles, build_step, build_step_solid, build_stl


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, protected_namespaces=())

    design: DesignConfig
    design_revision: int = Field(alias="designRevision", ge=0)
    base_name: str = Field(default="waveguide", alias="baseName", max_length=240)
    model_name: str = Field(default="MWG Horn", alias="modelName", max_length=240)


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


@router.post("/step")
async def export_step(
    request: ExportRequest,
    body: Literal["solid", "surface"] = Query(default="solid"),
) -> Response:
    """Export STEP. ``solid`` is the manufacturable part; ``surface`` the bore."""

    try:
        content = await (
            build_step_solid(request.design)
            if body == "solid"
            else build_step(request.design)
        )
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


def mount_exports(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["ExportRequest", "mount_exports", "router"]
