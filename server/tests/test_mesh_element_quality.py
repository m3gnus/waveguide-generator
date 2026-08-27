"""Triangle shape was never measured anywhere in the pipeline.

There is no aspect-ratio, minimum-angle, skewness or SICN check in ``server``,
``hornlab_mesher`` or ``hornlab_metal_bem``, and gmsh is never asked to optimise
or report element quality. Only outright degeneracy was caught, at
``areas <= 1e-15``. A sliver with finite area therefore passed every check in the
pipeline, and BEM conditioning is sensitive to exactly that.
"""

from __future__ import annotations

import numpy as np
import pytest

from server.mesh.integrity import (
    POOR_TRIANGLE_RADIUS_RATIO,
    mesh_element_quality_report,
    mesh_integrity_report,
)


EQUILATERAL = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 3.0**0.5 / 2, 0.0]])
#: Finite area, so every pre-existing check passes it.
SLIVER = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 1.0e-4, 0.0]])


def test_a_well_shaped_triangle_scores_one() -> None:
    report = mesh_element_quality_report(EQUILATERAL, [[0, 1, 2]])

    assert report["valid"]
    assert report["measured_triangle_count"] == 1
    assert report["min_radius_ratio"] == pytest.approx(1.0)
    assert report["min_angle_deg"] == pytest.approx(60.0)
    assert report["poor_radius_ratio_count"] == 0


def test_a_sliver_with_finite_area_is_caught_here_and_nowhere_else() -> None:
    report = mesh_element_quality_report(SLIVER, [[0, 1, 2]])

    assert report["min_radius_ratio"] < 1.0e-6
    assert report["min_angle_deg"] < 0.02
    assert report["poor_radius_ratio_count"] == 1
    # The point of the whole check: it is not degenerate by area.
    assert report["worst_triangles"][0]["area"] > 1.0e-15

    topology = mesh_integrity_report(SLIVER, [[0, 1, 2]], expected_volume_sign=None)
    assert topology["degenerate_triangle_count"] == 0


def test_radius_ratio_falls_for_a_cap_as_well_as_a_needle() -> None:
    """Why the radius ratio rather than an aspect ratio.

    An aspect ratio cannot tell a thin needle from a flat cap, and both hurt a
    boundary-element operator. A cap is wide and short: its longest side is
    nearly the sum of the other two.
    """

    cap = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 0.001, 0.0]])
    needle = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.999, 0.001, 0.0]])

    for shape in (cap, needle):
        report = mesh_element_quality_report(shape, [[0, 1, 2]])
        assert report["min_radius_ratio"] < POOR_TRIANGLE_RADIUS_RATIO


def test_faces_with_no_shape_are_excluded_rather_than_scored_zero() -> None:
    """A zero-area face would otherwise drag the reported minimum to zero.

    The caller already knows about degeneracy from the topology report; folding
    it in here would hide the sliver this check exists to surface.
    """

    points = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 3.0**0.5 / 2, 0.0], [2.0, 0.0, 0.0]]
    )
    # One good triangle, one collapsed onto a line, one addressing a missing vertex.
    report = mesh_element_quality_report(points, [[0, 1, 2], [0, 1, 3], [0, 1, 99]])

    assert report["measured_triangle_count"] == 1
    assert report["excluded_triangle_count"] == 2
    assert report["min_radius_ratio"] == pytest.approx(1.0)


def test_an_empty_or_malformed_mesh_reports_rather_than_raises() -> None:
    for vertices, triangles in (
        (np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)),
        (np.array([1.0, 2.0]), np.empty((0, 3), dtype=np.int64)),
    ):
        report = mesh_element_quality_report(vertices, triangles)
        assert report["measured_triangle_count"] == 0
        assert report["min_radius_ratio"] is None
