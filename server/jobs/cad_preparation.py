"""A CAD solve as an ordinary job: the preparation lane's work.

Stage 4 of the CAD Link simplification (docs/architecture/CAD-OPERATIONS.md,
"A CAD solve is a job") makes the job the one lifecycle of a CAD solve. WG
accepts a solve by creating a job in status ``preparing`` that holds a typed
:class:`CadSolveIntent` instead of a ``SolveRequest``. The preparation lane
(``JobRuntime``) then does what ``server/cadlink/preparation.py`` does for an
operation today, and this module is that work, moved rather than rewritten:

    retain -> setup -> ingest (mesh) -> frame and domain -> findings -> compose

Only the bookkeeping differs. Nothing here reads or writes an attempt
generation, ``stage`` or ``bind_request`` of a ``cad_operations`` row:

- every write of progress is a compare-and-set on the job's own row
  (``JobStore.advance_preparing_job`` and its siblings), so the lane is fenced
  by the job still being ``preparing`` and not stopped, and needs no takeover;
- a solve that cannot go on ends the job as ``error`` carrying
  ``task_metadata.cad.refusal = {code, message}``, with the reason codes and
  the words the operations use (``operations.REASON_CODES``), and the user's
  remedy is a new job, "Solve again" (``JobRuntime.solve_cad_again``);
- binding -- freezing the exact request and queueing the job -- is one
  transaction (``JobStore.bind_preparing_job``), so it needs no "released
  binding" and no reconciliation.

What the setup revision, the ingestions and the frame and domain tables hold is
unchanged: they are per-project memory, and the job refers to them.

Nothing calls this yet (S4-E2); the delivery pass and the routes switch to it in
S4-F1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from server.cadlink.default_setup import (
    DAMAGED_DEFAULTS_MESSAGE,
    DEFAULT_SETTINGS_NOTE,
    SolveDefaultsDamaged,
)
from server.cadlink.domain_interpretation import excitation_problem, resolve_domain_plan
from server.cadlink.ingest import (
    IngestRefusal,
    ingest_bundle,
    meshing_semantics_fingerprint,
)
from server.cadlink.isolation import ChildRefusal
from server.cadlink.operations import (
    REASON_CODES,
    REASON_UPDATE_RESTART_PENDING,
    REJECTED,
    STAGE_PREPARING_MESH,
    STAGE_READY,
    STAGE_RECEIVED,
    STAGE_SUBMITTED,
    STAGE_VALIDATING,
)
from server.cadlink.preparation import (
    DAMAGED_COPY_MESSAGE,
    PreparationContext,
    SnapshotUnavailable,
    _DefaultsUnavailable,
    _copy_is_whole,
    _default_setup,
    _is_default_setup,
    _load_setup,
    _project_gate,
    _record_frame_axis,
    _remember_default_setup,
    _retain_from_return,
    _retained,
    _retained_manifest,
    _snapshot_record,
    _submission_refusal_reason,
    cad_provenance_record,
    resumable_record,
)
from server.cadlink.project_setup import widen_polar_to_derivation
from server.cadlink.setup import DEFAULTS_ORIGIN, solve_request_for
from server.cadlink.solver_frame import (
    AS_MODELLED,
    REASON as FRAME_CONFIRMATION_REQUIRED,
    ensure_frame_suggestion,
    record_automatic_axis,
    record_frame_refusal,
    resolution_identity,
    resolve_for_manifest as resolve_solver_frame,
)
from server.cadlink.store import CadLinkStore, StaleAttempt
from server.cadlink.wgreturn import WgReturnError
from server.jobs.cad_intent import (
    BIND_BLOCKED,
    BIND_STOPPED,
    STAGE_WAITING_FOR_RESTART,
    CadSolveIntent,
    acceptance_details,
    approval_map,
    cad_of,
    intent_of,
)
from server.jobs.models import SolveRequest

logger = logging.getLogger(__name__)

_PROGRESS = {
    STAGE_RECEIVED: 0.0,
    STAGE_VALIDATING: 0.02,
    STAGE_PREPARING_MESH: 0.05,
    STAGE_READY: 0.09,
}
_STAGE_WORDS = {
    STAGE_RECEIVED: "Waiting to prepare this solve",
    STAGE_VALIDATING: "Checking the return",
    STAGE_PREPARING_MESH: "Preparing the mesh",
    STAGE_READY: "Prepared",
}


# -- what the job says about itself, in the operations' vocabulary ----------------


def job_operation_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A CAD job as ``operation_summary`` shows an operation: state, stage, reason, message.

    The compatibility operation routes serve this read model to the unchanged
    frontend. It changes no state: the state is derived, never stored:

    - bound (``queued`` and later): ``accepted``, stage ``submitted``;
    - ``error`` with a refusal: the state its reason belongs to
      (``operations.REASON_CODES``), so ``snapshot_invalid`` is ``rejected`` and
      every other refusal is ``needs_user_input``;
    - ``cancelled``: ``cancelled``;
    - ``preparing``: ``received`` until a lane holds it, else ``processing``,
      except a job handed back for an update restart, which waits under
      ``update_restart_pending``.
    """

    status = str(row["status"])
    cad = cad_of(row)
    refusal = cad.get("refusal") if isinstance(cad.get("refusal"), Mapping) else None
    setup = cad.get("setup") if isinstance(cad.get("setup"), Mapping) else None
    preparation = cad.get("preparation") if isinstance(cad.get("preparation"), Mapping) else None
    reason: str | None = None
    message: str | None = None
    stage: str | None = str(row.get("stage") or "") or None
    job_id: str | None = None
    config = row.get("config_json")
    if status in {"queued", "running", "complete"} or (isinstance(config, Mapping) and config.get("type") != "cad_intent"):
        state, stage = "accepted", STAGE_SUBMITTED
        job_id = str(row["id"])
        if setup and setup.get("origin") == DEFAULTS_ORIGIN:
            message = DEFAULT_SETTINGS_NOTE
    elif status == "error" and refusal is not None:
        reason, message = refusal.get("code"), refusal.get("message")
        state = REASON_CODES.get(reason, "needs_user_input")
        stage = _last_stage(cad)
    elif status == "cancelled":
        state, stage = "cancelled", _last_stage(cad)
    elif status == "preparing" and stage == STAGE_WAITING_FOR_RESTART:
        state, reason, message = "needs_user_input", REASON_UPDATE_RESTART_PENDING, str(row.get("stage_message") or "")
        stage = _last_stage(cad)
    elif status == "preparing":
        state = "received" if row.get("started_at") is None else "processing"
    else:  # error without a refusal: a job that failed as a solve
        state = "needs_user_input"
        message = row.get("error_message")
    snapshot = cad.get("snapshot") if isinstance(cad.get("snapshot"), Mapping) else None
    accepted = acceptance_details(row)
    return {
        "operationId": cad.get("operation_id"),
        "state": state,
        "stage": stage,
        "reason": reason,
        "message": message,
        "setupDefaults": accepted["setup_defaults"] and state == "accepted",
        "frameAxisAutomatic": accepted["frame_axis_automatic"],
        "jobId": job_id,
        "setupRevisionId": setup.get("revision_id") if setup else None,
        "preparationId": preparation.get("preparation_id") if preparation else None,
        "snapshot": (
            {
                "manifestSha256": snapshot.get("manifest_sha256"),
                "documentName": snapshot.get("document_name"),
                "projectLineageId": snapshot.get("project_lineage_id"),
            }
            if snapshot
            else None
        ),
    }


def job_cad_state(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The job-side CAD read model; no operation row participates."""

    metadata = row.get("task_metadata")
    if not (isinstance(metadata, Mapping) and isinstance(metadata.get("cad"), Mapping)) and intent_of(row) is None:
        return None
    cad = cad_of(row)
    view = job_operation_view(row)
    intent = intent_of(row)
    snapshot = cad.get("snapshot")
    preparation = cad.get("preparation")
    return {
        "operation_id": view["operationId"] or (intent.operation_id if intent else None),
        **{key: view[key] for key in ("state", "stage", "reason", "message")},
        "job_id": view["jobId"],
        "snapshot": (
            {key: snapshot.get(key) for key in ("document_name", "manifest_sha256", "artifact_sha256", "project_lineage_id")}
            if isinstance(snapshot, Mapping) else None
        ),
        "preparation": (
            {"preparation_id": preparation["preparation_id"],
             "blocking_finding_ids": list(preparation.get("blocking_finding_ids") or []),
             "report_sha256": preparation.get("report_sha256")}
            if isinstance(preparation, Mapping) and preparation.get("preparation_id") else None
        ),
        "approvals": list(preparation.get("approvals") or []) if isinstance(preparation, Mapping) else [],
        "setup_defaults": view["setupDefaults"],
        "frame_axis_automatic": view["frameAxisAutomatic"],
        "received_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _last_stage(cad: Mapping[str, Any]) -> str | None:
    """How far preparation got before it stopped, for the operation vocabulary."""

    if cad.get("last_stage"):
        return str(cad["last_stage"])
    return STAGE_READY if isinstance(cad.get("preparation"), Mapping) else None


# -- the lane's environment ------------------------------------------------------


@dataclass
class CadPreparationHost:
    """What preparation needs from the application, injectable for tests."""

    store: CadLinkStore
    data_dir: Path
    #: The selected WGLink folder now, or None (it can change while WG runs).
    workspace_root: Callable[[], Path | None] = lambda: None
    ingest: Callable[..., dict[str, Any]] = ingest_bundle


BindOutcome = str


class CadJobPort(Protocol):
    """The runtime, as the lane sees it."""

    @property
    def job_store(self) -> Any: ...

    def restart_refusal(self) -> str | None: ...

    def publish(self, event: Mapping[str, Any] | None) -> None: ...

    #: The jobs system's own refusals of a request (a capability it lacks, a
    #: request it cannot accept): the binding ends the job with their message.
    @property
    def binding_refusals(self) -> tuple[type[BaseException], ...]: ...

    async def bind_cad_job(
        self, job_id: str, request: SolveRequest, cad_provenance: Mapping[str, Any]
    ) -> BindOutcome: ...


class _Fenced(Exception):
    """The job is no longer this lane's: stopped, or bound. Stop without writing."""


@dataclass
class _Run:
    """One lane's hold on one job: the job's writes, and what it has recorded so far."""

    port: CadJobPort
    job_id: str
    intent: CadSolveIntent
    cad: dict[str, Any] = field(default_factory=dict)

    def advance(
        self,
        *,
        stage: str | None = None,
        cad: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a stage and what the lane made, or stop: the job is no longer ours."""

        changes = dict(cad or {})
        if stage is not None:
            changes["last_stage"] = stage
        applied, event = self.port.job_store.advance_preparing_job(
            self.job_id,
            stage=stage,
            stage_message=_STAGE_WORDS.get(stage or "", ""),
            progress=_PROGRESS.get(stage or ""),
            cad=changes or None,
        )
        if not applied:
            raise _Fenced()
        self.cad.update(changes)
        self.port.publish(event)
        if stage:
            logger.info("CAD job %s (operation %s): stage %s.", self.job_id, self.intent.operation_id, stage)

    def refuse(self, code: str, message: str) -> tuple[str, None]:
        """End the job as refused. ``("done", None)``: the preparation is over."""

        event = self.port.job_store.refuse_preparing_job(
            self.job_id, code=code, message=message, cad=self.cad or None
        )
        if event is None:
            raise _Fenced()
        logger.info(
            "CAD job %s (operation %s): refused (%s)%s.",
            self.job_id, self.intent.operation_id, code,
            f": {message}" if REASON_CODES.get(code) != REJECTED else "",
        )
        self.port.publish(event)
        return "done", None

    def is_held(self) -> bool:
        """The ingest's commit guard: the job is still preparing and not stopped."""

        state = self.port.job_store.cancellation_state(self.job_id)
        return state is not None and state[0] == "preparing" and not state[1]


def _setup_record(store: CadLinkStore, revision_id: str) -> dict[str, Any]:
    revision = store.get_setup_revision(revision_id)
    return {
        "revision_id": revision_id,
        "digest": revision["content_sha256"] if revision is not None else None,
        "origin": "wg_defaults" if _is_default_setup(store, revision_id) else "user",
    }


def _retention_row(
    store: CadLinkStore, intent: CadSolveIntent, recorded: Mapping[str, Any]
) -> dict[str, Any]:
    """What retention needs of the solve: the snapshot record and where the return was.

    The operations ledger holds the retained snapshot's record (it is written
    when the delivery is retained, before the delivery is acknowledged); a job
    that continues another carries the record the first one made
    (``task_metadata.cad.snapshot``), so the return may have left the WGLink
    folder and the ledger row may be gone. The intent carries the delivery's
    own identity either way.
    """

    row = store.get_operation(intent.operation_id) or {}
    snapshot = row.get("snapshot_json")
    if not snapshot and isinstance(recorded.get("snapshot"), Mapping):
        snapshot = json.dumps(dict(recorded["snapshot"]))
    return {
        "operation_id": intent.operation_id,
        "snapshot_json": snapshot,
        "inputs_json": json.dumps(
            {"bundle_path": intent.bundle_path, "manifest_sha256": intent.manifest_sha256}
        ),
    }


def _carried_approvals(cad: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, str]]:
    """The approvals already given on the preparation this job carries."""

    preparation = cad.get("preparation")
    return approval_map(preparation.get("approvals") if isinstance(preparation, Mapping) else ())


# -- the preparation --------------------------------------------------------------


def prepare_job_sync(
    host: CadPreparationHost,
    run: _Run,
    intent: CadSolveIntent,
    *,
    automatic_retry: bool = False,
) -> tuple[str, Any]:
    """The blocking half: retain, mesh, record. ``server.cadlink.preparation._prepare_sync``.

    Returns ``("done", None)`` when the job was refused, and
    ``("bind", (request, revision_id, preparation))`` when a request is ready.
    The lane's claim has already moved the job to ``validating``.
    ``automatic_retry`` marks the one second pass made to mesh along WG's
    confident automatic axis: it never starts another.

    The logic is the operation's, step for step, with the same reasons and
    words. What is gone: the bound-request recovery branch (binding is one
    transaction now), and every generation check (a stop ends the job, and the
    next write finds it ended).
    """

    store = host.store
    context = PreparationContext(
        store=store,
        data_dir=host.data_dir,
        workspace_root=host.workspace_root(),
        ingest=host.ingest,
    )
    ledger = _retention_row(store, intent, run.cad)
    expected_frame_axis = intent.frame_axis
    retained = _retained(host.data_dir, ledger)
    if retained is not None and not _copy_is_whole(retained):
        # WG's own copy is no longer the bundle its digest names: replaced from
        # the return, never meshed from and never blamed on the user's return.
        logger.warning(
            "CAD job %s: the retained copy is damaged; taking it again.", run.job_id
        )
        try:
            retained = _retain_from_return(context, ledger)
        except (OSError, ValueError) as exc:
            logger.info("CAD job %s: the damaged copy could not be replaced: %s", run.job_id, exc)
            return run.refuse("preparation_failed", DAMAGED_COPY_MESSAGE)
        run.advance(cad={"snapshot": _snapshot_record(store, retained)})
    if retained is None:
        if intent.bundle_path.startswith("ingest/"):
            # A manual solve has only WG's retained copy. Losing it is a
            # recoverable damaged-copy refusal, not an invalid Fusion return.
            return run.refuse("preparation_failed", DAMAGED_COPY_MESSAGE)
        try:
            retained = _retain_from_return(context, ledger)
        except SnapshotUnavailable as exc:
            return run.refuse("preparation_failed", str(exc))
        except (WgReturnError, ValueError) as exc:
            return run.refuse("snapshot_invalid", str(exc))
        except OSError as exc:
            return run.refuse(
                "preparation_failed", f"WG could not read the return Fusion sent: {exc}"
            )
        run.advance(cad={"snapshot": _snapshot_record(store, retained)})

    if "snapshot" not in run.cad:
        run.advance(cad={"snapshot": _snapshot_record(store, retained)})

    named_revision = _named_setup_revision(intent, run.cad)
    try:
        loaded = _load_setup(context, named_revision, retained)
        if loaded is None and named_revision is None:
            # A first-time model -- no settings recorded for its project and
            # these sources: WG's default settings, never another project's.
            loaded = _default_setup(context, retained)
    except SolveDefaultsDamaged as exc:
        logger.error("WG's default solve settings cannot be used: %s", exc)
        return run.refuse("setup_required", DAMAGED_DEFAULTS_MESSAGE)
    except _DefaultsUnavailable as exc:
        snapshot = _snapshot_record(store, retained)
        run.advance(cad={"snapshot": snapshot})
        document = snapshot["document_name"] or "this model"
        return run.refuse(
            "setup_required",
            f"WG cannot solve {document} with its default settings: {exc}. "
            "Choose its settings in WG, then solve it.",
        )
    except WgReturnError as exc:
        return run.refuse("snapshot_invalid", str(exc))
    except ValueError as exc:
        # A recorded setup, or the selected engine, this build cannot take.
        return run.refuse(
            "setup_required",
            f"The solve settings recorded for this model cannot be used: {exc}. "
            "Choose them again in WG, then press Solve now.",
        )
    if loaded is None:
        # The settings this solve names are gone: it never borrows settings from
        # whatever project is open, and waits for the user to choose them.
        snapshot = _snapshot_record(store, retained)
        run.advance(cad={"snapshot": snapshot})
        document = snapshot["document_name"]
        return run.refuse(
            "setup_required",
            f"Choose the solve settings for {document} in WG, then solve it."
            if document
            else "Choose the solve settings for this model in WG, then solve it.",
        )
    setup, revision_id = loaded
    # Recorded before anything can fail: a preparation that fails before it is
    # recorded still leaves the job holding the settings it was asked with, for
    # a Solve again that names none.
    run.advance(cad={"setup": _setup_record(store, revision_id)})
    manifest_sha256 = str(retained["manifest_sha256"])
    semantics = meshing_semantics_fingerprint()
    retained_manifest = _retained_manifest(retained)
    if retained_manifest is None:
        # Never guessed: a manifest read as linked would skip the frame the
        # snapshot may need.
        return run.refuse(
            "preparation_failed",
            "WG could not read its retained copy of this return. Press Solve now: "
            "WG takes a fresh copy from the WGLink folder. If the return has left that "
            "folder, send the model again from Fusion.",
        )
    solver_frame = resolve_solver_frame(
        store, retained_manifest, manifest_sha256, automatic=True
    )
    frame_axis = solver_frame.axis if solver_frame is not None else None
    frame_identity = resolution_identity(solver_frame) if solver_frame is not None else None

    # The domain reading this preparation would be made under (M1c-auto).
    domain_identity = resolve_domain_plan(store, retained_manifest, manifest_sha256).identity()

    record = resumable_record(
        store,
        _resume_candidate(run.cad),
        revision_id,
        manifest_sha256,
        semantics,
        frame_identity,
        domain_identity,
    )
    if record is None:
        # preparing-mesh: from the retained copy, while the job is still ours.
        run.advance(stage=STAGE_PREPARING_MESH)
        geometry = setup.geometry
        try:
            record = host.ingest(
                retained["retained_path"],
                dict(geometry.get("mesh") or {}),
                list(geometry.get("skipped_source_ids") or []),
                store,
                host.data_dir,
                prep_options={
                    "area_drift_overrides": list(setup.preparation.area_drift_overrides),
                    "symmetry_mode": setup.preparation.symmetry_mode,
                    **(
                        {"surface_deviation_mm": setup.preparation.surface_deviation_mm}
                        if setup.preparation.surface_deviation_mm is not None
                        else {}
                    ),
                    **(
                        {"solver_frame": frame_axis}
                        if frame_axis is not None and frame_axis != AS_MODELLED
                        else {}
                    ),
                },
                commit_guard=lambda _conn: run.is_held(),
                retained_copy=True,
                defer_viewport=True,
                **_project_gate(retained),
            )
        except StaleAttempt as exc:
            raise _Fenced() from exc
        except WgReturnError as exc:
            return run.refuse("snapshot_invalid", str(exc))
        except IngestRefusal as exc:
            if getattr(exc, "corruption", False):
                return run.refuse("snapshot_invalid", str(exc))
            return run.refuse("preparation_failed", str(exc))
        except (ChildRefusal, RuntimeError, ValueError, OSError) as exc:
            # A worker that crashed, ran out of time or memory: the app survives
            # and the user may solve again.
            return run.refuse("preparation_failed", f"Preparing the mesh failed: {exc}")

    if setup.origin == DEFAULTS_ORIGIN:
        standing = _remember_default_setup(store, retained_manifest, record, revision_id)
        if standing is not None:
            # The user recorded settings for this model while WG prepared it
            # with the defaults: theirs win, for this solve too.
            logger.info(
                "CAD job %s: settings were recorded for this model meanwhile; preparing "
                "with them instead of WG's defaults.",
                run.job_id,
            )
            run.advance(cad={"setup": _setup_record(store, standing)})
            return prepare_job_sync(
                host, run, replace(intent, setup_revision_id=standing),
                automatic_retry=automatic_retry,
            )

    # The automatic frame suggestion (M1e), inside this explicit command and
    # cached per snapshot: the frame card preselects it. It never confirms, and a
    # survey that fails only leaves the card asking.
    try:
        ensure_frame_suggestion(store, record)
        automatic = record_automatic_axis(store, record)
    except Exception as exc:  # noqa: BLE001 - advisory by construction
        logger.warning("Solver frame suggestion failed for %s: %s", record.get("ingest_id"), exc)
        automatic = None
    if (
        solver_frame is not None
        and automatic is not None
        and automatic != solver_frame.automatic_axis
        and automatic != _record_frame_axis(record)
        and not automatic_retry
    ):
        # Nothing confirmed and WG is confident which way the model faces: the
        # model is meshed again along it, once.
        logger.info(
            "CAD job %s: preparing again along the automatic axis %s.", run.job_id, automatic
        )
        return prepare_job_sync(host, run, intent, automatic_retry=True)
    if automatic_retry and automatic is not None and automatic != _record_frame_axis(record):
        # Bounded: a second pass that still disagrees is a defect, not a reason
        # to mesh again. The frame gate below answers it.
        logger.error(
            "CAD job %s: the automatic axis %s was not the axis meshed (%s) on the "
            "second pass; not preparing again.",
            run.job_id, automatic, _record_frame_axis(record),
        )

    blocking = [
        str(finding.get("id"))
        for finding in record.get("findings") or []
        if isinstance(finding, Mapping) and finding.get("blocking")
    ]
    preparation_id = str(record["ingest_id"])
    carried = {
        key: item for key, item in _carried_approvals(run.cad).items()
        if key[0] == preparation_id
    }
    preparation = {
        "preparation_id": preparation_id,
        "ingest_id": preparation_id,
        "setup_revision_id": revision_id,
        "snapshot_sha256": manifest_sha256,
        "report_sha256": record.get("report_sha256"),
        "blocking_finding_ids": blocking,
        "meshing_semantics": semantics,
        "approvals": list(carried.values()),
    }
    document = record.get("document")
    return_state_hash = document.get("return_state_hash") if isinstance(document, Mapping) else None
    run.advance(
        stage=STAGE_READY,
        cad={
            "preparation": preparation,
            **({"return_state_hash": return_state_hash} if return_state_hash else {}),
        },
    )

    # Before findings and approvals: a frame confirmed differently is a new
    # preparation, and approvals never carry to it. Read from the record, for
    # every preparation: it says what was prepared, linked or not, and which
    # frame; only that frame, confirmed, may be solved.
    frame_refusal = record_frame_refusal(store, record)
    if frame_refusal is not None:
        return run.refuse(FRAME_CONFIRMATION_REQUIRED, frame_refusal)
    prepared_axis = _record_frame_axis(record)
    if (
        expected_frame_axis is not None
        and prepared_axis is not None
        and prepared_axis != expected_frame_axis
    ):
        return run.refuse(
            FRAME_CONFIRMATION_REQUIRED,
            f"This project's solver frame is {prepared_axis} now, not the "
            f"{expected_frame_axis} WG showed when you pressed Solve: it was "
            "changed elsewhere. Check it, then press Solve again.",
        )

    requested = intent.approvals or {}
    reviewed = (
        [
            finding
            for finding in requested.get("finding_ids") or ()
            if finding in blocking
        ]
        if requested.get("preparation_id") == preparation_id
        else []
    )
    if reviewed:
        carried.update(
            {
                (preparation_id, str(finding)): {
                    "preparation_id": preparation_id, "finding_id": str(finding),
                }
                for finding in reviewed
            }
        )
        preparation = {**preparation, "approvals": list(carried.values())}
        run.advance(cad={"preparation": preparation})
    approved = {finding_id for (_prep, finding_id) in carried}
    missing = [finding for finding in blocking if finding not in approved]
    if missing:
        return run.refuse(
            "findings_need_review",
            "Review the preparation's findings before solving: " + ", ".join(missing),
        )
    if not intent.submit:
        return run.refuse("ready_to_solve", "Prepared. Press Solve to start it.")
    try:
        solve_request = solve_request_for(
            setup,
            ingest_id=preparation_id,
            manifest_sha256=str(record["manifest_sha256"]),
            artifact_sha256=str(record["artifact_sha256"]),
            acknowledged_findings=[
                f"{record.get('report_sha256')}:{finding}" for finding in blocking
            ],
            client_request_id=intent.submission_key,
            label=intent.label,
        )
        # Never narrower than the ingestion derived: the runtime refuses that.
        solve_request = widen_polar_to_derivation(solve_request, record.get("polar_grid_derivation"))
    except ValueError as exc:
        return run.refuse("submission_refused", str(exc))
    # Acoustic compatibility, a hard check on every submission: the excitation
    # is the setup's, so it is judged again whenever the settings change.
    excitation = excitation_problem(record, getattr(solve_request.geometry, "drive_channels", ()))
    if excitation is not None:
        return run.refuse("submission_refused", excitation)
    if intent.parent_job_id:
        solve_request.parent_job_id = intent.parent_job_id
    return "bind", (solve_request, revision_id, preparation)


def _named_setup_revision(intent: CadSolveIntent, recorded: Mapping[str, Any]) -> str | None:
    """The setup revision this run prepares with.

    ``CadSolveIntent.setup_revision_id`` when the user named one, else the one
    this job (or the job it continues) recorded, else the recorded preparation's.
    None only for a job that never had one: the project's own setup then applies.
    """

    if intent.setup_revision_id:
        return intent.setup_revision_id
    setup = recorded.get("setup")
    if isinstance(setup, Mapping) and setup.get("revision_id"):
        return str(setup["revision_id"])
    preparation = recorded.get("preparation")
    if isinstance(preparation, Mapping) and preparation.get("setup_revision_id"):
        return str(preparation["setup_revision_id"])
    return None


def _resume_candidate(recorded: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The preparation this run would resume: the last one recorded, its own or its parent's."""

    preparation = recorded.get("preparation")
    return preparation if isinstance(preparation, Mapping) else None


# -- the lane ---------------------------------------------------------------------


async def run_cad_preparation(port: CadJobPort, host: CadPreparationHost, job_id: str) -> str:
    """Prepare one accepted CAD solve, and bind it when it is ready.

    Returns what became of the job here: ``"bound"`` (queued for the solver),
    ``"refused"``, ``"waiting"`` (an approved update restart holds it; it is taken
    up again by itself), ``"fenced"`` (stopped, or already bound) or ``"idle"``
    (nothing to do). Never raises for a job's own failure: an unexpected one
    ends the job as refused (``preparation_failed``), so a solve is never
    stranded in ``preparing``.
    """

    store = port.job_store
    row = await asyncio.to_thread(store.get_job_row, job_id)
    intent = intent_of(row) if row is not None and row["status"] == "preparing" else None
    if intent is None:
        return "idle"
    if port.restart_refusal():
        # An approved restart: nothing is prepared and nothing is written. The
        # job stays as it is, and the lane takes it up when the latch is down.
        return "waiting"
    event = await asyncio.to_thread(
        store.claim_preparing_job,
        job_id,
        stage=STAGE_VALIDATING,
        stage_message=_STAGE_WORDS[STAGE_VALIDATING],
        progress=_PROGRESS[STAGE_VALIDATING],
        cad={"last_stage": STAGE_VALIDATING},
    )
    if event is None:
        return "fenced"
    port.publish(event)
    logger.info("CAD job %s (operation %s): the lane took it.", job_id, intent.operation_id)
    cad = dict(cad_of(row))
    cad["last_stage"] = STAGE_VALIDATING
    run = _Run(port=port, job_id=job_id, intent=intent, cad=cad)
    try:
        step, value = await asyncio.to_thread(prepare_job_sync, host, run, intent)
        if step == "done":
            return "refused"
        return await _bind(port, host, run, *value)
    except _Fenced:
        logger.info(
            "CAD job %s (operation %s): stopped; the lane wrote nothing more.",
            job_id, intent.operation_id,
        )
        return "fenced"
    except Exception as exc:  # noqa: BLE001 - never strand the job
        logger.exception("Preparing CAD job %s failed unexpectedly.", job_id)
        try:
            await asyncio.to_thread(
                run.refuse,
                "preparation_failed",
                f"Preparing this request failed unexpectedly: {exc}. "
                "Press Solve now to try again.",
            )
        except _Fenced:
            pass
        return "refused"


async def _bind(
    port: CadJobPort,
    host: CadPreparationHost,
    run: _Run,
    solve_request: SolveRequest,
    revision_id: str | None,
    preparation: Mapping[str, Any],
) -> str:
    store = host.store
    blocked = port.restart_refusal()
    if blocked:
        return await _hand_back(port, run, blocked)
    # Collected once, before the job is bound: the job's record and its frame
    # label come from the same read. A failure here is not a failed submission:
    # nothing was submitted.
    try:
        provenance = await asyncio.to_thread(
            _job_provenance, store, run, solve_request, revision_id, preparation
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, with its own words
        logger.warning("Collecting the provenance of CAD job %s failed: %s", run.job_id, exc)
        await asyncio.to_thread(
            run.refuse,
            "preparation_failed",
            f"WG could not record what this solve was prepared from: {exc}. "
            "Press Solve now to try again.",
        )
        return "refused"
    try:
        outcome = await port.bind_cad_job(run.job_id, solve_request, provenance)
    except port.binding_refusals as exc:
        # The jobs system refused this exact request; nothing was created.
        code = str(getattr(exc, "reason_code", "") or getattr(exc, "code", "") or "")
        await asyncio.to_thread(
            run.refuse, _submission_refusal_reason(exc, code), str(exc)
        )
        return "refused"
    except _Fenced:
        raise
    except Exception as exc:  # noqa: BLE001 - nothing was bound: binding is one transaction
        logger.warning("Binding CAD job %s failed: %s", run.job_id, exc)
        await asyncio.to_thread(
            run.refuse,
            "submission_refused",
            f"Submitting the solve failed: {exc}. Press Solve now to try again.",
        )
        return "refused"
    if outcome == BIND_BLOCKED:
        # A restart was approved between the check and the bind: the bind is
        # ordered with it, so the job was not queued.
        return await _hand_back(port, run, port.restart_refusal() or "")
    if outcome == BIND_STOPPED:
        raise _Fenced()
    logger.info("CAD job %s (operation %s): bound and queued.", run.job_id, run.intent.operation_id)
    return "bound"


async def _hand_back(port: CadJobPort, run: _Run, message: str) -> str:
    """Wait for the update restart: the job keeps what it prepared and no lane holds it."""

    event = await asyncio.to_thread(
        port.job_store.hand_back_preparing_job,
        run.job_id,
        stage=STAGE_WAITING_FOR_RESTART,
        stage_message=message,
    )
    if event is None:
        raise _Fenced()
    port.publish(event)
    logger.info(
        "CAD job %s (operation %s): waiting for the update restart.",
        run.job_id, run.intent.operation_id,
    )
    return "waiting"


def _job_provenance(
    store: CadLinkStore,
    run: _Run,
    solve_request: SolveRequest,
    revision_id: str | None,
    preparation: Mapping[str, Any],
) -> dict[str, Any]:
    """``task_metadata.cad`` of the bound job: what it keeps of the solve that made it.

    ``cad_provenance_record``'s, so a job made by the lane and one made by an
    operation say the same things, plus the snapshot and the retained state the
    preparation recorded on the way.
    """

    record = cad_provenance_record(
        store,
        run.intent.operation_id,
        solve_request,
        revision_id,
        preparation=preparation,
        approvals=preparation.get("approvals") or [],
    )
    for key in ("snapshot", "return_state_hash"):
        if key in run.cad:
            record[key] = run.cad[key]
    if run.intent.frame_axis:
        # The axis the user's Solve showed, which the solve was held to (the
        # solved frame is ``frame``; this is what the press named).
        record["frame_axis_shown"] = run.intent.frame_axis
    return record


__all__ = [
    "CadJobPort",
    "CadPreparationHost",
    "job_cad_state",
    "job_operation_view",
    "prepare_job_sync",
    "run_cad_preparation",
]
