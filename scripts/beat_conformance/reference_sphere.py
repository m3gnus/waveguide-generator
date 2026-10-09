"""Independent acoustic reference, shared by the opt-in test and CLI.

Let s=+1 for exp(+i omega t), s=-1 for exp(-i omega t), omega=2*pi*f,
k=omega/c, S=4*pi*a**2, and real outward velocity v0=1 m/s. We use
p_s(r)=s*i*omega*rho*v0*a**2*exp(-s*i*k*(r-a))/(r*(1+s*i*k*a)),
Z_s=F/v=rho*c*S*(s*i*k*a)/(1+s*i*k*a). F is the scalar pressure
integral over the moving surface (force required against the acoustic load),
not the signed reaction force on the sphere or its net vector force.

BEAT's exterior ideal-source basis is a unit velocity phasor, with no documented
peak/RMS label and no sqrt(2) scaling in exterior_excitations/exterior_neumann.
WG qualification labels the unit velocity RMS (owner decision 2026-10-06, after the
LEM sphere reference showed BEAT's output follows its input convention with no
hidden factor): SPL = 20*log10(abs(p)/20e-6). The peak reading,
20*log10(abs(p)/sqrt(2)/20e-6), is kept in the report for comparison only.

Magnitude error is abs(abs(actual)/abs(exact)-1); phase error is
arg(actual/exact) in degrees, wrapped to [-180,180]. Between matched 1 m and
2 m rays, arg(p2/p1)=-s*k*Delta_r modulo 2*pi. We report its principal value
and the branch nearest that analytic expectation, rather than claiming an
independent unwrapped delay from these sparse frequencies. Both pressure and
self-load magnitude/phase gates use 2 percent and 2 degrees;
real and imaginary self-load parts separately use 3 percent; delay error uses 2 degrees.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

import numpy as np

from .reference_support import (
    POSITIVE_TIME,
    NEGATIVE_TIME,
    ReferenceSession,
    ReferenceSolveError,
    SolveOutcome,
    build_sphere_request,
    expected_engine_revision,
    validate_scoring_outcome,
)

RADIUS_M = 0.1
CENTRE_M = np.array([0.23, -0.17, 0.31])
EXPECTED_BEAT_REVISION = expected_engine_revision()
SOUND_SPEED = 343.0
DENSITY = 1.21
VELOCITY_REFERENCE = 1.0
FREQUENCIES_HZ = (100.0, 300.0, 1000.0)
MAGNITUDE_TOLERANCE = 0.02
PHASE_TOLERANCE_DEG = 2.0


def _sign(convention: str) -> int:
    if convention not in {POSITIVE_TIME, NEGATIVE_TIME}:
        raise ValueError("Unknown phasor convention.")
    return 1 if convention == POSITIVE_TIME else -1


def analytic_pressure(frequency_hz, radius_m, convention):
    s = _sign(convention)
    omega = 2 * np.pi * frequency_hz
    k = omega / SOUND_SPEED
    radius_m = np.asarray(radius_m)
    return (
        s
        * 1j
        * omega
        * DENSITY
        * VELOCITY_REFERENCE
        * RADIUS_M**2
        / (radius_m * (1 + s * 1j * k * RADIUS_M))
        * np.exp(-s * 1j * k * (radius_m - RADIUS_M))
    )


def analytic_impedance(frequency_hz, convention):
    ka = 2 * np.pi * frequency_hz / SOUND_SPEED * RADIUS_M
    ika = _sign(convention) * 1j * ka
    return DENSITY * SOUND_SPEED * (4 * np.pi * RADIUS_M**2) * ika / (1 + ika)


def observation_sets() -> dict:
    directions = np.array(
        [[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0], [0, -1, 0], [0, 0, -1], [1, 1, 1]],
        dtype=float,
    )
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    return {"r1m": (CENTRE_M + directions).tolist(), "r2m": (CENTRE_M + 2 * directions).tolist()}


def make_sphere_mesh(destination: Path, *, mesh_size_m: float = 0.02, centre_m=CENTRE_M) -> dict:
    """Small, outward, closed, linear triangle surface; no Julia or solve.

    Measure max edge length after meshing to enforce >=6 elements/wavelength.
    A finer curvature-driven size than that wavelength bound is intentional:
    at ka=0.18, geometry area error is more limiting than wavelength sampling.
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
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.model.add("reference-sphere")
        volume = gmsh.model.occ.addSphere(*centre_m, RADIUS_M)
        gmsh.model.occ.synchronize()
        surfaces = [
            tag for dim, tag in gmsh.model.getBoundary([(3, volume)], oriented=False) if dim == 2
        ]
        gmsh.model.addPhysicalGroup(2, surfaces, tag=1)
        gmsh.model.setPhysicalName(2, 1, "pulsating-sphere")
        gmsh.model.mesh.generate(2)
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        coordinates = np.asarray(coordinates).reshape(-1, 3)
        node_index = {int(tag): i for i, tag in enumerate(node_tags)}
        types, _, connectivity = gmsh.model.mesh.getElements(2)
        if list(types) != [2]:
            raise ValueError("Reference requires only first-order triangles.")
        faces = np.array([node_index[int(tag)] for tag in connectivity[0]]).reshape(-1, 3)
        triangles = coordinates[faces]
        crosses = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        if not np.all(
            np.einsum("ij,ij->i", crosses, triangles.mean(axis=1) - np.asarray(centre_m)) > 0
        ):
            raise ValueError("Sphere triangles must be nondegenerate and outward wound.")
        edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
        _, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
        if not np.all(counts == 2):
            raise ValueError("Reference sphere must be closed and manifold.")
        max_edge = float(
            np.linalg.norm(coordinates[edges[:, 0]] - coordinates[edges[:, 1]], axis=1).max()
        )
        elements_per_wavelength = SOUND_SPEED / (max(FREQUENCIES_HZ) * max_edge)
        if elements_per_wavelength < 6:
            raise ValueError("Mesh does not meet six elements per wavelength at 1000 Hz.")
        if len(faces) > 1500:
            raise ValueError("Reference mesh exceeds the 1500-triangle CPU work budget.")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(destination))
        return {
            "file": destination.name,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "triangle_count": len(faces),
            "node_count": len(coordinates),
            "mesh_size_m": mesh_size_m,
            "max_edge_m": max_edge,
            "minimum_elements_per_wavelength": elements_per_wavelength,
            "area_m2": float(np.linalg.norm(crosses, axis=1).sum() / 2),
            "analytic_area_m2": 4 * math.pi * RADIUS_M**2,
            "physical_tag": 1,
            "outward_winding": True,
            "closed_manifold": True,
        }
    finally:
        gmsh.finalize()


def _complex_pairs(values) -> list:
    return [[float(z.real), float(z.imag)] for z in np.asarray(values).ravel()]


def _errors(actual, exact) -> dict:
    actual, exact = np.asarray(actual), np.asarray(exact)
    magnitude = np.abs(np.abs(actual) / np.abs(exact) - 1)
    phase = np.angle(actual / exact, deg=True)
    return {
        "actual_complex": _complex_pairs(actual),
        "analytic_complex": _complex_pairs(exact),
        "relative_magnitude_error": magnitude.tolist(),
        "phase_error_deg": phase.tolist(),
        "passed": bool(
            np.all(magnitude <= MAGNITUDE_TOLERANCE)
            and np.all(np.abs(phase) <= PHASE_TOLERANCE_DEG)
        ),
    }


def _finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in np.asarray(values).ravel()]


def _spl_diagnostic(pressure, scale):
    amplitude = np.abs(pressure)
    values = np.full(amplitude.shape, np.nan)
    valid = amplitude > 0
    values[valid] = 20 * np.log10(amplitude[valid] / scale / 20e-6)
    return _finite_list(values)


def _report_provenance(record: dict) -> dict:
    """Retain measured identity/hashes; exclude machine identifiers and local paths."""
    engine = record.get("engine", {})
    runtime = record.get("runtime", {})
    execution = record.get("execution", {})
    return {
        "schema_version": record.get("schema_version"),
        "engine": {
            key: engine[key]
            for key in (
                "name",
                "version",
                "repository_revision",
                "repository_dirty",
                "source_sha256",
                "source_files_sha256",
            )
            if key in engine
        },
        "runtime": {
            key: runtime[key]
            for key in (
                "julia_version",
                "project_sha256",
                "manifest_sha256",
                "sysimage_sha256",
                "julia_threads",
                "blas_threads",
                "kernel",
            )
            if key in runtime
        },
        "execution": {
            key: execution[key]
            for key in ("backend", "precision", "solver_options")
            if key in execution
        },
        "meshes": [
            {key: mesh[key] for key in ("id", "sha256") if key in mesh}
            for mesh in record.get("meshes", [])
        ],
        "omitted": ["local_paths", "machine_identity", "device_identity"],
    }


def _clean_pinned_provenance(records: list[dict]) -> bool:
    """Require measured provenance from a clean checkout at the full pinned SHA."""
    return bool(records) and all(
        record.get("engine", {}).get("repository_revision") == EXPECTED_BEAT_REVISION
        and record.get("engine", {}).get("repository_dirty") is False
        for record in records
    )


def score_outcome(outcome: SolveOutcome, convention: str, points: dict) -> dict:
    """Compare decoded complex values directly; never conjugate worker results."""
    specifications = {
        f"pressure:{name}": (
            "exterior_pressure",
            "Pa",
            ("excitation", "observation"),
            (1, len(rows)),
            None,
        )
        for name, rows in points.items()
    }
    specifications["radiation_impedance"] = (
        "radiation_impedance",
        "N*s/m",
        ("radiator",),
        (1,),
        None,
    )
    validate_scoring_outcome(
        outcome, FREQUENCIES_HZ, convention, ("excitation:sphere",), specifications
    )
    frequencies = []
    for result in outcome.results:
        quantities = {item.id: item for item in result.quantities}
        pressure_scores = {}
        pressure_values = {}
        for name, coordinates in points.items():
            pressure = quantities[f"pressure:{name}"].values[0]
            pressure_values[name] = pressure
            radius = np.linalg.norm(np.asarray(coordinates) - CENTRE_M, axis=1)
            pressure_scores[name] = _errors(
                pressure, analytic_pressure(result.freq_hz, radius, convention)
            )
            pressure_scores[name]["spl_db_if_input_peak_abs_p_over_sqrt2"] = _spl_diagnostic(
                pressure, np.sqrt(2)
            )
            pressure_scores[name]["spl_db_if_input_rms_abs_p"] = _spl_diagnostic(pressure, 1.0)
        impedance = _errors(
            quantities["radiation_impedance"].values,
            np.array([analytic_impedance(result.freq_hz, convention)]),
        )
        actual_z = quantities["radiation_impedance"].values
        exact_z = analytic_impedance(result.freq_hz, convention)
        impedance["relative_real_error"] = (np.abs(actual_z.real / exact_z.real - 1)).tolist()
        impedance["relative_imag_error"] = (np.abs(actual_z.imag / exact_z.imag - 1)).tolist()
        impedance["passed"] = impedance["passed"] and bool(
            np.all(np.abs(actual_z.real / exact_z.real - 1) <= 0.03)
            and np.all(np.abs(actual_z.imag / exact_z.imag - 1) <= 0.03)
        )
        k_delta_r = 2 * np.pi * result.freq_hz / SOUND_SPEED * (2.0 - 1.0)
        expected_phase = -_sign(convention) * k_delta_r
        valid_delay = (np.abs(pressure_values["r1m"]) > 0) & (np.abs(pressure_values["r2m"]) > 0)
        principal = np.full(pressure_values["r1m"].shape, np.nan)
        principal[valid_delay] = np.angle(
            pressure_values["r2m"][valid_delay] / pressure_values["r1m"][valid_delay]
        )
        residual = np.angle(np.exp(1j * (principal - expected_phase)))
        nearest = expected_phase + residual
        delay = {
            "k_delta_r_rad": k_delta_r,
            "expected_signed_phase_difference_rad": expected_phase,
            "principal_phase_difference_rad": _finite_list(principal),
            "phase_difference_nearest_analytic_branch_rad": _finite_list(nearest),
            "positive_propagation_delay_rad": _finite_list((-_sign(convention) * nearest)),
            "phase_error_deg": _finite_list(np.rad2deg(residual)),
            "branch_selection": "nearest analytic expectation; delay is measured modulo 2*pi",
            "undefined_reason": None
            if valid_delay.all()
            else "zero pressure has no phase or finite SPL",
            "passed": bool(
                valid_delay.all() and np.all(np.abs(np.rad2deg(residual)) <= PHASE_TOLERANCE_DEG)
            ),
        }
        diagnostics = {
            key: result.diagnostics[key]
            for key in (
                "phasor_convention",
                "precision",
                "bem_backend",
                "symmetry",
                "formulation",
                "regular_quadrature_order",
                "timings",
            )
            if key in result.diagnostics
        }
        frequencies.append(
            {
                "frequency_hz": result.freq_hz,
                "pressure": pressure_scores,
                "radiation_impedance": impedance,
                "propagation_1m_to_2m": delay,
                "diagnostics": diagnostics,
                "passed": all(score["passed"] for score in pressure_scores.values())
                and impedance["passed"]
                and delay["passed"],
            }
        )
    return {
        "phasor_convention": convention,
        "status": outcome.status,
        "frequencies": frequencies,
        "engine_runs": [_report_provenance(record) for record in outcome.engine_runs],
        "provenance_revision_passed": _clean_pinned_provenance(outcome.engine_runs),
        "worker": {
            key: outcome.worker_info[key]
            for key in (
                "protocol",
                "contracts",
                "phasor_conventions",
                "precisions",
                "solve_kinds",
                "cancellation",
            )
            if key in outcome.worker_info
        },
        "passed": _clean_pinned_provenance(outcome.engine_runs)
        and outcome.status == "completed"
        and len(outcome.results) == len(FREQUENCIES_HZ)
        and all(
            math.isclose(r.freq_hz, f, rel_tol=1e-6)
            for r, f in zip(outcome.results, FREQUENCIES_HZ)
        )
        and all(item["passed"] for item in frequencies),
    }


def write_report(report: dict, destination: Path) -> None:
    """Publish a UTF-8 report atomically on every supported OS."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            suffix=".json",
            delete=False,
        ) as stream:
            staging = Path(stream.name)
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        if os.name != "nt":
            staging.chmod(0o644)
        os.replace(staging, destination)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def run_reference(*, julia: str | Path, out: Path) -> dict:
    """Opt-in CPU solve: offset/translated meshes, both phasors, default-quadrature audit."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    mesh = out / "sphere.msh"
    mesh_info = make_sphere_mesh(mesh)
    translated_mesh = out / "sphere-local.msh"
    make_sphere_mesh(translated_mesh, centre_m=(0, 0, 0))
    points = observation_sets()
    report = {
        "schema_version": 1,
        "reference": "pulsating sphere",
        "expected_beat_revision": EXPECTED_BEAT_REVISION,
        "centre_m": CENTRE_M.tolist(),
        "radius_m": RADIUS_M,
        "sound_speed_m_per_s": SOUND_SPEED,
        "density_kg_per_m3": DENSITY,
        "velocity_reference_m_per_s": VELOCITY_REFERENCE,
        "velocity_normalization": "unit outward normal velocity",
        "beat_exterior_amplitude_convention": "unspecified unit complex phasor (neither peak nor RMS mandated)",
        "wg_reference_amplitude_convention": "rms",
        "amplitude_evidence": [
            "BEAT Compiled System Contract: 1 m/s velocity basis",
            "BeatEngineCompiledDriver.jl: exterior_excitations defaults amplitude to 1",
            "exterior_neumann applies neumann_scale(rho, omega) without sqrt(2)",
            "Exterior quantity metadata has no amplitude_convention; coupled RMS labels are separate",
        ],
        "formulas": {
            "positive_time_pressure": "i*omega*rho*v0*a^2*exp(-i*k*(r-a))/(r*(1+i*k*a))",
            "negative_time_pressure": "-i*omega*rho*v0*a^2*exp(+i*k*(r-a))/(r*(1-i*k*a))",
            "positive_time_self_load": "rho*c*(4*pi*a^2)*i*k*a/(1+i*k*a)",
            "negative_time_self_load": "rho*c*(4*pi*a^2)*(-i*k*a)/(1-i*k*a)",
            "spl_if_input_peak": "20*log10(abs(p)/sqrt(2)/20e-6)",
            "spl_if_input_rms": "20*log10(abs(p)/20e-6)",
            "propagation": "arg(p2/p1)=-s*k*(r2-r1) modulo 2*pi; s=+1 for positive time",
        },
        "self_load_definition": "scalar integral of pressure over moving surface / velocity; N*s/m; no reaction-force minus sign",
        "tolerances": {
            "relative_magnitude": MAGNITUDE_TOLERANCE,
            "phase_deg": PHASE_TOLERANCE_DEG,
            "impedance_real_and_imag_relative": 0.03,
            "propagation_phase_deg": PHASE_TOLERANCE_DEG,
            "adjusted": False,
        },
        "mesh": mesh_info,
        "observation_sets_m": points,
        "runs": [],
        "passed": False,
    }
    started = time.perf_counter()
    try:
        with ReferenceSession(
            julia=julia, backend="cpu", julia_threads=2, record_dir=out / "observations"
        ) as runtime:
            for convention, quadrature in (
                (POSITIVE_TIME, "fixed"),
                (NEGATIVE_TIME, "fixed"),
                (POSITIVE_TIME, "default"),
            ):
                request = build_sphere_request(
                    translated_mesh if convention == NEGATIVE_TIME else mesh,
                    FREQUENCIES_HZ,
                    points,
                    phasor_convention=convention,
                    sound_speed_m_per_s=SOUND_SPEED,
                    density_kg_per_m3=DENSITY,
                )
                # Fixed order 4 avoids wavelength-mode's lower default regular
                # quadrature in this absolute-phase and load reference.
                if convention == NEGATIVE_TIME:
                    request["compiled_system"]["meshes"][0]["translation_m"] = CENTRE_M.tolist()
                if quadrature == "fixed":
                    request["solver_options"].update(
                        regular_quadrature_mode="fixed", quadrature_order=4, singular_order=4
                    )
                else:
                    for key in ("regular_quadrature_mode", "quadrature_order", "singular_order"):
                        request["solver_options"].pop(key, None)
                try:
                    outcome = runtime.solve(request)
                except ReferenceSolveError as exc:
                    outcome = exc.outcome
                    required = {"pressure:r1m", "pressure:r2m", "radiation_impedance"}
                    if len(outcome.results) == len(FREQUENCIES_HZ) and all(
                        {q.id for q in r.quantities} >= required for r in outcome.results
                    ):
                        score = score_outcome(outcome, convention, points)
                    else:
                        score = {
                            "phasor_convention": convention,
                            "status": outcome.status,
                            "frequencies": [],
                            "passed": False,
                            "engine_runs": [_report_provenance(p) for p in outcome.engine_runs],
                            "provenance_revision_passed": _clean_pinned_provenance(
                                outcome.engine_runs
                            ),
                        }
                    score.update(passed=False, failure_type=type(exc).__name__)
                    if quadrature == "fixed":
                        score["quadrature"] = quadrature
                        report["runs"].append(score)
                        raise
                else:
                    score = score_outcome(outcome, convention, points)
                score["quadrature"] = quadrature
                score["default_quadrature_qualified"] = (
                    score["passed"] if quadrature == "default" else None
                )
                score["gate_policy"] = (
                    "required"
                    if quadrature == "fixed"
                    else "informational; qualifies only if same tolerances pass"
                )
                score["translation_m_exercised"] = convention == NEGATIVE_TIME
                report["runs"].append(score)
        report["passed"] = (
            len(report["runs"]) == 3
            and all(run["passed"] for run in report["runs"] if run["quadrature"] == "fixed")
            and all(
                run["provenance_revision_passed"]
                for run in report["runs"]
                if "failure_type" not in run or run["engine_runs"]
            )
        )
    except Exception as exc:
        # Exception prose may include executable paths or device names. Keep
        # those in console diagnostics, never in this portable result file.
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        report["elapsed_s"] = time.perf_counter() - started
        write_report(report, out / "reference-sphere.json")
    return report
