"""``POST /api/cadlink/live/deliveries``: a WG-bound delivery over HTTP.

docs/reference/CADLINK-LIVE-PROTOCOL.md, section 8. The same delivery as a v3
solve file, by another path: one acceptance (``solve_command.accept_delivery``),
one digest, one operation. The bundle travels by reference to the WGLink folder
and is retained into WG's own storage before the delivery is acknowledged.

The route is on the authenticated router, so Origin, installation header and
token are checked before the body is read, then the body is validated
(``400 invalid_request``). After them, in order:

1. an approved update restart → ``409 update_restart_pending`` (retryable);
2. WG's request consumer is not running (``WG2_CAD_DELIVERY=0``, or not
   started) → ``409 delivery_consumer_disabled`` (retryable): nothing would ever
   prepare what this accepted (M1 transfer contract, C3);
3. no WGLink folder selected → ``409 wglink_folder_not_selected`` (retryable);
4. the operation is held against the delivery pass, accepted or recovered,
   and its snapshot retained (a received snapshot is also settled):

   - the id names a different request or kind → ``409 operation_conflict``;
   - the store is locked or busy → ``503 store_busy``, ``Retry-After: 1``
     (any other store error is a 500);
   - the return cannot be read now (for a snapshot: not settled yet) →
     ``503 snapshot_not_readable``, ``Retry-After: 1``, held for 30 s from the
     first such answer; a retry at or after that bound is acknowledged;
   - otherwise ``200 {result: created|recovered, operation}``, whether the
     snapshot was retained or can never be retained as named (preparation
     then refuses it).

Nothing is accepted on any answer before step 4.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sqlite3
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from server.cadlink import preparation, solve_command
from server.cadlink.delivery_status import delivery_status
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.integration.contracts import ErrorEnvelope
from server.updates.restart import UPDATE_RESTART_PENDING

from .api import LiveRefusal, current_session, session_router
from .registry import LiveSession


logger = logging.getLogger(__name__)

_NonEmpty = Annotated[StrictStr, Field(min_length=1)]


class DeliveryRequest(BaseModel):
    """One outbox item. Transport fields (tokens, attempts, claims) are refused."""

    model_config = ConfigDict(extra="forbid")

    operationId: _NonEmpty
    kind: Literal["prepare_and_solve", "receive_snapshot"]
    #: Solve deliveries only, and required for them.
    returnId: StrictStr | None = None
    bundlePath: _NonEmpty
    manifestSha256: _NonEmpty
    #: Transport: never part of the operation's identity.
    requestedAt: _NonEmpty

    @model_validator(mode="after")
    def _return_id_for_solves_only(self) -> DeliveryRequest:
        if self.kind == PREPARE_AND_SOLVE and self.returnId is None:
            raise ValueError("a solve delivery names its returnId")
        if self.kind == RECEIVE_SNAPSHOT and "returnId" in self.model_fields_set:
            raise ValueError("a snapshot delivery names no returnId")
        return self


def _store_busy(exc: sqlite3.OperationalError) -> bool:
    """SQLite's lock contention: retryable. Every other store error is a 500."""

    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _retry_later(refusal: LiveRefusal) -> JSONResponse:
    response = refusal.response()
    response.headers["Retry-After"] = "1"
    return response


@session_router.post(
    "/deliveries",
    response_model=None,
    responses={
        503: {
            "model": ErrorEnvelope,
            "description": "snapshot_not_readable or store_busy: retry after Retry-After",
        }
    },
)
async def post_live_delivery(
    payload: DeliveryRequest,
    request: Request,
    _session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """Accept or recover one delivered operation once its snapshot is retained."""

    # Imported here: ``server.cadlink.api`` mounts this router.
    from server.cadlink.api import _preparation_context, _selected_workspace_root

    state = request.app.state
    restart = getattr(state, "update_restart", None)
    if restart is not None and restart.refusal() is not None:
        return LiveRefusal(
            409, UPDATE_RESTART_PENDING,
            "Waveguide Generator is about to restart for an update; deliver this again after it.",
            retryable=True,
        ).response()
    if not delivery_status(state).running():
        return LiveRefusal(
            409, "delivery_consumer_disabled",
            "Waveguide Generator is not collecting CAD Link requests now; deliver this again once it is.",
            retryable=True,
        ).response()
    workspace_root = await asyncio.to_thread(_selected_workspace_root, state)
    if workspace_root is None:
        return LiveRefusal(
            409, "wglink_folder_not_selected",
            "No WGLink folder is selected in WG, so it cannot read the return; retry once one is.",
            retryable=True,
        ).response()
    ctx = _preparation_context(state, workspace_root=workspace_root)
    item = solve_command.DeliveredItem.from_payload(payload.model_dump())
    data_dir = Path(state.data_dir)

    def retain(operation_id: str) -> str:
        return preparation.settle_snapshot_operation(ctx.store, data_dir, workspace_root, operation_id)

    try:
        delivered = await asyncio.to_thread(solve_command.deliver_live, ctx.store, item, retain=retain)
    except sqlite3.OperationalError as exc:
        if not _store_busy(exc):
            raise
        logger.warning("Live CAD Link delivery %r: the operation store is busy (%s).", item.operation_id, exc)
        return _retry_later(
            LiveRefusal(503, "store_busy", "WG's operation store is busy; retry shortly.", retryable=True)
        )
    answer = delivered.answer
    if delivered.status == solve_command.LIVE_CONFLICT:
        logger.warning(
            "Refused live CAD Link delivery %r: the id already names a different request.",
            item.operation_id,
        )
        return LiveRefusal(
            409, "operation_conflict",
            "This operation id already names a different request; it is left as it is.",
        ).response()
    if delivered.status == solve_command.LIVE_TRANSIENT:
        return _retry_later(
            LiveRefusal(
                503, "snapshot_not_readable",
                "WG cannot read the return in the WGLink folder yet; retry shortly.",
                retryable=True,
            )
        )
    logger.info(
        "Live CAD Link delivery %r (%s): %s.", item.operation_id, item.kind, answer.result
    )
    preparation._publish(ctx, answer.row)
    return {"result": answer.result, "operation": preparation.operation_summary(answer.row)}


__all__ = ["DeliveryRequest", "post_live_delivery"]
