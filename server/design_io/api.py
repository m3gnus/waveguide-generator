"""Open, save, and classify ATH/MWG text design documents."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException
from pydantic import ValidationError

from server.cadlink.identity import SaveIdentity, classify_open, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import ParsedDesign, TextConfigError, parse, serialize


_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
_UNTITLED_STEM = "untitled"
_DIRECT_STORE = CadLinkStore(":memory:")


def _suggested_filename(value: object = None) -> str:
    """Sanitize a requested filename, defaulting to the untitled stem.

    The client derives this from the design's name and sends it, so the
    fallback only covers a bare-design save from the API itself. It is the same
    word the interface shows for a design nobody has named."""

    raw = Path(str(value or _UNTITLED_STEM).replace("\\", "/")).name
    stem = Path(raw).stem or _UNTITLED_STEM
    stem = _SAFE_STEM.sub("_", stem).strip("._") or _UNTITLED_STEM
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


def _open_cadlink(parsed: ParsedDesign, store: CadLinkStore) -> dict[str, Any]:
    computed_hash = design_hash(parsed.design)
    head = store.get_design(parsed.cadlink.design_id) if parsed.cadlink else None
    classification = classify_open(parsed.cadlink, computed_hash, head)
    candidate = store.find_design_by_hash(computed_hash) if parsed.cadlink is None else None
    identity = parsed.cadlink.api_dict() if parsed.cadlink else None
    if identity is not None:
        identity["baseEditVersion"] = parsed.cadlink.edit_version
    return {
        "identity": identity,
        "classification": classification,
        "adoptionCandidate": (
            {
                "designId": candidate["design_id"],
                "lineageId": candidate["lineage_id"],
                "baseEditVersion": candidate["edit_version"],
                "filename": candidate["filename"],
            }
            if candidate is not None
            else None
        ),
    }


async def save_design(
    payload: dict[str, Any], *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Serialize and atomically commit a typed design identity."""

    registry = store or _DIRECT_STORE
    design_payload: object = payload.get("design", payload)
    filename = payload.get("filename") if "design" in payload else None
    suggested_filename = _suggested_filename(filename)
    try:
        design = DesignConfig.model_validate(design_payload)
        requested = (
            SaveIdentity.model_validate(payload["identity"])
            if "design" in payload and payload.get("identity") is not None
            else None
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    hashed = design_hash(design)
    try:
        committed = registry.save(
            requested=requested,
            design_hash=hashed,
            filename=suggested_filename,
            snapshot_builder=lambda identity: serialize(design, cadlink=identity),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    identity = committed["identity"]
    response_identity = {
        "designId": identity.design_id,
        "lineageId": identity.lineage_id,
        "baseEditVersion": identity.edit_version,
    }
    return {
        "text": committed["text"],
        "suggestedFilename": suggested_filename,
        "identity": response_identity,
        "forked": committed["forked"],
        "from": committed["from"],
    }


async def serialize_design(payload: dict[str, Any]) -> dict[str, Any]:
    """Serialize a typed design without creating or advancing CadLink identity."""

    design_payload: object = payload.get("design", payload)
    filename = payload.get("filename") if "design" in payload else None
    suggested_filename = _suggested_filename(filename)
    try:
        design = DesignConfig.model_validate(design_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return {
        "text": serialize(design),
        "suggestedFilename": suggested_filename,
    }


async def open_design(
    text: str, *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Parse and classify .cfg/.txt/legacy .mwg content without mutation."""

    parsed = _parse_text(text)
    return {
        "design": parsed.semantic_data(),
        **_report(parsed),
        "cadlink": _open_cadlink(parsed, store or _DIRECT_STORE),
    }


async def import_report(
    text: str, *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Classify a document without mutating any client or server design state."""

    parsed = _parse_text(text)
    return {
        **_report(parsed),
        "cadlink": _open_cadlink(parsed, store or _DIRECT_STORE),
    }


def create_design_io_router(store: CadLinkStore) -> APIRouter:
    router = APIRouter(prefix="/api/design", tags=["design-io"])

    @router.post("/save")
    async def save_endpoint(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return await save_design(payload, store=store)

    @router.post("/serialize")
    async def serialize_endpoint(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return await serialize_design(payload)

    @router.post("/open")
    async def open_endpoint(
        text: str = Body(..., media_type="text/plain"),
    ) -> dict[str, Any]:
        return await open_design(text, store=store)

    @router.post("/import-report")
    async def import_report_endpoint(
        text: str = Body(..., media_type="text/plain"),
    ) -> dict[str, Any]:
        return await import_report(text, store=store)

    return router


router = create_design_io_router(_DIRECT_STORE)


def mount_design_io(application: FastAPI) -> CadLinkStore:
    registry = CadLinkStore.for_data_dir(Path(application.state.data_dir))
    application.state.cadlink_store = registry
    application.include_router(create_design_io_router(registry))
    application.router.add_event_handler("shutdown", registry.close)
    return registry


__all__ = [
    "create_design_io_router",
    "import_report",
    "mount_design_io",
    "open_design",
    "router",
    "save_design",
    "serialize_design",
]
