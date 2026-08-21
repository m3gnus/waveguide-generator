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


def test_warning_names_the_count_size_and_place() -> None:
    assert _self_intersection_warnings({"checked": False}) == []
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
