"""FastAPI routes for revision-bound geometry file exports."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal, Mapping

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from server.preview.errors import mesher_error_message
from server.preview.translate import design_to_mesher_config
from server.cadlink.identity import CadLink, SaveIdentity, design_hash
from server.cadlink.limits import MAX_STEP_INPUT_BYTES
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import serialize
from server.mesh.child import run_mesh_build
from server.platform.paths import app_root
from server.workspace.api import WorkspaceState

from server.platform.cad_launch import focus_cad
from server.cadlink.fusion.fusion_status import ADDIN_OUTDATED_MESSAGE, read_fusion_status

from .cad_handoff import UPDATE_TARGET_REQUIRED
from .core import _refuse_ath_step, build_profiles, build_step, build_step_solid, build_stl
from .wglink_geometry import build_bundle
from .wglink_publication import (BUNDLE_LAYOUT, PublicationConflict, bundle_products,
    row_facts, publish_bundle, finish_publication, recover_publications, recover_reserved_export)


logger = logging.getLogger(__name__)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, protected_namespaces=())

    design: DesignConfig
    design_revision: int = Field(alias="designRevision", ge=0)
    base_name: str = Field(default="waveguide", alias="baseName", max_length=240)
    model_name: str = Field(default="MWG Horn", alias="modelName", max_length=240)


class WgLinkExportRequest(ExportRequest):
    identity: SaveIdentity | None = None
    expected_fusion_document_id: str | None = Field(
        default=None, alias="expectedFusionDocumentId"
    )
    expected_fusion_instance_id: str | None = Field(
        default=None, alias="expectedFusionInstanceId", min_length=1
    )
    expected_fusion_return_state_hash: str | None = Field(
        default=None, alias="expectedFusionReturnStateHash"
    )


router = APIRouter(prefix="/api/export", tags=["exports"])
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_UNSAFE_SLUG = re.compile(r"[^a-z0-9_]+")
_KNOWN_DESIGN_EXTENSIONS = frozenset({".cfg", ".txt", ".mwg"})
_THROAT_PARAMETER = "_throat_dia"


def _base_name(value: str) -> str:
    leaf = Path(value.replace("\\", "/")).name
    path = Path(leaf)
    stem = path.stem if path.suffix.lower() in _KNOWN_DESIGN_EXTENSIONS else leaf
    stem = stem or "waveguide"
    return _UNSAFE_FILENAME.sub("_", stem).strip("._") or "waveguide"


def _slug(value: str) -> str:
    """The mesher's namespace rule, applied here so the record is canonical."""

    return _UNSAFE_SLUG.sub("_", value.strip().lower()).strip("_")


def _slug_from_manifest(manifest_json: str) -> str | None:
    """Recover the namespace an earlier export baked into its parameter names.

    ``wg_<slug>_throat_dia`` is in every supported solid build and is how the
    add-in itself reads the namespace back off a bundle, so a link made before
    the registry recorded slugs still resolves to exactly what its Fusion
    expressions reference.
    """

    try:
        parameters = (json.loads(manifest_json) or {}).get("parameters") or []
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    matches = {
        str(entry["name"])[len("wg_") : -len(_THROAT_PARAMETER)]
        for entry in parameters
        if isinstance(entry, Mapping)
        and entry.get("role") == "interface"
        and str(entry.get("name") or "").startswith("wg_")
        and str(entry.get("name") or "").endswith(_THROAT_PARAMETER)
    }
    if len(matches) != 1:
        return None
    return next(iter(matches)) or None


def _instance_slug(store: CadLinkStore, lineage_id: str, design_name: str) -> str:
    """The Fusion parameter namespace: minted once per lineage, never renamed.

    Fusion's datum and enclosure expressions name ``wg_<slug>_*`` for the life
    of the document and no update can retarget them, so a namespace that
    followed the filename made Save As an unrecoverable link break.
    """

    recorded = store.get_lineage_cad_names(lineage_id) or {}
    slug = str(recorded.get("parameter_slug") or "")
    if slug:
        return slug
    previous = store.find_latest_export_for_lineage(lineage_id)
    if previous is not None:
        slug = _slug_from_manifest(str(previous["manifest_json"] or "")) or ""
    slug = slug or _slug(design_name)
    if not slug:
        # A name with no letter or digit has no namespace to record; leave the
        # mesher to say so rather than freezing an unusable one to the lineage.
        return design_name
    recorded = store.record_lineage_cad_names(lineage_id, parameter_slug=slug)
    return str(recorded.get("parameter_slug") or slug)


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


def _export_error(exc: Exception, design: DesignConfig | None = None) -> HTTPException:
    detail = str(exc) if design is None else mesher_error_message(lambda: design_to_mesher_config(design), str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(status_code=422, detail=detail)
    if isinstance(exc, (ImportError, RuntimeError, sqlite3.OperationalError)):
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=f"Export failed: {detail}")


def _app_version() -> str:
    version_path = app_root() / "shared" / "version.json"
    return str(json.loads(version_path.read_text(encoding="utf-8"))["version"])


class _ExportIdentityConflict(Exception):
    pass


def _identity_for_export(
    store: CadLinkStore, row: Mapping[str, object]
) -> SaveIdentity | None:
    """The identity an existing export was made against, for idempotent retries."""

    design = store.get_design(str(row["design_id"]))
    if design is None or int(design["edit_version"]) != int(row["edit_version"]):
        return None
    return SaveIdentity(
        designId=str(row["design_id"]),
        lineageId=str(design["lineage_id"]),
        baseEditVersion=int(row["edit_version"]),
    )


def _commit_design_for_export(
    store: CadLinkStore,
    requested: SaveIdentity | None,
    design: DesignConfig,
    current_design_hash: str,
    design_name: str,
) -> SaveIdentity:
    """Return an identity whose registry head is exactly this design state.

    Send to CAD used to refuse a design that had never been saved, or that had
    been edited since its last save.  What a bundle needs is a recorded design
    state to name -- and the record that matters is the registry row, whose
    ``snapshot_text`` every export copies.  A .cfg the user has downloaded is
    not what makes the link resolvable, so commit the design on screen and
    export that instead of demanding the download first.
    """

    head = store.get_design(requested.design_id) if requested is not None else None
    filename = f"{design_name}.cfg"
    if (
        requested is not None
        and head is not None
        and str(head["lineage_id"]) == requested.lineage_id
        and int(head["edit_version"]) == requested.base_edit_version
        and str(head["design_hash"]) == current_design_hash
        and str(head["filename"]) == filename
    ):
        return requested

    save_requested = requested
    if (
        requested is not None
        and head is not None
        and str(head["lineage_id"]) == requested.lineage_id
        and int(head["edit_version"]) != requested.base_edit_version
    ):
        if str(head["design_hash"]) != current_design_hash:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This design is based on an older CAD-link registry version. "
                    "Open its current head from File → CAD-linked designs before "
                    "sending it to CAD."
                ),
            )
        if str(head["filename"]) == filename:
            return SaveIdentity(
                designId=str(head["design_id"]),
                lineageId=str(head["lineage_id"]),
                baseEditVersion=int(head["edit_version"]),
            )
        save_requested = SaveIdentity(
            designId=str(head["design_id"]),
            lineageId=str(head["lineage_id"]),
            baseEditVersion=int(head["edit_version"]),
        )

    def snapshot(link: CadLink) -> str:
        return serialize(design, cadlink=link)

    try:
        committed = store.save(
            requested=save_requested,
            design_hash=current_design_hash,
            filename=filename,
            snapshot_builder=snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    link = committed["identity"]
    return SaveIdentity(
        designId=link.design_id,
        lineageId=link.lineage_id,
        baseEditVersion=link.edit_version,
    )


def _wglink_response(
    row: Mapping[str, object], identity: SaveIdentity | None = None
) -> dict[str, Any]:
    stored_path = row.get("bundle_path")
    if not stored_path:
        raise HTTPException(
            status_code=409,
            detail="This older CAD export has no stored destination. Send it to CAD again.",
        )
    bundle_path = Path(str(stored_path))
    try:
        manifest = json.loads((bundle_path / "wglink.json").read_text(encoding="utf-8"))
        manifest_bundle_id = str((manifest.get("bundle") or {}).get("id") or "")
        manifest_export_id = str((manifest.get("export") or {}).get("id") or "")
        artifact_hash = str(
            ((manifest.get("files") or {}).get("waveguide.step") or {}).get("sha256") or ""
        )
    except (OSError, AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="The original CAD-link bundle is no longer available. Export it again.",
        ) from exc
    if (
        manifest_bundle_id != str(row["bundle_id"])
        or manifest_export_id != str(row["export_id"])
        or artifact_hash != str(row["artifact_sha256"])
        or not (bundle_path / "waveguide.step").is_file()
    ):
        raise HTTPException(
            status_code=409,
            detail="The original CAD-link bundle was replaced or changed. Export it again.",
        )
    if bundle_path.name == f'{row["export_id"]}.wglink':
        try:
            bundle_products(bundle_path, row_facts(row))
        except PublicationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload: dict[str, Any] = {
        "bundlePath": str(bundle_path),
        "bundleId": row["bundle_id"],
        "exportId": row["export_id"],
        "sequence": row["sequence"],
        "designHash": row["design_hash"],
        "geometryHash": row["geometry_hash"],
        "artifactSha256": row["artifact_sha256"],
    }
    if bundle_path.name == f'{row["export_id"]}.wglink':
        payload["bundleLayout"] = BUNDLE_LAYOUT
    if identity is not None:
        payload["identity"] = {
            "designId": identity.design_id,
            "lineageId": identity.lineage_id,
            "baseEditVersion": identity.base_edit_version,
        }
    return payload


UNAVAILABLE_WORKSPACE = (
    "The selected workspace folder is unavailable. Choose a workspace folder first."
)


def _export_wglink_sync(
    request: WgLinkExportRequest,
    store: CadLinkStore,
    workspace_root: Path,
    idempotency_key: str,
    unavailable_detail: str = UNAVAILABLE_WORKSPACE,
    handoff: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Build one identity-bearing bundle under ``workspace_root/wglink``.

    ``workspace_root`` is the user's selected workspace for the Fusion leg, and
    WG's own data directory for Onshape -- which has no local client, so
    requiring a workspace folder merely to reach a cloud CAD would be a tax with
    no purpose. Everything else about the bundle, including its identity and its
    place in the export sequence, is the same either way.
    """

    from server.design.native import NATIVE_FORMULAS

    if request.design.formula == "LOOKUP":
        raise HTTPException(status_code=422, detail="LOOKUP cannot be sent to CAD Link as ATH .cfg: ATH has no LOOKUP profile. Export STEP instead.")
    if request.design.formula in NATIVE_FORMULAS:
        raise HTTPException(status_code=422, detail=f"{request.design.formula} cannot be sent to CAD Link: native recipe transport is not supported. Export STEP instead.")

    try:
        _refuse_ath_step(request.design)
    except ValueError as exc:
        raise _export_error(exc, request.design) from exc
    workspace_root = workspace_root.resolve()
    if not workspace_root.is_dir():
        raise HTTPException(status_code=409, detail=unavailable_detail)
    wglink_root = (workspace_root / "wglink").resolve()
    if workspace_root != wglink_root and workspace_root not in wglink_root.parents:
        raise HTTPException(status_code=422, detail="CAD-link destination resolves outside the selected workspace")
    try:
        retry = store.find_export_by_idempotency_key(idempotency_key) or recover_reserved_export(store, idempotency_key)
    except PublicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if retry is not None:
        if design_hash(request.design) != retry["design_hash"] or (
            request.identity is not None and request.identity.design_id != retry["design_id"]
        ):
            raise HTTPException(status_code=409, detail="Idempotency key names a different CAD export identity.")
        result = _wglink_response(retry, _identity_for_export(store, retry))
        result.update(_finish_publication(store, retry))
        return result

    design_name = _base_name(request.base_name)
    current_design_hash = design_hash(request.design)
    identity = _commit_design_for_export(
        store, request.identity, request.design, current_design_hash, design_name
    )
    instance_slug = _instance_slug(store, identity.lineage_id, design_name)

    try:
        wglink_root.mkdir(exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=409, detail=unavailable_detail) from exc


    def build(facts: Mapping[str, object]) -> Mapping[str, str]:
        if (
            int(facts["editVersion"]) != identity.base_edit_version
            or str(facts["designHash"]) != current_design_hash
        ):
            raise _ExportIdentityConflict(
                "The design changed while the CAD export was starting. "
                "Send it to CAD again."
            )
        def write(staged: Path) -> None:
            # Only the child builds geometry. Publication and SQLite stay in
            # this parent thread; child failure cannot publish partial bytes.
            asyncio.run(run_mesh_build(build_bundle,
                request.design.model_dump(mode="json"), str(staged), dict(facts),
                identity.lineage_id, design_name, instance_slug, _app_version()))

        return publish_bundle(store, wglink_root, facts, write, handoff=handoff)

    try:
        row = store.allocate_export(
            design_id=identity.design_id,
            idempotency_key=idempotency_key,
            export_builder=build,
        )
    except _ExportIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PublicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _export_error(exc, request.design) from exc

    result = _wglink_response(row, identity)
    result.update(_finish_publication(store, row))
    return result


def _finish_publication(store: CadLinkStore, row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return finish_publication(store, row)
    except PublicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError, TypeError, sqlite3.OperationalError) as exc:
        raise HTTPException(status_code=503, detail="CAD-link publication is incomplete. Retry this Send.") from exc


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
            request, f"{_base_name(request.base_name)}.step", warning, payload
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
            request, f"{_base_name(request.base_name)}.stl", result.warning, result.data
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
            request, f"{_base_name(request.base_name)}_{suffix}.csv", None, payload
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
        _export_wglink_sync,
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
