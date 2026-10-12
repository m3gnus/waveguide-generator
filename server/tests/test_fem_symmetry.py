"""The existing exterior reduction extended to conforming FEM volumes."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from server.cadlink.intake.isolated import build_coupled_mesh_isolated, build_imported_mesh_isolated
from server.mesh.artifact import read_verified_coupled_exterior_mesh, read_verified_fem_region_mesh
from server.mesh.fem_coupling import read_coupled_exterior
from server.mesh.fem_regions import build_fem_region_mesh, physical_name, read_fem_region_domain
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.integrity import mesh_integrity_report
from server.solver.beat_adapter.coupled_request import build_coupled_request, build_imported_coupled_request
from server.solver.beat_adapter.request import _image_frame
from server.solver.coupled_system import ExteriorSurface, FemRegionInput
from server.tests.test_cadlink_wgreturn import _manifest, write_bundle
from server.tests.test_fem_coupling import SIZES
from server.tests.test_fem_engine_route import FRAME, compiled, events
from server.tests.test_fem_ingest import _ingest
from server.tests.test_fem_regions import _face, _region
from server.tests.test_official_beat_bridge import runtime as runtime_fixture
from server.tests.test_fem_coupled_beat_live import worker as live_worker_fixture

runtime = runtime_fixture
worker = live_worker_fixture


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")


def symmetry_bundle(root: Path, planes=("x0", "y0"), *, authored=False, sealed=False, asymmetric_x=False):
    """A centred piston and mouth; an optional y-asymmetric rib permits x only."""
    root.mkdir(parents=True)
    low = np.array([-20., -15., 0.])
    high = np.array([20., 15., 20.])
    if authored:
        for plane in planes:
            low[{"x0": 0, "y0": 1}[plane]] = 0.
    region = _region(mesh_size_mm=10.)
    area = np.prod((high - low)[:2])
    region["surface_groups"][0]["faces"] = [_face(area, (*((low + high)[:2] / 2), 0.),
        [0., 0., 1.], low, [*high[:2], 0.])]
    region["surface_groups"][1]["faces"] = [_face(area, (*((low + high)[:2] / 2), 20.),
        [0., 0., 1.], [*low[:2], 20.], high)]
    if sealed:
        region["surface_groups"] = region["surface_groups"][:1]
    for group in region["surface_groups"]:
        group["mesh_size_mm"] = 8.

    def write_geometry():
        import gmsh

        gmsh.model.add("symmetric-air")
        gmsh.model.occ.addBox(*low, *(high - low))
        gmsh.model.occ.synchronize()
        gmsh.write(str(root / "air.step"))
        gmsh.model.remove()
        gmsh.model.add("symmetric-material")
        material = gmsh.model.occ.addBox(-22, -17, -2, 44, 34, 22)
        cavity = gmsh.model.occ.addBox(-20, -15, 0, 40, 30, 22)
        material, _ = gmsh.model.occ.cut([(3, material)], [(3, cavity)])
        if planes == ("x0",):
            rib = gmsh.model.occ.addBox(-22, 17, -2, 44, 3, 22)
            material, _ = gmsh.model.occ.fuse(material, [(3, rib)])
        if asymmetric_x:
            rib = gmsh.model.occ.addBox(22, -17, -2, 3, 34, 22)
            material, _ = gmsh.model.occ.fuse(material, [(3, rib)])
        if authored:
            clip_low = [-30., -30., -10.]
            for plane in planes:
                clip_low[{"x0": 0, "y0": 1}[plane]] = 0.
            clip = gmsh.model.occ.addBox(*clip_low, *(np.array([30., 30., 30.]) - clip_low))
            material, _ = gmsh.model.occ.intersect(material, [(3, clip)])
            gmsh.model.occ.remove(material, recursive=False)
            gmsh.model.occ.synchronize()
            from server.mesh.fem_regions import symmetry_surfaces
            caps = symmetry_surfaces(gmsh, [t for _, t in gmsh.model.getEntities(2)], planes)
            gmsh.model.occ.remove([(2, t) for t in caps], recursive=True)
        gmsh.model.occ.synchronize()
        gmsh.write(str(root / "material.step"))
        gmsh.model.remove()

    _run_in_gmsh_session(write_geometry)
    air = (root / "air.step").read_bytes()
    material = (root / "material.step").read_bytes()
    manifest = _manifest(material)
    manifest["instances"] = []
    manifest["sources"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["assembly"]["bbox_mm"] = [[-22, -17, -2], [25 if asymmetric_x else 22, 20 if planes == ("x0",) else 17, 20]]
    if authored:
        manifest["required_features"].append("reduced-domain-v1")
        manifest["assembly"]["domain"] = {"kind": "half" if len(planes) == 1 else "quarter",
            "cut_planes": list(planes), "declared_by": "cad-author",
            "evidence": {p: {"min_mm": 0, "max_mm": 22 if p == "x0" else 17,
                              "tolerance_mm": .001} for p in planes}}
        manifest["assembly"]["n_bodies_expected"] = len(_run_in_gmsh_session(_surface_count, root / "material.step"))
        for plane in planes:
            manifest["assembly"]["bbox_mm"][0][{"x0": 0, "y0": 1}[plane]] = 0
    manifest["scope"]["included"] = [{"object_id": "walls", "name": "Box walls",
        "body_kind": "surface" if authored else "solid", "visible": True, "external_reference": "local"}]
    if authored:
        template = manifest["scope"]["included"][0]
        manifest["scope"]["included"] = [{**template, "object_id": f"sheet-{i}", "name": f"Box wall {i}"}
                                         for i in range(manifest["assembly"]["n_bodies_expected"])]
    manifest["scope"]["fem_regions"] = [region]
    manifest["required_features"].append("fem-regions-v1")
    manifest["files"][region["file"]] = {"sha256": "sha256:" + hashlib.sha256(air).hexdigest(),
        "size_bytes": len(air), "media_type": "model/step", "purpose": "fem-region"}
    bundle = write_bundle(root, manifest, step=material)
    member = bundle / region["file"]
    member.parent.mkdir(parents=True)
    member.write_bytes(air)
    return bundle, manifest, region


def _surface_count(path):
    import gmsh
    gmsh.model.add("inventory")
    roots = gmsh.model.occ.importShapes(str(path))
    gmsh.model.remove()
    return roots


def mesh_pair(root, planes, *, geometry_planes=None):
    bundle, manifest, region = symmetry_bundle(root, planes if geometry_planes is None else geometry_planes)
    assembly = bundle / manifest["assembly"]["file"]
    exterior = build_imported_mesh_isolated(assembly, manifest, SIZES,
        options={"symmetry_mode": "full"}, include_viewport_mesh=False)
    built = build_coupled_mesh_isolated(assembly, [region],
        members={region["region_id"]: {"path": str(bundle / region["file"]),
                                       **manifest["files"][region["file"]]}},
        recipe=exterior["viewport_recipe"], expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=exterior["normalisation"]["matrix"], sizes=SIZES, exterior_names={},
        domain_planes=planes, cut_planes=planes)
    return built, region


def inputs(built, region):
    points, groups = read_coupled_exterior(built["exterior"]["msh_text"])
    exterior = ExteriorSurface(points, groups, built["exterior"]["physical_names"])
    domain = read_fem_region_domain(built["regions"][region["region_id"]]["msh_text"], region["region_id"])
    return exterior, FemRegionInput(region, domain)


@pytest.mark.parametrize("planes,symmetry", [(("x0",), "yz"), (("x0", "y0"), "yz+xz")])
def test_half_and_quarter_interface_is_shared_and_skin_open_only_on_planes(tmp_path, planes, symmetry):
    from beat_engine.beat_contract import validate_solve_request
    from server.solver.beat_adapter.results import map_coupled_sweep

    built, region = mesh_pair(tmp_path / "returns", planes)
    exterior, item = inputs(built, region)
    report = mesh_integrity_report(exterior.points_mm * .001,
        np.concatenate(list(exterior.groups.values())), symmetry_plane_axes=tuple({"x0": 0, "y0": 1}[p] for p in planes))
    assert report["open_edge_count"] > 0
    assert report["off_plane_open_edge_count"] == report["nonmanifold_edge_count"] == 0
    cap = physical_name(region["region_id"], "_symmetry", "rigid")
    assert cap in item.domain.surfaces and cap not in exterior.groups
    for plane in planes:
        assert item.domain.points_mm[:, {"x0": 0, "y0": 1}[plane]].min() >= -1e-6
    full_request = compiled(coupled=False)
    request = build_coupled_request(exterior, [item], layout=full_request.layout,
        frame=FRAME, frequencies_hz=[100.], symmetry=symmetry)
    validate_solve_request(request.wire)
    assert request.wire["solver_options"]["symmetry"] == _image_frame(symmetry, None)[0]
    topology = request.wire["compiled_system"]["interfaces"][0]["topology"]
    assert topology["max_coordinate_error"] == 0
    assert set(topology["normal_sign"]) == {1}
    assert topology["fem_facets_on_tetra_boundary"] == len(topology["fem_face_indices"])
    sweep = map_coupled_sweep(events(request), request)
    assert sweep.nodal_pressure.shape == (1, 1, len(item.domain.points_mm))


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
def test_sealed_box_cut_has_rigid_symmetry_and_correct_volume(tmp_path, planes):
    bundle, _, region = symmetry_bundle(tmp_path / "returns", planes, sealed=True)
    built = _run_in_gmsh_session(build_fem_region_mesh, bundle / region["file"], region,
                                 domain_planes=planes, cut_planes=planes)
    domain = read_fem_region_domain(built["msh_text"], region["region_id"])
    xyz = domain.points_mm[domain.tetrahedra]
    volume = np.abs(np.linalg.det(xyz[:, 1:] - xyz[:, :1])).sum() / 6
    assert volume == pytest.approx(40 * 30 * 20 / 2 ** len(planes))
    assert physical_name(region["region_id"], "_symmetry", "rigid") in domain.surfaces


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
def test_ingest_reuses_exterior_symmetry_for_region_artifacts(tmp_path, planes):
    bundle, _, region = symmetry_bundle(tmp_path / "returns", planes)
    record = _ingest(bundle, tmp_path)
    assert record["symmetry"]["domain_planes"] == list(planes)
    points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh(record))
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
    assert physical_name(region["region_id"], "_symmetry", "rigid") in domain.surfaces
    from server.solver.imported import imported_symmetry_from_cut_planes
    quadrants = imported_symmetry_from_cut_planes(planes).quadrants
    # Match the ordinary route's real context and observation construction.
    from server.solver.context import SolverContext
    from server.tests.test_imported_jobs import _request
    job = _request(record["ingest_id"])
    job.geometry.drive_channels = []
    context = SolverContext.from_imported_request(job, quadrants=quadrants, source_motion="normal")
    request = build_imported_coupled_request(record, job.geometry, context, frequencies_hz=[100.],
                                             resolved_quadrants=quadrants)
    assert request.wire["solver_options"]["symmetry"] == ("x" if len(planes) == 1 else "xy")
    assert len(points) and groups


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
def test_author_cut_sealed_region_is_verified_without_halving_again(tmp_path, planes):
    bundle, _, region = symmetry_bundle(tmp_path / "returns", planes, authored=True, sealed=True)
    built = _run_in_gmsh_session(build_fem_region_mesh, bundle / region["file"], region,
                                 domain_planes=planes)
    domain = read_fem_region_domain(built["msh_text"], region["region_id"])
    xyz = domain.points_mm[domain.tetrahedra]
    assert np.abs(np.linalg.det(xyz[:, 1:] - xyz[:, :1])).sum() / 6 == pytest.approx(24000 / 2 ** len(planes))


def test_y_only_image_rotates_fem_and_preserves_display_coordinates():
    request = compiled(coupled=False)
    item = request.regions[0]
    half = build_coupled_request(None, [item], layout=request.layout, frame=FRAME,
                                frequencies_hz=[100.], symmetry="xz")
    mesh = half.wire["compiled_system"]["meshes"][0]["mesh_data"]["points"]
    actual = np.frombuffer(base64.b64decode(mesh["data"]), dtype=mesh["dtype"]).reshape(mesh["shape"])
    _, rotation, _ = _image_frame("xz", None)
    np.testing.assert_allclose(actual, item.domain.points_mm @ rotation.T * .001)
    assert half.regions[0] is item  # The retained display frame stays WG's frame.
    assert half.wire["solver_options"]["symmetry"] == "x"


def test_negative_author_region_and_asymmetric_role_refuse(tmp_path):
    bundle, _, region = symmetry_bundle(tmp_path / "returns")
    with pytest.raises(ValueError, match="outside the positive author-cut domain"):
        _run_in_gmsh_session(build_fem_region_mesh, bundle / region["file"], region, domain_planes=("x0",))
    changed = deepcopy(region)
    changed["surface_groups"][0]["faces"][0]["centroid_mm"][0] = 5
    with pytest.raises(ValueError, match="matches no surface"):
        _run_in_gmsh_session(build_fem_region_mesh, bundle / region["file"], changed,
                            domain_planes=("x0",), cut_planes=("x0",))
    changed = deepcopy(region)
    changed["surface_groups"][0]["faces"] = [_face(
        600, [20, 0, 10], [1, 0, 0], [20, -15, 0], [20, 15, 20])]
    with pytest.raises(ValueError, match="does not mirror on x0"):
        _run_in_gmsh_session(build_fem_region_mesh, bundle / region["file"], changed,
                            domain_planes=("x0",), cut_planes=("x0",))


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
@pytest.mark.parametrize("sealed", [False, True], ids=["coupled", "interior"])
def test_fake_worker_job_retains_half_field_and_displays_completed_field(tmp_path, runtime, planes, sealed):
    import asyncio
    from io import BytesIO
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore
    from server.jobs.fem_field import load_fem_field_payload
    from server.tests.test_imported_jobs import _request
    from server.tests.test_jobs_store import _job
    from server.tests.beat_runtime.fake_host_worker import coupled_result

    bundle, _, region = symmetry_bundle(tmp_path / "returns", planes, sealed=sealed)
    record = _ingest(bundle, tmp_path)
    request = _request(record["ingest_id"], drive_channels=[], mesh=SIZES)
    request.options.engine = "beat-cpu"
    request.options.frequencies_hz = [100.]
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("symmetric", "running"))
    runner = JobRuntime(store)

    def mutate(raw):
        wire = runtime.requests[-1]
        raw.clear()
        raw.update(coupled_result(wire, 100.))
    runtime.mutate = mutate
    with pytest.raises(ValueError, match="disagree on symmetry"):
        asyncio.run(runner._run_coupled_regions("symmetric", request, record, lambda *args: None,
                                              {"resolved_quadrants": 1234}))
    assert not runtime.requests  # Reject before starting the native worker.
    result = asyncio.run(runner._run_coupled_regions("symmetric", request, record, lambda *args: None,
                                                   {"resolved_quadrants": 14 if len(planes) == 1 else 1}))
    assert result.results["channel_order"] == ["drive-lf"]
    wire = runtime.requests[-1]
    assert wire["solver_options"]["symmetry"] == ("x" if len(planes) == 1 else "xy")
    with np.load(BytesIO(store.get_fem_field("symmetric")), allow_pickle=False) as arrays:
        assert arrays["symmetry_planes"].tolist() == list(planes)
        half_points = arrays["points_mm_0"]
        half_tets = arrays["tetrahedra_0"]
        half_pressure = arrays["pressure"][0, 0]
    shown = load_fem_field_payload(store._fem_field_path("symmetric"), 100., "drive-lf")
    xyz = np.frombuffer(base64.b64decode(shown["points_mm"]), dtype="<f4").reshape(-1, 3)
    tets = np.frombuffer(base64.b64decode(shown["tetrahedra"]), dtype="<u4").reshape(-1, 4)
    multiplier = 2 ** len(planes)
    assert len(xyz) == multiplier * len(half_points)
    assert len(tets) == multiplier * len(half_tets)
    assert all("group=_symmetry|" not in surface["name"] for surface in shown["surfaces"])
    real = np.frombuffer(base64.b64decode(shown["pressure_real"]), dtype="<f4")
    imag = np.frombuffer(base64.b64decode(shown["pressure_imag"]), dtype="<f4")
    np.testing.assert_allclose(real + 1j * imag, np.tile(half_pressure, multiplier))
    det = np.linalg.det(xyz[tets][:, 1:] - xyz[tets][:, :1])
    half_det = np.linalg.det(half_points[half_tets][:, 1:] - half_points[half_tets][:, :1])
    np.testing.assert_allclose(det, np.tile(half_det, multiplier), rtol=1e-5, atol=1e-3)
    assert np.abs(det).sum() / 6 == pytest.approx(24000, rel=1e-5)
    for plane in planes:
        axis = {"x0": 0, "y0": 1}[plane]
        assert xyz[:, axis].min() < 0 < xyz[:, axis].max()
    assert region["region_id"] == "rear-chamber"


@pytest.mark.parametrize("planes,symmetry", [(("x0",), "yz"), (("x0", "y0"), "yz+xz"), (("y0",), "xz")])
def test_coupled_request_matches_exterior_image_options_and_observations(tmp_path, planes, symmetry):
    from server.solver.beat_adapter.request import build_request, SourceBasis

    built, region = mesh_pair(tmp_path / "returns", planes)
    exterior, item = inputs(built, region)
    layout = compiled(coupled=False).layout
    coupled = build_coupled_request(exterior, [item], layout=layout, frame=FRAME,
                                    frequencies_hz=[100.], symmetry=symmetry)
    name = physical_name(region["region_id"], "port", "interface")
    source = SourceBasis("port", built["exterior"]["physical_names"][name]["tag"], port_id="port")
    ordinary = build_request(built["exterior"]["msh_text"], sources=[source],
        channel_ports={"drive-lf": ["port"]}, layout=layout, frame=FRAME, frequencies_hz=[100.],
        symmetry=symmetry, precision="float64", mesh_scale_to_m=.001)
    for key in ("symmetry", "phasor_convention", "precision", "bem_backend",
                "regular_quadrature_mode", "quadrature_order", "singular_order"):
        assert coupled.wire["solver_options"][key] == ordinary.wire["solver_options"][key]
    exterior_outputs = [o for o in coupled.wire["outputs"] if o["quantity"] == "exterior_pressure"]
    assert exterior_outputs == [o for o in ordinary.wire["outputs"] if o["quantity"] == "exterior_pressure"]
    radiation_outputs = [o for o in coupled.wire["outputs"] if o["quantity"] == "interface_radiated_pressure"]
    assert [o["options"] for o in radiation_outputs] == [
        o["options"] for o in exterior_outputs if o["id"] != "pressure:sphere"]
    from server.solver.beat_adapter.results import map_coupled_sweep
    from server.solver.result_mapping import map_coupled_port_response
    sweep = map_coupled_sweep(events(coupled), coupled)
    response = {"frequencies": [100.], "metadata": {}}
    map_coupled_port_response(response, sweep, coupled, next(iter(coupled.channel_ports)))
    ports = response["metadata"]["fem_ports"]
    multiplier = 2 ** len(planes)
    assert ports["radiation_symmetry"] == "includes_symmetry_images"
    assert ports["represented_area_scope"] == "fundamental_domain_patch"
    for port in ports["interfaces"]:
        assert port["symmetry_multiplier"] == multiplier
        assert port["full_model_area_m2"] == pytest.approx(port["represented_area_m2"] * multiplier)


@pytest.mark.parametrize("planes", [("x0",), ("x0", "y0")])
def test_author_cut_coupled_ingest(tmp_path, planes):
    bundle, _, region = symmetry_bundle(tmp_path / "returns", planes, authored=True)
    record = _ingest(bundle, tmp_path)
    assert record["symmetry"]["domain_planes"] == list(planes)
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
    xyz = domain.points_mm[domain.tetrahedra]
    assert np.abs(np.linalg.det(xyz[:, 1:] - xyz[:, :1])).sum() / 6 == pytest.approx(24000 / 2 ** len(planes))


# Only requested explicitly through the compute broker; no live worker is used
# during ordinary targeted checks.
@pytest.mark.live
@pytest.mark.slow
@pytest.mark.real_runtime
@pytest.mark.skipif(__import__("os").environ.get("WG2_RUN_LIVE") != "1", reason="set WG2_RUN_LIVE=1")
@pytest.mark.parametrize("planes,symmetry", [(("x0",), "yz"), (("x0", "y0"), "yz+xz")])
def test_live_full_and_reduced_box_far_field_agree_within_one_percent(tmp_path, worker, planes, symmetry):
    from types import SimpleNamespace
    from server.solver.official_beat import solve_coupled_compiled

    layout = compiled(coupled=False).layout
    requests = []
    for label, cuts, mode in (("full", (), "full"), ("reduced", planes, symmetry)):
        built, region = mesh_pair(tmp_path / label, cuts, geometry_planes=("x0", "y0"))
        exterior, item = inputs(built, region)
        requests.append(build_coupled_request(exterior, [item], layout=layout, frame=FRAME,
                                              frequencies_hz=[100.], symmetry=mode))
    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    pressures = [solve_coupled_compiled(request, worker_manager=manager).channels["drive-lf"].pressure_complex
                 for request in requests]
    assert np.all(np.abs(pressures[0]) > 0)
    np.testing.assert_allclose(pressures[1], pressures[0], rtol=.01, atol=0)


def test_reduced_coupling_meshes_sealed_neighbour_in_the_same_session(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest

    bundle, manifest, region = symmetry_bundle(tmp_path / "returns")
    sealed = deepcopy(region)
    sealed.update(region_id="sealed", name="Sealed neighbour", object_id="air-2",
                  file="fem-regions/sealed.step")
    sealed["surface_groups"] = sealed["surface_groups"][:1]
    sealed["surface_groups"][0]["drive_channel_id"] = "drive-sealed"
    for face in sealed["surface_groups"][0]["faces"]:
        face["centroid_mm"][2] += 100
        face["bbox_mm"][0][2] += 100
        face["bbox_mm"][1][2] += 100

    def write_sealed():
        import gmsh
        gmsh.model.add("sealed-neighbour")
        gmsh.model.occ.addBox(-20, -15, 100, 40, 30, 20)
        gmsh.model.occ.synchronize()
        gmsh.write(str(bundle / sealed["file"]))
        gmsh.model.remove()
    _run_in_gmsh_session(write_sealed)
    data = (bundle / sealed["file"]).read_bytes()
    manifest["scope"]["fem_regions"].append(sealed)
    manifest["files"][sealed["file"]] = {"sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data), "media_type": "model/step", "purpose": "fem-region"}
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", lambda *args, **kwargs:
                        pytest.fail("Every reduced region must come from the coupled OCC session"))
    record = _ingest(bundle, tmp_path)
    assert len(record["fem_regions"]) == 2
    for name in (region["region_id"], "sealed"):
        domain = read_fem_region_domain(read_verified_fem_region_mesh(record, name), name)
        assert physical_name(name, "_symmetry", "rigid") in domain.surfaces
        assert domain.points_mm[:, :2].min() >= -1e-6


def test_request_rejects_leaks_and_exterior_plane_caps(tmp_path):
    from dataclasses import replace

    built, region = mesh_pair(tmp_path / "returns", ("x0", "y0"))
    exterior, item = inputs(built, region)
    layout = compiled(coupled=False).layout
    port = physical_name(region["region_id"], "port", "interface")
    groups = dict(exterior.groups)
    groups[port] = groups[port][1:]
    with pytest.raises(ValueError, match="open edges away from symmetry planes"):
        build_coupled_request(replace(exterior, groups=groups), [item], layout=layout,
                              frame=FRAME, frequencies_hz=[100.], symmetry="yz+xz")
    caps = physical_name(region["region_id"], "_symmetry", "rigid")
    # Use the FEM node order for all surfaces, preserving each interface face.
    cap_exterior = ExteriorSurface(item.domain.points_mm, item.domain.surfaces,
        {name: {"kind": "interface" if name == port else "rigid"} for name in item.domain.surfaces})
    assert caps in cap_exterior.groups
    with pytest.raises(ValueError, match="without plane caps"):
        build_coupled_request(cap_exterior, [item], layout=layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry="yz+xz")



def test_symmetry_snap_uses_union_extent_for_shared_interface_vertex():
    from dataclasses import replace
    from server.tests.test_fem_engine_route import region_input
    item = region_input(interface=True)
    points = item.domain.points_mm.copy()
    points[[0, 2, 3], 0] = 1e-4
    item = replace(item, domain=replace(item.domain, points_mm=points))
    # The interface's vertices are shared; the far BEM tip is 1 m away,
    # while the region is only 17 mm across its diagonal.
    exterior_points = points.copy()
    exterior_points[2, 1] = 1000.
    groups = {name: faces[:1] if "walls" in name else faces
              for name, faces in item.domain.surfaces.items()}
    exterior = ExteriorSurface(exterior_points, groups, {
        name: {"kind": "interface" if "interface" in name else "rigid"}
        for name in item.domain.surfaces})
    request = build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], symmetry="yz")
    topology = request.wire["compiled_system"]["interfaces"][0]["topology"]
    assert topology["max_coordinate_error"] == 0.
    assert request.regions[0] is item


@pytest.mark.parametrize("collapsed", ["exterior_triangle", "region_triangle", "tetrahedron"])
def test_symmetry_snap_refuses_collapsed_cells(collapsed):
    from dataclasses import replace
    from server.tests.test_fem_engine_route import region_input
    item = region_input()
    exterior = None
    points = item.domain.points_mm.copy()
    points[1, 0] = 1e-8
    if collapsed == "exterior_triangle":
        exterior = ExteriorSurface(points, item.domain.surfaces, {
            name: {"kind": "rigid"} for name in item.domain.surfaces})
    elif collapsed == "region_triangle":
        item = replace(item, domain=replace(item.domain, points_mm=points))
    else:
        # All four vertices are distinct, all surface triangles survive,
        # but snapping the fourth vertex into x=0 flattens the tetrahedron.
        points = np.array([[0., 0., 0.], [0., 10., 0.], [0., 0., 10.], [1e-8, 2., 2.]])
        item = replace(item, domain=replace(item.domain, points_mm=points))
    with pytest.raises(ValueError, match="Symmetry snapping collapsed a " +
                       ("tetrahedron" if collapsed == "tetrahedron" else "triangle")):
        build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry="yz")


def test_direct_builder_compares_record_to_stored_job_symmetry(monkeypatch):
    from types import SimpleNamespace
    from server.solver.context import SolverContext
    from server.tests.test_fem_engine_route import region_input
    region = region_input()
    record = {"fem_regions": [region.record], "symmetry": {"domain_planes": ["x0"]}}
    geometry = SimpleNamespace(drive_channels=[], combine=None)
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2, quadrants=14)
    monkeypatch.setattr("server.solver.beat_adapter.coupled_request.read_verified_fem_region_mesh",
                        lambda *args: pytest.fail("Stale stored symmetry must be refused before loading meshes"))
    with pytest.raises(ValueError, match="disagree on symmetry"):
        build_imported_coupled_request(record, geometry, context, frequencies_hz=[100.], resolved_quadrants=1234)


@pytest.mark.parametrize("sealed", [False, True], ids=["coupled", "interior"])
@pytest.mark.parametrize("failed_plane", ["x0", "y0"])
def test_auto_fem_role_asymmetry_drops_only_unsupported_candidate_planes(tmp_path, sealed, failed_plane):
    bundle, manifest, region = symmetry_bundle(tmp_path / "returns", sealed=sealed)
    # Geometry mirrors on both planes, but the driver on just one side wall
    # does not. FEM role matching must participate in AUTO's plane selection.
    region["surface_groups"][0]["faces"] = [_face(
        600, [20, 0, 10], [1, 0, 0], [20, -15, 0], [20, 15, 20])
        if failed_plane == "x0" else _face(
        800, [0, 15, 10], [0, 1, 0], [-20, 15, 0], [20, 15, 20])]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    record = _ingest(bundle, tmp_path)
    expected = [] if failed_plane == "x0" else ["x0"]
    assert record["symmetry"]["domain_planes"] == expected
    assert record["domain_decision"]["solver_domain"]["planes"] == expected
    assert record["domain_decision"]["refusal"] is None
    from server.cadlink.interpretation.domain_decision import decision_problem
    assert decision_problem(record) is None
    notes = [f for f in record["findings"] if f["kind"] == "fem-symmetry-fallback"]
    assert notes[0]["plane"] == failed_plane
    assert f"does not mirror on {failed_plane}" in notes[0]["reason"]
    assert all(not note["blocking"] for note in notes)
    if failed_plane == "x0":
        assert notes[1]["plane"] == "y0"
        assert "y0-only FEM" in notes[1]["reason"]
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
    xyz = domain.points_mm[domain.tetrahedra]
    assert np.abs(np.linalg.det(xyz[:, 1:] - xyz[:, :1])).sum() / 6 == pytest.approx(24000 / 2 ** len(expected))


def test_author_cut_y_only_is_refused_at_ingest(tmp_path):
    bundle, _, _ = symmetry_bundle(tmp_path / "returns", ("y0",), authored=True, asymmetric_x=True)
    with pytest.raises(ValueError, match="y0-only FEM returns cannot be solved by BEAT[.]"):
        _ingest(bundle, tmp_path)


def test_negative_author_cut_fem_return_has_accurate_refusal(tmp_path):
    bundle, manifest, region = symmetry_bundle(tmp_path / "returns", ("x0",), authored=True)
    # Reflect both actual STEP members and their declared face evidence.
    def reflect_step(path):
        import gmsh
        gmsh.model.add("negative-author-cut")
        roots = gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.mirror(roots, 1., 0., 0., 0.)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.model.remove()
    for path in (bundle / manifest["assembly"]["file"], bundle / region["file"]):
        _run_in_gmsh_session(reflect_step, path)
        data = path.read_bytes()
        member = manifest["files"][path.relative_to(bundle).as_posix()]
        member.update(sha256="sha256:" + hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    for group in region["surface_groups"]:
        for face in group["faces"]:
            face["centroid_mm"][0] *= -1
            face["normal"][0] *= -1
            lo, hi = face["bbox_mm"]
            lo[0], hi[0] = -hi[0], -lo[0]
    manifest["assembly"]["bbox_mm"][0][0] = -22
    manifest["assembly"]["bbox_mm"][1][0] = 0
    from server.tests.test_cadlink_domain_automatic import provenance
    manifest["required_features"].remove("reduced-domain-v1")
    manifest["required_features"].append("domain-automatic-v1")
    manifest["assembly"]["domain"] = {"kind": "automatic"}
    manifest["assembly"]["cut_provenance"] = [
        provenance(body["object_id"], kept_side="negative") for body in manifest["scope"]["included"]]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="author-cut model keeps the negative side; FEM regions need the positive side"):
        _ingest(bundle, tmp_path)


@pytest.mark.parametrize("sealed", [False, True], ids=["coupled", "interior"])
def test_author_cut_fem_fundamental_domain_failure_never_falls_back(tmp_path, sealed):
    bundle, manifest, region = symmetry_bundle(tmp_path / "author", ("x0",), authored=True, sealed=sealed)
    full, _, full_region = symmetry_bundle(tmp_path / "full", sealed=sealed)
    data = (full / full_region["file"]).read_bytes()
    (bundle / region["file"]).write_bytes(data)
    region["surface_groups"] = full_region["surface_groups"]
    manifest["files"][region["file"]].update(
        sha256="sha256:" + hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="outside the positive author-cut domain on x0"):
        _ingest(bundle, tmp_path)



def test_author_y_cut_with_auto_x_mirror_keeps_supported_quarter(tmp_path):
    bundle, _, _ = symmetry_bundle(tmp_path / "returns", ("y0",), authored=True)
    record = _ingest(bundle, tmp_path)
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["domain_decision"]["solver_domain"]["planes"] == ["x0", "y0"]


def test_union_snap_cap_preserves_genuine_small_vertex_in_metre_mesh():
    from dataclasses import replace
    from server.tests.test_fem_engine_route import region_input
    item = region_input()
    # Before the cap this union tolerance exceeds .0005 mm and flips the
    # determinant from -0.03 to +0.02, although neither element collapses.
    points = np.array([[.002, 10., 10.], [.002, 10., 20.], [.012, 20., 10.], [.0005, 8.2, 10.4]])
    item = replace(item, domain=replace(item.domain, points_mm=points))
    far = region_input('metre', 'far')
    far = replace(far, domain=replace(far.domain, points_mm=far.domain.points_mm * 100.))
    request = build_coupled_request(None, [item, far], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], symmetry='yz')
    packed = request.wire['compiled_system']['meshes'][0]['mesh_data']['points']
    compiled_points = np.frombuffer(base64.b64decode(packed['data']), dtype=packed['dtype']).reshape(packed['shape']) * 1000
    assert compiled_points[3, 0] == pytest.approx(.0005)
    assert np.linalg.det(compiled_points[1:] - compiled_points[:1]) == pytest.approx(-.03)


@pytest.mark.parametrize('element', ['triangle', 'tetrahedron'])
def test_snap_refuses_orientation_flip_and_names_mesh_element(element):
    from dataclasses import replace
    from server.tests.test_fem_engine_route import region_input
    item = region_input('flip')
    points = np.array([[.0002, 1., 1.], [.0012, 2., 1.], [.0002, 1., 2.], [.00005, .83, 1.04]])
    if element == 'triangle':
        points[3, 2] = 1.
    item = replace(item, domain=replace(item.domain, points_mm=points))
    far = region_input('metre', 'far')
    far = replace(far, domain=replace(far.domain, points_mm=far.domain.points_mm * 100.))
    with pytest.raises(ValueError, match=f"Symmetry snapping flipped a {element}: mesh 'flip'.*element"):
        build_coupled_request(None, [item, far], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry='yz')


def test_auto_full_fallback_keeps_rotated_translated_standalone_chamber_frame(tmp_path):
    from server.cadlink.intake import ingest
    from server.cadlink.store import CadLinkStore
    from server.tests.test_cadlink_wgreturn import _manifest
    from server.solver.imported import imported_anchor_frame
    bundle, manifest, region = symmetry_bundle(tmp_path / 'returns', sealed=True)
    region['surface_groups'][0]['faces'] = [_face(
        600, [20, 0, 10], [1, 0, 0], [20, -15, 0], [20, 15, 20])]
    transform = np.array([[0., 0., 1., 100.], [0., 1., 0., 200.],
                          [-1., 0., 0., 300.], [0., 0., 0., 1.]])
    rotation, translation = transform[:3, :3], transform[:3, 3]

    def place(path):
        import gmsh
        from server.mesh.imported import apply_rigid_normalisation
        gmsh.model.add('placed-return')
        roots = gmsh.model.occ.importShapes(str(path))
        apply_rigid_normalisation(gmsh, roots, transform)
        gmsh.write(str(path))
        gmsh.model.remove()
    for member in ('assembly.step', region['file']):
        _run_in_gmsh_session(place, bundle / member)
        data = (bundle / member).read_bytes()
        manifest['files'][member].update(sha256='sha256:' + hashlib.sha256(data).hexdigest(),
                                         size_bytes=len(data))
    for group in region['surface_groups']:
        for face in group['faces']:
            face['centroid_mm'] = (rotation @ face['centroid_mm'] + translation).tolist()
            face['normal'] = (rotation @ face['normal']).tolist()
            corners = np.array([rotation @ [x, y, z] + translation
                                for x in (face['bbox_mm'][0][0], face['bbox_mm'][1][0])
                                for y in (face['bbox_mm'][0][1], face['bbox_mm'][1][1])
                                for z in (face['bbox_mm'][0][2], face['bbox_mm'][1][2])])
            face['bbox_mm'] = [corners.min(axis=0).tolist(), corners.max(axis=0).tolist()]
    bounds = np.array([rotation @ [x, y, z] + translation
                       for x in (-22, 22) for y in (-17, 17) for z in (-2, 20)])
    manifest['assembly']['bbox_mm'] = [bounds.min(axis=0).tolist(), bounds.max(axis=0).tolist()]
    instance = _manifest(b'')['instances'][0]
    instance['assembly_from_link'] = transform.tolist()
    manifest['instances'] = [instance]
    manifest['coordinate_system']['solver_anchor_instance_id'] = instance['instance_id']
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    store = CadLinkStore(tmp_path / 'cadlink.db')
    try:
        record = ingest.ingest_bundle(bundle, SIZES, [], store, tmp_path / 'data',
                                     expected_design_id=instance['design_id'], defer_viewport=True)
    finally:
        store.close()
    assert record['symmetry']['domain_planes'] == []
    assert len([f for f in record['findings'] if f['kind'] == 'fem-symmetry-fallback']) == 2
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region['region_id']), region['region_id'])
    np.testing.assert_allclose(domain.points_mm.min(axis=0), [-20., -15., 0.], atol=1e-7)
    np.testing.assert_allclose(domain.points_mm.max(axis=0), [20., 15., 20.], atol=1e-7)
    np.testing.assert_allclose(record['fem_regions'][0]['normalisation_matrix'], record['normalisation']['matrix'])
    frame = imported_anchor_frame(record)
    np.testing.assert_allclose(frame['axis'], [0., 0., 1.], atol=1e-12)
    np.testing.assert_allclose(frame['origin'], [0., 0., 0.], atol=1e-12)


def test_auto_fallback_uses_typed_plane_and_resets_deadline(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    bundle, _, _ = symmetry_bundle(tmp_path / 'returns', sealed=True)
    original = ingest._mesh_coupled_regions
    deadlines = []

    def mesh(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        if len(deadlines) == 1:
            # Neither an English mirror message nor the y0-only marker appears.
            raise ingest.IngestRefusal('stage 7 meshing', 'Localized geometry verdict', symmetry_plane='x0')
        return original(*args, **kwargs)
    monkeypatch.setattr(ingest, '_mesh_coupled_regions', mesh)
    record = _ingest(bundle, tmp_path)
    assert record['symmetry']['domain_planes'] == []
    assert len(deadlines) == 3
    assert deadlines[0] < deadlines[1] < deadlines[2]
    notes = [f for f in record['findings'] if f['kind'] == 'fem-symmetry-fallback']
    assert notes[0]['plane'] == 'x0' and 'Localized geometry verdict' in notes[0]['reason']


def test_null_symmetry_is_safe_during_fem_ingest(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    from server.tests.test_fem_ingest import _bundle
    original = ingest.build_imported_mesh_isolated

    def mesh(*args, **kwargs):
        result = original(*args, **kwargs)
        result['symmetry'] = None
        return result
    monkeypatch.setattr(ingest, 'build_imported_mesh_isolated', mesh)
    record = _ingest(_bundle(tmp_path / 'returns'), tmp_path)
    assert record['fem_regions']


def test_y_only_refusal_names_supported_export_remedy():
    from server.cadlink.intake import ingest
    from server.tests.test_fem_regions import _region
    with pytest.raises(ingest.IngestRefusal) as raised:
        ingest._refuse_fem_domain({'scope': {'fem_regions': [_region()]}}, {}, ['y0'])
    message = str(raised.value)
    assert 'Export without the y0 cut' in message and 'keep the x0 cut' in message
    assert 'Force full domain' not in message
    assert raised.value.symmetry_plane == 'y0'


def test_auto_does_not_fallback_for_untyped_mirror_message(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    bundle, _, _ = symmetry_bundle(tmp_path / 'returns', sealed=True)

    def fail(*args, **kwargs):
        raise ingest.IngestRefusal('stage 7 meshing', "FEM region 'rear-chamber' does not mirror on x0: parser failure")
    monkeypatch.setattr(ingest, '_mesh_coupled_regions', fail)
    with pytest.raises(ingest.IngestRefusal, match='parser failure'):
        _ingest(bundle, tmp_path)
