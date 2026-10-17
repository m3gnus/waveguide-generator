"""V8 backend precision and V10 exterior replay through the F11 live pipeline.

Opt in through the compute broker exactly like F11: WG2_RUN_LIVE=1,
WG2_FEM_REGIONS=1, WG2_BEAT_PROVIDER=official and a provisioned
WG2_BEAT_RUNTIME_DIR. V8 Metal needs macOS and a proved Metal runtime and skips
otherwise. WG2_FEM_VALIDATION_DIR optionally receives JSON evidence.
"""
from __future__ import annotations

import base64
import os
import sys

import numpy as np
import pytest

from server.contracts.conventions import engineering_to_solver
from server.tests.fem_validation import comparison_evidence, helmholtz_frequency, write_evidence
from server.tests.fem_validation import C, CHAMBER_VOLUME_M3, PORT_LENGTH_M, PORT_RADIUS_M
from server.tests import test_fem_validation_pipeline_live as f11
from server.tests.test_fem_validation_pipeline_live import CPU_FLOAT64, _pipeline_runs

_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1; run V8/V10 through the compute broker")]

managed_cpu = f11.managed_cpu  # F11's managed CPU child fixture, reused unchanged.
METAL_FLOAT32 = ("beat-metal", "metal", "float32")
PLANES = ("horizontal", "vertical", "diagonal")
V8_TOLERANCE = 1e-2  # Accepted Float32 high-Q drift is about 6e-3.
V10_TOLERANCE = 1e-6


@pytest.fixture
def managed_cpu_metal(monkeypatch):
    """One child manager owning both CPU and Metal; skip without proved Metal."""
    if sys.platform != "darwin":
        pytest.skip("BEAT Metal requires macOS")
    from server.solver import beat_field, official_beat
    from server.solver.beat_runtime import readiness
    from server.solver.beat_runtime.manager import ManagedWorker, WorkerManager

    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    status = readiness.backend_status("cpu")
    assert status["available"], status["reason"]
    metal = readiness.backend_status("metal")
    if not metal["available"]:
        pytest.skip(f"BEAT Metal runtime unavailable ({metal['state']}): {metal['reason']}")
    manager = WorkerManager(mode="child")
    monkeypatch.setattr(official_beat, "get_manager", lambda: manager)
    monkeypatch.setattr(beat_field, "get_manager", lambda: manager)
    try:
        assert isinstance(manager.get_worker("cpu"), ManagedWorker)
        assert isinstance(manager.get_worker("metal"), ManagedWorker)
        yield
    finally:
        manager.shutdown()


@pytest.fixture
def managed_cpu_field(managed_cpu, monkeypatch):
    """Route BEAT field evaluation to F11's managed child, never a detached host."""
    from server.solver import beat_field, official_beat

    monkeypatch.setattr(beat_field, "get_manager", official_beat.get_manager)


def polar_pressure(channel, plane):
    """Complex solver-convention polar pressure (frequency x angle) from the durable envelope.

    Each directivity row is normalized at 0 degrees; plane_reference_spl_db
    restores that plane's absolute level (20 uPa), and directivity_phase is
    the raw wrapped phase of the same exp(-i omega t) samples. All float64.
    """
    level = np.asarray(channel["metadata"]["spl_on_axis"]["plane_reference_spl_db"][plane], dtype=float)
    rows = np.asarray(channel["directivity"][plane], dtype=float)
    phases = np.asarray(channel["directivity_phase"][plane], dtype=float)
    np.testing.assert_array_equal(rows[..., 0], phases[..., 0])
    magnitude = 20e-6 * 10 ** ((level[:, None] + rows[..., 1]) / 20)
    return magnitude * np.exp(1j * np.deg2rad(phases[..., 1]))


def max_relative(computed, reference):
    """Infinity-norm relative error per frequency row, then the worst row."""
    computed, reference = np.asarray(computed), np.asarray(reference)
    axes = tuple(range(1, reference.ndim))
    return float(np.max(np.max(np.abs(computed - reference), axis=axes)
                        / np.max(np.abs(reference), axis=axes)))


def _v8(case, frequencies, tmp_path, runs):
    record, ((cpu_env, cpu_field, cpu_details, _), (gpu_env, gpu_field, gpu_details, _)) = _pipeline_runs(
        tmp_path, case, frequencies, runs)
    assert cpu_details["job_id"] != gpu_details["job_id"]
    # One ingest: both jobs solved the identical retained FEM mesh.
    for name in ("points_mm_0", "tetrahedra_0"):
        np.testing.assert_array_equal(cpu_field[name], gpu_field[name])
    nodal_cpu, nodal_gpu = cpu_field["pressure"][:, 0], gpu_field["pressure"][:, 0]
    # Nodal pressure is normalized per frequency by max |p_cpu|: near a port
    # outlet |p| is far below the chamber level, where pointwise ratios only
    # amplify Float32 absolute noise. Pointwise values are recorded too.
    nodal_error = max_relative(nodal_gpu, nodal_cpu)
    checks = {"fem_nodal_pressure": {
        "max_relative_error": nodal_error, "tolerance": V8_TOLERANCE, "passed": nodal_error <= V8_TOLERANCE,
        "normalization": "per-frequency max |p_cpu| over all nodes",
        "max_pointwise_relative_error": float(np.max(np.abs(nodal_gpu - nodal_cpu) / np.abs(nodal_cpu)))}}
    if case in {"open", "ported"}:
        # Polar points at 2 m have no pressure nulls here (compact monopole
        # radiation), so the exterior gate is pointwise, per complex sample.
        cpu_channel, gpu_channel = (env["channels"]["drive-lf"] for env in (cpu_env, gpu_env))
        computed = np.stack([polar_pressure(gpu_channel, plane) for plane in PLANES], axis=1)
        reference = np.stack([polar_pressure(cpu_channel, plane) for plane in PLANES], axis=1)
        quantity = "exterior_polar_pressure_metal_float32_vs_cpu_float64"
    else:
        # Interior-only: the sealed field is uniform and compact; gate every node.
        computed, reference = nodal_gpu, nodal_cpu
        quantity = "fem_nodal_pressure_rerouted_vs_cpu_float64"
    errors = np.abs(computed - reference) / np.abs(reference)
    executions = {label: {key: env["metadata"]["fem"].get(key)
                          for key in ("engine", "backend", "precision", "execution_reroute")}
                  for label, env in (("cpu", cpu_env), ("second", gpu_env))}
    write_evidence(f"v8-{case}", frequencies, computed.ravel(), reference.ravel(), V8_TOLERANCE, record,
                   quantity=quantity, value_shape=list(computed.shape),
                   planes=list(PLANES) if case in {"open", "ported"} else None,
                   measured_max_error=max(float(errors.max()), nodal_error),
                   checks=checks, executions=executions, cpu_details=cpu_details,
                   second_details=gpu_details,
                   cpu_solver_engine=cpu_env["metadata"]["solver_engine"],
                   second_solver_engine=gpu_env["metadata"]["solver_engine"])
    assert errors.max() <= V8_TOLERANCE, f"V8 {case} {quantity}: max relative error {errors.max():.3g}"
    assert checks["fem_nodal_pressure"]["passed"], f"V8 {case}: {checks['fem_nodal_pressure']}"
    return cpu_env, gpu_env


@pytest.mark.parametrize("case", ["open", "ported"])
def test_v8_coupled_metal_float32_matches_cpu_float64(managed_cpu_metal, tmp_path, case):
    if case == "ported":
        tuning = helmholtz_frequency(C, np.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3,
                                     PORT_LENGTH_M, .85 * PORT_RADIUS_M, .61 * PORT_RADIUS_M)
        # Sweep 6% either side of the lumped tuning in 1% steps, so the true
        # resonance -- the high-Q worst case for Float32 -- is sampled within 1%.
        frequencies = (tuning * np.linspace(.94, 1.06, 13)).tolist()
    else:
        frequencies = [100.]
    cpu_env, gpu_env = _v8(case, frequencies, tmp_path, (("beat-cpu", CPU_FLOAT64),
                                                         ("beat-metal", METAL_FLOAT32)))
    assert "execution_reroute" not in cpu_env["metadata"]["fem"]
    assert "execution_reroute" not in gpu_env["metadata"]["fem"]
    assert cpu_env["metadata"]["solver_engine"]["precision"] == "double"
    assert gpu_env["metadata"]["solver_engine"]["precision"] == "single"
    assert gpu_env["metadata"]["solver_engine"]["device"] == "metal"
    assert cpu_env["metadata"]["solver_engine"]["device"] == "cpu"


def test_v8_sealed_metal_request_reroutes_to_cpu_float64(managed_cpu, tmp_path):
    # Interior-only FEM always runs on CPU float64 (no BEM, so no Float32
    # path exists). A Metal request must record that reroute, not run Metal.
    _, rerouted = _v8("sealed", [100.], tmp_path, (("beat-cpu", CPU_FLOAT64), ("beat-metal", CPU_FLOAT64)))
    reroute = rerouted["metadata"]["fem"]["execution_reroute"]
    assert reroute["requested_engine"] == "beat-metal"
    assert reroute["requested_backend"] == "metal"
    assert (reroute["backend"], reroute["precision"]) == ("cpu", "float64")
    assert reroute["reason"] == "Interior-only FEM uses CPU float64"
    # The second run is CPU against CPU, so its accuracy check proves only the reroute.
    assert rerouted["metadata"]["solver_engine"]["device"] == "cpu"


def _plane_request(origin, axis_u, axis_v, size_m, count, scope, frequency):
    from server.jobs.api import FemPlaneRequest

    return FemPlaneRequest.model_validate({
        "plane": {"origin_m": [float(x) for x in origin], "axis_u": [float(x) for x in axis_u],
                  "axis_v": [float(x) for x in axis_v], "width_m": size_m, "height_m": size_m,
                  "nx": count, "ny": count},
        "frequency": frequency, "channel": "drive-lf", "scope": scope, "quantity": "pressure"})


def _decoded(payload):
    pressure = (np.frombuffer(base64.b64decode(payload["pressure_real"]), "<f4").astype(float)
                + 1j * np.frombuffer(base64.b64decode(payload["pressure_imag"]), "<f4"))
    valid = np.frombuffer(base64.b64decode(payload["valid_mask"]), "u1").astype(bool)
    region = np.frombuffer(base64.b64decode(payload["region_class"]), "u1")
    return pressure, valid, region


def _replay(store, job_id, envelope, field):
    """Evaluate exterior and combined planes through the production coupled route."""
    from server.jobs.coupled_field import evaluate_coupled_plane
    from server.solver.field_plane import field_plane_points

    row = store.get_job_row(job_id)
    assert row["task_metadata"].get("has_bem_traces_artifact"), row["task_metadata"]
    frequency = envelope["frequencies"][0]
    channel = envelope["channels"]["drive-lf"]
    basis = channel["metadata"]["observation_frame_basis"]
    ports = channel["metadata"]["fem_ports"]
    assert ports["observation_angles_deg"] == [-180., -90., 0., 90., 180.]
    origin, axis, u, v = (np.asarray(basis[key], dtype=float) for key in ("origin_m", "axis", "u", "v"))
    inclination = np.pi * 45. / 180.  # build_observations' default diagonal inclination
    transverse = {"horizontal": u, "vertical": v,
                  "diagonal": np.cos(inclination) * u + np.sin(inclination) * v}
    distance = 2.
    exterior = {}
    for plane in PLANES:
        # A 3 x 3 grid of side 2r centred on the polar origin: its four edge
        # midpoints are exactly the -180/-90/0/90/180 degree observation points.
        request = _plane_request(origin, transverse[plane], axis, 2 * distance, 3, "exterior", frequency)
        grid = field_plane_points(request.plane)
        observed = np.asarray(ports["observation_points_m"][plane], dtype=float)
        gaps = np.linalg.norm(observed[:, None] - grid[None], axis=2)
        nearest = gaps.argmin(axis=1)
        assert gaps[np.arange(len(observed)), nearest].max() < 1e-9, gaps.min(axis=1)
        payload = evaluate_coupled_plane(store, job_id, request)
        assert payload["phasor_convention"] == "exp(-i omega t)"
        pressure, valid, region = _decoded(payload)
        assert valid[nearest].all() and (region[nearest] == 2).all()
        exterior[plane] = {"replay": pressure[nearest], "reference": polar_pressure(channel, plane)[0],
                           "ports_total": engineering_to_solver(np.frombuffer(base64.b64decode(
                               ports["total_radiated_pressure"][plane]["content_base64"]), "<c16"
                           ).reshape(ports["total_radiated_pressure"][plane]["shape"])[0]),
                           "grid_points_m": grid[nearest], "observation_points_m": observed}

    # Combined plane centred exactly on the deepest interior FEM node, as the
    # route sees it (Float32 mm, then metres), large enough to leave the box.
    points_mm = field["points_mm_0"]
    on_surface = np.unique(np.concatenate([field[f"surface_triangles_0_{i}"].ravel()
                                           for i in range(len(field["surface_names_0"]))]))
    depth = np.minimum(points_mm - points_mm.min(axis=0), points_mm.max(axis=0) - points_mm).min(axis=1)
    depth[on_surface] = -np.inf
    node = int(np.argmax(depth))
    assert depth[node] > 0
    centre = points_mm[node].astype("<f4").astype(float) / 1000
    request = _plane_request(centre, [1., 0., 0.], [0., 1., 0.], .2, 9, "combined", frequency)
    grid = field_plane_points(request.plane)
    middle = 4 * 9 + 4
    np.testing.assert_array_equal(grid[middle], centre)
    pressure, valid, region = _decoded(evaluate_coupled_plane(store, job_id, request))
    assert valid[middle] and region[middle] == 1
    assert (valid & (region == 2)).any(), "Combined plane should also contain exterior samples"
    interior = {"replay": pressure[middle], "reference": field["pressure"][0, 0, node],
                "node": node, "node_mm": points_mm[node].tolist(), "node_depth_mm": float(depth[node])}
    return exterior, interior


def test_v10_open_box_exterior_plane_replays_polar_pressure(managed_cpu_field, tmp_path):
    # 100 Hz only: Float32 staging of 2 m points (|dx| <= ~2e-7 m) bounds
    # the phase mismatch by k|dx| ~ 4e-7 here, inside the 1e-6 gate.
    frequencies = [100.]
    record, ((envelope, _, details, (exterior, interior)),) = _pipeline_runs(
        tmp_path, "open", frequencies, inspect=_replay)
    replay = np.concatenate([exterior[p]["replay"] for p in PLANES])
    reference = np.concatenate([exterior[p]["reference"] for p in PLANES])
    ports_total = np.concatenate([exterior[p]["ports_total"] for p in PLANES])
    grid = np.concatenate([exterior[p]["grid_points_m"] for p in PLANES])
    rounding = float(np.abs(grid.astype("<f4").astype(float) - grid).max())
    checks = {"interior_node": comparison_evidence([interior["replay"]], [interior["reference"]], V10_TOLERANCE)}
    write_evidence("v10-open", frequencies, replay, reference, V10_TOLERANCE, record,
                   quantity="exterior_pressure_pa_per_m_s2_solver_convention", planes=list(PLANES),
                   angles_deg=envelope["channels"]["drive-lf"]["metadata"]["fem_ports"]["observation_angles_deg"],
                   observation_points_m=np.concatenate([exterior[p]["observation_points_m"] for p in PLANES]).tolist(),
                   measured_max_error=float(np.max(np.abs(replay - reference) / np.abs(reference))),
                   float32_point_rounding_m=rounding,
                   float32_phase_bound_rad=2 * np.pi * frequencies[0] / C * rounding * np.sqrt(3),
                   informational_ports_total_vs_polar=comparison_evidence(ports_total, reference, V10_TOLERANCE),
                   interior_node=interior["node"], interior_node_mm=interior["node_mm"],
                   interior_node_depth_mm=interior["node_depth_mm"], checks=checks, **details)
    np.testing.assert_allclose(replay, reference, rtol=V10_TOLERANCE, atol=0,
                               err_msg="V10: exterior plane replay differs from the solver's polar pressure")
    np.testing.assert_allclose(interior["replay"], interior["reference"], rtol=V10_TOLERANCE, atol=0,
                               err_msg="V10: combined-plane FEM sample differs from the coincident nodal pressure")
