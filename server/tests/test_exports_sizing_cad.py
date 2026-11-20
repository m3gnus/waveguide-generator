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


def _reading(full: float, nested: float | None, chord: float = 0.1):
    """A cubic reading whose full value (nested + detuned) and nested part differ."""

    from server.exports.sizing import SurfaceDeviation

    return SurfaceDeviation(chord, full, chord, full, nested_cubic=nested)


def _measure_with(monkeypatch: pytest.MonkeyPatch, *readings) -> list[float]:
    """Serve ``readings`` in order (the last repeats); ``None`` is unmeasurable."""

    import server.exports.sizing as sizing

    calls: list[int] = []

    def measure(_params, angular: int, length: int):
        reading = readings[min(len(calls), len(readings) - 1)]
        calls.append(angular)
        return None if reading is None else (reading, angular, length)

    monkeypatch.setattr(sizing, "measure_deviation", measure)
    return calls


def test_a_detuned_term_that_never_passes_does_not_hand_over_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graded R-OSSE stations: the nested reading passes, the detuned gap never does.

    The search used to walk down to the 1 mm floor and hand it to the mesher,
    which then took minutes. The coarsest nested pass is the plan instead,
    capped at the fixed fallback because an aliasing expression surface reads
    the same way, and it says it was not fully verified.
    """

    from server.exports.sizing import _MIN_CAD_RESOLUTION_MM

    calls = _measure_with(monkeypatch, _reading(0.2, 0.005))
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert len(calls) > 1  # the search really did keep refining
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.resolution_mm > _MIN_CAD_RESOLUTION_MM
    # The nested pass was at the 24 mm probe; 12 mm itself was never measured.
    assert plan.deviation_mm is None
    assert plan.warning is not None
    assert "12 mm" in plan.warning and "Check fine surface detail" in plan.warning


def test_a_nested_pass_finer_than_the_fallback_keeps_its_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A large chord drives the 24 mm probe down by the 0.4 step limit to 9.6 mm,
    # where the nested reading passes and the detuned one still does not.
    _measure_with(monkeypatch, _reading(0.2, 0.2, chord=0.9), _reading(0.2, 0.005))
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == pytest.approx(9.6)
    assert plan.deviation_mm == pytest.approx(0.005)
    assert plan.warning is not None and "9.6 mm" in plan.warning


def test_no_passing_reading_at_all_takes_the_fixed_fallback_not_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _measure_with(monkeypatch, _reading(0.2, 0.2))
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.deviation_mm is None
    assert plan.warning is not None and "unverified" in plan.warning


def test_a_nested_pass_survives_a_later_unmeasurable_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early-exit path and the budget-exhausted path share one rule."""

    _measure_with(monkeypatch, _reading(0.2, 0.2, chord=0.9), _reading(0.2, 0.005), None)
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == pytest.approx(9.6)
    assert plan.deviation_mm == pytest.approx(0.005)
    assert plan.warning is not None and "fully verified" in plan.warning


def test_an_accepted_probe_wins_over_an_earlier_nested_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Designs that pass the full test plan as before: no warning, no nested plan."""

    from server.exports.sizing import _PROBE_CAD_RESOLUTION_MM

    calls = _measure_with(monkeypatch, _reading(0.2, 0.005), _reading(0.01, 0.005))
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert len(calls) == 2
    assert plan.resolution_mm < _PROBE_CAD_RESOLUTION_MM
    assert plan.deviation_mm == pytest.approx(0.01)
    assert plan.warning is None


def test_an_unmeasurable_probe_after_an_accepted_one_keeps_the_fixed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Accepted well inside the target, so the search tries coarser and then
    # loses its reading: unchanged behaviour, the fixed fallback.
    _measure_with(monkeypatch, _reading(0.001, 0.001), None)
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.deviation_mm is None


def test_the_unverified_sampling_warning_reaches_the_export_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user sees the note on the STEP result, not just in the log."""

    from server.exports import core

    _measure_with(monkeypatch, _reading(0.2, 0.005))
    written: list[float] = []

    def write(config, path):
        written.append(config["mesh"]["mouthResolution"])
        path.write_text("STEP transport checked separately by the STEP contract tests")
        return path, None

    monkeypatch.setattr("hornlab_mesher.cad.write_step_from_config", write)
    monkeypatch.setattr(core, "_assert_step", lambda *_a, **_k: None)
    result = core._build_step_solid_sync(_seed().model_dump(mode="json"))
    assert written == [_FALLBACK_CAD_RESOLUTION_MM]
    assert result.warning is not None
    assert "Check fine surface detail" in result.warning
