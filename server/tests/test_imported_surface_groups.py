"""G2 surface sizing and legacy mesh-byte regressions (synthetic geometry)."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pytest

from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.imported import ImportedMeshError, RoleResolutionError, build_imported_mesh

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
    with pytest.raises((ImportedMeshError, RoleResolutionError), match=match):
        _build(path, manifest, _sizes(manifest), surface_groups=groups)


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
def test_legacy_mesh_bytes_equal_base_commit(case, group_option, tmp_path):
    if case["name"] in {"synthetic-f1", "synthetic-f2"}:
        path = tmp_path / "assembly.step"
        manifest, _ = _synthetic_return(path, quarter=case["name"] == "synthetic-f2")
    else:
        bundle = FIXTURES / "cadlink-endpoint-oracle" / (case["name"] + ".wgreturn")
        path = bundle / "assembly.step"
        manifest = json.loads((bundle / "wgreturn.json").read_text())
    result = _build(path, manifest, case["sizes"], **case["options"], **group_option)
    assert hashlib.sha256(result["msh_text"].encode()).hexdigest() == case["msh_sha256"]
