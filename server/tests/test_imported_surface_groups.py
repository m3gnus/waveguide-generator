"""G2 surface sizing and legacy mesh-byte regressions (synthetic geometry)."""
from __future__ import annotations

from copy import deepcopy
import gc
import hashlib
import json
import re
from pathlib import Path
from time import process_time
from types import SimpleNamespace

import numpy as np
import pytest

from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.imported import ImportedMeshError, RoleResolutionError, build_imported_mesh
from server.tests._frozen_bytes import off_frozen_platform

FIXTURES = Path(__file__).parent / "fixtures"


def _synthetic_return(path: Path, *, quarter: bool = False, y_offset: float = 0):
    """F1-sized cabinet with a recessed HF disc; F2 has four quarter-body sources."""
    import gmsh
    from hornlab_mesher.step_import import advanced_face_order_for_surfaces

    def write():
        gmsh.clear()
        gmsh.option.setNumber("General.Terminal", 0)
        if quarter:
            body = gmsh.model.occ.addBox(0, 0, -275, 250, 143.5, 275)
            discs = [(40, 35, 8, "HF"), (100, 35, 16, "MF"),
                     (180, 55, 30, "LF"), (80, 105, 12, "PASSIVE_CARDIOID")]
        else:
            body = gmsh.model.occ.addBox(-250, -143.5 + y_offset, -275, 500, 287, 275)
            discs = [(0, y_offset, 10, "HF")]
        tools = [gmsh.model.occ.addCone(x, y, -100, 0, 0, 100, r, r * 2)
                 for x, y, r, _role in discs]
        gmsh.model.occ.cut([(3, body)], [(3, tool) for tool in tools])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        surfaces = [tag for _dim, tag in gmsh.model.getEntities(2)]
        order = advanced_face_order_for_surfaces(path, surfaces)
        faces = dict(zip(surfaces, order, strict=True))
        sources = []
        back = None
        for surface in surfaces:
            center = gmsh.model.occ.getCenterOfMass(2, surface)
            area = gmsh.model.occ.getMass(2, surface)
            if abs(center[2] + 275) < 1e-5:
                back = {"group_id": "back", "name": "Back", "size_mm": 40,
                        "selectors": {"advanced_face_indices": [faces[surface]]},
                        "observed": {"face_count": 1, "total_area_mm2": area,
                                     "per_face_area_mm2": [area]}}
            if abs(center[2] + 100) < 1e-5:
                role = min(discs, key=lambda disc: np.linalg.norm(np.array(center[:2]) - disc[:2]))[3]
                sources.append({"id": role.lower(), "role": role, "required": True,
                                "patch_policy": "single-connected", "expected_connected_components": 1,
                                "selectors": {"advanced_face_indices": [faces[surface]]},
                                "observed": {"face_count": 1, "total_area_mm2": area,
                                             "per_face_area_mm2": [area]}})
        gmsh.clear()
        assert back is not None and len(sources) == len(discs)
        return {"sources": sources, "instances": [], "coordinate_system": {},
                "assembly": {"n_bodies_expected": 1}}, back

    return _run_in_gmsh_session(write)


def _sizes(manifest, wall=25, transition=100):
    return {"rigid_size_mm": wall, "transition_mm": transition,
            "source_size_mm": {source["id"]: {"HF": 6, "MF": 15, "LF": 25,
                                                    "PASSIVE_CARDIOID": 25}[source["role"]]
                               for source in manifest["sources"]}}


def _build(path, manifest, sizes, **options):
    return _run_in_gmsh_session(
        build_imported_mesh, path, manifest, sizes, options=options, include_viewport_mesh=False
    )


def _back_triangles(result):
    from server.mesh.imported import gmsh22_triangle_arrays
    points, triangles = gmsh22_triangle_arrays(result["msh_text"])
    # Postprocessed .msh coordinates are metres.
    z = points[triangles, 2]
    return int(np.count_nonzero(np.all(np.isclose(z, -0.275, atol=1e-8), axis=1)))


@pytest.fixture(scope="module")
def cabinet(tmp_path_factory):
    path = tmp_path_factory.mktemp("surface-groups") / "assembly.step"
    manifest, back = _synthetic_return(path)
    return path, manifest, back


@pytest.mark.parametrize("mode,fraction,y_offset,planes", [
    ("full", 1, 0, []), ("auto", .5, 30, ["x0"]), ("auto", .25, 0, ["x0", "y0"]),
])
def test_coarse_back_keeps_size_and_rigid_tag_across_cuts(
    cabinet, tmp_path, mode, fraction, y_offset, planes,
):
    path, manifest, back = cabinet
    if y_offset:
        path = tmp_path / "half.step"
        manifest, back = _synthetic_return(path, y_offset=y_offset)
    result = _build(path, manifest, _sizes(manifest), symmetry_mode=mode, surface_groups=[back])
    count = _back_triangles(result)
    expected = 2.3 * 500 * 287 * fraction / 40**2
    # The shared rim follows the finer 25 mm side walls. Its boundary strip
    # matters most on the small quarter panel; the interior follows area/h².
    perimeter = 2 * (500 + 287) * fraction**.5
    assert .7 * expected < count < 1.5 * expected + perimeter / 25
    baseline = _build(path, manifest, _sizes(manifest), symmetry_mode=mode)
    assert count < .8 * _back_triangles(baseline)
    assert result["symmetry"]["domain_planes"] == planes
    assert set(result["stats"]["tag_counts"]) == {"1", "101"}
    group = result["surface_group_resolution"]["back"]
    assert group["retained_area_mm2"] == pytest.approx(500 * 287 * fraction)
    assert group["size_mm"] == 40


def test_fine_group_refines_and_defaults_stay_below_four_thousand(cabinet):
    path, manifest, back = cabinet
    default = _build(path, manifest, _sizes(manifest))
    fine = _build(path, manifest, _sizes(manifest), surface_groups=[{**back, "size_mm": 15}])
    assert default["stats"]["triangle_count"] < 4000
    assert _back_triangles(fine) > 2 * _back_triangles(default)


@pytest.mark.parametrize("kind", ["overlap", "source", "empty", "drift", "count"])
def test_surface_group_resolution_refuses_conflicts_and_drift(cabinet, kind):
    path, manifest, back = cabinet
    group = deepcopy(back)
    groups = [group]
    if kind == "overlap":
        groups.append({**back, "group_id": "other", "size_mm": 15})
        match = "more than one surface group"
    elif kind == "source":
        group.update(selectors=manifest["sources"][0]["selectors"], observed=manifest["sources"][0]["observed"])
        match = "source"
    elif kind == "empty":
        group["selectors"] = {"appearance_labels": ["WGMESH_missing"]}
        match = "missing"
    elif kind == "drift":
        group["observed"]["total_area_mm2"] *= 2
        match = "area drift"
    else:
        group["observed"]["face_count"] = 2
        match = "face count"
    with pytest.raises((ImportedMeshError, RoleResolutionError), match=match) as caught:
        _build(path, manifest, _sizes(manifest), surface_groups=groups)
    assert "surface group" in str(caught.value)
    if kind != "source":
        assert "source" not in str(caught.value)
    if kind == "drift":
        assert not caught.value.area_drift_sources


def test_painted_back_survives_both_cuts(cabinet, tmp_path):
    path, manifest, back = cabinet
    painted = tmp_path / "painted.step"
    text = path.read_text()
    last = max(map(int, re.findall(r"#(\d+)\s*=", text)))
    face = back["selectors"]["advanced_face_indices"][0]
    # The same STEP presentation chain used by CAD appearance paint.
    records = [
        f"#{last+1}=COLOUR_RGB('WGMESH_back',0.8,0.2,0.2);",
        f"#{last+2}=FILL_AREA_STYLE_COLOUR('',#{last+1});",
        f"#{last+3}=FILL_AREA_STYLE('',(#{last+2}));",
        f"#{last+4}=SURFACE_STYLE_FILL_AREA(#{last+3});",
        f"#{last+5}=SURFACE_SIDE_STYLE('',(#{last+4}));",
        f"#{last+6}=SURFACE_STYLE_USAGE(.BOTH.,#{last+5});",
        f"#{last+7}=PRESENTATION_STYLE_ASSIGNMENT((#{last+6}));",
        f"#{last+8}=STYLED_ITEM('',(#{last+7}),#{face});",
    ]
    end = text.rfind("ENDSEC;")
    painted.write_text(text[:end] + "\n".join(records) + "\n" + text[end:])
    group = {**back, "selectors": {"appearance_labels": ["WGMESH_back"]}}
    result = _build(painted, manifest, _sizes(manifest), surface_groups=[group])
    resolution = result["surface_group_resolution"]["back"]
    assert resolution["origins"] == ["appearance_labels"]
    assert resolution["retained_area_mm2"] == pytest.approx(500 * 287 / 4)
    assert result["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert _back_triangles(result) < 110


@pytest.mark.parametrize("size", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_group_sizes_are_refused(cabinet, size):
    path, manifest, back = cabinet
    with pytest.raises(ImportedMeshError, match="finite and positive"):
        _build(path, manifest, _sizes(manifest), surface_groups=[{**back, "size_mm": size}])


@pytest.mark.parametrize("source_size", [6, 60])
def test_group_fields_keep_grading_and_choose_smaller_shared_point_size(cabinet, monkeypatch, source_size):
    import gmsh
    import server.mesh.imported as imported

    path, manifest, back = cabinet
    points = []
    thresholds = []
    ceilings = []
    sagitta_bounds = []
    set_size = gmsh.model.mesh.setSize
    set_number = gmsh.model.mesh.field.setNumber
    set_option = gmsh.option.setNumber
    sagitta = imported._sagitta_background_view

    def record_size(dimtags, size):
        points.extend((gmsh.model.getValue(dim, tag, []), float(size)) for dim, tag in dimtags)
        return set_size(dimtags, size)

    def record_field(tag, option, value):
        if option in {"SizeMax", "DistMax"}:
            thresholds.append((option, value))
        return set_number(tag, option, value)

    def record_option(option, value):
        if option == "Mesh.MeshSizeMax":
            ceilings.append(value)
        return set_option(option, value)

    def record_sagitta(model, **kwargs):
        sagitta_bounds.append(kwargs["size_max_mm"])
        return sagitta(model, **kwargs)

    monkeypatch.setattr(gmsh.model.mesh, "setSize", record_size)
    monkeypatch.setattr(gmsh.model.mesh.field, "setNumber", record_field)
    monkeypatch.setattr(gmsh.option, "setNumber", record_option)
    monkeypatch.setattr(imported, "_sagitta_background_view", record_sagitta)
    sizes = _sizes(manifest)
    sizes["source_size_mm"]["hf"] = source_size
    _build(path, manifest, sizes, surface_groups=[back])
    # Original back corners share 25 mm side walls. The new corner at the
    # intersection of both open cuts belongs only to the 40 mm back panel.
    back_points = [(xyz, size) for xyz, size in points if abs(xyz[2] + 275) < 1e-6]
    assert back_points
    for xyz, size in back_points:
        shared = abs(xyz[0] - 250) < 1e-6 or abs(xyz[1] - 143.5) < 1e-6
        assert size == (25 if shared else 40)
    assert ("SizeMax", 40) in thresholds and ("DistMax", 100) in thresholds
    assert ceilings[-1] == 40 and sagitta_bounds == [40]


IDENTITY = json.loads((FIXTURES / "imported_legacy_mesh_identity.json").read_text())


@pytest.mark.parametrize("case", IDENTITY["cases"], ids=lambda case: case["name"])
@pytest.mark.parametrize("group_option", [{}, {"surface_groups": None}, {"surface_groups": []}],
                         ids=["absent", "none", "empty"])
def test_legacy_mesh_bytes_equal_base_commit(case, group_option, tmp_path, monkeypatch):
    geometry = case.get("geometry", case["name"])

    def sha(text):
        return hashlib.sha256(text.encode()).hexdigest()

    def observe(option, root):
        if geometry == "coupled-box":
            from server.tests.test_fem_coupling import SIZES, _coupled, _coupling_bundle

            monkeypatch.setenv("WG2_FEM_REGIONS", "1")
            assert case["sizes"] == SIZES
            bundle, manifest, region = _coupling_bundle(root / "returns")
            built = _coupled(bundle, manifest, region, options={**case["options"], **option})
            return {"msh": sha(built["exterior"]["msh_text"]),
                    "triangles": built["exterior"]["stats"]["triangles"],
                    "regions": {key: sha(value["msh_text"]) for key, value in built["regions"].items()}}
        if geometry in {"synthetic-f1", "synthetic-f2"}:
            path = root / "assembly.step"
            manifest, _ = _synthetic_return(path, quarter=geometry == "synthetic-f2")
        else:
            bundle = FIXTURES / "cadlink-endpoint-oracle" / (case["name"] + ".wgreturn")
            path = bundle / "assembly.step"
            manifest = json.loads((bundle / "wgreturn.json").read_text())
        options = deepcopy(case["options"])
        if "rigid_face_sizes_mm" in options:
            options["rigid_face_sizes_mm"] = {int(key): value for key, value in options["rigid_face_sizes_mm"].items()}
        result = _build(path, manifest, case["sizes"], **options, **option)
        return {"msh": sha(result["msh_text"]), "triangles": result["stats"]["triangle_count"]}

    root = tmp_path / "option"
    root.mkdir()
    actual = observe(group_option, root)
    elsewhere = off_frozen_platform(IDENTITY["platform"])
    if elsewhere:
        # Off the freezing platform, the portable half of the contract: no,
        # None and an empty list of surface groups build the same mesh bytes.
        if not group_option:
            pytest.skip(elsewhere)
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        assert actual == observe({}, legacy)
        return
    frozen = {"msh": case["msh_sha256"], "triangles": case["triangles"]}
    if geometry == "coupled-box":
        frozen["regions"] = case["region_msh_sha256"]
    assert actual == frozen


def test_disconnected_face_components_are_linear():
    from server.mesh.imported import _face_components

    calls = []

    def boundary(dimtags, **kwargs):
        face = dimtags[0][1]
        calls.append(face)
        return [(1, face * 4 + edge) for edge in range(4)]

    model = SimpleNamespace(getBoundary=boundary)
    # 20,000 disconnected faces take about 0.05 s of CPU here; the former
    # all-remaining-faces scan is quadratic and needs about 20 s. Measure this
    # process's CPU time with the collector paused, so neither a busy runner
    # nor a full collection of the suite's heap is counted as algorithm time.
    faces = 20_000
    collecting = gc.isenabled()
    gc.disable()
    try:
        start = process_time()
        assert _face_components(SimpleNamespace(model=model), range(faces)) == faces
        elapsed = process_time() - start
    finally:
        if collecting:
            gc.enable()
    assert elapsed < 2.0
    assert len(calls) == faces


def test_face_components_preserve_shared_edge_connectivity():
    from server.mesh.imported import _face_components

    edges = {1: [1, 2], 2: [-2, 3], 3: [3, 4], 4: [5], 5: [5, 6], 6: [7]}
    model = SimpleNamespace(getBoundary=lambda dimtags, **kw: [(1, e) for e in edges[dimtags[0][1]]])
    gmsh = SimpleNamespace(model=model)
    assert _face_components(gmsh, []) == 0
    assert _face_components(gmsh, [1, 2, 3, 4, 5, 6, 1]) == 3


def _scaled_coupled_bundle(root, planes):
    """Use the existing symmetric FEM box at cabinet scale (440 x 340 mm)."""
    from server.tests.test_fem_symmetry import symmetry_bundle
    from hornlab_mesher.step_import import advanced_face_order_for_surfaces

    bundle, manifest, region = symmetry_bundle(root, planes or ("x0", "y0"))
    back = None

    def scale(path, *, assembly=False):
        import gmsh

        gmsh.clear()
        roots = gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.dilate(roots, 0, 0, 0, 10, 10, 10)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        if assembly:
            surfaces = [tag for dim, tag in gmsh.model.getEntities(2)]
            identifiers = advanced_face_order_for_surfaces(path, surfaces)
            selected = [(identifier, gmsh.model.occ.getMass(2, tag))
                        for identifier, tag in zip(identifiers, surfaces)
                        if abs(gmsh.model.occ.getCenterOfMass(2, tag)[2] + 20) < 1e-6]
            assert len(selected) == 1
            identifier, area = selected[0]
            return {"group_id": "back", "size_mm": 40,
                    "selectors": {"advanced_face_indices": [identifier]},
                    "observed": {"face_count": 1, "total_area_mm2": area}}
        gmsh.clear()

    for file in [manifest["assembly"]["file"], region["file"]]:
        path = bundle / file
        group = _run_in_gmsh_session(scale, path, assembly=file == manifest["assembly"]["file"])
        if group:
            back = group
        data = path.read_bytes()
        manifest["files"][file].update(sha256="sha256:" + hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    manifest["assembly"]["bbox_mm"] = (np.array(manifest["assembly"]["bbox_mm"]) * 10).tolist()
    region["mesh_size_mm"] = 50
    for group in region["surface_groups"]:
        group["mesh_size_mm"] = 50
        for face in group["faces"]:
            face["area_mm2"] *= 100
            face["centroid_mm"] = (np.array(face["centroid_mm"]) * 10).tolist()
            face["bbox_mm"] = (np.array(face["bbox_mm"]) * 10).tolist()
    (bundle / "wgreturn.json").write_text(json.dumps(manifest))
    return bundle, manifest, back


@pytest.mark.parametrize("planes", [(), ("x0",), ("x0", "y0")], ids=["full", "half", "quarter"])
@pytest.mark.parametrize("target", [40, 15], ids=["coarse", "fine"])
def test_coupled_ingest_preserves_group_size_and_shared_interface(tmp_path, monkeypatch, planes, target):
    from server.cadlink.intake import ingest
    from server.cadlink.wgreturn import read_wgreturn
    from server.mesh.artifact import read_verified_coupled_exterior_mesh
    from server.mesh.fem_coupling import read_coupled_exterior
    from server.mesh.fem_regions import read_fem_region_domain, physical_name
    from server.solver.coupled_system import interface_topology

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    bundle_path, manifest, back = _scaled_coupled_bundle(tmp_path / "return", planes)
    path = bundle_path / manifest["assembly"]["file"]
    sizes = {"rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {}}
    mode = "auto" if planes else "full"
    counts = {}
    for size in [25, target]:
        options = {"symmetry_mode": mode, "surface_groups": [{**back, "size_mm": size}]}
        exterior = _build(path, manifest, sizes, **options)
        assert exterior["symmetry"]["domain_planes"] == list(planes)
        retained, regions = ingest._mesh_coupled_regions(
            read_wgreturn(bundle_path), tmp_path / f"mesh-{size}", exterior, sizes, options, planes,
        )
        points, groups = read_coupled_exterior(read_verified_coupled_exterior_mesh({"coupled_exterior": retained}))
        triangles = np.concatenate(list(groups.values()))
        counts[size] = np.count_nonzero(np.all(np.isclose(points[triangles, 2], -20), axis=1))
        resolution = retained["surface_group_resolution"]["back"]
        assert resolution["size_mm"] == size
        assert resolution["retained_area_mm2"] == pytest.approx(back["observed"]["total_area_mm2"] / 2**len(planes))
        rigid = [name for name, spec in retained["physical_names"].items() if spec["kind"] == "rigid"]
        assert rigid == ["wg-coupled-v1|rigid"]
        region = manifest["scope"]["fem_regions"][0]
        fem = read_fem_region_domain(regions[region["region_id"]]["msh_text"], region["region_id"])
        interface = physical_name(region["region_id"], "port", "interface")
        facets = fem.surfaces[interface]
        topology = interface_topology(fem.points_mm, fem.tetrahedra, facets, np.arange(len(facets)),
                                     points, groups[interface], np.arange(len(groups[interface])))
        assert topology["max_coordinate_error"] == 0
        assert topology["fem_facets_on_tetra_boundary"] == len(facets)
    if target == 40:
        assert counts[40] < .8 * counts[25]
    else:
        assert counts[15] > 1.7 * counts[25]


def test_sizing_replay_invalidates_only_grouped_mesh_cache_keys(monkeypatch):
    from server.cadlink.intake import ingest

    canonical = ingest._canonical
    captured = []

    def record(payload):
        captured.append(payload)
        return canonical(payload)

    monkeypatch.setattr(ingest, "_canonical", record)
    bundle = SimpleNamespace(artifact_sha256="step", manifest_sha256="manifest")
    manifest = {"sources": [], "instances": [], "coordinate_system": {}}
    sizes = {"rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {}}
    for groups in [None, [], [{"group_id": "back", "size_mm": 40}]]:
        options = {"surface_groups": groups}
        for key_function, extra in [(ingest._cache_key, ["geometry"]), (ingest._cache_lookup_key, [])]:
            key = key_function(bundle, manifest, sizes, [], options, *extra)
            old_payload = {k: v for k, v in captured[-1].items() if k != "surface_sizing_contract"}
            old_key = hashlib.sha256(canonical(old_payload)).hexdigest()
            assert (key != old_key) == bool(groups)


@pytest.mark.parametrize("source_size", [6, 60])
def test_coupled_group_fields_keep_source_clamp_and_grading(tmp_path, monkeypatch, source_size):
    import gmsh
    from server.mesh.fem_coupling import build_coupled_mesh

    bundle, manifest, back = _scaled_coupled_bundle(tmp_path / "return", ())
    path = bundle / manifest["assembly"]["file"]
    sizes = {"rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {}}
    exterior = _build(path, manifest, sizes, symmetry_mode="full", surface_groups=[back])
    recipe = exterior["viewport_recipe"]
    # The front rim is an exposed source, separate from the back sizing group
    # and the FEM diaphragm. Address it in the same original replay order.
    indices = [i for i, (center, _area) in enumerate(recipe["surface_order_reference"])
               if abs(center[2] - 200) < 1e-6]
    assert len(indices) == 1
    field_types, values, lists = {}, {}, {}
    add, set_number = gmsh.model.mesh.field.add, gmsh.model.mesh.field.setNumber
    set_numbers = gmsh.model.mesh.field.setNumbers

    def record_add(kind, *args):
        tag = add(kind, *args)
        field_types[tag] = kind
        return tag

    def record_number(tag, key, value):
        values.setdefault(tag, {})[key] = value
        return set_number(tag, key, value)

    def record_numbers(tag, key, value):
        lists.setdefault(tag, {})[key] = list(value)
        return set_numbers(tag, key, value)

    monkeypatch.setattr(gmsh.model.mesh.field, "setNumbers", record_numbers)
    monkeypatch.setattr(gmsh.model.mesh.field, "add", record_add)
    monkeypatch.setattr(gmsh.model.mesh.field, "setNumber", record_number)
    region = manifest["scope"]["fem_regions"][0]
    _run_in_gmsh_session(
        build_coupled_mesh, path, [(bundle / region["file"], region)], recipe=recipe,
        expected_geometry_hash=exterior["transformed_geometry_hash"],
        normalisation_matrix=exterior["normalisation"]["matrix"],
        sizes={**sizes, "source_size_mm": {"hf": source_size}},
        exterior_names={"wg-import-v1|source=hf|role=HF": {
            "indices": indices, "size_mm": source_size,
            "kind": {"kind": "moving", "drive_channel_id": "drive-hf"},
        }},
    )
    thresholds = [tag for tag, kind in field_types.items() if kind == "Threshold"]
    assert len(thresholds) == 1
    threshold = thresholds[0]
    assert values[threshold]["SizeMin"] == min(source_size, 25)
    assert values[threshold]["SizeMax"] == 40
    assert values[threshold]["DistMax"] == 100
    restrictions = [tag for tag, kind in field_types.items()
                    if kind == "Restrict" and values[tag]["InField"] == threshold]
    assert len(restrictions) == 1
    # Exterior grading is restricted to surfaces, leaving interior FEM volume
    # targets independent of the exterior's distant SizeMax.
    assert lists[restrictions[0]]["SurfacesList"]
    assert "VolumesList" not in lists[restrictions[0]]
