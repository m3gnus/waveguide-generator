"""Coupled trace retention and analytic plane replay without a native solve."""
import asyncio
import base64
import json

from fastapi import FastAPI
import numpy as np
import pytest

from server.jobs.api import create_jobs_router, FemPlaneRequest
from server.jobs.bem_traces import check_trace_size
from server.solver.beat_adapter.artifacts import trace_arrays
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


@pytest.mark.parametrize("kind", ["interface", "rigid"])
def test_an_fem_aperture_does_not_block_cells_but_a_wall_does(kind):
    from server.jobs.body_index import crossing_cells
    from server.jobs.coupled_field import wall_triangles
    request = plane_request()
    request.plane.nx = request.plane.ny = 2
    # The same 2 mm slab inside one cell, in a rotated solver frame. As the
    # FEM region's interface it is open air; as its rigid wall it blocks.
    corners = np.array([[.014, 0, 0], [.016, 0, 0], [.014, .01, 0], [.016, .01, 0],
                        [.014, 0, .01], [.016, 0, .01], [.014, .01, .01], [.016, .01, .01]])
    faces = np.array([[0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5]])
    rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    skin = (corners @ rotation.T)[faces]
    encode = lambda array, dtype: base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode()
    payload = {"points_mm": encode(corners * 1000, "<f4"),
               "surfaces": [{"name": f"wg-fem-v1|region=r|group=g|kind={kind}", "triangles": encode(faces, "<u4")}]}
    walls = wall_triangles(skin, payload, rotation)
    assert len(walls) == (0 if kind == "interface" else len(skin))
    assert crossing_cells(walls, request.plane, rotation).tolist() == [kind != "interface"]


def test_cells_across_a_box_regions_aperture_are_open_but_its_walls_still_block():
    from server.jobs.body_index import crossing_cells
    from server.jobs.coupled_field import wall_triangles
    request = plane_request()
    request.plane.origin_m = [.0155, .003, .002]
    # A 10 mm box region whose x = 10 mm face is its aperture. The plane
    # z = 2 mm meets that face inside the second column of cells only.
    corners = np.array([[x, y, z] for x in (0, .01) for y in (0, .01) for z in (0, .01)])
    quads = {"x0": (0, 1, 3, 2), "x1": (4, 6, 7, 5), "y0": (0, 4, 5, 1), "y1": (2, 3, 7, 6),
             "z0": (0, 2, 6, 4), "z1": (1, 5, 7, 3)}
    faces = {name: np.array([[a, b, c], [a, c, d]]) for name, (a, b, c, d) in quads.items()}
    skin = corners[np.concatenate(list(faces.values()))]
    encode = lambda array, dtype: base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode()
    surfaces = [{"name": f"wg-fem-v1|region=r|group={name}|kind={'interface' if name == 'x1' else 'rigid'}",
                 "triangles": encode(block, "<u4")} for name, block in faces.items()]
    payload = {"points_mm": encode(corners * 1000, "<f4"), "surfaces": surfaces}
    blocked = crossing_cells(skin, request.plane, np.eye(3)).reshape(2, 6)
    assert blocked[:, 1].all() and blocked.sum() == 2
    assert not crossing_cells(wall_triangles(skin, payload, np.eye(3)), request.plane, np.eye(3)).any()


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


def test_plane_requests_reuse_the_job_skin_index_until_its_artifact_changes(stored, monkeypatch):
    from server.jobs import body_index
    store, _, _, _, traces = stored
    body_index._skin_indexes.clear()
    builds = {"ray": 0, "distance": 0}
    for name, cls in (("ray", body_index.SkinRayIndex), ("distance", body_index.SkinDistanceIndex)):
        original = cls.__init__
        def counted(self, *args, _name=name, _original=original, **kwargs):
            builds[_name] += 1
            _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, "__init__", counted)
    def known(mesh, f, k, p, q, points, **kwargs):
        return points[:, 0] + 0j
    request = plane_request("exterior", quantity="velocity_normal", frequency=100.)
    request.plane.origin_m = (.2, .3, .4)
    evaluate_coupled_plane(store, "field", request, evaluator=known)
    request.plane.origin_m = (.25, .3, .4)
    evaluate_coupled_plane(store, "field", request, evaluator=known)
    evaluate_coupled_plane(store, "field", plane_request(), evaluator=known)
    assert builds == {"ray": 1, "distance": 1}
    # A re-published artifact with a different skin rebuilds and replaces
    # the job's entry.
    changed = dict(traces, points_m=traces["points_m"] * 1.01)
    store.store_bem_trace_arrays("field", changed)
    evaluate_coupled_plane(store, "field", request, evaluator=known)
    assert builds == {"ray": 2, "distance": 2}
    assert len([key for key in body_index._skin_indexes if key[0] == "field"]) == 1
    # A deleted artifact drops the job's entries.
    from types import SimpleNamespace
    from server.jobs.fem_field import FemFieldInvalidSelection
    payload = store.load_fem_field("field", 100., "fem")
    gone = SimpleNamespace(load_fem_field=lambda *a: payload, load_bem_traces=lambda *a: None)
    with pytest.raises(FemFieldInvalidSelection):
        evaluate_coupled_plane(gone, "field", request, evaluator=known)
    assert not [key for key in body_index._skin_indexes if key[0] == "field"]


def _skin(scale=1.):
    triangles = _grid_cube(2) * scale
    points, faces = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    return SurfaceMesh(points, faces.reshape(-1, 3), np.ones(len(triangles), dtype=int), (), ())


@pytest.fixture
def skin_cache():
    from server.jobs import body_index
    body_index._skin_indexes.clear()
    yield body_index
    body_index._skin_indexes.clear()


def test_skin_cache_holds_at_most_two_entries_within_a_byte_budget(skin_cache, monkeypatch):
    body_index = skin_cache
    meshes = [_skin(scale) for scale in (1., 2., 3.)]
    for i, mesh in enumerate(meshes):
        body_index.skin_indexes(f"job{i}", mesh, "off")
    assert [key[0] for key in body_index._skin_indexes] == ["job1", "job2"]
    size = body_index.skin_index_bytes(body_index.skin_triangles(meshes[0], "off"))
    # A budget for one entry evicts the least recently used to fit.
    monkeypatch.setattr(body_index, "SKIN_INDEX_CACHE_BYTES", size + size // 2)
    body_index.skin_indexes("job1", meshes[1], "off")
    body_index.skin_indexes("job0", meshes[0], "off")
    assert [key[0] for key in body_index._skin_indexes] == ["job0"]
    # An entry over the whole budget is built for its request, never cached.
    monkeypatch.setattr(body_index, "SKIN_INDEX_CACHE_BYTES", size - 1)
    entry = body_index.skin_indexes("job3", meshes[2], "off")
    assert entry.ray().query([[1., 1., 1.]])[0].tolist() == [True]
    assert [key[0] for key in body_index._skin_indexes] == ["job0"]
    assert body_index.skin_indexes("job3", meshes[2], "off") is not entry


def test_concurrent_first_requests_build_each_skin_index_once(skin_cache, monkeypatch):
    import threading
    import time
    body_index = skin_cache
    builds = {"ray": 0, "distance": 0}
    for name, cls in (("ray", body_index.SkinRayIndex), ("distance", body_index.SkinDistanceIndex)):
        original = cls.__init__
        def slow(self, *args, _name=name, _original=original, **kwargs):
            builds[_name] += 1
            time.sleep(.2)
            _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, "__init__", slow)
    entry = body_index.skin_indexes("job", _skin(), "off")
    start = threading.Barrier(2)
    results = []
    def request():
        start.wait()
        results.append((entry.ray(), entry.distance()))
    threads = [threading.Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert builds == {"ray": 1, "distance": 1}
    assert results[0][0] is results[1][0] and results[0][1] is results[1][1]


def test_deleting_the_trace_sidecar_forgets_the_job_skin_index(stored, skin_cache):
    store, _, _, _, _ = stored
    body_index = skin_cache
    body_index.skin_indexes("field", _skin(), "off")
    body_index.skin_indexes("other", _skin(2.), "off")
    store.delete_fem_field("field")
    assert [key[0] for key in body_index._skin_indexes] == ["other"]


@pytest.mark.parametrize("mirror", [1., -1.])
@pytest.mark.parametrize("offset", [5e-8, 5e-9])
def test_ray_parity_beside_an_edge_between_very_different_heights(mirror, offset):
    from server.jobs.body_index import SkinRayIndex
    # One sheet, split along y=0 into a tall and a sliver triangle. A ray just
    # beside the shared edge crosses the sheet once, inside the sliver.
    xyz = np.array([[0, 0, 1], [1, 0, 1], [.5, 1000, 1], [.5, -1e-3, 1]], float)
    xyz[:, 1] *= mirror
    faces = np.array([[0, 1, 2], [1, 0, 3]])
    points = [[.5, -offset * mirror, 0], [.5, offset * mirror, 0]]
    inside, _ = SkinRayIndex(xyz[faces], [0, 0, 1]).query(points)
    assert inside.tolist() == [True, True]


def _grid_cube(n=4):
    """Outward triangles of [0, n]^3, each face n x n quads split alternately."""
    vertices = {}
    def vertex(point):
        return vertices.setdefault(tuple(point), len(vertices))
    faces = []
    for axis in range(3):
        for side in (0, n):
            for i in range(n):
                for j in range(n):
                    def corner(a, b):
                        point = [0, 0, 0]
                        point[axis], point[(axis + 1) % 3], point[(axis + 2) % 3] = side, a, b
                        return vertex(point)
                    q = [corner(i, j), corner(i + 1, j), corner(i + 1, j + 1), corner(i, j + 1)]
                    pair = ([[q[0], q[1], q[2]], [q[0], q[2], q[3]]] if (i + j) % 2
                            else [[q[0], q[1], q[3]], [q[1], q[2], q[3]]])
                    faces.extend(pair)
    xyz = np.array(sorted(vertices, key=vertices.get), float)
    triangles = xyz[np.array(faces)]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    outward = np.einsum("ij,ij->i", normals, triangles.mean(axis=1) - n / 2) > 0
    triangles[~outward] = triangles[~outward][:, ::-1]
    return triangles


def test_ray_parity_is_exact_on_a_shared_diagonal_of_a_grid_cube():
    from server.jobs.body_index import SkinRayIndex
    # The (1,1,1) ray from (2, 1, 2.5) leaves through the shared diagonal of
    # two top-face triangles. A row-dependent matmul rounded the shared
    # vertices, and the query point, differently by row, so neither triangle
    # counted the crossing. The point is queried at an even and an odd row.
    inside, _ = SkinRayIndex(_grid_cube(), (1, 1, 1)).query([[2, 1, 2.5], [2, 2, 2], [2, 1, 2.5]])
    assert inside.tolist() == [True, True, True]


@pytest.mark.parametrize("direction", [(1, 1, 1), (1, 1, 0), (1, 0, 0), (0, 0, 1), (.371, .529, .763),
                                       (-1, 2, 3), (1, -1, 1)])
def test_ray_parity_on_a_grid_cube_through_vertices_edges_and_random_points(direction):
    from server.jobs.body_index import SkinRayIndex
    import itertools
    n = 4
    rng = np.random.default_rng(abs(hash(direction)) % 2 ** 32)
    lattice = np.arange(-1, n + 2, .5)
    # Lattice points send rays through shared vertices and along edges.
    points = np.vstack([np.array(list(itertools.product(lattice, lattice, lattice))),
                        rng.uniform(-1, n + 1, (3000, 3)),
                        rng.integers(-1, n + 2, (500, 3)) + rng.uniform(-1e-12, 1e-12, (500, 3))])
    # Points within 1e-9 of the skin count as on it, hence inside.
    truth = np.all((points >= -1e-9) & (points <= n + 1e-9), axis=1)
    triangles = _grid_cube(n)
    for order in (np.arange(len(triangles)), rng.permutation(len(triangles))):
        inside, _ = SkinRayIndex(triangles[order], direction).query(points)
        wrong = np.flatnonzero(inside != truth)
        assert not len(wrong), points[wrong[:5]].tolist()


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
    request.plane.origin_m = (.001, .001, -.2)
    request.plane.width_m = request.plane.height_m = .001
    steps = []
    def evaluate(mesh, f, k, p, q, points, **kwargs):
        assert not inside_bodies(mesh, points, "off").any()
        n = len(points) // 2
        steps.extend((points[:n, 2] - points[n:, 2]) / 2)
        return np.exp(1j * k * points[:, 2].astype(np.float32))
    result = evaluate_coupled_plane(store, "field", request, evaluator=evaluate)
    assert decode(result, "valid_mask", "u1").all()
    np.testing.assert_allclose(steps, .2 * .05, rtol=1e-9)


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
    if frequency * (radius - .01) * .05 * 2 * np.pi / 343 < .001:
        assert not decode(result, "valid_mask", "u1").any()
        assert (decode(result, "mask_reason", "u1") == 2).all()
        return
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


def test_float32_pressure_noise_masks_submicron_stencil_even_at_small_coordinates(stored):
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
    assert not decode(result, "valid_mask", "u1").any()
    assert (decode(result, "mask_reason", "u1") == 2).all()
    assert steps == []


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


@pytest.mark.parametrize('fault', ['nan', 'shape'])
def test_evaluator_fault_returns_retryable_500(stored, monkeypatch, fault):
    store, _, _, _, _ = stored
    def evaluate(mesh, f, k, p, q, points, **kwargs):
        return np.full(len(points), np.nan) if fault == 'nan' else np.ones((len(points), 1))
    monkeypatch.setattr('server.solver.beat_field.evaluate_exterior_from_traces', evaluate)
    app = FastAPI()
    app.include_router(create_jobs_router(JobRuntime(store)))
    status, body, _ = asyncio.run(_request_with_headers(app, 'POST', '/api/results/field/fem-field/plane',
        body=plane_request('exterior').model_dump(mode='json')))
    assert status == 500
    message = json.loads(body)['detail']
    assert 'BEAT runtime' in message and 'retry' in message


@pytest.mark.parametrize('distance', [.0003, .001, .002, .003, .005])
def test_float32_near_skin_velocity_is_conservatively_masked(stored, distance):
    store, _, _, _, _ = stored
    request = plane_request('exterior', quantity='velocity_normal', frequency=100.)
    request.plane.origin_m = (.001, .001, -distance)
    request.plane.width_m = request.plane.height_m = .001
    result = evaluate_coupled_plane(store, 'field', request,
        evaluator=lambda *a, **k: pytest.fail('unreliable float32 stencil evaluated'))
    assert not decode(result, 'valid_mask', 'u1').any()
    assert (decode(result, 'mask_reason', 'u1') == 2).all()


def test_near_degenerate_triangle_uses_segment_distance_at_every_scale():
    from server.jobs.body_index import point_triangle_distances
    # Collinear to roundoff: cancellation in barycentrics must not manufacture
    # a spurious interior projection much nearer than all three segments.
    a = np.array([-.05392527, -.07520606, -.00212985])
    b = np.array([-.03934518, -.08409726, .01996619])
    c = b + (b - a) * .7 + [1e-16, -1e-16, 1e-16]
    p = np.array([.02, .03, .05])
    for scale in (1e-6, 1., 1e6):
        triangle, point = np.array([a, b, c]) * scale, p * scale
        expected = min(np.linalg.norm(point - (start + np.clip((point - start) @ (end - start)
            / ((end - start) @ (end - start)), 0, 1) * (end - start)))
            for start, end in zip(triangle, np.roll(triangle, -1, axis=0)))
        assert point_triangle_distances(point, triangle) == pytest.approx(expected, rel=1e-12)


def test_best_first_distance_100k_sphere_256_plane_bounds_visited_pairs():
    from server.jobs.body_index import SkinDistanceIndex, MAX_WORK_PAIRS
    theta, phi = np.meshgrid(np.linspace(0, np.pi, 201), np.linspace(0, 2 * np.pi, 251), indexing='ij')
    xyz = np.column_stack([(np.sin(theta) * np.cos(phi)).ravel(),
                           (np.sin(theta) * np.sin(phi)).ravel(), np.cos(theta).ravel()])
    row, col = np.meshgrid(np.arange(200), np.arange(250), indexing='ij')
    a = (row * 251 + col).ravel()
    faces = np.concatenate([np.column_stack([a, a + 1, a + 252]),
                            np.column_stack([a, a + 252, a + 251])])
    grid = np.linspace(-1.2, 1.2, 256)
    x, y = np.meshgrid(grid, grid)
    points = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, 1.01)])
    index = SkinDistanceIndex(xyz[faces])
    distance = index.query(points)
    expected = np.linalg.norm(points, axis=1) - 1
    np.testing.assert_allclose(distance, expected, atol=.0002)
    assert index.bins.max_candidates <= 16
    assert index.peak_pairs <= MAX_WORK_PAIRS
    assert index.pair_count < len(points) * 100



def test_velocity_clearance_is_independent_of_skin_tessellation(stored):
    store, _, _, _, arrays = stored
    # A coarse planar skin does not impose an arbitrary element-size clearance.
    store.store_bem_trace_arrays('field', {**arrays, 'points_m': arrays['points_m'] * 100})
    request = plane_request('exterior', quantity='velocity_normal', frequency=1000.123456)
    request.plane.origin_m = (.1, .1, -.1)
    request.plane.width_m = request.plane.height_m = .001
    result = evaluate_coupled_plane(store, 'field', request,
        evaluator=lambda mesh, f, k, p, q, points, **kw: np.exp(1j * k * points[:, 2]))
    assert decode(result, 'valid_mask', 'u1').all()
    assert not decode(result, 'mask_reason', 'u1').any()


@pytest.mark.parametrize('frequency', [10000., 16000.])
@pytest.mark.parametrize('field', ['monopole', 'plane-wave'])
def test_high_frequency_remote_velocity_is_valid_and_accurate(stored, frequency, field):
    from types import SimpleNamespace
    from server.solver.field_plane import field_plane_points
    store = stored[0]
    fem = store.load_fem_field('field', 100., 'fem')
    traces = store.load_bem_traces('field', 100., 'fem')
    replay = SimpleNamespace(load_fem_field=lambda *a: {**fem, 'frequency_hz': frequency},
                             load_bem_traces=lambda *a: traces)
    request = plane_request('exterior', quantity='velocity_normal', frequency=frequency)
    request.plane.origin_m = (.004, .003, .5)
    request.plane.width_m = request.plane.height_m = .001
    k = 2 * np.pi * frequency / 343
    def evaluate(mesh, f, k, p, q, points, **kw):
        points = points.astype(np.float32).astype(float)
        r = np.linalg.norm(points, axis=1)
        return (np.exp(1j * k * r) / r if field == 'monopole'
                else np.exp(1j * k * points[:, 2])).astype(np.complex64)
    result = evaluate_coupled_plane(replay, 'field', request, evaluator=evaluate)
    assert decode(result, 'valid_mask', 'u1').all()
    assert not decode(result, 'mask_reason', 'u1').any()
    points = field_plane_points(request.plane)
    r = np.linalg.norm(points, axis=1)
    gradient = (np.exp(1j * k * r) * (1j * k * r - 1) * points[:, 2] / r**3
                if field == 'monopole' else 1j * k * np.exp(1j * k * points[:, 2]))
    expected = gradient / (1j * 2 * np.pi * frequency * 1.21)
    actual = decode(result, 'pressure_real') + 1j * decode(result, 'pressure_imag')
    assert np.max(np.abs(actual / expected - 1)) < .01


@pytest.mark.parametrize('frequency', [20., 20000.])
@pytest.mark.parametrize('clearance_factor,shown', [(.005, False), (1.25, True)])
def test_sweep_end_clearance_masks_wrong_noisy_surface_source_samples(stored, frequency, clearance_factor, shown):
    from types import SimpleNamespace
    from server.solver.field_plane import field_plane_points
    store = stored[0]
    fem = store.load_fem_field('field', 100., 'fem')
    traces = store.load_bem_traces('field', 100., 'fem')
    replay = SimpleNamespace(load_fem_field=lambda *a: {**fem, 'frequency_hz': frequency},
                             load_bem_traces=lambda *a: traces)
    request = plane_request('exterior', quantity='velocity_normal', frequency=frequency)
    k = 2 * np.pi * frequency / 343
    distance = clearance_factor * .001 / k / .05
    request.plane.origin_m = (.001, .001, -distance)
    request.plane.width_m = request.plane.height_m = .00001
    # A monopole on the retained z=0 skin plus an outgoing plane wave. Probe
    # away from the source pole, including the skin's singular evaluation band.
    source = np.array([.008, .001, 0.])
    def pressure(points):
        r = np.linalg.norm(points - source, axis=1)
        return np.exp(1j * k * points[:, 2]) + .0005 * np.exp(1j * k * r) / r
    def derivative(points):
        r = np.linalg.norm(points - source, axis=1)
        return (1j * k * np.exp(1j * k * points[:, 2])
                + .0005 * np.exp(1j * k * r) * (1j * k * r - 1) * points[:, 2] / r**3)
    calls = []
    def noisy(mesh, f, k, p, q, points, **kw):
        calls.append(len(points))
        n = len(points) // 2
        exact = pressure(points.astype(np.float32).astype(float))
        direction = derivative((points[:n] + points[n:]) / 2)
        direction /= np.abs(direction)
        # Opposing 1e-5 relative errors are a worst-case float32 potential
        # noise realization, independent of point-coordinate rounding.
        signs = np.concatenate([direction, -direction])
        return (exact + 1e-5 * np.abs(exact) * signs).astype(np.complex64)
    points = field_plane_points(request.plane)
    h = min(343 / frequency / 50, .1, distance * .05)
    probes = np.concatenate([points + [0, 0, h], points - [0, 0, h]])
    raw = noisy(None, frequency, k, None, None, probes)
    gradient = (raw[:len(points)] - raw[len(points):]) / (2 * h)
    raw_error = np.max(np.abs(gradient / derivative(points) - 1))
    calls.clear()
    result = evaluate_coupled_plane(replay, 'field', request, evaluator=noisy)
    valid = decode(result, 'valid_mask', 'u1')
    assert valid.all() if shown else not valid.any()
    if shown:
        actual = decode(result, 'pressure_real') + 1j * decode(result, 'pressure_imag')
        expected = derivative(points) / (1j * 2 * np.pi * frequency * 1.21)
        assert np.max(np.abs(actual / expected - 1)) < .01
        assert raw_error < .01
    else:
        assert raw_error > .01
        assert not calls
        assert (decode(result, 'mask_reason', 'u1') == 2).all()



@pytest.mark.parametrize("symmetry", ["off", "xy"])
def test_distance_traversal_work_does_not_depend_on_last_bit_rounding(monkeypatch, symmetry):
    """Points a fixed height above a flat skin tie with every leaf beneath them.
    Different CPUs (AVX-512 or AVX2 numpy and BLAS kernels) round those bounds
    differently in the last bit; the visited work must not depend on it."""
    import server.jobs.body_index as body_index
    from server.jobs.body_index import SkinDistanceIndex, skin_triangles
    from types import SimpleNamespace

    resolution = 64
    grid = np.linspace(0, .01, resolution + 1)
    x, y = np.meshgrid(grid, grid)
    vertices = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    row, col = np.meshgrid(np.arange(resolution), np.arange(resolution), indexing="ij")
    a = (row * (resolution + 1) + col).ravel()
    faces = np.concatenate([np.column_stack([a, a + 1, a + resolution + 2]),
                            np.column_stack([a, a + resolution + 2, a + resolution + 1])])
    triangles = np.concatenate([vertices[faces], [[[-1, -1, -1], [1, -1, -1], [-1, 1, -1]]]])
    mesh = SimpleNamespace(points_m=triangles.reshape(-1, 3), faces=np.arange(triangles.size // 3).reshape(-1, 3))
    triangles = skin_triangles(mesh, symmetry)
    query = np.linspace(.00001, .00999, 48)
    qx, qy = np.meshgrid(query, query)
    points = np.column_stack([qx.ravel(), qy.ravel(), np.full(qx.size, .1)])
    exact = body_index.point_triangle_distances

    def run(noise, seed):
        rng = np.random.default_rng(seed)

        def rounded(p, t):
            distances = exact(p, t)
            return distances * (1 + noise * rng.standard_normal(distances.shape)) if noise else distances

        monkeypatch.setattr(body_index, "point_triangle_distances", rounded)
        index = SkinDistanceIndex(triangles)
        np.testing.assert_allclose(index.query(points), .1, rtol=1e-9)
        return index.pair_count

    counts = {run(0, 0) for _ in range(20)} | {run(1e-13, seed) for seed in range(6)}
    assert len(counts) == 1
