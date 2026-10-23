"""CAD operation acceptance ledger, snapshot retention and delivery reporting.

CAD solves are prepared and executed by the jobs runtime. This module retains
file deliveries and joins their ledger rows to the jobs that own their lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from server.cadlink.intake.ingest import (
    retain_snapshot,
)
from server.cadlink.operations import (
    ACCEPTED,
    PREPARE_AND_SOLVE,
    PROCESSING,
    RECEIVE_SNAPSHOT,
    RECEIVED,
    REJECTED,
    TERMINAL_STATES,
)
from server.cadlink.intake.solve_command import (
    CAD_SOLVE_SUBMISSION_PREFIX,
    RETAIN_INVALID,
    RETAIN_TRANSIENT,
    RETAINED,
    SolveOutcomeConflict,
    record_outcome,
)
from server.cadlink.store import CadLinkStore

from server.cadlink.joblane.preparation_inputs import (
    SnapshotContext,
    SnapshotUnavailable,
    _inputs,
    exchange_bundle_path,
    _snapshot_record,
    _retained,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PreparationInput:
    """What the user asked of one preparation.

    ``approve_preparation_id`` and ``approve_finding_ids`` are blocking
    findings the user reviewed on that preparation. They are approved only
    when this attempt resumes that same preparation, and never carry to a new
    one.
    """

    setup_revision_id: str | None = None
    submit: bool = True
    approve_preparation_id: str | None = None
    approve_finding_ids: tuple[str, ...] = ()
    #: The solver frame axis the user saw when they pressed Solve. An unlinked
    #: snapshot is solved only along it: a confirmation changed elsewhere in
    #: the meantime stops at the frame gate instead of changing the axis.
    expected_frame_axis: str | None = None


@dataclass
class PreparationContext(SnapshotContext):
    """What a preparation needs from the application, injectable for tests."""

    runtime: Any
    #: The job a submission key created, if any (``JobStore.job_for_submission_key``).
    job_for_submission: Callable[[str], str | None] | None = None
    #: Called with an operation's summary after each committed change.
    publish: Callable[[Mapping[str, Any]], None] | None = None
    #: The jobs system's own refusals of a request: a capability it lacks, a
    #: request it cannot accept. Migration records these as refused jobs.
    submission_refusals: tuple[type[BaseException], ...] = ()
    #: Why no CAD preparation may start and no solve may be submitted now:
    #: an approved update restart (UPDATE-TRANSACTION-CONTRACT.md 4.2).
    submission_blocked: Callable[[], str | None] | None = None
    #: Called with each refusal of a taken inbox file that has no operation
    #: row of its own (``solve_command.inbox_refusal``).
    refuse: Callable[[Mapping[str, Any]], None] | None = None
    job_store: Any = None

    def __post_init__(self) -> None:
        if self.runtime is None:
            raise RuntimeError("CAD preparation requires the jobs runtime")


#: Why a delivery pass collected nothing. The CAD Link folder is the exchange
#: itself, so without one there is no command to read -- which is a stopped
#: consumer, not an idle one, and the user is the only one who can end it.
NO_WORKSPACE_REASON = (
    "No CAD Link folder is selected, so Waveguide Generator cannot collect the "
    "solve commands Fusion sends. Choose the folder in CAD Link settings."
)


class DeliveryPassReporter:
    """What to say about a delivery pass, given what the last one said.

    The loop runs about once a second, so a stopped consumer must be reported
    once rather than 86 000 times a day, and its resumption once. ``observe``
    takes the reason a pass started nothing (``None`` when it ran normally)
    and returns the line to log, or ``None`` for say nothing.

    Resumption is reported only after a stop, and that falls out of the
    equality guard rather than needing its own branch: reaching the resume line
    at all means the last pass carried a reason.
    """

    RESUMED = "Collecting CAD solve commands again."

    def __init__(self) -> None:
        self._reason: str | None = None

    def observe(self, reason: str | None) -> str | None:
        if reason == self._reason:
            return None
        self._reason = reason
        return reason if reason is not None else self.RESUMED


def submission_key(operation_id: str) -> str:
    return f"{CAD_SOLVE_SUBMISSION_PREFIX}{operation_id}"


def operation_summary(row: Mapping[str, Any], job_store: Any = None) -> dict[str, Any]:
    """An operation as the UI and the events channel see it."""

    outcome = json.loads(row["outcome_json"]) if row.get("outcome_json") else {}
    snapshot = json.loads(row["snapshot_json"]) if row.get("snapshot_json") else None
    state = str(row["state"])
    stage = row.get("stage")
    if stage is None:
        # Written before stages existed, or by an older build.
        stage = "submitted" if row.get("job_id") else ("received" if state == RECEIVED else None)
    summary = {
        "operationId": row["operation_id"],
        "acceptedSeq": int(row["accepted_seq"]) if row.get("accepted_seq") is not None else None,
        "kind": row["kind"],
        "state": state,
        "stage": stage,
        "reason": row.get("reason"),
        "message": outcome.get("message") if isinstance(outcome, Mapping) else None,
        "blocker": outcome.get("blocker"),
        "remedies": (outcome.get("blocker") or {}).get("remedies", []),
        # Solved with WG's default settings: ``message`` says so in words.
        "setupDefaults": bool(isinstance(outcome, Mapping) and outcome.get("setup_defaults")),
        # The solver axis WG chose itself, confident and unconfirmed (the
        # solve was never shown one); null when the axis was confirmed.
        "frameAxisAutomatic": (
            outcome.get("frame_axis_automatic") if isinstance(outcome, Mapping) else None
        ),
        "jobId": row.get("job_id"),
        "attemptGeneration": int(row.get("attempt_generation") or 0),
        "setupRevisionId": row.get("setup_revision_id"),
        "preparationId": row.get("preparation_id"),
        "snapshot": (
            {
                "manifestSha256": snapshot.get("manifest_sha256"),
                "documentName": snapshot.get("document_name"),
                "projectLineageId": snapshot.get("project_lineage_id"),
                # A Send names where its return sits in the WGLink folder, so
                # the page can open it once accepted (M1 transfer contract,
                # C4). Only a Send: a manual solve's input is an ingestion.
                **(
                    {"bundlePath": _inputs(row).get("bundle_path")}
                    if row["kind"] == RECEIVE_SNAPSHOT
                    else {}
                ),
            }
            if isinstance(snapshot, Mapping)
            else None
        ),
        "legacy": bool(row.get("legacy")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
    if job_store is not None and row["kind"] == PREPARE_AND_SOLVE:
        from server.jobs.cad_preparation import job_operation_view
        job = job_store.latest_cad_job(str(row["operation_id"]), row.get("job_id"))
        if job is not None and (job.get("task_metadata") or {}).get("cad"):
            view = job_operation_view(job)
            # Pre-cutover bound jobs may have no retained-snapshot provenance.
            if view.get("snapshot") is None:
                view.pop("snapshot", None)
            config = job.get("config_json")
            if isinstance(config, Mapping) and config.get("type") != "cad_intent":
                # Historical bound jobs may have only an operation identity.
                # Keep ledger inputs where that job recorded no value.
                for key in ("preparationId", "setupRevisionId"):
                    if view.get(key) is None:
                        view.pop(key, None)
                if isinstance(view.get("snapshot"), Mapping) and isinstance(summary.get("snapshot"), Mapping):
                    view["snapshot"] = {**summary["snapshot"],
                                        **{key: value for key, value in view["snapshot"].items() if value is not None}}
            summary.update(view)
            summary["updatedAt"] = job.get("updated_at") or summary["updatedAt"]
        elif job is None and row.get("job_id"):
            # A deleted job is a durable dismissal, including after reconnect.
            summary.update(state="cancelled", stage=None, reason=None, message=None, jobId=None)
    if not summary["jobId"] and state not in TERMINAL_STATES and outcome.get("admission_retrying"):
        summary.update(reason="admission_retrying", message=outcome["admission_retrying"])
    return summary


def _publish(ctx: PreparationContext, row: Mapping[str, Any] | None) -> None:
    if row is None or ctx.publish is None:
        return
    try:
        ctx.publish(operation_summary(row, ctx.job_store))
    except Exception:  # noqa: BLE001 - a notification never fails the work
        logger.debug("Could not publish a CAD operation update.", exc_info=True)


def retain_operation_snapshot(
    store: CadLinkStore, data_dir: Path, workspace_root: Path | None, operation_id: str
) -> str:
    """Receive-time retention: keep the snapshot an unfinished operation names.

    A solve (``prepare_and_solve``) or a received snapshot
    (``receive_snapshot``); ``settle_snapshot_operation`` then settles the
    latter.

    Called before the delivery is acknowledged, and says whether it may be:

    - ``RETAINED``: WG holds the snapshot, or there is nothing for it to hold
      (the operation is unknown, finished, or of another kind).
    - ``RETAIN_INVALID``: the return can never be retained as the command
      names it -- malformed, changed since the command, outside the WGLink
      folder. The delivery is acknowledged, and preparation refuses it.
    - ``RETAIN_TRANSIENT``: the return cannot be read now -- not in the
      WGLink folder yet, a file another process holds, a drive that is not
      mounted, a store that is busy. The delivery waits for another pass.

    Nothing is raised: a return the parser cannot take must never hold up the
    deliveries behind it. An unexpected failure is logged and counts as
    invalid, so its delivery is acknowledged, as before.
    """

    try:
        row = store.get_operation(operation_id)
        if (
            row is None
            or row["kind"] not in _RETAINED_KINDS
            or row["state"] in TERMINAL_STATES
        ):
            return RETAINED
        if _retained(data_dir, row) is not None:
            return RETAINED
        inputs = _inputs(row)
        retained = retain_snapshot(
            exchange_bundle_path(workspace_root, str(inputs.get("bundle_path") or "")),
            data_dir,
            expected_manifest_sha256=str(inputs.get("manifest_sha256") or "") or None,
        )
        store.record_snapshot(operation_id, _snapshot_record(store, retained))
    except (OSError, sqlite3.Error) as exc:
        # SnapshotUnavailable is an OSError: cannot proceed now, not never.
        logger.debug("Could not retain the snapshot of CAD operation %s yet: %s", operation_id, exc)
        return RETAIN_TRANSIENT
    except ValueError as exc:
        # WgReturnError, a changed return, a path outside the WGLink folder.
        logger.info("The snapshot of CAD operation %s cannot be retained: %s", operation_id, exc)
        return RETAIN_INVALID
    except Exception as exc:  # noqa: BLE001 - retention at receive never holds up a delivery
        logger.warning("Could not retain the snapshot of CAD operation %s: %s", operation_id, exc)
        return RETAIN_INVALID
    return RETAINED


_RETAINED_KINDS = frozenset({PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT})
#: A received snapshot is settled from either state: ``processing`` is one a
#: settler claimed and never recorded (WG stopped, or the store refused the
#: outcome), which the next settler takes over.
SETTLEABLE_SNAPSHOT_STATES = frozenset({RECEIVED, PROCESSING})
#: How long a received snapshot's bundle may stay unreadable before the
#: operation is rejected (``snapshot_unavailable``). Measured from the first
#: unreadable attempt, recorded durably, so a restart does not reset it.
SNAPSHOT_UNAVAILABLE_BOUND = timedelta(hours=24)
SNAPSHOT_ACCEPTED_MESSAGE = "WG verified and kept this snapshot."
SNAPSHOT_INVALID_MESSAGE = (
    "WG could not verify this snapshot as Fusion named it: it is malformed, changed since "
    "it was sent, or outside the WGLink folder. Send it again from Fusion."
)
SNAPSHOT_UNAVAILABLE_MESSAGE = (
    "WG could not read this snapshot in the WGLink folder for 24 hours. Send it again from "
    "Fusion."
)
_SNAPSHOT_PAGE = 100


def _wall_now() -> datetime:
    return datetime.now(timezone.utc)


def _unreadable_too_long(store: CadLinkStore, row: Mapping[str, Any], now: datetime) -> bool:
    """Record the first unreadable time if new; whether the bound has passed."""

    operation_id = str(row["operation_id"])
    since = row.get("snapshot_unreadable_since")
    if not since:
        since = store.note_snapshot_unreadable(
            operation_id, now.astimezone(timezone.utc).isoformat(timespec="seconds")
        )
    if not since:
        return False
    try:
        first = datetime.fromisoformat(str(since))
    except ValueError:
        return False
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    return now - first >= SNAPSHOT_UNAVAILABLE_BOUND


def settle_snapshot_operation(
    store: CadLinkStore, data_dir: Path, workspace_root: Path | None, operation_id: str
) -> str:
    """Retain a received snapshot and record what that found (CAD-OPERATIONS.md).

    For a ``receive_snapshot`` in ``received`` or ``processing``:

    - retained, and WG holds the copy -> claimed -> ``accepted``;
    - never retainable as named -> claimed -> ``rejected`` / ``snapshot_invalid``;
    - not readable now -> no claim and no outcome, unless it has been unreadable
      for ``SNAPSHOT_UNAVAILABLE_BOUND``: then ``rejected`` /
      ``snapshot_unavailable``.

    The claim moves the generation on, so a settler that claimed earlier and
    never recorded is taken over, and its late outcome is refused. Nothing is
    ingested, meshed or solved, and no intent is recorded. Returns
    ``RETAINED`` once accepted, ``RETAIN_INVALID`` once rejected, and
    ``RETAIN_TRANSIENT`` while it is still unsettled, whatever the reason;
    another kind is only retained (``retain_operation_snapshot``). A store
    error propagates.
    """

    retention = retain_operation_snapshot(store, data_dir, workspace_root, operation_id)
    row = store.get_operation(operation_id)
    if row is None or row["kind"] != RECEIVE_SNAPSHOT:
        return retention
    if row["state"] not in SETTLEABLE_SNAPSHOT_STATES:
        return RETAINED if row["state"] == ACCEPTED else RETAIN_INVALID
    if retention == RETAINED and _retained(data_dir, row) is None:
        # Nothing is accepted before WG holds a copy that is the return it
        # names: what is established here is the copy's *identity*, which is
        # all this path needs, because nothing here ingests, meshes or solves.
        # A member damaged inside an otherwise intact copy is caught by
        # ``_copy_is_whole`` before preparation meshes from it.
        retention = RETAIN_TRANSIENT
    if retention == RETAINED:
        state, reason, message = ACCEPTED, None, SNAPSHOT_ACCEPTED_MESSAGE
    elif retention != RETAIN_TRANSIENT:
        state, reason, message = REJECTED, "snapshot_invalid", SNAPSHOT_INVALID_MESSAGE
    elif _unreadable_too_long(store, row, _wall_now()):
        state, reason, message = REJECTED, "snapshot_unavailable", SNAPSHOT_UNAVAILABLE_MESSAGE
    else:
        return RETAIN_TRANSIENT
    held_at = int(row["attempt_generation"])
    generation = store.claim(operation_id, held_at)
    if generation is None:
        return RETAIN_TRANSIENT
    if row["state"] == PROCESSING:
        logger.info(
            "Received snapshot %s: attempt %d took it over from attempt %d, which never "
            "recorded an outcome.",
            operation_id, generation, held_at,
        )
    recorded = store.record_outcome(
        operation_id, generation, state, reason=reason, outcome={"message": message}
    )
    if recorded is None:
        return RETAIN_TRANSIENT
    log = logger.warning if reason == "snapshot_unavailable" else logger.info
    log("Received snapshot %s: %s%s.", operation_id, state, f" ({reason})" if reason else "")
    return RETAINED if state == ACCEPTED else RETAIN_INVALID


def settle_received_snapshots(ctx: PreparationContext) -> list[str]:
    """Settle file snapshots left received or processing after a crash.

    Every unsettled row is reached, however many bundles remain unreadable.
    A store error on one row is logged and the rest go on.
    """

    if ctx.workspace_root is None:
        return []
    settled: list[str] = []
    cursor = 0
    while True:
        page = ctx.store.operation_page(
            kind=RECEIVE_SNAPSHOT, states=SETTLEABLE_SNAPSHOT_STATES,
            after_rowid=cursor, limit=_SNAPSHOT_PAGE,
        )
        if not page:
            break
        for cursor, row in page:
            operation_id = str(row["operation_id"])
            try:
                settle_snapshot_operation(ctx.store, ctx.data_dir, ctx.workspace_root, operation_id)
                current = ctx.store.get_operation(operation_id)
            except sqlite3.Error as exc:
                logger.warning("Could not settle the received snapshot %s: %s", operation_id, exc)
                continue
            if current is not None and current["state"] not in SETTLEABLE_SNAPSHOT_STATES:
                settled.append(operation_id)
                _publish(ctx, current)
        if len(page) < _SNAPSHOT_PAGE:
            break
    return settled


def record_job_acceptance(ctx: PreparationContext, operation_id: str, job_id: str) -> dict[str, Any]:
    """Join acceptance to the job, keeping the provenance it already recorded."""

    from server.jobs.cad_intent import acceptance_details

    job = ctx.job_store.get_job_row(job_id) if ctx.job_store is not None else None
    return record_outcome(ctx.store, operation_id, state="accepted", job_id=job_id,
                          **acceptance_details(job or {}))


def reconcile_with_jobs(ctx: PreparationContext, operation_id: str) -> dict[str, Any] | None:
    """Record ``accepted`` when the operation's submission key already made a job.

    The job is the outcome: an earlier attempt created it, or the browser of a
    build before the backend owned solves did, and its acknowledgement never
    arrived. Returns the row when it did so.
    """

    if ctx.job_for_submission is None:
        return None
    job_id = ctx.job_for_submission(submission_key(operation_id))
    if not job_id:
        return None
    logger.info("CAD operation %s: its submission key already made job %s.", operation_id, job_id)
    try:
        record_job_acceptance(ctx, operation_id, job_id)
    except SolveOutcomeConflict:
        pass
    return ctx.store.get_operation(operation_id)


DISMISSAL_UNCONFIRMED = (
    "WG cannot confirm yet whether a solve job was already started for this request, "
    "because the jobs list cannot be read right now. Nothing was dismissed. Try again "
    "in a moment."
)


class DismissalUnconfirmed(RuntimeError):
    """A job may exist for the operation, and the jobs store cannot say whether it does."""


def dismiss_operation(ctx: PreparationContext, operation_id: str) -> dict[str, Any] | None:
    """Dismiss an operation, after reconciling it with the jobs store.

    A solve whose submission key already made a job follows the job: it is
    ``accepted`` with it, and the user cancels the job in the jobs list. A
    solve whose request is bound -- a job may exist -- is not dismissed while
    the jobs store cannot be read (:class:`DismissalUnconfirmed`), whether it
    is idle or an attempt is submitting it: a dismissal turns the attempt's
    "interrupted" into "cancelled", whatever job it made, and nothing
    reconciles a cancelled operation later. Otherwise as
    :meth:`CadLinkStore.request_cancel`. None for an unknown operation.
    """

    store = ctx.store
    row = store.get_operation(operation_id)
    if row is None:
        return None
    if row["kind"] == PREPARE_AND_SOLVE and row["state"] not in TERMINAL_STATES:
        try:
            reconciled = reconcile_with_jobs(ctx, operation_id)
            known = ctx.job_for_submission is not None
        except Exception:  # noqa: BLE001 - unknown: decided below
            logger.warning(
                "Could not read the jobs for CAD operation %s before dismissing it.",
                operation_id, exc_info=True,
            )
            reconciled, known = None, False
        if reconciled is not None:
            return reconciled
        current = store.get_operation(operation_id) or row
        if not known and current.get("request_json") and current["state"] not in TERMINAL_STATES:
            raise DismissalUnconfirmed(DISMISSAL_UNCONFIRMED)
    return store.request_cancel(operation_id)


def _restart_pending(ctx: PreparationContext) -> str | None:
    """The approved update restart's refusal, or None when none is pending."""

    return ctx.submission_blocked() if ctx.submission_blocked is not None else None


__all__ = [
    "NO_WORKSPACE_REASON",
    "DeliveryPassReporter",
    "DismissalUnconfirmed",
    "PreparationContext",
    "PreparationInput",
    "SnapshotUnavailable",
    "dismiss_operation",
    "exchange_bundle_path",
    "operation_summary",
    "reconcile_with_jobs",
    "retain_operation_snapshot",
    "settle_received_snapshots",
    "settle_snapshot_operation",
    "submission_key",
]
