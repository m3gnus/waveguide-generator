"""Fusion-bound requests over HTTP: long poll, claim, progress, completion.

docs/reference/CADLINK-LIVE-PROTOCOL.md, section 7. WG still records every
Fusion-bound operation and publishes its v3 request file; the add-in takes a
request either by renaming that file (the file transport) or through these
routes. The visible file is the mutual-exclusion token, so the first claim
wins whichever transport makes it.

All four routes are on the authenticated router: Origin, installation header
and token are checked before anything else, then the query or body is
validated (``400 invalid_request``). No restart latch applies: a claim starts
no work in WG, and the add-in claims request files during a restart anyway.

- ``GET /requests?waitSeconds=0..25`` answers the offerable requests at once,
  or waits up to ``waitSeconds`` for one. Offerable: a Fusion-bound operation
  in ``received`` whose request file is visible and valid, a return request
  only for the session's ``adapterSessionId``; ordered by ``deliverySequence``
  then operation id. The wait holds no store transaction and no lock: it is
  woken when a request is published and rescans every ``RESCAN_SECONDS``, and
  it ends early with 401 when its session stops authenticating, or 503
  ``store_busy`` when this WG stops.
- ``POST /requests/{operationId}/claim {attemptGeneration, claimId}``, under
  the publisher's lock: a claim already recorded answers first (the same
  ``claimId``, installation and generation replays the original answer from
  the store, even after the file is gone; anything else is
  ``409 already_claimed``). Otherwise the request file is renamed to
  ``.<operationId>.json.live-<claimId>.tmp`` (gone: ``409 claimed_elsewhere``),
  then the store claims the operation at that generation (``adapter-received``,
  the claim recorded), then the hidden file is deleted and
  ``{attemptGeneration: g+1, request}`` answered. A refused store claim
  (``409 stale_attempt``) deletes the file of an operation that finished or was
  dismissed, and puts any other back. Startup recovery restores a hidden file
  an interruption left only while its operation is still ``received``.
- ``POST /requests/{operationId}/progress {attemptGeneration, stage}``:
  ``queuedForFusion`` then ``executing``, one step at a time.
- ``POST /requests/{operationId}/complete {attemptGeneration, outcome,
  message?, evidence?}``: recorded with the heartbeat's outcome mapping
  (``fusion_outcomes.adapter_outcome``).

Progress and completion are fenced on the claiming installation (not the
session: a refreshed token or a new registration of the same installation
continues) and on the attempt generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Annotated, Any, Literal, Mapping

from fastapi import Depends, Query, Request
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from server.cadlink import fusion_delivery
from server.cadlink import store as cadlink_store
from server.cadlink.fusion_outcomes import FUSION_KINDS, adapter_outcome
from server.cadlink.identity import utc_now
from server.cadlink.operations import (
    CANCEL_REQUESTED,
    RECEIVED,
    REQUEST_RETURN,
    STAGE_EXECUTING,
    STAGE_QUEUED_FOR_FUSION,
    TERMINAL_STATES,
)
from server.cadlink.store import CadLinkStore
from server.integration.contracts import error_envelope

from . import registry as live_registry
from . import wake as live_wake
from .api import _AUTH_MESSAGES, STAGE, LiveRefusal, current_registry, current_session, session_router
from .registry import LiveRegistry, LiveSession


logger = logging.getLogger(__name__)

#: How often a waiting long poll reads the store again, whether woken or not.
RESCAN_SECONDS = 2.0
_OPERATION_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_CLAIM_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
#: An operation in one of these never runs again, so its request file is not restored.
_SETTLED_STATES = TERMINAL_STATES | {CANCEL_REQUESTED}
_WIRE_STAGES = {"queuedForFusion": STAGE_QUEUED_FOR_FUSION, "executing": STAGE_EXECUTING}

_Generation = Annotated[StrictInt, Field(ge=0)]
_OperationId = Annotated[str, PathParameter(pattern=_OPERATION_ID)]


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attemptGeneration: _Generation
    #: Chosen and journaled by the add-in before it sends the claim.
    claimId: StrictStr = Field(pattern=_CLAIM_ID)


class ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attemptGeneration: _Generation
    stage: Literal["queuedForFusion", "executing"]


class CompletionEvidence(BaseModel):
    """The link the document carries: this operation and its export."""

    model_config = ConfigDict(extra="forbid")

    operationId: StrictStr = Field(min_length=1, max_length=128)
    exportId: StrictStr = Field(min_length=1, max_length=128)


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attemptGeneration: _Generation
    outcome: Literal[
        "applied", "reconciled", "refused", "superseded", "discarded", "recoveryRequired", "failed"
    ]
    message: StrictStr | None = Field(default=None, min_length=1, max_length=2000)
    evidence: CompletionEvidence | None = None


# -- helpers -------------------------------------------------------------------


def _refusal(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    response = LiveRefusal(status, code, message, retryable=retryable).response()
    if status == 503:
        response.headers["Retry-After"] = "1"
    return response


def _unknown() -> JSONResponse:
    return _refusal(404, "operation_unknown", "No Fusion-bound operation has this id.")


def _store_busy() -> JSONResponse:
    return _refusal(503, "store_busy", "WG's operation store is busy; retry shortly.", retryable=True)


def _busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _invalid(field: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_envelope(
            code="invalid_request",
            stage=STAGE,
            message="This outcome does not apply to this operation, or its evidence does not match it.",
            details={"errors": [{"loc": ["body", field], "type": "value_error"}]},
        ),
    )


def _channel_directory(data_dir: Path, kind: str) -> Path:
    channel = fusion_delivery.RETURN_REQUESTS if kind == REQUEST_RETURN else fusion_delivery.HANDOFFS
    return fusion_delivery.ipc_folder(data_dir) / channel.directory


def _read_request(path: Path, operation_id: str) -> dict[str, Any] | None:
    """The v3 request document at ``path`` if it is this operation's, else None."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != fusion_delivery.SCHEMA_VERSION
        or document.get("operationId") != operation_id
    ):
        return None
    sequence = document.get(fusion_delivery.SEQUENCE_FIELD)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        return None
    return document


def _claim_record(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        value = json.loads(row["claim_json"]) if row.get("claim_json") else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _publish(request: Request, row: Mapping[str, Any] | None) -> None:
    """Tell the UI after the change is committed (the same event as preparation's)."""

    from server.cadlink.preparation import operation_summary

    runtime = getattr(request.app.state, "jobs_runtime", None)
    events = getattr(runtime, "events", None)
    if row is None or events is None:
        return
    try:
        events.publish({"v": 1, "kind": "cadOperation", "operation": operation_summary(row)})
    except Exception:  # noqa: BLE001 - an event is a hint; the store is authoritative
        logger.warning("Could not publish a CAD operation event.", exc_info=True)


# -- long poll -----------------------------------------------------------------


def _offers(store: CadLinkStore, data_dir: Path, session: LiveSession) -> list[dict[str, Any]]:
    """The requests this session may claim now: plain reads, no transaction."""

    found: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for kind in sorted(FUSION_KINDS):
        for row in store.list_operations(kind=kind, states=(RECEIVED,), limit=1000, oldest_first=True):
            operation_id = str(row["operation_id"])
            document = _read_request(_channel_directory(data_dir, kind) / f"{operation_id}.json", operation_id)
            if document is None:
                continue
            if kind == REQUEST_RETURN and document.get("sessionId") != session.adapter_session_id:
                continue
            found.append(
                (
                    (int(document[fusion_delivery.SEQUENCE_FIELD]), operation_id),
                    {
                        "operationId": operation_id,
                        "kind": kind,
                        "attemptGeneration": int(row["attempt_generation"]),
                        "request": document,
                    },
                )
            )
    return [offer for _key, offer in sorted(found, key=lambda item: item[0])]


def _stopped_waiting(
    request: Request, registry: LiveRegistry, session: LiveSession
) -> JSONResponse | None:
    """The answer for a long poll whose WG stopped or whose session ended meanwhile."""

    data_dir = request.app.state.data_dir
    if (
        getattr(request.app.state, "live_registry", None) is not registry
        or live_registry.registry_for(data_dir) is not registry
    ):
        return _refusal(503, "store_busy", "The live CAD Link session service stopped.", retryable=True)
    code = registry.still_current(session, request.state.live_token_digest)
    if code is not None:
        return LiveRefusal(401, code, _AUTH_MESSAGES[code]).response()
    return None


@session_router.get("/requests", response_model=None)
async def poll_fusion_requests(
    request: Request,
    waitSeconds: Annotated[int, Query(ge=0, le=live_registry.LONG_POLL_SECONDS)] = live_registry.LONG_POLL_SECONDS,
    registry: LiveRegistry = Depends(current_registry),
    session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """The Fusion-bound requests this session may claim; waits for one when none."""

    store: CadLinkStore = request.app.state.cadlink_store
    data_dir = Path(request.app.state.data_dir)
    deadline = time.monotonic() + waitSeconds
    # Subscribed before the first read, so a publish in between still wakes it.
    waiter = live_wake.Waiter(data_dir)
    try:
        while True:
            offers = await asyncio.to_thread(_offers, store, data_dir, session)
            # The file scan runs outside the event loop and can be slow on a
            # large or unhealthy IPC directory. Do not hand work to a session
            # that expired, was superseded, or was stopped while that scan ran.
            stopped = _stopped_waiting(request, registry, session)
            if stopped is not None:
                return stopped
            if offers:
                return {"requests": offers}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"requests": []}
            await waiter.wait(min(RESCAN_SECONDS, remaining))
            stopped = _stopped_waiting(request, registry, session)
            if stopped is not None:
                return stopped
    finally:
        waiter.close()


# -- claim ---------------------------------------------------------------------


def _delete_claimed_file(path: Path) -> None:
    """Delete a claimed request's hidden file; startup recovery retries a failure."""

    if fusion_delivery._remove(path) == "failed":
        logger.warning("Left the claimed Fusion request %s for startup recovery.", path.name)


def _restore(hidden: Path, visible: Path, data_dir: Path) -> None:
    try:
        os.replace(hidden, visible)
    except OSError:
        logger.error("Could not restore the Fusion request %s.", visible.name, exc_info=True)
        return
    live_wake.notify(data_dir)


def _claim(
    store: CadLinkStore,
    data_dir: Path,
    session: LiveSession,
    operation_id: str,
    generation: int,
    claim_id: str,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]] | JSONResponse:
    with fusion_delivery._LOCK:
        row = store.get_operation(operation_id)
        if row is None or row["kind"] not in FUSION_KINDS:
            return _unknown()
        recorded = _claim_record(row)
        if recorded is not None:
            if (
                recorded.get("claimId") == claim_id
                and recorded.get("installationId") == session.installation_id
                and recorded.get("attemptGeneration") == generation + 1
            ):
                # A lost answer: the same answer again, from the store.
                return None, {
                    "attemptGeneration": recorded["attemptGeneration"],
                    "request": recorded["request"],
                }
            return _refusal(409, "already_claimed", "This request was already claimed by another claim.")

        directory = _channel_directory(data_dir, str(row["kind"]))
        visible = directory / f"{operation_id}.json"
        if row["kind"] == REQUEST_RETURN:
            document = _read_request(visible, operation_id)
            if document is not None and document.get("sessionId") != session.adapter_session_id:
                return _refusal(
                    409, "session_mismatch",
                    "This return request is for another Fusion session.",
                )
        hidden = directory / f".{operation_id}.json.live-{claim_id}.tmp"
        # 1. Take the file: whoever renames it first has the request.
        try:
            os.rename(visible, hidden)
        except FileNotFoundError:
            return _refusal(409, "claimed_elsewhere", "The request was already taken.")
        except OSError as exc:
            logger.warning("Could not take the Fusion request %s: %s", visible.name, exc)
            return _store_busy()
        document = _read_request(hidden, operation_id)
        if document is None:
            _restore(hidden, visible, data_dir)
            return _refusal(409, "claimed_elsewhere", "The request file is not this operation's request.")
        # 2. Claim the operation at this generation, recording the claim.
        try:
            claimed = store.claim_fusion_request(
                operation_id,
                generation,
                {
                    "installationId": session.installation_id,
                    "liveSessionId": session.live_session_id,
                    "claimId": claim_id,
                    "claimedAt": utc_now(),
                    "request": document,
                },
            )
        except sqlite3.OperationalError as exc:
            _restore(hidden, visible, data_dir)
            if not _busy(exc):
                raise
            logger.warning("Live claim of %r: the operation store is busy (%s).", operation_id, exc)
            return _store_busy()
        except Exception:
            _restore(hidden, visible, data_dir)
            raise
        if claimed is None:
            current = store.get_operation(operation_id)
            if current is None or current["state"] in _SETTLED_STATES:
                # Finished or dismissed meanwhile: never resurrect it.
                _delete_claimed_file(hidden)
            else:
                # Still to run -- received at another generation, or a file
                # claim the add-in released: the file transport keeps it.
                _restore(hidden, visible, data_dir)
            return _refusal(409, "stale_attempt", "The operation is no longer at this attempt generation.")
        # 3. The claim is durable; the file has done its job.
        _delete_claimed_file(hidden)
        return store.get_operation(operation_id), {"attemptGeneration": claimed, "request": document}


@session_router.post("/requests/{operationId}/claim", response_model=None)
async def claim_fusion_request(
    operationId: _OperationId,
    payload: ClaimRequest,
    request: Request,
    session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """Take one offered request; the first claim by either transport wins."""

    result = await asyncio.to_thread(
        _claim,
        request.app.state.cadlink_store,
        Path(request.app.state.data_dir),
        session,
        operationId,
        payload.attemptGeneration,
        payload.claimId,
    )
    if isinstance(result, JSONResponse):
        return result
    row, answer = result
    if row is not None:
        logger.info("Fusion request %s claimed live (attempt %d).", operationId, answer["attemptGeneration"])
        _publish(request, row)
    return answer


# -- progress and completion -----------------------------------------------------


def _fenced(status: str) -> JSONResponse | None:
    if status == cadlink_store.FUSION_UNKNOWN:
        return _unknown()
    if status == cadlink_store.FUSION_CLAIMED_ELSEWHERE:
        return _refusal(
            409, "claimed_elsewhere",
            "This operation was not claimed live by this installation.",
        )
    if status == cadlink_store.FUSION_STALE_ATTEMPT:
        return _refusal(409, "stale_attempt", "This attempt is no longer the operation's current attempt.")
    if status == cadlink_store.FUSION_STAGE_OUT_OF_ORDER:
        return _refusal(
            409, "stage_out_of_order",
            "Progress moves one stage at a time: queuedForFusion, then executing.",
        )
    if status == cadlink_store.FUSION_OUTCOME_CONFLICT:
        return _refusal(409, "outcome_conflict", "A different outcome is already recorded for this operation.")
    return None


def _answer(request: Request, status: str, row: Mapping[str, Any] | None) -> dict[str, Any] | JSONResponse:
    from server.cadlink.preparation import operation_summary

    refused = _fenced(status)
    if refused is not None:
        return refused
    assert row is not None
    if status == cadlink_store.FUSION_ALREADY_RECORDED:
        return {"operation": operation_summary(row), "alreadyRecorded": True}
    _publish(request, row)
    return {"operation": operation_summary(row)}


@session_router.post("/requests/{operationId}/progress", response_model=None)
async def fusion_request_progress(
    operationId: _OperationId,
    payload: ProgressRequest,
    request: Request,
    session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """Record that Fusion queued, then began executing, the claimed request."""

    store: CadLinkStore = request.app.state.cadlink_store
    try:
        status, row = await asyncio.to_thread(
            store.advance_fusion_stage,
            operationId,
            payload.attemptGeneration,
            _WIRE_STAGES[payload.stage],
            session.installation_id,
        )
    except sqlite3.OperationalError as exc:
        if not _busy(exc):
            raise
        return _store_busy()
    return _answer(request, status, row)


@session_router.post("/requests/{operationId}/complete", response_model=None)
async def complete_fusion_request(
    operationId: _OperationId,
    payload: CompletionRequest,
    request: Request,
    session: LiveSession = Depends(current_session),
) -> dict[str, Any] | JSONResponse:
    """Record the claimed request's outcome, mapped as the heartbeat maps it."""

    store: CadLinkStore = request.app.state.cadlink_store
    evidence = (
        (payload.evidence.operationId, payload.evidence.exportId) if payload.evidence is not None else None
    )

    def decide(row: Mapping[str, Any]) -> tuple[str, str | None, Mapping[str, Any] | None]:
        return adapter_outcome(row, payload.outcome, evidence=evidence, message=payload.message)

    try:
        status, row = await asyncio.to_thread(
            store.complete_fusion_request,
            operationId,
            payload.attemptGeneration,
            session.installation_id,
            decide,
        )
    except ValueError:
        return _invalid("evidence" if payload.evidence is not None else "outcome")
    except sqlite3.OperationalError as exc:
        if not _busy(exc):
            raise
        return _store_busy()
    if status == cadlink_store.FUSION_RECORDED and row is not None:
        logger.info(
            "Fusion request %s completed live: %s (%s).", operationId, row["state"], row.get("reason")
        )
    return _answer(request, status, row)


__all__ = [
    "ClaimRequest",
    "CompletionRequest",
    "ProgressRequest",
    "RESCAN_SECONDS",
    "claim_fusion_request",
    "complete_fusion_request",
    "fusion_request_progress",
    "poll_fusion_requests",
]
