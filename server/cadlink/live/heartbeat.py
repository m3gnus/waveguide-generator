"""``POST /api/cadlink/live/heartbeat``: the file heartbeat, posted over HTTP.

docs/reference/CADLINK-LIVE-PROTOCOL.md, section 6. The body is exactly the
object WGLink writes to ``.fusion-status.json`` and is validated by the same
rules (``fusion_status.heartbeat_problem``). It is kept in memory in this data
directory's live registry, bound to the session that posted it, and never
written to disk. Readers choose between it and the file with
``fusion_status.select_heartbeat``.

The route is on the authenticated router, so Origin, installation header and
token are checked before the body is read. After them:

1. body not a JSON object, or ``schemaVersion``/``cadApplication``/``updatedAt``
   unusable → ``400 invalid_request`` (field and type only, never a value);
2. ``deliveryVersion`` missing or below 3 → ``409 addin_outdated``;
3. ``updatedAt`` already outside the freshness window → ``409 heartbeat_stale``;
4. ``sessionId`` other than the session's ``adapterSessionId`` →
   ``409 session_mismatch``;
5. otherwise ``204``: recorded, replacing the previous live heartbeat -- unless
   that one has a later ``updatedAt``, when the late request is ignored (still
   ``204``: it is not an error, and the newer heartbeat stays).

Nothing is recorded on any refusal. The request itself is activity for the
session's idle timeout, as every authenticated request is.
"""

from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, Response
from fastapi.responses import JSONResponse

from server.cadlink import fusion_delivery
from server.cadlink.fusion_status import HEARTBEAT_INVALID, heartbeat_problem, heartbeat_updated_at
from server.integration.contracts import error_envelope

from . import registry as live_registry
from .api import STAGE, LiveRefusal, current_registry, current_session, session_router
from .registry import LiveRegistry, LiveSession


def _invalid(field: str) -> JSONResponse:
    loc: list[str] = ["body", field] if field else ["body"]
    return JSONResponse(
        status_code=400,
        content=error_envelope(
            code="invalid_request",
            stage=STAGE,
            message="The heartbeat is not valid.",
            details={"errors": [{"loc": loc, "type": "value_error"}]},
        ),
    )


@session_router.post("/heartbeat", status_code=204, response_class=Response)
async def post_live_heartbeat(
    payload: dict[str, Any] = Body(
        description="Exactly the object WGLink writes to .fusion-status.json (heartbeat schema 1)."
    ),
    registry: LiveRegistry = Depends(current_registry),
    session: LiveSession = Depends(current_session),
) -> Response:
    """The add-in's heartbeat for its live session; 204 when recorded."""

    problem = heartbeat_problem(payload)
    if problem is not None and problem[0] == HEARTBEAT_INVALID:
        return _invalid(problem[1])
    delivery = fusion_delivery.addin_delivery_version(payload)
    if delivery is None or delivery < fusion_delivery.DELIVERY_VERSION:
        return LiveRefusal(
            409, "addin_outdated",
            "This add-in is older than delivery version 3; update WGLink.",
        ).response()
    if problem is not None:
        return LiveRefusal(
            409, "heartbeat_stale",
            "The heartbeat's updatedAt is outside the freshness window; send a current one.",
        ).response()
    if payload.get("sessionId") != session.adapter_session_id:
        return LiveRefusal(
            409, "session_mismatch",
            "The heartbeat's sessionId is not the one this live session registered.",
        ).response()
    updated_at = heartbeat_updated_at(payload)
    assert updated_at is not None  # heartbeat_problem checked it
    if registry.record_heartbeat(session, payload, updated_at) is None:
        return LiveRefusal(
            401, live_registry.SESSION_UNKNOWN,
            "No live CAD Link session has this token; register again.",
        ).response()
    return Response(status_code=204)


__all__ = ["post_live_heartbeat"]
