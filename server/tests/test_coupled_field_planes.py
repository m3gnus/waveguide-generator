"""Coupled trace retention and analytic plane replay without a native solve."""
import asyncio
import base64
import json

from fastapi import FastAPI
import numpy as np
import pytest

from server.jobs.api import create_jobs_router, FemPlaneRequest
from server.jobs.bem_traces import check_trace_size, trace_arrays
from server.jobs.coupled_field import evaluate_coupled_plane, sample_interior, inside_bodies
from server.jobs.fem_field import FemFieldCorrupt
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.solver.beat_adapter.mesh import SurfaceMesh
from server.solver.beat_adapter.results import map_coupled_sweep
from server.tests.test_fem_engine_route import compiled, events
from server.tests.test_jobs_api import _request_with_headers
from server.tests.test_jobs_store import _job
from server.tests.test_field_plane_beat import runtime as runtime_fixture  # fake official field worker


runtime = runtime_fixture

def decode(payload, key, dtype="<f4"):
    return np.frombuffer(base64.b64decode(payload[key]), dtype=dtype)


def plane_request(scope="combined", **options):
    return FemPlaneRequest.model_validate({"plane": {"origin_m": [0.015, 0.003, 0.002],
        "axis_u": [1., 0., 0.], "axis_v": [0., 1., 0.], "width_m": .03, "height_m": .004,
        "nx": 7, "ny": 3}, "scope": scope, **options})


@pytest.fixture
def stored(tmp_path):
    request = compiled()
    sweep = map_coupled_sweep(events(request), request)
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("field", "complete"))
    region = request.regions[0].domain
    arrays = {"schema": np.array("wg-fem-field-store-v2"), "phasor_convention": np.array("exp(-i omega t)"),
        "drive_normalization": np.array(["unit_acceleration", "unit_acceleration"]),
        "frequencies_hz": next(iter(sweep.channels.values())).frequencies_hz,
        "channel_ids": np.array(list(request.channel_ports)), "region_ids": np.array([region.region_id]),
        "points_mm_0": region.points_mm, "tetrahedra_0": region.tetrahedra,
        "surface_names_0": np.array([], dtype="U1"), "pressure": sweep.nodal_pressure,
        "density_kg_per_m3": np.array(1.21)}
    store.store_fem_field_arrays("field", arrays)
    traces = trace_arrays(request, sweep)
    store.store_bem_trace_arrays("field", traces)
    yield store, request, sweep, arrays, traces
    store.close()


@pytest.mark.parametrize("dtype", ["complex64", "complex128"])
@pytest.mark.parametrize("fortran", [False, True])
def test_sidecar_roundtrip_preserves_axes_mesh_and_complex_precision(stored, dtype, fortran, monkeypatch):
    store, request, sweep, _, arrays = stored
    arrays = {**arrays, "pressure": arrays["pressure"].astype(dtype), "neumann": arrays["neumann"].astype(dtype)}
    if fortran:
        arrays["pressure"] = np.asfortranarray(arrays["pressure"])
        arrays["neumann"] = np.asfortranarray(arrays["neumann"])
    original = np.savez_compressed
    def compress(*args, **kwargs):
        assert not store._lock._is_owned()
        return original(*args, **kwargs)
    monkeypatch.setattr(np, "savez_compressed", compress)
    assert store.store_bem_trace_arrays("field", arrays)
    original_get = np.lib.npyio.NpzFile.__getitem__
    def subset(self, key):
        assert key not in {"pressure", "neumann"}
        return original_get(self, key)
    monkeypatch.setattr(np.lib.npyio.NpzFile, "__getitem__", subset)
    mesh, p, q, speed, density, mode, rotation = store.load_bem_traces("field", 100., "fem")
    assert p.dtype == q.dtype == np.dtype(dtype)
    np.testing.assert_array_equal(p, arrays["pressure"][1, 1])
    np.testing.assert_array_equal(q, arrays["neumann"][1, 1])
    np.testing.assert_array_equal(mesh.faces, arrays["triangles"])
    assert (speed, density, mode) == (343, 1.21, "off")
    assert store.load_fem_field("field")["plane_scopes"] == ["interior", "exterior", "combined"]


def test_trace_size_is_refused_before_compression(stored, monkeypatch):
    store, _, _, _, arrays = stored
    with pytest.raises(ValueError, match="256 MiB"):
        check_trace_size(1000, 10, 100000, 200000, "float32")
    monkeypatch.setattr("server.jobs.bem_traces.MAX_BEM_TRACE_BYTES", 100)
    monkeypatch.setattr(np, "savez_compressed", lambda *a, **k: pytest.fail("oversized compression"))
    with pytest.raises(ValueError, match="256 MiB"):
        store.store_bem_trace_arrays("field", arrays)


@pytest.mark.parametrize("action", ["delete_field", "delete_job", "recovery", "retention"])
def test_trace_lifecycle_removes_both_sidecars(stored, action):
    store, _, _, _, _ = stored
    path = store._bem_traces_path("field")
    if action == "delete_field":
        store.delete_fem_field("field")
    elif action == "delete_job":
        store.delete_job_with_event("field")
    elif action == "recovery":
        store.update_job("field", status="running")
        store.recover_on_startup("restart")
    else:
        store.prune_terminal_jobs(retention_days=30)
    assert not path.exists()
    assert not store._fem_field_path("field").exists()
    assert store.load_bem_traces("field", 100., "fem") is None


def test_deleted_publication_does_not_resurrect_bem_sidecar(stored, monkeypatch):
    store, _, _, _, arrays = stored
    original = np.savez_compressed
    def compress(*args, **kwargs):
        original(*args, **kwargs)
        store.delete_job_with_event("field")
    monkeypatch.setattr(np, "savez_compressed", compress)
    assert not store.store_bem_trace_arrays("field", arrays)
    assert not store._bem_traces_path("field").exists()


@pytest.mark.parametrize("backend,precision", [("cpu", "float64"), ("metal", "float32")])
def test_trace_outputs_validate_node_face_metadata_and_scaling(backend, precision):
    request = compiled(backend=backend, precision=precision)
    sweep = map_coupled_sweep(events(request), request)
    assert sweep.bem_pressure.dtype == sweep.bem_neumann.dtype == np.dtype("complex64" if precision == "float32" else "complex128")
    np.testing.assert_allclose(sweep.bem_pressure[:, :, 0], sweep.nodal_pressure[:, :, 0], rtol=2e-7)
    np.testing.assert_allclose(sweep.bem_neumann[:, :, 0], sweep.nodal_pressure[:, :, 0], rtol=2e-7)
    from server.tests.beat_runtime.fake_host_worker import coupled_result
    from server.solver.beat_adapter.results import ResultContractError
    result = coupled_result(request.wire, request.wire["frequencies_hz"][0])
    trace = next(q for q in result["quantities"] if q["quantity"] == "bem_boundary_neumann")
    trace["metadata"]["face_offsets"] = [1]
    with pytest.raises(ResultContractError, match="BEM result layout"):
        map_coupled_sweep(iter([{"type": "result", "result": result}]), request)


def test_combined_priority_and_solid_mask_with_known_field(stored):
    store, _, sweep, _, traces = stored
    # Add a closed solid away from the bounded FEM air.
    traces = dict(traces)
    xyz = traces["points_m"]
    traces["points_m"] = np.concatenate([xyz, xyz + [.02, 0, 0]])
    traces["triangles"] = np.concatenate([traces["triangles"], traces["triangles"] + len(xyz)])
    traces["pressure"] = np.concatenate([traces["pressure"]] * 2, axis=2)
    traces["neumann"] = np.concatenate([traces["neumann"]] * 2, axis=2)
    store.store_bem_trace_arrays("field", traces)
    calls = []
    def known(mesh, f, k, p, q, points, **kwargs):
        calls.append(points.copy())
        assert np.all(points.sum(axis=1) > .01 - 1e-9)
        assert not np.any((points[:, 0] >= .02) & (points.sum(axis=1) < .03 - 1e-9))
        return points[:, 0] + 2j * points[:, 1]
    result = evaluate_coupled_plane(store, "field", plane_request(), evaluator=known)
    points = decode(result, "points_mm").reshape(-1, 3) / 1000
    valid = decode(result, "valid_mask", "u1").astype(bool)
    values = decode(result, "pressure_real") + 1j * decode(result, "pressure_imag")
    interior = points.sum(axis=1) <= .0100001
    solid = (points[:, 0] >= .02) & (points.sum(axis=1) <= .0300001)
    assert interior.any() and solid.any() and (~interior & ~solid).any()
    assert valid[interior].all() and not valid[solid].any()
    np.testing.assert_allclose(values[interior], sweep.nodal_pressure[0, 0, 0], rtol=2e-7)
    np.testing.assert_allclose(values[valid & ~interior], points[valid & ~interior, 0] + 2j * points[valid & ~interior, 1])
    assert len(calls) == 1


def test_monopole_plane_over_official_fake_worker(stored, runtime):
    store, _, _, _, _ = stored
    def monopole(arrays, wire):
        r = np.linalg.norm(arrays["points_m"], axis=1)
        return np.exp(1j * 2 * np.pi * wire["frequency_hz"] / wire["sound_speed_m_per_s"] * r) / r
    runtime.field_function = monopole
    result = evaluate_coupled_plane(store, "field", plane_request("exterior"))
    valid = decode(result, "valid_mask", "u1").astype(bool)
    points = decode(result, "points_mm").reshape(-1, 3)[valid] / 1000
    r = np.linalg.norm(points, axis=1)
    expected = np.exp(1j * 2 * np.pi * result["frequency_hz"] / 343 * r) / r
    np.testing.assert_allclose((decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"))[valid], expected, rtol=2e-6)
    assert runtime.operations == runtime.negotiated == ["bem_field"]


def test_p1_plane_wave_velocity_and_mirrored_vector_sign():
    xyz = np.array([[0, 0, 0], [.001, 0, 0], [0, .001, 0], [0, 0, .001]])
    frequency, density, speed = 100., 1.21, 343.
    k = 2 * np.pi * frequency / speed
    pressure = np.exp(1j * k * xyz[:, 0])
    values, velocity, inside = sample_interior(xyz, [[0, 1, 2, 3]], pressure, [[.0002, .0001, .0001]], frequency, density)
    assert inside.all()
    np.testing.assert_allclose(velocity[0, 0], 1 / (density * speed), rtol=.001, atol=0)
    assert velocity[0, 1] == velocity[0, 2] == 0
    reflected = xyz * [-1, 1, 1]
    _, mirrored, _ = sample_interior(reflected, [[1, 0, 2, 3]], pressure, [[-.0002, .0001, .0001]], frequency, density)
    np.testing.assert_allclose(mirrored[0], velocity[0] * [-1, 1, 1], atol=1e-15)


@pytest.mark.parametrize("symmetry", ["x", "xy"])
def test_body_mask_completes_positive_domain_images(symmetry):
    xyz = np.array([[0, 0, 0], [.01, 0, 0], [0, .01, 0], [0, 0, .01]])
    # Remove the artificial cut closures: reflected outer faces close the skin.
    faces = np.array([[0, 2, 1], [1, 2, 3], [2, 0, 3]]) if symmetry == "x" else np.array([[0, 2, 1], [1, 2, 3]])
    mesh = SurfaceMesh(xyz, faces, np.ones(len(faces)), (), ())
    inside = inside_bodies(mesh, np.array([[.002, .002, .002], [-.002, .002, .002], [-.002, -.002, .002], [.02, .02, .02]]), symmetry)
    assert inside.tolist() == [True, True, symmetry == "xy", False]


def test_plane_route_selection_validation_and_old_sidecar_fallback(stored):
    store, _, _, _, _ = stored
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    def post(body, job="field"):
        return asyncio.run(_request_with_headers(app, "POST", f"/api/results/{job}/fem-field/plane", body=body))
    body = plane_request("interior", frequency=100., channel="fem").model_dump(mode="json")
    status, raw, headers = post(body)
    assert status == 200 and headers["cache-control"] == "no-store"
    assert json.loads(raw)["frequency_hz"] == 100
    for bad in ({"frequency": 0}, {"frequency": 50}, {"channel": "absent"}, {"scope": "bad"},
                {"plane": {**body["plane"], "nx": 257}}):
        assert post({**body, **bad})[0] == 422
    assert post(body, "absent")[0] == 404
    store._bem_traces_path("field").unlink()
    assert "plane_scopes" not in store.load_fem_field("field")
    assert post({**body, "scope": "combined"})[0] == 422
    assert post(body)[0] == 200


def test_corrupt_trace_archive_cannot_be_replayed(stored):
    store, _, _, _, arrays = stored
    store.store_bem_trace_arrays("field", {**arrays, "neumann": np.full(arrays["neumann"].shape, np.nan, complex)})
    with pytest.raises(FemFieldCorrupt):
        store.load_bem_traces("field", 100., "fem")


def test_exterior_plane_wave_normal_velocity_from_official_potential(stored, runtime):
    store, _, _, _, _ = stored
    runtime.field_function = lambda arrays, wire: np.exp(1j * 2 * np.pi * wire["frequency_hz"] / wire["sound_speed_m_per_s"] * arrays["points_m"][:, 2])
    result = evaluate_coupled_plane(store, "field", plane_request("exterior", quantity="velocity_normal"))
    valid = decode(result, "valid_mask", "u1").astype(bool)
    z = decode(result, "points_mm").reshape(-1, 3)[valid, 2] / 1000
    expected = np.exp(1j * 2 * np.pi * result["frequency_hz"] / 343 * z) / (1.21 * 343)
    np.testing.assert_allclose((decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"))[valid], expected, rtol=.001)
    assert result["unit"] == "m/s"
    assert runtime.operations == ["bem_field"]


def test_plane_permit_prioritizes_solves_and_releases_after_worker_timeout(stored, runtime):
    from server.jobs.coupled_field import evaluate_coupled_plane_async
    from server.solver.field_plane import FieldPlaneTimedOut
    from server.solver.metal_permit import FieldEvaluationBusy
    store, _, _, _, _ = stored
    async def run():
        coordinator = JobRuntime(store).field_plane_service
        solve = await coordinator.permit.acquire_solve()
        try:
            with pytest.raises(FieldEvaluationBusy):
                await evaluate_coupled_plane_async(store, "field", plane_request("exterior"), coordinator)
        finally:
            await solve.release()
        assert not runtime.operations
        coordinator.timeout_seconds = .03
        runtime.cancelled = True
        with pytest.raises(FieldPlaneTimedOut):
            await evaluate_coupled_plane_async(store, "field", plane_request("exterior"), coordinator)
        # The session monitor cancels only this operation, then its drain
        # releases the permit. Keep the event loop alive for that owned drain.
        for _ in range(100):
            if not coordinator.permit.field_running:
                break
            await asyncio.sleep(.01)
        assert not coordinator.permit.field_running
        assert not coordinator.permit.field_pending
    asyncio.run(run())
    assert runtime.operations == ["bem_field"]


def test_replay_rotates_y_only_image_frame_without_reordering_traces(stored):
    store, _, _, _, arrays = stored
    rotation = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]])
    store.store_bem_trace_arrays("field", {**arrays, "points_m": arrays["points_m"] @ rotation.T,
        "symmetry": np.array("x"), "image_rotation": rotation})
    calls = []
    def evaluate(mesh, frequency, k, p, q, points, **kwargs):
        assert kwargs["symmetry_plane"] == "yz"
        np.testing.assert_array_equal(p, arrays["pressure"][0, 0])
        calls.append(points.copy())
        return points[:, 0] - 1j * points[:, 1]
    result = evaluate_coupled_plane(store, "field", plane_request("exterior"), evaluator=evaluate)
    valid = decode(result, "valid_mask", "u1").astype(bool)
    points = decode(result, "points_mm").reshape(-1, 3)[valid] / 1000
    np.testing.assert_allclose(calls[0], points @ rotation.T, atol=1e-9)
    np.testing.assert_allclose((decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"))[valid], points[:, 1] + 1j * points[:, 0])


def test_combined_fake_worker_field_is_continuous_with_retained_polar_basis(stored, runtime):
    store, _, sweep, _, _ = stored
    result = evaluate_coupled_plane(store, "field", plane_request("combined"))
    valid = decode(result, "valid_mask", "u1").astype(bool)
    points = decode(result, "points_mm").reshape(-1, 3) / 1000
    assert (points[valid].sum(axis=1) < .01).any()
    assert (points[valid].sum(axis=1) > .01).any()
    expected = sweep.channels["exterior"].pressure_complex[0, 0, 0]
    np.testing.assert_allclose((decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"))[valid], expected, rtol=1e-6)
    assert runtime.operations == ["bem_field"]


def test_normal_velocity_never_evaluates_a_stencil_inside_a_body(stored):
    store, _, _, _, _ = stored
    request = plane_request("exterior", quantity="velocity_normal")
    request.plane.origin_m = (.001, .001, -.000005)
    request.plane.width_m = request.plane.height_m = .001
    def forbidden(*args, **kwargs):
        pytest.fail("A difference stencil crossed the closed boundary")
    result = evaluate_coupled_plane(store, "field", request, evaluator=forbidden)
    assert not decode(result, "valid_mask", "u1").any()


def test_feature_flag_revert_hides_planes_without_deleting_retained_data(stored, monkeypatch):
    store, _, _, _, _ = stored
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    body = plane_request("interior").model_dump(mode="json")
    def post():
        return asyncio.run(_request_with_headers(app, "POST", "/api/results/field/fem-field/plane", body=body))[0]
    monkeypatch.setenv("WG2_FEM_REGIONS", "0")
    assert post() == 404
    assert store._fem_field_path("field").is_file() and store._bem_traces_path("field").is_file()
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    assert post() == 200
