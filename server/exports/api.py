"""FastAPI routes for revision-bound geometry file exports."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response

from server.cadlink.limits import MAX_STEP_INPUT_BYTES
from server.cadlink.store import CadLinkStore
from server.workspace.api import WorkspaceState

from server.platform.cad_launch import focus_cad
from server.cadlink.fusion.fusion_status import ADDIN_OUTDATED_MESSAGE, read_fusion_status

from .cad_handoff import UPDATE_TARGET_REQUIRED
from .core import build_profiles, build_step, build_step_solid, build_stl
from .wglink_publication import recover_publications
from .wglink_service import (
    ExportRequest as ExportRequest,
    WgLinkExportRequest as WgLinkExportRequest,
    export_error as _export_error,
    export_wglink_sync,
    normalize_base_name,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/export", tags=["exports"])
#: The size at which a geometry export says how big it is.
#:
#: Not a limit and not a gate. A STEP file is about 97% ``CARTESIAN_POINT``
#: records, so its size is the grid the fidelity search chose and nothing else;
#: a design that needs a fine grid gets a big file and gets it in full. What the
#: note buys is that a 30 MB download stops being a surprise, and that a user
#: whose next step is to send the same geometry back through the CAD link knows
#: it is heading toward ``MAX_STEP_INPUT_BYTES`` -- the 64 MiB refusal budget on
#: CAD *input*, which is a real gate and stays exactly where it is.
EXPORT_SIZE_NOTE_BYTES = 16 * 1024 * 1024


def _mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MiB"


def _size_note(payload: bytes | None) -> str | None:
    """Say how large a served export is, once it is worth remarking on.

    Returns ``None`` for everything at or below the threshold: the warning
    header means a compromise was made, so a note that fired on ordinary
    exports would train the reader to ignore it.
    """

    if payload is None or len(payload) <= EXPORT_SIZE_NOTE_BYTES:
        return None
    return (
        f"This export is {_mib(len(payload))}, above the "
        f"{_mib(EXPORT_SIZE_NOTE_BYTES)} an export of this kind is normally "
        "expected to reach. It was written in full and nothing was coarsened "
        "or refused for its size; expect a slow open in CAD, and note that "
        f"reading a file this large back in approaches the "
        f"{_mib(MAX_STEP_INPUT_BYTES)} ceiling on CAD input."
    )


def _headers(
    request: ExportRequest,
    filename: str,
    warning: str | None = None,
    payload: bytes | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Design-Revision": str(request.design_revision),
    }
    note = _size_note(payload)
    if note:
        # Joined with a space, the way the sizing planners already stack their
        # own notes: a compromised grid and a large file are two facts about
        # the same export and the user should get both.
        warning = f"{warning} {note}" if warning else note
    if warning:
        # A sizing backstop reports itself rather than refusing the export, so
        # the file is always served and the note rides with it. Header values
        # are latin-1 on the wire; the messages are ASCII, but encode
        # defensively rather than 500 on a stray character.
        headers["X-Export-Warning"] = warning.encode("ascii", "replace").decode()
    return headers


@router.post("/step")
async def export_step(
    request: ExportRequest,
    body: Literal["solid", "surface"] = Query(default="solid"),
) -> Response:
    """Export STEP. ``solid`` is the manufacturable part; ``surface`` the bore."""

    try:
        if body == "solid":
            solid = await build_step_solid(request.design)
            content, warning = solid.step_text, solid.warning
        else:
            surface = await build_step(request.design)
            content, warning = surface.step_text, surface.warning
    except Exception as exc:
        raise _export_error(exc, request.design) from exc
    # Both bodies size themselves and both can end up on a grid that missed the
    # target or was never measured against it. The file is still served -- an
    # export is never refused for this -- and the note rides with it, exactly as
    # the STL's sizing backstop already did.
    if warning:
        logger.warning("STEP %s export sizing: %s", body, warning)
    # Encoded once, here, so the size the note quotes is the size of the bytes
    # that go out rather than a second measurement of the same text.
    payload = content.encode("utf-8")
    return Response(
        content=payload,
        media_type="model/step",
        headers=_headers(
            request, f"{normalize_base_name(request.base_name)}.step", warning, payload
        ),
    )


@router.post("/stl")
async def export_stl(request: ExportRequest) -> Response:
    try:
        result = await build_stl(request.design, request.model_name)
    except Exception as exc:
        raise _export_error(exc, request.design) from exc
    if result.warning:
        logger.warning("STL export sizing: %s", result.warning)
    return Response(
        content=result.data,
        media_type="application/sla",
        headers=_headers(
            request, f"{normalize_base_name(request.base_name)}.stl", result.warning, result.data
        ),
    )


@router.post("/profiles")
async def export_profiles(
    request: ExportRequest,
    kind: Literal["profiles", "slices"] = Query(default="profiles"),
) -> Response:
    try:
        content = await asyncio.to_thread(build_profiles, request.design, kind)
    except Exception as exc:
        raise _export_error(exc, request.design) from exc
    suffix = "profiles" if kind == "profiles" else "slices"
    payload = content.encode("utf-8")
    return Response(
        content=payload,
        media_type="text/csv",
        headers=_headers(
            request, f"{normalize_base_name(request.base_name)}_{suffix}.csv", None, payload
        ),
    )


@router.post("/wglink")
async def export_wglink(
    payload: WgLinkExportRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=240, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Write an identity-bearing CAD-link bundle into the selected workspace."""

    if payload.expected_fusion_instance_id and not (
        payload.expected_fusion_document_id and payload.expected_fusion_return_state_hash
    ):
        # An update names its exact link and the model state WG measured, so the
        # add-in can refuse a model that moved. Refused before anything is built.
        raise HTTPException(status_code=422, detail=UPDATE_TARGET_REQUIRED)
    workspace: WorkspaceState = request.app.state.cad_workspace
    selected = workspace.selected_path()
    if selected is None:
        raise HTTPException(
            status_code=409,
            detail="No WGLink folder has been selected. Choose one in Settings → CAD Link first.",
        )
    store: CadLinkStore = request.app.state.cadlink_store
    result = await asyncio.to_thread(
        export_wglink_sync,
        payload,
        store,
        selected.resolve(),
        idempotency_key,
        handoff={"dataDir": str(request.app.state.data_dir),
                 "documentId": payload.expected_fusion_document_id,
                 "instanceId": payload.expected_fusion_instance_id,
                 "returnStateHash": payload.expected_fusion_return_state_hash},
    )
    # Startup repairs artifacts only. A live request earns the delivery outcome
    # above before this courtesy focus operation.
    waiting = await asyncio.to_thread(_handoff_waiting_notice, Path(request.app.state.data_dir))
    if waiting is not None:
        result["cadHandoffWaiting"] = waiting
    # The bundle is already on disk and the response is already earned, so
    # raising Fusion is strictly a courtesy: it runs off the request thread and
    # its outcome only reaches the log.
    result["cadLaunch"] = await asyncio.to_thread(focus_cad)
    return result


def _handoff_waiting_notice(data_dir: Path) -> str | None:
    """The remedy when the add-in Fusion runs is too old to take the handoff.

    The handoff is published anyway: the add-in WG installed takes it once
    Fusion loads it. Until then nothing happens in Fusion, so the user is told
    why rather than left waiting.
    """

    status = read_fusion_status(
        data_dir, current_design_hash="", current_formula="", design_id=None
    )
    return ADDIN_OUTDATED_MESSAGE if status.get("state") == "addin_outdated" else None


def mount_exports(application: FastAPI) -> None:
    application.include_router(router)

    async def recover_on_startup() -> None:
        await asyncio.to_thread(recover_publications, application.state.cadlink_store)

    application.router.add_event_handler("startup", recover_on_startup)


__all__ = ["ExportRequest", "WgLinkExportRequest", "mount_exports", "router"]
