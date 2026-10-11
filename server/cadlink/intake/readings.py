"""Remembered frame and domain readings for received CAD snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Any, TYPE_CHECKING

import numpy as np

from server.cadlink.wgreturn import cut_provenance, declared_domain_planes, domain_kind

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
    automatic_solve_axis,
    carried_axis,
    confirmation_key,
    document_up,
    frame_requirement,
    is_unlinked_manifest,
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

    ``automatic``: a Solve that may use WG's confident automatic axis
    (:func:`automatic_solve_axis`) meshes in it when nothing is confirmed.
    """

    if not is_unlinked_manifest(manifest):
        return None
    key = confirmation_key(snapshot_project(store, manifest), manifest_sha256)
    requirement = frame_requirement(manifest)
    allowed = allowed_axes(manifest)
    confirmed = _confirmed_axis(store, key, requirement)
    carried = carried_axis(store.get_frame_confirmation(key), requirement)
    automatic_axis = None
    if automatic and (confirmed is None or confirmed not in allowed) and carried is None:
        automatic_axis = automatic_solve_axis(
            store.get_frame_suggestion(manifest_sha256, _algorithm_version()), allowed
        )
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


def record_automatic_axis(store: CadLinkStore, record: Mapping[str, Any]) -> str | None:
    """The confident automatic axis this record's snapshot would be solved along.

    None for a linked record, a record stating no frame, one whose project (or
    snapshot) already confirmed a frame under this requirement, one with a v1
    confirmation carried forward, and whenever WG is not confident.
    """

    if not record_is_unlinked(record):
        return None
    frame = record_frame(record)
    if frame is None:
        return None
    key = record_confirmation_key(record)
    requirement = dict(frame["requirement"])
    if _confirmed_axis(store, key, requirement) is not None:
        return None
    if carried_axis(store.get_frame_confirmation(key), requirement) is not None:
        return None
    return automatic_solve_axis(_cached_suggestion(store, record), _record_allowed(frame))


def record_solved_frame_provenance(
    store: CadLinkStore, record: Mapping[str, Any]
) -> str | None:
    """``"automatic"`` when this record solves along WG's own confident axis
    with no confirmation behind it, else None."""

    frame = record_frame(record) if record_is_unlinked(record) else None
    if frame is None:
        return None
    return "automatic" if record_automatic_axis(store, record) == frame.get("axis") else None


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
    """Why this record cannot be solved yet, or None when its frame is confirmed.

    A linked record is never gated. An unlinked record solves only when its
    project (or, with no project, this snapshot) confirmed exactly the frame it
    was meshed in, under the same requirement -- and so the same contract. A
    record prepared before the frame contract states no frame and is never
    taken as confirmed.
    """

    if not record_is_unlinked(record):
        return None
    frame = record_frame(record)
    if frame is None:
        return (
            "This model was prepared before WG asked for its solver frame. "
            "Prepare it again in WG and confirm the axis it radiates along."
        )
    key = record_confirmation_key(record)
    requirement = dict(frame["requirement"])
    confirmed = _confirmed_axis(store, key, requirement)
    allowed = _record_allowed(frame)
    automatic = record_automatic_axis(store, record) if confirmed is None else None
    if confirmed is None and automatic == frame["axis"]:
        # WG is confident which way it faces and this is the frame it was
        # meshed in: solved along it, and never recorded as a confirmation.
        return None
    if confirmed is None and automatic is not None:
        # Meshed as modelled, but WG is confident of another axis: the next
        # preparation meshes along it (headless preparation owes the same).
        return (
            f"This model was prepared along {frame['axis']}, but WG would solve it "
            f"along {automatic}. Prepare it again in WG."
        )
    if confirmed is None:
        return (
            "Confirm this model's solver frame in WG first: choose the axis it "
            "radiates along and check the preview, then solve it."
        )
    if confirmed not in allowed:
        return (
            f"The confirmed solver frame ({confirmed}) cannot be used: this model is "
            "solved as a half or quarter model, which is solved only in the frame "
            "it was modelled in (+z). Confirm +z, or return the whole model."
        )
    if confirmed != frame["axis"]:
        return (
            f"This model was prepared along {frame['axis']}, but its confirmed solver "
            f"frame is {confirmed}. Prepare it again in WG to solve it in that frame."
        )
    return None


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
            "this model is solved as a half or quarter model, and so only "
            "in the frame it was modelled in (+z)"
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


def frame_preview(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any]:
    """Every axis's matrix, relative to the frame this record's geometry is shown in.

    Also the automatic suggestion for the snapshot, which axis is preselected
    and why -- a matching confirmed frame first, then a v1 confirmation
    carried forward, then an automatic suggestion the snapshot allows, else
    none -- and, when the suggestion clearly
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
                        else "a half or quarter model is solved only as modelled (+z)"
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
    if confirmed_axis is not None and confirmed_axis in allowed:
        preselected = {"axis": confirmed_axis, "source": "confirmed"}
    elif carried is not None and carried in allowed:
        # Confirmed under contract v1: the project's own choice, preselected
        # ahead of any suggestion, and confirmed under v2 by Solve.
        preselected = {"axis": carried, "source": "carried"}
        confirmed_axis = carried
    elif (
        suggestion is not None
        and suggestion.get("status") == "automatic"
        and suggestion.get("axis") in allowed
    ):
        preselected = {"axis": suggestion["axis"], "source": "suggested"}
    else:
        preselected = None
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
    "frame_preview",
    "frame_provenance",
    "interpretation_view",
    "lineage_reading",
    "reading_key",
    "record_automatic_axis",
    "record_frame_refusal",
    "record_reading",
    "record_solved_frame_provenance",
    "remember_provenance_reading",
    "resolve_domain_plan",
    "resolve_for_manifest",
    "snapshot_project",
    "solver_anchor",
]
