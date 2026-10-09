"""Qualify WG's opt-in v3 consumer against the translating-sphere solution.

Run explicitly through the compute broker. Tests do not start Julia. The exact
installed pin is verified before submission; this exercises the production
builder, managed bridge, negotiation and typed result decoder, not a raw port.
Frozen budgets: 2% complex relative pressure/load, 1% velocity/current/impedance;
pressure nodes below 3% of exact on-axis amplitude; doubled voltage responses
within 1e-10 relative and radiation matrix unchanged within 1e-10 relative.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import hashlib
from pathlib import Path

import numpy as np

from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.transducers import ExteriorDriver, build_transducer_request
from server.solver.beat_runtime.manager import WorkerManager
from server.solver.official_beat import solve_transducer_compiled
from . import reference_lem_sphere as exact
from .reference_sphere import make_sphere_mesh
from .reference_support import NEGATIVE_TIME, expected_engine_revision
from .verification import verify_runtime


def validate_installed_provenance(provenance: dict, package: Path) -> None:
    """Installed wheels lack a Git checkout; require dispatched source hashes.

    verify_runtime has already matched every installed package byte to the clean
    exact pin. The worker must independently report those same numerical bytes.
    """
    hashes = provenance.get("engine", {}).get("source_files_sha256", {})
    required = {"julia_local/BeatEngineCompiledDriver.jl", "julia_local/exterior_lumped_network.jl",
                "julia_local/src/BeatEngineCoupled.jl"}
    if not isinstance(hashes, dict) or not required.issubset(hashes):
        raise ValueError("Worker omitted required numerical source provenance")
    for name, digest in hashes.items():
        path = (package / name).resolve()
        if not path.is_relative_to(package.resolve()) or not path.is_file():
            raise ValueError(f"Worker reported unknown source: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Dispatched numerical source differs from verified pin: {name}")


def qualify(*, julia: str, engine_source: Path, output: Path, backend: str = "cpu") -> dict:
    facts = verify_runtime(julia, backend, engine_source=engine_source)
    if (facts["engine_revision"] != expected_engine_revision()
            or facts["engine_revision_source"] != "installed_source_byte_match"
            or facts["artifact_kind"] != "installed"):
        raise ValueError("Qualification requires independently byte-matched installed WG engine pin")
    output.mkdir(parents=True, exist_ok=True)
    mesh = output / "translating-sphere.msh"
    mesh_info = make_sphere_mesh(mesh, mesh_size_m=.016)
    frequencies = [400., 80., 40., 160.]
    precision = "float64" if backend == "cpu" else "float32"
    layout = build_observations(angle_range=(0, 180, 5), sphere_grid=None,
                                origin_m=exact.CENTRE_M, distance_m=1., precision=precision)
    frame = {"origin": exact.CENTRE_M.tolist(), "axis": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0]}
    driver = ExteriorDriver("sphere", (1,), [0, 0, 1],
                            **{k: v for k, v in exact.DRIVER_PARAMETERS.items()
                               if k not in {"motion_axis", "motion_profile"}})
    request = build_transducer_request(mesh.read_text(), drivers=[driver], layout=layout,
                                       frame=frame, frequencies_hz=frequencies,
                                       density_kg_per_m3=exact.DENSITY,
                                       sound_speed_m_per_s=exact.SOUND_SPEED, backend=backend, precision=precision)
    (output / "request.json").write_text(json.dumps(request.wire, indent=2) + "\n")
    manager = WorkerManager(mode="child")
    try:
        first = solve_transducer_compiled(request, worker_manager=manager, julia_executable=julia)
        doubled_wire = dict(request.wire, solver_options=dict(request.wire["solver_options"],
                                                             transducer_reference_voltage_v=5.66))
        doubled = solve_transducer_compiled(replace(request, wire=doubled_wire), worker_manager=manager,
                                            julia_executable=julia)
    finally:
        manager.shutdown()
    assert first.frequencies_hz.tolist() == frequencies and doubled.frequencies_hz.tolist() == frequencies
    errors = []
    for row, twice in zip(first.rows, doubled.rows):
        frequency = row.frequency_hz
        u, current, zin = exact.analytic_driver(frequency, NEGATIVE_TIME)
        zrad = exact.analytic_radiation_impedance(frequency, NEGATIVE_TIME)
        values = {"velocity": abs(row.velocity_rms_m_per_s[0, 0] / u - 1),
                  "current": abs(row.current_rms_a[0, 0] / current - 1),
                  "input_impedance": abs(row.input_impedance_ohm(driver.port_id) / zin - 1),
                  "radiation_load": abs(row.radiation.values[0, 0] / zrad - 1)}
        for name, points in layout.points_m.items():
            displacement = points - exact.CENTRE_M
            radius = np.linalg.norm(displacement, axis=1)
            mu = displacement[:, 2] / radius
            expected = exact.analytic_pressure(frequency, radius, mu, NEGATIVE_TIME, velocity=u)
            active = np.abs(mu) > (1e-6 if precision == "float32" else 1e-10)
            values[f"pressure:{name}"] = float(np.max(np.abs(row.pressure_rms_pa[name][0, active] / expected[active] - 1)))
            node = np.max(np.abs(row.pressure_rms_pa[name][0, ~active]))
            assert node < .03 * np.max(np.abs(expected))
            np.testing.assert_allclose(twice.pressure_rms_pa[name], 2 * row.pressure_rms_pa[name], rtol=1e-10, atol=1e-14)
        for name, error in values.items():
            assert error < (.02 if name == "radiation_load" or name.startswith("pressure:") else .01), (frequency, name, error)
        np.testing.assert_allclose(twice.velocity_rms_m_per_s, 2 * row.velocity_rms_m_per_s, rtol=1e-10)
        np.testing.assert_allclose(twice.current_rms_a, 2 * row.current_rms_a, rtol=1e-10)
        np.testing.assert_allclose(twice.radiation.values, row.radiation.values, rtol=1e-10)
        np.testing.assert_allclose(twice.input_impedance_ohm(driver.port_id), row.input_impedance_ohm(driver.port_id), rtol=1e-10)
        np.testing.assert_allclose(row.excursion_peak_mm[0, 0], np.sqrt(2) * 1000 * abs(u / (-2j * np.pi * frequency)), rtol=.01)
        assert row.radiation.metadata["effective_volume_area_zero_or_near_cancelling"] == [True]
        provenance = row.diagnostics.get("engine_provenance", {})
        validate_installed_provenance(provenance, Path(facts["engine_path"]))
        errors.append({"frequency_hz": frequency, **{k: float(v) for k, v in values.items()},
                       "matrix_metadata": row.radiation.metadata})
    report = {"passed": True, "scope": f"installed pin; explicit WG v3 {backend} {precision}, symmetry off",
              "observations": facts, "mesh": mesh_info, "errors": errors,
              "voltage_scaling": "2.83 -> 5.66 V RMS: linear pressure/current/velocity, invariant Zrad/Zin",
              "engine_provenance": first.rows[0].diagnostics["engine_provenance"]}
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("cpu", "metal"), default="cpu")
    parser.add_argument("--julia", required=True)
    parser.add_argument("--engine-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = qualify(julia=args.julia, engine_source=args.engine_source, output=args.output, backend=args.backend)
    print(json.dumps({"passed": report["passed"], "frequency_count": len(report["errors"]),
                      "report": str(args.output / "qualification.json")}))


if __name__ == "__main__":
    main()
