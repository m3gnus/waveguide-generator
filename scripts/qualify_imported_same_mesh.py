"""Same-mesh qualification of imported CAD solving on the CPU engines.

One verified record -- the same mesh, source tags, anchor frame, excitation and
frequencies -- goes to every engine that can run here, and the complex
per-channel responses and channel sums are compared at every observation point
(the horizontal, vertical and diagonal polar cuts and the DI sphere), never
magnitudes alone. Analytic references and a refinement ladder set the
tolerances the comparisons are judged against.

Run it on a host with Metal and a provisioned BEAT CPU runtime:

    python scripts/qualify_imported_same_mesh.py --json results.json --markdown report.md

BEMPP joins every fixture only on an OpenCL device; its numba backend is
never used here.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.jobs.models import SolveRequest  # noqa: E402
from server.mesh.imported import polar_grid_from_symmetry  # noqa: E402
from server.solver.combine import deserialize_channel_bases  # noqa: E402
from server.solver.imported import mesh_text_sha256  # noqa: E402

#: The air every engine here solves in (hornlab-metal-bem and hornlab-beat-bem
#: publish the same two constants).
AIR_DENSITY = 1.2041
SOUND_SPEED = 343.0
OBSERVATION_DISTANCE_M = 2.0
SPHERE_RADIUS_M = 0.1

#: Solved frequencies. ka runs 0.18 to 2.7 on the 0.1 m sphere; 1715 Hz is its
#: first interior Dirichlet resonance (ka = pi), where the two formulations are
#: expected to part.
FREQUENCIES_HZ = (100.0, 300.0, 700.0, 1200.0, 1500.0)

#: (polar rings, azimuthal points) for the refinement ladder. Every density has
#: meridians on x = 0 and y = 0 and an equator, so the same sphere can be split
#: into hemispheres or cut to a half or a quarter on exact planes.
LADDER = ((8, 16), (16, 32), (32, 64))
REFERENCE_LEVEL = 1


# ---------------------------------------------------------------- geometry


def uv_sphere(radius: float, rings: int, segments: int) -> tuple[np.ndarray, np.ndarray]:
    """A latitude-longitude sphere, outward wound, poles as fans."""

    if rings % 2 or segments % 4:
        raise ValueError("rings must be even and segments a multiple of 4")
    vertices = [np.asarray([0.0, 0.0, radius])]
    for ring in range(1, rings):
        theta = math.pi * ring / rings
        for segment in range(segments):
            phi = 2.0 * math.pi * segment / segments
            vertices.append(
                radius
                * np.asarray(
                    [math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta)]
                )
            )
    vertices.append(np.asarray([0.0, 0.0, -radius]))
    points = np.asarray(vertices)
    south = len(points) - 1

    def ring_index(ring: int, segment: int) -> int:
        return 1 + (ring - 1) * segments + (segment % segments)

    faces: list[tuple[int, int, int]] = []
    for segment in range(segments):
        faces.append((0, ring_index(1, segment), ring_index(1, segment + 1)))
        faces.append((south, ring_index(rings - 1, segment + 1), ring_index(rings - 1, segment)))
    for ring in range(1, rings - 1):
        for segment in range(segments):
            a = ring_index(ring, segment)
            b = ring_index(ring, segment + 1)
            c = ring_index(ring + 1, segment)
            d = ring_index(ring + 1, segment + 1)
            faces += [(a, c, d), (a, d, b)]
    triangles = np.asarray(faces, dtype=int)
    normals = np.cross(
        points[triangles[:, 1]] - points[triangles[:, 0]],
        points[triangles[:, 2]] - points[triangles[:, 0]],
    )
    centroids = points[triangles].mean(axis=1)
    inward = np.einsum("ij,ij->i", normals, centroids) < 0.0
    triangles[inward] = triangles[inward][:, ::-1]
    return points, triangles


def gmsh22(points: np.ndarray, triangles: np.ndarray, tags: np.ndarray) -> str:
    names = {1: "wg-import-v1|rigid"}
    for tag in sorted({int(value) for value in tags}):
        names.setdefault(tag, f"wg-import-v1|tag={tag}")
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(names))]
    rows += [f'2 {tag} "{name}"' for tag, name in sorted(names.items())]
    rows += ["$EndPhysicalNames", "$Nodes", str(len(points))]
    rows += [f"{index} {x:.17g} {y:.17g} {z:.17g}" for index, (x, y, z) in enumerate(points, 1)]
    rows += ["$EndNodes", "$Elements", str(len(triangles))]
    rows += [
        f"{index} 2 2 {int(tag)} {int(tag)} {a + 1} {b + 1} {c + 1}"
        for index, ((a, b, c), tag) in enumerate(zip(triangles, tags), 1)
    ]
    rows.append("$EndElements")
    return "\n".join(rows) + "\n"


def keep_side(
    points: np.ndarray, triangles: np.ndarray, tags: np.ndarray, planes: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The positive side of each named cut, as a CAD author would return it."""

    centroids = points[triangles].mean(axis=1)
    keep = np.ones(len(triangles), dtype=bool)
    for plane in planes:
        keep &= centroids[:, {"x0": 0, "y0": 1}[plane]] > 0.0
    used = np.unique(triangles[keep])
    remap = -np.ones(len(points), dtype=int)
    remap[used] = np.arange(len(used))
    return points[used], remap[triangles[keep]], tags[keep]


def rotation_matrix(axis: Sequence[float], degrees: float) -> np.ndarray:
    k = np.asarray(axis, dtype=float)
    k /= np.linalg.norm(k)
    angle = math.radians(degrees)
    cross = np.asarray([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * cross @ cross


# ---------------------------------------------------------------- analytic


def pulsating_sphere(radius: float, frequency_hz: float, distance: float) -> complex:
    """Pressure per unit outward normal acceleration, e^{-iwt}, outgoing e^{+ikr}.

    The form hornlab-beat-bem's conformance suite uses
    (``tests/conformance/analytic.py``): ``rho a^2 / r * e^{ik(r-a)} / (1 - ika)``.
    """

    k = 2.0 * math.pi * frequency_hz / SOUND_SPEED
    return complex(
        AIR_DENSITY * radius**2 / distance * np.exp(1j * k * (distance - radius)) / (1.0 - 1j * k * radius)
    )


def oscillating_sphere(
    radius: float, frequency_hz: float, distance: float, cos_theta: np.ndarray
) -> np.ndarray:
    """A rigid sphere oscillating along its axis, per unit axial acceleration.

    ``p = -rho cos(theta) h1(kr) / (k h1'(ka))`` with ``h1`` the outgoing
    spherical Hankel function; its ``k -> 0`` limit is the incompressible
    dipole ``rho a^3 cos(theta) / (2 r^2)``.
    """

    from scipy.special import spherical_jn, spherical_yn

    k = 2.0 * math.pi * frequency_hz / SOUND_SPEED
    h1 = spherical_jn(1, k * distance) + 1j * spherical_yn(1, k * distance)
    h1_prime = spherical_jn(1, k * radius, derivative=True) + 1j * spherical_yn(
        1, k * radius, derivative=True
    )
    return -AIR_DENSITY * np.asarray(cos_theta) * h1 / (k * h1_prime)


# ---------------------------------------------------------------- records


IDENTITY_FRAME = {
    "axis": [0.0, 0.0, 1.0],
    "u": [1.0, 0.0, 0.0],
    "v": [0.0, 1.0, 0.0],
    "origin_m": [0.0, 0.0, 0.0],
    "mouth_center_m": [0.0, 0.0, 0.0],
    "source_center_m": [0.0, 0.0, 0.0],
}


def frame_from(rotation: np.ndarray, origin: Sequence[float]) -> dict[str, list[float]]:
    origin = [float(value) for value in origin]
    return {
        "axis": [float(value) for value in rotation[:, 2]],
        "u": [float(value) for value in rotation[:, 0]],
        "v": [float(value) for value in rotation[:, 1]],
        "origin_m": origin,
        "mouth_center_m": origin,
        "source_center_m": origin,
    }


def record_for(
    msh_text: str,
    source_tags: Mapping[str, int],
    *,
    frame: Mapping[str, Any] = IDENTITY_FRAME,
    domain_planes: Sequence[str] = (),
) -> dict[str, Any]:
    symmetry = {
        "cut_planes": [],
        "declared_cut_planes": list(domain_planes),
        "domain_planes": list(domain_planes),
        "planes": {name: {"accepted": name in domain_planes} for name in ("x0", "y0", "z0")},
    }
    return {
        "manifest_sha256": "sha256:" + "1" * 64,
        "artifact_sha256": "sha256:" + "2" * 64,
        "mesh_content_sha256": mesh_text_sha256(msh_text),
        "_execution_msh_text": msh_text,
        "sources": [
            {"id": source_id, "required": True, "role": "LF"} for source_id in source_tags
        ],
        "source_tags": dict(source_tags),
        "tag_namespace": "wg-import-v1",
        "tag_map": {},
        "anchor": {"instance_id": "anchor", "design_id": None, "throat_frame": dict(frame)},
        "symmetry": symmetry,
        "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
        "mesh": {
            "stats": {"triangle_count": 0, "vertex_count": 0},
            "metadata": {},
            # Closed bodies: Metal's native open-edge check follows this count.
            "integrity": {"off_plane_open_edge_count": 0},
        },
        "evidence": {"fem_air_volumes": []},
        "findings": [],
    }


def request_for(
    record: Mapping[str, Any],
    channels: Sequence[Mapping[str, Any]],
    *,
    engine: str,
    frequencies: Sequence[float] = FREQUENCIES_HZ,
) -> SolveRequest:
    sources = list(record["source_tags"])
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": "wgi_" + "0" * 26,
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "drive_channels": list(channels),
                "mesh": {
                    "rigid_size_mm": 8.0,
                    "transition_mm": 20.0,
                    "source_size_mm": {source_id: 8.0 for source_id in sources},
                },
            },
            "options": {
                "engine": engine,
                "frequencies_hz": list(frequencies),
                "polar_config": {
                    "angle_range": [-180.0, 180.0, 73],
                    "distance": OBSERVATION_DISTANCE_M,
                    "enabled_axes": ["horizontal", "vertical", "diagonal"],
                },
            },
        }
    )


# ---------------------------------------------------------------- engines


def engine_adapter(name: str) -> Any:
    from server.engines.registry import create_engine

    return create_engine(name)


def available_engines() -> dict[str, str]:
    """Which of the qualified engines can run here, with the reason if not."""

    from server.engines.registry import detect_engines

    wanted = {"metal", "beat-cpu", "bempp"}
    status: dict[str, str] = {}
    for info in detect_engines():
        if info.name not in wanted:
            continue
        if info.name == "bempp" and info.available:
            from server.solver.bempp import bempp_status

            backend = str(bempp_status().get("assembly_backend") or bempp_status().get("backend") or "")
            if "opencl" not in backend.lower():
                status[info.name] = f"not qualified here: assembly backend {backend or 'unknown'} is not OpenCL"
                continue
        status[info.name] = "available" if info.available else f"unavailable: {info.reason}"
    return status


@dataclass
class Solved:
    engine: str
    channel_ids: list[str]
    frequencies_hz: np.ndarray
    angles_deg: np.ndarray
    planes: list[str]
    pressure: dict[str, np.ndarray]  # channel -> (F, planes, angles)
    sphere: dict[str, np.ndarray | None]  # channel -> (F, points) or None
    sphere_theta_deg: np.ndarray | None
    sphere_phi_deg: np.ndarray | None
    wall_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)
    #: channel -> complex source-average pressure per unit acceleration, for a
    #: single-source channel; None where the contract omits it.
    impedance: dict[str, np.ndarray | None] = field(default_factory=dict)

    def total(self, block: str = "pressure") -> np.ndarray:
        values = [getattr(self, block)[channel] for channel in self.channel_ids]
        return np.sum(values, axis=0)

    def observations(self, channel: str | None = None) -> np.ndarray:
        """Every complex observation of one channel (or the sum), flattened per frequency."""

        pressure = self.total() if channel is None else self.pressure[channel]
        rows = [pressure.reshape(pressure.shape[0], -1)]
        spheres = self.sphere if channel is not None else None
        sphere = (
            self.total("sphere")
            if channel is None and all(value is not None for value in self.sphere.values())
            else (spheres or {}).get(channel)
        )
        if sphere is not None:
            rows.append(np.asarray(sphere).reshape(sphere.shape[0], -1))
        return np.concatenate(rows, axis=1)


def solve(engine: str, record: Mapping[str, Any], channels: Sequence[Mapping[str, Any]], **kwargs: Any) -> Solved:
    request = request_for(record, channels, engine=engine, **kwargs)
    adapter = engine_adapter(engine)
    started = time.perf_counter()
    outcome = asyncio.run(
        adapter.run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_: None,
            imported_record=dict(record),
        )
    )
    wall = time.perf_counter() - started
    bases = deserialize_channel_bases(outcome.channel_bases)
    first = bases["results_by_id"][bases["channel_ids"][0]]
    return Solved(
        engine=engine,
        channel_ids=list(bases["channel_ids"]),
        frequencies_hz=np.asarray(bases["frequencies_hz"], dtype=float),
        angles_deg=np.asarray(first.observation_angles_deg, dtype=float),
        planes=list(first.observation_planes),
        pressure={
            channel: np.asarray(bases["results_by_id"][channel].pressure_complex)
            for channel in bases["channel_ids"]
        },
        sphere={
            channel: (
                None
                if bases["results_by_id"][channel].sphere_pressure_complex is None
                else np.asarray(bases["results_by_id"][channel].sphere_pressure_complex)
            )
            for channel in bases["channel_ids"]
        },
        sphere_theta_deg=first.sphere_theta_deg,
        sphere_phi_deg=first.sphere_phi_deg,
        wall_seconds=wall,
        metadata={"solver_engine": outcome.results.get("metadata", {}).get("solver_engine")},
        impedance={
            channel: _channel_impedance(outcome.results["channels"].get(channel, {}))
            for channel in bases["channel_ids"]
        },
    )


def _channel_impedance(channel: Mapping[str, Any]) -> np.ndarray | None:
    block = channel.get("impedance")
    if not isinstance(block, Mapping) or "real" not in block:
        return None
    return np.asarray(block["real"], dtype=float) + 1j * np.asarray(block["imaginary"], dtype=float)


# ---------------------------------------------------------------- metrics


def relative_error(candidate: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Per-frequency complex L2 error of ``candidate`` against ``reference``."""

    candidate = np.asarray(candidate).reshape(candidate.shape[0], -1)
    reference = np.asarray(reference).reshape(reference.shape[0], -1)
    return np.linalg.norm(candidate - reference, axis=1) / np.linalg.norm(reference, axis=1)


def polar_cos_theta(solved: Solved) -> np.ndarray:
    """cos(angle from the axis) at every polar-cut sample, (planes, angles)."""

    return np.cos(np.radians(solved.angles_deg))[None, :].repeat(len(solved.planes), axis=0)


def sphere_cos_theta(solved: Solved) -> np.ndarray | None:
    if solved.sphere_theta_deg is None:
        return None
    return np.cos(np.radians(np.asarray(solved.sphere_theta_deg, dtype=float)))


def analytic_observations(
    solved: Solved, kind: str, radius: float = SPHERE_RADIUS_M, *, include_sphere: bool = True
) -> np.ndarray:
    rows = []
    for frequency in solved.frequencies_hz:
        if kind == "pulsating":
            value = pulsating_sphere(radius, frequency, OBSERVATION_DISTANCE_M)
            polar = np.full((len(solved.planes), len(solved.angles_deg)), value, dtype=complex)
            sphere_count = 0 if solved.sphere_theta_deg is None else len(solved.sphere_theta_deg)
            sphere = np.full(sphere_count, value, dtype=complex)
        else:
            polar = oscillating_sphere(radius, frequency, OBSERVATION_DISTANCE_M, polar_cos_theta(solved))
            cos_sphere = sphere_cos_theta(solved)
            sphere = (
                np.zeros(0, dtype=complex)
                if cos_sphere is None
                else oscillating_sphere(radius, frequency, OBSERVATION_DISTANCE_M, cos_sphere)
            )
        parts = [polar.reshape(-1)]
        if include_sphere and solved.sphere_theta_deg is not None:
            parts.append(sphere.reshape(-1))
        rows.append(np.concatenate(parts))
    return np.asarray(rows)


# ---------------------------------------------------------------- fixtures


def sphere_mesh(level: int, *, split: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rings, segments = LADDER[level]
    points, triangles = uv_sphere(SPHERE_RADIUS_M, rings, segments)
    centroids = points[triangles].mean(axis=1)
    tags = np.where(centroids[:, 2] >= 0.0, 101, 102) if split else np.full(len(triangles), 101)
    return points, triangles, tags


ONE_CHANNEL = [{"id": "both", "source_ids": ["top", "bottom"]}]
TWO_CHANNELS = [
    {"id": "top", "source_ids": ["top"]},
    {"id": "bottom", "source_ids": ["bottom"]},
]
HEMISPHERE_TAGS = {"top": 101, "bottom": 102}


def with_motion(channels: Sequence[Mapping[str, Any]], motion: str) -> list[dict[str, Any]]:
    return [{**channel, "motion": motion} for channel in channels]


@dataclass
class Row:
    fixture: str
    engine: str
    compared_with: str
    quantity: str
    errors: list[float]
    tolerance: float | None = None
    note: str = ""

    @property
    def worst(self) -> float:
        return float(np.max(self.errors))

    @property
    def passed(self) -> bool | None:
        return None if self.tolerance is None else self.worst <= self.tolerance


def max_edge(points: np.ndarray, triangles: np.ndarray) -> float:
    corners = points[triangles]
    edges = np.concatenate(
        [corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 1], corners[:, 0] - corners[:, 2]]
    )
    return float(np.max(np.linalg.norm(edges, axis=1)))


#: A same-engine exact equivalence -- linearity, a mirror image, a reduced
#: domain against the whole, the same triangles moved -- differs only by
#: single-precision assembly: measured up to 7e-5 on BEAT, 1e-5 on Metal.
#: 1e-3 is fifteen times that, and three orders below the smallest error a
#: frame, sign or tag mistake produced in these fixtures (a sign flip reads 2.0).
EXACT_TOLERANCE = 1.0e-3

#: Margin on a same-mesh tolerance. The same-mesh bound below is
#: already a triangle-inequality sum of two engines' errors, so the margin
#: covers only extrapolating the sphere's envelope to another body.
TOLERANCE_MARGIN = 1.5


def run(engines: Sequence[str], report: Callable[[str], None] = print) -> dict[str, Any]:
    rows: list[Row] = []
    timings: dict[str, float] = {}

    def record_row(row: Row) -> Row:
        rows.append(row)
        report(
            f"{row.fixture:52s} {row.engine:9s} vs {row.compared_with:12s} {row.quantity:20s} "
            f"worst {row.worst:.3e}"
            + (
                f"  tol {row.tolerance:.2e} {'PASS' if row.passed else 'FAIL'}"
                if row.tolerance is not None
                else ""
            )
        )
        return row

    # Fixtures 2 + 3: analytic spheres on the refinement ladder. The breathing
    # sphere is split into two hemisphere tags, solved as one merged channel
    # and as two channels whose bases must sum to it. The oscillating sphere is
    # one axial tag: an axial split would ask each engine a different question
    # (see the rear-facing fixture below).
    ladder: dict[tuple[str, str], list[tuple[float, np.ndarray]]] = {}
    level_errors: dict[tuple[str, str, int], np.ndarray] = {}
    for level in range(len(LADDER)):
        for kind in ("pulsating", "oscillating"):
            points, triangles, tags = sphere_mesh(level, split=kind == "pulsating")
            h = max_edge(points, triangles)
            text = gmsh22(points, triangles, tags)
            if kind == "pulsating":
                record = record_for(text, HEMISPHERE_TAGS)
                cases = (("one channel", with_motion(ONE_CHANNEL, "normal")), ("two channels", with_motion(TWO_CHANNELS, "normal")))
            else:
                record = record_for(text, {"sphere": 101})
                cases = (("one tag", [{"id": "sphere", "source_ids": ["sphere"], "motion": "axial"}]),)
            for engine in engines:
                solved_cases = {}
                for label, channels in cases:
                    solved = solve(engine, record, channels)
                    solved_cases[label] = solved
                    timings[f"{kind}-L{level}-{label}-{engine}"] = solved.wall_seconds
                    errors = relative_error(solved.observations(), analytic_observations(solved, kind))
                    ladder.setdefault((kind, engine), []).append((h, errors))
                    level_errors[(kind, engine, level)] = errors
                    record_row(Row(f"{kind} sphere L{level} ({len(triangles)} tri), {label}", engine, "analytic", "complex, all points", errors.tolist()))
                if kind == "pulsating":
                    split = relative_error(
                        solved_cases["two channels"].observations(),
                        solved_cases["one channel"].observations(),
                    )
                    record_row(Row(f"pulsating sphere L{level}, two-channel sum", engine, "one channel", "complex, all points", split.tolist(), tolerance=EXACT_TOLERANCE, note="linearity: the bases of two tags sum to their merged tag"))

    # Tolerances, from the ladder above. Two engines that are each within their
    # own discretisation error of the truth differ by at most the sum of those
    # errors (the triangle inequality), so a same-mesh comparison at a given
    # density is held to that sum, measured on the analytic bodies at the same
    # density, times a margin for carrying it to another body.
    def same_mesh_tolerance(level: int) -> float:
        worst = 0.0
        for kind in ("pulsating", "oscillating"):
            summed = sum(level_errors[(kind, engine, level)] for engine in engines)
            worst = max(worst, float(np.max(summed)))
        return TOLERANCE_MARGIN * worst

    for level in range(len(LADDER)):
        report(f"same-mesh tolerance at L{level}: {same_mesh_tolerance(level):.3e}")
    for engine in engines:
        for kind in ("pulsating", "oscillating"):
            series = [float(np.max(level_errors[(kind, engine, level)])) for level in range(len(LADDER))]
            orders = [math.log2(a / b) for a, b in zip(series, series[1:])]
            report(f"{engine} {kind}: worst errors {', '.join(f'{v:.3e}' for v in series)}; observed order {', '.join(f'{o:.2f}' for o in orders)}")

    # Fixture 1 on the analytic bodies: the same record into every engine.
    level = REFERENCE_LEVEL
    points, triangles, tags = sphere_mesh(level, split=True)
    h_ref = max_edge(points, triangles)
    text = gmsh22(points, triangles, tags)
    record = record_for(text, HEMISPHERE_TAGS)
    pairs = [(a, b) for index, a in enumerate(engines) for b in engines[index + 1 :]]
    for motion in ("normal", "axial"):
        for label, channels in (("one channel", ONE_CHANNEL), ("two channels", TWO_CHANNELS)):
            solved = {engine: solve(engine, record, with_motion(channels, motion)) for engine in engines}
            for a, b in pairs:
                for channel in solved[a].channel_ids:
                    errors = relative_error(solved[a].observations(channel), solved[b].observations(channel))
                    record_row(Row(f"same mesh: hemispheres, {motion}, {label}, {channel}", a, b, "complex per channel", errors.tolist(), tolerance=same_mesh_tolerance(level),
                                   note=("an axial hemisphere faces backwards; every engine must drive it as Metal does" if motion == "axial" else "")))
                if len(solved[a].channel_ids) > 1:
                    errors = relative_error(solved[a].observations(), solved[b].observations())
                    record_row(Row(f"same mesh: hemispheres, {motion}, {label}, channel sum", a, b, "complex channel sum", errors.tolist(), tolerance=same_mesh_tolerance(level)))

    # Fixture 4: the oscillating sphere moved and turned in CAD. Its mesh and
    # its anchor frame are rotated and translated together, so every solver
    # frame response must equal the unmoved one -- and the analytic dipole,
    # whose cos(theta) pattern makes a wrong axis or a wrong sign visible.
    rotation = rotation_matrix([1.0, -0.4, 0.7], 131.0)
    offset = np.asarray([0.21, -0.37, 0.52])
    points, triangles, _tags = sphere_mesh(level, split=False)
    one_tag = np.full(len(triangles), 101)
    straight = record_for(gmsh22(points, triangles, one_tag), {"sphere": 101})
    moved = record_for(
        gmsh22(points @ rotation.T + offset, triangles, one_tag),
        {"sphere": 101},
        frame=frame_from(rotation, offset),
    )
    axial = [{"id": "sphere", "source_ids": ["sphere"], "motion": "axial"}]
    for engine in engines:
        base = solve(engine, straight, axial)
        turned = solve(engine, moved, axial)
        errors = relative_error(turned.observations(), base.observations())
        # The same triangles, turned: only rounding and quadrature orientation
        # differ, so the bound is the single-precision floor, not the mesh.
        record_row(Row("rotated + translated oscillating sphere", engine, "unmoved", "complex, all points", errors.tolist(), tolerance=EXACT_TOLERANCE, note="fixture 4: the frame rotation"))
        errors = relative_error(turned.observations(), analytic_observations(turned, "oscillating"))
        record_row(Row("rotated + translated oscillating sphere", engine, "analytic", "complex, all points", errors.tolist(), tolerance=float(np.max(level_errors[("oscillating", engine, level)])) + EXACT_TOLERANCE, note="fixture 4 against the analytic reference"))

    # Fixture 4, u/v-sensitive: a sphere whose source is an off-axis cap, on
    # the +x side, so its pattern is not symmetric about the axis. A swapped
    # or mirrored horizontal/vertical mapping, which the axisymmetric
    # oscillating sphere cannot show, changes this field. Moved and turned in
    # CAD, it must give the unmoved answer on each engine, and the engines
    # must agree with each other on the turned copy.
    points, triangles, _tags = sphere_mesh(level, split=False)
    centroids = points[triangles].mean(axis=1)
    cap_tags = np.where(centroids[:, 0] > 0.6 * SPHERE_RADIUS_M, 101, 1)
    cap = [{"id": "cap", "source_ids": ["cap"]}]
    straight_cap = record_for(gmsh22(points, triangles, cap_tags), {"cap": 101})
    moved_cap = record_for(
        gmsh22(points @ rotation.T + offset, triangles, cap_tags),
        {"cap": 101},
        frame=frame_from(rotation, offset),
    )
    turned_by_engine: dict[str, Solved] = {}
    for engine in engines:
        base = solve(engine, straight_cap, cap)
        turned = solve(engine, moved_cap, cap)
        turned_by_engine[engine] = turned
        errors = relative_error(turned.observations(), base.observations())
        record_row(Row("rotated + translated off-axis cap", engine, "unmoved", "complex, all points", errors.tolist(), tolerance=EXACT_TOLERANCE, note="fixture 4: u and v must map as well as the axis"))
        horizontal = base.planes.index("horizontal")
        vertical = base.planes.index("vertical")
        asymmetry = relative_error(base.pressure["cap"][:, horizontal, :], base.pressure["cap"][:, vertical, :])
        record_row(Row("off-axis cap: horizontal differs from vertical", engine, "vertical cut", "complex, polar", asymmetry.tolist(), note="non-vacuity: the fixture distinguishes u from v"))
    for a, b in pairs:
        errors = relative_error(turned_by_engine[a].observations(), turned_by_engine[b].observations())
        record_row(Row("rotated + translated off-axis cap", a, b, "complex, all points", errors.tolist(), tolerance=same_mesh_tolerance(level)))
        za, zb = turned_by_engine[a].impedance["cap"], turned_by_engine[b].impedance["cap"]
        if za is not None and zb is not None:
            record_row(Row("off-axis cap: source-average pressure (impedance)", a, b, "complex impedance", (np.abs(za - zb) / np.abs(zb)).tolist(), tolerance=same_mesh_tolerance(level)))

    # Fixture 5: two instances of one body, apart, sources on one channel and
    # then on two. Normal motion, per the fixture's own caveat. The two-channel
    # sum must equal the one-channel solve and the identities stay distinct.
    points, triangles, _tags = sphere_mesh(0, split=False)
    shift = np.asarray([0.3, 0.0, 0.0])
    both_points = np.concatenate([points - shift, points + shift])
    both_triangles = np.concatenate([triangles, triangles + len(points)])
    both_tags = np.concatenate([np.full(len(triangles), 101), np.full(len(triangles), 102)])
    twins = record_for(gmsh22(both_points, both_triangles, both_tags), {"left": 101, "right": 102})
    for engine in engines:
        one = solve(engine, twins, [{"id": "hf", "source_ids": ["left", "right"]}])
        two = solve(engine, twins, [{"id": "hf-left", "source_ids": ["left"]}, {"id": "hf-right", "source_ids": ["right"]}])
        errors = relative_error(two.observations(), one.observations())
        record_row(Row("repeated HF: two bodies, two-channel sum", engine, "one channel", "complex, all points", errors.tolist(), tolerance=EXACT_TOLERANCE, note=f"identities kept: {two.channel_ids}"))
        mirrored = relative_error(
            two.pressure["hf-left"][:, :, ::-1], two.pressure["hf-right"]
        )
        record_row(Row("repeated HF: left mirrors right in the horizontal cut", engine, "mirror", "complex, polar", mirrored.tolist(), tolerance=EXACT_TOLERANCE))

    # Fixture 6: an x0 half and an x0+y0 quarter of the same sphere, as a CAD
    # author would return them already cut, against the whole sphere.
    points, triangles, tags = sphere_mesh(level, split=True)
    whole = record_for(gmsh22(points, triangles, tags), HEMISPHERE_TAGS)
    for planes in (("x0",), ("x0", "y0")):
        cut_points, cut_triangles, cut_tags = keep_side(points, triangles, tags, planes)
        cut = record_for(gmsh22(cut_points, cut_triangles, cut_tags), HEMISPHERE_TAGS, domain_planes=planes)
        for motion in ("normal", "axial"):
            channels = with_motion(ONE_CHANNEL, motion) if motion == "normal" else [{"id": "both", "source_ids": ["top", "bottom"], "motion": "axial"}]
            for engine in engines:
                full = solve(engine, whole, channels)
                reduced = solve(engine, cut, channels)
                errors = relative_error(reduced.observations(), full.observations())
                record_row(Row(f"{'+'.join(planes)} return vs whole, {motion}", engine, "whole", "complex, all points", errors.tolist(), tolerance=EXACT_TOLERANCE, note="fixture 6: reduced domain executed natively"))

    return {"rows": rows, "timings": timings, "level_errors": level_errors, "same_mesh_tolerance": {level: same_mesh_tolerance(level) for level in range(len(LADDER))}, "reference_edge_m": h_ref}


def write_markdown(path: Path, result: Mapping[str, Any], status: Mapping[str, str], environment: Mapping[str, Any]) -> None:
    lines = [
        "# Imported CAD on the CPU engines: same-mesh qualification",
        "",
        f"Generated by `scripts/qualify_imported_same_mesh.py` on {environment['generated_at']}.",
        "",
        "## Environment",
        "",
    ]
    lines += [f"- {key}: `{value}`" for key, value in environment.items() if key != "generated_at"]
    lines += ["", "Engines:", ""]
    lines += [f"- `{name}`: {state}" for name, state in status.items()]
    lines += ["", "## Discretisation error on the analytic bodies", "", "Worst complex error over every observation point and frequency, per refinement level.", "", "| Engine | Body | " + " | ".join(f"L{level}" for level in range(len(LADDER))) + " |", "| --- | --- |" + " --- |" * len(LADDER)]
    engines_seen = sorted({engine for (_kind, engine, _level) in result["level_errors"]})
    for engine in engines_seen:
        for kind in ("pulsating", "oscillating"):
            values = [float(np.max(result["level_errors"][(kind, engine, level)])) for level in range(len(LADDER))]
            lines.append(f"| {engine} | {kind} | " + " | ".join(f"{value:.2e}" for value in values) + " |")
    lines += ["", "Same-mesh tolerance per level: " + ", ".join(f"L{level} {value:.2e}" for level, value in result["same_mesh_tolerance"].items()) + f"; exact-equivalence tolerance {EXACT_TOLERANCE:.0e}."]
    lines += ["", "## Results", "", "| Fixture | Engine | Against | Quantity | Worst | Tolerance | Verdict |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in result["rows"]:
        verdict = "" if row.passed is None else ("pass" if row.passed else "**FAIL**")
        tolerance = "" if row.tolerance is None else f"{row.tolerance:.2e}"
        lines.append(f"| {row.fixture} | {row.engine} | {row.compared_with} | {row.quantity} | {row.worst:.2e} | {tolerance} | {verdict} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def environment_facts() -> dict[str, Any]:
    """What the run measured: platform, interpreter, and each solver's pin.

    Repository-relative facts only; no host name or local path is recorded.
    """

    import importlib.metadata as metadata
    import platform
    import subprocess
    from datetime import datetime, timezone

    facts: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
    }
    try:
        facts["waveguide-generator"] = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        facts["waveguide-generator"] = "unknown"
    for dist in ("hornlab-metal-bem", "hornlab-beat-bem", "hornlab-bempp-bem"):
        try:
            distribution = metadata.distribution(dist)
            direct = json.loads(distribution.read_text("direct_url.json") or "{}")
            commit = (direct.get("vcs_info") or {}).get("commit_id", "")[:7]
            facts[dist] = f"{distribution.version} @ {commit or 'no VCS record'}"
        except metadata.PackageNotFoundError:
            facts[dist] = "not installed"
    return facts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="run only the record-level fixtures, not the real CAD returns",
    )
    args = parser.parse_args(argv)
    status = available_engines()
    engines = [name for name in ("metal", "beat-cpu", "bempp") if status.get(name) == "available"]
    print("engines:", json.dumps(status, indent=2))
    if "beat-cpu" not in engines:
        print("BEAT-CPU is not available here; nothing to qualify.")
        return 2
    environment = environment_facts()
    result = run(engines)
    result["ingest_facts"] = {}
    if not args.skip_ingest:
        import tempfile

        from scripts.qualify_ingest_level import run_ingest_level

        def record_row(row: Row) -> Row:
            result["rows"].append(row)
            verdict = "" if row.passed is None else ("PASS" if row.passed else "FAIL")
            print(f"{row.fixture:52s} {row.engine:9s} vs {row.compared_with:12s} worst {row.worst:.3e} {verdict}")
            return row

        with tempfile.TemporaryDirectory() as tmp:
            result["ingest_facts"] = run_ingest_level(engines, Path(tmp), record_row)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "environment": environment,
                    "engines": status,
                    "discretisation_errors": {
                        f"{engine}/{kind}/L{level}": errors.tolist()
                        for (kind, engine, level), errors in result["level_errors"].items()
                    },
                    "same_mesh_tolerance": result["same_mesh_tolerance"],
                    "exact_tolerance": EXACT_TOLERANCE,
                    "reference_edge_m": result["reference_edge_m"],
                    "frequencies_hz": list(FREQUENCIES_HZ),
                    "rows": [
                        {
                            "fixture": row.fixture,
                            "engine": row.engine,
                            "compared_with": row.compared_with,
                            "quantity": row.quantity,
                            "errors": row.errors,
                            "worst": row.worst,
                            "tolerance": row.tolerance,
                            "passed": row.passed,
                            "note": row.note,
                        }
                        for row in result["rows"]
                    ],
                    "timings_s": result["timings"],
                    "ingest_facts": result["ingest_facts"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if args.markdown:
        write_markdown(args.markdown, result, status, environment)
    failed = [row for row in result["rows"] if row.passed is False]
    for row in failed:
        print(f"FAIL: {row.fixture} ({row.engine} vs {row.compared_with}): {row.worst:.3e} > {row.tolerance:.3e}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
