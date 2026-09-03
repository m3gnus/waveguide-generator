"""Geometric self-intersection detection and its severity wiring.

A mesh can pass every topology check in ``mesh_integrity_report`` -- watertight,
manifold, consistently wound, correctly oriented -- and still be geometrically
impossible. These tests pin the detector's classification and the three-way
``warn`` / ``strict`` / ``off`` contract the builder wraps it in.
"""

from __future__ import annotations

import asyncio
import math
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig, Expr
from server.mesh.builder import (
    SELF_INTERSECTION_WARNING_PREFIX,
    _self_intersection_warnings,
    build_solver_mesh,
    clear_solver_mesh_cache,
)
from server.mesh.integrity import (
    mesh_integrity_report,
    mesh_self_intersection_report,
)


def _report(points, triangles):
    return mesh_self_intersection_report(
        np.asarray(points, dtype=float), np.asarray(triangles, dtype=np.int64)
    )


def _uv_sphere(rings: int) -> tuple[np.ndarray, np.ndarray]:
    """A sphere whose poles collapse a whole row onto one point.

    Every triangle in a pole fan therefore shares a full edge with its
    neighbour under a *different* vertex index -- the unwelded-seam shape that
    an index-only adjacency test reports as hundreds of crossings.
    """

    polar = np.linspace(0.0, math.pi, rings)
    azimuth = np.linspace(0.0, 2.0 * math.pi, 2 * rings)
    grid_polar, grid_azimuth = np.meshgrid(polar, azimuth, indexing="ij")
    points = np.stack(
        (
            np.sin(grid_polar) * np.cos(grid_azimuth),
            np.sin(grid_polar) * np.sin(grid_azimuth),
            np.cos(grid_polar),
        ),
        axis=-1,
    ).reshape(-1, 3)
    index = np.arange(rings * 2 * rings).reshape(rings, 2 * rings)
    a = index[:-1, :-1].ravel()
    b = index[1:, :-1].ravel()
    c = index[1:, 1:].ravel()
    d = index[:-1, 1:].ravel()
    triangles = np.concatenate(
        (np.stack((a, b, c), axis=1), np.stack((a, c, d), axis=1))
    )
    return points, triangles


def test_crossing_triangles_are_reported() -> None:
    report = _report(
        [[-1, 0, 0], [1, 0, 0], [0, 1, 0], [0, -0.5, -1], [0, -0.5, 1], [0, 0.5, 0]],
        [[0, 1, 2], [3, 4, 5]],
    )
    assert report["checked"] is True
    assert report["proper_crossing_count"] == 1
    assert report["intersecting_triangle_count"] == 2
    assert report["samples"][0]["extent_m"] > 0.0


def test_scale_invariance() -> None:
    """The same crossing must be found at any model scale.

    Every tolerance is relative to a length the mesh owns, so a millimetre
    figure that happened to work on a 0.6 m waveguide cannot silently stop
    working on a 600 m one.
    """

    points = np.asarray(
        [[-1, 0, 0], [1, 0, 0], [0, 1, 0], [0, -0.5, -1], [0, -0.5, 1], [0, 0.5, 0]],
        dtype=float,
    )
    for scale in (1.0e-3, 1.0, 1.0e3):
        assert _report(points * scale, [[0, 1, 2], [3, 4, 5]])[
            "proper_crossing_count"
        ] == 1


@pytest.mark.parametrize(
    "name, points, triangles",
    [
        (
            "shared edge, coplanar neighbours",
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]],
            [[0, 1, 2], [1, 3, 2]],
        ),
        (
            "shared edge, hinged out of plane",
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.3, 0.3, 1]],
            [[0, 1, 2], [0, 1, 3]],
        ),
        (
            "shared vertex, disjoint",
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]],
            [[0, 1, 2], [0, 3, 4]],
        ),
        (
            "coplanar but disjoint",
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 0], [6, 5, 0], [5, 6, 0]],
            [[0, 1, 2], [3, 4, 5]],
        ),
        (
            "closed tetrahedron",
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
        ),
    ],
)
def test_legitimate_adjacency_is_not_an_intersection(name, points, triangles) -> None:
    report = _report(points, triangles)
    assert report["checked"] is True
    assert report["proper_crossing_count"] == 0, name
    assert report["coplanar_overlap_count"] == 0, name


def test_adjacency_does_not_mask_a_foldover() -> None:
    """Sharing topology must not exempt a pair from the geometric test.

    A fold-over shares an edge or a vertex with the surface it folds onto, so
    skipping adjacent pairs -- the obvious way to suppress the shared edge --
    would blind the detector to exactly the defect it exists to find.
    """

    # Flat fold: the second triangle lies inside the first, hinged on an edge.
    folded = _report(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.2, 0.2, 0]], [[0, 1, 2], [1, 3, 2]]
    )
    assert folded["coplanar_overlap_count"] == 1

    # Out-of-plane stab through the interior, hinged on a shared vertex.
    stabbed = _report(
        [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0.5, 0.5, -1], [0.5, 0.5, 1]],
        [[0, 1, 2], [0, 3, 4]],
    )
    assert stabbed["proper_crossing_count"] == 1


def test_unwelded_coincident_seam_is_not_an_intersection() -> None:
    """Adjacency is decided by position, not by vertex index."""

    points, triangles = _uv_sphere(40)
    report = _report(points, triangles)
    assert report["checked"] is True
    assert report["proper_crossing_count"] == 0
    assert report["coplanar_overlap_count"] == 0
    # The same mesh is clean by every topology measure, which is the point:
    # a passing integrity report proves nothing about geometric validity.
    assert mesh_integrity_report(points, triangles, expected_volume_sign=None)[
        "nonmanifold_edge_count"
    ] == 0


def test_inverted_spike_in_a_sphere_is_found() -> None:
    points, triangles = _uv_sphere(40)
    points[len(points) // 2] *= -3.0
    assert _report(points, triangles)["proper_crossing_count"] > 0


@pytest.mark.parametrize(
    "points, triangles",
    [
        (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)),
        (np.zeros((3, 3)), np.asarray([[0, 1, 9]])),
        (np.asarray([[0, 0, 0], [1, 0, 0], [np.nan, 1, 0]]), np.asarray([[0, 1, 2]])),
        (np.zeros((3, 2)), np.asarray([[0, 1, 2]])),
    ],
)
def test_malformed_input_reports_unchecked_rather_than_clean(points, triangles) -> None:
    """"Not checked" and "no intersections" must never be confused."""

    report = mesh_self_intersection_report(points, triangles)
    assert report["checked"] is False
    assert report["proper_crossing_count"] == 0


def test_unchecked_is_reported_not_silently_passed() -> None:
    """"Could not check" must never read as "nothing wrong".

    The budget exists so a pathological mesh degrades instead of exhausting
    memory, and the mesh that trips it is the one most likely to be broken.
    Saying nothing would let it through strict validation in silence.
    """

    (warning,) = _self_intersection_warnings({"checked": False})
    assert warning.startswith(SELF_INTERSECTION_WARNING_PREFIX)
    assert "not checked" in warning


def test_warning_names_the_count_size_and_place() -> None:
    assert (
        _self_intersection_warnings(
            {
                "checked": True,
                "proper_crossing_count": 0,
                "coplanar_overlap_count": 0,
                "samples": [],
            }
        )
        == []
    )
    (warning,) = _self_intersection_warnings(
        {
            "checked": True,
            "proper_crossing_count": 77,
            "coplanar_overlap_count": 0,
            "samples": [
                {
                    "triangles": [1, 2],
                    "location_m": [-0.0203, 0.0141, 0.0266],
                    "extent_m": 0.01009,
                }
            ],
        }
    )
    assert warning.startswith(SELF_INTERSECTION_WARNING_PREFIX)
    assert "77" in warning
    assert "10.1 mm" in warning
    assert "Wall Thickness" in warning
    # "reduce Rear Resolution" was the remedy here and it is wrong: rear
    # resolution governs the outer shell, and the crossing this warning most
    # often reports is the acoustic surface chording across a mouth rollback.
    # Measured on a stock R-OSSE with a 3 mm wall, taking rear resolution from
    # 15 mm to 7 mm took the crossing count from 0 to 341.
    assert "reduce Rear Resolution" not in warning
    assert "Mouth Resolution" in warning


_SAMPLE_CROSSING = {
    "checked": True,
    "proper_crossing_count": 12,
    "coplanar_overlap_count": 0,
    "samples": [
        {
            "triangles": [1, 2],
            "location_m": [0.14, 0.0, 0.036],
            "extent_m": 0.004,
        }
    ],
}


def test_a_fold_replaces_the_remedy_instead_of_contradicting_it() -> None:
    """One mesh must not carry both "increase" and "reduce Wall Thickness".

    The fold warning used to be emitted beside the crossing warning, and the
    two remedies are opposites. A folded offset cannot be meshed apart at any
    density, so when one is present it is the one that answers.
    """

    (plain,) = _self_intersection_warnings(_SAMPLE_CROSSING)
    assert "Increase Wall Thickness" in plain
    assert "Reduce Wall Thickness" not in plain

    (folded,) = _self_intersection_warnings(
        _SAMPLE_CROSSING, fold="R-OSSE ... normal flip near azimuth row 0, interval 46"
    )
    assert "Reduce Wall Thickness" in folded
    assert "Increase Wall Thickness" not in folded
    assert "interval 46" in folded


def test_a_fold_alone_is_not_a_warning() -> None:
    """It fired on ATH's own 5 mm default while the mesh had zero crossings.

    On a stock R-OSSE the flip is at axial interval 46 of 50 -- the mouth
    rollback, whose curvature radius is 3.6 mm -- not at the throat the old
    text named, and it only moves to the throat past a 15 mm wall. The mesher
    records that 11 of 16 reference designs fold. What reaches the solver is
    the built mesh, so the fold is reported as a cause when the mesh actually
    crosses, and not as an alarm of its own.
    """

    assert (
        _self_intersection_warnings(
            {
                "checked": True,
                "proper_crossing_count": 0,
                "coplanar_overlap_count": 0,
                "samples": [],
            },
            fold="R-OSSE ... normal flip near azimuth row 0, interval 46",
        )
        == []
    )


def test_the_strict_refusal_and_the_warning_give_the_same_remedy() -> None:
    """They drifted apart once; sharing the text is what stops it recurring."""

    from server.mesh.builder import SELF_INTERSECTION_REMEDY

    (warning,) = _self_intersection_warnings(_SAMPLE_CROSSING)
    assert SELF_INTERSECTION_REMEDY in warning


def _crossing_mesh_design(monkeypatch, tmp_path) -> DesignConfig:
    """Stub the mesher so build_solver_mesh receives a self-intersecting mesh."""

    points = np.asarray(
        [
            [-0.01, 0, 0],
            [0.01, 0, 0],
            [0, 0.01, 0],
            [0, -0.005, -0.01],
            [0, -0.005, 0.01],
            [0, 0.005, 0],
        ],
        dtype=float,
    )
    triangles = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    parsed = SimpleNamespace(
        points=points,
        cells=[SimpleNamespace(type="triangle", data=triangles)],
        cell_data={"gmsh:physical": [np.asarray([2, 2], dtype=np.int32)]},
    )

    def fake_build_from_config(config, mesh_path, *, allow_large_mesh):
        mesh_path.write_text("$MeshFormat\n", encoding="utf-8")
        return SimpleNamespace(metadata={}, units="m")

    package = ModuleType("hornlab_mesher")
    package.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]
    config_builder = ModuleType("hornlab_mesher.config_builder")
    config_builder.build_from_config = fake_build_from_config  # type: ignore[attr-defined]
    meshio = ModuleType("meshio")
    meshio.read = lambda _path: parsed  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hornlab_mesher", package)
    monkeypatch.setitem(sys.modules, "hornlab_mesher.config_builder", config_builder)
    monkeypatch.setitem(sys.modules, "meshio", meshio)

    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 30,
            "a0": 10,
            "r0": 10,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {
                "angular_segments": 12,
                "length_segments": 4,
                "throat_resolution": 8,
                "mouth_resolution": 15,
                "quadrants": 1,
                "wall_thickness": 2,
                "max_triangles": 50000,
            },
            "source": {"shape": 2, "radius": -1, "curvature": 0},
        }
    )
    # Bare mode: the stub mesh is an open X, and a closed mode would be
    # rejected for its free edges before the crossing is ever reported.
    design.root.mesh.wall_thickness = Expr(value=0.0)
    design.root.mesh.quadrants = Expr(value=1234.0)
    return design


def _build(design, mode):
    clear_solver_mesh_cache()
    try:
        return asyncio.run(
            build_solver_mesh(
                design, {"mesh_validation_mode": mode}, force_rebuild=True
            )
        )
    finally:
        clear_solver_mesh_cache()


def test_warn_mode_reports_without_failing(monkeypatch, tmp_path) -> None:
    design = _crossing_mesh_design(monkeypatch, tmp_path)
    result = _build(design, "warn")
    report = result["integrity"]["self_intersection"]
    assert report["proper_crossing_count"] == 1
    assert any(
        warning.startswith(SELF_INTERSECTION_WARNING_PREFIX)
        for warning in result["stats"]["warnings"]
    )


def test_strict_mode_fails(monkeypatch, tmp_path) -> None:
    design = _crossing_mesh_design(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="self-intersecting"):
        _build(design, "strict")


def test_off_mode_suppresses_the_warning(monkeypatch, tmp_path) -> None:
    design = _crossing_mesh_design(monkeypatch, tmp_path)
    result = _build(design, "off")
    assert not any(
        warning.startswith(SELF_INTERSECTION_WARNING_PREFIX)
        for warning in result["stats"]["warnings"]
    )
    # Suppressed in the warning channel, still recorded in the report.
    assert result["integrity"]["self_intersection"]["proper_crossing_count"] == 1


def _interpenetrating_tetrahedra() -> tuple[np.ndarray, np.ndarray]:
    """Two closed tetrahedra that pass through each other.

    Each component is watertight, manifold, consistently wound and positively
    oriented, so every topology measure reports a healthy mesh. Only the
    geometry is impossible.
    """

    base = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float
    )
    faces = np.asarray([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64)
    shifted = base + np.asarray([0.3, 0.3, 0.3])
    points = np.vstack((base, shifted))
    triangles = np.vstack((faces, faces + len(base)))
    return points, triangles


def test_a_crossing_does_not_change_topology_validity() -> None:
    """The geometric verdict must stay out of integrity["valid"].

    ``_require_closed_acoustic_topology`` raises on that flag before
    ``mesh_validation_mode`` is read, and the results UI and render CLI both
    render it as "invalid topology". A crossing is neither.
    """

    points, triangles = _interpenetrating_tetrahedra()
    integrity = mesh_integrity_report(points, triangles)
    assert integrity["valid"] is True
    assert integrity["is_watertight"] is True
    assert integrity["degenerate_triangle_count"] == 0
    assert integrity["nonmanifold_edge_count"] == 0
    assert _report(points, triangles)["proper_crossing_count"] > 0


# --- regressions from the pre-push diff review -------------------------------


def test_a_distant_point_cannot_hide_a_crossing() -> None:
    """Tolerances must scale with the pair, not with the whole model.

    Scaling by the bounding diagonal let one far-away vertex -- here not even
    referenced by a triangle -- inflate the weld tolerance until both corners of
    a genuinely crossing pair counted as shared, and the pair was discarded as
    adjacency. A false negative is the worst failure this check can have.
    """

    corners = [[-1, 0, 0], [1, 0, 0], [0, 1, 0], [0, -0.5, -1], [0, -0.5, 1], [0, 0.5, 0]]
    faces = [[0, 1, 2], [3, 4, 5]]
    assert _report(corners, faces)["proper_crossing_count"] == 1
    assert (
        _report(corners + [[1.0e9, 1.0e9, 1.0e9]], faces)["proper_crossing_count"] == 1
    )


def test_a_touching_pair_is_not_a_metre_long_crossing() -> None:
    """On-plane vertices must be on-plane for the interpolation too.

    Zeroing a vertex's sign inside the tolerance while still interpolating with
    its true offset put the crossing parameter outside [0, 1], inventing an
    intersection far outside the triangles: two facets sharing a plane to within
    a nanometre were reported as crossing over 1.5 m.
    """

    report = _report(
        [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0.5, 0.5, 0], [1.5, 0.5, 2e-9], [0.5, 1.5, 6e-9]],
        [[0, 1, 2], [3, 4, 5]],
    )
    assert report["proper_crossing_count"] == 0
    # It is a real coplanar overlap, and reported as one.
    assert report["coplanar_overlap_count"] == 1
    assert report["samples"][0]["extent_m"] < 2.0


def test_a_t_junction_is_contact_not_a_crossing() -> None:
    """One triangle's edge lying along a longer edge of another.

    Imported CAD is full of these. Separating a pair only when every vertex is
    strictly off the plane missed them, because two of the vertices sit exactly
    on it -- so an ordinary T-junction was reported as a crossing.
    """

    assert (
        _report(
            [[0, 0, 0], [2, 0, 0], [0, 1, 0], [0.5, 0, 0], [1.5, 0, 0], [1, 0, 1]],
            [[0, 1, 2], [3, 4, 5]],
        )["proper_crossing_count"]
        == 0
    )


def test_a_coplanar_foldover_survives_the_shared_edge_shortcut() -> None:
    """Sharing an edge does not prove two coplanar triangles are disjoint.

    Skipping every edge-sharing coplanar pair is tempting -- a flat cap is
    thousands of them -- but a fold-over hinges on a shared edge and lands on
    top of its neighbour. They tile only when their free corners fall on
    opposite sides of the shared edge.
    """

    tiled = _report([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], [[0, 1, 2], [1, 3, 2]])
    assert tiled["coplanar_overlap_count"] == 0
    folded = _report(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.2, 0.2, 0]], [[0, 1, 2], [1, 3, 2]]
    )
    assert folded["coplanar_overlap_count"] == 1


def test_duplicate_faces_do_not_exhaust_the_candidate_budget() -> None:
    """Repeated faces pair combinatorially and would spend the whole budget.

    They are already counted by mesh_integrity_report, and dropping them keeps
    a mesh with a few thousand duplicates checkable instead of degrading it to
    "not checked".
    """

    corners = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.tile(np.asarray([[0, 1, 2]], dtype=np.int64), (5_000, 1))
    report = mesh_self_intersection_report(corners, faces)
    assert report["checked"] is True
    assert report["proper_crossing_count"] == 0


def test_coplanar_severity_is_reported_as_a_length() -> None:
    """Samples are ranked together and printed in millimetres.

    A coplanar overlap is an area; storing it raw next to a crossing's segment
    length made a 0.5 m^2 overlap print as "spanning 499.0 mm".
    """

    report = _report(
        [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0.5, 0.5, 0], [2.5, 0.5, 0], [0.5, 2.5, 0]],
        [[0, 1, 2], [3, 4, 5]],
    )
    assert report["coplanar_overlap_count"] == 1
    # The overlap is 0.5 m^2, so the equivalent side is sqrt(0.5).
    assert report["samples"][0]["extent_m"] == pytest.approx(math.sqrt(0.5))


def test_a_crossing_is_located_from_the_low_end_whatever_the_winding() -> None:
    """The reported place must not move when a triangle is wound the other way.

    ``_edge_plane_crossings`` returns a chord in edge order, which follows the
    winding rather than the dominant axis, so half of all chords run
    high-to-low. Both overlap ratios are measured from the chord's low end, so
    interpolating those from its first point mirrored the sampled segment about
    the chord midpoint and sent the warning to the opposite end of the triangle.
    """

    # The first triangle lies in z=0 and straddles y=0; the second lies in y=0
    # and straddles z=0, so their planes meet along x. One chord spans
    # x=1.25..8.75 and the other x=0.5..1.5, making the true crossing
    # x=1.25..1.5 -- a short segment at one end of a long chord, which is where
    # a mirrored answer is unmistakable rather than merely slightly off.
    points = [
        [0.0, -1.0, 0.0],
        [10.0, -1.0, 0.0],
        [5.0, 3.0, 0.0],
        [0.0, 0.0, -1.0],
        [2.0, 0.0, -1.0],
        [1.0, 0.0, 1.0],
    ]
    for first in ([0, 1, 2], [0, 2, 1]):
        for second in ([3, 4, 5], [3, 5, 4]):
            report = _report(points, [first, second])
            assert report["proper_crossing_count"] == 1, (first, second)
            sample = report["samples"][0]
            assert sample["location_m"][0] == pytest.approx(1.375), (first, second)
            assert sample["extent_m"] == pytest.approx(0.25), (first, second)
