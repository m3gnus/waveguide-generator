"""Open, save, and classify ATH/MWG text design documents."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any, Callable

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from server.cadlink.identity import SaveIdentity, classify_open, design_hash
from server.cadlink.store import CadLinkStore
from server.workspace.api import select_workspace_path
from server.design.uid import validate_design_uid
from server.design.schema import DesignConfig, Expr, OSSEConfig
from server.jobs.design_reader import parse, read_design
from server.design.textcfg import ParsedDesign, TextConfigError, serialize
from server.preview.errors import is_user_refusal, user_error_detail


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
    from server.preview.errors import user_error_message
    return {"type": "parse_error", "message": user_error_message(exc)}


def _report(parsed: ParsedDesign) -> dict[str, Any]:
    migrations = [
        {"name": application.name, "note": application.note}
        for application in parsed.migrations
    ]
    return {
        "dialect": parsed.dialect,
        "migrationsApplied": migrations,
        # Settings the file states that the solve does not read. Additive and
        # optional: an older client that ignores the key sees what it always saw.
        "ignoredSettings": [
            {"key": item.key, "value": item.value, "note": item.note}
            for item in parsed.ignored_settings
        ],
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


def _save_design(
    payload: dict[str, Any], *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Serialize and atomically commit a typed design identity."""

    registry = store or _DIRECT_STORE
    design_payload: object = payload.get("design", payload)
    filename = payload.get("filename") if "design" in payload else None
    try:
        design = read_design(design_payload)
        design_uid = validate_design_uid(payload.get("designUid") if "design" in payload else None)
        requested = (
            SaveIdentity.model_validate(payload["identity"])
            if "design" in payload and payload.get("identity") is not None
            else None
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    except ValueError as exc:
        # The Custom profile import refuses a saved table it cannot convert.
        if not is_user_refusal(exc):
            raise
        raise HTTPException(status_code=422, detail=user_error_detail(exc)) from exc
    suggested_filename = _suggested_filename(filename)
    hashed = design_hash(design)
    try:
        committed = registry.save(
            requested=requested,
            design_hash=hashed,
            filename=suggested_filename,
            snapshot_builder=lambda identity: serialize(design, cadlink=identity, design_uid=design_uid),
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


def _serialize_design(payload: dict[str, Any]) -> dict[str, Any]:
    """Serialize a typed design without creating or advancing CadLink identity."""

    design_payload: object = payload.get("design", payload)
    filename = payload.get("filename") if "design" in payload else None
    try:
        design = read_design(design_payload)
        design_uid = validate_design_uid(payload.get("designUid") if "design" in payload else None)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc
    except ValueError as exc:
        # The Custom profile import refuses a saved table it cannot convert.
        if not is_user_refusal(exc):
            raise
        raise HTTPException(status_code=422, detail=user_error_detail(exc)) from exc
    suggested_filename = _suggested_filename(filename)
    return {
        "text": serialize(design, design_uid=design_uid),
        "suggestedFilename": suggested_filename,
    }


def _open_design(
    text: str, *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Parse and classify .cfg/.txt/legacy .mwg content without mutation."""

    parsed = _parse_text(text)
    return {
        "design": parsed.semantic_data(),
        "designUid": parsed.design_uid,
        **_report(parsed),
        "cadlink": _open_cadlink(parsed, store or _DIRECT_STORE),
    }


def _import_report(
    text: str, *, store: CadLinkStore | None = None
) -> dict[str, Any]:
    """Classify a document without mutating any client or server design state."""

    parsed = _parse_text(text)
    return {
        **_report(parsed),
        "cadlink": _open_cadlink(parsed, store or _DIRECT_STORE),
    }


async def save_design(payload: dict[str, Any], *, store: CadLinkStore | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_save_design, payload, store=store)


async def serialize_design(payload: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_serialize_design, payload)


async def open_design(text: str, *, store: CadLinkStore | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_open_design, text, store=store)


async def import_report(text: str, *, store: CadLinkStore | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_import_report, text, store=store)


def _json_response(process: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> JSONResponse:
    # Encode on the same worker: returning a dict would serialize a large
    # LOOKUP document again on the event loop inside FastAPI.
    return JSONResponse(process(*args, **kwargs))


def _read_selected_design(selected_path: str) -> dict[str, Any]:
    path = Path(selected_path).resolve()
    try:
        if path.suffix.lower() not in {".cfg", ".mwg", ".txt", ".json"}:
            raise ValueError("Choose a .cfg, .mwg, .txt or .json design file")
        with path.open("rb") as stream:
            content = stream.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("Design exceeds the 16 MiB open limit")
        text = content.decode("utf-8-sig")
        stat = path.stat()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"selected": True, "outcome": "chosen", "filename": path.name,
            "source": str(path), "text": text, "fileKey": f"{stat.st_dev}:{stat.st_ino}"}


def create_design_io_router(store: CadLinkStore) -> APIRouter:
    router = APIRouter(prefix="/api/design", tags=["design-io"])

    @router.post("/choose", response_model=dict[str, Any])
    async def choose_endpoint() -> JSONResponse:
        # Only the native picker supplies a path: callers cannot ask this endpoint
        # to read an arbitrary server file. Paths distinguish same-name copies.
        selected = await asyncio.to_thread(select_workspace_path, "Open design", file=True)
        if selected.outcome != "chosen":
            return JSONResponse({"selected": False, "outcome": selected.outcome})
        return await asyncio.to_thread(_json_response, _read_selected_design, selected.path)

    @router.post("/save", response_model=dict[str, Any])
    async def save_endpoint(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        return await asyncio.to_thread(_json_response, _save_design, payload, store=store)

    @router.post("/serialize", response_model=dict[str, Any])
    async def serialize_endpoint(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        return await asyncio.to_thread(_json_response, _serialize_design, payload)

    @router.post("/resolve-expressions")
    async def resolve_expressions(design: DesignConfig, paths: list[str]) -> dict[str, float | None]:
        """Resolve OSSE constants/defaults without trusting editor samples."""
        from hornlab_mesher.config_builder import build_geometry_params
        from hornlab_mesher.profile_common import eval_param
        from server.preview.translate import design_to_mesher_config

        # Validate the wire document through the preview's migration owner before
        # resolving anything: unversioned WG log/ln differ from ATH expressions.
        if not isinstance(design.root, OSSEConfig):
            raise HTTPException(
                status_code=422,
                detail="Expression resolution requires an OSSE design. Choose OSSE under Family and try again.",
            )
        root = design.root

        # Expr owns constant detection and dialect translation. Use the geometry
        # evaluator for the final value so freezing cannot change its last bits.
        def number(value):
            if not isinstance(value, Expr) and type(value) not in (int, float):
                return None
            expression = value if isinstance(value, Expr) else Expr.model_validate(value)
            return eval_param(expression.execution_text()) if expression.constant_value() is not None else None

        def same_numbers(before, after):
            if isinstance(before, dict) and isinstance(after, dict):
                return before.keys() == after.keys() and all(same_numbers(value, after[key]) for key, value in before.items())
            if isinstance(before, list) and isinstance(after, list):
                return len(before) == len(after) and all(same_numbers(a, b) for a, b in zip(before, after))
            if before == after:
                return True
            try:
                return eval_param(before) == eval_param(after)
            except (TypeError, ValueError):
                return False

        def config(root):
            return design_to_mesher_config(DesignConfig(root=root))

        inputs = {}
        for path in paths:
            value = root
            for part in path.split('.'):
                value = getattr(value, part, None)
            inputs[path] = value
        result = {path: number(value) for path, value in inputs.items()}
        if 'scale' in inputs and isinstance(inputs['scale'], Expr):
            # Scale is structural: its existing owner uses constant_value(),
            # whose bounded arithmetic can differ from profile evaluation.
            result['scale'] = inputs['scale'].constant_value()
        missing = [path for path, value in inputs.items() if value is None and '.' not in path]
        # Let the existing field error name unresolved expressions before trying
        # to construct defaults (a parameterized Scale cannot build a profile).
        if any(value is not None and result[path] is None for path, value in inputs.items()):
            return result

        def parameters(root):
            return build_geometry_params(config(root))[0]

        try:
            original_config = config(root)
            for path, value in inputs.items():
                if not isinstance(value, Expr) or result[path] is None:
                    continue
                explicit = root.model_copy(deep=True)
                target = explicit
                parts = path.split('.')
                for part in parts[:-1]:
                    target = getattr(target, part)
                setattr(target, parts[-1], Expr(value=result[path]))
                # Legacy scaling formats expression multipliers differently
                # from literal multiplication. Refuse even a last-bit change.
                if not same_numbers(original_config, config(explicit)):
                    result[path] = None
            if not missing:
                return result
            # Read absent dimensions from the owner that builds the current horn,
            # never from the editor's new-design template.
            defaults = parameters(root)
            for path in missing:
                resolved = number(defaults.get(path))
                if resolved is None:
                    continue
                updated = parameters(root.model_copy(update={path: Expr(value=resolved)}))
                # Older saved imports leave omitted dimensions unscaled, while
                # explicit dimensions receive Scale. Keep the guard when making
                # a default explicit would change its effective geometry.
                if updated.get(path) == defaults.get(path):
                    result[path] = resolved
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=_parse_detail(exc)) from exc
        return result

    @router.post("/open", response_model=dict[str, Any])
    async def open_endpoint(
        text: str = Body(..., media_type="text/plain"),
    ) -> JSONResponse:
        return await asyncio.to_thread(_json_response, _open_design, text, store=store)

    @router.post("/import-report", response_model=dict[str, Any])
    async def import_report_endpoint(
        text: str = Body(..., media_type="text/plain"),
    ) -> JSONResponse:
        return await asyncio.to_thread(_json_response, _import_report, text, store=store)

    return router


router = create_design_io_router(_DIRECT_STORE)


def mount_design_io(application: FastAPI) -> CadLinkStore:
    registry = CadLinkStore.for_data_dir(Path(application.state.data_dir))
    application.state.cadlink_store = registry
    workspace = getattr(application.state, "workspace", None)
    if workspace is not None:
        registry.archive_root = workspace.path
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
