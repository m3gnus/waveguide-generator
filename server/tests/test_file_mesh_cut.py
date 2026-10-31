"""Full file meshes enter the pinned CAD cutter and the shared publication owner."""

import asyncio
import json
import threading

import numpy as np
import pytest

from server.cadlink.store import CadLinkStore, StaleAttempt, file_model_id
from server.jobs.models_api import MeshRegistration, register_mesh, register_mesh_async
from server.mesh.file_import import mesh_text, prepare_file_mesh, file_mesh_integrity
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.preparation_fence import PreparationFence


def full_mesh():
    from test_file_mesh_roles_symmetry import box_mesh

    mesh = box_mesh()
    mesh.points[:, :2] -= .1
    mesh.tags[np.isin(mesh.tags, [101, 102])] = 101
    # The other cap is an independent source, and untagged walls stay distinct.
    mesh.tags[np.all(mesh.points[mesh.triangles, 2] == 0, axis=1)] = 102
    mesh.tags[(mesh.tags == 1) & np.all(mesh.points[mesh.triangles, 0] < 0, axis=1)] = 0
    mesh.names = {0: "Untagged surface", 1: "Walls", 101: "HF", 102: "MF"}
    return mesh


def cutting_registration(mesh=None, **changes):
    return MeshRegistration(filename="symmetric.msh", msh_text=mesh_text(mesh or full_mesh()),
                            units="m", forward="+z", up="+y", roles={101: "HF", 102: "MF"}, **changes)


def prepare(mesh, **options):
    return _run_in_gmsh_session(lambda: prepare_file_mesh(mesh, "m", "+z", "+y",
                                                       roles={101: "HF", 102: "MF"}, **options))


def triangle_keys(mesh):
    # Order-independent coordinate key; winding is checked separately below.
    return {(int(tag), tuple(sorted(map(tuple, mesh.points[triangle]))))
            for triangle, tag in zip(mesh.triangles, mesh.tags)}


@pytest.mark.parametrize("planes", [("x0",), ("y0",), ("x0", "y0")])
def test_real_cut_preserves_facets_groups_winding_and_effective_domain(planes):
    original = full_mesh()
    snapshots = [array.copy() for array in (original.points, original.triangles, original.tags)]
    reduced, transform = prepare(original, domain_planes=planes)
    assert transform["domain_planes"] == transform["cut_planes"] == list(planes)
    assert len(reduced.triangles) < len(original.triangles)
    assert reduced.names == original.names
    axes = [{"x0": 0, "y0": 1}[plane] for plane in planes]
    retained = np.all(original.points[original.triangles][:, :, axes] >= 0, axis=(1, 2))
    keys = triangle_keys(reduced)
    assert all((int(tag), tuple(sorted(map(tuple, original.points[triangle])))) in keys
               for triangle, tag in zip(original.triangles[retained], original.tags[retained]))
    report = file_mesh_integrity(reduced, planes)
    assert report["valid"] and report["orientation_valid"]
    assert report["off_plane_open_edge_count"] == report["nonmanifold_edge_count"] == 0
    before = original.points[original.triangles]
    after = reduced.points[reduced.triangles]
    areas = lambda corners: np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum() / 2
    for tag in np.unique(original.tags):
        if tag in {101, 102}:
            assert areas(after[reduced.tags == tag]) == pytest.approx(areas(before[original.tags == tag]) / 2**len(planes))
    for actual, saved in zip((original.points, original.triangles, original.tags), snapshots):
        np.testing.assert_array_equal(actual, saved)


def test_nonsymmetric_triangulation_uses_native_mirror_rule(monkeypatch):
    import hornlab_mesher.step_prepare as native

    mesh = full_mesh()
    # Split one facet asymmetrically without changing its geometry or source.
    triangle = mesh.triangles[0]
    mesh.points = np.vstack((mesh.points, mesh.points[triangle].mean(axis=0)))
    centre = len(mesh.points) - 1
    mesh.triangles = np.vstack((mesh.triangles[1:], [[triangle[0], triangle[1], centre],
                                                 [triangle[1], triangle[2], centre], [triangle[2], triangle[0], centre]]))
    mesh.tags = np.r_[mesh.tags[1:], [mesh.tags[0]] * 3]
    owner = native.auto_cut_occ_geometry
    seen = []
    def call(groups, **kwargs):
        seen.append(kwargs)
        return owner(groups, **kwargs)
    monkeypatch.setattr(native, "auto_cut_occ_geometry", call)
    reduced, transform = prepare(mesh, auto_cut=True)
    assert transform["cut_planes"] == ["x0", "y0"]
    assert seen == [{"planes": ("x0", "y0"), "triangle_surface_mesh": True}]
    assert len(reduced.triangles) < len(mesh.triangles)


def test_asymmetric_geometry_and_independent_same_role_sources_stay_full():
    mesh = full_mesh()
    mesh.points[0, 0] -= .03
    # Displacement preserves topology but breaks both planes around that corner.
    full, transform = prepare(mesh, auto_cut=True)
    np.testing.assert_array_equal(full.points, mesh.points)
    assert transform["cut_planes"] == []
    assert "Export the half or quarter" in transform["symmetry_report"]["note"]
    mesh = full_mesh()
    mesh.tags[(mesh.tags == 101) & (mesh.points[mesh.triangles].mean(axis=1)[:, 0] < 0)] = 103
    mesh.names[103] = "Second HF"
    result, transform = _run_in_gmsh_session(lambda: prepare_file_mesh(
        mesh, "m", "+z", "+y", domain_planes=["x0"], roles={101: "HF", 103: "HF", 102: "MF"}))
    assert transform["cut_planes"] == transform["domain_planes"] == []
    np.testing.assert_array_equal(result.tags, mesh.tags)
    assert transform["symmetry_report"]["planes"]["x0"]["role_mismatches"] > 0


def test_post_mutation_refusal_reloads_original_model(monkeypatch):
    import gmsh
    import hornlab_mesher.step_prepare as native
    from server.mesh import file_cut

    original_load = file_cut._load_model
    inventories = []
    def load(mesh, roles):
        value = original_load(mesh, roles)
        inventories.append(len(gmsh.model.getEntities(2)))
        return value
    def mutate_then_refuse(*_args, **_kwargs):
        gmsh.model.occ.remove(gmsh.model.getEntities(2)[:3], recursive=True)
        gmsh.model.occ.synchronize()
        raise native.TriangleCutRefused("injected after destructive boolean")
    monkeypatch.setattr(file_cut, "_load_model", load)
    monkeypatch.setattr(native, "auto_cut_occ_geometry", mutate_then_refuse)
    mesh = full_mesh()
    result, transform = prepare(mesh, auto_cut=True)
    assert inventories == [len(mesh.triangles)] * 2
    np.testing.assert_array_equal(result.points, mesh.points)
    np.testing.assert_array_equal(result.triangles, mesh.triangles)
    np.testing.assert_array_equal(result.tags, mesh.tags)
    assert transform["domain_planes"] == transform["cut_planes"] == []
    assert "could not be cut safely" in transform["symmetry_report"]["note"]


@pytest.mark.parametrize("count,entered", [(5000, True), (5001, False)])
def test_owned_file_cut_bound_precedes_occ_model_creation(monkeypatch, count, entered):
    from server.mesh import file_cut

    mesh = full_mesh()
    mesh.triangles = np.resize(mesh.triangles, (count, 3))
    mesh.tags = np.resize(mesh.tags, count)
    def unexpected(*_args):
        raise LookupError("entered OCC")
    monkeypatch.setattr(file_cut, "_load_model", unexpected)
    if entered:
        with pytest.raises(LookupError, match="entered OCC"):
            _run_in_gmsh_session(lambda: file_cut.cut_file_mesh(mesh, ("x0",), {101: "HF"}))
    else:
        result, cut = file_cut.cut_file_mesh(mesh, ("x0",), {101: "HF"})
        assert result is mesh and cut["cut_planes"] == []
        assert "5,001" in cut["note"] and "5,000" in cut["note"]


def test_existing_reduced_mesh_and_opt_out_do_not_enter_cutter(monkeypatch):
    from server.mesh import file_cut
    from test_file_mesh_roles_symmetry import box_mesh

    monkeypatch.setattr(file_cut, "cut_file_mesh", lambda *_args: pytest.fail("unexpected reduction"))
    mesh = box_mesh(planes=("x0", "y0"))
    first, transform = prepare_file_mesh(mesh, "m", "+z", "+y", domain_planes=["x0", "y0"], auto_cut=True)
    np.testing.assert_array_equal(first.triangles, mesh.triangles)
    assert "cut_planes" not in transform
    full, transform = prepare_file_mesh(full_mesh(), "m", "+z", "+y")
    assert "cut_planes" not in transform
    assert len(full.triangles) == len(full_mesh().triangles)


@pytest.mark.parametrize("units,scale,forward,up", [("mm", 1000, "+z", "+y"), ("cm", 100, "+x", "+z"), ("in", 1/.0254, "-z", "+y")])
def test_unit_and_axis_frames_cut_the_same_si_geometry(units, scale, forward, up):
    from server.mesh.file_import import AXES
    mesh = full_mesh()
    rotation = np.asarray([np.cross(AXES[up], AXES[forward]), AXES[up], AXES[forward]])
    mesh.points = mesh.points @ rotation * scale
    reduced, transform = _run_in_gmsh_session(lambda: prepare_file_mesh(mesh, units, forward, up,
        auto_cut=True, roles={101: "HF", 102: "MF"}))
    assert transform["cut_planes"] == ["x0", "y0"]
    np.testing.assert_allclose(np.ptp(reduced.points, axis=0), [.1, .1, .3])


def test_record_reimport_setup_and_replay_share_effective_cut(tmp_path):
    from server.jobs.models_api import _file_import_memory, read_setup
    from server.cadlink.roles import prepared_inventory_sha256
    from collections import OrderedDict
    body = cutting_registration(auto_cut=True)
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        prepared = _run_in_gmsh_session(lambda: prepare_file_mesh(full_mesh(), "m", "+z", "+y", auto_cut=True, roles=body.driven_roles()))
        first = register_mesh(store, tmp_path, body, prepared=prepared)
        assert first["symmetry"] == {"domain_planes": ["x0", "y0"], "cut_planes": ["x0", "y0"], "reflected_planes": []}
        assert first["source_file"]["auto_cut"] is True
        assert first["polar_grid_derivation"]["cut_planes"] == ["x0", "y0"]
        assert first["summary"]["dimensions_mm"] == pytest.approx([100, 100, 300])
        assert first["source_tags"] == {"group_101": 101, "group_102": 102}
        assert [source["observed"]["total_area_mm2"] for source in first["sources"]] == pytest.approx([10000, 10000])
        same = register_mesh(store, tmp_path, body, prepared=prepared)
        assert same["ingest_id"] == first["ingest_id"]
        model_id = file_model_id(first["artifact_sha256"])
        setup = {"geometry": {"drive_channels": [{"id": "HF", "source_ids": ["group_101"]}, {"id": "MF", "source_ids": ["group_102"]}]}, "options": {"engine": "metal"}}
        revision = store.create_setup_revision(json.dumps(setup), "sha256:cut-setup")
        store.record_model_setup(model_id, prepared_inventory_sha256(first), revision["revision_id"])
        assert read_setup(store, model_id, first["ingest_id"])["setup"] == setup
        memory = _file_import_memory(store, body.filename, body.msh_text, full_mesh().summary()["groups"], OrderedDict())
        assert memory["known_model"]["latest_prepared_id"] == first["ingest_id"]
        assert memory["previous_setup"]["preparation"]["auto_cut"] is True
        assert memory["previous_setup"]["preparation"]["domain_planes"] == []
        # Explicit full remains available and selects its own immutable record.
        full_body = body.model_copy(update={"auto_cut": False})
        full = register_mesh(store, tmp_path, full_body)
        assert full["symmetry"]["domain_planes"] == []
        assert "auto_cut" not in full["source_file"]
        assert len(full_mesh().triangles) == full["mesh"]["stats"]["triangle_count"]
        # Selecting an older valid spec restores the model's latest pointer.
        assert register_mesh(store, tmp_path, body, prepared=prepared)["ingest_id"] == first["ingest_id"]
        assert store.get_model(model_id)["latest_ingest_id"] == first["ingest_id"]
    finally:
        store.close()


def test_cancelled_child_and_superseded_result_never_publish(tmp_path, monkeypatch):
    import server.jobs.models_api as api
    store = CadLinkStore.for_data_dir(tmp_path)
    body = cutting_registration(auto_cut=True)
    mesh = full_mesh()
    prepared = prepare_file_mesh(mesh, "m", "+z", "+y")
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        async def compute(*_args):
            started.set()
            await release.wait()
            return prepared
        monkeypatch.setattr(api, "prepare_registration", compute)
        cancelled = asyncio.create_task(register_mesh_async(store, tmp_path, body))
        await started.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert store.list_model_ingests(file_model_id(api._sha256(body.msh_text.encode()))) == []
        fence = PreparationFence()
        token = fence.begin("model", 1, "page")
        stale = asyncio.create_task(register_mesh_async(store, tmp_path, body, commit_guard=lambda _conn: fence.current(token)))
        await asyncio.sleep(0)
        fence.begin("model", 2, "page")
        release.set()
        with pytest.raises(StaleAttempt):
            await stale
        assert list((tmp_path / "imports" / "files").glob("wgi_*")) == []
    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_stale_publication_is_refused_inside_existing_store_transaction(tmp_path, monkeypatch):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        guard = threading.Event()
        owner = store.insert_file_ingest
        def supersede(**kwargs):
            guard.set()
            return owner(**kwargs)
        monkeypatch.setattr(store, "insert_file_ingest", supersede)
        with pytest.raises(StaleAttempt):
            register_mesh(store, tmp_path, cutting_registration(), commit_guard=lambda _conn: not guard.is_set())
        assert list((tmp_path / "imports" / "files").glob("wgi_*")) == []
        assert store.ingest_records() == []
    finally:
        store.close()


@pytest.mark.parametrize("newer", ["prepare", "load"])
def test_api_orders_prepare_and_load_before_older_child_publication(tmp_path, monkeypatch, newer):
    import server.jobs.models_api as api
    from test_models_api import app_for
    from test_jobs_api import _request

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        started, release = asyncio.Event(), asyncio.Event()
        owner = api.prepare_registration
        async def compute(body, cancel_cb=None):
            if body.auto_cut:
                started.set()
                await release.wait()
            return await owner(body.model_copy(update={"auto_cut": False}), cancel_cb)
        monkeypatch.setattr(api, "prepare_registration", compute)
        async def call(method, path, body=None, sequence=1):
            async def ordered(scope, receive, send):
                scope["headers"] += [(b"x-wg-preparation-session", b"one-page"),
                                     (b"x-wg-preparation-seq", str(sequence).encode())]
                await app(scope, receive, send)
            path, _, query = path.partition("?")
            status, raw = await _request(ordered, method, path, body=body, query=query)
            return status, json.loads(raw)
        try:
            body = cutting_registration().model_dump(mode="json")
            status, first = await call("POST", "/api/models/files", body)
            assert status == 200
            initial = first["prepared"]
            settings = {key: value for key, value in body.items() if key not in {"filename", "msh_text"}}
            path = f'/api/models/{initial["model_id"]}'
            old = asyncio.create_task(call("POST", path + "/prepare", {**settings, "auto_cut": True}, sequence=2))
            await started.wait()
            if newer == "prepare":
                status, current = await call("POST", path + "/prepare", {**settings, "units": "cm"}, sequence=3)
                assert status == 200, current
                expected = current["prepared"]["ingest_id"]
            else:
                status, current = await call("GET", path + f'?prepared_id={initial["ingest_id"]}', sequence=3)
                assert status == 200, current
                expected = initial["ingest_id"]
            release.set()
            assert (await old)[0] == 409
            assert store.get_model(initial["model_id"])["latest_ingest_id"] == expected
            # An older request delayed by the network cannot supersede a load.
            assert (await call("POST", path + "/prepare", settings, sequence=2))[0] == 409
            assert len(list((tmp_path / "imports" / "files").glob("wgi_*"))) == (2 if newer == "prepare" else 1)
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_postmesh_verification_failure_returns_immutable_input(monkeypatch):
    import server.mesh.file_import as file_import
    owner = file_import._prepare_transformed_file_mesh
    mesh = full_mesh()
    def verify(candidate, *args):
        if len(candidate.triangles) < len(mesh.triangles):
            raise ValueError("injected post-cut verification failure")
        return owner(candidate, *args)
    monkeypatch.setattr(file_import, "_prepare_transformed_file_mesh", verify)
    result, transform = prepare(mesh, auto_cut=True)
    np.testing.assert_array_equal(result.triangles, mesh.triangles)
    np.testing.assert_array_equal(result.points, mesh.points)
    assert transform["domain_planes"] == transform["cut_planes"] == []
    assert "could not be verified" in transform["symmetry_report"]["note"]


def test_native_cut_exports_and_restores_through_shared_replay_and_solve(tmp_path):
    from server.jobs.models_api import register_mesh_async
    from server.jobs.mesh_replay import _inputs, MeshReplayPackage
    from server.mesh.child import close_mesher_child
    from test_mesh_replay import app_for, call
    from test_mesh_import import request

    async def scenario():
        app, store, runtime, engine = app_for(tmp_path / "origin")
        try:
            record = await register_mesh_async(store, app.state.data_dir, cutting_registration(auto_cut=True))
            submission = request(record)
            submission.geometry.drive_channels[0].id = "HF"
            submission.geometry.drive_channels[0].source_ids = ["group_101"]
            submission.geometry.drive_channels.append(submission.geometry.drive_channels[0].model_copy(
                update={"id": "MF", "source_ids": ["group_102"]}))
            submission.options.symmetry = "auto"
            job_id = await runtime.submit(submission)
            await runtime.wait_idle()
            assert runtime.store.get_job_row(job_id)["status"] == "complete"
            package = await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay")
            assert package["registration"]["auto_cut"] is True
            assert "domain_planes" not in package["registration"]
            assert _inputs(MeshReplayPackage.model_validate(package))["registration"]["auto_cut"] is True
            solver = engine.calls[0][1]
        finally:
            await runtime.shutdown()
            store.close()
        app, store, runtime, _ = app_for(tmp_path / "fresh")
        try:
            restored = await call(app, "POST", "/api/mesh-replay", package)
            assert restored["record"]["msh_text"] == solver
            assert restored["record"]["source_file"]["auto_cut"] is True
            assert restored["record"]["symmetry"]["domain_planes"] == ["x0", "y0"]
            assert restored["request"]["geometry"]["drive_channels"] == package["request"]["geometry"]["drive_channels"]
            assert restored["request"]["options"] == package["request"]["options"]
        finally:
            await runtime.shutdown()
            store.close()
            close_mesher_child()
    asyncio.run(scenario())


def test_refused_requested_cut_uses_full_memory_and_polar_domain(tmp_path, monkeypatch):
    import server.jobs.models_api as api
    seen = []
    monkeypatch.setattr(api, "_enforce_dense_solver_memory_ceiling", lambda _triangles, quadrants, **_kwargs: seen.append(quadrants))
    original = full_mesh()
    full, transform = prepare_file_mesh(original, "m", "+z", "+y")
    transform.update(domain_planes=[], cut_planes=[], symmetry_report={"note": "The cut could not be verified. Export the half or quarter to solve faster."})
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, cutting_registration(domain_planes=["x0", "y0"]), prepared=(full, transform))
        from server.contracts.geometry import imported_symmetry_from_cut_planes
        assert seen == [imported_symmetry_from_cut_planes([]).quadrants]
        assert record["symmetry"]["domain_planes"] == []
        assert record["source_file"]["domain_planes"] == ["x0", "y0"]
        assert record["polar_grid_derivation"]["axes"]["horizontal"]["minimum_deg"] == -180
        assert record["polar_grid_derivation"]["axes"]["vertical"]["minimum_deg"] == -180
    finally:
        store.close()


@pytest.mark.parametrize("intent", [{"auto_cut": True}, {"domain_planes": ["x0", "y0"]}])
def test_native_cut_crash_has_a_working_full_model_remedy(tmp_path, monkeypatch, intent):
    import server.jobs.models_api as api
    from server.mesh.child import MesherCrashError
    from test_models_api import app_for, call

    async def fail(*_args, **_kwargs):
        raise MesherCrashError("parametric morph advice must not escape here")
    monkeypatch.setattr(api, "run_mesh_build", fail)
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            refused = await call(app, "POST", "/api/models/files", cutting_registration(**intent).model_dump(mode="json"), expected=422)
            assert "Choose Full model" in refused["detail"]
            assert "morph" not in refused["detail"]
            assert store.ingest_records() == []
            # That remedy uses the supported no-native path for valid full input.
            full = await call(app, "POST", "/api/models/files", cutting_registration().model_dump(mode="json"))
            assert full["prepared"]["symmetry"]["domain_planes"] == []
            assert full["prepared"]["summary"]["triangle_count"] == len(full_mesh().triangles)
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def capped_half_mesh(input_count=48):
    """Refine only the rigid x lid, preserving its boundary and winding."""
    from collections import deque
    from test_file_mesh_roles_symmetry import box_mesh

    mesh = box_mesh()
    mesh.points[:, 1] -= .1
    mesh.tags[mesh.tags == 102] = 101
    mesh.names = {1: "Walls", 101: "HF"}
    lid = np.all(mesh.points[mesh.triangles, 0] == 0, axis=1)
    queue = deque(mesh.triangles[lid].tolist())
    points = mesh.points.tolist()
    for _ in range((input_count - len(mesh.triangles)) // 2):
        a, b, c = queue.popleft()
        centre = len(points)
        points.append(np.asarray([points[a], points[b], points[c]]).mean(axis=0).tolist())
        queue.extend([[a, b, centre], [b, c, centre], [c, a, centre]])
    triangles = np.vstack((mesh.triangles[~lid], np.asarray(queue)))
    tags = np.r_[mesh.tags[~lid], np.ones(len(queue), dtype=int)]
    from server.mesh.file_import import FileMesh
    result = FileMesh(np.asarray(points), triangles, tags, mesh.names)
    assert len(result.triangles) == input_count
    return result


@pytest.mark.parametrize("count,limit", [(48, 40), (5002, 5000)])
def test_original_cut_admission_survives_lid_removal_without_native_entry(tmp_path, monkeypatch, count, limit):
    import gmsh
    import server.jobs.models_api as api
    from server.mesh import file_cut
    from server.mesh.file_import import prepare_file_mesh_input
    from test_models_api import app_for, call

    original = capped_half_mesh(count)
    snapshots = [array.copy() for array in (original.points, original.triangles, original.tags)]
    monkeypatch.setattr(file_cut, "MAX_FILE_CUT_TRIANGLES", limit)
    def native_entry(*_args, **_kwargs):
        pytest.fail("above-bound original must never enter Gmsh/OCC after lid removal")
    async def native_child(*_args, **_kwargs):
        pytest.fail("above-bound original must not dispatch a native child")
    monkeypatch.setattr(gmsh.model, "getCurrent", native_entry)
    monkeypatch.setattr(file_cut, "_load_model", native_entry)
    monkeypatch.setattr(api, "run_mesh_build", native_child)
    body = MeshRegistration(filename="capped-half.msh", msh_text=mesh_text(original), units="m",
                            forward="+z", up="+y", roles={101: "HF"},
                            domain_planes=["x0", "y0"], remove_lid_planes=["x0"])
    preparation = prepare_file_mesh_input(original, "m", "+z", "+y", roles=body.driven_roles(),
                                          domain_planes=body.domain_planes, remove_lid_planes=body.remove_lid_planes)
    assert preparation.input_triangle_count == count
    assert len(preparation.mesh.triangles) == 40
    assert preparation.declared_planes == ("x0",)
    assert preparation.candidate_planes == ("y0",)
    assert not preparation.needs_native_cut
    async def scenario():
        result, transform = await api.prepare_registration(body)
        np.testing.assert_array_equal(result.points, preparation.mesh.points)
        np.testing.assert_array_equal(result.triangles, preparation.mesh.triangles)
        np.testing.assert_array_equal(result.tags, preparation.mesh.tags)
        assert transform["domain_planes"] == ["x0"] and transform["cut_planes"] == []
        note = transform["symmetry_report"]["note"]
        assert f"{count:,} triangles" in note and f"up to {limit:,}" in note
        assert "Export the half or quarter" in note
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            record = (await call(app, "POST", "/api/models/files", body.model_dump(mode="json")))["prepared"]
            assert record["symmetry"]["domain_planes"] == ["x0"]
            assert record["symmetry"]["cut_planes"] == []
            assert record["source_file"]["domain_planes"] == ["x0", "y0"]
            assert record["source_file"]["remove_lid_planes"] == ["x0"]
            assert record["source_file"]["transform"]["symmetry_report"]["note"] == note
            assert record["summary"]["triangle_count"] == 40
            assert record["source_tags"] == {"group_101": 101}
            assert record["sources"][0]["role"] == "HF"
            assert record["sources"][0]["observed"]["total_area_mm2"] == pytest.approx(40000)
            assert record["polar_grid_derivation"]["axes"]["horizontal"]["minimum_deg"] == 0
            assert record["polar_grid_derivation"]["axes"]["vertical"]["minimum_deg"] == -180
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())
    for array, saved in zip((original.points, original.triangles, original.tags), snapshots):
        np.testing.assert_array_equal(array, saved)


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
@pytest.mark.parametrize("auto_cut", [False, True])
@pytest.mark.parametrize("failure", ["startup", "shutdown"])
def test_reduced_registration_and_api_need_no_native_child(tmp_path, monkeypatch, planes, auto_cut, failure):
    import gmsh
    import server.jobs.models_api as api
    from server.mesh.child import MesherChildError, MesherShuttingDownError
    from test_file_mesh_roles_symmetry import box_mesh
    from test_models_api import app_for, call

    # Decisions are made in the transformed solver frame, including negative
    # side normalization, rather than assuming the file's raw x/y axes.
    mesh = box_mesh(planes=planes, negative=("x0",))
    rotation = np.asarray([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
    mesh.points = mesh.points @ rotation * 1000
    body = MeshRegistration(filename="reduced.msh", msh_text=mesh_text(mesh), units="mm",
                            forward="+x", up="+z", roles={101: "HF", 102: "MF"},
                            domain_planes=list(planes), auto_cut=auto_cut)
    expected, transform = prepare_file_mesh(mesh, "mm", "+x", "+z", domain_planes=planes,
                                            auto_cut=auto_cut, roles=body.driven_roles())
    calls = []
    async def unavailable(*_args, **_kwargs):
        calls.append(failure)
        error = MesherChildError if failure == "startup" else MesherShuttingDownError
        raise error("native child must not be needed for an existing reduced mesh")
    monkeypatch.setattr(api, "run_mesh_build", unavailable)
    monkeypatch.setattr(gmsh.model, "getCurrent", lambda: pytest.fail("reduced import touched Gmsh"))
    async def scenario():
        result, actual = await api.prepare_registration(body)
        np.testing.assert_array_equal(result.points, expected.points)
        np.testing.assert_array_equal(result.triangles, expected.triangles)
        np.testing.assert_array_equal(result.tags, expected.tags)
        assert actual == transform
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            first = (await call(app, "POST", "/api/models/files", body.model_dump(mode="json")))["prepared"]
            assert first["symmetry"]["domain_planes"] == list(planes)
            assert first["symmetry"]["cut_planes"] == []
            assert first["symmetry"]["reflected_planes"] == ["x0"]
            assert first["source_tags"] == {"group_101": 101, "group_102": 102}
            settings = body.model_dump(mode="json", exclude={"filename", "msh_text"})
            again = (await call(app, "POST", f'/api/models/{first["model_id"]}/prepare', settings))["prepared"]
            assert again["ingest_id"] == first["ingest_id"]
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())
    assert calls == []
