"""Directivity index is one solid-angle-weighted power ratio, not H/V curves."""

from __future__ import annotations

import math

import numpy as np
import pytest

from server.solver.directivity_index import calculate_di_from_spherical_grid


def test_uniform_full_sphere_has_zero_di() -> None:
    theta = [0.0, 30.0, 90.0, 180.0]
    phi = [-180.0, -140.0, -10.0, 100.0]
    levels = np.full((2, len(theta), len(phi)), 37.5)

    assert calculate_di_from_spherical_grid(theta, phi, levels) == pytest.approx([0.0, 0.0])


def test_uniform_infinite_baffle_hemisphere_has_three_db_di() -> None:
    theta = [0.0, 30.0, 90.0]
    phi = [0.0, 90.0, 180.0, 270.0]
    levels = np.zeros((1, len(theta), len(phi)))

    assert calculate_di_from_spherical_grid(theta, phi, levels, hemisphere=True) == pytest.approx(
        [10.0 * math.log10(2.0)]
    )


def test_spherical_grid_uses_periodic_phi_cell_widths() -> None:
    theta = [0.0, 90.0, 180.0]
    phi = [0.0, 30.0, 180.0, 300.0]
    grid = np.zeros((1, len(theta), len(phi)))
    grid[0, 1, 1] = -20.0

    # The 30-degree cell spans from 15 to 105 degrees, one quarter of azimuth;
    # an unweighted sample average would incorrectly give it the same share as
    # the much narrower cell centered at zero.
    di = calculate_di_from_spherical_grid(theta, phi, grid)[0]
    assert di is not None
    assert di > 0.3


def test_spherical_di_does_not_depend_on_display_plane_selection() -> None:
    theta = [0.0, 45.0, 90.0, 135.0, 180.0]
    phi = [0.0, 90.0, 180.0, 270.0]
    grid = np.zeros((1, len(theta), len(phi)))
    grid[0, 1:, :] = np.asarray([[-2.0], [-8.0], [-14.0], [-20.0]])

    baseline = calculate_di_from_spherical_grid(theta, phi, grid)

    # Plane selection is intentionally absent from the integrator's contract:
    # the same complete field always produces the same single DI curve.
    assert baseline[0] is not None
    assert calculate_di_from_spherical_grid(theta, phi, grid) == baseline
