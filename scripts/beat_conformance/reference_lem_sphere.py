"""Independent translating-sphere / electrodynamic reference (no client solver).

Set s=+1/-1 for exp(s*j*omega*t), k=omega/c, x=k*a, and u along +z.
Euler's equation gives dp/dr=-s*j*omega*rho*u*cos(theta) at r=a.
The outgoing l=1 solution is A*h_s(k*r)*cos(theta), with
h_+=h_1^(2)=j_1-i*y_1, h_-=h_1^(1)=j_1+i*y_1. Therefore
p_s=-s*j*rho*c*u*h_s(k*r)/h_s'(k*a)*cos(theta), where prime differentiates
its dimensionless argument. For positive time h_+=-exp(-j*x)*(x-j)/x^2.
Its far field is h_s(k*r) ~ -exp(-s*j*k*r)/(k*r), so
p_s ~ s*j*rho*c*u*exp(-s*j*k*r)/(k*r*h_s'(k*a))*cos(theta).
For ka << 1, h_s'(ka) ~ -2*s*j/(ka)^3, giving
p_s ~ -rho*omega^2*u*a^3*exp(-s*j*k*r)*cos(theta)/(2*c*r).
This dipole has a node at theta=90 degrees and zero net volume velocity.

Force required against the load is integral(p*cos(theta)*dS), not the reaction
force. integral(cos^2(theta)*dS)=4*pi*a^2/3, hence
Zrad_s=(-s*j*rho*c*4*pi*a^2/3)*h_s(ka)/h_s'(ka)
       =(4*pi*a^2*rho*c/3)*(x^4+s*j*x*(2+x^2))/(4+x^4).
Ze_s=Re+s*j*omega*Le; Zm_s=Rms+s*j*omega*Mmd+1/(s*j*omega*Cms).
u_s=Bl*V/(Ze_s*(Zm_s+Zrad_s)+Bl^2); i_s=(V-Bl*u_s)/Ze_s; Zin_s=V/i_s.
For real V, negative-time outputs are conjugates of positive-time outputs.
These equations preserve the voltage's amplitude convention, with no sqrt(2)
or 1/2 scale. WG qualification labels amplitudes RMS (owner decision 2026-10-06,
after this reference showed no hidden factor): SPL = 20*log10(abs(p)/20e-6).
The peak reading is kept in the report for comparison only.

Standard l=1/Hankel separation and compact translating-sphere dipole limit:
https://link.springer.com/chapter/10.1007/978-3-030-44787-8_12 (section 12.6).
The formula and load above are derived here from Euler's boundary condition,
then checked independently by numerical surface integration in fast tests.
"""

from __future__ import annotations

import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
from scipy.special import spherical_jn, spherical_yn

from .reference_support import POSITIVE_TIME, NEGATIVE_TIME
from .lem_fixture import (
    COMPONENT_ID,
    PORT_ID,
    DRIVER_PARAMETERS,
    REFERENCE_VOLTAGE_V,
    build_lem_reference_request,
)
from .reference_support import (
    ReferenceSession,
    ReferenceSolveError,
    SolveOutcome,
    validate_scoring_outcome,
)
from .reference_sphere import (
    CENTRE_M,
    EXPECTED_BEAT_REVISION,
    DENSITY,
    SOUND_SPEED,
    RADIUS_M,
    _clean_pinned_provenance,
    _complex_pairs,
    _report_provenance,
    _sign,
    write_report,
)

OUTER_RADIUS_M = 0.13
FREQUENCIES_HZ = (40.0, 80.0, 160.0, 400.0)
ANGLES_DEG = (0.0, 45.0, 90.0, 135.0, 180.0)
NODE_ABSOLUTE_FRACTION = 0.03  # absolute pressure / exact on-axis pressure at same radius


def hankel1(x, convention, *, derivative=False):
    """Outgoing spherical Hankel of degree 1; derivative is with respect to x."""
    return spherical_jn(1, x, derivative) - _sign(convention) * 1j * spherical_yn(1, x, derivative)


def analytic_radiation_impedance(frequency_hz, convention):
    """Generalized opposing load F/u in N*s/m, for translating radius a."""
    x = 2 * np.pi * np.asarray(frequency_hz) * RADIUS_M / SOUND_SPEED
    return (
        4
        * np.pi
        * RADIUS_M**2
        * DENSITY
        * SOUND_SPEED
        / 3
        * (x**4 + _sign(convention) * 1j * x * (2 + x * x))
        / (4 + x**4)
    )


def analytic_driver(frequency_hz, convention, *, voltage_v=REFERENCE_VOLTAGE_V):
    """Return u [m/s], i [A], Zin [ohm]; input and outputs share one convention."""
    s = _sign(convention)
    w = 2 * np.pi * frequency_hz
    d = DRIVER_PARAMETERS
    ze = d["re_ohm"] + s * 1j * w * d["le_h"]
    zm = d["rms_n_s_per_m"] + s * 1j * w * d["mmd_kg"] + 1 / (s * 1j * w * d["cms_m_per_n"])
    u = (
        d["bl_n_per_a"]
        * voltage_v
        / (
            ze * (zm + analytic_radiation_impedance(frequency_hz, convention))
            + d["bl_n_per_a"] ** 2
        )
    )
    current = (voltage_v - d["bl_n_per_a"] * u) / ze
    return u, current, voltage_v / current


def analytic_pressure(frequency_hz, radius_m, cos_theta, convention, *, velocity=1.0):
    """Exact field above, radius from sphere centre and signed axial direction."""
    k = 2 * np.pi * frequency_hz / SOUND_SPEED
    return (
        -_sign(convention)
        * 1j
        * DENSITY
        * SOUND_SPEED
        * velocity
        * hankel1(k * np.asarray(radius_m), convention)
        / hankel1(k * RADIUS_M, convention, derivative=True)
        * np.asarray(cos_theta)
    )


def observation_points():
    """Ten points: radii 1,2 m and theta 0,45,90,135,180 from +z, offset centre."""
    theta = np.deg2rad(ANGLES_DEG)
    directions = np.column_stack([np.sin(theta), np.zeros_like(theta), np.cos(theta)])
    return np.concatenate([CENTRE_M + r * directions for r in (1.0, 2.0)])


def _packed(array, dtype):
    values = np.asarray(array, dtype=dtype, order="C")
    return {
        "dtype": dtype,
        "shape": list(values.shape),
        "data": base64.b64encode(values.tobytes()).decode("ascii"),
    }


def _mesh_data(points, blocks, names):
    return {
        "schema_version": 1,
        "points": _packed(points, "<f8"),
        "physical_names": names,
        "cells": [
            {
                "type": kind,
                "connectivity": _packed(indices, "<i8"),
                "physical_tags": _packed(np.full(len(indices), tag), "<i8"),
            }
            for kind, indices, tag in blocks
        ],
    }


def shell_mesh_buffers(points, inner_faces, outer_faces, tetrahedra):
    """Pack conforming P1 arrays; prove full boundary coverage and outward normals.

    FEM triangle order is inner then outer. Compact BEM vertex order is sorted
    outer FEM index; face order exactly matches outer FEM triangles. Both outer
    normals are bounded-region-outward, hence normal_sign=+1. WG qualification's builder
    uses no nearest-point matching, tolerance guessing, or separate remeshing.
    The pinned BEAT worker rebuilds the map from loaded meshes and derives
    normal_sign from winding (combined_interface_map_from_wire). The numerical
    run therefore does not validate WG qualification's supplied topology.
    """
    points = np.asarray(points, dtype=float)
    inner, outer, tets = [
        np.asarray(a, dtype=np.int64).copy() for a in (inner_faces, outer_faces, tetrahedra)
    ]
    if (
        points.ndim != 2
        or points.shape[1] != 3
        or not np.isfinite(points).all()
        or any(
            a.ndim != 2 or a.shape[1] != n or not len(a) or a.min() < 0 or a.max() >= len(points)
            for a, n in ((inner, 3), (outer, 3), (tets, 4))
        )
    ):
        raise ValueError("Invalid shell mesh arrays.")
    v = points[tets]
    det = np.linalg.det(np.stack([v[:, 1] - v[:, 0], v[:, 2] - v[:, 0], v[:, 3] - v[:, 0]], axis=2))
    if np.any(np.abs(det) < 1e-15):
        raise ValueError("Degenerate tetrahedron.")
    tets[det < 0] = tets[det < 0][:, [1, 0, 2, 3]]
    facets = Counter(
        tuple(sorted(face))
        for tet in tets
        for face in (tet[[0, 1, 2]], tet[[0, 1, 3]], tet[[0, 2, 3]], tet[[1, 2, 3]])
    )
    supplied = Counter(tuple(sorted(face)) for face in np.concatenate([inner, outer]))
    if any(n > 2 for n in facets.values()) or supplied != Counter(
        {f: 1 for f, n in facets.items() if n == 1}
    ):
        raise ValueError("Shell triangles must exactly cover the tetrahedral boundary.")
    areas = []
    for faces, radial_sign in ((inner, -1), (outer, 1)):
        triangles = points[faces]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        dot = np.einsum("ij,ij->i", cross, triangles.mean(axis=1))
        if np.any(np.abs(dot) < 1e-15):
            raise ValueError("Degenerate spherical face.")
        flip = radial_sign * dot < 0
        faces[flip] = faces[flip][:, [0, 2, 1]]
        edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
        _, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
        if not np.all(counts == 2):
            raise ValueError("Each shell sphere must be closed and manifold.")
        areas.append(float(np.linalg.norm(cross, axis=1).sum() / 2))
    vertices = np.unique(outer)
    inverse = np.full(len(points), -1, dtype=int)
    inverse[vertices] = np.arange(len(vertices))
    fem = _mesh_data(
        points,
        [("triangle", inner, 1), ("triangle", outer, 2), ("tetra", tets, 3)],
        {"diaphragm": [1, 2], "interface": [2, 2], "air": [3, 3]},
    )
    bem = _mesh_data(points[vertices], [("triangle", inverse[outer], 2)], {"interface": [2, 2]})
    topology = {
        "fem_vertex_indices": vertices.tolist(),
        "fem_to_bem_vertex_indices": list(range(len(vertices))),
        "fem_face_indices": list(range(len(inner), len(inner) + len(outer))),
        "bem_face_indices": list(range(len(outer))),
        "normal_sign": [1] * len(outer),
        "max_coordinate_error": 0.0,
        "fem_facets_on_tetra_boundary": len(outer),
        "bem_boundary_edges": 0,
    }
    all_edges = np.concatenate([tets[:, [i, j]] for i in range(4) for j in range(i + 1, 4)])
    max_edge = float(
        np.linalg.norm(points[all_edges[:, 0]] - points[all_edges[:, 1]], axis=1).max()
    )
    epw = SOUND_SPEED / (max(FREQUENCIES_HZ) * max_edge)
    if epw < 6:
        raise ValueError("Shell does not meet six elements per wavelength.")
    info = {
        "fem_nodes": len(points),
        "tetrahedra": len(tets),
        "inner_triangles": len(inner),
        "outer_triangles": len(outer),
        "bem_nodes": len(vertices),
        "max_edge_m": max_edge,
        "minimum_elements_per_wavelength": epw,
        "inner_area_m2": areas[0],
        "outer_area_m2": areas[1],
        "tetrahedral_volume_m3": float(np.abs(det).sum() / 6),
        "normal_sign": 1,
        "coordinate_error_m": 0.0,
        "closed_manifold": True,
        "boundary_coverage_verified": True,
    }
    for name, data in (("fem", fem), ("bem", bem)):
        info[f"{name}_sha256"] = hashlib.sha256(
            json.dumps(data, sort_keys=True).encode("utf-8")
        ).hexdigest()
    return fem, bem, topology, info


def make_shell_mesh(*, mesh_size_m=0.016):
    """Small deterministic Gmsh P1 air shell at origin, placed via translation_m.

    Default mesh target is 16 mm, finer than the wavelength bound. Guard at
    2500 outer/BEM triangles and 10000 tetrahedra before solving; these limits
    do not guarantee Julia wall time.
    """
    import gmsh

    if not math.isfinite(mesh_size_m) or mesh_size_m <= 0:
        raise ValueError("Mesh size must be finite and positive.")
    if gmsh.isInitialized():
        raise RuntimeError("Reference meshing requires its own Gmsh session.")
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Mesh.RandomSeed", 42)
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_m)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_m)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.add("lem-sphere-shell")
        outer = gmsh.model.occ.addSphere(0, 0, 0, OUTER_RADIUS_M)
        inner = gmsh.model.occ.addSphere(0, 0, 0, RADIUS_M)
        volumes, _ = gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        surfaces = gmsh.model.getBoundary(volumes, oriented=False)
        inner_tags, outer_tags = [], []
        for dim, tag in surfaces:
            area = gmsh.model.occ.getMass(dim, tag)
            (
                inner_tags
                if abs(area - 4 * np.pi * RADIUS_M**2) < abs(area - 4 * np.pi * OUTER_RADIUS_M**2)
                else outer_tags
            ).append(tag)
        gmsh.model.addPhysicalGroup(2, inner_tags, 1)
        gmsh.model.addPhysicalGroup(2, outer_tags, 2)
        gmsh.model.addPhysicalGroup(3, [tag for _, tag in volumes], 3)
        gmsh.model.mesh.generate(3)
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        order = np.argsort(node_tags)
        points = np.asarray(coordinates).reshape(-1, 3)[order]
        lookup = {int(tag): i for i, tag in enumerate(np.asarray(node_tags)[order])}

        def elements(dimension, entities, expected_type, width):
            rows = []
            for entity in entities:
                types, _, blocks = gmsh.model.mesh.getElements(dimension, entity)
                if list(types) != [expected_type]:
                    raise ValueError("Reference requires linear triangles and tetrahedra.")
                rows.extend(lookup[int(tag)] for tag in blocks[0])
            return np.asarray(rows).reshape(-1, width)

        result = shell_mesh_buffers(
            points,
            elements(2, inner_tags, 2, 3),
            elements(2, outer_tags, 2, 3),
            elements(3, [tag for _, tag in volumes], 4, 4),
        )
        info = result[3]
        if info["outer_triangles"] > 2500 or info["tetrahedra"] > 10000:
            raise ValueError("Reference exceeds the CPU mesh budget.")
        info["mesh_size_m"] = mesh_size_m
        return result
    finally:
        gmsh.finalize()


def _errors(actual, exact, *, magnitude_tolerance=0.02, phase_tolerance_deg=2.0):
    actual, exact = np.asarray(actual), np.asarray(exact)
    magnitude = np.abs(np.abs(actual) / np.abs(exact) - 1)
    phase = np.angle(actual / exact, deg=True)
    return {
        "actual_complex": _complex_pairs(actual),
        "analytic_complex": _complex_pairs(exact),
        "relative_magnitude_error": magnitude.tolist(),
        "phase_error_deg": phase.tolist(),
        "passed": bool(
            np.all(magnitude <= magnitude_tolerance)
            and np.all(np.abs(phase) <= phase_tolerance_deg)
        ),
    }


def score_outcome(outcome: SolveOutcome, convention: str, points_m=None):
    """Gate unmodified complex outputs. At the dipole node use an absolute gate.

    Node tolerance is 3% of the exact on-axis pressure at the SAME frequency and
    radius, avoiding relative division by zero. Zin is derived only for scoring
    from the returned current, not used to solve or modify BEAT's driver chain.
    Net amplitude/convention/sign errors in u/i/p are detected; these gates do
    not qualify radiation resistance in the feedback load. A 0.5*Re(Zrad)
    feedback error passes. The exterior sphere checks its separate self-load output;
    it does not qualify coupled feedback resistance.
    """
    points = observation_points() if points_m is None else np.asarray(points_m)
    specifications = {
        name: (name, unit, ("excitation", "transducer"), (1, 1), [COMPONENT_ID])
        for name, unit in (("diaphragm_velocity", "m/s"), ("voice_coil_current", "A"))
    }
    specifications["pressure"] = (
        "exterior_pressure",
        "Pa",
        ("excitation", "observation"),
        (1, len(points)),
        None,
    )
    validate_scoring_outcome(outcome, FREQUENCIES_HZ, convention, (PORT_ID,), specifications)
    relative = points - CENTRE_M
    radii = np.linalg.norm(relative, axis=1)
    cosines = relative[:, 2] / radii
    node = np.abs(cosines) < 1e-12
    frequencies = []
    for result in outcome.results:
        quantities = {q.id: q for q in result.quantities}
        u, current, zin = analytic_driver(result.freq_hz, convention)
        actual_u = quantities["diaphragm_velocity"].values[0, 0]
        actual_i = quantities["voice_coil_current"].values[0, 0]
        velocity_score = _errors(actual_u, u)
        current_score = _errors(actual_i, current)
        # A zero returned current fails current/Zin rather than publishing inf.
        impedance_score = (
            _errors(REFERENCE_VOLTAGE_V / actual_i, zin)
            if actual_i != 0
            else {"passed": False, "failure": "zero current"}
        )
        p = quantities["pressure"].values[0]
        exact_p = analytic_pressure(result.freq_hz, radii, cosines, convention, velocity=u)
        pressure = _errors(
            p[~node], exact_p[~node], magnitude_tolerance=0.03, phase_tolerance_deg=3.0
        )
        pressure["observation_indices"] = np.flatnonzero(~node).tolist()
        node_scale = np.abs(
            analytic_pressure(result.freq_hz, radii[node], 1, convention, velocity=u)
        )
        node_error = np.abs(p[node] - exact_p[node])
        pressure["node"] = {
            "observation_indices": np.flatnonzero(node).tolist(),
            "actual_complex": _complex_pairs(p[node]),
            "analytic_complex": _complex_pairs(exact_p[node]),
            "absolute_error_pa": node_error.tolist(),
            "absolute_tolerance_pa": (NODE_ABSOLUTE_FRACTION * node_scale).tolist(),
            "passed": bool(np.all(node_error <= NODE_ABSOLUTE_FRACTION * node_scale)),
        }
        pressure["passed"] = pressure["passed"] and pressure["node"]["passed"]
        # theta=0 at r=1 m is observation 0; preserve both amplitude labels.
        on_axis = abs(p[0])
        spl = {
            "voltage_label": "2.83 V",
            "client_amplitude_convention": "rms",
            "db_if_input_peak_abs_p_over_sqrt2": float(20 * np.log10(on_axis / np.sqrt(2) / 20e-6))
            if on_axis
            else None,
            "db_if_input_rms_abs_p": float(20 * np.log10(on_axis / 20e-6)) if on_axis else None,
        }
        voltage = result.diagnostics.get("transducer_reference_voltage_v")
        voltage_passed = (
            not isinstance(voltage, bool)
            and isinstance(voltage, (int, float))
            and math.isclose(voltage, REFERENCE_VOLTAGE_V, rel_tol=1e-6)
        )
        frequencies.append(
            {
                "frequency_hz": result.freq_hz,
                "diaphragm_velocity": velocity_score,
                "voice_coil_current": current_score,
                "input_impedance_ohm": impedance_score,
                "exterior_pressure": pressure,
                "spl_1m_on_axis": spl,
                "reference_voltage_passed": voltage_passed,
                "returned_amplitude_metadata": {
                    name: quantities[name].metadata.get("amplitude_convention")
                    for name in ("diaphragm_velocity", "voice_coil_current", "pressure")
                },
                "transducer_component_ids": quantities["diaphragm_velocity"].metadata.get(
                    "component_ids"
                ),
                "diagnostics": {
                    key: result.diagnostics[key]
                    for key in (
                        "phasor_convention",
                        "precision",
                        "bem_backend",
                        "transducer_reference_voltage_v",
                        "timings",
                    )
                    if key in result.diagnostics
                },
                "passed": voltage_passed
                and all(
                    score["passed"]
                    for score in (velocity_score, current_score, impedance_score, pressure)
                ),
            }
        )
    complete = (
        outcome.status == "completed"
        and len(outcome.results) == len(FREQUENCIES_HZ)
        and all(
            math.isclose(r.freq_hz, f, rel_tol=1e-6)
            for r, f in zip(outcome.results, FREQUENCIES_HZ)
        )
    )
    return {
        "phasor_convention": convention,
        "status": outcome.status,
        "frequencies": frequencies,
        "engine_runs": [_report_provenance(p) for p in outcome.engine_runs],
        "provenance_revision_passed": _clean_pinned_provenance(outcome.engine_runs),
        "passed": _clean_pinned_provenance(outcome.engine_runs)
        and complete
        and all(f["passed"] for f in frequencies),
    }


def run_reference(*, julia: str | Path, out: Path):
    """Opt-in public-worker CPU run; four frequencies, both time conventions.

    No Julia is launched merely by importing, meshing, or scoring this module.
    The warm CPU target is under two minutes; it requires overseer measurement.
    A failed gate preserves its report and never relaxes the tolerances.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fem, bem, topology, mesh_info = make_shell_mesh()
    points = observation_points()
    report = {
        "schema_version": 1,
        "reference": "electrodynamic translating rigid sphere in free air",
        "expected_beat_revision": EXPECTED_BEAT_REVISION,
        "client_amplitude_convention": "rms",
        "reference_voltage_v": REFERENCE_VOLTAGE_V,
        "driver_parameters": DRIVER_PARAMETERS,
        "sphere_radius_m": RADIUS_M,
        "shell_outer_radius_m": OUTER_RADIUS_M,
        "centre_m": CENTRE_M.tolist(),
        "density_kg_per_m3": DENSITY,
        "sound_speed_m_per_s": SOUND_SPEED,
        "frequencies_hz": list(FREQUENCIES_HZ),
        "angles_deg": list(ANGLES_DEG),
        "observation_points_m": points.tolist(),
        "mesh": mesh_info,
        "formulas_and_derivation": __doc__,
        "tolerances": {
            "driver_relative_magnitude": 0.02,
            "driver_phase_deg": 2.0,
            "pressure_relative_magnitude": 0.03,
            "pressure_phase_deg": 3.0,
            "node_absolute_fraction_of_same_radius_on_axis": NODE_ABSOLUTE_FRACTION,
            "adjusted": False,
        },
        "amplitude_metadata_source_audit": {
            "revision": EXPECTED_BEAT_REVISION,
            "source": "src/beat_engine/julia_local/BeatEngineCompiledDriver.jl",
            "interface_average_normal_velocity": "rms",
            "interface_radiated_pressure": "rms",
            "diaphragm_velocity": "no amplitude_convention key",
            "voice_coil_current": "no amplitude_convention key",
            "exterior_pressure": "no amplitude_convention key",
        },
        "scale_error_detection": {
            "sqrt2_or_inverse_sqrt2": True,
            "half_or_double": True,
            "magnitude_shift_db": [float(20 * np.log10(np.sqrt(2))), float(20 * np.log10(2))],
            "scope": "Net amplitude/convention/sign errors in velocity, current and pressure (sqrt(2), 1/2, reversal). Uniform voltage scaling changes all three; Zin cancels.",
            "limitation": "A 0.5*Re(Zrad) feedback error passes the driver gates. Exterior sphere self-load output is checked separately; coupled feedback resistance is not qualified.",
        },
        "interface_map_note": "The pinned BEAT worker rebuilds the FEM-BEM map from loaded meshes and derives normal_sign from winding (combined_interface_map_from_wire); the run does not validate WG qualification's supplied topology. No nearest-point matching refers only to WG qualification's builder.",
        "runs": [],
        "passed": False,
    }
    started = time.perf_counter()
    try:
        with ReferenceSession(
            julia=julia, backend="cpu", julia_threads=2, record_dir=out / "observations"
        ) as runtime:
            for convention, name in ((POSITIVE_TIME, "positive"), (NEGATIVE_TIME, "negative")):
                request = build_lem_reference_request(
                    fem,
                    bem,
                    topology,
                    FREQUENCIES_HZ,
                    points,
                    centre_m=CENTRE_M,
                    phasor_convention=convention,
                )
                write_report(request, out / f"reference-lem-request-{name}.json")
                try:
                    outcome = runtime.solve(request)
                except ReferenceSolveError as exc:
                    # Structural failures can omit quantities; keep only safe identity
                    # fields, and retain a full numerical score when outputs exist.
                    if len(exc.outcome.results) == len(FREQUENCIES_HZ) and all(
                        {q.id for q in r.quantities}
                        >= {"diaphragm_velocity", "voice_coil_current", "pressure"}
                        for r in exc.outcome.results
                    ):
                        report["runs"].append(score_outcome(exc.outcome, convention, points))
                    raise
                score = score_outcome(outcome, convention, points)
                report["runs"].append(score)
        report["passed"] = len(report["runs"]) == 2 and all(r["passed"] for r in report["runs"])
    except Exception as exc:
        report["failure_type"] = type(exc).__name__  # exception prose can contain local paths
        raise
    finally:
        report["elapsed_s"] = time.perf_counter() - started
        write_report(report, out / "reference-lem-sphere.json")
    return report
