"""Imported CAD geometry on hornlab-beat-bem.

Metal solves an ingestion record directly
(``server/solver/metal.py``, ``solve_imported_metal_from_msh_text``). The BEAT
package accepts less, and this module closes the gap in the adapter, without a
package change:

* **One velocity tag.** hornlab-beat-bem drives exactly one physical tag and
  treats every other tag as rigid. Each drive channel is therefore solved on
  its own, with its member source tags rewritten to the one driven tag and
  every other tag to the rigid tag. Metal drives every member source of a
  channel at unit weight too, so the merged tag is the same drive, and each
  channel keeps its own complex basis for recombination.
* **A +z frame.** BEAT measures its polar cuts in the mesh's own axes, around
  the mesh origin; its only frame freedom is a translation. The mesh is rotated
  rigidly so the record's anchor throat frame (u, v, axis) becomes
  (+x, +y, +z). Polar cuts and the DI sphere are frame-relative and the surface
  traces are per vertex, so every result maps back unchanged; the run records
  the record's own frame, not BEAT's.
* **Axial drive.** Metal drives each axial source face at ``n . axis`` and flips
  a whole tag whose area-weighted projection is negative, so a tag facing back
  along the axis is pushed outward (``hornlab_metal_bem.bie``,
  ``_build_axial_face_scale``). BEAT drives ``n . z`` and flips nothing, and
  its one driven tag cannot carry a sign. An axial channel is therefore solved
  as up to two groups -- its forward tags, and its backward-facing tags -- and
  the channel is their difference, by linearity.
* **Reduced domains.** BEAT mirrors across its own x = 0, or x = 0 and y = 0,
  with the mesh on the positive side. An x0 half and an x0+y0 quarter execute
  when the rotation leaves those planes, and that side, where they are. A
  y-only half is not representable; submission refuses it by capability
  (``EngineInfo.symmetry_domains``) and :func:`imported_beat_preflight` names
  every other case this module cannot solve before a job exists.

Formulation: BEAT solves Burton-Miller where Metal solves ``complex_k``. Small
same-mesh differences near interior resonances come from that, not from the
frame or the tag merge.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

import numpy as np

from server.jobs.models import ImportedGeometrySource, SolveRequest

from .acoustics import solver_sound_speed_m_per_s
from .base import CancelCallback, ResultCallback, StageCallback
from .beat import (
    BeatUnavailable,
    _load_api,
    announce_beat_warmup_wait,
    beat_backend_statuses,
    beat_engine_name,
)
from .combine import combine_drive_channels, serialize_channel_bases
from .context import SolverContext
from .driver_limits import MemberLimits, member_limits_from_channel
from .field_traces_store import (
    BEAT_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .frequency_sweep import live_execution_frequencies, sort_native_result_frequencies
from .imported import (
    imported_anchor_frame,
    imported_domain_planes,
    imported_symmetry_from_cut_planes,
)
from .metal import (
    _apply_channel_driver,
    _channel_basis_metadata,
    _channel_source_identity,
    _imported_validity_metadata,
)
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    observation_config,
    response_solver_log,
)


logger = logging.getLogger(__name__)

#: The tag BEAT drives, and the tag every other surface of a channel's mesh
#: becomes. Rewriting every tag to one of the two means no source tag of the
#: record can collide with the driven tag.
VELOCITY_TAG = 2
RIGID_TAG = 1

#: How far the anchor frame may depart from a right-handed orthonormal basis,
#: and how far a rotated mirror plane may tilt, before BEAT refuses the return.
FRAME_TOLERANCE = 1.0e-6

#: How far off a mirror plane, in metres, the observation origin or a kept-side
#: vertex may sit before a reduced return is refused rather than solved with
#: its mirror moved.
MIRROR_PLANE_TOLERANCE_M = 1.0e-6

PASSIVE_CARDIOID_REFUSAL = (
    "The passive-cardioid radiation campaign is implemented on Metal only. "
    "Turn the passive cardioid off to solve this return on BEAT, or solve it "
    "with Metal."
)

#: Which of BEAT's own axes each representable native plane mirrors across.
_MIRROR_AXES: dict[str | None, tuple[int, ...]] = {
    None: (),
    "yz": (0,),
    "yz+xz": (0, 1),
}
_AXIS_NAMES = ("x", "y", "z")
_DRIVE_CONVENTION = (
    "q=i*rho*omega*v_n on a 1 m/s velocity basis, rescaled to unit normal "
    "acceleration by the package"
)


class ImportedBeatRefusal(ValueError):
    """A return BEAT cannot solve as prepared, with the reason named."""


@dataclass(frozen=True, slots=True)
class BeatImportedFrame:
    """The rigid map from the record's solver frame into BEAT's +z frame.

    ``rotation`` has the record's u, v and axis as rows, so it maps them onto
    +x, +y and +z. ``origin``, ``mouth_center`` and ``source_center`` are
    already in BEAT's frame. ``record_frame`` is the frame the run reports.
    """

    rotation: np.ndarray
    origin: np.ndarray
    mouth_center: np.ndarray
    source_center: np.ndarray
    record_frame: Mapping[str, np.ndarray]


def _vector_text(vector: np.ndarray) -> str:
    return "(" + ", ".join(f"{float(value):.6g}" for value in vector) + ")"


def beat_imported_frame(
    record: Mapping[str, Any], native_plane: str | None
) -> BeatImportedFrame:
    """Rotate the record's anchor frame onto BEAT's, or say why it cannot be."""

    if native_plane not in _MIRROR_AXES:
        raise ImportedBeatRefusal(
            "This return is a y-only half, mirrored on y = 0. BEAT mirrors "
            "across x = 0, or across x = 0 and y = 0, and has no y-only "
            "mirror. Solve it with Metal, or return the model whole or cut on "
            "x = 0 from CAD."
        )
    parts = imported_anchor_frame(record)
    rotation = np.vstack([parts["u"], parts["v"], parts["axis"]]).astype(float)
    if not np.allclose(
        rotation @ rotation.T, np.eye(3), atol=FRAME_TOLERANCE
    ) or np.linalg.det(rotation) <= 0.0:
        raise ImportedBeatRefusal(
            "BEAT rotates an imported mesh into its own +z frame, which needs "
            "the record's anchor throat frame to be a right-handed orthonormal "
            f"basis. This record's is not: horizontal {_vector_text(parts['u'])}, "
            f"vertical {_vector_text(parts['v'])}, axis {_vector_text(parts['axis'])}. "
            "Re-ingest the CAD return, or solve it with Metal."
        )
    mirrors = _MIRROR_AXES[native_plane]
    for index in mirrors:
        axis_name = _AXIS_NAMES[index]
        if not np.allclose(rotation[:, index], np.eye(3)[index], atol=FRAME_TOLERANCE):
            raise ImportedBeatRefusal(
                f"This return is mirrored on {axis_name} = 0. BEAT mirrors across "
                f"its own {axis_name} = 0 after rotating the mesh into its +z "
                "frame, and this return's anchor frame would move that plane: "
                f"horizontal {_vector_text(parts['u'])}, vertical "
                f"{_vector_text(parts['v'])}, axis {_vector_text(parts['axis'])}. "
                "Solve it with Metal, or return the model whole from CAD."
            )
    origin = rotation @ parts["origin"]
    for index in mirrors:
        axis_name = _AXIS_NAMES[index]
        if abs(float(origin[index])) > MIRROR_PLANE_TOLERANCE_M:
            raise ImportedBeatRefusal(
                f"This return is mirrored on {axis_name} = 0, but its observation "
                f"origin, the anchor throat, lies {float(origin[index]) * 1.0e3:.4g} mm "
                "off that plane. BEAT observes around its own origin, so centring "
                "on this throat would move the mirror with it. Solve it with "
                "Metal, or return the model whole from CAD."
            )
        # Exactly on the plane, so BEAT's translation cannot shift the mirror.
        origin[index] = 0.0
    return BeatImportedFrame(
        rotation=rotation,
        origin=origin,
        mouth_center=rotation @ parts["mouth_center"],
        source_center=rotation @ parts["source_center"],
        record_frame=parts,
    )


@dataclass(frozen=True, slots=True)
class _Gmsh22Mesh:
    """An ASCII Gmsh 2.2 surface mesh, held so it can be rotated and re-tagged."""

    format_lines: tuple[str, ...]
    node_ids: tuple[str, ...]
    coordinates: np.ndarray
    elements: tuple[tuple[str, ...], ...]

    @classmethod
    def parse(cls, msh_text: str) -> "_Gmsh22Mesh":
        lines = [line.strip() for line in msh_text.splitlines()]
        try:
            format_start = lines.index("$MeshFormat")
            format_end = lines.index("$EndMeshFormat")
            nodes_start = lines.index("$Nodes")
            elements_start = lines.index("$Elements")
            version = lines[format_start + 1].split()
            node_count = int(lines[nodes_start + 1])
            element_count = int(lines[elements_start + 1])
        except (ValueError, IndexError) as exc:
            raise ValueError(
                "an imported BEAT solve needs an ASCII Gmsh 2.2 mesh artifact"
            ) from exc
        if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
            raise ValueError(
                "an imported BEAT solve needs an ASCII Gmsh 2.2 mesh artifact, "
                f"not format {' '.join(version)!r}"
            )
        node_ids: list[str] = []
        coordinates = np.empty((node_count, 3), dtype=float)
        try:
            for row_index, row in enumerate(
                lines[nodes_start + 2 : nodes_start + 2 + node_count]
            ):
                parts = row.split()
                node_ids.append(parts[0])
                coordinates[row_index] = [float(value) for value in parts[1:4]]
        except (IndexError, ValueError) as exc:
            raise ValueError("imported mesh artifact contains invalid Gmsh nodes") from exc
        if len(node_ids) != node_count:
            raise ValueError("imported mesh artifact is missing Gmsh nodes")
        elements = tuple(
            tuple(row.split())
            for row in lines[elements_start + 2 : elements_start + 2 + element_count]
        )
        if len(elements) != element_count:
            raise ValueError("imported mesh artifact is missing Gmsh elements")
        return cls(
            format_lines=tuple(lines[format_start : format_end + 1]),
            node_ids=tuple(node_ids),
            coordinates=coordinates,
            elements=elements,
        )

    def rotated(self, rotation: np.ndarray) -> "_Gmsh22Mesh":
        return _Gmsh22Mesh(
            format_lines=self.format_lines,
            node_ids=self.node_ids,
            coordinates=self.coordinates @ np.asarray(rotation, dtype=float).T,
            elements=self.elements,
        )

    def triangles(self) -> tuple[np.ndarray, np.ndarray]:
        """Every triangle's node rows (into ``coordinates``) and physical tag."""

        index = {node_id: row for row, node_id in enumerate(self.node_ids)}
        corners: list[list[int]] = []
        tags: list[int] = []
        for parts in self.elements:
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            if tag_count < 1 or len(parts) < 6 + tag_count:
                continue
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]))
        return (
            np.asarray(corners, dtype=np.int64).reshape(-1, 3),
            np.asarray(tags, dtype=np.int64),
        )

    def triangle_tags(self) -> set[int]:
        return {int(tag) for tag in self.triangles()[1]}

    def axial_orientation(self) -> dict[int, tuple[float, float]]:
        """Each physical tag's area-weighted ``n . z`` and its total area.

        A triangle's raw cross product is twice its area times its unit normal,
        so summing its z components gives Metal's area-weighted projection
        (``_build_axial_face_scale``) up to the same positive factor as the
        summed magnitudes -- the scale the projection's sign is judged on. The
        mesh is already in BEAT's frame, where the axis is +z.
        """

        corners, tags = self.triangles()
        if not len(tags):
            return {}
        points = self.coordinates[corners]
        cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
        areas = np.linalg.norm(cross, axis=1)
        return {
            int(tag): (float(cross[tags == tag, 2].sum()), float(areas[tags == tag].sum()))
            for tag in np.unique(tags)
        }

    def text(self, velocity_tags: frozenset[int]) -> str:
        """The mesh with ``velocity_tags`` driven and every other tag rigid."""

        rows = [
            *self.format_lines,
            "$PhysicalNames",
            "2",
            f'2 {RIGID_TAG} "rigid"',
            f'2 {VELOCITY_TAG} "source"',
            "$EndPhysicalNames",
            "$Nodes",
            str(len(self.node_ids)),
        ]
        rows.extend(
            f"{node_id} {x:.17g} {y:.17g} {z:.17g}"
            for node_id, (x, y, z) in zip(self.node_ids, self.coordinates, strict=True)
        )
        rows.extend(["$EndNodes", "$Elements", str(len(self.elements))])
        for parts in self.elements:
            if len(parts) > 3 and int(parts[2]) >= 1:
                tag = VELOCITY_TAG if int(parts[3]) in velocity_tags else RIGID_TAG
                if parts[1] == "2" and parts[2] == "1":
                    # BEAT reads a triangle's physical tag only from a row with
                    # both tags; a one-tag row would be skipped as no triangle.
                    parts = (parts[0], "2", "2", str(tag), str(tag), *parts[4:])
                else:
                    parts = (*parts[:3], str(tag), *parts[4:])
            rows.append(" ".join(parts))
        rows.append("$EndElements")
        return "\n".join(rows) + "\n"


#: How negative a tag's area-weighted ``n . axis`` must be, as a fraction of its
#: area, before it counts as facing backwards. A closed tag projects to zero,
#: and rounding alone must not decide its sign.
AXIAL_ORIENTATION_TOLERANCE = 1.0e-9


def _drive_groups(
    tags: frozenset[int], motion: str, orientation: Mapping[int, tuple[float, float]]
) -> list[tuple[float, frozenset[int]]]:
    """The signed tag groups one channel is solved as.

    A normal drive is one group. An axial drive is Metal's: a tag whose
    area-weighted ``n . axis`` is negative is flipped whole so it drives
    outward. BEAT's one driven tag cannot carry that sign, so the flipped tags
    are solved as their own group and subtracted.

    A tag whose projection is zero to rounding -- a closed body's, whose
    faces cancel -- has no outward direction along the axis and is driven
    ``n . axis`` unflipped. Metal decides that case by the sign of its rounding
    residue, so the two engines can disagree only for a closed axial tag.
    """

    if motion != "axial":
        return [(1.0, tags)]
    backward = frozenset(
        tag
        for tag in tags
        if orientation.get(tag, (0.0, 0.0))[0]
        < -AXIAL_ORIENTATION_TOLERANCE * orientation.get(tag, (0.0, 0.0))[1]
    )
    return [
        (sign, group)
        for sign, group in ((1.0, tags - backward), (-1.0, backward))
        if group
    ]


def _signed_sum(parts: Sequence[tuple[float, Any]]) -> Any:
    """One channel's result from its signed group solves, by linearity."""

    first_sign, first = parts[0]
    if len(parts) == 1 and first_sign == 1.0:
        return first
    combined = copy(first)

    def total(name: str) -> Any:
        values = [getattr(result, name, None) for _, result in parts]
        if any(value is None for value in values):
            return None
        return sum(
            sign * np.asarray(value, dtype=np.complex128)
            for (sign, _), value in zip(parts, values, strict=True)
        )

    for name in (
        "pressure_complex",
        "impedance",
        "sphere_pressure_complex",
        "surface_pressure_complex",
        "surface_neumann_complex",
    ):
        if hasattr(first, name):
            setattr(combined, name, total(name))
    with np.errstate(divide="ignore"):
        combined.spl_db = 20.0 * np.log10(
            np.abs(np.asarray(combined.pressure_complex)) / 20.0e-6
        )
    timings: dict[str, float] = {}
    for _, result in parts:
        for key, value in dict(getattr(result, "timings", {}) or {}).items():
            if isinstance(value, (int, float)):
                timings[key] = timings.get(key, 0.0) + float(value)
    combined.timings = timings
    return combined


def _check_kept_side(mesh: _Gmsh22Mesh, native_plane: str | None) -> None:
    """BEAT solves the positive side of every mirror; refuse the other side.

    Only nodes a triangle uses are the surface; an orphan node is not.
    """

    corners, _tags = mesh.triangles()
    coordinates = mesh.coordinates[np.unique(corners)] if len(corners) else mesh.coordinates[:0]
    for index in _MIRROR_AXES.get(native_plane, ()):
        minimum = float(np.min(coordinates[:, index])) if len(coordinates) else 0.0
        if minimum < -MIRROR_PLANE_TOLERANCE_M:
            axis_name = _AXIS_NAMES[index]
            raise ImportedBeatRefusal(
                f"This return is mirrored on {axis_name} = 0, but its mesh reaches "
                f"{axis_name} = {minimum * 1.0e3:.4g} mm on the negative side. BEAT "
                "solves only the positive side of a mirrored domain. Solve it "
                "with Metal, or return the whole model from CAD."
            )


def imported_beat_preflight(record: Mapping[str, Any], msh_text: str) -> str | None:
    """Why BEAT cannot solve this return as prepared, or ``None``.

    Read-only and Julia-free, so submission can ask it before a job exists:
    the same frame and positive-side checks the solve makes.
    """

    try:
        symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
        frame = beat_imported_frame(record, symmetry.native_plane)
        mesh = _Gmsh22Mesh.parse(msh_text).rotated(frame.rotation)
        _check_kept_side(mesh, symmetry.native_plane)
    except ImportedBeatRefusal as exc:
        return str(exc)
    except ValueError as exc:
        return f"BEAT cannot read this return's solve inputs: {exc}"
    return None


def _frame_basis(frame: BeatImportedFrame) -> dict[str, list[float]]:
    parts = frame.record_frame
    return {
        key: [float(value) for value in parts[name]]
        for key, name in (
            ("axis", "axis"),
            ("u", "u"),
            ("v", "v"),
            ("origin_m", "origin"),
            ("mouth_center_m", "mouth_center"),
            ("source_center_m", "source_center"),
        )
    }


def _beat_section(
    *,
    backend: str,
    native_plane: str | None,
    merged_tags: list[int] | None,
    result: Any,
    reversed_tags: list[int] | None = None,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "native_symmetry_plane": native_plane,
        "formulation": "burton_miller",
        "backend": backend,
        "drive_convention": _DRIVE_CONVENTION,
        "precision": "single",
        "solver_log": json_safe_native_value(
            response_solver_log(getattr(result, "solver_log", []))
        ),
    }
    if merged_tags is not None:
        # The record's tags this channel drove as BEAT's one velocity tag.
        section["merged_source_tags"] = merged_tags
    if reversed_tags:
        # Axial tags facing back along the axis, driven outward as Metal
        # drives them: solved as their own group and subtracted.
        section["axially_reversed_source_tags"] = reversed_tags
    return section


def _combined_channel_response(
    *,
    geometry: ImportedGeometrySource,
    sorted_results: Mapping[str, Any],
    request: SolveRequest,
    quadrants: int,
    config: Any,
    config_motion: str,
    started: float,
    status: Mapping[str, Any],
    backend: str,
    native_plane: str | None,
    per_source_validity: Mapping[str, Any],
    channel_identity: Mapping[str, Mapping[str, Any]],
    member_channels: Mapping[str, Any],
    frame_basis: Mapping[str, Any],
) -> dict[str, Any]:
    """BEAT's counterpart of Metal's combined channel: the filtered sum."""

    spec = geometry.combine
    assert spec is not None
    channels_by_id = {channel.id: channel for channel in geometry.drive_channels}
    member_validity_hz: dict[str, float] = {}
    for member in spec.members:
        limits = [
            float(item["effective_max_valid_frequency_hz"])
            for source_id in channels_by_id[member].source_ids
            if isinstance(item := per_source_validity.get(source_id), Mapping)
            and item.get("effective_max_valid_frequency_hz") is not None
        ]
        if limits:
            member_validity_hz[member] = min(limits)
    frequencies_hz = np.asarray(
        sorted_results[spec.members[0]].frequencies_hz, dtype=np.float64
    ).reshape(-1)
    member_limits: dict[str, MemberLimits] = {}
    for member in spec.members:
        limits = member_limits_from_channel(
            member_channels.get(member),
            frequencies_hz=frequencies_hz,
            max_voltage_v=geometry.max_drive_voltage_v,
        )
        if limits is not None:
            member_limits[member] = limits

    resolved = spec.resolved()
    combined_result, combine_payload = combine_drive_channels(
        sorted_results,
        members=list(spec.members),
        channels=resolved["channels"],
        reference=resolved["reference"],
        member_validity_hz=member_validity_hz,
        member_roles={
            member: channel_identity.get(member, {}).get("role")
            for member in spec.members
        },
        member_limits=member_limits,
    )
    source_ids = [
        source_id
        for member in spec.members
        for source_id in channels_by_id[member].source_ids
    ]
    metadata = {
        "solver_backend": "beat",
        "solver_mode": "full_3d",
        "geometry_type": "imported",
        "drive_channel_id": spec.id,
        "derived_from_channels": list(spec.members),
        "source_ids": source_ids,
        "device_interface": {
            "selected": beat_engine_name(backend),
            beat_engine_name(backend): dict(status),
        },
        "engine": "hornlab-beat-bem",
        "phase_time_convention": "exp(+ikr)",
        "combine": combine_payload,
        "mesh_validation": {
            "mode": request.options.mesh_validation_mode,
            "backend": "hornlab-beat-bem",
        },
        "performance": {"total_time_seconds": time.time() - started},
        "observation_frame_basis": dict(frame_basis),
        "beat": _beat_section(
            backend=backend,
            native_plane=native_plane,
            merged_tags=None,
            result=combined_result,
        ),
    }
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    response = build_solver_response(
        result=combined_result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
    )
    response.pop("impedance", None)
    response["metadata"]["impedance_omitted"] = (
        "combined channel: member drives differ; no single impedance exists"
    )
    return response


def solve_imported_beat_from_msh_text(
    msh_text: str,
    request: SolveRequest,
    record: Mapping[str, Any],
    *,
    backend: str,
    field_trace_cap_bytes: int | None = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Solve every imported drive channel on BEAT, one merged-tag solve each.

    Returns Metal's imported envelope: a ``multi_channel`` result with one
    contract-shaped channel per drive channel (plus a combined channel when the
    request has one), the channel-bases NPZ under ``_channel_bases_npz`` and
    the surface traces under ``_field_traces``.
    """

    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        raise ValueError("imported BEAT solve requires imported geometry")
    if request.options.ground_plane.enabled:
        raise BeatUnavailable(
            "The HornLab BEAT adapter cannot apply a rigid ground plane to "
            "imported geometry."
        )
    if geometry.passive_cardioid_enabled:
        raise BeatUnavailable(PASSIVE_CARDIOID_REFUSAL)
    package = _load_api()
    if package is None:
        raise BeatUnavailable("hornlab-beat-bem is not installed.")
    status = beat_backend_statuses().get(backend)
    if status is None:
        raise BeatUnavailable(f"Unknown BEAT backend {backend!r}")
    if not status["available"]:
        raise BeatUnavailable(status["reason"])

    domain_planes = imported_domain_planes(record)
    imported_symmetry = imported_symmetry_from_cut_planes(domain_planes)
    quadrants = imported_symmetry.quadrants
    native_plane = imported_symmetry.native_plane
    source_tags = record.get("source_tags")
    if not isinstance(source_tags, Mapping):
        raise ValueError("ingestion record has no source tag map")
    try:
        frame = beat_imported_frame(record, native_plane)
        mesh = _Gmsh22Mesh.parse(msh_text).rotated(frame.rotation)
        _check_kept_side(mesh, native_plane)
    except ImportedBeatRefusal as exc:
        raise BeatUnavailable(str(exc)) from exc
    present_tags = mesh.triangle_tags()
    channel_tags: dict[str, frozenset[int]] = {}
    for channel in geometry.drive_channels:
        tags: set[int] = set()
        for source_id in channel.source_ids:
            if source_id not in source_tags:
                raise ValueError(f"ingestion tag map has no active source {source_id!r}")
            tags.add(int(source_tags[source_id]))
        missing = sorted(tags - present_tags)
        if missing:
            raise ValueError(
                f"imported mesh has no triangles for drive channel {channel.id!r} "
                f"source tags {missing}"
            )
        channel_tags[channel.id] = frozenset(tags)

    channel_order = [channel.id for channel in geometry.drive_channels]
    started = time.time()
    if stage_callback:
        stage_callback(
            "setup", 0.0, f"Configuring imported BEAT Engine BEM solve ({backend})"
        )
    announce_beat_warmup_wait(stage_callback)

    motions = {channel.motion for channel in geometry.drive_channels}
    config_motion = next(iter(motions)) if len(motions) == 1 else "normal"
    context = SolverContext.from_imported_request(
        request, quadrants=quadrants, source_motion=config_motion
    )
    context.validate()
    mesh_record = record.get("mesh")
    mesh_record = mesh_record if isinstance(mesh_record, Mapping) else {}
    imported_mesh_stats = mesh_record.get("stats")
    imported_mesh_stats = (
        imported_mesh_stats if isinstance(imported_mesh_stats, Mapping) else None
    )
    frequencies = live_execution_frequencies(context).tolist()
    field_plane_enabled = (
        getattr(context, "polar_config", {}).get("field_plane", True) is True
    )
    retain_traces, trace_reason, trace_estimated_bytes, trace_cap_bytes = (
        field_trace_retention_plan(
            msh_text,
            mesh_stats=imported_mesh_stats,
            frequency_count=len(frequencies),
            channel_count=len(geometry.drive_channels),
            enabled=field_plane_enabled,
            supported=bool(status.get("surface_traces")),
            cap_bytes=field_trace_cap_bytes,
            unsupported_reason="unsupported_solver_version",
        )
    )
    channel_identity = _channel_source_identity(geometry, record)
    frame_basis = _frame_basis(frame)
    observation = observation_config(
        context, package.ObservationConfig, BeatUnavailable, "hornlab-beat-bem"
    )
    beat_frame = package.ObservationFrame(
        axis=np.asarray([0.0, 0.0, 1.0]),
        origin=np.asarray(frame.origin, dtype=float),
        u=np.asarray([1.0, 0.0, 0.0]),
        v=np.asarray([0.0, 1.0, 0.0]),
        mouth_center=np.asarray(frame.mouth_center, dtype=float),
        source_center=np.asarray(frame.source_center, dtype=float),
    )
    orientation = (
        mesh.axial_orientation()
        if any(channel.motion == "axial" for channel in geometry.drive_channels)
        else {}
    )
    channel_groups = {
        channel.id: _drive_groups(channel_tags[channel.id], channel.motion, orientation)
        for channel in geometry.drive_channels
    }
    frequency_count = len(frequencies)
    channel_count = len(geometry.drive_channels)
    total_work = max(
        1, frequency_count * sum(len(groups) for groups in channel_groups.values())
    )

    def stage_status(message: str) -> None:
        if stage_callback and message:
            stage_callback("setup", 0.0, message)

    sorted_results: dict[str, Any] = {}
    configs: dict[str, Any] = {}
    work_done = 0
    # The runtime keeps one revision per streamed frame and drops any that
    # does not advance it, so frames are numbered across channels.
    next_revision = [0]
    for channel_index, channel in enumerate(geometry.drive_channels):
        channel_context = SolverContext.from_imported_request(
            request, quadrants=quadrants, source_motion=channel.motion
        )
        groups = channel_groups[channel.id]
        # A channel solved as two groups is streamed only while the last one
        # solves, each frame the signed sum of both groups at that frequency.
        earlier: dict[int, dict[str, Any]] = {}
        parts: list[tuple[float, Any]] = []
        holder: dict[str, Any] = {}
        for group_index, (sign, group_tags) in enumerate(groups):
            last_group = group_index == len(groups) - 1

            def progress(
                index: int,
                total: int,
                frequency_hz: float,
                *,
                _offset: int = work_done,
                _channel_index: int = channel_index,
                _channel_id: str = channel.id,
            ) -> None:
                del frequency_hz
                if cancellation_callback:
                    cancellation_callback()
                if stage_callback:
                    stage_callback(
                        "frequency_solve",
                        (_offset + index + 1) / total_work,
                        f"Solving frequency {index + 1}/{total} of drive channel "
                        f"{_channel_index + 1}/{channel_count} ({_channel_id}) "
                        "with BEAT Engine",
                    )

            def on_frequency_result(
                index: int,
                frequency_hz: float,
                entry: dict[str, Any],
                *,
                _sign: float = sign,
                _last: bool = last_group,
                _earlier: dict[int, dict[str, Any]] = earlier,
                _channel_index: int = channel_index,
                _channel: Any = channel,
                _context: SolverContext = channel_context,
                _holder: dict[str, Any] = holder,
            ) -> bool:
                if cancellation_callback:
                    cancellation_callback()
                if result_callback is None:
                    return True
                pressure = _sign * np.asarray(
                    entry.get("observation_pressure_complex"), dtype=np.complex128
                )
                impedance = entry.get("impedance")
                impedance = None if impedance is None else _sign * complex(impedance)
                prior = _earlier.get(index)
                if prior is not None:
                    pressure = prior["pressure"] + pressure
                    impedance = (
                        None
                        if impedance is None or prior["impedance"] is None
                        else prior["impedance"] + impedance
                    )
                if not _last:
                    _earlier[index] = {"pressure": pressure, "impedance": impedance}
                    return True
                with np.errstate(divide="ignore"):
                    spl = 20.0 * np.log10(np.abs(pressure) / 20.0e-6)
                channel_response = build_provisional_frequency_response(
                    index=index,
                    frequency_hz=frequency_hz,
                    entry={
                        "observation_angles_deg": entry.get("observation_angles_deg"),
                        "observation_planes": entry.get("observation_planes"),
                        "observation_spl_db": spl,
                        "observation_pressure_complex": pressure,
                        "impedance": impedance,
                    },
                    config=_holder["config"],
                    context=_context,
                    backend="beat",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
                )
                if len(_channel.source_ids) > 1:
                    channel_response.pop("impedance", None)
                channel_metadata = channel_response.setdefault("metadata", {})
                channel_metadata.update(channel_identity[_channel.id])
                channel_metadata["observation_frame_basis"] = dict(frame_basis)
                revision = next_revision[0]
                next_revision[0] += 1
                frame: dict[str, Any] = {
                    "result_kind": "multi_channel",
                    "result_contract_version": 2,
                    "channels": {_channel.id: channel_response},
                    "channel_order": channel_order,
                    "metadata": {
                        "geometry_type": "imported",
                        # Channels arrive one after another here, so the
                        # count is the current channel's, out of the sweep.
                        "provisional": {
                            "completed_frequency_count": int(index) + 1,
                            "expected_frequency_count": frequency_count,
                            "channel": {
                                "id": _channel.id,
                                "index": _channel_index + 1,
                                "count": channel_count,
                            },
                        },
                    },
                }
                if _channel_index == 0:
                    # The envelope's frequency axis is the sweep's: each
                    # frequency once, from the first channel's frames.
                    frame["frequencies"] = [float(frequency_hz)]
                result_callback(revision, frame)
                return True

            try:
                config = package.SolveConfig(
                    freq_min_hz=context.frequency_range[0],
                    freq_max_hz=context.frequency_range[1],
                    freq_count=context.num_frequencies,
                    freq_spacing=context.frequency_spacing,
                    velocity_sources={VELOCITY_TAG: 1.0},
                    source_motion=channel.motion,
                    observation=observation,
                    frame_override=beat_frame,
                    native_symmetry_plane=native_plane,
                    mesh_scale=1.0,
                    beat_backend=backend,
                    **({"surface_traces": True} if retain_traces else {}),
                    progress_callback=progress,
                    on_frequency_result=(
                        on_frequency_result if result_callback is not None else None
                    ),
                )
                package.reject_unsupported_native_symmetry(config)
            except NotImplementedError as exc:
                raise BeatUnavailable(str(exc)) from exc
            holder["config"] = config

            path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".msh", delete=False, encoding="utf-8"
                ) as handle:
                    path = Path(handle.name)
                    handle.write(mesh.text(group_tags))
                try:
                    result = package.solve_frequencies(
                        str(path), frequencies, config, status_callback=stage_status
                    )
                except NotImplementedError as exc:
                    raise BeatUnavailable(str(exc)) from exc
            finally:
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(
                            "Could not remove temporary BEAT mesh %s: %s", path, exc
                        )
            sort_native_result_frequencies(result)
            parts.append((sign, result))
            work_done += frequency_count
        sorted_results[channel.id] = _signed_sum(parts)
        configs[channel.id] = holder["config"]

    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging imported drive-channel bases")

    driver_payloads: dict[str, dict[str, Any]] = {}
    for channel in geometry.drive_channels:
        if channel.driver is None:
            continue
        result = sorted_results[channel.id]
        # The driver model reads the driven source's average surface pressure,
        # which BEAT reports as ``impedance``. A solve that returned none
        # cannot be voltage-driven, and NaN must not become a driver answer.
        if not np.all(np.isfinite(np.asarray(result.impedance, dtype=np.complex128))):
            raise BeatUnavailable(
                f"BEAT returned no source-average pressure for drive channel "
                f"{channel.id!r}, which its driver model needs."
            )
        driver_payloads[channel.id] = _apply_channel_driver(
            channel,
            result,
            record,
            source_tags,
            drive_voltage_v=geometry.drive_voltage_v,
            rg_ohm=geometry.rg_ohm,
        )

    channels: dict[str, Any] = {}
    for channel in geometry.drive_channels:
        result = sorted_results[channel.id]
        channel_context = SolverContext.from_imported_request(
            request, quadrants=quadrants, source_motion=channel.motion
        )
        channel_metadata = {
            "solver_backend": "beat",
            "solver_mode": "full_3d",
            "geometry_type": "imported",
            "drive_channel_id": channel.id,
            "source_ids": list(channel.source_ids),
            **channel_identity[channel.id],
            "device_interface": {
                "selected": beat_engine_name(backend),
                beat_engine_name(backend): status,
            },
            "engine": "hornlab-beat-bem",
            "phase_time_convention": "exp(+ikr)",
            "mesh_validation": {
                "mode": context.mesh_validation_mode,
                "backend": "hornlab-beat-bem",
            },
            "performance": {
                "total_time_seconds": time.time() - started,
                "native_timings": json_safe_native_value(
                    dict(getattr(result, "timings", {}) or {})
                ),
            },
            # Set before the response is built, so the record's frame is
            # reported and not BEAT's rotated +z frame.
            "observation_frame_basis": dict(frame_basis),
            "beat": _beat_section(
                backend=backend,
                native_plane=native_plane,
                merged_tags=sorted(channel_tags[channel.id]),
                result=result,
                reversed_tags=sorted(
                    tag
                    for sign, group in channel_groups[channel.id]
                    if sign < 0.0
                    for tag in group
                ),
            ),
        }
        channel_response = build_solver_response(
            result=result,
            config=configs[channel.id],
            context=channel_context,
            start_time=started,
            metadata=channel_metadata,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
        )
        if len(channel.source_ids) > 1:
            channel_response.pop("impedance", None)
            channel_response["metadata"]["impedance_omitted"] = (
                "multi-source channel: per-patch impedance is not a channel impedance"
            )
        driver_payload = driver_payloads.get(channel.id)
        if driver_payload is not None:
            electrical = driver_payload.pop("electrical_impedance_ohm")
            channel_response["impedance"] = {
                "frequencies": electrical["frequencies"],
                "real": electrical["real"],
                "imaginary": electrical["imaginary"],
            }
            response_metadata = channel_response["metadata"]
            response_metadata["impedance_units"] = "ohms"
            response_metadata["impedance_quantity"] = "electrical_input_impedance"
            response_metadata["impedance_phase_convention"] = "engineering_exp_plus_jwt"
            response_metadata["impedance_drive"] = "voltage"
            response_metadata["driver"] = driver_payload
            response_metadata["drive"] = {
                "voltage_v": geometry.drive_voltage_v,
                "rg_ohm": geometry.rg_ohm,
            }
            warnings = response_metadata.setdefault("warnings", [])
            warnings.extend(driver_payload.get("warnings") or [])
            response_metadata["warning_count"] = len(warnings)
        channels[channel.id] = channel_response

    per_source_validity = _imported_validity_metadata(record)
    channel_bases_npz = serialize_channel_bases(
        sorted_results,
        metadata_by_id=_channel_basis_metadata(
            geometry, record, source_tags, driver_payloads
        ),
    )
    first_config = configs[geometry.drive_channels[0].id]
    if geometry.combine is not None:
        channels[geometry.combine.id] = _combined_channel_response(
            geometry=geometry,
            sorted_results=sorted_results,
            request=request,
            quadrants=quadrants,
            config=first_config,
            config_motion=config_motion,
            started=started,
            status=status,
            backend=backend,
            native_plane=native_plane,
            per_source_validity=per_source_validity,
            channel_identity=channel_identity,
            member_channels=channels,
            frame_basis=frame_basis,
        )
        channel_order.append(geometry.combine.id)
    fem_volumes = (
        (record.get("evidence") or {}).get("fem_air_volumes")
        if isinstance(record.get("evidence"), Mapping)
        else []
    ) or []
    metadata = {
        "result_contract_version": 2,
        "geometry_type": "imported",
        "solver_backend": "beat",
        "solver_mode": "full_3d",
        "solve_path": "full-3d",
        "axisymmetric_eligibility_reasons": ["imported geometry solves full 3-D only"],
        "solver_engine": {
            "engine": beat_engine_name(backend),
            "package": "hornlab-beat-bem",
            "package_version": status.get("version"),
            "device": backend,
            "formulation": "burton_miller",
            "precision": "single",
        },
        "ingest_id": geometry.ingest_id,
        "manifest_sha256": geometry.manifest_sha256,
        "artifact_sha256": geometry.artifact_sha256,
        "tag_namespace": record.get("tag_namespace"),
        "tag_map": json_safe_native_value(record.get("tag_map") or {}),
        "per_source_frequency_validity": per_source_validity,
        "symmetry_planes_used": sorted(domain_planes),
        "polar_grid_derivation": json_safe_native_value(
            record.get("polar_grid_derivation") or {}
        ),
        "observation_origin_effective": "throat",
        "observation_frame_basis": dict(frame_basis),
        "beat_solver_frame": {
            "rotation_rows": [[float(value) for value in row] for row in frame.rotation],
            "origin_m": [float(value) for value in frame.origin],
            "note": (
                "hornlab-beat-bem measures in its own +z frame; the mesh was "
                "rotated rigidly into it, and every result is frame-relative"
            ),
        },
        "acknowledged_findings": list(geometry.acknowledged_findings),
        "exterior_only": geometry.exterior_only,
        "fem_exclusion": (
            {
                "excluded": True,
                "declared_volume_count": len(fem_volumes),
                "reason": "Phase 2 exterior_only override",
            }
            if fem_volumes and geometry.exterior_only
            else {"excluded": False, "declared_volume_count": len(fem_volumes)}
        ),
        "performance": {"total_time_seconds": time.time() - started},
        "field_trace_retention": {
            "estimated_bytes": trace_estimated_bytes,
            "cap_bytes": trace_cap_bytes,
        },
    }
    envelope: dict[str, Any] = {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "channels": channels,
        "channel_order": channel_order,
        "metadata": metadata,
    }
    envelope_frequencies = sorted(
        {
            float(value)
            for channel_payload in channels.values()
            for value in (channel_payload.get("frequencies") or [])
        }
    )
    if envelope_frequencies:
        envelope["frequencies"] = envelope_frequencies
    if channel_bases_npz is not None:
        envelope["_channel_bases_npz"] = channel_bases_npz
    # Surface traces are per vertex and per face, and the rotation moved no
    # vertex relative to another, so they belong to the mesh as it was
    # ingested -- the one the job stores and the viewport draws.
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            [(channel.id, sorted_results[channel.id]) for channel in geometry.drive_channels],
            first_config,
            backend=BEAT_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
        )
        if retain_traces
        else None
    )
    if retain_traces and field_traces is None:
        trace_reason = "trace_output_missing"
    envelope["_field_traces"] = field_traces
    envelope["_field_trace_unavailable_reason"] = trace_reason
    return envelope


__all__ = [
    "BeatImportedFrame",
    "ImportedBeatRefusal",
    "MIRROR_PLANE_TOLERANCE_M",
    "PASSIVE_CARDIOID_REFUSAL",
    "RIGID_TAG",
    "VELOCITY_TAG",
    "beat_imported_frame",
    "imported_beat_preflight",
    "solve_imported_beat_from_msh_text",
]
