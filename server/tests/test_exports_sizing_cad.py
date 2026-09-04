"""The solid STEP's CAD sampling search, which has no segment counts to turn.

``write_step_from_config`` runs its own curvature-driven grid refinement and is
reachable only through the millimetre resolutions, so this planner searches
those. Before it existed those numbers came straight from the solver's mesh
settings, which is what made a STEP follow the solve.
"""

from __future__ import annotations

import pytest

from server.exports.sizing import (
    STEP_SURFACE_TOLERANCE_MM,
    _FALLBACK_CAD_RESOLUTION_MM,
    _MAX_CAD_RESOLUTION_MM,
    plan_cad_resolution,
)
from server.mesh.builder import _solver_mesher_config

from server.tests.test_exports_sizing import _seed


def _config(**mesh: float) -> dict:
    return _solver_mesher_config(_seed(**mesh), keep_placement=True)


def test_cad_sampling_is_the_same_whatever_the_solve_was_set_to() -> None:
    default = plan_cad_resolution(_config())
    refined = plan_cad_resolution(_config(mouth_resolution=3, throat_resolution=1.5))
    coarse = plan_cad_resolution(_config(mouth_resolution=40, throat_resolution=20))
    assert default.resolution_mm == refined.resolution_mm == coarse.resolution_mm


def test_the_chosen_sampling_meets_the_cad_tolerance() -> None:
    plan = plan_cad_resolution(_config())
    assert plan.deviation_mm is not None
    assert plan.deviation_mm <= STEP_SURFACE_TOLERANCE_MM


def test_a_tighter_tolerance_samples_more_finely() -> None:
    loose = plan_cad_resolution(_config(), tolerance_mm=STEP_SURFACE_TOLERANCE_MM)
    tight = plan_cad_resolution(_config(), tolerance_mm=1e-6)
    assert tight.resolution_mm <= loose.resolution_mm


def test_an_unmeasurable_design_falls_back_to_a_finer_grid_not_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back must never be coarser than the tolerance search would pick."""

    import server.exports.sizing as sizing

    monkeypatch.setattr(sizing, "measure_deviation", lambda *_a, **_k: None)
    plan = plan_cad_resolution(_config())
    assert plan.deviation_mm is None
    assert plan.resolution_mm <= _FALLBACK_CAD_RESOLUTION_MM
    assert plan.resolution_mm < _MAX_CAD_RESOLUTION_MM


def test_a_probe_that_raises_does_not_fail_the_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hornlab_mesher.config_builder as builder

    def boom(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(builder, "resolve_geometry", boom)
    plan = plan_cad_resolution(_config())
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
