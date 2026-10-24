"""Remembered frame and domain readings for received CAD snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import sys
from typing import Any, TYPE_CHECKING

import numpy as np

from server.cadlink.operations import AXIS_NOT_ALLOWED_NEXT_STEP, axis_not_allowed_message
from server.cadlink.wgreturn import cut_provenance, declared_domain_planes, domain_kind
from server.platform.incidents import record_incident

from server.cadlink.interpretation.solver_frame import (
    AS_MODELLED,
    AXES,
    CONTRACT,
    CONTRACT_V1,
    CONTRACT_V2,
    FrameConfirmationError,
    FrameResolution,
    ROTATIONS,
    allowed_axes,
    carried_axis,
    confirmation_key,
    document_up,
    frame_requirement,
    is_unlinked_manifest,
    manual_choice,
    record_confirmation_key,
    record_frame,
    record_is_unlinked,
    require_axis,
    resolve_up,
    spec_matrix,
)

from server.cadlink.interpretation.domain_interpretation import (
    DECLARATION,
    DomainPlan,
    DomainReadingError,
    LINEAGE,
    PLANES,
    PROVENANCE,
    READING_AS_SHOWN,
    READING_REDUCED,
    SUPPORTED_PLANES,
    USER,
    USER_LINEAGE,
    ORIGIN_PLANE,
    export_frame,
    plane_words,
    record_key,
)

if TYPE_CHECKING:  # pragma: no cover
    from server.cadlink.store import CadLinkStore


logger = logging.getLogger(__name__)


#: Evidence keys that identify the request rather than describe the failure.
_DIAGNOSTIC_IDS = ("job_id", "operation_id", "ingest_id", "preparation_id", "manifest_sha256")


def record_preparation_diagnostic(
    stage: str, evidence: Mapping[str, Any], *, chosen_axis: str | None = None,
    phase: str = "cad-preparation", exception: BaseException | None = None,
) -> str | None:
    """Record a developer diagnostic: one incident record, and the log line.

    ``stage`` is the incident's kind. The exception is the one being handled
    when none is named, so a catch-all's stack is kept without passing it. The
    log line (``extra["cad_diagnostic"]``) stays: the replay tool reads it.
    Returns the incident id, or ``None`` when no store is configured.
    """

    failure = exception if exception is not None else sys.exc_info()[1]
    diagnostic = {"stage": stage, "evidence": dict(evidence), "chosen_axis": chosen_axis}
    incident = record_incident(
        stage, phase,
        ids={key: evidence.get(key) for key in _DIAGNOSTIC_IDS},
        exception=failure if isinstance(failure, Exception) else None,
        evidence={**dict(evidence), "chosen_axis": chosen_axis},
    )
    if incident is not None:
        diagnostic["incident"] = incident
    logger.warning("CAD preparation diagnostic: %s", diagnostic, extra={"cad_diagnostic": diagnostic})
    return incident


def solver_anchor(manifest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A snapshot's solver anchor instance: the one its frame names, else its only one.

    The project a snapshot belongs to (``snapshot_project``) and the design that
    backend preparation names to the ingest's project gate
    (``preparation._project_gate``) both come from this, so they cannot disagree.
    """

    instances = [
        instance for instance in manifest.get("instances") or [] if isinstance(instance, Mapping)
    ]
    frame = manifest.get("coordinate_system")
    anchor_id = frame.get("solver_anchor_instance_id") if isinstance(frame, Mapping) else None
    anchor = next(
        (instance for instance in instances if instance.get("instance_id") == anchor_id), None
    )
    if anchor is None and len(instances) == 1:
        anchor = instances[0]
    return anchor


def snapshot_project(store: CadLinkStore, manifest: Mapping[str, Any]) -> str | None:
    """The project lineage a snapshot belongs to, without claiming one.

    The same project ingestion files the return under (``ingest._resolve_project``):
    the lineage of the solver anchor instance's WG design, or, when the anchor
    names no design, the lineage its Fusion document already has. None when WG
    has never seen that document: such a snapshot has no recorded setup yet.
    """

    anchor = solver_anchor(manifest)
    design_id = str((anchor or {}).get("design_id") or "").strip()
    if design_id:
        row = store.get_design(design_id)
        return str((row or {}).get("lineage_id") or "").strip() or None
    document = manifest.get("document") if isinstance(manifest.get("document"), Mapping) else {}
    native_id = str(document.get("native_id") or "").strip()
    if not native_id:
        return None
    row = store.get_lineage_for_cad_document(native_id)
    return str((row or {}).get("lineage_id") or "").strip() or None


def _confirmed_axis(
    store: CadLinkStore, key: str, requirement: Mapping[str, Any]
) -> str | None:
    row = store.get_frame_confirmation(key)
    if row is None or row.get("requirement") != dict(requirement):
        return None
    axis = row.get("axis")
    return axis if axis in ROTATIONS else None


def resolve_for_manifest(
    store: CadLinkStore,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    *,
    automatic: bool = False,
) -> FrameResolution | None:
    """The frame state of a snapshot about to be prepared; None when it is linked.

    ``automatic`` meshes in WG's own decision (``frame_infer.choose_axis``:
    the confident axis, else the fallback ranking's first allowed axis) when
    no manual choice stands. It always decides; nothing asks.
    """

    if not is_unlinked_manifest(manifest):
        return None
    key = confirmation_key(snapshot_project(store, manifest), manifest_sha256)
    requirement = frame_requirement(manifest)
    allowed = allowed_axes(manifest)
    confirmed = _confirmed_axis(store, key, requirement)
    carried = carried_axis(store.get_frame_confirmation(key), requirement)
    automatic_axis = None
    if automatic and manual_choice(confirmed, carried) is None:
        from server.cadlink.interpretation.frame_infer import choose_axis

        automatic_axis = choose_axis(
            store.get_frame_suggestion(manifest_sha256, _algorithm_version()), allowed
        ).axis
    return FrameResolution(
        key=key,
        requirement=requirement,
        allowed_axes=allowed,
        confirmed_axis=confirmed,
        document_up=document_up(manifest),
        carried_axis=carried,
        automatic_axis=automatic_axis,
    )


def _algorithm_version() -> str:
    from server.cadlink.interpretation.frame_infer import ALGORITHM_VERSION

    return ALGORITHM_VERSION


def _record_manual_choice(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """The axis a person chose for this record's project, allowed or not (:func:`manual_choice`)."""

    frame = record_frame(record)
    if frame is None:
        return None
    key = record_confirmation_key(record)
    requirement = dict(frame["requirement"])
    return manual_choice(
        _confirmed_axis(store, key, requirement),
        carried_axis(store.get_frame_confirmation(key), requirement),
    )


def _manual_axis(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """The axis a person chose for this record's project, when its domain allows it.

    A choice a half or quarter model cannot be solved along does not stand,
    and is never replaced by another axis either: the preparation refuses it
    (:func:`frame_axis_not_allowed`).
    """

    frame = record_frame(record)
    choice = _record_manual_choice(store, record)
    return choice if frame is not None and choice in _record_allowed(frame) else None


def frame_axis_not_allowed(
    store: CadLinkStore, record: Mapping[str, Any], suggestion: Mapping[str, Any] | None
) -> str | None:
    """The radiation axis this record's domain cannot be solved along, or None.

    A person's choice (confirmed, or carried from v1) the domain does not
    allow; with no choice, an axis the survey is confident about and the
    domain does not allow (``frame_infer.confident_unallowed_axis``). Never
    solved along another axis instead: the preparation ends calmly with
    ``reduced_axis_not_allowed``. None for a linked record and one stating no
    frame.
    """

    from server.cadlink.interpretation.frame_infer import confident_unallowed_axis

    if not record_is_unlinked(record):
        return None
    frame = record_frame(record)
    if frame is None:
        return None
    allowed = _record_allowed(frame)
    choice = _record_manual_choice(store, record)
    if choice is not None:
        return None if choice in allowed else choice
    return confident_unallowed_axis(suggestion, allowed)


def legacy_automatic_axis(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """The confident automatic axis a record prepared before stored decisions was solved in.

    Records prepared before :func:`store_frame_decision` existed carry no
    decision. Each was solved along the confident verdict of the survey of
    its time, which its snapshot's cache still holds (``frame-infer-v1`` for
    released 0.3.5, ``-v2``, the unreleased ``-v3``), or of the current one;
    only a confident verdict naming the axis the record was meshed in counts,
    never a weak one. The record's own frame (its v1 or
    v2 matrix) is what it solves in; nothing is re-meshed.
    """

    from server.cadlink.interpretation.frame_infer import (
        ALGORITHM_VERSION,
        LEGACY_ALGORITHM_VERSIONS,
        STATUS_AUTOMATIC,
    )

    frame = record_frame(record)
    snapshot = _snapshot(record)
    if frame is None or not snapshot:
        return None
    allowed = _record_allowed(frame)
    for algorithm in (*LEGACY_ALGORITHM_VERSIONS, ALGORITHM_VERSION):
        suggestion = store.get_frame_suggestion(snapshot, algorithm)
        if (
            suggestion is not None
            and suggestion.get("status") == STATUS_AUTOMATIC
            and suggestion.get("axis") == frame["axis"]
            and frame["axis"] in allowed
        ):
            return str(frame["axis"])
    return None


def record_frame_decision(
    store: CadLinkStore, record: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The automatic decision this record is solved by, or None.

    One canonical answer for admission, execution, retry and restart: the
    decision stored for this exact record when it was prepared
    (:func:`store_frame_decision`); for a record prepared before decisions
    were stored, its snapshot's confident verdict of the time
    (:func:`legacy_automatic_axis`). Never re-derived from today's survey.
    None for a linked record, one stating no frame, and one whose project
    (or snapshot) chose its axis -- a person's choice wins.
    """

    if not record_is_unlinked(record) or record_frame(record) is None:
        return None
    if _record_manual_choice(store, record) is not None:
        return None
    stored = store.get_frame_decision(str(record.get("ingest_id") or ""))
    if stored is not None:
        return stored
    legacy = legacy_automatic_axis(store, record)
    if legacy is not None:
        return {"axis": legacy, "provenance": "automatic", "reasonCode": "inferred", "legacy": True}
    return None


def decide_record_frame(
    store: CadLinkStore, record: Mapping[str, Any], suggestion: Mapping[str, Any] | None
) -> Any:
    """The axis WG would solve this record's snapshot along when nobody chose one.

    ``frame_infer.choose_axis`` over the record's allowed axes: the confident
    answer, else the fallback ranking's first allowed axis. None when a person
    chose (or a v1 choice is carried), for a linked record, and for one
    stating no frame.
    """

    from server.cadlink.interpretation.frame_infer import choose_axis

    if not record_is_unlinked(record):
        return None
    frame = record_frame(record)
    # A person's choice wins; one the domain cannot use is refused, never decided over.
    if frame is None or _record_manual_choice(store, record) is not None:
        return None
    return choose_axis(suggestion, _record_allowed(frame))


def store_frame_decision(
    store: CadLinkStore,
    record: Mapping[str, Any],
    choice: Any,
    suggestion: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Seal ``choice`` as this record's decision; the record must be meshed along it.

    Written once per record, at preparation. A fallback is recorded as such
    (``automatic-fallback``), with a developer diagnostic, never as confident
    inference.
    """

    from server.cadlink.interpretation.frame_infer import ALGORITHM_VERSION, DECISION_FALLBACK

    frame = record_frame(record)
    if frame is None or frame.get("axis") != choice.axis:
        raise ValueError("a frame decision is stored only for the record meshed along it")
    ingest_id = str(record.get("ingest_id") or "")
    held = store.get_frame_decision(ingest_id)
    if held is not None:
        return held
    summary = suggestion or {}
    decision = {
        **choice.to_json(),
        "algorithm": ALGORITHM_VERSION,
        "snapshotSha256": _snapshot(record),
        "suggestion": {
            "algorithm": summary.get("algorithm"),
            "status": summary.get("status"),
            "axis": summary.get("axis"),
            "reasonCode": summary.get("reasonCode"),
            "confidence": summary.get("confidence"),
        } if suggestion is not None else None,
        "allowedAxes": list(_record_allowed(frame)),
    }
    stored = store.record_frame_decision(ingest_id, decision)
    if stored.get("provenance") == DECISION_FALLBACK:
        record_preparation_diagnostic(
            "frame-fallback",
            {
                "ingest_id": ingest_id,
                "reason_code": stored.get("reasonCode"),
                "suggestion": stored.get("suggestion"),
                "allowed_axes": stored.get("allowedAxes"),
                "fallback_ranking": list(summary.get("fallbackRanking") or []),
            },
            chosen_axis=str(stored.get("axis")),
        )
    return stored


def record_automatic_axis(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """The axis this record solves along by WG's own decision, or None."""

    decision = record_frame_decision(store, record)
    return str(decision["axis"]) if decision is not None else None


def record_solved_frame_provenance(
    store: CadLinkStore, record: Mapping[str, Any]
) -> str | None:
    """How WG itself decided the axis this record solves along, or None.

    ``automatic`` (confident inference) or ``automatic-fallback`` (the
    documented ranking chose), for a record solving along its stored decision
    with nobody's choice behind it.
    """

    frame = record_frame(record) if record_is_unlinked(record) else None
    if frame is None:
        return None
    decision = record_frame_decision(store, record)
    if decision is None or decision.get("axis") != frame.get("axis"):
        return None
    return str(decision.get("provenance") or "automatic")


def _frame_for_axis(frame: Mapping[str, Any], axis: str) -> dict[str, Any]:
    """The complete frame ``axis`` would be under this record's contract."""

    if frame.get("contract") == CONTRACT_V1:
        return {"contract": CONTRACT_V1, "axis": axis}
    stated = frame.get("requirement", {}).get("document_up")
    up, source = resolve_up(axis, stated)
    return {
        "contract": CONTRACT_V2,
        "axis": axis,
        "up": up,
        "up_source": source,
        "document_up": stated,
    }


def _record_allowed(frame: Mapping[str, Any]) -> tuple[str, ...]:
    allowed = frame.get("allowed_axes")
    if not isinstance(allowed, list):
        return (AS_MODELLED,)
    return tuple(axis for axis in AXES if axis in allowed)


def record_frame_refusal(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """Why the prepared frame cannot be solved, or None for a usable frame.

    A linked record is never gated. An unlinked record solves in the frame it
    was meshed in when that is its project's (or, with no project, this
    snapshot's) manual choice under the same requirement -- a v1 choice
    carried forward included -- or WG's own decision stored for exactly this
    record at preparation (:func:`record_frame_decision`), the one decision
    every submission, retry and restart reads. Nothing here asks: every
    refusal is an internal inconsistency that preparing again resolves. A
    record prepared before the frame contract states no frame and is never
    taken as decided.
    """

    if not record_is_unlinked(record):
        return None
    frame = record_frame(record)
    if frame is None:
        return (
            "This model was prepared before WG recorded its solver frame. "
            "Prepare it again in WG."
        )
    blocked = frame_axis_not_allowed(store, record, None)
    if blocked is not None:
        return axis_not_allowed_message(None, blocked)
    manual = _manual_axis(store, record)
    if manual is not None:
        if manual == frame["axis"]:
            return None
        return (
            f"This model was prepared along {frame['axis']}, but its solver frame is "
            f"{manual}. Prepare it again in WG to solve it in that frame."
        )
    decision = record_frame_decision(store, record)
    if decision is not None and decision.get("axis") == frame["axis"]:
        # WG's own decision for this record: never written as a confirmation.
        return None
    if decision is not None:
        return (
            f"This model was prepared along {frame['axis']}, but WG decided to solve "
            f"it along {decision.get('axis')}. Prepare it again in WG."
        )
    # A record an earlier build prepared without recording how it chose its
    # axis is prepared again; WG then decides.
    return (
        "WG has no record of how it chose this model's solver frame when it "
        "was prepared. Prepare it again."
    )


def confirm_frame(
    store: CadLinkStore, record: Mapping[str, Any], axis: object
) -> dict[str, Any]:
    """Record the user's confirmation for the project an ingestion record belongs to.

    The row records the complete transform the confirmed axis means under this
    record's contract, its up and where that came from, and the automatic
    suggestion it agreed with or overrode.
    """

    if not record_is_unlinked(record):
        raise FrameConfirmationError("a linked model is solved in its WG design's frame")
    frame = record_frame(record)
    if frame is None:
        raise FrameConfirmationError(
            "this model was prepared before WG asked for its solver frame; prepare it again"
        )
    try:
        chosen = require_axis(axis)
    except ValueError as exc:
        raise FrameConfirmationError(str(exc)) from exc
    if chosen not in _record_allowed(frame):
        raise FrameConfirmationError(
            "this model is solved as a half or quarter model, whose mirror planes "
            f"contain only {', '.join(_record_allowed(frame))}"
        )
    spec = _frame_for_axis(frame, chosen)
    suggestion = _cached_suggestion(store, record)
    confirmed_frame = {
        **spec,
        "matrix": spec_matrix(spec).tolist(),
        "provenance": (
            "suggested"
            if suggestion is not None
            and suggestion.get("status") == "automatic"
            and suggestion.get("axis") == chosen
            else "chosen"
        ),
        "suggestion": (
            {
                "algorithm": suggestion.get("algorithm"),
                "status": suggestion.get("status"),
                "axis": suggestion.get("axis"),
                "confidence": suggestion.get("confidence"),
                "snapshotSha256": suggestion.get("snapshotSha256"),
            }
            if suggestion is not None
            else None
        ),
    }
    return store.record_frame_confirmation(
        record_confirmation_key(record), dict(frame["requirement"]), chosen, frame=confirmed_frame
    )


def frame_provenance(
    store: CadLinkStore, record: Mapping[str, Any], *, automatic: bool = False
) -> dict[str, Any]:
    """The frame provenance a job records, read from the record it solves.

    ``{axis, provenance, confirmed, requirement}``, plus ``suggestion`` (the
    cached survey, when there is one). ``confirmed`` is true only when a person
    confirmed the axis, so a chosen or suggested frame is distinguishable from
    one nobody confirmed.

    ``automatic`` is the caller's statement that WG solved in its own
    automatic axis. This function holds no rule for it: the one definition is
    ``record_solved_frame_provenance`` (the same value the operation records as
    ``frame_axis_automatic``), and a person's confirmation always wins.
    """

    if not record_is_unlinked(record):
        return {"axis": None, "provenance": "linked", "confirmed": False, "requirement": None}
    frame = record_frame(record)
    if frame is None:
        return {"axis": None, "provenance": "unconfirmed", "confirmed": False, "requirement": None}
    axis = frame["axis"]
    requirement = dict(frame["requirement"])
    row = store.get_frame_confirmation(record_confirmation_key(record))
    suggestion = _cached_suggestion(store, record)
    provenance = "unconfirmed"
    confirmed = False
    if row is not None and row.get("requirement") == requirement and row.get("axis") == axis:
        held = (row.get("frame") or {}).get("provenance")
        provenance = held if held in ("chosen", "suggested") else "confirmed"
        confirmed = True
    elif automatic:
        # The user-facing provenance is WG's automatic axis either way; how it
        # was decided (confident or fallback) is the decision's own field.
        provenance = "automatic"
    elif carried_axis(row, requirement) == axis:
        provenance = "carried"
    out: dict[str, Any] = {
        "axis": axis,
        "provenance": provenance,
        "confirmed": confirmed,
        "requirement": requirement,
    }
    if suggestion is not None:
        out["suggestion"] = {
            "algorithm": suggestion.get("algorithm"),
            "status": suggestion.get("status"),
            "axis": suggestion.get("axis"),
            "confidence": suggestion.get("confidence"),
        }
    if provenance == "automatic":
        decision = record_frame_decision(store, record)
        if decision is not None:
            out["decision"] = {
                key: decision.get(key)
                for key in ("provenance", "reasonCode", "algorithm", "legacy")
                if decision.get(key) is not None
            }
    return out


def _snapshot(record: Mapping[str, Any]) -> str:
    return str(record.get("manifest_sha256") or "")


def _cached_suggestion(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any] | None:
    from server.cadlink.interpretation.frame_infer import ALGORITHM_VERSION

    snapshot = _snapshot(record)
    if not snapshot:
        return None
    return store.get_frame_suggestion(snapshot, ALGORITHM_VERSION)


def ensure_frame_suggestion(
    store: CadLinkStore, record: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The automatic suggestion for this record's snapshot, computed once and cached.

    Run inside the explicit import and preparation commands. Keyed by the
    snapshot and the algorithm version, so a new algorithm recomputes and a
    confirmation is never touched. A survey that could not run is returned
    but not cached, so the next command tries again. None for a linked record.
    """

    if not record_is_unlinked(record):
        return None
    from server.mesh.artifact import read_verified_import_mesh

    from server.cadlink.interpretation.frame_infer import STATUS_UNAVAILABLE, infer_record_frame, unavailable

    cached = _cached_suggestion(store, record)
    if cached is not None:
        return cached
    frame = record_frame(record)
    allowed = _record_allowed(frame) if frame is not None else ()
    try:
        result = infer_record_frame(record, read_verified_import_mesh(record), supported_axes=allowed)
    except Exception as exc:  # noqa: BLE001 - the survey only ever advises
        logger.warning("Solver frame survey failed for %s: %s", record.get("ingest_id"), exc)
        result = unavailable(str(exc) or type(exc).__name__, supported_axes=allowed)
    payload = {
        **result.to_json(),
        "snapshotSha256": _snapshot(record),
        "ingestId": record.get("ingest_id"),
    }
    if result.status != STATUS_UNAVAILABLE and payload["snapshotSha256"]:
        return store.record_frame_suggestion(
            payload["snapshotSha256"], result.algorithm, payload, str(record.get("ingest_id") or "")
        )
    return payload


def _differs_notice(confirmed_axis: str | None, suggestion: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if (
        confirmed_axis is None
        or suggestion is None
        or suggestion.get("status") != "automatic"
        or suggestion.get("axis") in (None, confirmed_axis)
    ):
        return None
    suggested = str(suggestion["axis"])
    return {
        "confirmedAxis": confirmed_axis,
        "suggestedAxis": suggested,
        "message": (
            f"This version looks like it faces {suggested}; this project is set to "
            f"{confirmed_axis}. WG keeps {confirmed_axis} until you switch."
        ),
    }


def _decided_preselection(
    store: CadLinkStore, record: Mapping[str, Any], suggestion: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The axis WG solves this record along when nobody chose one, and how.

    The decision stored for this record when it was prepared, else the one
    :func:`decide_record_frame` makes now -- ``frame_infer.choose_axis``, the
    same owner a preparation seals. ``suggested``: confident inference;
    ``fallback``: the documented ranking chose. None for a record stating no
    frame (preparing it again decides).
    """

    from server.cadlink.interpretation.frame_infer import DECISION_AUTOMATIC

    decision = record_frame_decision(store, record)
    if decision is not None:
        axis, provenance = decision.get("axis"), decision.get("provenance") or DECISION_AUTOMATIC
    else:
        choice = decide_record_frame(store, record, suggestion)
        if choice is None:
            return None
        axis, provenance = choice.axis, choice.provenance
    if axis not in AXES:
        return None
    return {"axis": axis, "source": "suggested" if provenance == DECISION_AUTOMATIC else "fallback"}


def frame_preview(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any]:
    """Every axis's matrix, relative to the frame this record's geometry is shown in.

    Also the automatic suggestion for the snapshot, which axis is preselected
    and why -- a matching confirmed frame first, then a v1 confirmation
    carried forward, then WG's own decision (:func:`_decided_preselection`),
    which is never a question -- and, when the suggestion clearly
    disagrees with the confirmed frame, a notice that never changes it.
    """

    if not record_is_unlinked(record):
        return {"linked": True}
    frame = record_frame(record)
    record_axis = str(frame["axis"]) if frame is not None else AS_MODELLED
    requirement = (
        dict(frame["requirement"]) if frame is not None else None
    )
    allowed = _record_allowed(frame) if frame is not None else ()
    key = record_confirmation_key(record)
    row = store.get_frame_confirmation(key)
    matching = row is not None and requirement is not None and row.get("requirement") == requirement
    confirmed = (
        {
            "axis": row["axis"],
            "confirmedAt": row["confirmed_at"],
            "frame": row.get("frame"),
        }
        if matching
        else None
    )
    contract = str(frame["contract"]) if frame is not None else CONTRACT
    if frame is not None:
        record_from_assembly = spec_matrix(_frame_for_axis(frame, record_axis))
    else:
        record_from_assembly = np.eye(4)
    assembly_from_record = record_from_assembly.T  # a rotation's inverse
    axes = []
    for axis in AXES:
        spec = _frame_for_axis(frame, axis) if frame is not None else {"contract": CONTRACT_V1, "axis": axis}
        solver_from_assembly = spec_matrix(spec)
        axes.append(
            {
                "axis": axis,
                "allowed": axis in allowed,
                "reason": (
                    None
                    if axis in allowed
                    else (
                        "prepare this model again first"
                        if frame is None
                        else "this model's mirror planes contain only "
                        + ", ".join(allowed)
                    )
                ),
                "up": spec.get("up"),
                "upSource": spec.get("up_source"),
                "solverFromAssembly": solver_from_assembly.tolist(),
                "previewFromRecord": (solver_from_assembly @ assembly_from_record).tolist(),
            }
        )
    suggestion = _cached_suggestion(store, record) if frame is not None else None
    if frame is not None and suggestion is None:
        suggestion = ensure_frame_suggestion(store, record)
    confirmed_axis = confirmed["axis"] if confirmed is not None else None
    carried = carried_axis(row, requirement) if confirmed is None else None
    # A person's choice first (``manual_choice``), else WG's own decision.
    chosen = manual_choice(confirmed_axis, carried)
    blocked = frame_axis_not_allowed(store, record, suggestion) if frame is not None else None
    if blocked is not None:
        # Never shown as another axis: Solve ends calmly, saying why.
        preselected = None
    elif chosen is not None:
        # A v1 choice (``carried``) is the project's own, preselected ahead of
        # any suggestion, and confirmed under v2 by a Solve that names it.
        preselected = {"axis": chosen, "source": "confirmed" if confirmed_axis is not None else "carried"}
        confirmed_axis = chosen
    else:
        preselected = _decided_preselection(store, record, suggestion)
    return {
        "linked": False,
        "ingestId": record.get("ingest_id"),
        "contract": contract,
        "requirement": requirement,
        "recordAxis": record_axis,
        "recordStatesFrame": frame is not None,
        "recordFrame": (
            {
                "up": frame.get("up"),
                "upSource": frame.get("up_source"),
                "documentUp": frame.get("document_up"),
                "matrix": record_from_assembly.tolist(),
            }
            if frame is not None
            else None
        ),
        "confirmed": confirmed,
        "axes": axes,
        "suggestion": suggestion,
        "preselected": preselected,
        "notAllowed": (
            {"axis": blocked, "message": axis_not_allowed_message(None, blocked),
             "nextStep": AXIS_NOT_ALLOWED_NEXT_STEP}
            if blocked is not None else None
        ),
        "differs": _differs_notice(confirmed_axis if confirmed_axis in allowed else None, suggestion),
    }


def _is_unlinked(manifest: Mapping[str, Any]) -> bool:
    from server.cadlink.interpretation.solver_frame import is_unlinked_manifest

    return is_unlinked_manifest(manifest)


def _manifest_context(manifest: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    scope = manifest.get("scope")
    included = scope.get("included") if isinstance(scope, Mapping) else None
    body_ids = tuple(
        sorted(
            str(item.get("object_id"))
            for item in included or ()
            if isinstance(item, Mapping) and item.get("object_id")
        )
    )
    return body_ids, export_frame(manifest)


def _remembered_applies(
    remembered: Mapping[str, Any], manifest_sha256: str, context: tuple[tuple[str, ...], str]
) -> bool:
    """A reading carries only to the same exported bodies in the same frame."""

    if remembered.get("snapshot") == manifest_sha256:
        return True
    body_ids, export_frame = context
    return (
        tuple(sorted(str(item) for item in remembered.get("body_object_ids") or ()))
        == body_ids
        and remembered.get("export_frame") == export_frame
    )


def reading_key(store: CadLinkStore, manifest: Mapping[str, Any], manifest_sha256: str) -> str:
    """Where a lineage's reading is remembered: its project, or this exact snapshot."""

    from server.cadlink.interpretation.solver_frame import confirmation_key

    return confirmation_key(snapshot_project(store, manifest), manifest_sha256)


def resolve_domain_plan(
    store: CadLinkStore | None, manifest: Mapping[str, Any], manifest_sha256: str
) -> DomainPlan:
    """The evidence for this snapshot's domain, in precedence order.

    1. A declaration in the return: today's declared path, unchanged.
    2. The user's reading (Change) for this lineage: theirs wins over any
       provenance, in either direction.
    3. This return's own cut provenance, when it belongs to this snapshot.
    4. An earlier provenance-backed reading of the lineage.
    """

    kind = domain_kind(manifest)
    declared = declared_domain_planes(manifest)
    if kind == "declared":
        return DomainPlan(
            manifest_domain=kind,
            source=DECLARATION if declared else None,
            reading=READING_REDUCED if declared else None,
            planes=declared,
        )
    unlinked = _is_unlinked(manifest)
    if not unlinked:
        # A linked return is solved in its WG design's frame, and a pre-cut
        # linked throat already fails role resolution; nothing to read here.
        return DomainPlan(manifest_domain=kind, unlinked=False)
    body_ids, export_frame = _manifest_context(manifest)
    remembered = None
    if store is not None:
        remembered = store.get_domain_reading(reading_key(store, manifest, manifest_sha256))
        if remembered is not None and not _remembered_applies(
            remembered, manifest_sha256, (body_ids, export_frame)
        ):
            remembered = None
    if remembered is not None and remembered.get("source") == USER:
        own = remembered.get("snapshot") == manifest_sha256
        return DomainPlan(
            manifest_domain=kind,
            source=USER if own else USER_LINEAGE,
            reading=str(remembered.get("reading")),
            planes=tuple(plane for plane in PLANES if plane in (remembered.get("planes") or [])),
            snapshot=str(remembered.get("snapshot") or "") or None,
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    entries, ignored = _usable_provenance(manifest)
    if entries:
        features = tuple(
            {"plane": entry["plane"], "kind": entry["feature"]["kind"], "name": entry["feature"]["name"]}
            for entry in entries
        )
        planes = tuple(plane for plane in PLANES if any(entry["plane"] == plane for entry in entries))
        refusals = _provenance_refusals(entries)
        return DomainPlan(
            manifest_domain=kind,
            source=PROVENANCE,
            reading=READING_REDUCED,
            planes=planes,
            features=features,
            ignored=tuple(ignored),
            refusal=refusals[0][1] if refusals else None,
            refusals=tuple(refusals),
            kept_sides=tuple(
                (str(entry["plane"]), str(entry.get("kept_side") or "positive"))
                for entry in entries
                if entry.get("kept_side") in ("positive", "negative")
            ),
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    if remembered is not None and remembered.get("source") == PROVENANCE:
        return DomainPlan(
            manifest_domain=kind,
            source=LINEAGE,
            reading=READING_REDUCED,
            planes=tuple(plane for plane in PLANES if plane in (remembered.get("planes") or [])),
            snapshot=str(remembered.get("snapshot") or "") or None,
            features=tuple(dict(item) for item in remembered.get("features") or []),
            ignored=tuple(ignored),
            body_object_ids=body_ids,
            export_frame=export_frame,
        )
    return DomainPlan(
        manifest_domain=kind,
        ignored=tuple(ignored),
        body_object_ids=body_ids,
        export_frame=export_frame,
    )


def _usable_provenance(
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Provenance entries that belong to this snapshot's bodies and frame.

    The reader already checked the body is one of this return's included
    bodies. An entry recorded in another component's frame names a plane in
    coordinates this STEP is not written in, so it is not used.
    """

    frame = export_frame(manifest)
    usable: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for entry in cut_provenance(manifest):
        if entry.get("export_frame") != frame:
            ignored.append(
                {
                    "feature": (entry.get("feature") or {}).get("name"),
                    "reason": f"recorded in the {entry.get('export_frame')} frame; this model is in the {frame} frame",
                }
            )
            continue
        usable.append(entry)
    return usable, ignored


def _provenance_refusals(
    entries: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    refusals: list[tuple[str, str]] = []
    for entry in entries:
        plane = str(entry["plane"])
        name = (entry.get("feature") or {}).get("name") or "the cut"
        if plane not in SUPPORTED_PLANES:
            refusals.append(
                (
                    plane,
                    f"{name} cuts this model on {plane_words(plane)} "
                    f"(the {ORIGIN_PLANE[plane]} plane), which WG cannot mirror yet. Cut it on "
                    "the YZ or XZ origin plane instead, or send the whole model.",
                )
            )
        elif entry.get("kept_side") not in ("positive", "negative"):
            refusals.append(
                (
                    plane,
                    f"{name} does not say which side of {plane_words(plane)} it kept; "
                    "send the model again, or send the whole model.",
                )
            )
    return refusals


def record_reading(store: CadLinkStore, record: Mapping[str, Any], reading: Mapping[str, Any]) -> dict[str, Any]:
    """Remember the user's reading of this model's domain for its lineage.

    Only a reading the record offers (``choices``) is taken: a reduced reading
    offered is one the geometry already revalidated, and it is checked again,
    like a declaration, when the model is prepared. Solve prepares again,
    under the new plan, so no approval given before carries to it.
    """

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        raise DomainReadingError("this model was prepared before WG interpreted its domain; prepare it again")
    if interpretation.get("evidence", {}).get("source") == DECLARATION:
        raise DomainReadingError("this return declares its domain in Fusion; change it there")
    kind = reading.get("reading")
    planes = [str(plane) for plane in reading.get("planes") or []]
    if kind == READING_AS_SHOWN:
        wanted: dict[str, Any] = {"reading": READING_AS_SHOWN}
        planes = []
    elif kind == READING_REDUCED:
        planes = [plane for plane in PLANES if plane in set(planes)]
        wanted = {"reading": READING_REDUCED, "planes": planes}
    else:
        raise DomainReadingError("a reading is 'as-shown' or 'reduced' with its planes")
    offered = [dict(choice) for choice in interpretation.get("choices") or []]
    current = {"reading": interpretation.get("reading"), **({"planes": list(interpretation.get("planes") or [])} if interpretation.get("reading") == READING_REDUCED else {})}
    if wanted not in offered and wanted != current:
        raise DomainReadingError(
            "this model cannot be read that way: "
            + ("WG solves it as shown already" if kind == READING_AS_SHOWN else "the cut does not validate on the planes named")
        )
    return store.record_domain_reading(
        record_key(record),
        {
            "source": USER,
            "reading": wanted["reading"],
            "planes": planes,
            "snapshot": str(record.get("manifest_sha256") or ""),
            "ingest_id": record.get("ingest_id"),
            **dict(interpretation.get("lineage_context") or {}),
        },
    )


def remember_provenance_reading(store: CadLinkStore, record: Mapping[str, Any]) -> None:
    """After a provenance-backed reading solved, remember it for the lineage.

    Only a project lineage remembers; a snapshot with none has nothing to carry
    it to. A user's own reading is never replaced by it.
    """

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        return
    evidence = interpretation.get("evidence") or {}
    if interpretation.get("reading") != READING_REDUCED or evidence.get("source") != PROVENANCE:
        return
    key = record_key(record)
    if not key.startswith("lineage:"):
        return
    held = store.get_domain_reading(key)
    if held is not None and held.get("source") == USER:
        return
    store.record_domain_reading(
        key,
        {
            "source": PROVENANCE,
            "reading": READING_REDUCED,
            "planes": list(interpretation.get("planes") or []),
            "features": list(evidence.get("features") or []),
            "snapshot": str(record.get("manifest_sha256") or ""),
            "ingest_id": record.get("ingest_id"),
            **dict(interpretation.get("lineage_context") or {}),
        },
    )


def lineage_reading(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The reading remembered for this record's lineage (or snapshot), if any."""

    return store.get_domain_reading(record_key(record))


def interpretation_view(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any]:
    """The model card's domain line: what was solved, why, the Change choices, and a pending Change."""

    interpretation = record.get("domain_interpretation")
    if not isinstance(interpretation, Mapping):
        return {"ingestId": record.get("ingest_id"), "available": False}
    remembered = lineage_reading(store, record)
    pending = None
    if remembered is not None and remembered.get("source") == USER:
        wanted = {"reading": remembered.get("reading")}
        if wanted["reading"] == READING_REDUCED:
            wanted["planes"] = list(remembered.get("planes") or [])
        solved = {"reading": interpretation.get("reading")}
        if solved["reading"] == READING_REDUCED:
            solved["planes"] = list(interpretation.get("planes") or [])
        solved_as_user = (interpretation.get("evidence") or {}).get("source") in (USER, USER_LINEAGE)
        if wanted["reading"] == READING_AS_SHOWN:
            differs = solved["reading"] == READING_REDUCED
        else:
            differs = solved != wanted or not solved_as_user
        offered = [dict(choice) for choice in interpretation.get("choices") or []]
        # A carried reading can stop applying after the model changes (for
        # example, a former half becomes whole). Do not promise that Solve
        # will apply a reading this geometry no longer offers.
        if differs and wanted in offered:
            pending = wanted
    decision = record.get("domain_decision")
    return {
        "ingestId": record.get("ingest_id"),
        "available": True,
        "interpretation": dict(interpretation),
        # The snapshot's one domain decision (``domain_decision.py``), exactly
        # as the ingestion record seals it and every submission checks it.
        "decision": dict(decision) if isinstance(decision, Mapping) else None,
        "remembered": remembered,
        "pending": pending,
    }


__all__ = [
    "confirm_frame",
    "ensure_frame_suggestion",
    "frame_axis_not_allowed",
    "frame_preview",
    "frame_provenance",
    "interpretation_view",
    "lineage_reading",
    "reading_key",
    "decide_record_frame",
    "legacy_automatic_axis",
    "record_automatic_axis",
    "record_frame_decision",
    "record_frame_refusal",
    "record_preparation_diagnostic",
    "record_reading",
    "record_solved_frame_provenance",
    "remember_provenance_reading",
    "store_frame_decision",
    "resolve_domain_plan",
    "resolve_for_manifest",
    "snapshot_project",
    "solver_anchor",
]
