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
import json
import logging
from pathlib import Path
import time
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from server.cadlink.identity import SaveIdentity, design_hash
from server.cadlink.ingest import IngestRefusal
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
from .return_leg import (
    OnshapeReturnError,
    source_contract_from_export,
    write_and_ingest_return,
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
    instance_id: str | None = Field(
        default=None, alias="instanceId", min_length=1, max_length=160
    )


class OnshapeSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design: DesignConfig
    design_revision: int = Field(alias="designRevision", ge=0)
    base_name: str = Field(default="waveguide", alias="baseName", max_length=240)
    identity: SaveIdentity | None = None
    instance_id: str | None = Field(
        default=None, alias="instanceId", min_length=1, max_length=160
    )
    # Explicit consent to create a world-readable document, which is the only
    # kind Onshape's Free plan allows. Never defaulted to true.
    allow_public: bool = Field(default=False, alias="allowPublic")
    build_mode: Literal["import", "native"] = Field(
        default="import", alias="buildMode"
    )


class OnshapeUnlinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design_id: str = Field(alias="designId", min_length=1, max_length=120)
    instance_id: str | None = Field(
        default=None, alias="instanceId", min_length=1, max_length=160
    )


class OnshapeReturnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    design_id: str = Field(alias="designId", min_length=1, max_length=120)
    instance_id: str | None = Field(
        default=None, alias="instanceId", min_length=1, max_length=160
    )


class OnshapeInstanceSelectionError(OnshapeAdapterError):
    """An Onshape action did not resolve one exact managed link."""


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
    if isinstance(exc, OnshapeInstanceSelectionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, OnshapeAdapterError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, IngestRefusal):
        return HTTPException(status_code=409 if exc.corruption else 422, detail=str(exc))
    if isinstance(exc, OnshapeReturnError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, OnshapeError):
        return HTTPException(status_code=504, detail=str(exc))
    return exc if isinstance(exc, HTTPException) else HTTPException(
        status_code=500, detail=f"The Onshape send failed: {exc}"
    )


def _link_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "instanceId": row.get("instance_id"),
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
        "datumFeatureStudioElementId": row.get("datum_feature_studio_element_id"),
        "datumFeatureId": row.get("datum_feature_id"),
        "buildMode": row.get("build_mode") or "import",
        "lastSequence": row.get("last_sequence"),
        "updatedAt": row.get("updated_at"),
    }


def _lineage_links(
    store: CadLinkStore,
    design_id: str,
    *,
    lineage_id: str | None = None,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return every local link that can honestly belong to this design."""

    resolved_lineage = lineage_id
    if resolved_lineage is None:
        design = store.get_design(design_id)
        resolved_lineage = str(design.get("lineage_id") or "") if design else ""
    if resolved_lineage:
        return store.find_onshape_links_for_lineage(resolved_lineage, account_id)
    exact = store.get_onshape_link(design_id, account_id)
    return [exact] if exact is not None else []


def _select_link(
    candidates: list[dict[str, Any]], expected_instance_id: str | None
) -> dict[str, Any] | None:
    """Resolve one managed link without using newest/first as identity."""

    if expected_instance_id is not None:
        selected = [
            row
            for row in candidates
            if str(row.get("instance_id") or "") == expected_instance_id
        ]
        if len(selected) != 1:
            raise OnshapeInstanceSelectionError(
                f"The selected Onshape instance {expected_instance_id!r} is stale or "
                "does not belong to this design and account. Refresh and choose the exact link again."
            )
        return selected[0]
    if len(candidates) > 1:
        raise OnshapeInstanceSelectionError(
            "This design lineage has more than one managed Onshape link. "
            "Choose an instance before updating, returning, or unlinking it."
        )
    return candidates[0] if candidates else None


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

    candidates: list[dict[str, Any]] = []
    if payload.identity is not None:
        candidates = await asyncio.to_thread(
            _lineage_links,
            store,
            payload.identity.design_id,
            lineage_id=payload.identity.lineage_id,
        )

    selection_required = False
    try:
        row = _select_link(candidates, payload.instance_id)
    except OnshapeInstanceSelectionError:
        row = None
        selection_required = True

    if not credentials["configured"]:
        state: str = "not_configured"
    elif selection_required:
        state = "instance_selection_required"
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
        "matchingLinks": [_link_payload(candidate) for candidate in candidates],
        "selectedInstanceId": row.get("instance_id") if row is not None else None,
        "wgChangesAvailable": state == "stale",
        "currentFormula": payload.design.root.formula.lower(),
    }


def _push(
    store: CadLinkStore,
    client: OnshapeClient,
    *,
    bundle_path: str,
    design_id: str,
    lineage_id: str | None = None,
    expected_instance_id: str | None = None,
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
    candidates = _lineage_links(
        store, design_id, lineage_id=lineage_id, account_id=account_id
    )
    existing = _select_link(candidates, expected_instance_id)
    target = (
        OnshapeTarget(
            document_id=str(existing["document_id"]),
            workspace_id=str(existing["workspace_id"]),
            blob_element_id=str(existing["blob_element_id"]),
            part_studio_element_id=existing.get("part_studio_element_id"),
            variable_studio_element_id=existing.get("variable_studio_element_id"),
            feature_studio_element_id=existing.get("feature_studio_element_id"),
            native_feature_id=existing.get("native_feature_id"),
            datum_feature_studio_element_id=existing.get("datum_feature_studio_element_id"),
            datum_feature_id=existing.get("datum_feature_id"),
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
            store.delete_onshape_link(str(existing["design_id"]), account_id)
            raise OnshapeAdapterError(
                "The linked Onshape document no longer exists, so WG has "
                "unlinked it. Send again to create a new document."
            ) from exc
        raise

    stored = store.save_onshape_link(
        # A conflicting save may fork design_id while retaining lineage. The
        # selected link continues to own its original registry row; its latest
        # immutable export carries the new design id. Moving the row would
        # collapse two explicitly selectable lineage links into one PK.
        design_id=str(existing["design_id"]) if existing is not None else design_id,
        account_id=account_id,
        instance_id=(
            str(existing["instance_id"]) if existing is not None else None
        ),
        document_id=result.target.document_id,
        workspace_id=result.target.workspace_id,
        blob_element_id=result.target.blob_element_id,
        part_studio_element_id=result.target.part_studio_element_id,
        variable_studio_element_id=result.target.variable_studio_element_id,
        feature_studio_element_id=result.target.feature_studio_element_id,
        native_feature_id=result.target.native_feature_id,
        datum_feature_studio_element_id=result.target.datum_feature_studio_element_id,
        datum_feature_id=result.target.datum_feature_id,
        build_mode=result.build_mode,
        document_name=result.document_name,
        is_public=result.is_public,
        last_export_id=export_id,
        last_sequence=sequence,
        last_design_hash=design_hash_value,
        last_geometry_hash=geometry_hash_value,
    )
    return {
        "instanceId": stored["instance_id"],
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
    lineage_id = str(identity.get("lineageId") or "")
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
            lineage_id=lineage_id or None,
            expected_instance_id=payload.instance_id,
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
    candidates = await asyncio.to_thread(
        _lineage_links, store, payload.design_id
    )
    try:
        row = _select_link(candidates, payload.instance_id)
    except Exception as exc:
        raise _onshape_error(exc) from exc
    if row is None:
        return {"unlinked": False}
    removed = await asyncio.to_thread(
        store.delete_onshape_link,
        str(row.get("design_id") or ""),
        str(row.get("account_id") or ""),
    )
    return {"unlinked": removed}


@router.post("/return")
async def return_to_wg(
    payload: OnshapeReturnRequest, request: Request
) -> dict[str, Any]:
    """Export the linked Part Studio, create wgreturn 1.1, and ingest it."""

    store: CadLinkStore = request.app.state.cadlink_store
    client = _client(request)
    try:
        # This is an explicit user action, not status polling. Resolve the
        # authenticated account so a key switch cannot export another link's
        # document by accident.
        session = await asyncio.to_thread(client.session_info)
        account_id = str(session.get("id") or "")
        if not account_id:
            raise OnshapeAdapterError(
                "Onshape did not identify the authenticated account."
            )
        candidates = await asyncio.to_thread(
            _lineage_links,
            store,
            payload.design_id,
            account_id=account_id,
        )
        link = _select_link(candidates, payload.instance_id)
        if link is None:
            raise OnshapeAdapterError(
                "This design is not linked to an Onshape document for the authenticated account."
            )
        part_studio_id = str(link.get("part_studio_element_id") or "")
        if not part_studio_id:
            raise OnshapeReturnError(
                "The stored Onshape link has no Part Studio identity. Send the design to Onshape again."
            )
        export_id = str(link.get("last_export_id") or "")
        export_row = await asyncio.to_thread(store.get_export, export_id) if export_id else None
        if export_row is None:
            raise OnshapeReturnError(
                "The exact outbound bundle that created this Onshape link is no longer in the CAD registry. "
                "WG will not invent its source or identity evidence; send the design to Onshape again."
            )
        try:
            outbound_manifest = json.loads(str(export_row["manifest_json"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise OnshapeReturnError(
                "The linked export's stored manifest is unreadable."
            ) from exc
        if not isinstance(outbound_manifest, dict):
            raise OnshapeReturnError(
                "The linked export's stored manifest is not an object."
            )
        # Refuse missing WG-authored source policy before creating a paid,
        # rate-limited translation job. This check is registry-only.
        source_contract_from_export(outbound_manifest)
        adapter = OnshapeAdapter(client)
        translation_id = await asyncio.to_thread(
            adapter.create_step_export,
            str(link["document_id"]),
            str(link["workspace_id"]),
            part_studio_id,
        )
        _translation, foreign_id = await asyncio.to_thread(
            adapter.await_step_export, translation_id
        )
        step_bytes = await asyncio.to_thread(
            adapter.download_external_data, str(link["document_id"]), foreign_id
        )
        bundle_path, record = await run_on_gmsh_worker(
            write_and_ingest_return,
            step_bytes,
            link=link,
            export_row=export_row,
            store=store,
            data_dir=Path(request.app.state.data_dir),
        )
    except Exception as exc:
        raise _onshape_error(exc) from exc
    return {
        "translationId": translation_id,
        "bundle": {
            "name": bundle_path.name,
            "bundlePath": str(bundle_path),
            "documentName": link.get("document_name"),
            "sourceCount": 1,
            "instanceCount": 1,
            "instanceId": link.get("instance_id"),
        },
        "ingest": record,
    }


def mount_onshape(application: FastAPI) -> None:
    application.include_router(router)


__all__ = ["mount_onshape", "router"]
