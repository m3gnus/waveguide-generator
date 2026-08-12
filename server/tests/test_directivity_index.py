"""Directivity index is one solid-angle-weighted power ratio, not H/V curves."""

from __future__ import annotations

import math

import numpy as np
import pytest

from server.solver.directivity_index import (
    calculate_di_from_polar_patterns,
    calculate_di_from_spherical_grid,
)


def _pattern(levels: list[float], angles: list[float] | None = None) -> list[list[float]]:
    resolved_angles = angles or [0.0, 90.0, 180.0]
    return [[angle, level] for angle, level in zip(resolved_angles, levels, strict=True)]


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


def test_h_and_v_orbits_produce_one_combined_full_sphere_di() -> None:
    patterns = {
        "horizontal": [_pattern([0.0, -3.0, -20.0])],
        "vertical": [_pattern([0.0, -12.0, -20.0])],
        # A diagonal display cut is not another equal-area share of the sphere.
        "diagonal": [_pattern([0.0, 80.0, 80.0])],
    }

    result = calculate_di_from_polar_patterns(patterns)

    front_cap = (1.0 - math.cos(math.radians(45.0))) / 2.0
    middle_strip = math.cos(math.radians(45.0))
    rear_cap = front_cap
    middle_energy = (10.0 ** (-3.0 / 10.0) + 10.0 ** (-12.0 / 10.0)) / 2.0
    mean_energy = front_cap + middle_strip * middle_energy + rear_cap * 0.01
    expected = -10.0 * math.log10(mean_energy)
    assert result == pytest.approx([expected])


def test_signed_orbits_average_both_sides_in_linear_power() -> None:
    angles = [-180.0, -90.0, 0.0, 90.0, 180.0]
    patterns = {
        "horizontal": [_pattern([-20.0, -3.0, 0.0, -9.0, -20.0], angles)],
        "vertical": [_pattern([-20.0, -6.0, 0.0, -12.0, -20.0], angles)],
    }

    combined = calculate_di_from_polar_patterns(patterns)
    mirrored = {
        "horizontal": [_pattern([0.0, -6.0, -20.0])],
        "vertical": [_pattern([0.0, -9.0, -20.0])],
    }
    # Averaging -3/-9 dB in linear power is not -6 dB, so the asymmetric
    # signed result must differ from this arithmetic-dB mirror surrogate.
    assert combined[0] is not None
    assert combined[0] != pytest.approx(calculate_di_from_polar_patterns(mirrored)[0])


def test_plane_only_data_is_not_mislabeled_as_standard_di() -> None:
    result = calculate_di_from_polar_patterns({"horizontal": [_pattern([0.0, -6.0, -20.0])]})
    assert result == [None]


def test_incomplete_angular_domain_is_unavailable() -> None:
    angles = [0.0, 45.0, 90.0]
    patterns = {
        "horizontal": [_pattern([0.0, -3.0, -12.0], angles)],
        "vertical": [_pattern([0.0, -6.0, -18.0], angles)],
    }
    assert calculate_di_from_polar_patterns(patterns) == [None]
    assert calculate_di_from_polar_patterns(patterns, hemisphere=True)[0] is not None
