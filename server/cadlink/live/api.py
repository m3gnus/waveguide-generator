"""``/api/cadlink/live``: endpoint hello, registration, refresh and end of a session.

Check order on every live route (docs/reference/CADLINK-LIVE-PROTOCOL.md,
"Loopback, origin, validation and ordering"): the global Host/Origin guard and
the body size limit (middleware), then here any ``Origin`` header at all
(403 ``origin_not_allowed``), the ``X-WGLink-Installation`` header, the session
token, and only then body validation, then any restart latch a route checks
itself, then the handler.

FastAPI decodes a JSON body before it resolves dependencies, so the checks
before the body cannot be dependencies: a request with a body that is not even
JSON would answer 400 before its Origin or token was looked at. They run in
the route classes below, before FastAPI reads the body. A route that needs a
session is added to :data:`session_router`, whose routes authenticate first
and hand the handler the session through :func:`current_session`.

Session tokens, proofs, nonces and the registration secret are never logged,
never in a digest, a database or a file, and never in any response other than
registration and refresh (the secret in none at all).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import re
from typing import Annotated, Any, Callable, Coroutine, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from server.cadlink import fusion_delivery
from server.cadlink.addin_update import pinned_commit
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.platform.paths import app_root

from . import endpoint as live_endpoint
from . import proof as live_proof
from . import registry as live_registry
from .registry import LiveAuthError, LiveRegistry, LiveSession


LIVE_PREFIX = "/api/cadlink/live"
STAGE = "cadlink-live"
INSTALLATION_HEADER = "X-WGLink-Installation"
#: The add-in's installation id grammar, the same as WG's request ids.
INSTALLATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{1,256})")

logger = logging.getLogger(__name__)


class LiveRefusal(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status,
            content=error_envelope(
                code=self.code, stage=STAGE, message=self.message, retryable=self.retryable
            ),
        )


_AUTH_MESSAGES = {
    live_registry.SESSION_UNKNOWN: "No live CAD Link session has this token; register again.",
    live_registry.TOKEN_EXPIRED: "The live CAD Link session token has expired; register again.",
    live_registry.SESSION_SUPERSEDED: "A newer registration replaced this live CAD Link session.",
    live_registry.INSTALLATION_MISMATCH: "The installation header does not match this live CAD Link session.",
}


def _refuse_origin(request: Request) -> None:
    if "origin" in request.headers:
        raise LiveRefusal(403, "origin_not_allowed", "Live CAD Link routes refuse browser requests.")


def _registry(request: Request) -> LiveRegistry:
    registry = getattr(request.app.state, "live_registry", None)
    if registry is None or live_registry.registry_for(request.app.state.data_dir) is not registry:
        raise LiveRefusal(
            503, "store_busy", "The live CAD Link session service is not running.", retryable=True
        )
    return registry


def _installation(request: Request, *, status: int) -> str:
    value = request.headers.get(INSTALLATION_HEADER)
    if value is None or INSTALLATION_ID.fullmatch(value) is None:
        raise LiveRefusal(
            status,
            live_registry.INSTALLATION_MISMATCH,
            f"Live CAD Link requests carry a valid {INSTALLATION_HEADER} header.",
        )
    return value


def _authenticate(request: Request, registry: LiveRegistry, installation_id: str) -> LiveSession:
    match = _BEARER.fullmatch(request.headers.get("authorization") or "")
    if match is None:
        raise LiveRefusal(401, live_registry.SESSION_UNKNOWN, _AUTH_MESSAGES[live_registry.SESSION_UNKNOWN])
    try:
        session = registry.authenticate(match.group(1), installation_id)
    except LiveAuthError as refused:
        raise LiveRefusal(401, refused.code, _AUTH_MESSAGES[refused.code]) from None
    request.state.live_token_digest = live_registry.token_digest(match.group(1))
    return session


class LiveRoute(APIRoute):
    """A live route that answers no request carrying ``Origin``."""

    #: HTTP status for a missing or malformed installation header; None: not required.
    installation_status: int | None = None
    authenticated = False

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                _refuse_origin(request)
                if self.installation_status is not None:
                    installation_id = _installation(request, status=self.installation_status)
                registry = _registry(request)
                request.state.live_registry = registry
                if self.authenticated:
                    request.state.live_session = _authenticate(request, registry, installation_id)
            except LiveRefusal as refusal:
                return refusal.response()
            return await handler(request)

        return checked


class RegistrationRoute(LiveRoute):
    installation_status = 400


class SessionRoute(LiveRoute):
    installation_status = 401
    authenticated = True


def current_registry(request: Request) -> LiveRegistry:
    return request.state.live_registry


def current_session(request: Request) -> LiveSession:
    return request.state.live_session


#: Live routes answer every refusal with an error envelope, and an invalid
#: request with 400 ``invalid_request`` -- never FastAPI's 422, which the
#: ``4XX`` entry keeps out of the OpenAPI document.
LIVE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "invalid_request (no input echoed) or installation_mismatch"},
    "4XX": {"model": ErrorEnvelope, "description": "Refused; error.code names why (never 422)"},
    503: {"model": ErrorEnvelope, "description": "store_busy: the live session service is not running"},
}

public_router = APIRouter(
    prefix=LIVE_PREFIX, tags=["cadlink-live"], route_class=LiveRoute, responses=LIVE_RESPONSES
)
registration_router = APIRouter(
    prefix=LIVE_PREFIX, tags=["cadlink-live"], route_class=RegistrationRoute, responses=LIVE_RESPONSES
)
session_router = APIRouter(
    prefix=LIVE_PREFIX, tags=["cadlink-live"], route_class=SessionRoute, responses=LIVE_RESPONSES
)


# -- models --------------------------------------------------------------------


class LoadedIdentity(BaseModel):
    """The build WGLink captured when it loaded."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["managed", "devSync", "unmanaged"]
    sourceCommit: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")] | None
    addinVersion: Annotated[StrictStr, Field(max_length=128)] | None
    managedBy: Annotated[StrictStr, Field(max_length=128)] | None
    waveguideGeneratorRoot: Annotated[StrictStr, Field(max_length=4096)] | None
    loadedAt: StrictStr = Field(max_length=64)

    @field_validator("loadedAt")
    @classmethod
    def _iso_8601(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadApplication: Literal["fusion360"]
    liveProtocol: StrictInt
    deliveryVersion: StrictInt
    installationId: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    adapterSessionId: StrictStr = Field(min_length=1, max_length=128)
    adapterVersion: StrictStr = Field(min_length=1, max_length=128)
    clientNonce: StrictStr = Field(max_length=128)
    clientProof: StrictStr = Field(max_length=128)
    loadedIdentity: LoadedIdentity


# -- routes --------------------------------------------------------------------


def _hello(registry: LiveRegistry) -> dict[str, Any]:
    return {
        "schemaVersion": live_endpoint.ENDPOINT_SCHEMA_VERSION,
        "producer": live_endpoint.PRODUCER,
        "instanceId": registry.instance_id,
        "liveProtocol": fusion_delivery.LIVE_PROTOCOL,
        "deliveryVersion": fusion_delivery.DELIVERY_VERSION,
    }


@public_router.get("/endpoint")
async def live_endpoint_hello(registry: LiveRegistry = Depends(current_registry)) -> dict[str, Any]:
    """Which WG start answers here. Never the secret."""

    return _hello(registry)


def _token_fields(session: LiveSession, token: str) -> dict[str, Any]:
    return {
        "liveSessionId": session.live_session_id,
        "sessionToken": token,
        "expiresAt": live_endpoint.utc_timestamp(session.expires_at_wall),
        "refreshAfter": live_endpoint.utc_timestamp(session.refresh_after_wall),
    }


@registration_router.post("/sessions", status_code=201, response_model=None)
async def register_live_session(
    payload: RegistrationRequest,
    request: Request,
    registry: LiveRegistry = Depends(current_registry),
) -> dict[str, Any] | JSONResponse:
    """Register the add-in after it proves it read this start's secret."""

    installation_id = request.headers[INSTALLATION_HEADER]
    if payload.installationId != installation_id:
        return LiveRefusal(
            400, live_registry.INSTALLATION_MISMATCH,
            f"The {INSTALLATION_HEADER} header and installationId differ.",
        ).response()
    if payload.liveProtocol != fusion_delivery.LIVE_PROTOCOL:
        return LiveRefusal(
            409, "protocol_unsupported",
            "This WG serves live protocol 1 only; use file delivery.",
        ).response()
    if payload.deliveryVersion < fusion_delivery.DELIVERY_VERSION:
        return LiveRefusal(
            409, "addin_outdated",
            "This add-in is older than delivery version 3; update WGLink.",
        ).response()
    nonce = live_proof.decode_32(payload.clientNonce)
    if (
        nonce is None
        or not live_proof.verify_client_proof(
            registry.secret, payload.clientNonce, payload.clientProof,
            registry.instance_id, installation_id,
        )
        # Recorded only once the proof verified, so a bad proof burns nothing.
        or not registry.claim_nonce(nonce)
    ):
        return LiveRefusal(
            401, "registration_proof_invalid",
            "The registration proof does not verify; read the endpoint file again.",
        ).response()

    pin = await asyncio.to_thread(pinned_commit, app_root())
    identity = payload.loadedIdentity.model_dump()
    identity["matchesPin"] = identity["sourceCommit"] is not None and identity["sourceCommit"] == pin
    session, token = registry.register(
        installation_id=installation_id,
        adapter_session_id=payload.adapterSessionId,
        adapter_version=payload.adapterVersion,
        loaded_identity=identity,
    )
    logger.info(
        "Live CAD Link session registered (add-in %s, %s build%s).",
        payload.adapterVersion,
        identity["source"],
        "" if identity["matchesPin"] else ", not the pinned commit",
    )
    return {
        **_token_fields(session, token),
        "idleTimeoutSeconds": live_registry.IDLE_TIMEOUT_SECONDS,
        "heartbeatIntervalSeconds": live_registry.HEARTBEAT_INTERVAL_SECONDS,
        "longPollSeconds": live_registry.LONG_POLL_SECONDS,
        "instanceId": registry.instance_id,
        "serverProof": live_proof.server_proof(
            registry.secret, payload.clientNonce, registry.instance_id, installation_id
        ),
        "liveProtocol": fusion_delivery.LIVE_PROTOCOL,
        "capabilities": fusion_delivery.capabilities(),
        "loadedIdentity": dict(identity),
    }


@session_router.post("/sessions/refresh", response_model=None)
async def refresh_live_session(
    request: Request,
    registry: LiveRegistry = Depends(current_registry),
    session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """A new token for the same session; the old one stays valid for 30 s.

    Only the current token refreshes; a token in its grace window is refused.
    """

    try:
        token = registry.refresh(session, request.state.live_token_digest)
    except LiveAuthError as refused:
        return LiveRefusal(401, refused.code, _AUTH_MESSAGES[refused.code]).response()
    return _token_fields(session, token)


@session_router.delete("/sessions/current", status_code=204, response_class=Response)
async def end_live_session(
    registry: LiveRegistry = Depends(current_registry),
    session: LiveSession = Depends(current_session),
) -> Response:
    """End the session at once; the add-in continues with file delivery."""

    registry.end(session)
    logger.info("Live CAD Link session ended by the add-in.")
    return Response(status_code=204)


# -- lifetime ------------------------------------------------------------------


def mount_live(application: FastAPI) -> None:
    """Add the live routes and the per-start registry and endpoint file."""

    application.state.live_registry = None
    for router in (public_router, registration_router, session_router):
        application.include_router(router)

    async def start_live_session() -> None:
        data_dir = Path(application.state.data_dir)
        registry = LiveRegistry.create()
        live_registry.install_registry(data_dir, registry)
        application.state.live_registry = registry
        port = getattr(application.state, "advertised_port", None)
        if port is None:
            return
        document = live_endpoint.endpoint_document(
            instance_id=registry.instance_id,
            pid=os.getpid(),
            port=port,
            started_at=live_endpoint.utc_timestamp(live_registry._wall()),
            secret=registry.secret,
        )
        try:
            await asyncio.to_thread(live_endpoint.write_endpoint, data_dir, document)
        except OSError as exc:
            # Without the file the add-in stays with file delivery.
            logger.warning("Could not publish the live CAD Link endpoint: %s", exc)

    async def stop_live_session() -> None:
        registry = application.state.live_registry
        if registry is None:
            return
        data_dir = Path(application.state.data_dir)
        application.state.live_registry = None
        live_registry.remove_registry_if_ours(data_dir, registry.instance_id)
        if getattr(application.state, "advertised_port", None) is not None:
            await asyncio.to_thread(live_endpoint.remove_endpoint_if_ours, data_dir, registry.instance_id)

    application.router.add_event_handler("startup", start_live_session)
    application.router.add_event_handler("shutdown", stop_live_session)


__all__ = [
    "INSTALLATION_HEADER",
    "LIVE_PREFIX",
    "LiveRefusal",
    "LiveRoute",
    "RegistrationRoute",
    "SessionRoute",
    "current_registry",
    "current_session",
    "mount_live",
    "public_router",
    "registration_router",
    "session_router",
]
