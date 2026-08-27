"""Curvature dial, honest cost reporting, and orientation warnings.

From the 260825 CAD Link meshing review. The curvature segment count is the
imported path's real cost dial and was hard-coded; the pre-mesh cost estimate
was curvature-blind and read ~3.6x optimistic on triangles and ~19x on RAM; and
a component the source-cap anchor cannot judge had no user-visible trace.
"""

from __future__ import annotations

import pytest

from server.mesh.imported import (
    IMPORTED_SURFACE_DEVIATION_MM,
    IMPORTED_SURFACE_DEVIATION_MAX_MM,
    IMPORTED_SURFACE_DEVIATION_MIN_MM,
    ImportedMeshError,
    _orientation_warnings,
    _validated_surface_deviation,
    imported_tessellation_settings,
)

SIZES = {
    "rigid_size_mm": 30.0,
    "transition_mm": 30.0,
    "source_size_mm": {"hf": 4.0, "mf": 15.0, "lf": 30.0},
}


def test_tessellation_defaults_to_the_server_surface_deviation():
    settings = imported_tessellation_settings(SIZES)
    assert settings["surface_deviation_mm"] == IMPORTED_SURFACE_DEVIATION_MM
    # the floor still tracks the finest explicit target, not the deviation dial
    assert settings["mesh_size_min_mm"] == pytest.approx(2.0)
    assert settings["mesh_size_max_mm"] == pytest.approx(30.0)


def test_surface_deviation_override_is_honoured_without_touching_the_size_bounds():
    settings = imported_tessellation_settings(SIZES, deviation_mm=0.2)
    assert settings["surface_deviation_mm"] == pytest.approx(0.2)
    assert settings["mesh_size_min_mm"] == pytest.approx(2.0)
    assert settings["mesh_size_max_mm"] == pytest.approx(30.0)


def test_absent_override_reads_as_the_default():
    assert _validated_surface_deviation(None) is None


@pytest.mark.parametrize(
    "value",
    [IMPORTED_SURFACE_DEVIATION_MIN_MM, 0.3, IMPORTED_SURFACE_DEVIATION_MAX_MM],
)
def test_in_band_surface_deviation_is_accepted(value):
    assert _validated_surface_deviation(value) == pytest.approx(value)


@pytest.mark.parametrize(
    "value",
    [
        True,                                      # bool is not a deviation
        IMPORTED_SURFACE_DEVIATION_MIN_MM / 2,     # runs the triangle count away
        IMPORTED_SURFACE_DEVIATION_MAX_MM * 2,     # visibly faceted past here
        0.0,
        -0.3,
        "0.3",
        float("inf"),
    ],
)
def test_out_of_band_surface_deviation_is_refused(value):
    with pytest.raises(ImportedMeshError, match="surfaceDeviationMm"):
        _validated_surface_deviation(value)


def test_the_default_sits_inside_its_own_band():
    assert (
        IMPORTED_SURFACE_DEVIATION_MIN_MM
        <= IMPORTED_SURFACE_DEVIATION_MM
        <= IMPORTED_SURFACE_DEVIATION_MAX_MM
    )


def test_orientation_warnings_are_silent_on_a_clean_repair():
    assert _orientation_warnings({}) == []
    assert _orientation_warnings({"unresolved_symmetry_components": 0}) == []


def test_an_unresolvable_component_is_reported_rather_than_shipped_quietly():
    warnings = _orientation_warnings({"unresolved_symmetry_components": 2})
    assert len(warnings) == 1
    assert "may be inverted" in warnings[0]


def test_a_volume_anchored_flip_is_reported():
    warnings = _orientation_warnings({"symmetry_volume_fallback_flipped": 1})
    assert len(warnings) == 1
    assert "signed volume" in warnings[0]


def test_orientation_warnings_tolerate_an_older_pinned_mesher():
    """The keys did not exist before the mesher fix; absence is not a crash."""
    assert _orientation_warnings({"flipped_global": 3, "flipped_consistency": 1}) == []
