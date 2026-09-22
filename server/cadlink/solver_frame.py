"""The solver frame of an unlinked (CAD-authored) snapshot.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A return with no
WG instance carries no throat frame: nothing in it says which way the model
radiates. WG used to solve it in the assembly frame as modelled (+Z) and warn
nothing; a mis-framed model then gave wrong directivity in silence. Now the
forward axis is confirmed once per project, after WG infers it from the
geometry (``frame_infer``) and shows it; no path solves it before then.

Two contracts exist. Both name the forward ``axis``, one of
``+z -z +x -x +y -y``, and map it to the solver's +Z about the assembly origin.

- ``cad-solver-frame-v2`` (current) also fixes the roll. The solver's +Y is
  the model's **up**, so the horizontal polar plane (solver x-z) contains the
  forward axis and is perpendicular to up. Up is the CAD document's up axis
  when the return states it (``coordinate_system.document_up`` under the
  ``document-up-v1`` feature, +Y or +Z); otherwise CAD +Z, or +Y when the
  forward axis is +-Z. A forward axis parallel to the document's up has no
  roll of its own and takes the other of +Y/+Z, recorded as such. The
  transform is ``solver_from_assembly`` with rows (up x forward, up, forward).
  ``+z`` is the identity under every up rule, so the modelled frame and every
  declared (reduced) domain keep exactly the transform they had.
- ``cad-solver-frame-v1`` (historical) turned each axis by the minimal
  rotation, keeping the perpendicular axis the two frames share. Records and
  confirmations made under it keep that meaning: they still resolve and solve
  as they were prepared. New preparations are v2. A v1 confirmation is carried
  forward (:func:`carried_axis`): a v2 preparation meshes in and preselects
  its axis, and Solve confirms it under v2, so an existing project is not
  asked again.

:func:`spec_matrix` is the only producer of these matrices. Mesh preparation,
the ingestion record, the mesh cache key, the preview and the confirmation all
read it, which is what makes the preview the solved frame.

A confirmation holds for a **requirement**: the contract, the manifest's
``export_frame`` (which component's coordinates the STEP is written in) and,
under v2, the document up the return stated. Together with the axis these fix
the whole transform, which the confirmation also records with its up
provenance. A different requirement is a different frame and must be confirmed
again. A return declaring a reduced domain states its cut planes and retained
side in the assembly frame, so only ``+z`` keeps them meaningful.

A confirmation is keyed by the snapshot's project, or, for a snapshot that has
none (an unsaved CAD document), by that exact snapshot. It is not part of a
setup revision: a revision is recorded from UI state by any client and must
not be able to confirm a frame. The frame reaches a solve through the
ingestion record, which states the frame it was meshed in, and so through the
bound ``ingest_id``.

The automatic suggestion (``frame_infer``) is computed inside the explicit
import and preparation commands and cached per snapshot and algorithm version.
It preselects; it never confirms. A confirmed frame whose identity matches
always wins over it, and a suggestion that clearly disagrees with the
confirmed frame is reported, never acted on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, TYPE_CHECKING

import numpy as np

from .wgreturn import DOCUMENT_UP_AXES, DOCUMENT_UP_FEATURE

if TYPE_CHECKING:  # pragma: no cover
    from .store import CadLinkStore


logger = logging.getLogger(__name__)

CONTRACT_V1 = "cad-solver-frame-v1"
CONTRACT_V2 = "cad-solver-frame-v2"
#: The contract every new preparation is made under.
CONTRACT = CONTRACT_V2
CONTRACTS = (CONTRACT_V1, CONTRACT_V2)
AS_MODELLED = "+z"
AXES: tuple[str, ...] = ("+z", "-z", "+x", "-x", "+y", "-y")
#: The operation reason code (``operations.REASON_CODES``) and jobs refusal code.
REASON = "frame_confirmation_required"
DEFAULT_EXPORT_FRAME = "root-component"
UP_FROM_DOCUMENT = "document"
UP_DEFAULT = "default"
UP_FORWARD_PARALLEL = "forward-parallel-to-document-up"

_VECTORS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}

# Contract v1. Row-major rotations, solver_from_assembly. Each takes its axis
# to +Z by the minimal rotation.
_ROTATIONS: dict[str, tuple[tuple[float, float, float], ...]] = {
    "+z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    # 180 degrees about X.
    "-z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    # -90 degrees about Y: +X -> +Z, +Z -> -X.
    "+x": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
    # +90 degrees about Y: -X -> +Z, +Z -> +X.
    "-x": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    # +90 degrees about X: +Y -> +Z, +Z -> -Y.
    "+y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    # -90 degrees about X: -Y -> +Z, +Z -> +Y.
    "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}


class FrameConfirmationError(ValueError):
    """A confirmation this snapshot cannot take: linked, or an axis it does not allow."""


def _require_axis(axis: object) -> str:
    if not isinstance(axis, str) or axis not in _ROTATIONS:
        raise ValueError(f"solver frame axis must be one of {', '.join(AXES)}, got {axis!r}")
    return axis


def frame_matrix(axis: str) -> np.ndarray:
    """Contract v1's ``solver_from_assembly`` for one axis (historical records)."""

    rotation = _ROTATIONS[_require_axis(axis)]
    matrix = np.eye(4)
    matrix[:3, :3] = np.asarray(rotation, dtype=float)
    return matrix


def is_unlinked_manifest(manifest: Mapping[str, Any]) -> bool:
    """A return with no WG instance: CAD-authored, with no throat frame."""

    instances = manifest.get("instances")
    return not (isinstance(instances, list) and instances)


def document_up(manifest: Mapping[str, Any]) -> str | None:
    """The CAD document's up axis the return states, or None when it states none.

    Read only under ``document-up-v1``; the manifest reader has already
    refused the field without the feature and the feature without the field.
    """

    features = manifest.get("required_features")
    if not isinstance(features, list) or DOCUMENT_UP_FEATURE not in features:
        return None
    coordinates = manifest.get("coordinate_system")
    value = coordinates.get("document_up") if isinstance(coordinates, Mapping) else None
    return value if value in DOCUMENT_UP_AXES else None


def resolve_up(forward: str, stated_up: str | None) -> tuple[str, str]:
    """The model's up for a forward axis, and where it came from."""

    _require_axis(forward)
    if stated_up is None:
        return ("+y" if forward[1] == "z" else "+z"), UP_DEFAULT
    if stated_up not in DOCUMENT_UP_AXES:
        raise ValueError(f"document up must be one of {', '.join(DOCUMENT_UP_AXES)}, got {stated_up!r}")
    if forward[1] == stated_up[1]:
        # Radiating along the document's own up (or down): no roll to take
        # from it, so the other of the two up axes a CAD document can have.
        return ("+z" if stated_up == "+y" else "+y"), UP_FORWARD_PARALLEL
    return stated_up, UP_FROM_DOCUMENT


def frame_spec(axis: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The current contract's complete frame for ``axis`` on this snapshot."""

    stated = document_up(manifest)
    up, source = resolve_up(axis, stated)
    return {
        "contract": CONTRACT_V2,
        "axis": axis,
        "up": up,
        "up_source": source,
        "document_up": stated,
    }


def _v2_matrix(axis: str, up: str) -> np.ndarray:
    forward = np.asarray(_VECTORS[_require_axis(axis)])
    upward = np.asarray(_VECTORS[_require_axis(up)])
    if abs(float(forward @ upward)) > 0.5:
        raise ValueError(f"up {up} is parallel to the forward axis {axis}")
    matrix = np.eye(4)
    matrix[0, :3] = np.cross(upward, forward)
    matrix[1, :3] = upward
    matrix[2, :3] = forward
    return matrix


def spec_matrix(spec: str | Mapping[str, Any]) -> np.ndarray:
    """``solver_from_assembly`` for a frame: a v1 axis, or a v1/v2 frame spec.

    A bare axis string is contract v1, which is how preparations before v2
    named their frame.
    """

    if isinstance(spec, str):
        return frame_matrix(spec)
    if not isinstance(spec, Mapping):
        raise ValueError(f"solver frame must be an axis or a frame, got {spec!r}")
    contract = spec.get("contract")
    if contract == CONTRACT_V1:
        return frame_matrix(spec.get("axis"))  # type: ignore[arg-type]
    if contract == CONTRACT_V2:
        axis = _require_axis(spec.get("axis"))
        up, source = resolve_up(axis, spec.get("document_up"))
        if spec.get("up") != up or spec.get("up_source") != source:
            raise ValueError(
                f"solver frame up {spec.get('up')!r} ({spec.get('up_source')!r}) is not the "
                f"contract's {up!r} ({source!r}) for {axis} with document up "
                f"{spec.get('document_up')!r}"
            )
        return _v2_matrix(axis, up)
    raise ValueError(f"unknown solver frame contract {contract!r}")


def spec_axis(spec: str | Mapping[str, Any]) -> str:
    """The forward axis a frame names."""

    axis = spec if isinstance(spec, str) else spec.get("axis") if isinstance(spec, Mapping) else None
    return _require_axis(axis)


def frame_requirement(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """What a confirmation must match to hold for this snapshot (current contract)."""

    coordinates = manifest.get("coordinate_system")
    export_frame = (
        coordinates.get("export_frame") if isinstance(coordinates, Mapping) else None
    )
    return {
        "contract": CONTRACT,
        "export_frame": str(export_frame or DEFAULT_EXPORT_FRAME),
        "document_up": document_up(manifest),
    }


def allowed_axes(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The axes this snapshot can be solved along."""

    assembly = manifest.get("assembly")
    domain = assembly.get("domain") if isinstance(assembly, Mapping) else None
    if domain:
        return (AS_MODELLED,)
    return AXES


def confirmation_key(lineage_id: str | None, manifest_sha256: str) -> str:
    """The project's key, or the exact snapshot's when it belongs to no project."""

    lineage = str(lineage_id or "").strip()
    if lineage:
        return f"lineage:{lineage}"
    return f"snapshot:{manifest_sha256}"


@dataclass(frozen=True)
class FrameResolution:
    """An unlinked snapshot's frame state before it is prepared."""

    key: str
    requirement: dict[str, Any]
    allowed_axes: tuple[str, ...]
    #: The axis confirmed under this requirement, whether or not it is allowed.
    confirmed_axis: str | None
    #: The manifest's stated document up (None when it states none).
    document_up: str | None = None
    #: The axis the project confirmed under contract v1 (``carried_axis``):
    #: preselected and meshed in, never taken as confirmed.
    carried_axis: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_axis is not None and self.confirmed_axis in self.allowed_axes

    @property
    def axis(self) -> str:
        """The frame a preparation meshes in: the confirmed one, else a v1
        confirmation carried forward, else as modelled.

        Meshing in the carried axis is what lets Solve confirm it under the
        current contract without preparing the model again; the solve still
        waits for that confirmation (``record_frame_refusal``).
        """

        if self.confirmed:
            return self.confirmed_axis  # type: ignore[return-value]
        if self.carried_axis is not None and self.carried_axis in self.allowed_axes:
            return self.carried_axis
        return AS_MODELLED

    @property
    def spec(self) -> dict[str, Any]:
        """The complete frame a preparation meshes in."""

        up, source = resolve_up(self.axis, self.document_up)
        return {
            "contract": CONTRACT_V2,
            "axis": self.axis,
            "up": up,
            "up_source": source,
            "document_up": self.document_up,
        }


def _confirmed_axis(
    store: CadLinkStore, key: str, requirement: Mapping[str, Any]
) -> str | None:
    row = store.get_frame_confirmation(key)
    if row is None or row.get("requirement") != dict(requirement):
        return None
    axis = row.get("axis")
    return axis if axis in _ROTATIONS else None


def carried_axis(
    row: Mapping[str, Any] | None, requirement: Mapping[str, Any] | None
) -> str | None:
    """A project's contract-v1 confirmation, carried forward to a v2 requirement.

    Contract v2 changed only the roll: the forward axis a user confirmed under
    v1 still names the same CAD direction. So an existing CAD project is not
    asked again. Its v1 axis is preselected for a v2 preparation, which Solve
    then confirms under v2, and it is never taken as confirmed until then.
    Only for the same ``export_frame``: a snapshot written in another
    component's coordinates is a different frame under every contract.
    """

    if row is None or not isinstance(requirement, Mapping):
        return None
    held = row.get("requirement")
    if (
        not isinstance(held, Mapping)
        or held.get("contract") != CONTRACT_V1
        or requirement.get("contract") != CONTRACT_V2
        or held.get("export_frame") != requirement.get("export_frame")
    ):
        return None
    axis = row.get("axis")
    return axis if axis in _ROTATIONS else None


def resolve_for_manifest(
    store: CadLinkStore, manifest: Mapping[str, Any], manifest_sha256: str
) -> FrameResolution | None:
    """The frame state of a snapshot about to be prepared; None when it is linked."""

    if not is_unlinked_manifest(manifest):
        return None
    from .project_setup import snapshot_project

    key = confirmation_key(snapshot_project(store, manifest), manifest_sha256)
    requirement = frame_requirement(manifest)
    return FrameResolution(
        key=key,
        requirement=requirement,
        allowed_axes=allowed_axes(manifest),
        confirmed_axis=_confirmed_axis(store, key, requirement),
        document_up=document_up(manifest),
        carried_axis=carried_axis(store.get_frame_confirmation(key), requirement),
    )


def record_solver_frame(
    manifest: Mapping[str, Any], frame: str | Mapping[str, Any]
) -> dict[str, Any]:
    """What an unlinked ingestion record states about the frame it was meshed in."""

    spec = dict(frame) if isinstance(frame, Mapping) else frame_spec(frame, manifest)
    if spec.get("contract") != CONTRACT_V2:
        raise ValueError("a new preparation is made under the current solver frame contract")
    matrix = spec_matrix(spec)
    allowed = allowed_axes(manifest)
    if allowed == (AS_MODELLED,) and not np.array_equal(matrix, np.eye(4)):
        # A declared domain keeps its modelled transform until M1d permits more.
        raise ValueError("a declared half or quarter is solved only in the frame it was modelled in")
    return {
        "contract": CONTRACT_V2,
        "axis": spec["axis"],
        "up": spec["up"],
        "up_source": spec["up_source"],
        "document_up": spec.get("document_up"),
        "requirement": frame_requirement(manifest),
        "allowed_axes": list(allowed),
        "matrix": matrix.tolist(),
    }


# -- ingestion records ---------------------------------------------------------


def record_is_unlinked(record: Mapping[str, Any]) -> bool:
    """Whether an ingestion record was prepared from a return with no WG instance."""

    anchor = record.get("anchor")
    if isinstance(anchor, Mapping) and "instance_id" in anchor:
        return anchor.get("instance_id") is None
    normalisation = record.get("normalisation")
    if isinstance(normalisation, Mapping):
        if "solver_frame" in normalisation:
            return True
        if "anchor_instance_id" in normalisation:
            return normalisation.get("anchor_instance_id") is None
        return bool(normalisation.get("assembly_frame_is_solver_frame"))
    return False


def _record_frame(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    normalisation = record.get("normalisation")
    frame = normalisation.get("solver_frame") if isinstance(normalisation, Mapping) else None
    if (
        isinstance(frame, Mapping)
        and frame.get("contract") in CONTRACTS
        and frame.get("axis") in _ROTATIONS
        and isinstance(frame.get("requirement"), Mapping)
        and frame["requirement"].get("contract") == frame.get("contract")
    ):
        return frame
    return None


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


def record_confirmation_key(record: Mapping[str, Any]) -> str:
    project = record.get("project")
    lineage = project.get("lineage_id") if isinstance(project, Mapping) else None
    return confirmation_key(lineage, str(record.get("manifest_sha256") or ""))


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
    frame = _record_frame(record)
    if frame is None:
        return (
            "This model was prepared before WG asked for its solver frame. "
            "Prepare it again in WG and confirm the axis it radiates along."
        )
    key = record_confirmation_key(record)
    requirement = dict(frame["requirement"])
    confirmed = _confirmed_axis(store, key, requirement)
    allowed = _record_allowed(frame)
    if confirmed is None:
        return (
            "Confirm this model's solver frame in WG first: choose the axis it "
            "radiates along and check the preview, then solve it."
        )
    if confirmed not in allowed:
        return (
            f"The confirmed solver frame ({confirmed}) cannot be used: this return is "
            "declared as a half or quarter model, which is solved only in the frame "
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
    frame = _record_frame(record)
    if frame is None:
        raise FrameConfirmationError(
            "this model was prepared before WG asked for its solver frame; prepare it again"
        )
    try:
        chosen = _require_axis(axis)
    except ValueError as exc:
        raise FrameConfirmationError(str(exc)) from exc
    if chosen not in _record_allowed(frame):
        raise FrameConfirmationError(
            "this return is declared as a half or quarter model and is solved only "
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


# -- the automatic suggestion ----------------------------------------------------


def _snapshot(record: Mapping[str, Any]) -> str:
    return str(record.get("manifest_sha256") or "")


def _cached_suggestion(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any] | None:
    from .frame_infer import ALGORITHM_VERSION

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

    from .frame_infer import STATUS_UNAVAILABLE, infer_record_frame, unavailable

    cached = _cached_suggestion(store, record)
    if cached is not None:
        return cached
    frame = _record_frame(record)
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
    frame = _record_frame(record)
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


__all__ = [
    "AS_MODELLED",
    "AXES",
    "CONTRACT",
    "CONTRACTS",
    "CONTRACT_V1",
    "CONTRACT_V2",
    "DOCUMENT_UP_AXES",
    "DOCUMENT_UP_FEATURE",
    "FrameConfirmationError",
    "FrameResolution",
    "REASON",
    "allowed_axes",
    "carried_axis",
    "confirm_frame",
    "confirmation_key",
    "document_up",
    "ensure_frame_suggestion",
    "frame_matrix",
    "frame_preview",
    "frame_requirement",
    "frame_spec",
    "is_unlinked_manifest",
    "record_confirmation_key",
    "record_frame_refusal",
    "record_is_unlinked",
    "record_solver_frame",
    "resolve_for_manifest",
    "resolve_up",
    "spec_axis",
    "spec_matrix",
]
