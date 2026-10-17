"""One way to solve an imported model: ``POST /api/models/{id}/solve``.

Every imported solve WG starts goes through here (docs/architecture/
CAD-OPERATIONS.md, "Solving a model"): the Solve button for a file or a CAD
model, a restored run solved again, ``POST /api/solve`` with imported geometry
(``submit_imported_request``) and the retry of an imported run. The press
names its model, the prepared mesh it is looking at and the setup -- a saved
revision, or the setup itself -- and the server decides everything else:

- **A file model** is solved directly: the setup becomes the request with the
  composition every lane shares (``compose_solve_request``) and is submitted
  under ``model-solve:<client_request_id>``.
- **A CAD model** goes through the preparation lane, as a Fusion "Solve in WG"
  does: a manual intent under ``cad-solve:manual-solve:<client_request_id>``,
  whose preparation is resumed when nothing it depends on changed and made
  again when the setup did (a changed engine is a new revision). A refused
  CAD solve of the same snapshot is continued (Solve again) instead of starting
  beside it. An unlinked model's displayed solver frame is confirmed here.
- **In flight.** While a job for the same model, prepared mesh and setup is
  preparing, queued or running -- a Fusion "Solve in WG" included -- the press
  is that job: the server returns it rather than starting a second one.

Every job records the setup revision it was made from
(``task_metadata.imported_geometry.setup_revision_id``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any
import weakref

from pydantic import Field, field_validator, model_validator

from server.cadlink.intake.ingest import read_snapshot, retained_snapshot_path
from server.cadlink.intake.readings import (
    confirm_frame,
    frame_preview,
    resolve_domain_plan,
    resolve_for_manifest,
)
from server.cadlink.interpretation.domain_interpretation import record_plan_identity
from server.cadlink.interpretation.solver_frame import (
    AXES as SOLVER_FRAME_AXES,
    FrameConfirmationError,
    REASON as FRAME_CONFIRMATION_REQUIRED,
    record_frame_identity,
    record_is_unlinked,
    resolution_identity,
)
from server.cadlink.joblane.manual_solve import OperationConflict, SnapshotNotRetained, UnknownIngest
from server.cadlink.joblane.project_setup import compose_solve_request
from server.cadlink.operations import REASON_CODES, REASON_UPDATE_RESTART_PENDING, REJECTED
from server.cadlink.roles import inventory_sha256
from server.cadlink.store import UNFILED_MODEL_ID
from server.contracts import JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.jobs.cad_intent import CAD_INTENT, cad_of
from server.jobs.imported_setup import (
    DEFAULTS_ORIGIN,
    CadSolveSetup,
    ImportedSolveSetup,
    setup_content,
    setup_digest,
    setup_of_request,
    validate_setup,
)
from server.jobs.models import CadApprovalsRequest, ImportedGeometrySource, SolveRequest
from server.jobs.runtime import (
    EngineUnavailableError,
    ImportedSolveRefusal,
    JobConflictError,
    RemovedSolverError,
    SymmetryValidationError,
    UnknownEngineError,
)
from server.jobs.store import SubmissionConflictError

ACTIVE_STATUSES = ("preparing", "queued", "running")
FILE_PRODUCER = "mesh-file"
#: ``UNFILED_MODEL_ID`` ('unfiled', reserved by the store) is the model id a
#: press names for a prepared mesh filed under no model (its ``ingests.model_id``
#: is ''): a CAD return of a design this workspace does not know. It is solved
#: as any other; no setup is recorded for it.
# Refusals of the request that mean the prepared mesh itself is gone.
_PREPARED_GONE = frozenset({"ingest_not_found", "ingest_mesh_unavailable"})


class ModelSolveRequest(JobModel):
    """One Solve of an imported model: what the press showed."""

    prepared_id: str = Field(min_length=1, description="The prepared mesh on screen (its ingest id).")
    setup_revision_id: str | None = Field(
        default=None, min_length=1,
        description="A saved setup revision: an unedited restored run sends its own.",
    )
    setup: ImportedSolveSetup | None = Field(default=None, description="The setup on screen.")
    client_request_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
        description="Stable id of this press; a replay recovers its original job.",
    )
    label: str | None = None
    #: The solver frame axis the press showed for an unlinked CAD model. It is
    #: confirmed for the model's project, and the solve is held to it.
    frame_axis: str | None = None
    #: Blocking findings the user reviewed on one preparation.
    approvals: CadApprovalsRequest | None = None

    @field_validator("frame_axis")
    @classmethod
    def known_axis(cls, value: str | None) -> str | None:
        if value is not None and value not in SOLVER_FRAME_AXES:
            raise ValueError(f"frame_axis must be one of {', '.join(SOLVER_FRAME_AXES)}")
        return value

    @model_validator(mode="after")
    def one_setup(self) -> ModelSolveRequest:
        if (self.setup is None) == (self.setup_revision_id is None):
            raise ValueError("name exactly one of setup_revision_id or setup")
        return self


class ModelSolveRefusal(ValueError):
    """A Solve the server refuses, as an error envelope: status, code and words."""

    def __init__(
        self, status: int, code: str, message: str, *,
        details: Mapping[str, Any] | None = None, retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status, self.code = status, code
        self.details = dict(details) if details else None
        self.retryable = retryable


# -- shared reads ----------------------------------------------------------------


_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _model_lock(model_id: str) -> asyncio.Lock:
    """One Solve of a model at a time: the in-flight check and the job it makes are one step."""

    return _locks.setdefault(asyncio.get_running_loop(), {}).setdefault(model_id, asyncio.Lock())


def restart_refusal(runtime: Any) -> str | None:
    """The approved update restart's (or a retirement hold's) refusal of new work."""

    approval = getattr(runtime, "restart_approval", None)
    message = approval.refusal() if approval is not None else None
    hold = getattr(approval, "retirement_hold", None)
    if message is None and hold is not None:
        message = hold.refusal()
    return message


def inputs_digest(setup: CadSolveSetup) -> str:
    """What a setup asks a solve for, without the run's name.

    Two revisions that differ only in their label make the same solve and the
    same preparation, so the in-flight check and a resumed preparation treat
    them as one.
    """

    return setup_digest(setup.model_copy(update={"label": None}))


def _revision_inputs(store: Any, revision_id: str | None, cache: dict[str, str | None]) -> str | None:
    if not revision_id:
        return None
    if revision_id not in cache:
        row = store.get_setup_revision(revision_id)
        try:
            cache[revision_id] = (
                inputs_digest(CadSolveSetup.model_validate(json.loads(row["setup_json"])))
                if row is not None else None
            )
        except (TypeError, ValueError):
            cache[revision_id] = None
    return cache[revision_id]


def recorded_setup_revision(row: Mapping[str, Any]) -> str | None:
    """The setup revision a job recorded it was made from, or None."""

    metadata = row.get("task_metadata") if isinstance(row.get("task_metadata"), Mapping) else {}
    imported = metadata.get("imported_geometry") if isinstance(metadata.get("imported_geometry"), Mapping) else {}
    setup = cad_of(row).get("setup")
    return (
        imported.get("setup_revision_id")
        or (setup.get("revision_id") if isinstance(setup, Mapping) else None)
        or None
    )


def _job_target(row: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    """``(model, snapshot manifest, setup revision)`` a job solves, as far as it says.

    A bound job states them in ``imported_geometry`` (or its CAD record); one
    still being prepared in its intent and CAD record.
    """

    metadata = row.get("task_metadata") if isinstance(row.get("task_metadata"), Mapping) else {}
    imported = metadata.get("imported_geometry") if isinstance(metadata.get("imported_geometry"), Mapping) else {}
    cad = cad_of(row)
    config = row.get("config_json") if isinstance(row.get("config_json"), Mapping) else {}
    geometry = config.get("geometry") if isinstance(config.get("geometry"), Mapping) else {}
    intent = config if config.get("type") == CAD_INTENT else {}
    snapshot = cad.get("snapshot") if isinstance(cad.get("snapshot"), Mapping) else {}
    manifest = (
        imported.get("manifest_sha256") or geometry.get("manifest_sha256")
        or intent.get("manifest_sha256") or snapshot.get("manifest_sha256")
    )
    model = imported.get("model_id") or snapshot.get("project_lineage_id")
    revision = recorded_setup_revision(row) or intent.get("setup_revision_id")
    return (model or None, manifest or None, revision or None)


def _prepared_ingest_id(row: Mapping[str, Any]) -> str | None:
    """The prepared mesh a job solves (bound) or has prepared so far, or None."""

    preparation = cad_of(row).get("preparation")
    if isinstance(preparation, Mapping) and preparation.get("ingest_id"):
        return str(preparation["ingest_id"])
    config = row.get("config_json") if isinstance(row.get("config_json"), Mapping) else {}
    geometry = config.get("geometry") if isinstance(config.get("geometry"), Mapping) else {}
    return str(geometry["ingest_id"]) if geometry.get("ingest_id") else None


def record_identity(record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Any]:
    """``(frame, domain plan)`` a prepared record was meshed under (frame None when linked)."""

    frame = record_frame_identity(record) if record_is_unlinked(record) else None
    return frame, record_plan_identity(record)


@dataclass(frozen=True)
class SolveTarget:
    """What a press asks a job to be, beyond its model and snapshot.

    ``inputs`` is the setup's content without its label; ``frame`` and
    ``domain`` are the solver frame and domain plan the solve is prepared under
    (``frame`` None: not compared, a linked model). ``adopts``: this press's
    setup becomes the model's setup, so a Fusion request still preparing on
    WG's defaults, or not yet holding any setup, will take it up.
    """

    inputs: str
    frame: dict[str, Any] | None = None
    domain: Any = None
    compare_domain: bool = False
    adopts: bool = False


def _is_defaults(store: Any, revision_id: str | None) -> bool:
    row = store.get_setup_revision(revision_id) if revision_id else None
    try:
        return row is not None and json.loads(row["setup_json"]).get("origin") == DEFAULTS_ORIGIN
    except (TypeError, ValueError, AttributeError):
        return False


def _matches(
    store: Any, row: Mapping[str, Any], model_id: str, record: Mapping[str, Any],
    target: SolveTarget, cache: dict[str, str | None], standing: list[str | None],
) -> str | None:
    """How a job solves this model's snapshot as the target asks: None (it does not),
    ``"same"`` (the same setup, frame and domain), or ``"adopting"`` (a request
    still choosing its settings, which takes up the press's setup)."""

    job_model, job_manifest, revision = _job_target(row)
    if job_manifest != record.get("manifest_sha256") or (job_model and job_model != model_id):
        return None
    prepared = _prepared_ingest_id(row)
    prepared_row = store.get_ingest(prepared) if prepared else None
    preparing = row.get("status") == "preparing"
    if preparing and prepared_row is None and target.adopts and (
        revision is None or _is_defaults(store, revision)
    ):
        # Not yet past choosing its settings: it prepares with the model's
        # setup, which this press is about to make its own.
        how = "adopting"
    else:
        how = "same"
        if revision is None and preparing:
            if not standing:
                saved = store.get_model_setup(model_id, inventory_sha256(record.get("sources") or []))
                standing.append(str(saved["revision_id"]) if saved else None)
            revision = standing[0]
        if _revision_inputs(store, revision, cache) != target.inputs:
            return None
    if prepared_row is not None:
        frame, domain = record_identity(json.loads(prepared_row["record_json"]))
        if target.frame is not None and frame != target.frame:
            return None
        if target.compare_domain and domain != target.domain:
            return None
    elif target.frame is not None:
        config = row.get("config_json") if isinstance(row.get("config_json"), Mapping) else {}
        held = config.get("frame_axis") if config.get("type") == CAD_INTENT else None
        if held and held != target.frame.get("axis"):
            return None
    return how


def active_model_match(
    runtime: Any, store: Any, model_id: str, record: Mapping[str, Any], target: SolveTarget | str
) -> tuple[str, str] | None:
    """A preparing, queued or running job solving this model's mesh as the target asks.

    The prepared mesh is compared by the snapshot it was prepared from: the
    preparation lane prepares a CAD snapshot itself, so a Fusion "Solve in WG"
    of the snapshot on screen is the same solve whichever ingest its lane made.
    Its setup is compared without the label, and its solver frame and domain
    plan with the ones this press is prepared under (an axis the user picked
    is never answered by a job meshed along another). A job still being
    prepared that names no setup yet is compared by the model's saved setup,
    or taken as this press's when the press makes its setup the model's. A
    manual solve still waiting for its first press solves nothing yet and is
    never this.
    """

    if isinstance(target, str):
        target = SolveTarget(inputs=target)
    rows, _total = runtime.store.list_jobs(statuses=list(ACTIVE_STATUSES), limit=1000)
    cache: dict[str, str | None] = {}
    standing: list[str | None] = []
    for row in rows:
        if cad_of(row).get("manual_waiting"):
            continue
        how = _matches(store, row, model_id, record, target, cache, standing)
        if how is not None:
            return str(row["id"]), how
    return None


def active_model_job(
    runtime: Any, store: Any, model_id: str, record: Mapping[str, Any], target: SolveTarget | str
) -> str | None:
    """``active_model_match``'s job, however it matched."""

    match = active_model_match(runtime, store, model_id, record, target)
    return match[0] if match is not None else None


def refused_model_job(
    runtime: Any, store: Any, model_id: str, record: Mapping[str, Any], target: SolveTarget
) -> Mapping[str, Any] | None:
    """The newest refused CAD solve a Solve continues, or None.

    One of this snapshot, waiting for the user at any gate -- its frame,
    findings, an engine, a failed or interrupted preparation -- and the latest
    of its chain, whose setup, frame and domain are the press's: a refused
    solve of other settings is left as it is and the press starts its own. A
    return WG rejected as invalid is never continued.
    """

    cache: dict[str, str | None] = {}
    for row in runtime.store.refused_cad_intents(str(record.get("manifest_sha256") or "")):
        refusal = cad_of(row).get("refusal")
        if not isinstance(refusal, Mapping) or REASON_CODES.get(str(refusal.get("code"))) == REJECTED:
            continue
        if runtime.store.job_for_submission_key(f"cad-solve-again:{row['id']}") is not None:
            continue
        if _matches(store, row, model_id, record, replace(target, adopts=False), cache, []) is not None:
            return row
    return None


def _model_and_record(store: Any, model_id: str, prepared_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = store.get_ingest(prepared_id)
    if row is None:
        raise ModelSolveRefusal(
            404, "prepared_unavailable", "This prepared mesh is no longer retained. Prepare it again."
        )
    filed = row.get("model_id") or ""
    record = json.loads(row["record_json"])
    if model_id == UNFILED_MODEL_ID and not filed:
        return {"model_id": UNFILED_MODEL_ID, "producer": record.get("producer") or "cad"}, record
    if filed != model_id:
        raise ModelSolveRefusal(409, "model_mismatch", "This prepared mesh belongs to another model.")
    model = store.get_model(model_id)
    if model is None:
        raise ModelSolveRefusal(404, "model_not_found", "Imported model not found.")
    return model, record


def _resolve_setup(store: Any, body: ModelSolveRequest) -> tuple[CadSolveSetup, str]:
    """The setup this press solves with, as a revision: the one it names, or the one it sent.

    Nothing is recorded for the model here: only an admitted press makes its
    setup the model's (``_record_for_model``).
    """

    if body.setup is None:
        row = store.get_setup_revision(str(body.setup_revision_id))
        if row is None:
            raise ModelSolveRefusal(
                409, "setup_required",
                "The settings this solve names are no longer retained. Choose them again, then solve.",
            )
        try:
            return validate_setup(json.loads(row["setup_json"])), str(row["revision_id"])
        except ValueError as exc:
            raise ModelSolveRefusal(
                409, "setup_required",
                f"The settings this solve names cannot be used: {exc}. Choose them again, then solve.",
            ) from exc
    try:
        setup = validate_setup(body.setup.model_dump(mode="json"))
    except ValueError as exc:
        raise ModelSolveRefusal(422, "setup_refused", str(exc)) from exc
    revision = store.create_setup_revision(setup_content(setup), setup_digest(setup))
    return setup, str(revision["revision_id"])


def _record_for_model(store: Any, model_id: str, record: Mapping[str, Any], revision_id: str) -> None:
    """A CAD model's setup is the one last solved in WG, for these sources.

    The next "Solve in WG" from Fusion prepares with it. A press that names a
    revision (an unedited restored run) records that one. Never for a mesh
    filed under no model.
    """

    if model_id != UNFILED_MODEL_ID:
        store.record_model_setup(model_id, inventory_sha256(record.get("sources") or []), revision_id)


# -- the press and the job it is answered with ----------------------------------

_PRESS_KEY = "model-solve-press:"


def press_digest(body: ModelSolveRequest) -> str:
    """Everything a press asked for but its id: a replay is this, anything else conflicts."""

    content = body.model_dump(mode="json", exclude={"client_request_id"})
    return "sha256:" + hashlib.sha256(canonical_utf8_strict_json(content).encode("utf-8")).hexdigest()


def _bound_press(runtime: Any, store: Any, model_id: str, body: ModelSolveRequest) -> str | None:
    """The job an earlier answer to this press id named, while it exists; a different press conflicts."""

    held = store.get_setting(_PRESS_KEY + body.client_request_id)
    if not isinstance(held, Mapping):
        return None
    if held.get("press_sha256") != press_digest(body) or held.get("model_id") != model_id:
        raise ModelSolveRefusal(
            409, "submission_key_conflict",
            "This Solve id was already used for another solve. Press Solve again.",
        )
    job_id = str(held.get("job_id") or "")
    return job_id if job_id and runtime.store.get_job_row(job_id) is not None else None


def _bind_press(store: Any, model_id: str, body: ModelSolveRequest, job_id: str) -> str:
    """Remember which job answered this press id, however it was answered.

    A job the press was deduplicated to (one in flight) has no key of this
    press, so without this a replay after that job ended would start another.
    """

    store.set_setting(_PRESS_KEY + body.client_request_id, {
        "job_id": job_id, "press_sha256": press_digest(body), "model_id": model_id,
    })
    return job_id


def _lane_target(
    store: Any, data_dir: Path, record: Mapping[str, Any], inputs: str, *, adopts: bool
) -> SolveTarget | None:
    """The frame and domain plan the preparation lane would prepare this snapshot under now.

    Read as the lane reads them, from WG's retained copy, after the press's
    axis is confirmed. None when the retained copy cannot be read: nothing can
    then be known to be the same solve.
    """

    manifest_sha256 = str(record.get("manifest_sha256") or "")
    try:
        manifest = read_snapshot(retained_snapshot_path(data_dir, manifest_sha256), retained=True).manifest
    except (OSError, ValueError):
        return None
    solver_frame = resolve_for_manifest(store, manifest, manifest_sha256, automatic=True)
    return SolveTarget(
        inputs=inputs,
        frame=resolution_identity(solver_frame) if solver_frame is not None else None,
        domain=resolve_domain_plan(store, manifest, manifest_sha256).identity(),
        compare_domain=True,
        adopts=adopts,
    )


def _request_revision(store: Any, request: SolveRequest, record: Mapping[str, Any] | None) -> str | None:
    """The setup revision an exact imported request is, stored once (content-named).

    None when the request is not a setup, or when its setup cannot be known
    exactly without the prepared record (``setup_of_request``): no revision
    is better than one that solves differently.
    """

    content, exact = setup_of_request(request.model_dump(mode="json"), record)
    if not exact:
        return None
    try:
        setup = validate_setup(content)
    except ValueError:
        return None
    return str(store.create_setup_revision(setup_content(setup), setup_digest(setup))["revision_id"])


def refusal_from(exc: BaseException) -> ModelSolveRefusal:
    """A jobs-system refusal of the request, as the envelope a Solve answers with."""

    if isinstance(exc, ModelSolveRefusal):
        return exc
    if isinstance(exc, SubmissionConflictError):
        return ModelSolveRefusal(409, "submission_key_conflict", str(exc))
    if isinstance(exc, EngineUnavailableError):
        return ModelSolveRefusal(503, "engine_unavailable", str(exc))
    if isinstance(exc, ImportedSolveRefusal):
        code = exc.reason_code
        if code == FRAME_CONFIRMATION_REQUIRED:
            return ModelSolveRefusal(409, FRAME_CONFIRMATION_REQUIRED, str(exc), details=exc.details)
        if code == "unacknowledged_findings":
            return ModelSolveRefusal(409, "findings_need_review", str(exc), details=exc.details)
        if code in _PREPARED_GONE:
            return ModelSolveRefusal(409, "prepared_unavailable", str(exc), details=exc.details)
        return ModelSolveRefusal(422, code, str(exc), details=exc.details)
    if isinstance(exc, UnknownEngineError):
        return ModelSolveRefusal(422, "unknown_engine", str(exc))
    if isinstance(exc, RemovedSolverError):
        return ModelSolveRefusal(422, exc.code, str(exc))
    if isinstance(exc, SymmetryValidationError):
        return ModelSolveRefusal(422, "invalid_symmetry", str(exc))
    return ModelSolveRefusal(422, "submission_refused", str(exc))


SUBMISSION_REFUSALS: tuple[type[BaseException], ...] = (
    SubmissionConflictError, EngineUnavailableError, ImportedSolveRefusal,
    UnknownEngineError, RemovedSolverError, SymmetryValidationError,
)


def _refuse_restart(runtime: Any) -> None:
    message = restart_refusal(runtime)
    if message is not None:
        raise ModelSolveRefusal(409, REASON_UPDATE_RESTART_PENDING, message, retryable=True)


# -- a file model ----------------------------------------------------------------


def _acknowledged(record: Mapping[str, Any], body: ModelSolveRequest) -> list[str]:
    """The prepared mesh's blocking findings the press reviewed; refused while any is not."""

    blocking = [
        str(finding.get("id"))
        for finding in record.get("findings") or []
        if isinstance(finding, Mapping) and finding.get("blocking")
    ]
    reviewed = (
        set(body.approvals.finding_ids)
        if body.approvals is not None and body.approvals.preparation_id == body.prepared_id
        else set()
    )
    missing = [finding for finding in blocking if finding not in reviewed]
    if missing:
        raise ModelSolveRefusal(
            409, "findings_need_review",
            "Review the preparation's findings before solving: " + ", ".join(missing),
            details={"preparation_id": body.prepared_id, "finding_ids": missing},
        )
    return [f"{record.get('report_sha256')}:{finding}" for finding in blocking]


async def _solve_file(
    runtime: Any, store: Any, model_id: str, body: ModelSolveRequest, record: Mapping[str, Any]
) -> str:
    key = f"model-solve:{body.client_request_id}"
    existing = await asyncio.to_thread(runtime.store.job_for_submission_key, key)
    if existing is None:
        _refuse_restart(runtime)
    setup, revision_id = await asyncio.to_thread(_resolve_setup, store, body)
    try:
        request = compose_solve_request(
            setup, record, acknowledged_findings=_acknowledged(record, body),
            client_request_id=key, label=body.label,
        )
    except ValueError as exc:
        if isinstance(exc, ModelSolveRefusal):
            raise
        raise ModelSolveRefusal(422, "setup_refused", str(exc)) from exc
    async with _model_lock(model_id):
        if existing is None:
            target = SolveTarget(inputs=inputs_digest(setup), frame=record_identity(record)[0],
                                 domain=record_identity(record)[1], compare_domain=True)
            active = await asyncio.to_thread(active_model_job, runtime, store, model_id, record, target)
            if active is not None:
                return active
        try:
            return await runtime.submit(request, setup_revision_id=revision_id)
        except SUBMISSION_REFUSALS as exc:
            raise refusal_from(exc) from exc


# -- a CAD model -----------------------------------------------------------------


def _press_frame(store: Any, record: Mapping[str, Any], frame_axis: str | None) -> str | None:
    """Confirm the axis the press showed for an unlinked model; the solve is held to it.

    A linked model is solved in its design's frame and names none. With no
    axis shown, the model solves along its project's confirmed frame or WG's
    confident automatic one, and is refused when it has neither.
    """

    if not record_is_unlinked(record):
        return None
    preview = frame_preview(store, record)
    if frame_axis is None:
        if preview.get("preselected") is None:
            raise ModelSolveRefusal(
                409, FRAME_CONFIRMATION_REQUIRED,
                "Choose which way this model radiates, in the CAD Link panel, then press Solve.",
            )
        return None
    confirmed = preview.get("confirmed")
    if not isinstance(confirmed, Mapping) or confirmed.get("axis") != frame_axis:
        try:
            confirm_frame(store, record, frame_axis)
        except FrameConfirmationError as exc:
            raise ModelSolveRefusal(409, FRAME_CONFIRMATION_REQUIRED, str(exc)) from exc
    return frame_axis


def _press(body: ModelSolveRequest, revision_id: str, frame_axis: str | None) -> dict[str, Any]:
    return {
        "setup_revision_id": revision_id,
        "frame_axis": frame_axis,
        "approve_preparation_id": body.approvals.preparation_id if body.approvals else None,
        "approve_finding_ids": tuple(body.approvals.finding_ids) if body.approvals else (),
        "submit": True,
    }


async def _solve_cad(
    state: Any, runtime: Any, store: Any, model_id: str, body: ModelSolveRequest,
    record: Mapping[str, Any],
) -> str:
    from server.cadlink.api import accept_manual_solve

    operation_id = f"manual-solve:{body.client_request_id}"
    started = await asyncio.to_thread(runtime.store.job_for_submission_key, f"cad-solve:{operation_id}")
    continued = (
        None if started is not None
        else await asyncio.to_thread(runtime.store.job_for_model_solve, body.client_request_id)
    )
    if continued is not None:
        # A replay of a press that continued a refused solve: that press's job.
        row = await asyncio.to_thread(runtime.store.get_job_row, continued)
        if row is None or _job_target(row)[1] != record.get("manifest_sha256"):
            raise ModelSolveRefusal(
                409, "submission_key_conflict", "This Solve id already names another model's solve."
            )
        return continued
    if started is not None:
        row = await asyncio.to_thread(runtime.store.get_job_row, started)
        if row is None or not cad_of(row).get("manual_waiting"):
            # A replay recovers the original job, however it ended, without
            # another press, retention or an open restart latch.
            try:
                return await accept_manual_solve(state, operation_id, body.prepared_id)
            except OperationConflict as exc:
                raise ModelSolveRefusal(409, "submission_key_conflict", str(exc)) from exc
    else:
        _refuse_restart(runtime)
    setup, revision_id = await asyncio.to_thread(_resolve_setup, store, body)
    inputs = inputs_digest(setup)
    adopts = model_id != UNFILED_MODEL_ID
    async with _model_lock(model_id):
        # The axis the press showed is confirmed first: the frame it is
        # compared and prepared under is then the one the user chose.
        frame_axis = await asyncio.to_thread(_press_frame, store, record, body.frame_axis)
        press = _press(body, revision_id, frame_axis)
        target = await asyncio.to_thread(
            _lane_target, store, Path(state.data_dir), record, inputs, adopts=adopts
        )
        if started is None and target is not None:
            match = await asyncio.to_thread(active_model_match, runtime, store, model_id, record, target)
            if match is not None:
                active, how = match
                if how == "adopting":
                    # A request still choosing its settings (a Fusion "Solve in
                    # WG" of a first-time model): it takes up this press's
                    # setup, by recording it as the model's.
                    await asyncio.to_thread(_record_for_model, store, model_id, record, revision_id)
                return active
        refused = None if started is not None or target is None else await asyncio.to_thread(
            refused_model_job, runtime, store, model_id, record, target
        )
        if refused is not None:
            held = _job_target(refused)[2]
            if held and held != revision_id and _revision_inputs(store, held, {}) == inputs:
                # The same settings under another name: keep the revision the
                # refused solve holds, so its preparation (and the approvals on
                # it) is resumed rather than made again.
                press["setup_revision_id"] = held
            try:
                job_id = await runtime.solve_cad_again(
                    str(refused["id"]), **press, request_id=body.client_request_id, label=body.label,
                )
            except JobConflictError as exc:
                raise ModelSolveRefusal(409, "submission_key_conflict", str(exc)) from exc
            await asyncio.to_thread(_record_for_model, store, model_id, record, revision_id)
            return job_id
        try:
            job_id = await accept_manual_solve(state, operation_id, body.prepared_id)
        except UnknownIngest as exc:
            raise ModelSolveRefusal(404, "prepared_unavailable", str(exc)) from exc
        except SnapshotNotRetained as exc:
            raise ModelSolveRefusal(409, "prepared_unavailable", str(exc)) from exc
        except OperationConflict as exc:
            raise ModelSolveRefusal(409, "submission_key_conflict", str(exc)) from exc
        row = await asyncio.to_thread(runtime.store.get_job_row, job_id)
        if row is not None and cad_of(row).get("manual_waiting"):
            await runtime.prepare_cad_solve(job_id, **press, label=body.label)
        await asyncio.to_thread(_record_for_model, store, model_id, record, revision_id)
        return job_id


# -- the entry points ------------------------------------------------------------


async def solve_model(state: Any, model_id: str, body: ModelSolveRequest) -> str:
    """Solve one imported model as the press showed it; the job id (an existing one in flight)."""

    runtime = state.jobs_runtime
    store = state.cadlink_store
    await runtime.start()
    bound = await asyncio.to_thread(_bound_press, runtime, store, model_id, body)
    if bound is not None:
        return bound
    model, record = await asyncio.to_thread(_model_and_record, store, model_id, body.prepared_id)
    if model.get("producer") == FILE_PRODUCER:
        job_id = await _solve_file(runtime, store, model_id, body, record)
    else:
        job_id = await _solve_cad(state, runtime, store, model_id, body, record)
    return await asyncio.to_thread(_bind_press, store, model_id, body, job_id)


async def submit_imported_request(
    runtime: Any,
    request: SolveRequest,
    *,
    cad_provenance: Mapping[str, Any] | None = None,
    setup_revision_id: str | None = None,
) -> str:
    """Submit an exact imported request as its model's solve.

    The adapter for ``POST /api/solve`` with imported geometry and for a retry
    of an imported run: the request is kept exactly as given (nothing is
    rebuilt from a rail), recorded as the setup revision it is unless one is
    named, and held to the same in-flight rule as a Solve of the model. A
    prepared mesh filed under no model is submitted as it is. Raises what
    ``JobRuntime.submit`` raises.
    """

    geometry = request.geometry
    assert isinstance(geometry, ImportedGeometrySource)
    await runtime.start()
    store = runtime.cadlink_store
    row = await asyncio.to_thread(store.get_ingest, geometry.ingest_id) if store is not None else None
    if row is None:
        return await runtime.submit(request, cad_provenance=cad_provenance, setup_revision_id=setup_revision_id)
    record = json.loads(row["record_json"])
    if setup_revision_id is None and not (cad_provenance or {}).get("setup"):
        setup_revision_id = await asyncio.to_thread(_request_revision, store, request, record)
    model_id = row.get("model_id") or None
    if model_id is None:
        return await runtime.submit(request, cad_provenance=cad_provenance, setup_revision_id=setup_revision_id)
    revision = setup_revision_id or recorded_setup_revision({"task_metadata": {"cad": dict(cad_provenance or {})}})
    async with _model_lock(model_id):
        replay = request.client_request_id is not None and await asyncio.to_thread(
            runtime.store.job_for_submission_key, request.client_request_id
        ) is not None
        inputs = _revision_inputs(store, revision, {})
        if not replay and inputs is not None:
            frame, domain = record_identity(record)
            target = SolveTarget(inputs=inputs, frame=frame, domain=domain, compare_domain=True)
            active = await asyncio.to_thread(active_model_job, runtime, store, model_id, record, target)
            if active is not None:
                return active
        return await runtime.submit(request, cad_provenance=cad_provenance, setup_revision_id=setup_revision_id)

__all__ = [
    "ModelSolveRefusal",
    "ModelSolveRequest",
    "SUBMISSION_REFUSALS",
    "SolveTarget",
    "UNFILED_MODEL_ID",
    "active_model_job",
    "active_model_match",
    "inputs_digest",
    "press_digest",
    "record_identity",
    "recorded_setup_revision",
    "refusal_from",
    "refused_model_job",
    "restart_refusal",
    "solve_model",
    "submit_imported_request",
]
