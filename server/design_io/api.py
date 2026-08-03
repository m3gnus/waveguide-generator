"""Open, save, and classify ATH/MWG text design documents."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException
from pydantic import ValidationError

from server.design.schema import DesignConfig
from server.design.textcfg import ParsedDesign, TextConfigError, parse, serialize


router = APIRouter(prefix="/api/design", tags=["design-io"])
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _suggested_filename(value: object = None) -> str:
    raw = Path(str(value or "waveguide").replace("\\", "/")).name
    stem = Path(raw).stem or "waveguide"
    stem = _SAFE_STEM.sub("_", stem).strip("._") or "waveguide"
    return f"{stem}.cfg"


def _parse_detail(exc: Exception) -> dict[str, str]:
    return {"type": "parse_error", "message": str(exc)}


def _report(parsed: ParsedDesign) -> dict[str, Any]:
    migrations = [
        {"name": application.name, "note": application.note}
        for application in parsed.migrations
    ]
    return {
        "dialect": parsed.dialect,
        "migrationsApplied": migrations,
        "passthrough": {
            "keysPreserved": sorted(parsed.extra_keys),
            "blocksPreserved": sorted(parsed.extra_blocks),
            "keyCount": len(parsed.extra_keys),
            "blockCount": len(parsed.extra_blocks),
        },
    }


def _parse_text(text: str) -> ParsedDesign:
    try:
        return parse(text)
    except (TextConfigError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_parse_detail(exc)) from exc


@router.post("/save")
async def save_design(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Serialize a typed design to v1-readable text with a canonical .cfg name."""

    design_payload: object = payload.get("design", payload)
    filename = payload.get("filename") if "design" in payload else None
    try:
        design = DesignConfig.model_validate(design_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return {
        "text": serialize(design),
        "suggestedFilename": _suggested_filename(filename),
    }


@router.post("/open")
async def open_design(text: str = Body(..., media_type="text/plain")) -> dict[str, Any]:
    """Parse and migrate .cfg/.txt/legacy .mwg content into API JSON."""

    parsed = _parse_text(text)
    return {
        "design": parsed.semantic_data(),
        **_report(parsed),
    }


@router.post("/import-report")
async def import_report(text: str = Body(..., media_type="text/plain")) -> dict[str, Any]:
    """Classify a document without mutating any client or server design state."""

    return _report(_parse_text(text))


def mount_design_io(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["mount_design_io", "router"]
