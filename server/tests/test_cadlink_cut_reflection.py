"""A model already cut in CAD is recovered as the reduced domain of the whole speaker.

Stage 3, branch 2 of the CAD Link simplification (``server/cadlink/cut_recovery.py``,
``server.mesh.imported.reflect_triangle_mesh``). A cut the geometry shows is
recovered when every flip condition holds and refused otherwise, naming the
condition; a cut that kept the negative side is reflected at mesh level onto
the positive side the solver mirrors. It is never solved as an open shell in
free space.

The integration fixtures are small real gmsh models put through the production
ingest (bundle reader, the isolated mesher child, record publication).
"""

from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink import cut_recovery as cr
from server.cadlink import domain_decision as dd
from server.cadlink.domain_interpretation import Observations, PlaneObservation
from server.cadlink.frame_infer import parse_tagged_msh
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.imported import (
    ImportedMeshError,
    build_imported_mesh,
    reflect_triangle_mesh,
)
from test_cadlink_domain_automatic import (
    HORN_THROAT,
    _bundle,
    _cut_open,
    _drop_faces,
    _faces_near,
    _ingest,
    _negative_half,
    _solve_verdicts,
    _surfaces_only,
)


gmsh = pytest.importorskip("gmsh")

_ENGINES = ("auto", "metal", "beat", "beat-cpu", "bempp")


# ------------------------------------------------------------------ geometry


def _cut_box(
    path: Path,
    *,
    x_side: int = -1,
    y_side: int = 0,
    discs: tuple[tuple[float, float, float], ...] = ((0.0, 15.0, 10.0),),
    open_bottom: bool = False,
    z_range: tuple[float, float] = (-80.0, 0.0),
    side_disc: bool = False,
    z_keep: tuple[float, float] | None = None,
) -> None:
    """A box speaker (x, y in [-60, 60] x [-40, 40]) cut open in CAD.

    Drivers are discs on the top face (z = z_range[1]); ``side_disc`` adds one
    on the x = +60 wall centred on z = 0; ``open_bottom`` leaves the bottom
    wall out (another opening). Then ``x_side``/``y_side`` keep one side of
    x = 0 / y = 0 (-1 negative, +1 positive, 0 all of it) and ``z_keep`` a z
    range, each cut left OPEN -- no face on the cut plane, as an open Fusion
    cut leaves it (``_cut_open``).
    """

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        z0, z1 = z_range
        box = occ.addBox(-60.0, -40.0, z0, 120.0, 80.0, z1 - z0)
        tools = [(2, occ.addDisk(cx, cy, z1, radius, radius)) for cx, cy, radius in discs]
        if side_disc:
            disc = occ.addDisk(0.0, 0.0, 0.0, 12.0, 12.0)
            occ.rotate([(2, disc)], 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, math.pi / 2)
            occ.translate([(2, disc)], 60.0, 0.0, 0.0)
            tools.append((2, disc))
        occ.fragment([(3, box)], tools)
        _surfaces_only()
        if open_bottom:
            _drop_faces(lambda bbox, _tag: abs(bbox[2] - z0) < 1e-6 and abs(bbox[5] - z0) < 1e-6)
        gmsh.model.occ.healShapes(sewFaces=True, makeSolids=False)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)
    keep: dict[str, tuple[float, float]] = {}
    for letter, side in (("x", x_side), ("y", y_side)):
        if side:
            keep[letter] = (0.0, math.inf) if side > 0 else (-math.inf, 0.0)
    if z_keep is not None:
        keep["z"] = z_keep
    if keep:
        _cut_open(path, keep)


def _half_disc_centre(cx: float, cy: float, radius: float, x_side: int, y_side: int) -> tuple[float, float, float]:
    """The centroid of the part of a disc on the kept side(s) of the cut(s)."""

    offset = 4.0 * radius / (3.0 * math.pi)
    return (cx + x_side * offset if cx == 0.0 and x_side else cx,
            cy + y_side * offset if cy == 0.0 and y_side else cy, 0.0)


def _box_bundle(tmp_path: Path, name: str, *, sources: list[str] | None = None, **box: Any) -> Path:
    x_side, y_side = int(box.get("x_side", -1)), int(box.get("y_side", 0))
    discs = box.get("discs", ((0.0, 15.0, 10.0),))
    centres = [_half_disc_centre(cx, cy, radius, x_side, y_side) for cx, cy, radius in discs]
    return _bundle(
        tmp_path, name, lambda path: _cut_box(path, **box), _faces_near(*centres), sources=sources,
    )


def _mesh(record: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points_m, triangles, tags, _names = parse_tagged_msh(Path(record["mesh_store_path"]).read_text())
    return np.asarray(points_m, float) * 1000.0, np.asarray(triangles, np.int64), np.asarray(tags)


def _normals(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    cross = np.cross(points[triangles[:, 1]] - points[triangles[:, 0]], points[triangles[:, 2]] - points[triangles[:, 0]])
    return cross / np.linalg.norm(cross, axis=1)[:, None]


def _mirror_expanded(points: np.ndarray, triangles: np.ndarray, planes: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """The whole model the solver's mirrors describe: every image, welded on the planes."""

    axes = [{"x0": 0, "y0": 1}[plane] for plane in planes]
    images_points, images_triangles = [], []
    for mask in range(2 ** len(axes)):
        flip = [axis for bit, axis in enumerate(axes) if mask >> bit & 1]
        image = points.copy()
        for axis in flip:
            image[:, axis] *= -1.0
        tris = triangles[:, [0, 2, 1]] if len(flip) % 2 else triangles
        images_triangles.append(tris + sum(len(item) for item in images_points))
        images_points.append(image)
    stacked = np.vstack(images_points)
    keys = np.round(stacked, 6)
    _unique, weld = np.unique(keys, axis=0, return_inverse=True)
    return _unique, weld.reshape(-1)[np.vstack(images_triangles)]


def _assert_closed_and_outward(points: np.ndarray, triangles: np.ndarray) -> None:
    """Closed: every edge has two faces. Oriented: each directed edge occurs once. Outward: V > 0."""

    directed = triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    undirected = Counter(map(tuple, np.sort(directed, axis=1).tolist()))
    assert set(undirected.values()) == {2}, Counter(undirected.values())
    assert max(Counter(map(tuple, directed.tolist())).values()) == 1
    volume = float(
        np.einsum("ij,ij->i", points[triangles[:, 0]], np.cross(points[triangles[:, 1]], points[triangles[:, 2]])).sum()
        / 6.0
    )
    assert volume > 0.0


def _decision(record: dict[str, Any]) -> dict[str, Any]:
    decision = record["domain_decision"]
    assert dd.decision_problem(record) is None
    return decision


# ------------------------------------------------------------------ the reflection itself


def _tetra_half() -> tuple[np.ndarray, np.ndarray]:
    """An open negative-x shell: the x <= 0 half of an octahedron, wound outward."""

    points = np.array(
        [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]]
    )
    triangles = np.array([[0, 3, 1], [0, 2, 3], [0, 1, 4], [0, 4, 2]])
    return points, triangles


@pytest.mark.parametrize(
    ("planes", "parity"),
    [(["x0"], 1), (["y0"], 1), (["x0", "y0"], 0), (["y0", "x0"], 0)],
)
def test_reflection_negates_coordinates_and_reverses_winding_once_per_plane(
    planes: list[str], parity: int
) -> None:
    points, triangles = _tetra_half()
    tags = np.array([1, 1, 101, 101])
    reflected, wound, record = reflect_triangle_mesh(points, triangles, planes, tags=tags)

    matrix = np.diag([-1.0 if "x0" in planes else 1.0, -1.0 if "y0" in planes else 1.0, 1.0])
    assert np.array_equal(reflected, points @ matrix.T)
    # Winding: reversed for one reflection, kept for two (a rotation by 180 degrees).
    assert np.array_equal(wound, triangles[:, [0, 2, 1]] if parity else triangles)
    # Each normal n becomes R n: the orientation (outward) is carried over.
    assert np.allclose(_normals(reflected, wound), _normals(points, triangles) @ matrix.T)
    assert record["parity"] == parity and record["winding_reversed"] is bool(parity)
    assert record["planes"] == [plane for plane in ("x0", "y0") if plane in planes]
    assert record["axes"] == [plane[0] for plane in record["planes"]]
    assert record["determinant"] == pytest.approx(np.linalg.det(matrix))
    assert record["max_normal_error"] == 0.0 and record["max_area_error"] == 0.0
    assert record["tag_areas"]["101"]["before"] == record["tag_areas"]["101"]["after"] > 0.0


def test_reflection_refuses_planes_the_solver_does_not_mirror() -> None:
    points, triangles = _tetra_half()
    with pytest.raises(ImportedMeshError, match="only across x0, y0"):
        reflect_triangle_mesh(points, triangles, ["z0"])
    with pytest.raises(ImportedMeshError, match="must not repeat"):
        reflect_triangle_mesh(points, triangles, ["x0", "x0"])


def test_the_mesher_reflects_only_a_cut_it_was_asked_to_mirror(tmp_path: Path) -> None:
    import json

    bundle = _box_bundle(tmp_path, "guard")
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    sizes = {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}}
    step = bundle / "assembly.step"
    with pytest.raises(ImportedMeshError, match="only for a cut WG read from the model"):
        _run_in_gmsh_session(build_imported_mesh, step, manifest, sizes, options={"reflect_planes": ["x0"]})
    with pytest.raises(ImportedMeshError, match="distinct cut planes"):
        _run_in_gmsh_session(
            build_imported_mesh, step, manifest, sizes,
            options={
                "declared_cut_planes": ["x0"],
                "reflect_planes": ["y0"],
                "domain_interpretation": {"applied": ["x0"], "recovered": {}},
            },
        )


# ------------------------------------------------------------------ recovered on real geometry


def test_a_negative_half_is_reflected_onto_the_positive_side_with_its_normals(tmp_path: Path) -> None:
    record = _ingest(_box_bundle(tmp_path, "neg-box"), tmp_path / "data")
    decision = _decision(record)
    points, triangles, tags = _mesh(record)

    # Plane occupancy: the final solver mesh is entirely on x >= 0, open on x = 0.
    assert points[:, 0].min() == 0.0 and points[:, 0].max() == pytest.approx(60.0)
    assert record["symmetry"]["domain_planes"] == ["x0"]
    assert record["symmetry"]["cut_planes"] == []  # the drivers are not y-symmetric
    assert record["symmetry_verification"]["verified"] is True
    assert record["symmetry_verification"]["detection"]["plane_vertex_side_counts"]["x0"]["negative"] == 0
    # The mesher found nothing left to reflect, and repaired no winding.
    assert record["mesh"]["metadata"]["topology"]["axis_normalization"]["reflected_axes"] == []
    repair = record["mesh"]["metadata"]["postprocess"]
    assert repair["flipped_global"] == repair["flipped_consistency"] == 0
    assert repair["symmetry_parent_volume_flipped"] == 0
    assert record["symmetry_verification"]["reduced_orientation"]["inverted_component_count"] == 0

    # Normals: every face points out of the box, the driver's included.
    normals = _normals(points, triangles)
    centres = points[triangles].mean(axis=1)
    source_tag = record["source_tags"]["throat"]
    assert np.allclose(normals[tags == source_tag], [0.0, 0.0, 1.0], atol=1e-9)
    far_wall = np.isclose(centres[:, 0], 60.0)
    assert far_wall.any() and np.allclose(normals[far_wall], [1.0, 0.0, 0.0], atol=1e-9)
    for axis, value in ((1, 40.0), (1, -40.0), (2, -80.0)):
        wall = np.isclose(centres[:, axis], value)
        expected = np.zeros(3)
        expected[axis] = math.copysign(1.0, value)
        assert np.allclose(normals[wall], expected, atol=1e-9)

    # Closed and outward once mirrored: the whole box the solver describes.
    _assert_closed_and_outward(*_mirror_expanded(points, triangles, ["x0"]))

    # The record: the reflection apart from the proper frame.
    assert record["reflection"]["axes"] == ["x"] and record["reflection"]["determinant"] == -1.0
    assert record["reflection"]["source_areas_preserved"] is True
    assert decision["reflected_axes"] == ["x"]
    assert decision["frame"]["determinant"] == pytest.approx(1.0)
    assert decision["frame"]["solver_from_cad"] == np.eye(4).tolist()
    assert (decision["frame"]["axis"], decision["frame"]["allowed_axes"]) == ("+z", ["+z"])
    assert record["normalisation"]["matrix"] == np.eye(4).tolist()
    plan, outcomes = _solve_verdicts(record)
    assert plan["code"] is None and plan["engine"] == "metal"
    assert "imported_open_half_shell" not in outcomes.values()


def test_the_reflected_mesh_is_the_arrived_mesh_mirrored(tmp_path: Path) -> None:
    """Coordinates: the solve mesh is the kept side's own mesh, x negated, nothing else."""

    import json

    bundle = _box_bundle(tmp_path, "mirror-of")
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    sizes = {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}}

    def build(options: dict[str, Any]) -> dict[str, Any]:
        return _run_in_gmsh_session(
            build_imported_mesh, bundle / "assembly.step", manifest, sizes,
            options={"symmetry_mode": "auto", **options}, include_viewport_mesh=False,
        )

    arrived_build = build({})
    recovered_build = build(
        {
            "declared_cut_planes": ["x0"],
            "reflect_planes": ["x0"],
            "domain_interpretation": {"applied": ["x0"], "recovered": {"reflect": ["x0"]}},
        }
    )
    assert arrived_build["symmetry"]["domain_planes"] == []
    assert recovered_build["symmetry"]["domain_planes"] == ["x0"]

    def arrays(built: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        points_m, tris, tag_values, _names = parse_tagged_msh(built["msh_text"])
        return np.asarray(points_m, float) * 1000.0, np.asarray(tris, np.int64), np.asarray(tag_values)

    arrived, arrived_triangles, arrived_tags = arrays(arrived_build)
    points, triangles, tags = arrays(recovered_build)
    assert len(points) == len(arrived) and len(triangles) == len(arrived_triangles)
    mirrored = arrived * np.array([-1.0, 1.0, 1.0])
    assert np.array_equal(np.unique(np.round(points, 9), axis=0), np.unique(np.round(mirrored, 9), axis=0))

    def faces(pts: np.ndarray, tris: np.ndarray, tag_values: np.ndarray) -> Counter[Any]:
        return Counter(
            (int(tag), tuple(sorted(tuple(np.round(pts[i], 9).tolist()) for i in tri)))
            for tri, tag in zip(tris.tolist(), tag_values.tolist(), strict=True)
        )

    # The same triangles, each keeping its own tag.
    assert faces(points, triangles, tags) == faces(mirrored, arrived_triangles, arrived_tags)
    reflection = recovered_build["reflection"]
    for tag, areas in reflection["tag_areas"].items():
        assert areas["before"] == areas["after"]
        assert reflection["final_tag_areas"][tag] == pytest.approx(areas["before"], rel=1e-12)
    assert recovered_build["tag_allocation"] == arrived_build["tag_allocation"]
    assert recovered_build["post_cut_source_areas"] == arrived_build["post_cut_source_areas"]


@pytest.mark.parametrize(("x_side", "y_side"), [(1, 1), (-1, 1), (1, -1), (-1, -1)])
def test_a_quarter_in_each_quadrant_is_the_same_positive_quarter(
    tmp_path: Path, x_side: int, y_side: int
) -> None:
    record = _ingest(
        _box_bundle(tmp_path, f"quarter-{x_side}{y_side}", x_side=x_side, y_side=y_side, discs=((0.0, 0.0, 10.0),)),
        tmp_path / "data",
    )
    decision = _decision(record)
    points, triangles, tags = _mesh(record)

    reflected = [axis for axis, side in (("x", x_side), ("y", y_side)) if side < 0]
    assert decision["reflected_axes"] == reflected
    assert decision["reflection"]["parity"] == len(reflected) % 2
    assert decision["reflection"]["winding_reversed"] is (len(reflected) == 1)
    assert [cut["kept_side"] for cut in decision["cad_cuts"]] == [
        "negative" if x_side < 0 else "positive", "negative" if y_side < 0 else "positive",
    ]
    assert {cut["status"] for cut in decision["cad_cuts"]} == {"recovered"}
    assert decision["solver_domain"] == {"planes": ["x0", "y0"], "fraction": "quarter", "multiplier": 4}
    assert decision["refusal"] is None
    # Every quadrant arrives at the same positive quarter, wound outward.
    assert points.min(axis=0).tolist() == [0.0, 0.0, -80.0]
    assert points.max(axis=0) == pytest.approx([60.0, 40.0, 0.0])
    areas = 0.5 * np.linalg.norm(np.cross(points[triangles[:, 1]] - points[triangles[:, 0]],
                                          points[triangles[:, 2]] - points[triangles[:, 0]]), axis=1)
    assert areas.sum() == pytest.approx(60 * 40 + 60 * 40 + 60 * 80 + 40 * 80, rel=1e-9)
    source_tag = record["source_tags"]["throat"]
    # The CAD quarter disc is what is kept; its facets inscribe the arc.
    assert decision["sources"]["by_id"]["throat"]["retained_area_mm2"] == pytest.approx(math.pi * 100.0 / 4.0, rel=1e-6)
    assert 0.85 * math.pi * 100.0 / 4.0 < areas[tags == source_tag].sum() < math.pi * 100.0 / 4.0
    assert np.allclose(_normals(points, triangles)[tags == source_tag], [0.0, 0.0, 1.0], atol=1e-9)
    _assert_closed_and_outward(*_mirror_expanded(points, triangles, ["x0", "y0"]))


def test_a_curved_negative_half_mirrors_back_to_a_closed_outward_horn(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "horn-negative", _negative_half, HORN_THROAT), tmp_path / "data")
    points, triangles, _tags = _mesh(record)
    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert points[:, 0].min() >= 0.0 and points[:, 1].min() >= 0.0
    _assert_closed_and_outward(*_mirror_expanded(points, triangles, ["x0", "y0"]))


def test_sources_keep_identity_tag_area_channel_and_excitation_through_the_reflection(tmp_path: Path) -> None:
    bundle = _box_bundle(
        tmp_path, "two-drivers", sources=["mid", "tweeter"],
        discs=((0.0, 15.0, 10.0), (0.0, -22.0, 6.0)),
    )
    record = _ingest(bundle, tmp_path / "data")
    decision = _decision(record)
    points, triangles, tags = _mesh(record)

    by_id = decision["sources"]["by_id"]
    assert sorted(by_id) == ["mid", "tweeter"]
    assert by_id["mid"]["tag"] != by_id["tweeter"]["tag"]
    projections = record["mesh"]["metadata"]["topology"]["source_normal_projections"]
    for source_id, radius in (("mid", 10.0), ("tweeter", 6.0)):
        item = by_id[source_id]
        tag = record["source_tags"][source_id]
        assert item["tag"] == tag and item["channel"] == f"drive-{source_id}"
        assert item["reflected"]["axes"] == ["x"]
        assert item["reflected"]["meshed_area_before_mm2"] == pytest.approx(
            item["reflected"]["meshed_area_after_mm2"], rel=1e-12
        )
        # The CAD half-disc, measured before meshing, is what is kept.
        assert item["retained_area_mm2"] == pytest.approx(math.pi * radius**2 / 2.0, rel=1e-6)
        assert item["retained_fraction"] == pytest.approx(1.0)
        # Excitation: a piston along its outward normal, +z, over the same
        # area (the projection sums the facets' doubled-area normals).
        vector = projections[source_id]["vector_step_units2"]
        assert vector[2] == pytest.approx(2.0 * item["reflected"]["meshed_area_after_mm2"], rel=1e-9)
        assert abs(vector[0]) < 1e-9 and abs(vector[1]) < 1e-9
        assert np.all(points[np.unique(triangles[tags == tag])][:, 0] >= 0.0)
    # Each channel drives its own source, once, on the solve every engine gets.
    from server.cadlink.domain_interpretation import excitation_problem

    channels = [{"id": f"drive-{sid}", "source_ids": [sid], "motion": "normal"} for sid in ("mid", "tweeter")]
    assert excitation_problem(record, channels) is None
    _, outcomes = _solve_verdicts(record)
    assert "imported_open_half_shell" not in outcomes.values()


# ------------------------------------------------------------------ refused, naming the condition


def test_separately_identified_left_and_right_sources_refuse_the_reduction(tmp_path: Path) -> None:
    record = _ingest(_box_bundle(tmp_path, "left", sources=["woofer-left"]), tmp_path / "data")
    decision = _decision(record)

    assert decision["solver_domain"]["planes"] == []
    [cut] = decision["cad_cuts"]
    assert (cut["kept_side"], cut["status"]) == ("negative", "refused")
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.SIDE_IDENTIFIED_SOURCE]
    assert decision["refusal"]["code"] == dd.OPEN_HALF_CODE
    assert "source woofer-left is identified as the left one" in decision["refusal"]["message"]
    _, outcomes = _solve_verdicts(record)
    assert outcomes == {engine: "imported_open_half_shell" for engine in _ENGINES}
    # Positive control: the same model with an unsided source is recovered.
    control = _ingest(_box_bundle(tmp_path, "unsided", sources=["woofer"]), tmp_path / "data-control")
    assert _decision(control)["solver_domain"]["planes"] == ["x0"]


def test_a_cut_square_to_the_radiation_axis_is_refused(tmp_path: Path) -> None:
    """A model open along z = 0 with a driver the plane passes through: a front/back cut."""

    bundle = _bundle(
        tmp_path, "front-back",
        lambda path: _cut_box(
            path, x_side=0, discs=(), z_range=(-80.0, 80.0), side_disc=True, z_keep=(-math.inf, 0.0)
        ),
        _faces_near((60.0, 0.0, -4.0 * 12.0 / (3.0 * math.pi))),
    )
    record = _ingest(bundle, tmp_path / "data")
    decision = _decision(record)
    cut = next(item for item in decision["cad_cuts"] if item["plane"] == "z0")
    assert cut["status"] == "refused"
    assert cr.FRONT_BACK in [item["code"] for item in cut["recovery"]["failed"]]
    assert "square to the radiation axis (+z)" in decision["refusal"]["message"]
    assert decision["solver_domain"]["planes"] == []


def test_a_cut_with_another_opening_is_refused(tmp_path: Path) -> None:
    record = _ingest(_box_bundle(tmp_path, "open-bottom", open_bottom=True), tmp_path / "data")
    decision = _decision(record)
    assert record["domain_interpretation"]["observations"]["other_open_edges"] > 0
    [cut] = decision["cad_cuts"]
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.OTHER_OPENINGS]
    assert "other open edge(s)" in decision["refusal"]["message"]
    assert decision["solver_domain"]["planes"] == []


def _observations(**plane: Any) -> Observations:
    defaults = dict(
        negative=100, positive=0, on_plane=20, rim_edges=20, cap_triangles=0, cap_area_mm2=0.0,
        rigid_cut_rim_edges=20, solver_plane="x0", sources_on_plane=["driver"], sources_bisected=["driver"],
    )
    x0 = PlaneObservation(plane="x0", **{**defaults, **plane})
    quiet = dict(negative=50, positive=50, on_plane=0, rim_edges=0, cap_triangles=0, cap_area_mm2=0.0)
    return Observations(
        planes={
            "x0": x0,
            "y0": PlaneObservation(plane="y0", solver_plane="y0", **quiet),
            "z0": PlaneObservation(plane="z0", solver_plane="z0", **quiet),
        },
        other_open_edges=0,
        free_edges=20,
    )


@pytest.mark.parametrize(
    ("change", "code", "words"),
    [
        (dict(positive=5), cr.CROSSING, "geometry crosses x = 0"),
        (dict(cap_triangles=12), cr.CAPPED, "would solve as a wall across the cut"),
        (dict(sources_on_plane=[], sources_bisected=[]), cr.NO_SOURCE_ON_PLANE, "no source meets x = 0"),
    ],
)
def test_each_failed_flip_condition_is_named(change: dict[str, Any], code: str, words: str) -> None:
    verdict = cr.assess_cut_recovery(_observations(**change), sources=[{"id": "driver"}], radiation_axis="+z")
    assert verdict.planes == ("x0",) and not verdict.recoverable
    assert [item.code for item in verdict.failures["x0"]] == [code]
    assert words in (verdict.reason() or "")


def test_shared_flip_conditions_are_named_on_every_cut() -> None:
    observations = _observations()
    observations.other_open_edges = 7
    verdict = cr.assess_cut_recovery(
        observations,
        sources=[{"id": "driver", "selectors": {"appearance_labels": ["HF_R"]}}],
        radiation_axis="+x",
        identity_problem="source driver could not be identified by its own faces",
        integrity_problem="the model intersects itself",
    )
    assert [item.code for item in verdict.failures["x0"]] == [
        cr.FRONT_BACK, cr.OTHER_OPENINGS, cr.SOURCE_IDENTITY, cr.SIDE_IDENTIFIED_SOURCE, cr.SELF_INTERSECTION,
    ]
    assert "7 other open edge(s)" in (verdict.reason() or "")
    assert "identified as the right one" in (verdict.reason() or "")
    # All or nothing: a failure found later (the mesher) is every cut's.
    denied = cr.assess_cut_recovery(_observations(), sources=[], radiation_axis="+z").with_failure(
        cr.Failure(cr.MESH_DENIED, "its reduced mesh does not verify")
    )
    assert not denied.recoverable and denied.reflect == ()


def test_a_clean_negative_cut_is_recoverable_by_reflection() -> None:
    verdict = cr.assess_cut_recovery(_observations(), sources=[{"id": "driver", "role": "LF"}], radiation_axis="+z")
    assert verdict.recoverable and verdict.reflect == ("x0",)
    assert verdict.to_json()["kept_sides"] == {"x0": "negative"}


@pytest.mark.parametrize(
    ("source", "side"),
    [
        ({"id": "woofer-left"}, "left"),
        ({"id": "LeftWoofer"}, "left"),
        ({"id": "s1", "selectors": {"appearance_labels": ["HF_R"]}}, "right"),
        ({"id": "s2", "selectors": {"shell_names": ["Right tweeter"]}}, "right"),
        ({"id": "wgs-M4908B04CKKRTSFQV4KC", "role": "LF"}, None),
        ({"id": "throat", "role": "HF", "selectors": {"appearance_labels": ["LF", "MF"]}}, None),
        # Acronym and digit boundaries (review S3-2b, finding 1).
        ({"id": "MFLeft"}, "left"),
        ({"id": "MFRight"}, "right"),
        ({"id": "LeftMF"}, "left"),
        ({"id": "LF_R"}, "right"),
        ({"id": "TweeterR"}, "right"),
        ({"id": "woofer2Left"}, "left"),
        ({"id": "Left2"}, "left"),
        ({"id": "LH woofer"}, "left"),
        ({"id": "FrontL"}, "left"),
        # Not sides: words that merely start with L or R, all-capital runs.
        ({"id": "Rear"}, None),
        ({"id": "RearWoofer"}, None),
        ({"id": "Throat"}, None),
        ({"id": "Front"}, None),
        ({"id": "Rotor"}, None),
        ({"id": "Lower"}, None),
        ({"id": "MFL"}, None),
        ({"id": "LFR"}, None),
        ({"id": "HFr"}, None),
        ({"id": "wgs-R4908B04CKKRTSFQV4KC"}, None),
        ({"id": "Mid 2"}, None),
        # Re-review S3-2c: minted ids are never names; more boundaries and words.
        ({"id": "wgs-QBQ26C6122140K1H055R"}, None),
        ({"id": "wgs-QBQ26C6122140K1H0R55"}, None),
        ({"id": "wgs-QBQ26C6122140K1H0L55"}, "left"),  # L is not Crockford: a user's id
        ({"id": "wgs-QBQ26C6122140K1H055R", "selectors": {"appearance_labels": ["HF_R"]}}, "right"),
        ({"id": "woofer_L1"}, "left"),
        ({"id": "wooferL2"}, "left"),
        ({"id": "L1"}, "left"),
        ({"id": "R1"}, "right"),
        ({"id": "MFRIGHT"}, "right"),
        ({"id": "Woofer_Rt"}, "right"),
        ({"id": "MF_Lft"}, "left"),
        ({"id": "mfleft"}, "left"),
        ({"id": "wooferleft"}, "left"),
        ({"id": "leftwoofer"}, "left"),
        ({"id": "Rgt tweeter"}, "right"),
        ({"id": "Leftover"}, None),
        ({"id": "Lateral"}, None),
        ({"id": "Rim"}, None),
        ({"id": "Lf"}, None),
        ({"id": "LR"}, None),
        ({"id": "TL"}, None),
        ({"id": "Rhorn"}, None),
        ({"id": "ROUND"}, None),
        ({"id": "LEDRing"}, None),
        ({"id": "upright"}, None),
        ({"id": "Bright"}, None),
        ({"id": "source-hf"}, None),
        ({"id": "s1", "role": "LF", "name": "Rear port"}, None),
        # Glued position words are sided; a short list of ordinary words is not.
        ({"id": "frontleft"}, "left"),
        ({"id": "bottomleft"}, "left"),
        ({"id": "upperleft"}, "left"),
        ({"id": "lowerright"}, "right"),
        ({"id": "rearright"}, "right"),
        ({"id": "leftside"}, "left"),
        ({"id": "farleft"}, "left"),
        ({"id": "rightangle"}, None),
        ({"id": "Brightness"}, None),
        ({"id": "Leftovers"}, None),
        ({"id": "copyright"}, None),
        ({"id": "cleft"}, None),
        ({"id": "MFR"}, None),
        ({"id": "HFR"}, None),
        ({"id": "WOOFERL"}, None),
    ],
)
def test_a_source_is_sided_only_by_its_own_words(source: dict[str, Any], side: str | None) -> None:
    assert cr.side_of_source(source) == side


def test_a_recovered_decision_offers_nothing_and_says_how_it_was_found(tmp_path: Path) -> None:
    record = _ingest(_box_bundle(tmp_path, "finding"), tmp_path / "data")
    finding = next(item for item in record["findings"] if item["kind"] == "recovered-reduced-domain")
    assert finding["blocking"] is False
    assert finding["reflected_planes"] == ["x0"]
    assert "the kept x ≤ 0 side was reflected" in finding["detail"]
    interpretation = record["domain_interpretation"]
    assert interpretation["choices"] == [] and interpretation["reflected_planes"] == ["x0"]
    summary = dd.decision_summary(record)
    assert summary["reflected_axes"] == ["x"]
    assert summary["cad_cuts"] == [
        {"plane": "x0", "solver_plane": "x0", "kept_side": "negative", "found_by": "geometry",
         "status": "recovered", "reflected": True},
    ]


def test_a_rim_on_another_plane_blocks_unless_it_is_a_cut_judged_too() -> None:
    """One list of failed conditions: another plane's open rim is one of them."""

    observations = _observations()
    observations.planes["z0"].rim_edges = 12
    verdict = cr.assess_cut_recovery(observations, sources=[{"id": "driver"}], radiation_axis="+z")
    assert [item.code for item in verdict.failures["x0"]] == ["open-rim-on-z0"]
    assert "also open along z = 0 (12 rim edges)" in (verdict.reason() or "")
    # A quarter's second plane is judged as a cut in its own right, not a blocker.
    quarter = _observations()
    quarter.planes["y0"] = PlaneObservation(
        plane="y0", negative=100, positive=0, on_plane=20, rim_edges=20, cap_triangles=0,
        cap_area_mm2=0.0, rigid_cut_rim_edges=20, solver_plane="y0",
        sources_on_plane=["driver"], sources_bisected=["driver"],
    )
    both = cr.assess_cut_recovery(quarter, sources=[{"id": "driver"}], radiation_axis="+z")
    assert both.planes == ("x0", "y0") and both.recoverable and both.reflect == ("x0", "y0")


# ------------------------------------------------------------------ review S3-2 reproducers


def test_provenance_never_overrides_a_front_back_cut_or_resets_the_confirmed_frame(tmp_path: Path) -> None:
    """Review finding 1: a negative y0 cut facing -y is refused with and without provenance."""

    from server.cadlink.ingest import IngestRefusal
    from test_cadlink_domain_automatic import provenance

    box = dict(x_side=0, y_side=-1, discs=((15.0, 0.0, 10.0),))
    automatic = _ingest(_box_bundle(tmp_path, "front-back-auto", **box), tmp_path / "data-auto", solver_frame="-y")
    decision = _decision(automatic)
    assert decision["solver_domain"]["planes"] == [] and decision["frame"]["axis"] == "-y"
    assert "square to the radiation axis (-y)" in decision["refusal"]["message"]
    _, outcomes = _solve_verdicts(automatic)
    assert set(outcomes.values()) == {"imported_open_half_shell"}

    x_side, y_side = 0, -1
    centre = _half_disc_centre(15.0, 0.0, 10.0, x_side, y_side)
    bundle = _bundle(
        tmp_path, "front-back-provenance", lambda path: _cut_box(path, **box), _faces_near(centre),
        cut=[provenance("body-0", "y0", kept_side="negative")],
    )
    with pytest.raises(IngestRefusal, match=r"square to the radiation axis \(-y\)"):
        _ingest(bundle, tmp_path / "data-provenance", solver_frame="-y")


def test_a_cut_model_confirmed_to_face_another_way_is_never_mirrored_by_resetting_its_frame(
    tmp_path: Path,
) -> None:
    """The plane contains the confirmed axis, but a mirror would need +z: refused, frame kept."""

    from server.cadlink.ingest import IngestRefusal
    from test_cadlink_domain_automatic import provenance

    record = _ingest(_box_bundle(tmp_path, "facing-x"), tmp_path / "data", solver_frame="-y")
    decision = _decision(record)
    assert decision["frame"]["axis"] == "-y" and decision["solver_domain"]["planes"] == []
    [cut] = decision["cad_cuts"]
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.FRAME_NOT_MODELLED]
    assert "change the frame you confirmed" in decision["refusal"]["message"]
    bundle = _bundle(
        tmp_path, "facing-x-provenance", lambda path: _cut_box(path), _faces_near(_half_disc_centre(0.0, 15.0, 10.0, -1, 0)),
        cut=[provenance("body-0", "x0", kept_side="negative")],
    )
    with pytest.raises(IngestRefusal, match="change the frame you confirmed"):
        _ingest(bundle, tmp_path / "data-provenance", solver_frame="-y")


def test_a_cut_through_an_off_centre_driver_is_refused(tmp_path: Path) -> None:
    """Review finding 2: the driver touches the plane but is not bisected by it."""

    whole_area = math.pi * 100.0
    record = _ingest(
        _bundle(tmp_path, "off-centre-driver", lambda path: _cut_box(path, discs=((-5.0, 15.0, 10.0),)),
                _faces_near((-7.0, 15.0, 0.0))),
        tmp_path / "data",
    )
    decision = _decision(record)
    # Mirrored, the retained 252.7 mm2 would become 505.5 mm2, not 314.2 mm2.
    retained = decision["sources"]["by_id"]["throat"]["retained_area_mm2"]
    assert 2.0 * retained > 1.5 * whole_area
    assert decision["solver_domain"]["planes"] == []
    [cut] = decision["cad_cuts"]
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.ASYMMETRIC_SECTION]
    assert "at an angle (up to 30.0 degrees from square" in decision["refusal"]["message"]
    _, outcomes = _solve_verdicts(record)
    assert set(outcomes.values()) == {"imported_open_half_shell"}


def _box_with_port(path: Path, *, port_x: float) -> None:
    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        box = occ.addBox(-60.0, -40.0, -80.0, 120.0, 80.0, 80.0)
        tube = occ.addCylinder(port_x, -20.0, -90.0, 0.0, 0.0, 100.0, 10.0)
        shape, _ = occ.cut([(3, box)], [(3, tube)])
        occ.fragment(shape, [(2, occ.addDisk(0.0, 15.0, 0.0, 10.0, 10.0))])
        _surfaces_only()
        occ.healShapes(sewFaces=True, makeSolids=False)
        occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(build)
    _cut_open(path, {"x": (-math.inf, 0.0)})


@pytest.mark.parametrize(("port_x", "recovered"), [(-5.0, False), (0.0, True)], ids=["off-axis-port", "centred-port"])
def test_a_cut_through_an_off_axis_port_is_refused(tmp_path: Path, port_x: float, recovered: bool) -> None:
    """Review finding 2: the cut reshapes an off-axis port; a centred one is mirrored."""

    record = _ingest(
        _bundle(tmp_path, f"port-{port_x}", lambda path: _box_with_port(path, port_x=port_x),
                _faces_near((-4.0 * 10.0 / (3.0 * math.pi), 15.0, 0.0))),
        tmp_path / "data",
    )
    decision = _decision(record)
    assert (decision["solver_domain"]["planes"] == ["x0"]) is recovered
    if recovered:
        assert decision["refusal"] is None and decision["reflected_axes"] == ["x"]
    else:
        [cut] = decision["cad_cuts"]
        assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.ASYMMETRIC_SECTION]
        _, outcomes = _solve_verdicts(record)
        assert set(outcomes.values()) == {"imported_open_half_shell"}


def test_a_cut_on_an_oblique_plane_is_refused(tmp_path: Path) -> None:
    """Review finding 3: a clean half rotated 20 degrees is a cut, not an open sheet."""

    angle = math.radians(20.0)

    def geometry(path: Path) -> None:
        _cut_box(path)

        def rotate() -> None:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.clear()
            occ = gmsh.model.occ
            shapes = occ.importShapes(str(path), highestDimOnly=True)
            occ.rotate(shapes, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, angle)
            occ.synchronize()
            gmsh.write(str(path))
            gmsh.clear()

        _run_in_gmsh_session(rotate)

    x, y = -4.0 * 10.0 / (3.0 * math.pi), 15.0
    centre = (x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle), 0.0)
    record = _ingest(_bundle(tmp_path, "oblique", geometry, _faces_near(centre)), tmp_path / "data")
    decision = _decision(record)
    [oblique] = decision["oblique_cuts"]
    assert oblique["normal"] == pytest.approx([-math.cos(angle), -math.sin(angle), 0.0], abs=1e-6)
    assert decision["input_reading"] == dd.INPUT_CUT
    assert decision["solver_domain"]["planes"] == []
    assert "cut on an oblique plane" in decision["refusal"]["message"]
    _, outcomes = _solve_verdicts(record)
    assert set(outcomes.values()) == {"imported_open_half_shell"}


def test_one_source_owning_a_mirrored_pair_is_recovered_from_its_kept_driver(tmp_path: Path) -> None:
    """PartyMEH's MF: one identity, two drivers, the cut keeps one clear of the plane."""

    record = _ingest(
        _box_bundle(tmp_path, "pair-one-identity", sources=["mid", "pair"], discs=((0.0, 15.0, 10.0), (-30.0, -20.0, 6.0))),
        tmp_path / "data",
    )
    decision = _decision(record)
    assert decision["solver_domain"]["planes"] == ["x0"] and decision["refusal"] is None
    assert "x0" not in decision["sources"]["by_id"]["pair"]["meets_planes"]
    # The same pair named as one side's own driver is refused.
    sided = _ingest(
        _box_bundle(tmp_path, "pair-sided", sources=["mid", "woofer-left"], discs=((0.0, 15.0, 10.0), (-30.0, -20.0, 6.0))),
        tmp_path / "data-sided",
    )
    [cut] = _decision(sided)["cad_cuts"]
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.SIDE_IDENTIFIED_SOURCE]


def test_an_asymmetric_section_is_read_on_the_cad_curves() -> None:
    from server.mesh.imported import asymmetric_section

    def run(centre_x: float) -> list[dict[str, Any]]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        occ = gmsh.model.occ
        disc = occ.addDisk(centre_x, 0.0, 0.0, 10.0, 10.0)
        occ.intersect([(2, disc)], [(3, occ.addBox(-20.0, -20.0, -1.0, 20.0, 40.0, 2.0))])
        occ.synchronize()
        found = asymmetric_section(gmsh, ["x0"])
        gmsh.clear()
        return found

    assert _run_in_gmsh_session(run, 0.0) == []
    kinks = _run_in_gmsh_session(run, -5.0)
    assert len(kinks) == 2
    assert [item["angle_from_normal_deg"] for item in kinks] == pytest.approx([30.0, 30.0], abs=1e-6)


@pytest.mark.parametrize("name", ["MFLeft", "MFRight"])
def test_an_acronym_prefixed_side_name_refuses_the_reduction(tmp_path: Path, name: str) -> None:
    """Review S3-2b, finding 1: "MFLeft" is as sided as "woofer-left"."""

    record = _ingest(
        _box_bundle(tmp_path, name, sources=["mid", name], discs=((0.0, 15.0, 10.0), (-30.0, -20.0, 6.0))),
        tmp_path / "data",
    )
    decision = _decision(record)
    [cut] = decision["cad_cuts"]
    assert [item["code"] for item in cut["recovery"]["failed"]] == [cr.SIDE_IDENTIFIED_SOURCE]
    assert decision["solver_domain"]["planes"] == []
    _, outcomes = _solve_verdicts(record)
    assert set(outcomes.values()) == {"imported_open_half_shell"}


def test_a_declared_half_with_a_side_identified_source_is_refused(tmp_path: Path) -> None:
    """Review S3-2b, finding 2: a declaration never overrides the source-identity condition."""

    from server.cadlink.ingest import IngestRefusal

    def bundle(name: str, source: str) -> Path:
        return _bundle(
            tmp_path, name, lambda path: _cut_box(path, x_side=1),
            _faces_near(_half_disc_centre(0.0, 15.0, 10.0, 1, 0)), sources=[source], domain=("x0",),
        )

    with pytest.raises(IngestRefusal, match="declares it was already cut on x0, but source woofer-left is identified as the left one"):
        _ingest(bundle("declared-left", "woofer-left"), tmp_path / "data")
    # Control: the same declaration of an unsided source is mirrored.
    declared = _ingest(bundle("declared-woofer", "woofer"), tmp_path / "data-control")
    assert declared["symmetry"]["domain_planes"] == ["x0"]


def test_a_stored_record_mirroring_a_side_identified_source_is_refused_at_plan_and_submission(
    tmp_path: Path,
) -> None:
    """Re-review S3-2c, note 3: a record prepared before the check cannot bypass it."""

    import json

    from server.jobs.runtime import SIDE_IDENTIFIED_SOURCE_CODE, _imported_side_identity_refusal

    record = _ingest(_box_bundle(tmp_path, "stored", sources=["woofer"]), tmp_path / "data")
    assert record["symmetry"]["domain_planes"] == ["x0"]
    assert _imported_side_identity_refusal(record) is None
    older = json.loads(json.dumps(record))
    older["sources"][0]["selectors"] = {**older["sources"][0]["selectors"], "appearance_labels": ["Woofer_Rt"]}
    refusal = _imported_side_identity_refusal(older)
    assert refusal is not None and refusal[0] == SIDE_IDENTIFIED_SOURCE_CODE
    assert "identified as the right one" in refusal[1]
    plan, outcomes = _solve_verdicts(older)
    assert plan["code"] == SIDE_IDENTIFIED_SOURCE_CODE
    assert set(outcomes.values()) == {SIDE_IDENTIFIED_SOURCE_CODE}
    # A plane WG cut itself is not judged: its mirror test is per identity.
    whole = json.loads(json.dumps(older))
    whole["symmetry"]["cut_planes"] = list(whole["symmetry"]["domain_planes"])
    assert _imported_side_identity_refusal(whole) is None
