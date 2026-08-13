"""FastAPI routes for the Onshape leg of the CAD link.

Two rules shape this module.

*Status polling never touches the network.* The CAD Link panel polls, and
Onshape's rate limit is a shared account resource; everything the panel needs to
decide "linked / current / stale" is already in WG's registry. Only an explicit
connection check and an actual send talk to Onshape.

*Nothing here returns a secret.* The connection check reports who the key pair
authenticates as and where the file lives, never the pair itself.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig
from server.mesh.gmsh_worker import run_on_gmsh_worker

from .adapter import (
    OnshapeAdapter,
    OnshapeAdapterError,
    OnshapePublicDocumentConsent,
    OnshapeTarget,
    send_bundle,
)
from .client import OnshapeClient, OnshapeError, OnshapeHttpError
from .credentials import (
    OnshapeCredentialsError,
    credentials_path,
    file_is_world_readable,
    load_credentials,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cadlink/onshape", tags=["cadlink"])

# A connection check is two API calls; the panel may ask for it on every mount.
CONNECTION_CACHE_TTL_S = 300.0
# Failures are cached far more briefly: long enough to stop a remount loop from
# hammering a failing endpoint, short enough that fixing the key pair or the
# network shows up almost immediately.
CONNECTION_FAILURE_TTL_S = 30.0
BUNDLE_SUBDIRECTORY = Path("cadlink") / "onshape"


class OnshapeStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design: DesignConfig
    identity: SaveIdentity | None = None


class OnshapeSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design: DesignConfig
    design_revision: int = Field(alias="designRevision", ge=0)
    base_name: str = Field(default="waveguide", alias="baseName", max_length=240)
    identity: SaveIdentity | None = None
    # Explicit consent to create a world-readable document, which is the only
    # kind Onshape's Free plan allows. Never defaulted to true.
    allow_public: bool = Field(default=False, alias="allowPublic")
    build_mode: Literal["import", "native"] = Field(
        default="import", alias="buildMode"
    )


class OnshapeUnlinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design_id: str = Field(alias="designId", min_length=1, max_length=120)


def _client(request: Request) -> OnshapeClient:
    try:
        credentials = load_credentials()
    except OnshapeCredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    transport = getattr(request.app.state, "onshape_transport", None)
    return OnshapeClient(credentials, transport=transport)


def _onshape_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnshapePublicDocumentConsent):
        return HTTPException(status_code=428, detail=str(exc))
    if isinstance(exc, OnshapeCredentialsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, OnshapeHttpError):
        if exc.is_auth_failure:
            return HTTPException(status_code=502, detail=str(exc))
        if exc.is_rate_limited:
            return HTTPException(status_code=429, detail=str(exc))
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, OnshapeAdapterError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, OnshapeError):
        return HTTPException(status_code=504, detail=str(exc))
    return exc if isinstance(exc, HTTPException) else HTTPException(
        status_code=500, detail=f"The Onshape send failed: {exc}"
    )


def _link_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "designId": row.get("design_id"),
        "accountId": row.get("account_id"),
        "documentId": row.get("document_id"),
        "workspaceId": row.get("workspace_id"),
        "documentName": row.get("document_name"),
        "documentUrl": _document_url(str(row.get("document_id") or ""), str(row.get("workspace_id") or "")),
        "isPublic": bool(row.get("is_public")),
        "partStudioElementId": row.get("part_studio_element_id"),
        "variableStudioElementId": row.get("variable_studio_element_id"),
        "featureStudioElementId": row.get("feature_studio_element_id"),
        "nativeFeatureId": row.get("native_feature_id"),
        "buildMode": row.get("build_mode") or "import",
        "lastSequence": row.get("last_sequence"),
        "updatedAt": row.get("updated_at"),
    }


def _document_url(document_id: str, workspace_id: str) -> str | None:
    if not document_id:
        return None
    suffix = f"/w/{workspace_id}" if workspace_id else ""
    return f"https://cad.onshape.com/documents/{document_id}{suffix}"


def _credentials_state() -> dict[str, Any]:
    """Report whether a key pair is present, without contacting Onshape."""

    path = credentials_path()
    try:
        load_credentials()
    except OnshapeCredentialsError as exc:
        return {
            "configured": False,
            "credentialsPath": str(path),
            "detail": str(exc),
            "insecureKeyFile": False,
        }
    return {
        "configured": True,
        "credentialsPath": str(path),
        "detail": None,
        "insecureKeyFile": path.is_file() and file_is_world_readable(path),
    }


@router.get("/connection")
async def connection(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Identify the account the key pair authenticates as, and its plan.

    Cached, because this is the only route that spends rate limit on something
    the user did not explicitly ask for.
    """

    state = _credentials_state()
    if not state["configured"]:
        return {**state, "reachable": False, "account": None, "plan": None}

    cached = getattr(request.app.state, "onshape_connection", None)
    now = time.monotonic()
    if (
        not refresh
        and isinstance(cached, dict)
        and now - float(cached.get("checkedAt", 0.0)) < float(cached.get("ttl", CONNECTION_CACHE_TTL_S))
    ):
        return {**state, **cached["payload"]}

    client = _client(request)
    try:
        session = await asyncio.to_thread(client.session_info)
        plan = await asyncio.to_thread(client.plan_summary)
    except OnshapeError as exc:
        # Advisory route: report unreachable rather than failing the panel.
        # A failure is cached too, on a shorter clock. Without this, every mount
        # of the panel retries two API calls against an endpoint already known
        # to be failing -- and a bad key pair answers 401 just as fast as a good
        # one answers 200, so nothing throttles the retries but the user's
        # patience. The short TTL keeps a transient outage recovering quickly.
        failure = {
            "reachable": False,
            "account": None,
            "plan": None,
            "detail": str(exc),
        }
        request.app.state.onshape_connection = {
            "checkedAt": now,
            "payload": failure,
            "ttl": CONNECTION_FAILURE_TTL_S,
        }
        return {**state, **failure}

    account_id = str(session.get("id") or "") or None
    payload = {
        "reachable": True,
        "account": {
            "id": account_id,
            "name": session.get("name"),
        },
        "plan": {
            "name": plan.get("name"),
            "group": plan.get("group"),
            "publicOnly": plan.get("public_only"),
        },
        "rateLimitRemaining": client.last_rate_limit_remaining,
    }
    request.app.state.onshape_connection = {
        "checkedAt": now,
        "payload": payload,
        "ttl": CONNECTION_CACHE_TTL_S,
    }
    if account_id:
        request.app.state.onshape_account_id = account_id
    return {**state, **payload}


@router.post("/status")
async def status(payload: OnshapeStatusRequest, request: Request) -> dict[str, Any]:
    """Decide linked / current / stale for the design on screen. No network."""

    credentials = _credentials_state()
    store: CadLinkStore = request.app.state.cadlink_store
    current_hash = await asyncio.to_thread(design_hash, payload.design)

    row: dict[str, Any] | None = None
    if payload.identity is not None:
        row = await asyncio.to_thread(store.get_onshape_link, payload.identity.design_id)
        if row is None:
            candidates = await asyncio.to_thread(
                store.find_onshape_links_for_lineage, payload.identity.lineage_id
            )
            row = candidates[0] if candidates else None

    if not credentials["configured"]:
        state: str = "not_configured"
    elif row is None:
        state = "not_linked"
    elif str(row.get("last_design_hash") or "") == current_hash:
        state = "current"
    else:
        state = "stale"

    return {
        "state": state,
        "credentials": credentials,
        "link": _link_payload(row),
        "wgChangesAvailable": state == "stale",
        "currentFormula": payload.design.root.formula.lower(),
    }


def _push(
    store: CadLinkStore,
    client: OnshapeClient,
    *,
    bundle_path: str,
    design_id: str,
    document_name: str,
    step_filename: str,
    export_id: str | None,
    sequence: int | None,
    design_hash_value: str,
    geometry_hash_value: str | None,
    allow_public: bool,
    build_mode: Literal["import", "native"] = "import",
) -> dict[str, Any]:
    """Materialise the bundle in Onshape and record where it landed."""

    session = client.session_info()
    account_id = str(session.get("id") or "") or "unknown"
    existing = store.get_onshape_link(design_id, account_id)
    target = (
        OnshapeTarget(
            document_id=str(existing["document_id"]),
            workspace_id=str(existing["workspace_id"]),
            blob_element_id=str(existing["blob_element_id"]),
            part_studio_element_id=existing.get("part_studio_element_id"),
            variable_studio_element_id=existing.get("variable_studio_element_id"),
            feature_studio_element_id=existing.get("feature_studio_element_id"),
            native_feature_id=existing.get("native_feature_id"),
        )
        if existing
        else None
    )
    adapter = OnshapeAdapter(client)
    try:
        result = send_bundle(
            adapter,
            bundle_path,
            document_name=document_name,
            step_filename=step_filename,
            target=target,
            allow_public=allow_public,
            build_mode=build_mode,
        )
    except OnshapeHttpError as exc:
        # The stored document is gone (deleted in Onshape, or the account
        # changed). Forget the link so the next send starts clean rather than
        # failing forever against an id that no longer resolves.
        if exc.status == 404 and target is not None:
            store.delete_onshape_link(design_id, account_id)
            raise OnshapeAdapterError(
                "The linked Onshape document no longer exists, so WG has "
                "unlinked it. Send again to create a new document."
            ) from exc
        raise

    store.save_onshape_link(
        design_id=design_id,
        account_id=account_id,
        document_id=result.target.document_id,
        workspace_id=result.target.workspace_id,
        blob_element_id=result.target.blob_element_id,
        part_studio_element_id=result.target.part_studio_element_id,
        variable_studio_element_id=result.target.variable_studio_element_id,
        feature_studio_element_id=result.target.feature_studio_element_id,
        native_feature_id=result.target.native_feature_id,
        build_mode=result.build_mode,
        document_name=result.document_name,
        is_public=result.is_public,
        last_export_id=export_id,
        last_sequence=sequence,
        last_design_hash=design_hash_value,
        last_geometry_hash=geometry_hash_value,
    )
    return {
        "documentId": result.target.document_id,
        "workspaceId": result.target.workspace_id,
        "documentName": result.document_name,
        "documentUrl": result.document_url,
        "createdDocument": result.created_document,
        "isPublic": result.is_public,
        "variablesPushed": result.variables_pushed,
        "partNames": list(result.part_names),
        "buildMode": result.build_mode,
        "accountId": account_id,
    }


@router.post("/send")
async def send(
    payload: OnshapeSendRequest,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=240, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Export the design as a wglink bundle and materialise it in Onshape."""

    # Imported here rather than at module scope: server.exports.api imports the
    # CAD-link store, and importing it back at module scope would make the two
    # packages import-circular.
    from server.exports.api import (
        WgLinkExportRequest,
        _base_name,
        _export_wglink_sync,
    )

    store: CadLinkStore = request.app.state.cadlink_store
    # Onshape has no local client, so the bundle belongs to WG rather than to a
    # user-selected workspace folder. This keeps a durable, checksummed artifact
    # for every send without making a cloud CAD depend on a local folder.
    bundle_root = Path(request.app.state.data_dir).resolve() / BUNDLE_SUBDIRECTORY
    try:
        bundle_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"WG could not create its Onshape bundle folder at {bundle_root}: {exc}",
        ) from exc

    export_request = WgLinkExportRequest(
        design=payload.design,
        designRevision=payload.design_revision,
        baseName=payload.base_name,
        identity=payload.identity,
    )
    export = await run_on_gmsh_worker(
        _export_wglink_sync,
        export_request,
        store,
        bundle_root,
        idempotency_key,
        "WG's own data folder is unavailable, so the CAD-link bundle could not be written.",
    )

    client = _client(request)
    identity = export.get("identity") or {}
    design_id = str(identity.get("designId") or "")
    if not design_id:
        raise HTTPException(
            status_code=409,
            detail="The export did not commit a design identity, so it cannot be linked to an Onshape document.",
        )
    document_name = _base_name(payload.base_name)
    try:
        pushed = await asyncio.to_thread(
            _push,
            store,
            client,
            bundle_path=str(export["bundlePath"]),
            design_id=design_id,
            document_name=document_name,
            step_filename=f"{document_name}.step",
            export_id=str(export.get("exportId") or "") or None,
            sequence=int(export["sequence"]) if export.get("sequence") is not None else None,
            design_hash_value=str(export.get("designHash") or ""),
            geometry_hash_value=str(export.get("geometryHash") or "") or None,
            allow_public=payload.allow_public,
            build_mode=payload.build_mode,
        )
    except Exception as exc:
        raise _onshape_error(exc) from exc

    return {**export, "onshape": pushed}


@router.post("/unlink")
async def unlink(payload: OnshapeUnlinkRequest, request: Request) -> dict[str, Any]:
    """Forget a link. The Onshape document is left exactly as it is."""

    store: CadLinkStore = request.app.state.cadlink_store
    row = await asyncio.to_thread(store.get_onshape_link, payload.design_id)
    if row is None:
        return {"unlinked": False}
    removed = await asyncio.to_thread(
        store.delete_onshape_link, payload.design_id, str(row.get("account_id") or "")
    )
    return {"unlinked": removed}


def mount_onshape(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["mount_onshape", "router"]
