"""Stage 4 compatibility: the operation is a ledger; the job owns the solve.

These shims can be removed with the operations UI in Stage 5. Migration is
restartable across the two databases: the job and its key commit first, then
the ledger records acceptance. A retry finds the key even when an older build
hashed its bound SolveRequest rather than the delivery.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from server.jobs.cad_intent import CadSolveIntent, INTERRUPTED_MESSAGE, cad_of
from server.jobs.models import SolveRequest
from .operations import PREPARE_AND_SOLVE, TERMINAL_STATES
from .preparation import PreparationInput, _publish, _submission_refusal_reason, submission_key
from .solve_command import record_outcome, live_held_operation_ids
from .solver_frame import frame_provenance, record_solved_frame_provenance


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
                                 *, manual: bool = False, schedule: bool = True) -> str | None:
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
    if job_id is None:
        record = await asyncio.to_thread(migration_record, store, row)
        if manual:
            record["manual_waiting"] = True
        inputs = json.loads(row["inputs_json"])
        press = press or PreparationInput(submit=not manual)
        intent = CadSolveIntent(
            operation_id=operation_id, bundle_path=inputs["bundle_path"],
            manifest_sha256=inputs["manifest_sha256"], return_id=inputs.get("return_id") or "",
            setup_revision_id=press.setup_revision_id or row.get("setup_revision_id"),
            frame_axis=press.expected_frame_axis or row.get("frame_axis"), submit=press.submit,
            approvals=(dict(preparation_id=press.approve_preparation_id, finding_ids=list(press.approve_finding_ids))
                       if press.approve_preparation_id else None),
        )
        if row.get("request_json"):
            # A legacy bind is immutable, including settings changed since it.
            request = SolveRequest.model_validate_json(row["request_json"])
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
                    refusal=({"code": reason, "message": str(exc)}
                             if row["state"] != "cancel_requested" else None),
                )
                if row["state"] == "cancel_requested":
                    await runtime.stop(job_id)
        else:
            refusal = None
            if row["state"] == "processing":
                # A claim can still carry the previous attempt's refusal fields.
                # It was active when WG stopped, regardless of that stale reason.
                refusal = {"code": "interrupted", "message": INTERRUPTED_MESSAGE}
            elif row["state"] == "needs_user_input":
                outcome = json.loads(row.get("outcome_json") or "{}")
                refusal = {"code": row.get("reason"), "message": outcome.get("message")}
                # A restart held this solve: the new lane resumes it automatically.
                if row.get("reason") == "update_restart_pending":
                    refusal = None
            job_id = await runtime.accept_cad_solve(intent, key, prepare=not manual and schedule, cad_record=record, refusal=refusal)
            if row["state"] == "cancel_requested":
                await runtime.stop(job_id)
    await asyncio.to_thread(runtime.store.make_durable)
    if row["state"] not in TERMINAL_STATES:
        await asyncio.to_thread(record_outcome, store, operation_id, state="accepted", job_id=job_id)
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
                await accept_operation_solve(ctx, str(row["operation_id"]))
                changed += 1


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
