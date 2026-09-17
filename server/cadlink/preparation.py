"""Backend preparation of a CAD solve: the operation, not the browser, owns it.

One ``prepare_and_solve`` operation is prepared in fenced stages
(docs/architecture/CAD-OPERATIONS.md, "Preparation"):

    received -> validating -> preparing-mesh -> ready -> submitted

- **received**: the snapshot is retained in WG's own storage before the
  delivery is acknowledged (``retain_operation_snapshot``); a return that
  cannot be read yet keeps its delivery for a bounded number of passes. From
  then on preparation reads the retained copy, so a return whose exchange
  folder was removed still prepares.
- **validating**: the retained copy is found, or made now for an operation
  received before retention existed.
- **preparing-mesh**: the retained snapshot is ingested and meshed with the
  setup revision's options, under the attempt's fence.
- **ready**: the preparation is recorded. Its blocking findings need approvals
  bound to this preparation, never to another one. A preparation of the same
  snapshot, setup revision and meshing semantics is resumed, not made again,
  so approvals given on it still apply.
- **submitted**: the exact solve request is bound -- the binding point, after
  which it never changes -- and submitted to the jobs system under
  ``cad-solve:<operationId>``. Solve execution stays in the jobs system.

Every stage write, the ingestion record and the outcome are conditional on the
attempt's generation. An attempt that lost its operation -- taken over,
dismissed, finished elsewhere -- stops without committing anything. A request
that may have been accepted by the jobs system is recovered by reconciling
through its submission key, never by submitting a changed request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from server.jobs.models import SolveRequest

from .ingest import (
    IngestRefusal,
    ingest_bundle,
    meshing_semantics_fingerprint,
    read_snapshot,
    retain_snapshot,
    retained_snapshot_path,
)
from .isolation import ChildRefusal
from .operations import (
    ACCEPTED,
    CANCEL_REQUESTED,
    NEEDS_USER_INPUT,
    PREPARE_AND_SOLVE,
    PROCESSING,
    REASON_UPDATE_RESTART_PENDING,
    RECEIVE_SNAPSHOT,
    RECEIVED,
    RECOVERY_REQUIRED,
    REJECTED,
    STAGE_PREPARING_MESH,
    STAGE_SUBMITTED,
    STAGE_VALIDATING,
    TERMINAL_STATES,
    canonical_json,
)
from .project_setup import (
    project_setup,
    snapshot_project,
    solver_anchor,
    widen_polar_to_derivation,
)
from .setup import CadSolveSetup, solve_request_for, validate_setup
from .solve_command import (
    CAD_SOLVE_SUBMISSION_PREFIX,
    RETAIN_INVALID,
    RETAIN_TRANSIENT,
    RETAINED,
    SolveOutcomeConflict,
    collect_solve_deliveries,
    live_held_operation_ids,
    record_outcome,
)
from .store import BindingConflict, CadLinkStore, StaleAttempt
from .wgreturn import WgReturnError


logger = logging.getLogger(__name__)

SubmitFn = Callable[[SolveRequest], Awaitable[str]]


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


@dataclass
class PreparationContext:
    """What a preparation needs from the application, injectable for tests."""

    store: CadLinkStore
    data_dir: Path
    workspace_root: Path | None
    #: Submits a solve request to the jobs system and returns its job id.
    submit: SubmitFn | None = None
    #: The job a submission key created, if any (``JobStore.job_for_submission_key``).
    job_for_submission: Callable[[str], str | None] | None = None
    #: Called with an operation's summary after each committed change.
    publish: Callable[[Mapping[str, Any]], None] | None = None
    #: The jobs system's own refusals of a request: a capability it lacks, a
    #: request it cannot accept. They release the binding.
    submission_refusals: tuple[type[BaseException], ...] = ()
    #: Why no CAD preparation may start and no solve may be submitted now:
    #: an approved update restart (UPDATE-TRANSACTION-CONTRACT.md 4.2).
    submission_blocked: Callable[[], str | None] | None = None
    ingest: Callable[..., dict[str, Any]] = ingest_bundle


class _Fenced(Exception):
    """The attempt lost its operation; stop without writing."""


class SnapshotUnavailable(OSError):
    """The return cannot be read now; the operation waits instead of failing for good."""


def submission_key(operation_id: str) -> str:
    return f"{CAD_SOLVE_SUBMISSION_PREFIX}{operation_id}"


def operation_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """An operation as the UI and the events channel see it."""

    outcome = json.loads(row["outcome_json"]) if row.get("outcome_json") else {}
    snapshot = json.loads(row["snapshot_json"]) if row.get("snapshot_json") else None
    state = str(row["state"])
    stage = row.get("stage")
    if stage is None:
        # Written before stages existed, or by an older build.
        stage = "submitted" if row.get("job_id") else ("received" if state == RECEIVED else None)
    return {
        "operationId": row["operation_id"],
        "kind": row["kind"],
        "state": state,
        "stage": stage,
        "reason": row.get("reason"),
        "message": outcome.get("message") if isinstance(outcome, Mapping) else None,
        "jobId": row.get("job_id"),
        "attemptGeneration": int(row.get("attempt_generation") or 0),
        "setupRevisionId": row.get("setup_revision_id"),
        "preparationId": row.get("preparation_id"),
        "snapshot": (
            {
                "manifestSha256": snapshot.get("manifest_sha256"),
                "documentName": snapshot.get("document_name"),
                "projectLineageId": snapshot.get("project_lineage_id"),
            }
            if isinstance(snapshot, Mapping)
            else None
        ),
        "legacy": bool(row.get("legacy")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _publish(ctx: PreparationContext, row: Mapping[str, Any] | None) -> None:
    if row is None or ctx.publish is None:
        return
    try:
        ctx.publish(operation_summary(row))
    except Exception:  # noqa: BLE001 - a notification never fails the work
        logger.debug("Could not publish a CAD operation update.", exc_info=True)


def _inputs(row: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(row["inputs_json"]) if row.get("inputs_json") else {}


def exchange_bundle_path(workspace_root: Path | None, bundle_path: str) -> Path:
    """The return a solve command names, inside the selected WGLink folder."""

    from server.workspace.api import _path_segments, _strictly_inside

    if workspace_root is None:
        raise SnapshotUnavailable(
            "No WGLink folder is selected, so WG cannot read the return Fusion sent. "
            "Choose it in Settings → CAD Link, then press Solve now."
        )
    segments = _path_segments(bundle_path, "bundlePath")
    if not segments or segments[0].casefold() != "wgreturn" or not segments[-1].endswith(".wgreturn"):
        raise WgReturnError("bundlePath must name a .wgreturn bundle under the workspace's wgreturn/")
    path = workspace_root.joinpath(*segments).resolve()
    _strictly_inside(path, workspace_root, "bundlePath")
    if not path.is_dir():
        # The folder was switched, a drive is not mounted, or the return was
        # removed before WG kept a copy: cannot proceed now, not never.
        raise SnapshotUnavailable(
            "The return Fusion sent is not in the WGLink folder, and WG has no copy of it. "
            "Check the WGLink folder in Settings → CAD Link, or send it again from Fusion."
        )
    return path


def _snapshot_record(store: CadLinkStore, retained: Mapping[str, Any]) -> dict[str, Any]:
    """What an operation stores of its snapshot: hashes and whose it is, never a path.

    The copy's place follows from the manifest hash (``retained_snapshot_path``),
    so a moved data directory does not orphan it. The document's name and the
    project it belongs to, when WG knows one, are what lets the UI say which
    project to open for its settings.
    """

    manifest = _retained_manifest(retained)
    return {
        "manifest_sha256": str(retained["manifest_sha256"]),
        "artifact_sha256": str(retained["artifact_sha256"]),
        "document_name": _document_name(manifest),
        "project_lineage_id": snapshot_project(store, manifest) if manifest else None,
    }


def _retained(data_dir: Path, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The operation's retained snapshot, when WG still holds the copy."""

    snapshot = json.loads(row["snapshot_json"]) if row.get("snapshot_json") else None
    if not isinstance(snapshot, Mapping):
        return None
    try:
        path = retained_snapshot_path(data_dir, str(snapshot.get("manifest_sha256") or ""))
    except ValueError:
        return None
    return {**snapshot, "retained_path": str(path)} if path.is_dir() else None


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
        # Nothing is accepted before WG holds its bytes.
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
    """Settle the unsettled snapshots nobody is delivering now. Returns those settled.

    What a delivery over HTTP left ``received`` or ``processing`` -- WG
    stopped, or the store refused, between acceptance and outcome; a return
    not readable within the live bound -- is settled here
    (``settle_snapshot_operation``), at startup and on each delivery pass with
    a WGLink folder. Every such row is reached, page by page, however many stay
    unreadable. Each page is listed first and the live holds read after, so a
    snapshot still being delivered is never settled from under its delivery.
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
        held = live_held_operation_ids()
        for cursor, row in page:
            operation_id = str(row["operation_id"])
            if operation_id in held:
                continue
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
        record_outcome(ctx.store, operation_id, state="accepted", job_id=job_id)
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


def _finish(
    ctx: PreparationContext,
    operation_id: str,
    generation: int,
    state: str,
    *,
    reason: str | None = None,
    message: str | None = None,
    job_id: str | None = None,
    stage: str | None = None,
    release_binding: bool = False,
) -> dict[str, Any]:
    row = ctx.store.record_outcome(
        operation_id,
        generation,
        state,
        job_id=job_id,
        reason=reason,
        outcome={"message": message} if message else None,
        stage=stage,
        release_binding=release_binding,
    )
    if row is None:
        raise _Fenced()
    logger.info(
        "CAD operation %s, attempt %d: finished %s%s%s.",
        operation_id,
        generation,
        row["state"],
        f" ({row['reason']})" if row.get("reason") else "",
        f", job {row['job_id']}" if row.get("job_id") else "",
    )
    _publish(ctx, row)
    return row


def _advance(ctx: PreparationContext, operation_id: str, generation: int, **fields: Any) -> dict[str, Any]:
    row = ctx.store.advance_operation(operation_id, generation, **fields)
    if row is None:
        raise _Fenced()
    if fields.get("stage"):
        logger.info("CAD operation %s, attempt %d: stage %s.", operation_id, generation, fields["stage"])
    _publish(ctx, row)
    return row


def _load_setup(
    ctx: PreparationContext, revision_id: str | None, retained: Mapping[str, Any]
) -> tuple[CadSolveSetup, str] | None:
    """The setup this preparation uses, and its revision id.

    One the request names; otherwise the snapshot's project's own, with the
    engine selected in WG (CAD-OPERATIONS.md, "Project setups"). None when the
    project has none for these sources yet.
    """

    if revision_id:
        row = ctx.store.get_setup_revision(revision_id)
        if row is None:
            return None
        return validate_setup(json.loads(row["setup_json"])), str(row["revision_id"])
    manifest = read_snapshot(str(retained["retained_path"]), retained=True).manifest
    lineage_id = snapshot_project(ctx.store, manifest)
    if lineage_id is None:
        return None
    return project_setup(ctx.store, lineage_id, manifest.get("sources") or [])


def _retained_manifest(retained: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A retained snapshot's manifest, already verified when it was retained."""

    try:
        manifest = json.loads(
            (Path(str(retained["retained_path"])) / "wgreturn.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return manifest if isinstance(manifest, Mapping) else None


def _document_name(manifest: Mapping[str, Any] | None) -> str | None:
    """The CAD document a snapshot came from, as the user named it."""

    document = manifest.get("document") if manifest is not None else None
    name = str(document.get("name") or "").strip() if isinstance(document, Mapping) else ""
    return name or None


def _resumable(
    store: CadLinkStore,
    row: Mapping[str, Any],
    revision_id: str,
    manifest_sha256: str,
    semantics: str,
) -> dict[str, Any] | None:
    """The operation's last preparation, when this attempt would make the same one.

    The same snapshot, setup revision and meshing semantics: the attempt
    resumes it instead of preparing anew, so the approvals given on it apply.
    """

    preparation_id = row.get("preparation_id")
    if not preparation_id:
        return None
    preparation = store.get_preparation(str(preparation_id))
    if (
        preparation is None
        or preparation["setup_revision_id"] != revision_id
        or preparation["snapshot_sha256"] != manifest_sha256
        or preparation["meshing_semantics"] != semantics
    ):
        return None
    ingest = store.get_ingest(str(preparation["ingest_id"]))
    return json.loads(ingest["record_json"]) if ingest is not None else None


def _project_gate(retained: Mapping[str, Any]) -> dict[str, str]:
    """The design and instance the ingest's project gate is given for a snapshot.

    The backend prepares into the snapshot's own project -- the lineage of its
    solver anchor instance's WG design, as ``snapshot_project`` resolves it --
    never into a model that is open. So the gate is told that design and that
    exact instance. A snapshot whose anchor names no design, authored in CAD,
    names nothing, as before.
    """

    anchor = solver_anchor(_retained_manifest(retained) or {})
    if anchor is None:
        return {}
    design_id = anchor.get("design_id")
    if not isinstance(design_id, str) or not design_id.strip():
        return {}
    gate = {"expected_design_id": design_id}
    instance_id = anchor.get("instance_id")
    if isinstance(instance_id, str) and instance_id:
        gate["expected_instance_id"] = instance_id
    return gate


def _prepare_sync(
    ctx: PreparationContext, operation_id: str, generation: int, request: PreparationInput
) -> tuple[str, Any]:
    """The blocking half: retain, mesh, record. Returns what happens next.

    ``("done", row)`` when the operation already reached its outcome;
    ``("submit", (solve_request, revision_id))`` when a request is ready.
    """

    store = ctx.store
    row = store.get_operation(operation_id)
    assert row is not None
    if row.get("request_json"):
        # Bound already: a crash or a lost answer, while a job may exist.
        # Recovery submits exactly the bound request; a setup change made
        # since does not alter it.
        return "submit", (SolveRequest.model_validate_json(row["request_json"]), row.get("setup_revision_id"))

    # validating: the retained copy, made now if the operation predates it.
    _advance(ctx, operation_id, generation, stage=STAGE_VALIDATING)
    retained = _retained(ctx.data_dir, row)
    if retained is None:
        inputs = _inputs(row)
        try:
            retained = retain_snapshot(
                exchange_bundle_path(ctx.workspace_root, str(inputs.get("bundle_path") or "")),
                ctx.data_dir,
                expected_manifest_sha256=str(inputs.get("manifest_sha256") or "") or None,
            )
        except SnapshotUnavailable as exc:
            return "done", _finish(
                ctx, operation_id, generation, NEEDS_USER_INPUT, reason="preparation_failed",
                message=str(exc),
            )
        except (WgReturnError, ValueError) as exc:
            return "done", _finish(
                ctx, operation_id, generation, REJECTED, reason="snapshot_invalid",
                message=str(exc),
            )
        except OSError as exc:
            return "done", _finish(
                ctx, operation_id, generation, NEEDS_USER_INPUT, reason="preparation_failed",
                message=f"WG could not read the return Fusion sent: {exc}",
            )
        _advance(ctx, operation_id, generation, snapshot=_snapshot_record(store, retained))

    try:
        loaded = _load_setup(ctx, request.setup_revision_id, retained)
    except WgReturnError as exc:
        return "done", _finish(
            ctx, operation_id, generation, REJECTED, reason="snapshot_invalid", message=str(exc)
        )
    except ValueError as exc:
        # A recorded setup, or the selected engine, this build cannot take.
        return "done", _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="setup_required",
            message=f"The solve settings recorded for this model cannot be used: {exc}. "
            "Choose them again in WG, then press Solve now.",
        )
    if loaded is None:
        # A first-time CAD-authored model never borrows settings from whatever
        # project is open: it waits for the user to choose them.
        # Whose it is may have become known since it was retained.
        snapshot = _snapshot_record(store, retained)
        _advance(ctx, operation_id, generation, snapshot=snapshot)
        document = snapshot["document_name"]
        return "done", _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="setup_required",
            message=(
                f"Choose the solve settings for {document} in WG: open it from File → "
                "CAD-linked designs, then press Solve now."
                if document
                else "Choose the solve settings for this model in WG, then press Solve now."
            ),
        )
    setup, revision_id = loaded
    manifest_sha256 = str(retained["manifest_sha256"])
    semantics = meshing_semantics_fingerprint()

    record = _resumable(store, row, revision_id, manifest_sha256, semantics)
    if record is None:
        # preparing-mesh: from the retained copy, under the attempt's fence.
        _advance(ctx, operation_id, generation, stage=STAGE_PREPARING_MESH)
        geometry = setup.geometry
        try:
            record = ctx.ingest(
                retained["retained_path"],
                dict(geometry.get("mesh") or {}),
                list(geometry.get("skipped_source_ids") or []),
                store,
                ctx.data_dir,
                prep_options={
                    "area_drift_overrides": list(setup.preparation.area_drift_overrides),
                    "symmetry_mode": setup.preparation.symmetry_mode,
                    **(
                        {"surface_deviation_mm": setup.preparation.surface_deviation_mm}
                        if setup.preparation.surface_deviation_mm is not None
                        else {}
                    ),
                },
                commit_guard=lambda conn: store.attempt_is_current(conn, operation_id, generation),
                retained_copy=True,
                **_project_gate(retained),
            )
        except StaleAttempt as exc:
            raise _Fenced() from exc
        except WgReturnError as exc:
            return "done", _finish(
                ctx, operation_id, generation, REJECTED, reason="snapshot_invalid", message=str(exc)
            )
        except IngestRefusal as exc:
            if getattr(exc, "corruption", False):
                return "done", _finish(
                    ctx, operation_id, generation, REJECTED, reason="snapshot_invalid",
                    message=str(exc),
                )
            return "done", _finish(
                ctx, operation_id, generation, NEEDS_USER_INPUT, reason="preparation_failed",
                message=str(exc),
            )
        except (ChildRefusal, RuntimeError, ValueError, OSError) as exc:
            # A worker that crashed, ran out of time or memory: the app survives
            # and the request is kept for another attempt.
            return "done", _finish(
                ctx, operation_id, generation, NEEDS_USER_INPUT, reason="preparation_failed",
                message=f"Preparing the mesh failed: {exc}",
            )

    blocking = [
        str(finding.get("id"))
        for finding in record.get("findings") or []
        if isinstance(finding, Mapping) and finding.get("blocking")
    ]
    preparation_id = str(record["ingest_id"])
    prepared = store.record_preparation(
        operation_id,
        generation,
        preparation_id=preparation_id,
        snapshot_sha256=manifest_sha256,
        setup_revision_id=revision_id,
        ingest_id=preparation_id,
        report_sha256=record.get("report_sha256"),
        blocking_finding_ids=blocking,
        meshing_semantics=semantics,
    )
    if prepared is None:
        raise _Fenced()
    logger.info(
        "CAD operation %s, attempt %d: stage ready, preparation %s.",
        operation_id, generation, preparation_id,
    )
    _publish(ctx, prepared)

    reviewed = (
        [finding for finding in request.approve_finding_ids if finding in blocking]
        if request.approve_preparation_id == preparation_id
        else []
    )
    if reviewed:
        prepared = store.add_approvals(operation_id, preparation_id, reviewed, generation=generation)
        if prepared is None:
            raise _Fenced()
    approvals = json.loads(prepared["approvals_json"]) if prepared.get("approvals_json") else []
    approved = {
        str(item.get("finding_id"))
        for item in approvals
        if isinstance(item, Mapping) and item.get("preparation_id") == preparation_id
    }
    missing = [finding for finding in blocking if finding not in approved]
    if missing:
        return "done", _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="findings_need_review",
            message="Review the preparation's findings before solving: " + ", ".join(missing),
        )
    if not request.submit:
        return "done", _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="ready_to_solve",
            message="Prepared. Press Solve to start it.",
        )
    try:
        solve_request = solve_request_for(
            setup,
            ingest_id=preparation_id,
            manifest_sha256=str(record["manifest_sha256"]),
            artifact_sha256=str(record["artifact_sha256"]),
            acknowledged_findings=[
                f"{record.get('report_sha256')}:{finding}" for finding in blocking
            ],
            client_request_id=submission_key(operation_id),
        )
        # Never narrower than the ingestion derived: the runtime refuses that.
        solve_request = widen_polar_to_derivation(solve_request, record.get("polar_grid_derivation"))
    except ValueError as exc:
        return "done", _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="submission_refused",
            message=str(exc),
        )
    return "submit", (solve_request, revision_id)


async def _submit(
    ctx: PreparationContext,
    operation_id: str,
    generation: int,
    solve_request: SolveRequest,
    revision_id: str | None,
) -> dict[str, Any]:
    store = ctx.store
    blocked = _restart_pending(ctx)
    if blocked:
        # A restart was approved while this attempt prepared. It waits under a
        # reason of its own, so the next start -- or this process, once the
        # latch is down without a restart -- queues it again by itself.
        return await asyncio.to_thread(
            _finish, ctx, operation_id, generation, NEEDS_USER_INPUT,
            reason=REASON_UPDATE_RESTART_PENDING, message=blocked,
        )
    if ctx.submit is None:
        return await asyncio.to_thread(
            _finish, ctx, operation_id, generation, NEEDS_USER_INPUT,
            reason="submission_refused", message="The jobs system is not running.",
        )
    request_json = canonical_json(solve_request.model_dump(mode="json"))
    try:
        bound = await asyncio.to_thread(
            store.bind_request,
            operation_id,
            generation,
            setup_revision_id=revision_id or "",
            request_json=request_json,
        )
    except BindingConflict:
        row = await asyncio.to_thread(store.get_operation, operation_id)
        solve_request = SolveRequest.model_validate_json(row["request_json"])
        bound = row
    if bound is None:
        raise _Fenced()
    try:
        job_id = await ctx.submit(solve_request)
    except ctx.submission_refusals as exc:
        # The jobs system refused this exact request -- nothing was created --
        # so the binding is released and the user can change what they chose.
        code = str(getattr(exc, "reason_code", "") or getattr(exc, "code", "") or "")
        reason = (
            "engine_unavailable"
            if "engine" in code or type(exc).__name__ == "EngineUnavailableError"
            else "submission_refused"
        )
        return await asyncio.to_thread(
            _finish, ctx, operation_id, generation, NEEDS_USER_INPUT, reason=reason,
            message=str(exc), release_binding=True,
        )
    except Exception as exc:  # noqa: BLE001 - the job may or may not exist
        logger.warning("Submitting CAD solve %s failed: %s", operation_id, exc)
        # A submission-key conflict lands here too: that key already made a
        # job, and the job is the outcome.
        try:
            reconciled = await asyncio.to_thread(reconcile_with_jobs, ctx, operation_id)
            known = ctx.job_for_submission is not None
        except Exception:  # noqa: BLE001 - unknown: keep the binding
            logger.warning("Could not read the jobs for CAD solve %s.", operation_id, exc_info=True)
            reconciled, known = None, False
        if reconciled is not None:
            _publish(ctx, reconciled)
            return reconciled
        if known:
            # The jobs system writes the submission key with the job, and the
            # key names none: nothing was created, so the binding goes.
            return await asyncio.to_thread(
                _finish, ctx, operation_id, generation, NEEDS_USER_INPUT,
                reason="submission_refused",
                message=f"Submitting the solve failed: {exc}. Press Solve now to try again.",
                release_binding=True,
            )
        return await asyncio.to_thread(
            _finish, ctx, operation_id, generation, NEEDS_USER_INPUT, reason="interrupted",
            message=f"Submitting the solve failed: {exc}. Press Solve now to try again.",
        )
    return await asyncio.to_thread(
        _finish, ctx, operation_id, generation, ACCEPTED, job_id=job_id, stage=STAGE_SUBMITTED,
    )


def _restart_pending(ctx: PreparationContext) -> str | None:
    """The approved update restart's refusal, or None when none is pending."""

    return ctx.submission_blocked() if ctx.submission_blocked is not None else None


def requeue_restart_parked(ctx: PreparationContext) -> list[str]:
    """Queue again the solves an update restart held at submission.

    Each goes back to ``received`` at its own generation, so the delivery
    loop starts it as it starts any untouched operation, and that attempt
    resumes its preparation. Nothing is queued while a restart is still
    pending. Returns the operations queued.
    """

    if _restart_pending(ctx):
        return []
    queued: list[str] = []
    for row in ctx.store.list_operations(
        kind=PREPARE_AND_SOLVE, states={NEEDS_USER_INPUT}, oldest_first=True, limit=1000,
    ):
        if row.get("reason") != REASON_UPDATE_RESTART_PENDING:
            continue
        operation_id = str(row["operation_id"])
        requeued = ctx.store.requeue_operation(
            operation_id, int(row["attempt_generation"]), reason=REASON_UPDATE_RESTART_PENDING
        )
        if requeued is not None:
            logger.info(
                "CAD operation %s: queued again now that no update restart is pending.",
                operation_id,
            )
            queued.append(operation_id)
            _publish(ctx, requeued)
    return queued


def _hold_for_restart(
    ctx: PreparationContext, operation_id: str, listed_generation: int, refusal: str
) -> dict[str, Any]:
    """Park an operation the user asked for while an update restart is approved.

    Nothing is prepared: the attempt claims the operation and records that it
    waits for the restart (``update_restart_pending``), so it is queued again
    like any solve the latch held. The request itself is not kept: the queued
    attempt prepares and submits from the project's setup, as the delivery
    loop does, and approvals sent with this request are asked for again. Ones
    already recorded on the preparation still apply.
    """

    generation = ctx.store.claim(operation_id, listed_generation)
    if generation is None:
        return ctx.store.get_operation(operation_id) or {}
    logger.info("CAD operation %s: attempt %d holds it for the update restart.", operation_id, generation)
    try:
        return _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT,
            reason=REASON_UPDATE_RESTART_PENDING, message=refusal,
        )
    except _Fenced:
        return _settle_fenced(ctx, operation_id, generation)


def _settle_fenced(ctx: PreparationContext, operation_id: str, generation: int) -> dict[str, Any]:
    row = ctx.store.get_operation(operation_id)
    if row is not None and row["state"] == CANCEL_REQUESTED:
        settled = ctx.store.settle_cancel(operation_id, generation)
        if settled is not None:
            _publish(ctx, settled)
            return settled
    return ctx.store.get_operation(operation_id) or {}


def _abandon(
    ctx: PreparationContext, operation_id: str, generation: int, error: Exception
) -> dict[str, Any]:
    """An attempt that failed unexpectedly leaves its operation waiting, never stranded.

    A pending dismissal then stands (``record_outcome`` records it); a bound
    request stays bound, because a job may exist.
    """

    try:
        return _finish(
            ctx, operation_id, generation, NEEDS_USER_INPUT, reason="preparation_failed",
            message=f"Preparing this request failed unexpectedly: {error}. "
            "Press Solve now to try again.",
        )
    except _Fenced:
        return _settle_fenced(ctx, operation_id, generation)


async def prepare_operation(
    ctx: PreparationContext,
    operation_id: str,
    request: PreparationInput,
    *,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    """Prepare one solve operation, and submit it when asked. Returns its summary.

    A new call takes over an attempt still holding the operation: the claim
    moves the generation on, and the older attempt's next write is refused.
    ``expected_generation`` is the delivery loop's: it starts only an
    operation still untouched at the generation it listed, so it never takes
    over the user's own attempt or retries one waiting for the user.
    """

    store = ctx.store
    row = await asyncio.to_thread(store.get_operation, operation_id)
    if row is None:
        raise KeyError(operation_id)
    if row["kind"] != PREPARE_AND_SOLVE:
        raise ValueError(f"operation {operation_id!r} is not a solve")
    if row["state"] in TERMINAL_STATES or row["state"] in {CANCEL_REQUESTED, RECOVERY_REQUIRED}:
        return operation_summary(row)
    if expected_generation is not None and (
        row["state"] != RECEIVED or int(row["attempt_generation"]) != expected_generation
    ):
        return operation_summary(row)
    try:
        reconciled = await asyncio.to_thread(reconcile_with_jobs, ctx, operation_id)
    except Exception:  # noqa: BLE001 - the jobs system dedupes by key on submission
        logger.warning(
            "Could not read the jobs for CAD operation %s before preparing it.",
            operation_id, exc_info=True,
        )
        reconciled = None
    if reconciled is not None:
        _publish(ctx, reconciled)
        return operation_summary(reconciled)
    refusal = _restart_pending(ctx)
    if refusal:
        # An update restart was approved after this was asked for: after the
        # route let it through, or after the loop listed it. Nothing is
        # prepared. A received operation stays so, for the loop's next pass;
        # any other waits for the restart, so it is queued again by itself
        # instead of being dropped.
        if row["state"] == RECEIVED:
            return operation_summary(row)
        return operation_summary(await asyncio.to_thread(
            _hold_for_restart, ctx, operation_id, int(row["attempt_generation"]), refusal
        ))
    generation = await asyncio.to_thread(
        store.claim, operation_id, int(row["attempt_generation"])
    )
    if generation is None:
        return operation_summary(await asyncio.to_thread(store.get_operation, operation_id))
    logger.info("CAD operation %s: attempt %d claimed it.", operation_id, generation)
    _publish(ctx, await asyncio.to_thread(store.get_operation, operation_id))
    try:
        step, value = await asyncio.to_thread(_prepare_sync, ctx, operation_id, generation, request)
        if step == "done":
            return operation_summary(value)
        solve_request, revision_id = value
        return operation_summary(
            await _submit(ctx, operation_id, generation, solve_request, revision_id)
        )
    except _Fenced:
        logger.info(
            "CAD operation %s, attempt %d: a later attempt or a dismissal holds the "
            "operation now; this attempt stopped without writing.",
            operation_id, generation,
        )
        return operation_summary(
            await asyncio.to_thread(_settle_fenced, ctx, operation_id, generation)
        )
    except Exception as exc:  # noqa: BLE001 - never strand the operation
        logger.exception("Preparing CAD operation %s failed unexpectedly.", operation_id)
        return operation_summary(
            await asyncio.to_thread(_abandon, ctx, operation_id, generation, exc)
        )


async def run_delivery_pass(
    ctx: PreparationContext,
    *,
    spawn: Callable[[str, Awaitable[Any]], object],
    running: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Collect Fusion's solve commands, and start preparing each new one.

    The backend is the one consumer of solve commands (CAD-OPERATIONS.md,
    "Delivery"): each is retained, recorded and acknowledged, then prepared
    from its project's setup and submitted. Only an operation no attempt has
    touched (``received``) is started, so one waiting for the user is never
    retried unasked; ``running`` names the ones already started. One whose
    delivery is kept because its return cannot be read yet is started once the
    return is retained, or once the delivery is given up. Without a WGLink
    folder nothing is collected, because nothing could be retained. While an
    update restart is approved the pass does nothing at all: no preparation
    starts, and delivered files stay on disk for after the restart. Once the
    latch is down, the solves it held at submission are queued again first,
    with or without a WGLink folder. Received snapshots nobody is delivering
    are settled first (``settle_received_snapshots``). An operation a delivery
    over HTTP holds is not started: the received rows are listed first and the
    live holds read after them. Returns the operations this pass started.
    """

    if _restart_pending(ctx):
        return []
    await asyncio.to_thread(requeue_restart_parked, ctx)
    if ctx.workspace_root is None:
        return []
    await asyncio.to_thread(settle_received_snapshots, ctx)
    held: set[str] = set()
    await asyncio.to_thread(
        collect_solve_deliveries,
        ctx.data_dir,
        ctx.store,
        retain=lambda operation_id: retain_operation_snapshot(
            ctx.store, ctx.data_dir, ctx.workspace_root, operation_id
        ),
        held=held,
    )
    if _restart_pending(ctx):
        # Approved while this pass collected: nothing starts.
        return []
    rows = await asyncio.to_thread(
        ctx.store.list_operations,
        kind=PREPARE_AND_SOLVE, states={RECEIVED}, oldest_first=True, limit=100,
    )
    # After the listing, never before: a live hold is recorded before its
    # operation commits, so every listed row still being delivered is in it.
    held |= live_held_operation_ids()
    started: list[str] = []
    for row in rows:
        operation_id = str(row["operation_id"])
        if row.get("legacy") or operation_id in running or operation_id in held:
            continue
        started.append(operation_id)
        spawn(
            operation_id,
            prepare_operation(
                ctx, operation_id, PreparationInput(),
                expected_generation=int(row["attempt_generation"]),
            ),
        )
    return started


def recover_operations(ctx: PreparationContext) -> int:
    """Startup: settle what a backend that stopped left of its solve operations.

    An operation whose submission key made a job is ``accepted`` with it.
    One an attempt still held when the backend stopped is taken over and waits
    for the user (``interrupted``); a bound request is kept, so the next
    preparation submits exactly it. One an update restart held at submission
    is queued again (``received``), so the delivery loop prepares it by
    itself. Received snapshots a stopped backend left are settled
    (``settle_received_snapshots``) when a WGLink folder is selected. Returns
    how many operations changed.
    """

    store = ctx.store
    changed = len(requeue_restart_parked(ctx))
    changed += len(settle_received_snapshots(ctx))
    for row in store.list_operations(
        kind=PREPARE_AND_SOLVE, states={RECEIVED, PROCESSING, NEEDS_USER_INPUT, CANCEL_REQUESTED},
        oldest_first=True, limit=1000,
    ):
        operation_id = str(row["operation_id"])
        reconciled = reconcile_with_jobs(ctx, operation_id)
        if reconciled is not None and reconciled.get("state") == ACCEPTED:
            changed += 1
            _publish(ctx, reconciled)
            continue
        if row["state"] not in {PROCESSING, CANCEL_REQUESTED}:
            continue
        generation = store.claim(operation_id, int(row["attempt_generation"]))
        if generation is None:
            if row["state"] == CANCEL_REQUESTED:
                # Nobody is working on it any more: the dismissal stands.
                settled = store.settle_cancel(operation_id, int(row["attempt_generation"]))
                if settled is not None:
                    changed += 1
                    _publish(ctx, settled)
            continue
        recorded = store.record_outcome(
            operation_id,
            generation,
            NEEDS_USER_INPUT,
            reason="interrupted",
            outcome={
                "message": "WG stopped while preparing this request. Press Solve now to "
                "prepare it again."
            },
        )
        if recorded is not None:
            logger.info(
                "CAD operation %s: attempt %d took it over at startup; it waits as interrupted.",
                operation_id, generation,
            )
            changed += 1
            _publish(ctx, recorded)
    return changed


__all__ = [
    "DismissalUnconfirmed",
    "PreparationContext",
    "PreparationInput",
    "SnapshotUnavailable",
    "dismiss_operation",
    "exchange_bundle_path",
    "operation_summary",
    "prepare_operation",
    "reconcile_with_jobs",
    "recover_operations",
    "requeue_restart_parked",
    "retain_operation_snapshot",
    "run_delivery_pass",
    "settle_received_snapshots",
    "settle_snapshot_operation",
    "submission_key",
]
