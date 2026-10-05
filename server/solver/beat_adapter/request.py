"""WG solve inputs as optional official BEAT system-v1 requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.contracts.geometry import PLANE_BY_QUADRANTS
from server.jobs.models import DriveChannel
from server.solver.beat_runtime.paths import PROVIDER_ID
from server.solver.context import SolverContext
from server.solver.frequency_sweep import live_execution_frequencies
from server.solver.ground_plane import GroundPlane
from server.solver.imported import imported_anchor_frame, prepare_axial_drive
from server.solver.result_mapping import native_observation_frame

from .driver_loading import BoundaryLoading
from .mesh import SurfaceMesh, read_surface
from .observations import ObservationLayout, build_observations
from .results import result_outputs


@dataclass(frozen=True)
class SourceBasis:
    source_id: str
    tag: int
    motion: str = "normal"
    axis: Sequence[float] | None = None
    port_id: str | None = None


@dataclass(frozen=True)
class CompiledRequest:
    wire: dict[str, Any]
    layout: ObservationLayout
    mesh: SurfaceMesh
    bases: tuple[SourceBasis, ...]
    channel_ports: dict[str, tuple[str, ...]]
    channel_loading: dict[str, BoundaryLoading]
    frame: Mapping[str, Any]
    engine_id: str
    surface_traces: bool


def _axis(value: Sequence[float] | None) -> np.ndarray:
    if value is not None and any(isinstance(item, (bool, np.bool_)) for item in value):
        raise ValueError("Motion axis components must be real numbers, not booleans")
    axis = np.asarray(value, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or not np.any(axis):
        raise ValueError("Axial motion requires a finite nonzero three-vector")
    axis = axis / np.max(np.abs(axis))
    return axis / np.linalg.norm(axis)


def _backend(engine_id: str, backend: str | None) -> str:
    named = {"beat-cpu": "cpu", "beat-metal": "metal",
             "official-beat-cpu": "cpu", "official-beat-metal": "metal"}
    if engine_id not in {"beat", *named}:
        raise ValueError(f"Unsupported BEAT engine ID {engine_id!r}")
    selected = backend or named.get(engine_id)
    if selected not in {"cpu", "metal"}:
        raise ValueError("A legacy 'beat' request requires a resolved CPU/Metal backend")
    if engine_id in named and named[engine_id] != selected:
        raise ValueError("Stored BEAT engine ID disagrees with selected backend")
    return selected


def _image_frame(symmetry: str, ground: GroundPlane | None) -> tuple[str, np.ndarray, np.ndarray]:
    aliases = {"full": "off", "off": "off", "x": "x", "xy": "xy",
               "yz": "x", "yz+xz": "xy", "xz": "x", "y": "x"}
    if symmetry not in aliases:
        raise ValueError(f"Unsupported BEAT symmetry {symmetry!r}")
    mode = aliases[symmetry]
    rotation, translation = np.eye(3), np.zeros(3)
    # A proper rotation represents a y-only half with official X symmetry.
    if symmetry in {"xz", "y"}:
        rotation = np.array([[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]])
    if ground is not None:
        if mode != "off":
            raise ValueError("Official BEAT cannot combine ground and reduced-domain symmetry")
        if not np.isfinite(ground.height_m):
            raise ValueError("Ground height must be finite")
        mode = "ground"
        translation[ground.component] = ground.height_m
        # The official image plane is Y=0; rotate mesh, sources and points together.
        if ground.axis == "x":
            rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        elif ground.axis == "z":
            rotation = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
    return mode, rotation, translation


def _layout(context: SolverContext, frame: Mapping[str, Any], precision: str) -> ObservationLayout:
    polar = context.polar_config
    return build_observations(
        angle_range=tuple(polar.get("angle_range") or (0., 180., 37)),
        planes=polar.get("enabled_axes") or ("horizontal", "vertical"),
        distance_m=float(polar.get("distance", 2.)),
        inclination_deg=float(polar.get("inclination", 45.)),
        origin_m=frame["origin"], axis=frame["axis"], u=frame["u"], v=frame["v"],
        sphere_grid=(int(polar.get("spherical_theta_count") or 37),
                     int(polar.get("spherical_phi_count") or 72)), precision=precision)


def build_request(
    msh_text: str, *, sources: Sequence[SourceBasis], channel_ports: Mapping[str, Sequence[str]],
    layout: ObservationLayout, frame: Mapping[str, Any], frequencies_hz: Sequence[float],
    engine_id: str = "beat-cpu", backend: str | None = None, precision: str = "float32",
    symmetry: str = "full", ground: GroundPlane | None = None, mesh_scale_to_m: float = 1.,
    sound_speed_m_per_s: float = 343., density_kg_per_m3: float = 1.2041,
    quadrature_order: int = 4, singular_order: int = 4, surface_traces: bool = False,
    cancel_path: Path | None = None, ground_plane_min_clearance_m: float = 0.,
) -> CompiledRequest:
    """Build independent unit-velocity bases; WG channels sum their named ports.

    Packed triangle buffers preserve original node/face order and tags. Motion
    axes and arbitrary observation points stay in the original metre frame,
    except for a common proper rotation required by an official image plane.
    No files are written and no engine import or runtime discovery occurs.
    """
    backend = _backend(engine_id, backend)
    if precision not in {"float32", "float64"} or (backend == "metal" and precision != "float32"):
        raise ValueError("Unsupported BEAT backend precision")
    if (type(quadrature_order) is not int or quadrature_order not in {1, 2, 4}
            or type(singular_order) is not int or not 1 <= singular_order <= 12
            or (singular_order > 4 and precision != "float64")):
        raise ValueError("Unsupported regular/singular quadrature order at this precision")
    if any(not np.isfinite(value) or value <= 0 for value in
           (sound_speed_m_per_s, density_kg_per_m3)):
        raise ValueError("Air speed and density must be finite and positive")
    if any(isinstance(value, (bool, np.bool_)) for value in frequencies_hz):
        raise ValueError("Frequencies must be real numbers, not booleans")
    frequencies = np.asarray(frequencies_hz, dtype=float)
    with np.errstate(over="ignore", under="ignore"):
        wire_frequencies = frequencies.astype(precision)
    if (frequencies.ndim != 1 or not frequencies.size or not np.isfinite(wire_frequencies).all()
            or np.any(wire_frequencies <= 0) or len(np.unique(wire_frequencies)) != len(frequencies)):
        raise ValueError("Frequencies must be positive, finite and distinct at solver precision")
    if not np.isfinite(ground_plane_min_clearance_m) or ground_plane_min_clearance_m < 0:
        raise ValueError("Ground clearance must be finite and non-negative")
    mesh = read_surface(msh_text, scale_to_m=mesh_scale_to_m)
    mode, rotation, translation = _image_frame(symmetry, ground)
    transformed = replace(mesh, points_m=(mesh.points_m + translation) @ rotation.T)
    transformed = replace(transformed, points_m=transformed.points_m.astype(precision))
    tolerance = max(1e-9, float(np.linalg.norm(np.ptp(transformed.points_m, axis=0))) * 1e-6)
    if mode in {"x", "xy"}:
        axes = (0,) if mode == "x" else (0, 1)
        if any(np.min(transformed.points_m[:, axis]) < -tolerance for axis in axes):
            raise ValueError("Mesh is outside the positive symmetry fundamental domain")
        # Match official snap_symmetry_planes before computing physical loading.
        points = transformed.points_m.copy()
        for axis in axes:
            points[np.abs(points[:, axis]) <= tolerance, axis] = 0.
        transformed = replace(transformed, points_m=points)
    if mode == "ground":
        triangles_y = transformed.points_m[transformed.faces, 1]
        if np.min(triangles_y) < -1e-6 or np.any(np.all(np.abs(triangles_y) <= 1e-6, axis=1)):
            raise ValueError("Ground mesh must stay above Y=0 with no coplanar triangles")
        if np.min(triangles_y) < ground_plane_min_clearance_m:
            raise ValueError("Mesh does not meet the requested ground clearance")
    if np.any(transformed.areas_m2 <= 0):
        raise ValueError("Symmetry snapping collapsed a triangle")
    transformed_layout = replace(layout, points_m={
        name: (points + translation) @ rotation.T for name, points in layout.points_m.items()})
    bases, components, ports = [], [], []
    tags_by_id: dict[str, int] = {}
    for source in sources:
        if (not isinstance(source.source_id, str) or not source.source_id or type(source.tag) is not int or source.tag <= 0
                or source.tag not in mesh.tags or source.motion not in {"normal", "axial"}):
            raise ValueError("Source requires an identity, present physical tag and supported motion")
        if source.source_id in tags_by_id and tags_by_id[source.source_id] != source.tag:
            raise ValueError("One source identity cannot reference different tags")
        tags_by_id[source.source_id] = source.tag
        port_id = source.port_id or f"excitation:{source.motion}:{quote(source.source_id, safe='')}"
        if not port_id or any(basis.port_id == port_id for basis in bases):
            raise ValueError("Source port IDs must be nonempty and unique")
        boundary_id = f"boundary:tag:{source.tag}"
        component_id = f"component:{port_id}"
        parameters: dict[str, Any] = {}
        if source.motion == "axial":
            axis = rotation @ _axis(source.axis)
            axes = (0,) if mode == "x" else (0, 1) if mode == "xy" else ()
            if any(abs(axis[index]) > 1e-8 for index in axes):
                raise ValueError("Axial motion axis must lie in physical symmetry planes")
            parameters = {"motion_profile": "rigid_translation", "motion_axis": axis.tolist()}
        elif source.axis is not None:
            raise ValueError("Normal motion must not supply an axial direction")
        bases.append(replace(source, port_id=port_id))
        components.append({"id": component_id, "name": source.source_id,
                           "kind": "ideal_velocity_source", "boundary_ids": [boundary_id],
                           "parameters": parameters})
        ports.append({"id": port_id, "name": source.source_id, "component_id": component_id,
                      "kind": "normal_velocity"})
    if not bases or not channel_ports:
        raise ValueError("Request requires sources and drive channels")
    if len(set(tags_by_id.values())) != len(tags_by_id):
        raise ValueError("Distinct source identities cannot share a physical tag")
    port_by_id = {basis.port_id: basis for basis in bases}
    channels, loading = {}, {}
    for channel_id, members in channel_ports.items():
        members = tuple(members)
        if (not channel_id or not members or len(set(members)) != len(members)
                or set(members) - set(port_by_id)):
            raise ValueError("Channel requires unique existing source ports")
        tags = [port_by_id[port].tag for port in members]
        if len(set(tags)) != len(tags):
            raise ValueError("Channel cannot drive the same source twice")
        channels[channel_id] = members
        loading[channel_id] = BoundaryLoading.from_mesh(transformed, tags)
    boundaries = [{"id": f"boundary:tag:{tag}", "name": f"Surface {tag}",
                   "kind": "moving" if tag in tags_by_id.values() else "rigid",
                   "region_id": "region:air",
                   "group": {"mesh_id": "mesh:surface", "dimension": 2, "tag": int(tag), "name": None},
                   "parameters": {}} for tag in np.unique(mesh.tags)]
    options = {"precision": precision, "bem_backend": backend, "symmetry": mode,
               "phasor_convention": SOLVER_TIME_CONVENTION,
               "regular_quadrature_mode": "wavelength" if backend == "cpu" else "fixed",
               "quadrature_order": quadrature_order, "singular_order": singular_order,
               "wavelength_mesh_stat": "p90", "wavelength_kh_q1_max": 0.,
               "wavelength_kh_q2_max": 2., "ground_plane_min_clearance_m": ground_plane_min_clearance_m}
    system = {"id": "system:waveguide-generator", "name": "WG exterior radiation",
              "contract_version": 2 if any(b.motion == "axial" for b in bases) else 1,
              "meshes": [{"id": "mesh:surface", "name": "Surface", "file": "",
                          "purpose": "bem_surface", "scale_to_m": 1., "translation_m": [0., 0., 0.],
                          "mesh_data": transformed.packed()}],
              "regions": [{"id": "region:air", "name": "Air", "kind": "unbounded_air",
                           "mesh_ids": ["mesh:surface"], "volume_groups": [],
                           "sound_speed_m_per_s": sound_speed_m_per_s,
                           "density_kg_per_m3": density_kg_per_m3, "loss_model": {}}],
              "boundaries": boundaries, "interfaces": [], "components": components,
              "excitation_ports": ports,
              "metadata": {"provider": PROVIDER_ID, "wg_engine_id": engine_id,
                           "source_tags": tags_by_id,
                           "channel_ports": {name: list(members) for name, members in channels.items()}}}
    wire = {"schema_version": 1, "compiled_system": system,
            "frequencies_hz": frequencies.tolist(), "excitation_port_ids": list(port_by_id),
            "outputs": result_outputs(transformed_layout, surface_traces=surface_traces,
                                      driver_loading=True), "solver_options": options}
    if cancel_path is not None:
        wire["cancel_path"] = str(cancel_path.resolve())
    return CompiledRequest(wire, transformed_layout, transformed, tuple(bases), channels,
                           loading, frame, engine_id, surface_traces)


def build_parametric_request(
    msh_text: str, context: SolverContext, *, frequencies_hz: Sequence[float] | None = None,
    precision: str = "float32", mesh_scale_to_m: float = 1., **options: Any,
) -> CompiledRequest:
    """Translate beat.py's context without constructing an HBB SolveConfig."""
    context.validate()
    if context.sim_type != 2:
        raise ValueError("Exterior adapter cannot represent a coupled infinite baffle")
    # Existing WG frame policy includes cabinet and reduced-source corrections.
    native = native_observation_frame(context, msh_text, SimpleNamespace)
    if native is None:
        raise ValueError("Parametric mesh requires the authoritative source-tag-2 frame")
    frame = vars(native).copy()
    for name in ("origin", "mouth_center", "source_center"):
        frame[name] = np.asarray(frame[name]) * mesh_scale_to_m
    source = SourceBasis("source", 2, context.source_motion,
                         frame["axis"] if context.source_motion == "axial" else None,
                         "excitation:source")
    return build_request(
        msh_text, sources=[source], channel_ports={"source": [source.port_id]}, frame=frame,
        layout=_layout(context, frame, precision), precision=precision,
        frequencies_hz=live_execution_frequencies(context) if frequencies_hz is None else frequencies_hz,
        symmetry=PLANE_BY_QUADRANTS[context.quadrants] or "full", ground=context.ground_plane,
        mesh_scale_to_m=mesh_scale_to_m, **options)


def build_imported_request(
    msh_text: str, context: SolverContext, record: Mapping[str, Any],
    drive_channels: Sequence[DriveChannel], *, frequencies_hz: Sequence[float] | None = None,
    precision: str = "float32", **options: Any,
) -> CompiledRequest:
    """Retain CAD source IDs, axes, channels and anchor frame in solver coordinates."""
    from server.solver.imported import imported_domain_planes, imported_symmetry_from_cut_planes

    context.validate()
    if context.sim_type != 2:
        raise ValueError("Imported adapter requires an exterior radiation problem")
    symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(record))
    if symmetry.quadrants != context.quadrants:
        raise ValueError("Stored context and ingestion record disagree on symmetry")
    tags = record.get("source_tags")
    if not isinstance(tags, Mapping):
        raise ValueError("Ingestion record requires source_tags")
    axes, _ = prepare_axial_drive(record, msh_text, drive_channels)
    sources, channels, by_key = [], {}, {}
    for channel in drive_channels:
        if channel.id in channels:
            raise ValueError("Drive channel IDs must be unique")
        members = []
        for source_id in channel.source_ids:
            if source_id not in tags or type(tags[source_id]) is not int:
                raise ValueError(f"Stored source identity {source_id!r} has no physical tag")
            key = (source_id, channel.motion)
            if key not in by_key:
                tag = tags[source_id]
                source = SourceBasis(source_id, tag, channel.motion,
                                     axes[tag].axis if channel.motion == "axial" else None,
                                     f"excitation:{channel.motion}:{quote(source_id, safe='')}")
                sources.append(source)
                by_key[key] = source.port_id
            members.append(by_key[key])
        channels[channel.id] = members
    frame = imported_anchor_frame(record)
    return build_request(
        msh_text, sources=sources, channel_ports=channels, frame=frame,
        layout=_layout(context, frame, precision), precision=precision,
        frequencies_hz=live_execution_frequencies(context) if frequencies_hz is None else frequencies_hz,
        symmetry=symmetry.native_plane or "full", ground=context.ground_plane, **options)
