"""Broker-only CPU/Metal qualification of the opt-in WG CAD job facade."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from server.jobs.models import SolveRequest
from server.jobs.runtime import _extend_provisional_results
from server.solver.beat_runtime.manager import WorkerManager
from server.solver.beat_transducer_imported import solve
from .reference_exterior_transducer import validate_installed_provenance
from . import reference_lem_sphere as exact
from .reference_sphere import make_sphere_mesh
from .reference_support import NEGATIVE_TIME
from .verification import verify_runtime


def complex_array(value):
    if isinstance(value, list):
        return np.asarray([complex_array(v) for v in value])
    return complex(value["real"], value["imaginary"])


def qualify(*, julia: str, engine_source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    mesh = output / "job-sphere.msh"
    mesh_info = make_sphere_mesh(mesh, mesh_size_m=0.016)
    parameters = {
        k: v
        for k, v in exact.DRIVER_PARAMETERS.items()
        if k not in {"motion_profile", "motion_axis"}
    }
    parameters.update(version=1, motion_axis=[0.0, 0.0, 1.0])
    record = {
        "source_tags": {"sphere": 1},
        "symmetry": {"cut_planes": []},
        "anchor": {
            "throat_frame": {
                "origin_m": exact.CENTRE_M.tolist(),
                "axis": [0, 0, 1],
                "u": [1, 0, 0],
                "v": [0, 1, 0],
                "mouth_center_m": exact.CENTRE_M.tolist(),
                "source_center_m": exact.CENTRE_M.tolist(),
            }
        },
    }
    payload = dict(
        geometry=dict(
            type="imported",
            ingest_id="wgi_" + "0" * 26,
            manifest_sha256="sha256:" + "1" * 64,
            artifact_sha256="sha256:" + "2" * 64,
            drive_channels=[
                dict(
                    id="sphere",
                    source_ids=["sphere"],
                    motion="axial",
                    exterior_transducer=parameters,
                )
            ],
            mesh=dict(rigid_size_mm=16.0, transition_mm=20.0, source_size_mm={"sphere": 16.0}),
        ),
        options=dict(
            engine="beat-cpu",
            frequencies_hz=[40.0, 160.0, 400.0],
            polar_config=dict(
                angle_range=[0, 180, 5],
                enabled_axes=["horizontal"],
                distance=1.0,
                spherical_sampling=True,
                spherical_theta_count=5,
                spherical_phi_count=8,
                field_plane=False,
            ),
        ),
    )
    manager, responses = WorkerManager(mode="child"), {}
    try:
        for backend in ["cpu", "metal"]:
            facts = verify_runtime(julia, backend, engine_source=engine_source)
            payload["options"]["engine"] = "beat-" + backend
            live, revisions = {}, []

            def partial(index, delta):
                revisions.append(index)
                _extend_provisional_results(live, delta)

            result = solve(
                mesh.read_text(),
                SolveRequest.model_validate(payload),
                record,
                backend=backend,
                worker_manager=manager,
                julia_executable=julia,
                result_callback=partial,
            )
            assert revisions == [0, 1, 2]
            assert live["frequencies"] == result["frequencies"] == [40.0, 160.0, 400.0]
            assert live["channels"]["sphere"]["frequencies"] == [40.0, 160.0, 400.0]
            for diagnostics in result["metadata"]["native_diagnostics"]:
                validate_installed_provenance(
                    diagnostics["engine_provenance"], Path(facts["engine_path"])
                )
            channel = result["channels"]["sphere"]
            trace = channel["metadata"]["beat_transducer"]
            velocity = complex_array(trace["velocity_rms_m_per_s"])[:, 0]
            current = complex_array(trace["current_rms_a"])[:, 0]
            for index, f in enumerate(result["frequencies"]):
                # Match the WG facade's declared air density exactly.
                w, d = 2 * np.pi * f, exact.DRIVER_PARAMETERS
                ze = d["re_ohm"] - 1j * w * d["le_h"]
                zm = d["rms_n_s_per_m"] - 1j * w * d["mmd_kg"] + 1 / (-1j * w * d["cms_m_per_n"])
                zrad = exact.analytic_radiation_impedance(f, NEGATIVE_TIME) * (
                    1.2041 / exact.DENSITY
                )
                u = d["bl_n_per_a"] * 2.83 / (ze * (zm + zrad) + d["bl_n_per_a"] ** 2)
                i = (2.83 - d["bl_n_per_a"] * u) / ze
                zin = 2.83 / i
                assert abs(velocity[index] / u - 1) < 0.01
                assert abs(current[index] / i - 1) < 0.01
                z = (
                    channel["impedance"]["real"][index]
                    + 1j * channel["impedance"]["imaginary"][index]
                )
                assert abs(z / np.conj(zin) - 1) < 0.01
            assert channel["metadata"]["phase_time_convention"] == "exp(+ikr)"
            assert channel["metadata"]["impedance_units"] == "ohms"
            responses[backend] = result
            (output / f"{backend}-result.json").write_text(json.dumps(result, indent=2) + "\n")
    finally:
        manager.shutdown()
    errors = {}
    for quantity in ["velocity_rms_m_per_s", "current_rms_a"]:
        values = [
            complex_array(
                responses[b]["channels"]["sphere"]["metadata"]["beat_transducer"][quantity]
            )
            for b in ["cpu", "metal"]
        ]
        error = float(np.max(np.abs(values[1] / values[0] - 1)))
        assert error < 0.001, (quantity, error)
        errors[quantity] = error
    values = [
        complex_array(responses[b]["metadata"]["radiation_impedance_matrix"]["values"])
        for b in ["cpu", "metal"]
    ]
    errors["matrix"] = float(np.max(np.abs(values[1] / values[0] - 1)))
    assert errors["matrix"] < 0.001
    for quantity in ["spl", "phase_degrees"]:
        values = [
            np.asarray(responses[b]["channels"]["sphere"]["spl_on_axis"][quantity])
            for b in ["cpu", "metal"]
        ]
        error = float(np.max(np.abs(values[1] - values[0])))
        assert error < 0.01, (quantity, error)
        errors[quantity] = error
    report = dict(
        passed=True,
        scope="official installed pin; complete exterior sphere WG job/live/result facade; CPU Float64 + Metal Float32",
        mesh=mesh_info,
        backend_drift=errors,
        budgets=dict(network_relative=0.001, spl_db=0.01, phase_deg=0.01),
    )
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--julia", required=True)
    parser.add_argument("--engine-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    qualify(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
