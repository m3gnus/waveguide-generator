"""The solver frame of an unlinked (CAD-authored) snapshot.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A return with no
WG instance carries no throat frame: nothing in it says which way the model
radiates. WG used to solve it in the assembly frame as modelled (+Z) and warn
nothing; a mis-framed model then gave wrong directivity in silence. Now the
user confirms, once per project, which assembly axis the model radiates along,
after seeing the geometry in that frame, and no path solves it before then.

The contract (``cad-solver-frame-v1``) is deliberately closed:

- ``axis`` is one of ``+z -z +x -x +y -y``. Each maps to one fixed proper
  rotation taking that axis to the solver's +Z by the minimal rotation, so the
  perpendicular axis the two share is kept. The origin is the assembly origin:
  the throat is modelled there, as before. ``+z`` is the identity, the frame
  every earlier release solved in.
- :func:`frame_matrix` is the only producer of these matrices. Mesh
  preparation, the ingestion record, the preview and the frontend's fixture all
  read it, which is what makes the preview the solved frame.
- A confirmation holds for a **requirement**: this contract and the manifest's
  ``export_frame`` (which component's coordinates the STEP is written in). A
  different requirement is a different frame and must be confirmed again.
- A return declaring a reduced domain states its cut planes and retained side
  in the assembly frame, so only ``+z`` keeps them meaningful.

A confirmation is keyed by the snapshot's project, or, for a snapshot that has
none (an unsaved CAD document), by that exact snapshot. It is not part of a
setup revision: a revision is recorded from UI state by any client and must
not be able to confirm a frame. The frame reaches a solve through the
ingestion record, which states the frame it was meshed in, and so through the
bound ``ingest_id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .store import CadLinkStore


CONTRACT = "cad-solver-frame-v1"
AS_MODELLED = "+z"
AXES: tuple[str, ...] = ("+z", "-z", "+x", "-x", "+y", "-y")
#: The operation reason code (``operations.REASON_CODES``) and jobs refusal code.
REASON = "frame_confirmation_required"
DEFAULT_EXPORT_FRAME = "root-component"

# Row-major rotations, solver_from_assembly. Each takes its axis to +Z.
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
    """``solver_from_assembly`` for one axis: a 4x4 row-major rigid rotation."""

    rotation = _ROTATIONS[_require_axis(axis)]
    matrix = np.eye(4)
    matrix[:3, :3] = np.asarray(rotation, dtype=float)
    return matrix


def is_unlinked_manifest(manifest: Mapping[str, Any]) -> bool:
    """A return with no WG instance: CAD-authored, with no throat frame."""

    instances = manifest.get("instances")
    return not (isinstance(instances, list) and instances)


def frame_requirement(manifest: Mapping[str, Any]) -> dict[str, str]:
    """What a confirmation must match to hold for this snapshot."""

    coordinates = manifest.get("coordinate_system")
    export_frame = (
        coordinates.get("export_frame") if isinstance(coordinates, Mapping) else None
    )
    return {
        "contract": CONTRACT,
        "export_frame": str(export_frame or DEFAULT_EXPORT_FRAME),
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
    requirement: dict[str, str]
    allowed_axes: tuple[str, ...]
    #: The axis confirmed under this requirement, whether or not it is allowed.
    confirmed_axis: str | None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_axis is not None and self.confirmed_axis in self.allowed_axes

    @property
    def axis(self) -> str:
        """The frame a preparation meshes in: the confirmed one, else as modelled."""

        return self.confirmed_axis if self.confirmed else AS_MODELLED  # type: ignore[return-value]


def _confirmed_axis(
    store: CadLinkStore, key: str, requirement: Mapping[str, str]
) -> str | None:
    row = store.get_frame_confirmation(key)
    if row is None or row.get("requirement") != dict(requirement):
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
    )


def record_solver_frame(manifest: Mapping[str, Any], axis: str) -> dict[str, Any]:
    """What an unlinked ingestion record states about the frame it was meshed in."""

    return {
        "contract": CONTRACT,
        "axis": _require_axis(axis),
        "requirement": frame_requirement(manifest),
        "allowed_axes": list(allowed_axes(manifest)),
        "matrix": frame_matrix(axis).tolist(),
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
        and frame.get("contract") == CONTRACT
        and frame.get("axis") in _ROTATIONS
        and isinstance(frame.get("requirement"), Mapping)
    ):
        return frame
    return None


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
    was meshed in, under the same requirement. A record prepared before the
    frame contract states no frame and is never taken as confirmed.
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
    """Record the user's confirmation for the project an ingestion record belongs to."""

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
    return store.record_frame_confirmation(
        record_confirmation_key(record), dict(frame["requirement"]), chosen
    )


def frame_preview(store: CadLinkStore, record: Mapping[str, Any]) -> dict[str, Any]:
    """Every axis's matrix, relative to the frame this record's geometry is shown in."""

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
    confirmed = (
        {"axis": row["axis"], "confirmedAt": row["confirmed_at"]}
        if row is not None and requirement is not None and row.get("requirement") == requirement
        else None
    )
    record_from_assembly = frame_matrix(record_axis)
    assembly_from_record = record_from_assembly.T  # a rotation's inverse
    axes = []
    for axis in AXES:
        solver_from_assembly = frame_matrix(axis)
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
                "solverFromAssembly": solver_from_assembly.tolist(),
                "previewFromRecord": (solver_from_assembly @ assembly_from_record).tolist(),
            }
        )
    return {
        "linked": False,
        "ingestId": record.get("ingest_id"),
        "contract": CONTRACT,
        "requirement": requirement,
        "recordAxis": record_axis,
        "recordStatesFrame": frame is not None,
        "confirmed": confirmed,
        "axes": axes,
    }


__all__ = [
    "AS_MODELLED",
    "AXES",
    "CONTRACT",
    "FrameConfirmationError",
    "FrameResolution",
    "REASON",
    "allowed_axes",
    "confirm_frame",
    "confirmation_key",
    "frame_matrix",
    "frame_preview",
    "frame_requirement",
    "is_unlinked_manifest",
    "record_confirmation_key",
    "record_frame_refusal",
    "record_is_unlinked",
    "record_solver_frame",
    "resolve_for_manifest",
]
