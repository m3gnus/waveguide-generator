"""FastAPI routes for revision-bound geometry file exports."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Literal, Mapping

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import CadLink, SaveIdentity, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.design.textcfg import serialize
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.preview.translate import design_to_mesher_config
from server.workspace.api import WorkspaceState, _path_segments, _portable_path_key

from .cad_launch import focus_cad
from .cad_handoff import publish_fusion_handoff
from .core import build_profiles, build_step, build_step_solid, build_stl
from .geometry_identity import geometry_hash as _geometry_hash
from .geometry_identity import mesher_version as _mesher_version


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
    expected_fusion_return_state_hash: str | None = Field(
        default=None, alias="expectedFusionReturnStateHash"
    )


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


def _app_version() -> str:
    version_path = Path(__file__).resolve().parents[2] / "shared" / "version.json"
    return str(json.loads(version_path.read_text(encoding="utf-8"))["version"])


def _exchange_directories(first: Path, second: Path) -> None:
    """Exchange two directory entries, atomically where the OS can.

    POSIX systems swap without a reader-visible gap (renamex_np on macOS,
    renameat2 elsewhere). Windows has no directory-exchange syscall reachable
    from Python, so it falls back to a three-rename swap with the same
    postcondition -- the old bundle ends up at ``first`` for the caller to
    remove -- and a sub-millisecond window in which the destination name does
    not exist. ``ctypes.CDLL(None)`` is the POSIX handle to libc and raises on
    Windows, which is why the platform check comes first.
    """

    if os.name != "posix":
        aside = second.with_name(second.name + f".swap-{os.getpid()}")
        os.rename(second, aside)
        try:
            os.rename(first, second)
        except OSError:
            os.rename(aside, second)  # restore the live bundle before failing
            raise
        os.rename(aside, first)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    encoded_first = os.fsencode(first)
    encoded_second = os.fsencode(second)
    if hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(encoded_first, encoded_second, 0x00000002)  # RENAME_SWAP
    elif hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, encoded_first, -100, encoded_second, 0x00000002)
    else:
        raise OSError("atomic directory exchange is unavailable on this platform")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(first), str(second))


def _replace_bundle(staged: Path, destination: Path) -> None:
    """Publish a complete bundle atomically for readers of the live path."""

    if not destination.exists() and not destination.is_symlink():
        os.replace(staged, destination)
        return
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError(f"CAD-link destination is not a bundle directory: {destination}")
    _exchange_directories(staged, destination)
    shutil.rmtree(staged, ignore_errors=True)


def _bundle_destination(
    wglink_root: Path, design_name: str, design_id: str
) -> Path:
    """Resolve a stable portable name without overwriting another design.

    A fresh/unsaved document has no filename, so every such document asks for
    ``waveguide.wglink``.  Refusing the second one made Send to CAD fail before
    it ever reached the Fusion launcher.  Keep the readable name for the first
    owner and give later owners a deterministic design-id suffix; subsequent
    exports of either design then keep updating their own bundle in place.
    """

    def available(filename: str) -> Path | None:
        requested_key = _portable_path_key([filename])
        for existing in wglink_root.iterdir():
            if _portable_path_key([existing.name]) != requested_key:
                continue
            if existing.name != filename:
                raise ValueError(
                    "CAD-link bundle name collides with an existing portable "
                    f"name: {existing.name}"
                )
            if existing.is_symlink() or not existing.is_dir():
                raise ValueError(
                    "CAD-link bundle name conflicts with an existing workspace "
                    f"entry: {existing.name}"
                )
            try:
                manifest = json.loads(
                    (existing / "wglink.json").read_text(encoding="utf-8")
                )
                existing_design_id = str(
                    (manifest.get("design") or {}).get("id") or ""
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "CAD-link bundle name conflicts with an unreadable existing "
                    f"bundle: {existing.name}"
                ) from exc
            return existing if existing_design_id == design_id else None
        return wglink_root / filename

    requested = f"{design_name}.wglink"
    destination = available(requested)
    if destination is not None:
        return destination

    # Eight hex digits are ample for the ordinary case.  Trying longer slices
    # keeps the rule deterministic even under a deliberately constructed hash
    # prefix collision.  Trim a maximal user stem so the filename remains a
    # valid 255-byte portable component after adding the suffix and extension.
    digest = hashlib.sha256(design_id.encode("utf-8")).hexdigest()
    stem = design_name[:239].rstrip("._-") or "waveguide"
    for length in (8, 12, 16, 32, 64):
        candidate = f"{stem}-{digest[:length]}.wglink"
        destination = available(candidate)
        if destination is not None:
            return destination

    raise ValueError("Could not allocate a unique CAD-link bundle name.")


class _ExportIdentityConflict(Exception):
    pass


class _BundleNameConflict(Exception):
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
        and str(head["design_hash"]) == current_design_hash
    ):
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
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
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
    payload: dict[str, Any] = {
        "bundlePath": str(bundle_path),
        "bundleId": row["bundle_id"],
        "exportId": row["export_id"],
        "sequence": row["sequence"],
        "designHash": row["design_hash"],
        "geometryHash": row["geometry_hash"],
        "artifactSha256": row["artifact_sha256"],
    }
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
) -> dict[str, Any]:
    """Build one identity-bearing bundle under ``workspace_root/wglink``.

    ``workspace_root`` is the user's selected workspace for the Fusion leg, and
    WG's own data directory for Onshape -- which has no local client, so
    requiring a workspace folder merely to reach a cloud CAD would be a tax with
    no purpose. Everything else about the bundle, including its identity and its
    place in the export sequence, is the same either way.
    """

    from hornlab_mesher import WgLinkIdentity, write_wglink
    from hornlab_mesher.config_builder import resolve_geometry

    workspace_root = workspace_root.resolve()
    if not workspace_root.is_dir():
        raise HTTPException(status_code=409, detail=unavailable_detail)
    wglink_root = (workspace_root / "wglink").resolve()
    if workspace_root != wglink_root and workspace_root not in wglink_root.parents:
        raise HTTPException(status_code=422, detail="CAD-link destination resolves outside the selected workspace")
    retry = store.find_export_by_idempotency_key(idempotency_key)
    if retry is not None:
        return _wglink_response(retry, _identity_for_export(store, retry))

    design_name = _base_name(request.base_name)
    try:
        _path_segments(f"{design_name}.wglink", "CAD-link bundle name")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    current_design_hash = design_hash(request.design)
    identity = _commit_design_for_export(
        store, request.identity, request.design, current_design_hash, design_name
    )

    config = design_to_mesher_config(request.design)
    resolved = resolve_geometry(config)
    mesher_version = _mesher_version()
    geometry_hash = _geometry_hash(resolved.geometry, mesher_version)
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
        try:
            destination = _bundle_destination(
                wglink_root, design_name, identity.design_id
            )
        except ValueError as exc:
            raise _BundleNameConflict(str(exc)) from exc
        temporary_root = Path(tempfile.mkdtemp(prefix=".wg2-wglink-", dir=wglink_root))
        staged = temporary_root / "bundle.wglink"
        bundle_identity = WgLinkIdentity(
            bundle={"id": facts["bundleId"], "created_at": facts["createdAt"]},
            generator={
                "app": "waveguide-generator",
                "app_version": _app_version(),
                "mesher_version": mesher_version,
                "datum_schema": 1,
            },
            design={
                "id": facts["designId"],
                "lineage_id": identity.lineage_id,
                "edit_version": facts["editVersion"],
                "design_hash": facts["designHash"],
                "name": design_name,
                "formula": request.design.root.formula.lower(),
                # The STEP and point grid are the realized geometry; this is
                # the exact WG config that produced them. WGLink persists the
                # snapshot with the managed Fusion instance so future updates,
                # audits, and round trips do not have to reverse-engineer a
                # formula from the body or from the smaller CAD parameter set.
                "config": request.design.model_dump(mode="json", by_alias=True),
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
                identity=bundle_identity,
                instance_slug=design_name,
                open_throat=True,
            )
            manifest_json = product.manifest_path.read_text(encoding="utf-8")
            artifact_sha256 = str(product.manifest["files"]["waveguide.step"]["sha256"])
            _replace_bundle(staged, destination)
            return {
                "manifest_json": manifest_json,
                "geometry_hash": geometry_hash,
                "artifact_sha256": artifact_sha256,
                "bundle_path": str(destination),
            }
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    try:
        row = store.allocate_export(
            design_id=identity.design_id,
            idempotency_key=idempotency_key,
            export_builder=build,
        )
    except _ExportIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except _BundleNameConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _export_error(exc) from exc

    return _wglink_response(row, identity)


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
    result = await run_on_gmsh_worker(
        _export_wglink_sync,
        payload,
        store,
        selected.resolve(),
        idempotency_key,
    )
    # The marker is written before Fusion is raised so a cold-started add-in
    # can consume the exact export that caused the launch.  Like window focus,
    # this is delivery metadata: a failure must not invalidate the completed,
    # durable bundle.
    try:
        await asyncio.to_thread(
            publish_fusion_handoff,
            Path(request.app.state.data_dir),
            selected.resolve(),
            result,
            expected_document_id=payload.expected_fusion_document_id,
            expected_return_state_hash=payload.expected_fusion_return_state_hash,
        )
        result["cadHandoff"] = "published"
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not publish the Fusion handoff: %s", exc)
        result["cadHandoff"] = "failed"
    # The bundle is already on disk and the response is already earned, so
    # raising Fusion is strictly a courtesy: it runs off the request thread and
    # its outcome only reaches the log.
    result["cadLaunch"] = await asyncio.to_thread(focus_cad)
    return result


def mount_exports(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["ExportRequest", "WgLinkExportRequest", "mount_exports", "router"]
