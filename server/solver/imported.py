"""Shared contracts for solving immutable CAD-ingestion mesh artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

import numpy as np

from server.contracts.geometry import (
    ImportedSymmetry as ImportedSymmetry,
    ImportedSymmetryUnsupportedError as ImportedSymmetryUnsupportedError,
    SYMMETRY_PLANE_AXIS as _PLANE_NORMAL_INDEX,
    imported_symmetry_from_cut_planes as imported_symmetry_from_cut_planes,
)
from server.mesh.artifact import (
    ImportedMeshArtifactError,
    mesh_text_sha256,
    read_verified_import_mesh,
    verify_record_mesh_text,
)


def imported_domain_planes(record: Mapping[str, Any]) -> tuple[str, ...]:
    """The planes an ingestion record's solve must mirror on.

    This is the one place the distinction is resolved, because two readers that
    disagree about it solve two different models. ``symmetry.domain_planes`` is
    the domain: the planes WG cut here *plus* the planes the CAD author had
    already cut before exporting. ``symmetry.cut_planes`` is only the first set,
    and a return that arrived already reduced has none of them -- reading it
    would resolve a declared half to ``full`` and solve an open shell.

    Records written before ``domain_planes`` existed carry only ``cut_planes``,
    where the two lists are the same.
    """

    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    planes = symmetry.get("domain_planes")
    if planes is None:
        planes = symmetry.get("cut_planes") or []
    return tuple(str(plane) for plane in planes)


def imported_anchor_frame(record: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """The observation frame an ingestion record's solve is measured in.

    The anchor's throat frame, built during normalisation from the anchor's
    transformed axis datum (``server/mesh/imported.py``), with the throat as
    the observation origin. A record with no anchor falls back to the assembly
    frame only when normalisation said that frame *is* the solver frame.

    Every engine reads the frame here, so two engines handed the same record
    measure on the same arc. Returns ``axis`` (unit), ``origin``, ``u``, ``v``,
    ``mouth_center`` and ``source_center`` as float 3-vectors.
    """

    anchor = record.get("anchor")
    anchor = anchor if isinstance(anchor, Mapping) else {}
    frame = anchor.get("throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        native = record.get("native_source")
        if isinstance(native, Mapping) and native.get("required_features") == [
            "native-source-contour-v1",
            "native-front-baffle-woofer-v1",
        ]:
            frame = normalisation.get("source_frame")
        else:
            frame = normalisation.get("anchor_throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        if bool(normalisation.get("assembly_frame_is_solver_frame")):
            frame = {
                "axis": [0.0, 0.0, 1.0],
                "origin_m": [0.0, 0.0, 0.0],
                "u": [1.0, 0.0, 0.0],
                "v": [0.0, 1.0, 0.0],
                "mouth_center_m": [0.0, 0.0, 0.0],
                "source_center_m": [0.0, 0.0, 0.0],
            }
        else:
            raise ValueError(
                "ingestion record has no anchor throat frame; re-ingest the CAD return"
            )

    def vector(name: str, *fallback_names: str) -> np.ndarray:
        value = frame.get(name)
        if value is None:
            for fallback in fallback_names:
                value = frame.get(fallback)
                if value is not None:
                    break
        result = np.asarray(value, dtype=float)
        if result.shape != (3,) or not np.isfinite(result).all():
            raise ValueError(f"ingestion anchor throat frame {name!r} must be a finite 3-vector")
        return result

    axis = vector("axis", "normal")
    axis /= np.linalg.norm(axis)
    source_center = vector("source_center_m", "origin_m", "origin")
    mouth_center = vector("mouth_center_m", "origin_m", "origin")
    return {
        "axis": axis,
        "origin": source_center,
        "u": vector("u", "horizontal"),
        "v": vector("v", "vertical"),
        "mouth_center": mouth_center,
        "source_center": source_center,
    }


def mesh_frequency_validation(record: Mapping[str, Any]) -> Mapping[str, Any]:
    mesh = record.get("mesh")
    mesh = mesh if isinstance(mesh, Mapping) else {}
    metadata = mesh.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    validation = metadata.get("mesh_frequency_validation")
    return validation if isinstance(validation, Mapping) else {}


# -- axial source axes ------------------------------------------------------------------

#: Version of the axial drive contract recorded with every axial channel. v2 is
#: one explicit axis per source tag in solver coordinates, no sign vote and no
#: observation-frame dependence. Results that record no version were solved
#: with the frame-axis sign-vote rule (:data:`LEGACY_AXIAL_CONTRACT`).
AXIAL_CONTRACT_VERSION = "per-source-axis-v2"
LEGACY_AXIAL_CONTRACT = "legacy-frame-axis-v1"

#: An axis within this angle of +-X, +-Y or +-Z of the solver frame snaps to it,
#: so engines agree exactly and a reduced domain stays valid.
AXIAL_SNAP_DEGREES = 0.5

#: A source whose net area vector is below this fraction of its area has no
#: outward direction (a closed or two-sided surface) and is refused.
AXIAL_NET_AREA_FRACTION = 1.0e-3

#: How far off a mirror plane, in metres, a node may sit and still count as
#: lying on it, which is what makes a source "cut" by that plane.
AXIAL_PLANE_TOLERANCE_M = 1.0e-6

#: An axis component along a mirror plane's normal must be below this for the
#: axis to lie in the symmetry subspace.
AXIAL_SUBSPACE_TOLERANCE = 1.0e-9

_SNAP_TARGETS = tuple(
    (name, np.asarray(vector, dtype=float))
    for name, vector in (
        ("+x", (1.0, 0.0, 0.0)),
        ("-x", (-1.0, 0.0, 0.0)),
        ("+y", (0.0, 1.0, 0.0)),
        ("-y", (0.0, -1.0, 0.0)),
        ("+z", (0.0, 0.0, 1.0)),
        ("-z", (0.0, 0.0, -1.0)),
    )
)


class SourceAxisError(ValueError):
    """An axial source has no usable axis; the message says which and why."""


@dataclass(frozen=True, slots=True)
class SourceAxis:
    """One axial source tag's resolved axis, in solver coordinates."""

    tag: int
    axis: tuple[float, float, float]
    raw_axis: tuple[float, float, float]
    snapped_to: str | None
    area_m2: float
    net_area_ratio: float
    projected_planes: tuple[str, ...]
    off_symmetry_planes: tuple[str, ...]

    def in_symmetry_subspace(self) -> bool:
        return not self.off_symmetry_planes

    def metadata(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "axis": list(self.axis),
            "raw_axis": list(self.raw_axis),
            "snapped_to": self.snapped_to,
            "area_m2": self.area_m2,
            "net_area_ratio": self.net_area_ratio,
            "projected_planes": list(self.projected_planes),
        }


def _msh_triangles(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Node coordinates, triangle corner rows and physical tags of a Gmsh 2.2 mesh."""

    lines = msh_text.splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        elements_start = lines.index("$Elements")
        node_count = int(lines[nodes_start + 1])
        element_count = int(lines[elements_start + 1])
        index: dict[str, int] = {}
        coordinates = np.empty((node_count, 3), dtype=float)
        for row, line in enumerate(lines[nodes_start + 2 : nodes_start + 2 + node_count]):
            parts = line.split()
            index[parts[0]] = row
            coordinates[row] = [float(value) for value in parts[1:4]]
        corners: list[list[int]] = []
        tags: list[int] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            if tag_count < 1 or len(parts) < 6 + tag_count:
                continue
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]))
    except (ValueError, IndexError, KeyError) as exc:
        raise ValueError("imported mesh artifact is not a readable Gmsh 2.2 mesh") from exc
    return (
        coordinates,
        np.asarray(corners, dtype=np.int64).reshape(-1, 3),
        np.asarray(tags, dtype=np.int64),
    )


#: The length of a source's edges lying in a mirror plane, as a fraction of the
#: source's extent, below which the plane only touches the source.
AXIAL_CUT_EDGE_FRACTION = 1.0e-2


def _plane_cuts_source(coordinates: np.ndarray, corners: np.ndarray, component: int) -> bool:
    """Whether a mirror plane cuts a source, rather than touching it at a point.

    A cut source has face edges lying in the plane, of real length: the mirror
    image completes it. A source that only has a vertex (or a short sliver) on
    the plane is a whole source elsewhere, and its axis must not be projected.
    """

    on_plane = np.abs(coordinates[:, component]) <= AXIAL_PLANE_TOLERANCE_M
    edges = np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    lying = edges[on_plane[edges[:, 0]] & on_plane[edges[:, 1]]]
    if not len(lying):
        return False
    length = float(np.linalg.norm(coordinates[lying[:, 0]] - coordinates[lying[:, 1]], axis=1).sum())
    nodes = coordinates[np.unique(corners)]
    extent = float(np.linalg.norm(nodes.max(axis=0) - nodes.min(axis=0)))
    return length >= AXIAL_CUT_EDGE_FRACTION * extent


def resolve_source_axes(
    msh_text: str, tags: Iterable[int], mirror_planes: Iterable[str] = ()
) -> dict[int, SourceAxis]:
    """The axis each axial source tag moves along, from the solve mesh alone.

    ``axis = normalize(P_sym(sum of n dA over the tag's faces))`` with the
    canonical outward winding, so the axis is outward-positive by
    construction and no sign is ever voted. ``P_sym`` zeroes the component
    along the normal of each active mirror plane -- exact for a source the
    plane cuts (its mirror image completes it), and applied only to a source
    with face edges lying in the plane. A source the plane does not cut keeps its
    unprojected axis, which may then lie outside the symmetry subspace
    (:meth:`SourceAxis.in_symmetry_subspace`); the caller refuses the reduction
    rather than projecting it.

    A net area vector below :data:`AXIAL_NET_AREA_FRACTION` of the tag's area is
    a closed or two-sided surface with no outward axis, and raises
    :class:`SourceAxisError`. An axis within :data:`AXIAL_SNAP_DEGREES` of a
    solver-frame axis snaps to it exactly; the raw axis is kept beside it.
    """

    wanted = sorted({int(tag) for tag in tags})
    planes = tuple(dict.fromkeys(str(plane) for plane in mirror_planes))
    coordinates, corners, mesh_tags = _msh_triangles(msh_text)
    if len(corners):
        points = coordinates[corners]
        cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    else:
        cross = np.zeros((0, 3))
    cos_snap = math.cos(math.radians(AXIAL_SNAP_DEGREES))
    resolved: dict[int, SourceAxis] = {}
    for tag in wanted:
        selected = mesh_tags == tag
        if not selected.any():
            raise SourceAxisError(f"axial source tag {tag} has no faces in the solve mesh")
        # cross is 2 * area * normal, so halving gives n dA.
        net = cross[selected].sum(axis=0) * 0.5
        area = float(np.linalg.norm(cross[selected], axis=1).sum() * 0.5)
        projected: list[str] = []
        for plane in planes:
            component = _PLANE_NORMAL_INDEX.get(plane)
            if component is None:
                continue
            if _plane_cuts_source(coordinates, corners[selected], component):
                net[component] = 0.0
                projected.append(plane)
        magnitude = float(np.linalg.norm(net))
        ratio = magnitude / area if area > 0.0 else 0.0
        if not math.isfinite(ratio) or ratio < AXIAL_NET_AREA_FRACTION:
            raise SourceAxisError(
                f"axial source tag {tag} has no outward axis: its faces' net area vector is "
                f"{ratio:.2g} of its area (a closed or two-sided source). Use normal motion "
                "for it, or split it into one-sided sources."
            )
        raw = net / magnitude
        axis = raw
        snapped_to = None
        for name, target in _SNAP_TARGETS:
            if float(raw @ target) >= cos_snap:
                axis, snapped_to = target.copy(), name
                break
        off = tuple(
            plane
            for plane in planes
            if plane in _PLANE_NORMAL_INDEX
            and abs(float(axis[_PLANE_NORMAL_INDEX[plane]])) > AXIAL_SUBSPACE_TOLERANCE
        )
        resolved[tag] = SourceAxis(
            tag=tag,
            axis=tuple(float(value) for value in axis),
            raw_axis=tuple(float(value) for value in raw),
            snapped_to=snapped_to,
            area_m2=area,
            net_area_ratio=ratio,
            projected_planes=tuple(projected),
            off_symmetry_planes=off,
        )
    return resolved


def axial_channel_tags(
    record: Mapping[str, Any], drive_channels: Iterable[Any]
) -> dict[str, dict[str, int]]:
    """Each axial drive channel's ``{source id: physical tag}`` from the record."""

    def field(channel: Any, name: str) -> Any:
        return channel.get(name) if isinstance(channel, Mapping) else getattr(channel, name, None)

    source_tags = record.get("source_tags")
    source_tags = source_tags if isinstance(source_tags, Mapping) else {}
    result: dict[str, dict[str, int]] = {}
    for channel in drive_channels:
        if str(field(channel, "motion") or "normal") != "axial":
            continue
        members: dict[str, int] = {}
        for source_id in field(channel, "source_ids") or ():
            if str(source_id) not in source_tags:
                raise ValueError(f"ingestion tag map has no active source {str(source_id)!r}")
            members[str(source_id)] = int(source_tags[str(source_id)])
        result[str(field(channel, "id") or "?")] = members
    return result


def resolve_record_axial_axes(
    record: Mapping[str, Any], msh_text: str, drive_channels: Iterable[Any]
) -> dict[int, SourceAxis]:
    """Axes of every axial channel's source tags in the record's solve mesh."""

    channels = axial_channel_tags(record, drive_channels)
    tags = {tag for members in channels.values() for tag in members.values()}
    if not tags:
        return {}
    axes = resolve_source_axes(msh_text, tags, imported_domain_planes(record))
    if record.get("native_source") is not None:
        # Native contour v1 declares the aligned motion axis explicitly;
        # triangulation asymmetry must not tilt it through an area-vector vote.
        from dataclasses import replace
        axes = {tag: replace(item, axis=(0.0,0.0,1.0), raw_axis=(0.0,0.0,1.0), snapped_to="+z") for tag,item in axes.items()}
    return axes


def axial_domain_problem(
    axes: Mapping[int, SourceAxis], planes: Iterable[str]
) -> str | None:
    """Why a mirrored domain cannot carry these axes, or ``None``.

    An axis outside the symmetry subspace is never projected: the reduction is
    refused, so the model is solved whole.
    """

    planes = tuple(planes)
    if not planes:
        return None
    for tag, resolved in sorted(axes.items()):
        if not resolved.in_symmetry_subspace():
            return (
                f"Axial source tag {tag} moves along ({resolved.axis[0]:.4g}, "
                f"{resolved.axis[1]:.4g}, {resolved.axis[2]:.4g}), which is not in the "
                f"symmetry of this mirrored model ({', '.join(planes)}), and the mirror "
                "does not cut the source. Solve it as shown (Change on the model card), "
                "or send the whole model."
            )
    return None


def config_supports_source_axes(config: Any) -> bool:
    """Whether an engine package's ``SolveConfig`` accepts ``source_axes``.

    A dataclass is asked for its field; a plain callable for its parameter or a
    ``**kwargs``. Anything that cannot be inspected is taken as unsupported, so
    a package that cannot show the option is never handed an axial drive.
    """

    import dataclasses
    import inspect

    if config is None:
        return False
    if dataclasses.is_dataclass(config):
        return "source_axes" in {field.name for field in dataclasses.fields(config)}
    try:
        parameters = inspect.signature(config).parameters
    except (TypeError, ValueError):
        return False
    return "source_axes" in parameters or any(
        item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
    )


def has_axial_channel(drive_channels: Iterable[Any] | None) -> bool:
    return any(
        str(
            (channel.get("motion") if isinstance(channel, Mapping) else getattr(channel, "motion", None))
            or "normal"
        )
        == "axial"
        for channel in drive_channels or ()
    )


def axial_metadata(axes: Mapping[int, SourceAxis], tags: Iterable[int]) -> dict[str, Any]:
    """The per-channel record of an axial drive: contract version and axes."""

    return {
        "axial_contract": AXIAL_CONTRACT_VERSION,
        "source_axes": [axes[int(tag)].metadata() for tag in tags],
    }


def prepare_axial_drive(
    record: Mapping[str, Any], msh_text: str, drive_channels: Iterable[Any]
) -> tuple[dict[int, SourceAxis], dict[str, dict[str, Any]]]:
    """Resolve every axial channel's axes for a solve, and their identity.

    Returns ``(axes by tag, identity by channel id)``. The identity is what each
    axial channel's result and pressure basis record: the motion, the contract
    version and the per-tag axes (snapped and raw). An axis outside a mirrored
    domain's symmetry subspace raises :class:`SourceAxisError`: the reduction is
    refused, never projected. A drive with no axial channel returns empty maps.
    """

    channels = list(drive_channels)
    axes = resolve_record_axial_axes(record, msh_text, channels)
    if not axes:
        return {}, {}
    problem = axial_domain_problem(axes, imported_domain_planes(record))
    if problem is not None:
        raise SourceAxisError(problem)
    identity = {
        channel_id: {
            "source_motion": "axial",
            **axial_metadata(axes, members.values()),
        }
        for channel_id, members in axial_channel_tags(record, channels).items()
    }
    return axes, identity


__all__ = [
    "ImportedSymmetry",
    "ImportedSymmetryUnsupportedError",
    "ImportedMeshArtifactError",
    "imported_anchor_frame",
    "imported_domain_planes",
    "imported_symmetry_from_cut_planes",
    "mesh_frequency_validation",
    "mesh_text_sha256",
    "read_verified_import_mesh",
    "verify_record_mesh_text",
    "AXIAL_CONTRACT_VERSION",
    "AXIAL_SNAP_DEGREES",
    "LEGACY_AXIAL_CONTRACT",
    "SourceAxis",
    "SourceAxisError",
    "axial_channel_tags",
    "axial_domain_problem",
    "axial_metadata",
    "config_supports_source_axes",
    "has_axial_channel",
    "prepare_axial_drive",
    "resolve_record_axial_axes",
    "resolve_source_axes",
]
