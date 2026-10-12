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


def _write_assembly(
    path: Path, *, baffle=False, missing_wall=False, second_box=False,
    sealed_chamber=False, cavity_width=30, second_sealed=False, sealed_half=False,
) -> Path:
    def build():
        import gmsh

        gmsh.model.add("ported-box")
        try:
            outer = gmsh.model.occ.addBox(-2, -2, -2, 42, cavity_width + 4, 24)
            cavity = gmsh.model.occ.addBox(0, 0, 0, 42, cavity_width, 20)
            material, _ = gmsh.model.occ.cut([(3, outer)], [(3, cavity)])
            if sealed_half:
                cap = gmsh.model.occ.addBox(40, 30, -2, 2, cavity_width - 28, 24)
                material, _ = gmsh.model.occ.fuse(material, [(3, cap)])
            if sealed_chamber:
                chamber = gmsh.model.occ.addBox(10, 5, -1.6, 10, 10, 0.8)
                material, _ = gmsh.model.occ.cut(material, [(3, chamber)])
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
                if second_sealed:
                    cap = gmsh.model.occ.addBox(140, -2, -2, 2, 34, 24)
                    gmsh.model.occ.fuse(second, [(3, cap)])
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
        [[-2, -15, -15], [40, 45, 35]] if geometry.get("baffle")
        else [[-2, -2, -2], [40, geometry.get("cavity_width", 30) + 2, 22]]
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
    if geometry.get('sealed_half'):
        manifest['assembly']['bbox_mm'][1][0] = 42
    if geometry.get("second_box"):
        manifest["assembly"]["n_bodies_expected"] = 2
        manifest["assembly"]["bbox_mm"][1][0] = 142 if geometry.get("second_sealed") else 140
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
    bundle, manifest, region, *, mutate_recipe=None, normalisation=None, options=None, source=False,
    source_center=None, source_centers=None, extra_regions=(), contacts_only=False,
):
    assembly = bundle / manifest["assembly"]["file"]
    exterior = build_imported_mesh_isolated(
        assembly, manifest, SIZES, options=options, include_viewport_mesh=False
    )
    recipe = deepcopy(exterior["viewport_recipe"])
    if mutate_recipe:
        mutate_recipe(recipe)
    names = {}
    if source:
        centers = source_centers or [source_center if source_center is not None else [19, 15, -2]]
        indices = [
            i
            for i, (center, _area) in enumerate(recipe["surface_order_reference"])
            if any(np.allclose(center, wanted) for wanted in centers)
        ]
        assert len(indices) == len(centers)
        names["wg-import-v1|source=outer-cone|role=LF"] = {
            "indices": indices,
            "size_mm": 8,
            "kind": {"kind": "moving", "drive_channel_id": "drive-lf"},
        }
    return build_coupled_mesh_isolated(
        assembly,
        [region, *extra_regions],
        members={
            r["region_id"]: {"path": str(bundle / r["file"]), **manifest["files"][r["file"]]}
            for r in (region, *extra_regions)
        },
        recipe=recipe,
        expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=normalisation
        if normalisation is not None
        else exterior["normalisation"]["matrix"],
        sizes=SIZES,
        exterior_names=names,
        contacts_only=contacts_only,
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
    system, _domain = _system(built, region)
    points, groups = read_coupled_exterior(built["exterior"]["msh_text"])
    triangles = np.concatenate(list(groups.values()))
    corners = points[triangles]
    area = np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    ).sum() / 2
    volume = np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6
    # Material plus declared air fills the outer box, with the port closed.
    # The finite baffle adds the part of its plate outside the box footprint.
    assert area == pytest.approx(11080 if baffle else 6504)
    assert volume == pytest.approx(38640 if baffle else 34272)
    # Packing the single-component skin preserves the previous global winding.
    import base64

    packed = system["meshes"][0]["mesh_data"]
    packed_points = np.frombuffer(base64.b64decode(packed["points"]["data"]), "<f8").reshape(-1, 3)
    packed_faces = np.frombuffer(
        base64.b64decode(packed["cells"][0]["connectivity"]["data"]), "<i8"
    ).reshape(-1, 3)
    packed_corners = packed_points[packed_faces]
    packed_volume = np.einsum(
        "ij,ij->i", packed_corners[:, 0], np.cross(packed_corners[:, 1], packed_corners[:, 2])
    ).sum() / 6
    assert packed_volume == pytest.approx(volume * 1e-9)
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


def test_ingest_domain_check_uses_only_received_domain_evidence(tmp_path):
    from server.cadlink.intake import ingest

    _bundle, manifest, _region_record = _coupling_bundle(tmp_path / "returns")
    assert "Rear chamber" in ingest._refuse_fem_domain(manifest, {}, [])
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*FEM symmetry supports"):
        ingest._refuse_fem_domain(manifest, {}, ["x"])


def test_painted_exterior_face_keeps_its_ancestry_and_drive(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    built = _coupled(bundle, manifest, region, source=True)
    name = "wg-import-v1|source=outer-cone|role=LF"
    assert built["exterior"]["physical_names"][name]["drive_channel_id"] == "drive-lf"
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
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*FEM symmetry supports"):
        ingest._mesh_coupled_regions(bundle, tmp_path, {}, SIZES, {}, ["x"])


def test_coupled_child_is_disabled_without_the_flag(tmp_path, monkeypatch):
    from server.cadlink.intake import isolated

    monkeypatch.setenv("WG2_FEM_REGIONS", "0")

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
        if group.get("drive_channel_id"):
            # Two diaphragms on one channel are refused; this is a second driver.
            group["drive_channel_id"] = "drive-lf-2"
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


@pytest.mark.parametrize("closed_port", [False, True], ids=["open-port", "coincident-rigid-sheet"])
def test_sheet_walls_and_air_skin_share_the_aperture_rim(tmp_path, closed_port):
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
            if not closed_port:
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

    def build():
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
        return built

    if closed_port:
        with pytest.raises(FemRegionMeshError, match="rigid sheet face.*Rear chamber.*Port.*interface"):
            build()
    else:
        _system(build(), region)


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


def test_declared_reduced_domain_reaches_exterior_meshing(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest

    bundle, manifest, _region_record = _coupling_bundle(tmp_path / "returns")
    manifest["required_features"].append("reduced-domain-v1")
    manifest["assembly"]["domain"] = {
        "kind": "half", "cut_planes": ["x0"], "declared_by": "cad-author",
        "evidence": {"x0": {"min_mm": 0, "max_mm": 40, "tolerance_mm": 0.001}},
    }
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))

    def reached(*args, **kwargs):
        raise RuntimeError("exterior meshing reached")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", reached)
    with pytest.raises(ingest.IngestRefusal, match="exterior meshing reached"):
        _ingest(bundle, tmp_path)


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


def _add_region(bundle, manifest, *, origin=(40, 0, 0), interface=True):
    second = deepcopy(manifest["scope"]["fem_regions"][0])
    second.update(region_id="front-chamber", name="Front chamber", object_id="air-2",
                  file="fem-regions/front-chamber.step")
    for group in second["surface_groups"]:
        for face in group["faces"]:
            face["centroid_mm"] = (np.asarray(face["centroid_mm"]) + origin).tolist()
            face["bbox_mm"] = (np.asarray(face["bbox_mm"]) + origin).tolist()
        if group["kind"] == "interface" and not interface:
            # The neighbouring interface region selects an exterior solve;
            # use a rigid end so these fixtures exercise geometry refusals.
            group["kind"] = "rigid"
    path = _write_step(bundle / second["file"], boxes=(origin,))
    manifest["scope"]["fem_regions"].append(second)
    manifest["files"][second["file"]] = {
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size, "media_type": "model/step", "purpose": "fem-region",
    }
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    return second


def test_ported_enclosure_with_sealed_chamber_is_a_blocking_ingest_refusal(tmp_path):
    from server.cadlink.intake import ingest

    bundle, manifest, region = _coupling_bundle(tmp_path / "returns", sealed_chamber=True)
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Port.*Nested.*sealed chamber.*area"):
        _coupled(bundle, manifest, region)
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Port.*Nested.*sealed chamber.*area") as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"


def test_nested_closed_components_refuse_even_with_opposite_seed_windings():
    from server.mesh.fem_coupling import _closed

    outer = np.array([[0., 0., 0.], [10., 0., 0.], [0., 10., 0.], [0., 0., 10.]])
    inner = outer * 0.1 + 1
    surface = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
    points = np.concatenate([outer, inner])
    triangles = np.concatenate([surface, surface[:, [0, 2, 1]] + 4])
    with pytest.raises(FemRegionMeshError, match="Nested.*sealed chamber.*mm.*area.*enclosed"):
        _closed(points, triangles)


@pytest.mark.parametrize("into_body", [False, True])
def test_components_touching_at_a_concave_vertex_are_not_nested(into_body):
    # L-shaped prism: the reentrant vertical edge has an interior solid angle
    # greater than 2*pi. A probe at its middle vertex falsely classifies the
    # separate tetrahedron in the missing quadrant as enclosed.
    outline = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]])
    points = np.array([[x, y, z] for z in (0, 1, 2) for x, y in outline], dtype=float)
    cap = np.array([[0, 1, 3], [1, 2, 3], [0, 3, 5], [3, 4, 5]])
    sides = []
    for layer in (0, 6):
        for a in range(6):
            b = (a + 1) % 6
            sides.extend([[layer + a, layer + b, layer + b + 6],
                          [layer + a, layer + b + 6, layer + a + 6]])
    outer = np.concatenate([cap, cap + 12, sides])
    points = np.concatenate([points, [[1.4, 1.1, 1], [1.1, 1.4, 1], [1.1, 1.1, 1.4]]])
    inner = np.array([[9, 19, 18], [9, 18, 20], [18, 19, 20], [9, 20, 19]])
    triangles = np.concatenate([outer, inner])
    oriented = orient_closed_surface(points, triangles, into_body=into_body)
    assert open_edges(oriented) == 0
    for component in (oriented[:len(outer)], oriented[len(outer):]):
        corners = points[component]
        volume = np.einsum(
            "ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])
        ).sum() / 6
        assert volume < 0 if into_body else volume > 0
    assert np.array_equal(
        oriented, orient_closed_surface(points, oriented, into_body=into_body)
    )


@pytest.mark.parametrize("interface", [True, False], ids=["two-interface-regions", "interior-only-neighbour"])
def test_two_chambers_share_independent_moving_meshes_and_one_driver(tmp_path, interface):
    from server.tests.test_fem_regions import _face
    from server.tests.test_fem_electrodynamic import driver
    from beat_engine.beat_contract import validate_compiled_system

    bundle, manifest, first = _coupling_bundle(tmp_path / "returns", cavity_width=60, sealed_half=not interface)
    second = _add_region(bundle, manifest, origin=(0, 30, 0), interface=interface)
    diaphragm = _face(800, (20, 30, 10), [0, 1, 0], (0, 30, 0), (40, 30, 20))
    first["surface_groups"][0]["faces"] = [diaphragm]
    second["surface_groups"][0]["faces"] = [{**diaphragm, "normal": [0, -1, 0]}]
    first['surface_groups'][0]['mesh_size_mm'] = 2.
    second['surface_groups'][0]['mesh_size_mm'] = 7.
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    built = _coupled(bundle, manifest, first, extra_regions=[second])
    inputs = [FemRegionInput(region, read_fem_region_domain(
        built['regions'][region['region_id']]['msh_text'], region['region_id']))
        for region in (first, second)]
    a, b = (built['regions'][region['region_id']] for region in (first, second))
    assert a['stats']['surface_groups']['cone']['triangle_count'] > b['stats']['surface_groups']['cone']['triangle_count']
    name_a = physical_name(first['region_id'], 'cone', 'moving')
    name_b = physical_name(second['region_id'], 'cone', 'moving')
    assert set(a['physical_names'][name_a]['entities']).isdisjoint(b['physical_names'][name_b]['entities'])
    points, groups = read_coupled_exterior(built['exterior']['msh_text'])
    exterior = ExteriorSurface(points, groups, built['exterior']['physical_names'])
    system = build_coupled_system(exterior, inputs, transducers={'drive-lf': driver().model_copy(update={'motion_axis': (0., 1., 0.)})})
    validate_compiled_system(system)
    component, = system['components']
    assert component['kind'] == 'electrodynamic_transducer'
    assert set(component['boundary_ids']) == {'boundary:fem:rear-chamber:cone', 'boundary:fem:front-chamber:cone'}
    assert system['metadata']['projected_areas_m2']['drive-lf'] == pytest.approx(.0008)
    assert len(system['interfaces']) == (2 if interface else 1)
    assert all(item['topology']['max_coordinate_error'] < 1e-9 for item in system['interfaces'])
    with pytest.raises(ValueError, match='give this channel an electrodynamic driver'):
        build_coupled_system(exterior, inputs)
    # Removing the driver's spec must also refuse through normal job admission.
    from server.jobs.models import DriveChannel
    from server.solver.coupled_admission import fem_request_problem
    record = {'fem_regions': [first, second]}
    assert fem_request_problem(record, [DriveChannel(id='drive-lf', source_ids=['painted-front'])])[0] == 'fem_multiple_diaphragms_unsupported'
    retained = _ingest(bundle, tmp_path)
    assert len(retained['fem_regions']) == 2
    assert all(region['stats']['tetrahedra'] > 0 for region in retained['fem_regions'])


@pytest.mark.parametrize('mutation', ['other-channel', 'rigid', 'unclaimed', 'partial'])
@pytest.mark.parametrize('interior_only', [False, True])
def test_shared_contact_requires_same_channel_on_every_touching_face(tmp_path, mutation, interior_only):
    from server.tests.test_fem_regions import _face
    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=60)
    second = _add_region(bundle, manifest, origin=(0, 30, 0))
    diaphragm = _face(800, (20, 30, 10), [0, 1, 0], (0, 30, 0), (40, 30, 20))
    first['surface_groups'][0]['faces'] = [diaphragm]
    second['surface_groups'][0]['faces'] = [{**diaphragm, 'normal': [0, -1, 0]}]
    if mutation == 'other-channel':
        second['surface_groups'][0]['drive_channel_id'] = 'drive-hf'
    elif mutation == 'rigid':
        second['surface_groups'][0]['kind'] = 'rigid'
        second['surface_groups'][0].pop('drive_channel_id')
    elif mutation == 'unclaimed':
        second['surface_groups'] = second['surface_groups'][1:]
    else:
        first['surface_groups'][0]['faces'] = [_face(1200, (20, 15, 0), [0, 0, 1], (0, 0, 0), (40, 30, 0))]
    if interior_only:
        for region in (first, second):
            for group in region['surface_groups']:
                if group['kind'] == 'interface':
                    group['kind'] = 'rigid'
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    if not interior_only:
        with pytest.raises(FemRegionMeshError, match='Rear chamber.*Front chamber.*same electrodynamic channel'):
            _coupled(bundle, manifest, first, extra_regions=[second])
    from server.cadlink.intake.ingest import IngestRefusal
    with pytest.raises(IngestRefusal, match='Rear chamber.*Front chamber.*same electrodynamic channel'):
        _ingest(bundle, tmp_path)


def test_interior_only_touching_diaphragms_use_independent_region_children(tmp_path):
    from server.tests.test_fem_regions import _face
    from server.tests.test_fem_electrodynamic import driver
    from beat_engine.beat_contract import validate_compiled_system
    from server.mesh.artifact import read_verified_fem_region_mesh

    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=60)
    second = _add_region(bundle, manifest, origin=(0, 30, 0), interface=False)
    diaphragm = _face(800, (20, 30, 10), [0, 1, 0], (0, 30, 0), (40, 30, 20))
    first['surface_groups'][0]['faces'] = [diaphragm]
    second['surface_groups'][0]['faces'] = [{**diaphragm, 'normal': [0, -1, 0]}]
    first['surface_groups'][0]['mesh_size_mm'] = 2.
    second['surface_groups'][0]['mesh_size_mm'] = 7.
    first['surface_groups'][1]['kind'] = 'rigid'
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    retained = _ingest(bundle, tmp_path)
    assert not retained.get('coupled_exterior')
    a, b = retained['fem_regions']
    assert a['stats']['surface_groups']['cone']['triangle_count'] > b['stats']['surface_groups']['cone']['triangle_count']
    inputs = [FemRegionInput(region, read_fem_region_domain(
        read_verified_fem_region_mesh(retained, region['region_id']), region['region_id']))
        for region in retained['fem_regions']]
    system = build_coupled_system(None, inputs, transducers={
        'drive-lf': driver().model_copy(update={'motion_axis': (0., 1., 0.)})})
    validate_compiled_system(system)
    assert len(system['components']) == len(system['excitation_ports']) == 1
    assert not system['interfaces']
    assert system['metadata']['projected_areas_m2']['drive-lf'] == pytest.approx(.0008)


def test_interior_only_overlapping_regions_remain_refused(tmp_path):
    from server.cadlink.intake.ingest import IngestRefusal
    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=60)
    _add_region(bundle, manifest, origin=(0, 0, 0), interface=False)
    first['surface_groups'][1]['kind'] = 'rigid'
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    with pytest.raises(IngestRefusal, match="Front chamber.*overlaps FEM region.*Rear chamber"):
        _ingest(bundle, tmp_path)


def test_region_split_by_a_rigid_sheet_has_a_single_region_refusal(tmp_path):
    from server.mesh import fem_coupling
    from server.mesh.imported import _geometry_fingerprint, _import_occ_root_bodies
    from hornlab_mesher.step_import import gmsh_surface_geometries, gmsh_surface_tags

    assembly = tmp_path / "partition.step"
    air = _write_step(tmp_path / "air.step")

    def build():
        import gmsh

        gmsh.clear()
        gmsh.model.add("partition")
        sheet = gmsh.model.occ.addRectangle(0, 0, 0, 20, 30)
        gmsh.model.occ.rotate([(2, sheet)], 0, 0, 0, 0, 1, 0, -np.pi / 2)
        gmsh.model.occ.translate([(2, sheet)], 20, 0, 0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(assembly))
        gmsh.clear()
        gmsh.model.add("partition-reference")
        _import_occ_root_bodies(gmsh, assembly)
        reference = gmsh_surface_geometries(gmsh_surface_tags())
        return fem_coupling.build_coupled_mesh(
            assembly, [(air, _region())],
            recipe={"healing_options": [], "normalisation_matrix": np.eye(4).tolist(),
                    "recentre_translation_mm": [0, 0, 0], "surface_order_reference": reference},
            expected_geometry_hash=_geometry_fingerprint(reference),
            normalisation_matrix=np.eye(4).tolist(), sizes=SIZES, exterior_names={},
        )

    with pytest.raises(FemRegionMeshError) as caught:
        _run_in_gmsh_session(build)
    assert (
        "FEM region 'Rear chamber' is cut into pieces by another body; "
        "make the region one closed solid"
    ) in str(caught.value)
    assert "'Rear chamber' and 'Rear chamber'" not in str(caught.value)


@pytest.mark.parametrize("interface", [True, False], ids=["two-interface-regions", "interior-only-neighbour"])
def test_touching_regions_refuse_by_both_names_before_implicit_walls(tmp_path, interface):
    from server.cadlink.intake import ingest

    bundle, manifest, first = _coupling_bundle(tmp_path / "returns", cavity_width=60)
    second = _add_region(bundle, manifest, origin=(0, 30, 0), interface=interface)
    with pytest.raises(FemRegionMeshError, match="Rear chamber.*Front chamber.*touch.*shared FEM air"):
        _coupled(bundle, manifest, first, extra_regions=[second])
    with pytest.raises(ingest.IngestRefusal, match="Rear chamber.*Front chamber.*touch.*shared FEM air") as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"
    assert "uncovered air" not in str(caught.value)
    assert str(caught.value).count("Rear chamber") == 1
    assert str(caught.value).count("Front chamber") == 1


@pytest.mark.parametrize('gap', [0., 1e-5, 1e-3, .1])
@pytest.mark.parametrize('interface', [False, True])
def test_near_contact_requires_both_moving_faces_before_interface_matching(tmp_path, gap, interface):
    from server.cadlink.intake.ingest import IngestRefusal
    from server.tests.test_fem_regions import _face

    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=80)
    second = _add_region(bundle, manifest, origin=(0, 30 + gap, 0), interface=interface)
    first['surface_groups'][0]['faces'] = [
        _face(800, (20, 30, 10), [0, 1, 0], (0, 30, 0), (40, 30, 20))]
    second['surface_groups'][0].update(kind='rigid', faces=[
        _face(800, (20, 30 + gap, 10), [0, -1, 0], (0, 30 + gap, 0), (40, 30 + gap, 20))])
    second['surface_groups'][0].pop('drive_channel_id')
    if not interface:
        first['surface_groups'][1]['kind'] = 'rigid'
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    if gap == .1:
        # Test separation through the contact gate, without requiring the
        # synthetic assembly to close each independently ported chamber.
        assert _coupled(bundle, manifest, first, extra_regions=[second], contacts_only=True) == {
            'exterior': None, 'regions': {}}
        if not interface:
            assert len(_ingest(bundle, tmp_path)['fem_regions']) == 2
        return
    with pytest.raises(IngestRefusal) as caught:
        _ingest(bundle, tmp_path)
    message = str(caught.value)
    assert 'Rear chamber' in message and 'Front chamber' in message
    assert 'same electrodynamic channel' in message
    assert 'did not survive' not in message
    assert message.count('Rear chamber') == message.count('Front chamber') == 1
    if gap:
        assert 'near-contact' in message and 'tolerance 0.001 mm' in message
        assert message.count("group 'Cone'") == 2


@pytest.mark.parametrize('gap', [1e-5, 1e-3])
@pytest.mark.parametrize('other_channel', [False, True])
def test_near_contact_moving_pair_requires_same_channel(tmp_path, gap, other_channel):
    from server.cadlink.intake.ingest import IngestRefusal
    from server.tests.test_fem_regions import _face

    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=80)
    second = _add_region(bundle, manifest, origin=(0, 30 + gap, 0), interface=False)
    first['surface_groups'][1]['kind'] = 'rigid'
    for region, y, normal in ((first, 30., 1), (second, 30 + gap, -1)):
        region['surface_groups'][0]['faces'] = [
            _face(800, (20, y, 10), [0, normal, 0], (0, y, 0), (40, y, 20))]
    if other_channel:
        second['surface_groups'][0]['drive_channel_id'] = 'drive-hf'
    (bundle / 'wgreturn.json').write_text(json.dumps(manifest))
    if other_channel:
        with pytest.raises(IngestRefusal, match='near-contact.*same electrodynamic channel'):
            _ingest(bundle, tmp_path)
    else:
        assert len(_ingest(bundle, tmp_path)['fem_regions']) == 2


def test_touching_moving_fields_are_restricted_but_port_grading_reaches_exterior(tmp_path, monkeypatch):
    import gmsh
    from server.mesh import fem_coupling
    from server.tests.test_fem_regions import _face

    bundle, manifest, first = _coupling_bundle(tmp_path / 'returns', cavity_width=60)
    second = _add_region(bundle, manifest, origin=(0, 30, 0))
    for region, normal in ((first, 1), (second, -1)):
        region['surface_groups'][0]['faces'] = [
            _face(800, (20, 30, 10), [0, normal, 0], (0, 30, 0), (40, 30, 20))]
    exterior = build_imported_mesh_isolated(
        bundle / manifest['assembly']['file'], manifest, SIZES, include_viewport_mesh=False)
    field_types, inputs, surfaces, background = {}, {}, {}, []
    add, number, numbers = (
        gmsh.model.mesh.field.add, gmsh.model.mesh.field.setNumber,
        gmsh.model.mesh.field.setNumbers)

    def record_add(kind, *args):
        tag = add(kind, *args)
        field_types[tag] = kind
        return tag

    def record_number(tag, key, value):
        if key == 'InField':
            inputs[tag] = int(value)
        return number(tag, key, value)

    def record_numbers(tag, key, values):
        if key == 'FieldsList':
            background.extend(values)
        if key == 'SurfacesList':
            surfaces[tag] = set(values)
        return numbers(tag, key, values)

    monkeypatch.setattr(gmsh.model.mesh.field, 'add', record_add)
    monkeypatch.setattr(gmsh.model.mesh.field, 'setNumber', record_number)
    monkeypatch.setattr(gmsh.model.mesh.field, 'setNumbers', record_numbers)
    built = _run_in_gmsh_session(lambda: fem_coupling.build_coupled_mesh(
        bundle / manifest['assembly']['file'], [(bundle / r['file'], r) for r in (first, second)],
        recipe=exterior['viewport_recipe'], expected_geometry_hash=exterior['transformed_geometry_hash'],
        normalisation_matrix=exterior['normalisation']['matrix'], sizes=SIZES, exterior_names={}))
    thresholds = [tag for tag, kind in field_types.items() if kind == 'Threshold']
    assert len(thresholds) == 4
    # The two ports remain in the global minimum, grading the adjoining BEM
    # skin. Only the two diaphragm thresholds are wrapped in a region Restrict.
    global_thresholds = set(thresholds).intersection(background)
    assert len(global_thresholds) == 2
    restricted = [tag for tag in background if field_types[tag] == 'Restrict'
                  and inputs[tag] in thresholds]
    assert len(restricted) == 2
    port_faces, moving_faces = set(), set()
    for result in built['regions'].values():
        for spec in result['physical_names'].values():
            if spec.get('kind') == 'interface':
                port_faces.update(spec['entities'])
            elif spec.get('kind') == 'moving':
                moving_faces.update(spec['entities'])
    assert set().union(*(surfaces[inputs[tag]] for tag in global_thresholds)) == port_faces
    assert set().union(*(surfaces[inputs[inputs[tag]]] for tag in restricted)) == moving_faces


@pytest.mark.parametrize("partial", [False, True], ids=["fully-covered", "partly-covered"])
def test_painted_source_covered_by_unclaimed_region_faces_refuses(tmp_path, partial):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    region["surface_groups"][0]["kind"] = "rigid"
    region["surface_groups"][0].pop("drive_channel_id")
    # One source can paint several faces. The exterior face must not survive
    # alone while its covered face silently becomes a rigid region wall.
    centers = [[20, 15, 0], [19, 15, -2]] if partial else [[20, 15, 0]]
    with pytest.raises(FemRegionMeshError, match="outer-cone.*Rear chamber.*not claimed.*moving group"):
        _coupled(bundle, manifest, region, source=True, source_centers=centers)


def test_painted_source_covered_by_diaphragm_refuses_with_remedy(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    with pytest.raises(FemRegionMeshError, match="outer-cone.*Rear chamber.*unpaint the source; the region's diaphragm group drives it"):
        _coupled(bundle, manifest, region, source=True, source_center=[20, 15, 0])


def test_flag_off_uses_f6_wording_before_coupled_meshing(tmp_path, monkeypatch):
    from server.cadlink.intake import ingest
    from server.cadlink.wgreturn import read_wgreturn

    bundle_path, _manifest_record, _region_record = _coupling_bundle(tmp_path / "returns")
    bundle = read_wgreturn(bundle_path)
    monkeypatch.setenv("WG2_FEM_REGIONS", "0")

    def unexpected(*args, **kwargs):
        pytest.fail("flag-off FEM must refuse before coupled meshing")

    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal, match="This return declares FEM regions; this Waveguide Generator has FEM regions turned off\\."):
        ingest._mesh_coupled_regions(bundle, tmp_path, {}, SIZES, {}, [])


def test_separate_interior_region_keeps_independent_f6_mesh(tmp_path):
    bundle, manifest, first = _coupling_bundle(tmp_path / "returns", second_box=True, second_sealed=True)
    second = _add_region(bundle, manifest, origin=(100, 0, 0), interface=False)
    built = _coupled(bundle, manifest, first, extra_regions=[second])
    assert set(built["regions"]) == {first["region_id"]}
    record = _ingest(bundle, tmp_path)
    assert {r["region_id"] for r in record["fem_regions"]} == {first["region_id"], second["region_id"]}
    independent = next(r for r in record["fem_regions"] if r["region_id"] == second["region_id"])
    assert read_verified_fem_region_mesh(record, second["region_id"]).startswith("$MeshFormat\n4.1")
    assert independent["stats"]["tetrahedra"] > 0


def test_part_of_a_single_painted_face_cannot_become_rigid(tmp_path):
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns", cavity_width=31)
    region["surface_groups"][0]["kind"] = "rigid"
    region["surface_groups"][0].pop("drive_channel_id")
    with pytest.raises(FemRegionMeshError, match="outer-cone.*Rear chamber.*not claimed.*moving group"):
        _coupled(bundle, manifest, region, source=True, source_center=[20, 15.5, 0])


@pytest.mark.parametrize("moving,partial", [(False, False), (True, False), (False, True)],
                         ids=["unclaimed-region-face", "diaphragm-remedy", "partial-coverage"])
def test_covered_painted_source_is_a_named_blocking_ingest_finding(tmp_path, moving, partial):
    from server.cadlink.intake import ingest
    from server.mesh.imported import _import_occ_root_bodies
    from hornlab_mesher.step_import import (
        advanced_face_order, advanced_face_order_for_surfaces,
        gmsh_surface_geometries, gmsh_surface_tags,
    )

    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    assembly = bundle / "assembly.step"

    def address_floor():
        import gmsh

        gmsh.clear()
        gmsh.model.add("source-address")
        try:
            _import_occ_root_bodies(gmsh, assembly)
            surfaces = gmsh_surface_tags()
            identifiers = advanced_face_order_for_surfaces(
                assembly, surfaces, model_from_step=np.eye(4),
                addressed_faces=advanced_face_order(assembly),
            )
            geometry = gmsh_surface_geometries(surfaces)
            centers = [[20, 15, 0], [19, 15, -2]] if partial else [[20, 15, 0]]
            return [(identifier, area) for identifier, (center, area) in zip(identifiers, geometry)
                    if any(np.allclose(center, wanted) for wanted in centers)]
        finally:
            gmsh.model.remove()

    faces = _run_in_gmsh_session(address_floor)
    assert len(faces) == (2 if partial else 1)
    identifiers, areas = zip(*faces)
    if not moving:
        region["surface_groups"][0]["kind"] = "rigid"
        region["surface_groups"][0].pop("drive_channel_id")
    manifest["sources"] = [{
        "id": "outer-cone", "role": "LF", "instance_id": None, "required": True,
        "default_drive_channel_id": "drive-lf",
        "patch_policy": "explicit-disconnected" if partial else "single-connected",
        "expected_connected_components": len(faces), "selectors": {"advanced_face_indices": list(identifiers)},
        "observed": {"face_count": len(faces), "total_area_mm2": sum(areas),
                     "per_face_area_mm2": list(areas), "bodies": ["walls"]},
        "suggested_resolution_mm": 8,
    }]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    message = "unpaint the source; the region's diaphragm group drives it" if moving else "not claimed.*moving group"
    with pytest.raises(ingest.IngestRefusal, match=f"outer-cone.*Rear chamber.*{message}") as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"
