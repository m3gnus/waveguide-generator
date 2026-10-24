"""The solver frame of an unlinked (CAD-authored) snapshot.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A return with no
WG instance carries no throat frame: nothing in it says which way the model
radiates. WG used to solve it in the assembly frame as modelled (+Z) and warn
nothing; a mis-framed model then gave wrong directivity in silence. Now the
forward axis is confirmed once per project, after WG infers it from the
geometry (``frame_infer``) and shows it. Fusion requests always use WG's best
available automatic axis when no manual choice stands; weak evidence is
recorded for developers and the user may change the axis afterwards.

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
It preselects; it never confirms. Fusion requests (a Solve with no shown axis)
always decide through ``frame_infer.choose_axis``: the confident answer, or the
first allowed axis of the survey's documented fallback ranking (mouth, then the
v2 votes, then as modelled first), recorded as ``automatic-fallback`` with a
developer diagnostic. The model is meshed along that axis, with at most one
retry after the survey, and the decision is stored on the prepared record
(``readings.record_frame_decision``): admission, execution, retry and restart
read that, never a re-derivation. No confirmation is written. A matching manual
choice, including a carried v1 choice, takes precedence. Manual solves still
check the axis the user was shown.

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from server.cadlink.wgreturn import DOCUMENT_UP_AXES, DOCUMENT_UP_FEATURE


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
ROTATIONS: dict[str, tuple[tuple[float, float, float], ...]] = {
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


def require_axis(axis: object) -> str:
    if not isinstance(axis, str) or axis not in ROTATIONS:
        raise ValueError(f"solver frame axis must be one of {', '.join(AXES)}, got {axis!r}")
    return axis


def frame_matrix(axis: str) -> np.ndarray:
    """Contract v1's ``solver_from_assembly`` for one axis (historical records)."""

    rotation = ROTATIONS[require_axis(axis)]
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

    require_axis(forward)
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
    forward = np.asarray(_VECTORS[require_axis(axis)])
    upward = np.asarray(_VECTORS[require_axis(up)])
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
        axis = require_axis(spec.get("axis"))
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
    return require_axis(axis)


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


#: Kept for readers of the earlier contract, where a reduced domain was solved
#: only as modelled. A CAD cut now turns with the frame (:func:`solver_cut_planes`):
#: a reduced domain is solved along any axis lying in all of its planes.
REDUCED_DOMAIN_AXES: tuple[str, ...] = (AS_MODELLED,)


def axes_in_planes(planes: Iterable[str]) -> tuple[str, ...]:
    """The axes that lie in every one of ``planes`` (x0, y0; CAD frame).

    A mirror plane must contain the radiation axis: x = 0 leaves +-y and +-z,
    y = 0 leaves +-x and +-z, both leave +-z.
    """

    normals = {"x0": "x", "y0": "y", "z0": "z"}
    excluded = {normals[str(plane)] for plane in planes if str(plane) in normals}
    return tuple(axis for axis in AXES if axis[1] not in excluded)


def allowed_axes(
    manifest: Mapping[str, Any], domain_planes: Iterable[str] = ()
) -> tuple[str, ...]:
    """The axes this snapshot can be solved along.

    A mirror plane must contain the radiation axis, so a CAD cut allows the
    axes lying in all of its planes, and the cut turns with the frame into the
    solver's mirror planes (:func:`solver_cut_planes`):

    - A declared domain: the axes in its declared planes (an explicit full
      declaration has none, so every axis).
    - ``"kind": "automatic"`` declares nothing, so it is treated as no domain
      at all -- never as a truthy declaration that restricts the axis.
    - ``domain_planes`` are validated pre-cut planes WG mirrors on recorded
      evidence or recovered from the geometry (``domain_interpretation``,
      ``cut_recovery``), in CAD terms.
    - A return with FEM regions keeps a reduced domain as modelled (+z): its
      regions are meshed against the CAD planes.
    """

    from server.cadlink.wgreturn import declared_domain_planes, domain_kind

    planes = tuple(domain_planes)
    if domain_kind(manifest) == "declared":
        planes = tuple(declared_domain_planes(manifest)) or planes
    if not planes:
        return AXES
    inside = axes_in_planes(planes)
    scope = manifest.get("scope")
    if isinstance(scope, Mapping) and scope.get("fem_regions"):
        return tuple(axis for axis in inside if axis in REDUCED_DOMAIN_AXES)
    return inside


def solver_cut_planes(
    frame: str | Mapping[str, Any],
    cad_planes: Iterable[str],
    *,
    negative: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """CAD cut planes turned into the solver's mirror planes by a frame.

    The frame is a proper rotation taking the radiation axis to solver +Z, so
    a CAD plane that contains that axis (x = 0 or y = 0 in CAD, or z = 0 for a
    model radiating along x or y) becomes the solver's x = 0 or y = 0. Its kept
    side turns with it: a positive CAD side can become the solver's negative
    one and the reverse. Returns the solver planes and those of them on whose
    negative side the solver mesh lies (``negative`` names the CAD planes that
    kept their negative side), in canonical order. Reflection is recorded
    apart from the rotation, which stays proper. Raises ValueError for a
    plane square to the radiation axis: it is never a symmetry plane.
    """

    matrix = spec_matrix(frame)[:3, :3]
    held_negative = {str(plane) for plane in negative}
    solver: dict[str, bool] = {}
    for plane in cad_planes:
        name = str(plane)
        if len(name) != 2 or name[0] not in "xyz" or name[1] != "0":
            raise ValueError(f"unknown cut plane {plane!r}")
        column = "xyz".index(name[0])
        row = int(np.argmax(np.abs(matrix[:, column])))
        if row == 2:
            raise ValueError(
                f"the cut on {name} is square to the radiation axis, so it is not a symmetry plane"
            )
        solver["xy"[row] + "0"] = (name in held_negative) != (matrix[row, column] < 0)
    planes = [plane for plane in ("x0", "y0") if plane in solver]
    return planes, [plane for plane in planes if solver[plane]]


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
    #: The axis WG's own confident automatic inference names, when nothing is
    #: confirmed or carried and the caller asked for it (``resolve_for_manifest``
    #: ``automatic=True``): a Solve that never showed the axis meshes in it and
    #: solves along it. Never a confirmation.
    automatic_axis: str | None = None

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
        if self.automatic_axis is not None and self.automatic_axis in self.allowed_axes:
            return self.automatic_axis
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
    return axis if axis in ROTATIONS else None


def automatic_solve_axis(
    suggestion: Mapping[str, Any] | None, allowed: Iterable[str], *, best_effort: bool = False
) -> str | None:
    """The axis a Solve may use without asking.

    ``frame_infer.choose_axis`` is the one rule. Without ``best_effort`` only
    its confident answer (the suggestion's own ``automatic`` verdict, on an
    axis this snapshot allows) is returned, else None. With it -- a Solve that
    shows no axis, a Fusion request -- the fallback ranking's first allowed
    axis when WG is not confident: it always decides.
    """

    from server.cadlink.interpretation.frame_infer import DECISION_AUTOMATIC, choose_axis

    choice = choose_axis(suggestion, allowed)
    if best_effort or choice.provenance == DECISION_AUTOMATIC:
        return choice.axis
    return None


def record_solver_frame(
    manifest: Mapping[str, Any],
    frame: str | Mapping[str, Any],
    *,
    domain_planes: Iterable[str] = (),
) -> dict[str, Any]:
    """What an unlinked ingestion record states about the frame it was meshed in.

    ``domain_planes``: the pre-cut planes the preparation mirrored on evidence.
    """

    spec = dict(frame) if isinstance(frame, Mapping) else frame_spec(frame, manifest)
    if spec.get("contract") != CONTRACT_V2:
        raise ValueError("a new preparation is made under the current solver frame contract")
    matrix = spec_matrix(spec)
    allowed = allowed_axes(manifest, domain_planes)
    if spec["axis"] not in allowed:
        # A mirror plane must contain the radiation axis.
        raise ValueError(
            f"a model cut on {', '.join(domain_planes) or 'its declared planes'} is solved "
            f"only along {', '.join(allowed)}"
        )
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


def record_frame(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    normalisation = record.get("normalisation")
    frame = normalisation.get("solver_frame") if isinstance(normalisation, Mapping) else None
    if (
        isinstance(frame, Mapping)
        and frame.get("contract") in CONTRACTS
        and frame.get("axis") in ROTATIONS
        and isinstance(frame.get("requirement"), Mapping)
        and frame["requirement"].get("contract") == frame.get("contract")
    ):
        return frame
    return None


def resolution_identity(resolution: FrameResolution) -> dict[str, Any]:
    """The complete frame a preparation of this snapshot would mesh in.

    Everything that fixes the transform: the contract, the forward axis, the
    resolved up and where it came from, the stated document up, the export
    frame, and the matrix itself. Compared with :func:`record_frame_identity`.
    """

    spec = resolution.spec
    return {
        "contract": spec["contract"],
        "axis": spec["axis"],
        "up": spec["up"],
        "up_source": spec["up_source"],
        "document_up": spec["document_up"],
        "export_frame": resolution.requirement.get("export_frame"),
        "matrix": spec_matrix(spec).tolist(),
    }


def record_frame_identity(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The complete frame an unlinked record states it was meshed in, or None."""

    frame = record_frame(record)
    if frame is None:
        return None
    requirement = frame.get("requirement")
    return {
        "contract": frame.get("contract"),
        "axis": frame.get("axis"),
        "up": frame.get("up"),
        "up_source": frame.get("up_source"),
        "document_up": frame.get("document_up"),
        "export_frame": requirement.get("export_frame") if isinstance(requirement, Mapping) else None,
        "matrix": frame.get("matrix"),
    }


def record_confirmation_key(record: Mapping[str, Any]) -> str:
    project = record.get("project")
    lineage = project.get("lineage_id") if isinstance(project, Mapping) else None
    return confirmation_key(lineage, str(record.get("manifest_sha256") or ""))


#: How the frame a run was solved in came to be. ``chosen``: a person picked the
#: axis. ``suggested``: a person confirmed the axis WG's survey had proposed.
#: ``automatic``: WG solved in its own automatic axis and nobody confirmed it
#: (the operation's ``frame_axis_automatic`` says so).
#: ``carried``: a project's earlier-contract confirmation carried forward.
#: ``confirmed``: confirmed, but its row predates the recorded provenance.
#: ``linked``: a linked model, solved in its WG design's frame; nothing to choose.
#: ``unconfirmed``: the frame is not confirmed and no automatic axis stands for it.
FRAME_PROVENANCES = (
    "chosen", "suggested", "automatic", "carried", "confirmed", "linked", "unconfirmed",
)


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
    "REDUCED_DOMAIN_AXES",
    "ROTATIONS",
    "allowed_axes",
    "axes_in_planes",
    "carried_axis",
    "confirmation_key",
    "document_up",
    "frame_matrix",
    "frame_requirement",
    "frame_spec",
    "is_unlinked_manifest",
    "automatic_solve_axis",
    "record_confirmation_key",
    "record_frame",
    "record_frame_identity",
    "record_is_unlinked",
    "record_solver_frame",
    "require_axis",
    "resolution_identity",
    "solver_cut_planes",
    "resolve_up",
    "spec_axis",
    "spec_matrix",
]
