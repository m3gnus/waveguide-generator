"""Stage 4 compatibility: the operation is a ledger; the job owns the solve.

These shims can be removed with the operations UI in Stage 5. Migration is
restartable across the two databases: the job and its key commit first, then
the ledger records acceptance. A retry finds the key even when an older build
hashed its bound SolveRequest rather than the delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar
from collections.abc import Mapping
from typing import Any

from server.jobs.cad_intent import CadSolveIntent, INTERRUPTED_MESSAGE, cad_of
from server.jobs.models import SolveRequest
from .operations import PREPARE_AND_SOLVE, TERMINAL_STATES
from .preparation import (PreparationInput, _publish, _submission_refusal_reason, submission_key,
                          retain_operation_snapshot, record_job_acceptance, RETAINED, RETAIN_TRANSIENT)
from .solve_command import live_held_operation_ids, waiting_claim_operation_ids
from .solver_frame import frame_provenance, record_solved_frame_provenance


logger = logging.getLogger(__name__)


class BadSolvePayload(ValueError):
    """Invalid persisted request data, rather than an implementation failure."""


_fences = weakref.WeakKeyDictionary()
_inside_fence = ContextVar("cad_admission_fences", default=frozenset())


@asynccontextmanager
async def operation_fence(operation_id: str):
    """Serialize admission with Cancel; nested receipt recovery uses the same fence."""
    key = (asyncio.get_running_loop(), operation_id)
    inside = _inside_fence.get()
    # Re-entry is only safe in this task. Do not await admission from a child
    # task (gather/ensure_future or a thread bridge) while holding this fence.
    entry = (key, asyncio.current_task())
    if entry in inside:
        yield
        return
    locks = _fences.setdefault(key[0], {})
    lock = locks.setdefault(operation_id, asyncio.Lock())
    async with lock:
        token = _inside_fence.set(inside | {entry})
        try:
            yield
        finally:
            _inside_fence.reset(token)


def _solve_inputs(row):
    try:
        if not isinstance(row["inputs_json"], str):
            raise ValueError("Request inputs must be JSON text")
        inputs = json.loads(row["inputs_json"])
        if not isinstance(inputs, dict) or not all(isinstance(inputs.get(k), str) and inputs[k]
                                                  for k in ("bundle_path", "manifest_sha256")):
            raise ValueError("Missing bundle path or manifest digest")
        return inputs
    except ValueError as exc:
        raise BadSolvePayload(str(exc)) from exc


def migration_record(store: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the exact legacy preparation, approvals, setup and retained snapshot."""

    record: dict[str, Any] = {"operation_id": row["operation_id"], "last_stage": row.get("stage") or "received"}
    if row.get("frame_axis"):
        record["frame_axis_shown"] = row["frame_axis"]
    if row.get("snapshot_json"):
        record["snapshot"] = json.loads(row["snapshot_json"])
    revision = store.get_setup_revision(str(row["setup_revision_id"])) if row.get("setup_revision_id") else None
    if revision:
        setup = json.loads(revision["setup_json"])
        record["setup"] = dict(revision_id=revision["revision_id"], digest=revision["content_sha256"],
                               origin="wg_defaults" if setup.get("origin") == "wg_defaults" else "user")
    prep = store.get_preparation(str(row["preparation_id"])) if row.get("preparation_id") else None
    if prep:
        record["preparation"] = {
            key: prep[key] for key in ("preparation_id", "ingest_id", "snapshot_sha256", "setup_revision_id",
                                       "report_sha256", "meshing_semantics")
        }
        record["preparation"].update(
            blocking_finding_ids=json.loads(prep["blocking_findings_json"]),
            approvals=json.loads(row.get("approvals_json") or "[]"),
        )
        ingest = store.get_ingest(str(prep["ingest_id"]))
        if ingest:
            ingest_record = json.loads(ingest["record_json"])
            record["frame"] = frame_provenance(
                store, ingest_record,
                automatic=record_solved_frame_provenance(store, ingest_record) == "automatic",
            )
            document = ingest_record.get("document") or {}
            if document.get("return_state_hash"):
                record["return_state_hash"] = document["return_state_hash"]
    return record


async def accept_operation_solve(ctx: Any, operation_id: str, press: PreparationInput | None = None,
                                 *, manual: bool = False, schedule: bool = True, delivery_released: bool = False) -> str | None:
    """Create or recover the job before acknowledging the ledger's Solve."""

    runtime, store = ctx.runtime, ctx.store
    await runtime.start()
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None or row["kind"] != PREPARE_AND_SOLVE or row.get("legacy"):
        return None
    key = submission_key(operation_id)
    job_id = await asyncio.to_thread(runtime.store.job_for_submission_key, key)
    if job_id is None and row["state"] in TERMINAL_STATES:
        return row.get("job_id")
    # Retention may block in a worker. Cancel is free to finish while it runs.
    retention = await asyncio.to_thread(retain_operation_snapshot, store, ctx.data_dir, ctx.workspace_root, operation_id)
    async with operation_fence(operation_id):
        return await _admit_operation_solve(ctx, operation_id, press, manual=manual,
                                           schedule=schedule, retention=retention,
                                           delivery_released=delivery_released)


async def _admit_operation_solve(ctx, operation_id, press, *, manual, schedule, retention, delivery_released):
    runtime, store = ctx.runtime, ctx.store
    # Re-read both stores immediately before admission, under Cancel's fence.
    row = await asyncio.to_thread(store.get_operation, operation_id)
    key = submission_key(operation_id)
    job_id = await asyncio.to_thread(runtime.store.job_for_submission_key, key)
    if row is None or (job_id is None and row["state"] in TERMINAL_STATES):
        return row.get("job_id") if row else None
    cancelled = row["state"] in {"cancel_requested", "cancelled"}
    if retention == RETAIN_TRANSIENT and not cancelled and not delivery_released:
        waiting = live_held_operation_ids() | await asyncio.to_thread(waiting_claim_operation_ids, ctx.data_dir)
        if operation_id in waiting:
            if press is not None:
                raise ValueError("The delivered return is still being retained. Wait for delivery to finish, then press Prepare again with these settings.")
            return None
    inputs = _solve_inputs(row)
    manual = manual or (press is None and inputs["bundle_path"].startswith("ingest/"))
    if job_id is None:
        record = await asyncio.to_thread(migration_record, store, row)
        if manual:
            record["manual_waiting"] = True
        inputs = _solve_inputs(row)
        press = press or PreparationInput(submit=not manual)
        try:
            intent = CadSolveIntent(
                operation_id=operation_id, bundle_path=inputs["bundle_path"],
                manifest_sha256=inputs["manifest_sha256"], return_id=inputs.get("return_id") or "",
                setup_revision_id=press.setup_revision_id or row.get("setup_revision_id"),
                frame_axis=press.expected_frame_axis or row.get("frame_axis"), submit=press.submit,
                approvals=(dict(preparation_id=press.approve_preparation_id, finding_ids=list(press.approve_finding_ids))
                           if press.approve_preparation_id else None),
            )
        except ValueError as exc:
            raise BadSolvePayload(str(exc)) from exc
        if cancelled:
            job_id = await runtime.accept_cad_solve(intent, key, prepare=False, cad_record=record, cancelled=True)
        elif row.get("request_json"):
            # A legacy bind is immutable, including settings changed since it.
            try:
                request = SolveRequest.model_validate_json(row["request_json"])
            except ValueError as exc:
                raise BadSolvePayload(str(exc)) from exc
            request = request.model_copy(update={"client_request_id": key})
            try:
                job_id = await runtime.submit(request, cad_provenance=record)
            except ctx.submission_refusals as exc:
                # The old submit path refused an inadmissible bind too. Make
                # that refusal durable as a job; do not strand the old row or
                # prevent the rest of the startup sweep from migrating.
                code = str(getattr(exc, "reason_code", "") or getattr(exc, "code", "") or "")
                reason = _submission_refusal_reason(exc, code)
                job_id = await runtime.accept_cad_solve(
                    intent, key, prepare=False, cad_record=record,
                    refusal={"code": reason, "message": str(exc)},
                )
        else:
            # Let preparation validate the return and keep its exact stage and remedy.
            refusal = None
            if row["state"] == "processing" and retention == RETAINED:
                # A claim can still carry the previous attempt's refusal fields.
                # It was active when WG stopped, regardless of that stale reason.
                refusal = {"code": "interrupted", "message": INTERRUPTED_MESSAGE}
            elif row["state"] == "needs_user_input" and retention == RETAINED:
                outcome = json.loads(row.get("outcome_json") or "{}")
                refusal = {"code": row.get("reason"), "message": outcome.get("message")}
                # A restart held this solve: the new lane resumes it automatically.
                if row.get("reason") == "update_restart_pending":
                    refusal = None
            job_id = await runtime.accept_cad_solve(intent, key, prepare=not manual and schedule, cad_record=record, refusal=refusal)
    if cancelled:
        # Re-derived on every replay, including a job committed before its receipt.
        job = await asyncio.to_thread(runtime.store.get_job_row, job_id)
        if job["status"] in {"preparing", "queued", "running"}:
            await runtime.stop(job_id)
    await asyncio.to_thread(runtime.store.make_durable)
    if row["state"] not in TERMINAL_STATES:
        await asyncio.to_thread(record_job_acceptance, ctx, operation_id, job_id)
    current = await asyncio.to_thread(store.get_operation, operation_id)
    _publish(ctx, current)
    return job_id


async def sweep_pending_solves(ctx: Any) -> int:
    """Upgrade all pending solves page by page; every committed key is replayable."""

    cursor, changed = 0, 0
    while True:
        page = await asyncio.to_thread(ctx.store.operation_page, kind=PREPARE_AND_SOLVE,
                                      states={"received", "processing", "needs_user_input", "cancel_requested"},
                                      after_rowid=cursor, limit=100)
        if not page:
            return changed
        held = live_held_operation_ids()
        for cursor, row in page:
            if not row.get("legacy") and row["operation_id"] not in held:
                job_id = await accept_operation_solve_isolated(ctx, str(row["operation_id"]))
                if job_id is not None:
                    changed += 1


async def accept_operation_solve_isolated(ctx, operation_id, **kwargs):
    """One bad row never stops a pass; code/storage failures retry with bounded backoff."""
    retries = getattr(ctx.runtime, "_cad_admission_retries", None)
    if retries is None:
        retries = ctx.runtime._cad_admission_retries = {}
    attempts, retry_at = retries.get(operation_id, (0, 0))
    if time.monotonic() < retry_at:
        return None
    try:
        try:
            result = await accept_operation_solve(ctx, operation_id, **kwargs)
        except BadSolvePayload as exc:
            logger.warning("Refused malformed CAD solve %s: %s", operation_id, exc)
            async with operation_fence(operation_id):
                key = submission_key(operation_id)
                result = await asyncio.to_thread(ctx.runtime.store.job_for_submission_key, key)
                row = await asyncio.to_thread(ctx.store.get_operation, operation_id)
                if result is None and row["state"] not in TERMINAL_STATES:
                    intent = CadSolveIntent(operation_id=operation_id, bundle_path="wgreturn/unavailable.wgreturn",
                                            manifest_sha256="sha256:" + "0" * 64, return_id="unavailable")
                    result = await ctx.runtime.accept_cad_solve(
                        intent, key, prepare=False,
                        refusal={"code": "request_payload_invalid", "message": f"Legacy CAD solve could not be migrated: {exc}"},
                        cancelled=row["state"] == "cancel_requested",
                    )
                if result is not None:
                    await asyncio.to_thread(ctx.runtime.store.make_durable)
                    if row["state"] not in TERMINAL_STATES:
                        await asyncio.to_thread(record_job_acceptance, ctx, operation_id, result)
        retries.pop(operation_id, None)
        return result
    except Exception as exc:
        attempts += 1
        retries[operation_id] = (attempts, time.monotonic() + min(60, 2 ** min(attempts, 6)))
        if attempts >= 5:
            try:
                async with operation_fence(operation_id):
                    row = await asyncio.to_thread(ctx.store.note_admission_retry, operation_id, f"{type(exc).__name__}: {exc}")
                    _publish(ctx, row)
            except Exception:
                logger.exception("Could not record admission retry for CAD solve %s", operation_id)
        logger.exception("Could not admit CAD solve %s; retrying on a later pass (backoff at most 60 seconds)", operation_id)
        return None


def operation_detail(ctx: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    from .preparation import operation_summary

    detail = operation_summary(row, ctx.job_store)
    job = ctx.job_store.latest_cad_job(str(row["operation_id"]), row.get("job_id"))
    cad = cad_of(job) if job else {}
    prep = cad.get("preparation")
    detail["approvals"] = list(prep.get("approvals") or []) if isinstance(prep, Mapping) else []
    detail["preparation"] = (
        {"preparationId": prep["preparation_id"], "ingestId": prep.get("ingest_id") or prep["preparation_id"],
         "snapshotSha256": prep.get("snapshot_sha256"), "setupRevisionId": prep.get("setup_revision_id"),
         "reportSha256": prep.get("report_sha256"), "blockingFindingIds": prep.get("blocking_finding_ids") or [],
         "attemptGeneration": 0}
        if isinstance(prep, Mapping) else None
    )
    return detail
