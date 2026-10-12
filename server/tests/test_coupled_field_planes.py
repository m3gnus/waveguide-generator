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
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3]]) if symmetry == "x" else np.array([[0, 2, 1], [1, 2, 3]])
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
    np.testing.assert_allclose((decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"))[valid], expected, rtol=.003)
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
        # Allow indexed setup to finish before timing out the held worker.
        coordinator.timeout_seconds = .5
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


def test_combined_fake_worker_routes_distinct_fem_and_exterior_fields(stored, runtime):
    # Routing/scaling/masking only. Physical continuity is checked by the live
    # V10 exterior-plane versus polar-point comparison (relative error <=1e-6).
    store, _, sweep, _, _ = stored
    runtime.field_function = lambda arrays, wire: 7 + arrays["points_m"][:, 0] + 3j * arrays["points_m"][:, 1]
    result = evaluate_coupled_plane(store, "field", plane_request("combined"))
    valid = decode(result, "valid_mask", "u1").astype(bool)
    region = decode(result, "region_class", "u1")
    points = decode(result, "points_mm").reshape(-1, 3) / 1000
    values = decode(result, "pressure_real") + 1j * decode(result, "pressure_imag")
    interior, exterior = valid & (region == 1), valid & (region == 2)
    assert interior.any() and exterior.any()
    np.testing.assert_allclose(values[interior], sweep.nodal_pressure[0, 0, 0], rtol=1e-6)
    np.testing.assert_allclose(values[exterior], 7 + points[exterior, 0] + 3j * points[exterior, 1], rtol=1e-6)
    assert not np.allclose(values[interior][0], values[exterior][0])
    assert not values[~valid].any()
    assert runtime.operations == ["bem_field"]


def test_normal_velocity_never_evaluates_a_stencil_inside_a_body(stored):
    store, _, _, _, _ = stored
    request = plane_request("exterior", quantity="velocity_normal")
    request.plane.origin_m = (.001, .001, -5e-9)
    request.plane.width_m = request.plane.height_m = .001
    def forbidden(*args, **kwargs):
        pytest.fail("A difference stencil crossed the closed boundary")
    result = evaluate_coupled_plane(store, "field", request, evaluator=forbidden)
    assert not decode(result, "valid_mask", "u1").any()
    assert (decode(result, "mask_reason", "u1") == 2).all()


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


@pytest.mark.parametrize("action", ["cancel", "replace"])
def test_bem_publication_requires_the_original_fem_marker(stored, monkeypatch, action):
    store, _, _, fem, traces = stored
    store._bem_traces_path("field").unlink()
    save = np.savez_compressed
    def compress(*args, **kwargs):
        save(*args, **kwargs)
        monkeypatch.setattr(np, "savez_compressed", save)
        if action == "cancel":
            store.delete_fem_field("field")
        else:
            store.store_fem_field_arrays("field", fem)
    monkeypatch.setattr(np, "savez_compressed", compress)
    assert not store.store_bem_trace_arrays("field", traces)
    assert not store._bem_traces_path("field").exists()


def test_corrupt_evaluator_artifact_returns_gone(stored, monkeypatch):
    from server.solver.field_traces_store import ArtifactCorrupt
    store, _, _, _, _ = stored
    def corrupt(*a, **k):
        raise ArtifactCorrupt("invalid runtime field artifact")
    monkeypatch.setattr("server.solver.beat_field.evaluate_exterior_from_traces", corrupt)
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    status, body, _ = asyncio.run(_request_with_headers(app, "POST", "/api/results/field/fem-field/plane",
        body=plane_request("exterior").model_dump(mode="json")))
    assert status == 410
    assert "invalid runtime field artifact" in json.loads(body)["detail"]


def test_indexed_192_plane_20k_skin_with_symmetry_scales_by_local_faces():
    from server.jobs.body_index import SkinRayIndex, skin_triangles
    # Half sphere: exactly 20k faces, no artificial x=0 closure.
    theta = np.linspace(0, np.pi / 2, 101)
    phi = np.arange(100) * 2 * np.pi / 100
    xyz = np.array([[np.cos(t), np.sin(t) * np.cos(p), np.sin(t) * np.sin(p)] for t in theta for p in phi])
    faces = []
    for j in range(100):
        for i in range(100):
            a, b = j * 100 + i, j * 100 + (i + 1) % 100
            faces.extend([[a, b, b + 100], [a, b + 100, a + 100]])
    mesh = SurfaceMesh(xyz, np.asarray(faces), np.ones(len(faces)), (), ())
    grid = np.linspace(-1.2, 1.2, 192)
    x, y = np.meshgrid(grid, grid)
    points = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, .123)])
    index = SkinRayIndex(skin_triangles(mesh, "x"), [.371, .529, .763])
    inside, _ = index.query(points)
    # Avoid machine-dependent wall-time assertions: fewer than 1% of the
    # all-pairs triangle tests at the exact reviewed production scale.
    assert index.pair_count < len(points) * len(faces) * 2 / 100
    radius = np.linalg.norm(points, axis=1)
    assert inside[radius < .99].all()
    assert not inside[radius > 1.01].any()


def test_subgrid_wall_blocks_cells_even_with_all_four_corners_exterior():
    from server.jobs.body_index import crossing_cells
    request = plane_request()
    request.plane.nx = request.plane.ny = 2
    # A 2mm slab lies entirely between x corners of the 30mm grid cell.
    corners = np.array([[.014, 0, 0], [.016, 0, 0], [.014, .01, 0], [.016, .01, 0],
                        [.014, 0, .01], [.016, 0, .01], [.014, .01, .01], [.016, .01, .01]])
    faces = np.array([[0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5]])
    from server.solver.field_plane import field_plane_points
    mesh = SurfaceMesh(corners, faces, np.ones(len(faces)), (), ())
    assert not inside_bodies(mesh, field_plane_points(request.plane), "off").any()
    assert crossing_cells(corners[faces], request.plane, np.eye(3)).tolist() == [True]


def test_float32_velocity_stencil_reuses_base_mask_and_resolves_remote_points(stored, monkeypatch):
    from server.jobs.body_index import SkinRayIndex
    store, _, _, _, _ = stored
    request = plane_request("exterior", quantity="velocity_normal", frequency=100.)
    request.plane.origin_m = (20., 30., 40.)
    calls, separations = [], []
    original = SkinRayIndex.query
    def query(self, points, check=lambda: None):
        calls.append(len(points))
        return original(self, points, check)
    monkeypatch.setattr(SkinRayIndex, "query", query)
    def wave(mesh, f, k, p, q, points, **kwargs):
        points = points.astype(np.float32)
        n = len(points) // 2
        separations.extend(points[:n, 2] - points[n:, 2])
        return np.exp(1j * k * points[:, 2].astype(float))
    result = evaluate_coupled_plane(store, "field", request, evaluator=wave)
    valid = decode(result, "valid_mask", "u1").astype(bool)
    assert valid.all()
    assert calls == [21]  # base classification only, never 2N masking
    assert min(separations) > .1
    z = decode(result, "points_mm").reshape(-1, 3)[:, 2] / 1000
    expected = np.exp(1j * 2 * np.pi * 100 / 343 * z) / (1.21 * 343)
    np.testing.assert_allclose(decode(result, "pressure_real") + 1j * decode(result, "pressure_imag"), expected, rtol=.003)


def test_spatial_ray_tangency_does_not_toggle_body_parity():
    from server.jobs.body_index import SkinRayIndex
    xyz = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
                    [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]], float)
    faces = np.array([[0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5]])
    direction = np.array([.371, .529, .763])
    # Outside, but the ray touches an isolated corner. The second ray exits
    # through a corner from inside; the third starts exactly on a face.
    points = [xyz[5] - direction * .2, xyz[7] - direction * .2, [.5, .5, 0]]
    inside, _ = SkinRayIndex(xyz[faces], direction).query(points)
    assert inside.tolist() == [False, True, True]
    assert SkinRayIndex(xyz[faces], [1, 0, 0]).query([[.5, 0, .5]])[0].tolist() == [True]


def test_interior_location_batches_tetrahedra_and_preserves_shared_face_priority(monkeypatch):
    xyz = np.array([[0, 0, 0], [.01, 0, 0], [0, .01, 0], [0, 0, .01]])
    tets = np.tile([0, 1, 2, 3], (1024, 1))
    pressure = 1 + xyz[:, 0] + 2j * xyz[:, 1]
    original = np.linalg.inv
    batches = []
    def inverse(edges):
        batches.append(len(edges))
        return original(edges)
    monkeypatch.setattr(np.linalg, "inv", inverse)
    points = np.array([[.001, .002, .003], [.002, .003, .001]])
    values, velocity, inside = sample_interior(xyz, tets, pressure, points, 100, 1.21)
    assert batches == [512, 512]
    assert inside.all()
    np.testing.assert_allclose(values, 1 + points[:, 0] + 2j * points[:, 1])
    np.testing.assert_allclose(velocity, np.tile([1, 2j, 0], (2, 1)) / (1j * 2 * np.pi * 100 * 1.21))


def test_velocity_step_shrinks_to_nearest_surface_distance(stored):
    store, _, _, _, _ = stored
    request = plane_request("exterior", quantity="velocity_normal", frequency=100.)
    request.plane.origin_m = (.001, .001, -.002)
    request.plane.width_m = request.plane.height_m = .001
    steps = []
    def evaluate(mesh, f, k, p, q, points, **kwargs):
        assert not inside_bodies(mesh, points, "off").any()
        n = len(points) // 2
        steps.extend((points[:n, 2] - points[n:, 2]) / 2)
        return np.exp(1j * k * points[:, 2].astype(np.float32))
    result = evaluate_coupled_plane(store, "field", request, evaluator=evaluate)
    assert decode(result, "valid_mask", "u1").all()
    np.testing.assert_allclose(steps, .002 * .05, rtol=1e-9)


@pytest.mark.parametrize("frequency", [50., 100., 200., 500.])
@pytest.mark.parametrize("radius", [.05, .1, .15])
def test_near_field_monopole_velocity_error_below_one_percent(stored, frequency, radius):
    from types import SimpleNamespace
    store, _, _, _, _ = stored
    fem = store.load_fem_field("field", 100., "fem")
    traces = store.load_bem_traces("field", 100., "fem")
    replay = SimpleNamespace(load_fem_field=lambda *a: {**fem, "frequency_hz": frequency},
                             load_bem_traces=lambda *a: traces)
    request = plane_request("exterior", quantity="velocity_normal", frequency=frequency)
    request.plane.origin_m = (.004, .003, radius)
    request.plane.width_m = request.plane.height_m = .001
    def monopole(mesh, f, k, p, q, points, **kwargs):
        # Exercise the actual float32 point boundary used by the worker too.
        r = np.linalg.norm(points.astype(np.float32).astype(float), axis=1)
        return np.exp(1j * k * r) / r
    result = evaluate_coupled_plane(replay, "field", request, evaluator=monopole)
    assert decode(result, "valid_mask", "u1").all()
    assert not decode(result, "mask_reason", "u1").any()
    from server.solver.field_plane import field_plane_points
    points = field_plane_points(request.plane)
    r = np.linalg.norm(points, axis=1)
    k = 2 * np.pi * frequency / 343
    expected = np.exp(1j * k * r) * (1j * k * r - 1) * points[:, 2] / r ** 3 / (1j * 2 * np.pi * frequency * 1.21)
    actual = decode(result, "pressure_real") + 1j * decode(result, "pressure_imag")
    assert np.max(np.abs(actual / expected - 1)) < .01


def test_nearest_skin_distance_checks_faces_edges_and_remote_centroids():
    from server.jobs.body_index import SkinDistanceIndex
    # The large triangle is closer although its centroid is much farther away.
    triangles = np.array([[[0, 0, 0], [100, 0, 0], [0, 100, 0]],
                          [[0, 0, 1], [1, 0, 1], [0, 1, 1]]], float)
    # Force separate leaves, so the centroid seed alone cannot find the answer.
    triangles = np.concatenate([triangles[:1], np.repeat(triangles[1:], 260, axis=0)])
    points = np.array([[.1, .1, .01], [-.1, .1, 0], [-.1, -.1, 0]])
    np.testing.assert_allclose(SkinDistanceIndex(triangles).query(points), [.01, .1, np.sqrt(.02)])


def test_velocity_ulp_floor_tracks_small_coordinates(stored):
    store, _, _, _, _ = stored
    request = plane_request("exterior", quantity="velocity_normal", frequency=100.)
    request.plane.origin_m = (.001, .001, -5e-6)
    request.plane.width_m = request.plane.height_m = .001
    steps = []
    def wave(mesh, f, k, p, q, points, **kwargs):
        n = len(points) // 2
        steps.extend((points[:n, 2] - points[n:, 2]) / 2)
        return np.exp(1j * k * points[:, 2].astype(np.float32).astype(float))
    result = evaluate_coupled_plane(store, "field", request, evaluator=wave)
    assert decode(result, "valid_mask", "u1").all()
    np.testing.assert_allclose(steps, 5e-6 * .05)


def test_remote_near_surface_velocity_is_masked_with_reason(stored):
    store, _, _, _, arrays = stored
    offset = np.array([1000., 1000., 1000.])
    store.store_bem_trace_arrays("field", {**arrays, "points_m": arrays["points_m"] + offset})
    request = plane_request("exterior", quantity="velocity_normal", frequency=100.)
    request.plane.origin_m = tuple(offset + [.001, .001, -5e-6])
    request.plane.width_m = request.plane.height_m = .001
    result = evaluate_coupled_plane(store, "field", request,
        evaluator=lambda *a, **k: pytest.fail("unresolvable stencil evaluated"))
    assert not decode(result, "valid_mask", "u1").any()
    assert (decode(result, "region_class", "u1") == 2).all()
    assert (decode(result, "mask_reason", "u1") == 2).all()
    assert not decode(result, "pressure_real").any()


def test_rotated_box_cut_supercover_blocks_only_crossed_cells():
    from server.jobs.body_index import crossing_cells
    from types import SimpleNamespace
    # Off-grid corners avoid numerical ambiguity about closed-cell edge touches.
    corners = np.array([[-.61, -.17], [.61, -.17], [.61, .17], [-.61, .17]])
    angle = .61
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    corners = corners @ rotation.T + [.003, .007]
    triangles = []
    for a, b in zip(corners, np.roll(corners, -1, axis=0)):
        bottom_a, bottom_b = np.r_[a, -.2], np.r_[b, -.2]
        top_a, top_b = np.r_[a, .2], np.r_[b, .2]
        triangles.extend([[bottom_a, bottom_b, top_b], [bottom_a, top_b, top_a]])
    plane = SimpleNamespace(origin_m=[0, 0, 0], axis_u=[1, 0, 0], axis_v=[0, 1, 0],
                            width_m=2., height_m=2., nx=81, ny=81)
    blocked = crossing_cells(np.asarray(triangles), plane, np.eye(3)).reshape(80, 80)
    # Independent segment/rectangle separating-axis oracle for every cell.
    expected = np.zeros_like(blocked)
    for row in range(80):
        for col in range(80):
            lo = np.array([col, row]) * .025 - 1
            hi = lo + .025
            cell = np.array([lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]])
            for a, b in zip(corners, np.roll(corners, -1, axis=0)):
                normal = np.array([a[1] - b[1], b[0] - a[0]])
                axes = np.array([[1, 0], [0, 1], normal])
                segment_projection, cell_projection = np.array([a, b]) @ axes.T, cell @ axes.T
                if ((segment_projection.max(axis=0) >= cell_projection.min(axis=0))
                    & (cell_projection.max(axis=0) >= segment_projection.min(axis=0))).all():
                    expected[row, col] = True
    assert expected.any()
    assert np.count_nonzero(blocked & ~expected) == 0
    assert np.count_nonzero(expected & ~blocked) == 0


def test_grid_edge_corner_clipping_and_coplanar_supercovers():
    from server.jobs.body_index import _supercover_segment, _coplanar_cells
    blocked = np.zeros((4, 4), bool)
    _supercover_segment(blocked, np.array([-1., 2.]), np.array([5., 2.]))
    assert blocked.tolist() == [[False] * 4, [True] * 4, [True] * 4, [False] * 4]
    blocked[:] = False
    _supercover_segment(blocked, np.array([.5, .5]), np.array([3.5, 3.5]))
    row, col = np.indices(blocked.shape)
    np.testing.assert_array_equal(blocked, np.abs(row - col) <= 1)
    blocked[:] = False
    _coplanar_cells(blocked, np.array([[.1, .1], [3.9, .1], [.1, 3.9]]))
    assert blocked[0].all() and blocked[:, 0].all()
    assert not blocked[3, 3]
    blocked[:] = False
    _coplanar_cells(blocked, np.array([[-10., .1], [-8., .1], [-10., 3.9]]))
    assert not blocked.any()


@pytest.mark.parametrize("symmetry", ["off", "xy"])
def test_adaptive_dense_feature_bins_bound_memory_and_local_scaling(symmetry):
    from server.jobs.body_index import SkinRayIndex, SkinDistanceIndex, skin_triangles, MAX_BIN_FACES, MAX_WORK_PAIRS
    from types import SimpleNamespace
    counts, distance_counts = [], []
    grid = np.linspace(.00001, .00999, 48)
    x, y = np.meshgrid(grid, grid)
    points = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, .1)])
    for resolution in (64, 128):
        grid = np.linspace(0, .01, resolution + 1)
        x, y = np.meshgrid(grid, grid)
        vertices = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
        row, col = np.meshgrid(np.arange(resolution), np.arange(resolution), indexing="ij")
        a = (row * (resolution + 1) + col).ravel()
        faces = np.concatenate([np.column_stack([a, a + 1, a + resolution + 2]),
                                np.column_stack([a, a + resolution + 2, a + resolution + 1])])
        # A distant feature broadens the whole-skin projection by 200x.
        triangles = np.concatenate([vertices[faces], [[[-1, -1, -1], [1, -1, -1], [-1, 1, -1]]]])
        mesh = SimpleNamespace(points_m=triangles.reshape(-1, 3), faces=np.arange(triangles.size // 3).reshape(-1, 3))
        triangles = skin_triangles(mesh, symmetry)
        index = SkinRayIndex(triangles, [0, 0, 1])
        inside, _ = index.query(points)
        assert not inside.any()
        assert index.bins.max_candidates <= MAX_BIN_FACES
        assert index.peak_pairs <= MAX_WORK_PAIRS
        assert index.pair_count < len(points) * len(triangles) * .05
        counts.append(index.pair_count)
        distance_index = SkinDistanceIndex(triangles)
        np.testing.assert_allclose(distance_index.query(points), .1)
        assert distance_index.peak_pairs <= MAX_WORK_PAIRS
        distance_counts.append(distance_index.pair_count)
    # Quadrupling the dense feature does not quadruple work on a zoomed plane.
    assert counts[1] < counts[0] * 2
    assert distance_counts[1] < distance_counts[0] * 2


def test_corrupt_retained_bem_trace_route_returns_gone(stored):
    store, _, _, _, arrays = stored
    store.store_bem_trace_arrays("field", {**arrays, "neumann": np.full(arrays["neumann"].shape, np.nan, complex)})
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    status, body, _ = asyncio.run(_request_with_headers(app, "POST", "/api/results/field/fem-field/plane",
        body=plane_request("exterior").model_dump(mode="json")))
    assert status == 410
    assert "corrupt" in json.loads(body)["detail"]
