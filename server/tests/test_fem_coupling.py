"""OCC closure of synthetic ported boxes, including a finite front baffle."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from server.cadlink.intake.isolation import ChildRefusal
from server.cadlink.intake.isolated import build_coupled_mesh_isolated, build_imported_mesh_isolated
from server.mesh.artifact import (
    ImportedMeshArtifactError,
    read_verified_coupled_exterior_mesh,
    read_verified_fem_region_mesh,
    read_verified_import_mesh,
)
from server.mesh.fem_coupling import read_coupled_exterior
from server.mesh.fem_regions import FemRegionMeshError, physical_name, read_fem_region_domain
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.solver.coupled_system import (
    ExteriorSurface,
    FemRegionInput,
    build_coupled_system,
    interface_topology,
    open_edges,
    orient_closed_surface,
)
from server.tests.test_cadlink_wgreturn import _manifest, write_bundle
from server.tests.test_fem_ingest import _ingest
from server.tests.test_fem_regions import _region, _write_step

SIZES = {"rigid_size_mm": 12, "transition_mm": 12, "source_size_mm": {}}


def _write_assembly(path: Path, *, baffle=False, missing_wall=False, second_box=False) -> Path:
    def build():
        import gmsh

        gmsh.model.add("ported-box")
        try:
            outer = gmsh.model.occ.addBox(-2, -2, -2, 42, 34, 24)
            cavity = gmsh.model.occ.addBox(0, 0, 0, 42, 30, 20)
            material, _ = gmsh.model.occ.cut([(3, outer)], [(3, cavity)])
            if baffle:
                plate = gmsh.model.occ.addBox(38, -15, -15, 2, 60, 50)
                bore = gmsh.model.occ.addBox(37, 0, 0, 4, 30, 20)
                plate, _ = gmsh.model.occ.cut([(3, plate)], [(3, bore)])
                material, _ = gmsh.model.occ.fuse(material, plate)
            if missing_wall:
                gap = gmsh.model.occ.addBox(10, 5, -3, 10, 10, 4)
                material, _ = gmsh.model.occ.cut(material, [(3, gap)])
            if second_box:
                second = gmsh.model.occ.copy(material)
                gmsh.model.occ.translate(second, 100, 0, 0)
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
        finally:
            gmsh.model.remove()

    _run_in_gmsh_session(build)
    return path


def _coupling_bundle(root: Path, **geometry):
    root.mkdir(parents=True)
    assembly = _write_assembly(root / "material.step", **geometry).read_bytes()
    air = _write_step(root / "air.step").read_bytes()
    region = _region(mesh_size_mm=10)
    for group in region["surface_groups"]:
        group["mesh_size_mm"] = 8
    manifest = _manifest(assembly)
    manifest["instances"] = []
    manifest["sources"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["assembly"]["bbox_mm"] = (
        [[-2, -15, -15], [40, 45, 35]] if geometry.get("baffle") else [[-2, -2, -2], [40, 32, 22]]
    )
    manifest["scope"]["included"] = [
        {
            "object_id": "walls",
            "name": "Box walls",
            "body_kind": "solid",
            "visible": True,
            "external_reference": "local",
        }
    ]
    if geometry.get("second_box"):
        manifest["assembly"]["n_bodies_expected"] = 2
        manifest["assembly"]["bbox_mm"][1][0] = 140
        manifest["scope"]["included"].append(
            {
                "object_id": "walls-2",
                "name": "Second box walls",
                "body_kind": "solid",
                "visible": True,
                "external_reference": "local",
            }
        )
    manifest["scope"]["fem_regions"] = [region]
    manifest["required_features"].append("fem-regions-v1")
    manifest["files"][region["file"]] = {
        "sha256": "sha256:" + hashlib.sha256(air).hexdigest(),
        "size_bytes": len(air),
        "media_type": "model/step",
        "purpose": "fem-region",
    }
    bundle = write_bundle(root, manifest, step=assembly)
    path = bundle / region["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(air)
    return bundle, manifest, region


def _coupled(
    bundle, manifest, region, *, mutate_recipe=None, normalisation=None, options=None, source=False
):
    assembly = bundle / manifest["assembly"]["file"]
    exterior = build_imported_mesh_isolated(
        assembly, manifest, SIZES, options=options, include_viewport_mesh=False
    )
    recipe = deepcopy(exterior["viewport_recipe"])
    if mutate_recipe:
        mutate_recipe(recipe)
    member = manifest["files"][region["file"]]
    names = {}
    if source:
        index = next(
            i
            for i, (center, _area) in enumerate(recipe["surface_order_reference"])
            if np.allclose(center, [19, 15, -2])
        )
        names["wg-import-v1|source=outer-cone|role=LF"] = {
            "indices": [index],
            "size_mm": 8,
            "kind": {"kind": "moving", "drive_channel_id": "drive-LF"},
        }
    return build_coupled_mesh_isolated(
        assembly,
        [region],
        members={region["region_id"]: {"path": str(bundle / region["file"]), **member}},
        recipe=recipe,
        expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=normalisation
        if normalisation is not None
        else exterior["normalisation"]["matrix"],
        sizes=SIZES,
        exterior_names=names,
    )


def _system(built, region):
    surface = built["exterior"]
    points, groups = read_coupled_exterior(surface["msh_text"])
    exterior = ExteriorSurface(points, groups, surface["physical_names"])
    domain = read_fem_region_domain(
        built["regions"][region["region_id"]]["msh_text"], region["region_id"]
    )
    triangles = np.concatenate(list(groups.values()))
    assert open_edges(triangles) == surface["stats"]["open_edges"] == 0
    assert np.array_equal(triangles, orient_closed_surface(points, triangles, into_body=False))
    for group in region["surface_groups"]:
        if group["kind"] != "interface":
            continue
        name = physical_name(region["region_id"], group["group_id"], "interface")
        fem, bem = domain.surfaces[name], groups[name]
        topology = interface_topology(
            domain.points_mm,
            domain.tetrahedra,
            fem,
            np.arange(len(fem)),
            points,
            bem,
            np.arange(len(bem)),
        )
        assert topology["max_coordinate_error"] == 0
        assert topology["normal_sign"] == [1] * len(fem)
        assert topology["fem_facets_on_tetra_boundary"] == len(fem)
    system = build_coupled_system(exterior, [FemRegionInput(region, domain)])
    from beat_engine.beat_contract import validate_compiled_system

    validate_compiled_system(system)
    return system, domain


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")


@pytest.mark.parametrize("baffle", [False, True], ids=["box-with-port", "box-in-finite-baffle"])
def test_closed_shared_mesh_is_accepted_by_the_coupled_builder(tmp_path, baffle):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns", baffle=baffle)
    built = _coupled(bundle, manifest, region)
    _system(built, region)
    assert (
        built["regions"][region["region_id"]]["normalisation_matrix"]
        == built["exterior"]["normalisation_matrix"]
    )


def test_real_ingest_retains_separate_verified_coupled_skin(tmp_path, monkeypatch):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    from server.cadlink.intake import ingest

    calls = []
    original_coupled = ingest.build_coupled_mesh_isolated
    original_regions = ingest._mesh_fem_regions
    deadlines = []

    def coupled_call(*args, **kwargs):
        calls.append("coupled")
        assert kwargs["deadline"] > ingest.time.monotonic()
        deadlines.append(kwargs["deadline"])
        return original_coupled(*args, **kwargs)

    def retain_regions(*args, **kwargs):
        assert kwargs["deadline"] == deadlines[0]
        return original_regions(*args, **kwargs)

    monkeypatch.setattr(ingest, "_mesh_fem_regions", retain_regions)

    def independent(*args, **kwargs):
        pytest.fail("an interface region must use the coupled child")

    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", coupled_call)
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", independent)
    record = _ingest(bundle, tmp_path)
    assert calls == ["coupled"]
    assert Path(record["fem_regions"][0]["mesh_store_path"]).name == "region-rear-chamber.msh"
    coupled = read_verified_coupled_exterior_mesh(record)
    original = build_imported_mesh_isolated(
        bundle / manifest["assembly"]["file"], manifest, SIZES, include_viewport_mesh=False
    )
    assert read_verified_import_mesh(record) == original["msh_text"]
    _system(
        {
            "exterior": {**record["coupled_exterior"], "msh_text": coupled},
            "regions": {
                region["region_id"]: {
                    "msh_text": read_verified_fem_region_mesh(record, region["region_id"])
                }
            },
        },
        region,
    )
    altered = deepcopy(record)
    altered["coupled_exterior"]["mesh_size_bytes"] += 1
    with pytest.raises(ImportedMeshArtifactError, match="size mismatch"):
        read_verified_coupled_exterior_mesh(altered)
    path = Path(record["coupled_exterior"]["mesh_store_path"])
    path.write_text(coupled + " ")
    with pytest.raises(ImportedMeshArtifactError, match="digest mismatch"):
        read_verified_coupled_exterior_mesh(record)


def test_normalisation_mismatch_is_a_named_refusal(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    bad = np.eye(4)
    bad[0, 3] = 10
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Port.*normalisation mismatch"):
        _coupled(bundle, manifest, region, normalisation=bad.tolist())


def test_uncovered_wall_refuses_instead_of_sealing_the_air(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns", missing_wall=True)
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Port.*not closed"):
        _coupled(bundle, manifest, region)


def test_verified_extra_member_refuses_after_admission(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    path = bundle / region["file"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ChildRefusal, match="rear-chamber.*changed after bundle verification"):
        _coupled(bundle, manifest, region)


@pytest.mark.parametrize("option", [{"infinite_baffle": True}, {"ground_plane": {"enabled": True}}])
def test_infinite_baffle_is_a_blocking_named_ingest_refusal(tmp_path, option):
    from server.cadlink.intake import ingest

    bundle, _manifest_record, _region_record = _coupling_bundle(tmp_path / "returns")
    from server.cadlink.store import CadLinkStore

    store = CadLinkStore(tmp_path / "cadlink.db")
    try:
        with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*infinite baffle"):
            ingest.ingest_bundle(
                bundle,
                SIZES,
                [],
                store,
                tmp_path / "data",
                prep_options=option,
                defer_viewport=True,
            )
    finally:
        store.close()


def test_painted_exterior_face_keeps_its_ancestry_and_drive(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    built = _coupled(bundle, manifest, region, source=True)
    name = "wg-import-v1|source=outer-cone|role=LF"
    assert built["exterior"]["physical_names"][name]["drive_channel_id"] == "drive-LF"
    system, _domain = _system(built, region)
    assert len(system["components"][0]["boundary_ids"]) == 2


def test_region_receives_the_same_rotated_and_recentred_frame(tmp_path):
    from server.cadlink.interpretation.solver_frame import frame_spec

    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    built = _coupled(bundle, manifest, region, options={"solver_frame": frame_spec("+x", manifest)})
    system, domain = _system(built, region)
    matrix = np.asarray(built["exterior"]["normalisation_matrix"])
    assert not np.allclose(matrix, np.eye(4))
    assert built["regions"][region["region_id"]]["normalisation_matrix"] == matrix.tolist()
    # Check all port nodes against the transformed CAD port plane.
    name = physical_name(region["region_id"], "port", "interface")
    nodes = domain.points_mm[np.unique(domain.surfaces[name])]
    origin = matrix[:3, :3] @ [40, 15, 10] + matrix[:3, 3]
    normal = matrix[:3, :3] @ [1, 0, 0]
    assert np.allclose((nodes - origin) @ normal, 0, atol=1e-10)
    assert system["interfaces"][0]["topology"]["max_coordinate_error"] == 0


def test_interface_winding_refusal_names_region_and_group():
    from server.mesh.fem_coupling import _check_interface

    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    triangle = np.array([[0, 1, 2]])
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Port.*interface winding"):
        _check_interface(
            points, triangle, triangle[:, [0, 2, 1]], "FEM region 'Rear chamber', group 'Port'"
        )


def test_closed_skin_refuses_nonmanifold_edges_even_with_no_open_edges():
    from server.mesh.fem_coupling import _closed

    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    surface = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
    triangles = np.concatenate([surface, surface])
    assert open_edges(triangles) == 0
    with pytest.raises(FemRegionMeshError, match="not closed.*0 open edges"):
        _closed(points, triangles)


def test_reduced_domain_refuses_before_starting_a_region_child(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    from server.cadlink.wgreturn import read_wgreturn

    bundle_path, _manifest_record, _region_record = _coupling_bundle(tmp_path / "returns")
    bundle = read_wgreturn(bundle_path)

    def unexpected(*args, **kwargs):
        pytest.fail("a reduced domain must refuse before coupled meshing")

    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*reduced domain"):
        ingest._mesh_coupled_regions(bundle, tmp_path, {}, SIZES, {}, ["x"])


def test_coupled_child_is_disabled_without_the_flag(tmp_path, monkeypatch):
    from server.cadlink.intake import isolated

    monkeypatch.delenv("WG2_FEM_REGIONS")

    def unexpected(*args, **kwargs):
        pytest.fail("flag-off coupled mesh must not start a child")

    monkeypatch.setattr(isolated, "isolated_step_task", unexpected)
    with pytest.raises(ChildRefusal, match="declared FEM regions are disabled"):
        build_coupled_mesh_isolated(
            tmp_path / "absent.step",
            [],
            members={},
            recipe={},
            expected_geometry_hash="",
            normalisation_matrix=[],
            sizes={},
            exterior_names={},
        )


def test_coupled_sizes_are_forwarded_without_limits(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    from server.cadlink.wgreturn import read_wgreturn

    bundle_path, manifest, region = _coupling_bundle(tmp_path / "returns")
    bundle = read_wgreturn(bundle_path)
    region = bundle.manifest["scope"]["fem_regions"][0]
    region["mesh_size_mm"] = 0.001
    region["surface_groups"][0]["mesh_size_mm"] = 0.0001
    exterior = build_imported_mesh_isolated(
        bundle.assembly_path, manifest, SIZES, include_viewport_mesh=False
    )
    captured = []

    def child(_assembly, regions, **kwargs):
        captured.append((regions, kwargs["sizes"]))
        return {
            "exterior": {
                "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                "stats": {"open_edges": 0},
                "physical_names": {},
                "normalisation_matrix": exterior["normalisation"]["matrix"],
            },
            "regions": {region["region_id"]: {}},
        }

    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", child)
    ingest._mesh_coupled_regions(bundle, tmp_path, exterior, SIZES, {}, [])
    assert captured == [([region], SIZES)]
    assert captured[0][0][0]["mesh_size_mm"] == 0.001
    assert captured[0][0][0]["surface_groups"][0]["mesh_size_mm"] == 0.0001


def test_unshared_buried_interface_is_a_named_refusal(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    # The y=0 inner wall borders material, so it cannot couple to exterior air.
    from server.tests.test_fem_regions import _face

    region["surface_groups"][1]["faces"] = [
        _face(800, (20, 0, 10), [0, -1, 0], (0, 0, 0), (40, 0, 20))
    ]
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Port.*interface.*not shared"):
        _coupled(bundle, manifest, region)


def test_disconnected_skins_are_each_oriented_outward():
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    surface = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
    points = np.concatenate([points, points + [3, 0, 0]])
    triangles = np.concatenate([surface, surface[:, [0, 2, 1]] + 4])
    oriented = orient_closed_surface(points, triangles, into_body=False)
    for component in (oriented[:4], oriented[4:]):
        corners = points[component]
        volume = (
            np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6
        )
        assert volume > 0
    assert np.array_equal(oriented, orient_closed_surface(points, oriented, into_body=False))


def test_every_interface_region_is_meshed_in_the_same_child(tmp_path):
    bundle, manifest, first = _coupling_bundle(tmp_path / "returns", second_box=True)
    second = deepcopy(first)
    second.update(
        region_id="front-chamber",
        name="Front chamber",
        object_id="air-2",
        file="fem-regions/front-chamber.step",
    )
    for group in second["surface_groups"]:
        for face in group["faces"]:
            face["centroid_mm"][0] += 100
            for corner in face["bbox_mm"]:
                corner[0] += 100
    path = _write_step(bundle / second["file"], boxes=((100.0, 0.0, 0.0),))
    manifest["scope"]["fem_regions"].append(second)
    manifest["files"][second["file"]] = {
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "media_type": "model/step",
        "purpose": "fem-region",
    }
    exterior = build_imported_mesh_isolated(
        bundle / "assembly.step", manifest, SIZES, include_viewport_mesh=False
    )
    built = build_coupled_mesh_isolated(
        bundle / "assembly.step",
        [first, second],
        members={
            r["region_id"]: {"path": str(bundle / r["file"]), **manifest["files"][r["file"]]}
            for r in (first, second)
        },
        recipe=exterior["viewport_recipe"],
        expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=exterior["normalisation"]["matrix"],
        sizes=SIZES,
        exterior_names={},
    )
    assert set(built["regions"]) == {first["region_id"], second["region_id"]}
    points, groups = read_coupled_exterior(built["exterior"]["msh_text"])
    assert open_edges(np.concatenate(list(groups.values()))) == 0
    inputs = [
        FemRegionInput(
            r, read_fem_region_domain(built["regions"][r["region_id"]]["msh_text"], r["region_id"])
        )
        for r in (first, second)
    ]
    system = build_coupled_system(
        ExteriorSurface(points, groups, built["exterior"]["physical_names"]), inputs
    )
    assert len(system["interfaces"]) == 2
    assert all(
        set(interface["topology"]["normal_sign"]) == {1} for interface in system["interfaces"]
    )
    from beat_engine.beat_contract import validate_compiled_system

    validate_compiled_system(system)


def test_region_and_exterior_share_rigid_translation_and_recentring(tmp_path):
    from server.mesh.imported import (
        _geometry_fingerprint,
        _import_occ_root_bodies,
        apply_rigid_normalisation,
    )
    from hornlab_mesher.step_import import gmsh_surface_geometries, gmsh_surface_tags

    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    assembly = bundle / "assembly.step"
    exterior = build_imported_mesh_isolated(assembly, manifest, SIZES, include_viewport_mesh=False)
    recipe = deepcopy(exterior["viewport_recipe"])
    matrix = np.eye(4)
    matrix[:3, 3] = [5, 20, -10]
    recentre = np.array([0.0, -15.0, 0.0])
    recipe["normalisation_matrix"] = matrix.tolist()
    recipe["recentre_translation_mm"] = recentre.tolist()
    total = matrix.copy()
    total[:3, 3] += recentre

    def observe_fixture():
        import gmsh

        gmsh.clear()
        gmsh.model.add("translated-fixture")
        try:
            roots = _import_occ_root_bodies(gmsh, assembly)
            apply_rigid_normalisation(gmsh, roots, matrix)
            gmsh.model.occ.translate(roots, *recentre.tolist())
            gmsh.model.occ.synchronize()
            geometry = gmsh_surface_geometries(gmsh_surface_tags())
            return _geometry_fingerprint(geometry), geometry
        finally:
            gmsh.model.remove()

    fingerprint, reference = _run_in_gmsh_session(observe_fixture)
    recipe["surface_order_reference"] = reference
    built = build_coupled_mesh_isolated(
        assembly,
        [region],
        members={
            region["region_id"]: {
                "path": str(bundle / region["file"]),
                **manifest["files"][region["file"]],
            }
        },
        recipe=recipe,
        expected_geometry_hash=fingerprint,
        normalisation_matrix=total.tolist(),
        sizes=SIZES,
        exterior_names={},
    )
    _system_record, domain = _system(built, region)
    assert domain.points_mm.min(axis=0) == pytest.approx(total[:3, 3])
    assert domain.points_mm.max(axis=0) == pytest.approx(total[:3, 3] + [40, 30, 20])
    assert (
        built["exterior"]["normalisation_matrix"]
        == built["regions"][region["region_id"]]["normalisation_matrix"]
        == total.tolist()
    )


def test_sheet_walls_and_air_skin_share_the_aperture_rim(tmp_path):
    from server.mesh.imported import _geometry_fingerprint, _import_occ_root_bodies
    from hornlab_mesher.step_import import gmsh_surface_geometries, gmsh_surface_tags

    assembly = tmp_path / "wall-sheets.step"
    air = _write_step(tmp_path / "air.step")
    region = _region()

    def write_fixture():
        import gmsh

        gmsh.clear()
        gmsh.model.add("sheet-walls")
        try:
            box = gmsh.model.occ.addBox(0, 0, 0, 40, 30, 20)
            gmsh.model.occ.synchronize()
            boundary = gmsh.model.getBoundary([(3, box)], oriented=False)
            port = next(
                tag
                for dim, tag in boundary
                if dim == 2 and np.allclose(gmsh.model.occ.getCenterOfMass(2, tag), [40, 15, 10])
            )
            gmsh.model.occ.remove([(3, box)], recursive=False)
            gmsh.model.occ.remove([(2, port)], recursive=False)
            gmsh.model.occ.synchronize()
            gmsh.write(str(assembly))
        finally:
            gmsh.model.remove()
        gmsh.model.add("sheet-replay-reference")
        try:
            _import_occ_root_bodies(gmsh, assembly)
            geometry = gmsh_surface_geometries(gmsh_surface_tags())
            return _geometry_fingerprint(geometry), geometry
        finally:
            gmsh.model.remove()

    fingerprint, reference = _run_in_gmsh_session(write_fixture)
    built = build_coupled_mesh_isolated(
        assembly,
        [region],
        members={
            region["region_id"]: {
                "path": str(air),
                "sha256": "sha256:" + hashlib.sha256(air.read_bytes()).hexdigest(),
                "size_bytes": air.stat().st_size,
            }
        },
        recipe={
            "healing_options": [],
            "normalisation_matrix": np.eye(4).tolist(),
            "recentre_translation_mm": [0, 0, 0],
            "surface_order_reference": reference,
        },
        expected_geometry_hash=fingerprint,
        normalisation_matrix=np.eye(4).tolist(),
        sizes=SIZES,
        exterior_names={},
    )
    _system(built, region)


def test_declared_rigid_walls_stay_separate_from_unclaimed_walls(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    group = region["surface_groups"][0]
    group.update(group_id="walls", kind="rigid")
    group.pop("drive_channel_id")
    built = _coupled(bundle, manifest, region, source=True)
    _system(built, region)
    stats = built["regions"][region["region_id"]]["stats"]["surface_groups"]
    assert stats["walls"]["face_count"] == 1
    assert stats["walls"]["area_mm2"] == pytest.approx(1200)
    assert stats["_walls"]["face_count"] == 4
    assert stats["_walls"]["area_mm2"] == pytest.approx(3400)


def test_interface_can_use_walls_without_merging_with_implicit_walls(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    region["surface_groups"][1]["group_id"] = "walls"
    built = _coupled(bundle, manifest, region)
    _system(built, region)
    stats = built["regions"][region["region_id"]]["stats"]["surface_groups"]
    assert stats["walls"]["kind"] == "interface"
    assert stats["walls"]["area_mm2"] == pytest.approx(600)
    assert stats["_walls"]["kind"] == "rigid"
    assert stats["_walls"]["area_mm2"] == pytest.approx(3400)


def test_declared_reduced_domain_refuses_before_exterior_meshing(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest

    bundle, manifest, _region_record = _coupling_bundle(tmp_path / "returns")
    manifest["required_features"].append("reduced-domain-v1")
    manifest["assembly"]["domain"] = {
        "kind": "half",
        "cut_planes": ["x0"],
        "declared_by": "cad-author",
        "evidence": {"x0": {"min_mm": 0, "max_mm": 40, "tolerance_mm": 0.001}},
    }
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))

    def unexpected(*args, **kwargs):
        pytest.fail("declared reduced FEM domain must refuse before exterior meshing")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*reduced domain") as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"


@pytest.fixture
def coupled_intake_stub(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from server.cadlink.intake import ingest

    region = _region()
    member = {"sha256": "sha256:step", "size_bytes": 1}
    bundle = SimpleNamespace(
        manifest={"scope": {"fem_regions": [region]}, "sources": [],
                  "files": {region["file"]: member}},
        members={region["file"]: tmp_path / "air.step"},
        assembly_path=tmp_path / "material.step", artifact_sha256="sha256:assembly",
        artifact_size_bytes=1,
    )
    exterior = {"viewport_recipe": {"groups_by_tag": {}}, "tag_allocation": {"tag_map": {}},
                "transformed_geometry_hash": "geometry", "normalisation": {"matrix": []}}
    built = {"msh_text": "mesh", "stats": {"tetrahedra": 42}, "physical_names": {},
             "normalisation_matrix": []}
    now = [0.0]
    deadlines = []
    monkeypatch.setattr(ingest.time, "monotonic", lambda: now[0])

    def child(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        now[0] += 6.0
        return {"exterior": built.copy(), "regions": {region["region_id"]: built.copy()}}

    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", child)
    return ingest, bundle, exterior, now, deadlines


@pytest.mark.parametrize("exhaustion", ["coupled-child", "coupled-publish", "independent-child"])
def test_coupled_and_independent_regions_share_publication_budget(
    tmp_path, monkeypatch, coupled_intake_stub, exhaustion
):
    ingest, bundle, exterior, now, deadlines = coupled_intake_stub
    deadline = 10.0
    if exhaustion == "coupled-child":
        now[0] = 5.0
    if exhaustion == "coupled-publish":
        original = ingest._write_cache

        def publish(*args, **kwargs):
            original(*args, **kwargs)
            now[0] = 11.0

        monkeypatch.setattr(ingest, "_write_cache", publish)
    if exhaustion != "independent-child":
        with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*wall-clock budget"):
            ingest._mesh_coupled_regions(bundle, tmp_path, exterior, SIZES, {}, [], deadline=deadline)
        assert deadlines == [deadline]
        return
    _skin, coupled = ingest._mesh_coupled_regions(
        bundle, tmp_path, exterior, SIZES, {}, [], deadline=deadline
    )
    second = _region(region_id="front", name="Front chamber", file="fem-regions/front.step")
    second["surface_groups"][1]["kind"] = "plane-wave-termination"
    bundle.manifest["scope"]["fem_regions"].append(second)
    bundle.manifest["files"][second["file"]] = {"sha256": "sha256:step", "size_bytes": 1}
    bundle.members[second["file"]] = tmp_path / "front.step"

    def independent(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        now[0] += 5.0
        return {"msh_text": "mesh", "stats": {}, "physical_names": {}}

    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", independent)
    with pytest.raises(ingest.IngestRefusal, match="Front chamber.*wall-clock budget"):
        ingest._mesh_fem_regions(bundle, tmp_path, coupled, deadline=deadline)
    assert deadlines == [deadline, deadline]
    assert now[0] == 11.0


@pytest.mark.parametrize("failure", ["publish", "verify"])
def test_coupled_artifact_failures_name_regions(tmp_path, monkeypatch, coupled_intake_stub, failure):
    ingest, bundle, exterior, _now, _deadlines = coupled_intake_stub

    def fail(*args, **kwargs):
        raise OSError("artifact failure")

    monkeypatch.setattr(ingest, "_write_cache" if failure == "publish" else "read_verified_import_mesh", fail)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*publish and verify.*artifact failure"):
        ingest._mesh_coupled_regions(bundle, tmp_path, exterior, SIZES, {}, [], deadline=10.0)


def test_coupled_parent_keeps_child_stats_and_forwards_deadline(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from server.cadlink.intake import isolated
    from server.mesh import fem_regions

    region = _region(region_id="con")
    stats = {"tetrahedra": 42, "surface_groups": {"_walls": {"triangle_count": 12}}}
    artifacts = {}
    for name in ("coupled.msh", "region-con.msh"):
        path = tmp_path / name
        path.write_text("verified mesh")
        artifacts[name] = path
    members = {"con": {"path": "air.step", "sha256": "sha256:step", "size_bytes": 1}}

    @contextmanager
    def task(task, payload, **kwargs):
        assert task == "coupled_mesh"
        assert kwargs["deadline"] == 123.0
        assert kwargs["additional_steps"] == members
        assert kwargs["allowed_artifacts"] == ("coupled.msh", "region-con.msh")
        yield SimpleNamespace(
            result={"built": {"exterior": {"stats": {}}, "regions": {"con": {"stats": stats}}}},
            artifacts=artifacts,
        )

    monkeypatch.setattr(isolated, "isolated_step_task", task)
    monkeypatch.setattr(fem_regions, "read_fem_region_domain", lambda *args, **kwargs:
                        pytest.fail("parent must not parse a coupled region mesh for statistics"))
    result = build_coupled_mesh_isolated(
        tmp_path / "assembly.step", [region], members=members, recipe={},
        expected_geometry_hash="", normalisation_matrix=[], sizes={}, exterior_names={}, deadline=123.0,
    )
    assert result["regions"]["con"]["stats"] == stats
    assert result["regions"]["con"]["msh_text"] == "verified mesh"



def test_coupled_mesh_writes_and_reads_only_inside_child_scratch(tmp_path, monkeypatch):
    from server.mesh import fem_coupling
    from server.mesh import fem_regions

    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    exterior = build_imported_mesh_isolated(
        bundle / manifest["assembly"]["file"], manifest, SIZES, include_viewport_mesh=False
    )
    scratch = tmp_path / "staging" / "tmp"
    scratch.mkdir(parents=True)
    seen = []
    original = fem_coupling.tempfile.TemporaryDirectory

    def temporary(*args, **kwargs):
        if kwargs.get("prefix") in {"wg-coupled-", "wg-fem-read-"}:
            seen.append(Path(kwargs["dir"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(fem_coupling.tempfile, "TemporaryDirectory", temporary)
    # tempfile is shared with the region reader; both writes and validation reads
    # must use the swept staging tree rather than create siblings of it.
    assert fem_regions.tempfile.TemporaryDirectory is temporary
    built = _run_in_gmsh_session(lambda: fem_coupling.build_coupled_mesh(
        bundle / manifest["assembly"]["file"], [(bundle / region["file"], region)],
        recipe=exterior["viewport_recipe"], expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=exterior["normalisation"]["matrix"], sizes=SIZES, exterior_names={},
        scratch_dir=scratch,
    ))
    assert seen == [scratch, scratch, scratch]
    assert list(scratch.iterdir()) == []
    stats = built["regions"][region["region_id"]]["stats"]
    assert stats["tetrahedra"] > 0
    assert stats["surface_groups"]["_walls"]["triangle_count"] > 0
