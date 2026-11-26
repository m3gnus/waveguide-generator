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
    return _solver_mesher_config(_seed(**mesh))


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
    assert plan.fallback_reason == "unmeasurable"
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
    assert plan.fallback_reason == "probe_exception"


def _reading(full: float, nested: float | None, chord: float = 0.1):
    """A cubic reading whose full value (nested + detuned) and nested part differ."""

    from server.exports.sizing import SurfaceDeviation

    return SurfaceDeviation(chord, full, chord, full, nested_cubic=nested)


def _measure_with(monkeypatch: pytest.MonkeyPatch, *readings) -> list[float]:
    """Serve ``readings`` in order (the last repeats); ``None`` is unmeasurable."""

    import server.exports.sizing as sizing

    calls: list[int] = []

    def measure(_params, angular: int, length: int, _why=None):
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
    assert plan.fallback_reason == "detuned_mismatch"


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
    assert plan.fallback_reason == "budget_exhausted"


def test_a_nested_pass_survives_a_later_unmeasurable_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early-exit path and the budget-exhausted path share one rule."""

    _measure_with(monkeypatch, _reading(0.2, 0.2, chord=0.9), _reading(0.2, 0.005), None)
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == pytest.approx(9.6)
    assert plan.deviation_mm == pytest.approx(0.005)
    assert plan.warning is not None and "fully verified" in plan.warning
    assert plan.fallback_reason == "unmeasurable"


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
    assert plan.fallback_reason is None


def test_an_unmeasurable_probe_after_an_accepted_one_keeps_the_fixed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Accepted well inside the target, so the search tries coarser and then
    # loses its reading: unchanged behaviour, the fixed fallback.
    _measure_with(monkeypatch, _reading(0.001, 0.001), None)
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.deviation_mm is None
    assert plan.fallback_reason == "unmeasurable"


def test_the_unverified_sampling_warning_reaches_the_export_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user sees the note on the STEP result, not just in the log."""

    from server.exports import core

    import hornlab_mesher.config_builder as config_builder

    _measure_with(monkeypatch, _reading(0.2, 0.005))
    resolved_at: list[float] = []
    real_resolve = config_builder.resolve_geometry

    def resolve(config, *args, **kwargs):
        resolved_at.append(config["mesh"]["mouthResolution"])
        return real_resolve(config, *args, **kwargs)

    written: list[object] = []

    def write(geometry, path, **_kwargs):
        written.append(geometry)
        path.write_text("STEP transport checked separately by the STEP contract tests")
        return path, None

    monkeypatch.setattr(config_builder, "resolve_geometry", resolve)
    monkeypatch.setattr("hornlab_mesher.cad.write_step", write)
    monkeypatch.setattr(core, "_assert_step", lambda *_a, **_k: None)
    result = core._build_step_solid_sync(_seed().model_dump(mode="json"))
    # The capped plan was never probed, so the written geometry is resolved
    # once more, at the fallback sampling.
    assert len(written) == 1
    assert resolved_at[-1] == _FALLBACK_CAD_RESOLUTION_MM
    assert result.warning is not None
    assert "Check fine surface detail" in result.warning


#: A generic rounded-rectangle OSSE (corner arcs: the angular lattice never nests).
_ROUNDED_OSSE = {
    "formula": "OSSE", "L": 120, "a": 45, "a0": 10, "r0": 17, "n": 3.7, "q": 0.99, "s": 0.7,
    "morph": {"corner_radius": 18, "rate": 3, "target_shape": 1, "fixed_part": 0},
}


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        # An ATH-imported design keeps ATH's axial sampling, so refining the
        # length alone moves its coarse stations too: no reading at all. This is
        # the 23 Nov live fallback (an imported OSSE with a rounded morph).
        ({**_ROUNDED_OSSE, "text_import_version": "ath-2026-09a-v3"},
         "unmeasurable_axial_not_nested"),
        # The same horn authored in WG: a chord reading with no cubic model.
        (_ROUNDED_OSSE, "non_nested_grid"),
    ],
    ids=["ath-import", "wg-authored"],
)
def test_a_fallback_records_why_without_changing_the_grid(payload, reason, caplog) -> None:
    """The planner's reason reaches the manifest record and the server log."""

    import logging

    from server.design.schema import DesignConfig
    from server.exports.cad_geometry import resolve_cad_geometry

    with caplog.at_level(logging.WARNING, logger="server.exports.cad_geometry"):
        cad = resolve_cad_geometry(DesignConfig.model_validate(payload))
    record = cad.sampling_record()
    assert record["fallback_reason"] == reason
    # Diagnostics only: the fixed fallback grid, as before.
    assert record["resolution_mm"] == _FALLBACK_CAD_RESOLUTION_MM
    assert record["deviation_mm"] is None and record["warning"] is not None
    assert any(f"fallback reason {reason}" in r.getMessage() for r in caplog.records)


def test_an_accepted_plan_records_no_fallback_reason() -> None:
    from server.exports.cad_geometry import resolve_cad_geometry

    cad = resolve_cad_geometry(_seed())
    assert cad.plan.warning is None
    assert cad.sampling_record()["fallback_reason"] is None


def test_every_fallback_reason_the_planner_can_give_is_listed() -> None:
    """The export no longer asserts its reasons, so this holds the list complete.

    Every literal reason ``_unaccepted_cad_plan`` is given (directly, or through
    the ``reason`` variable) and every ``measure_deviation`` reason behind the
    ``unmeasurable_`` prefix must be in ``CAD_FALLBACK_REASONS``.
    """

    import ast
    import inspect

    import server.exports.sizing as sizing

    def literals(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        return set()  # the f-string: covered by the _note reasons below

    given: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(sizing))):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "reason" for target in node.targets
        ):
            given |= literals(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_unaccepted_cad_plan":
                given |= literals(node.args[2])
            elif node.func.id == "_note":
                given |= {f"unmeasurable_{value}" for value in literals(node.args[1])}
    assert {"probe_exception", "probe_unsampled", "unmeasurable", "non_nested_grid",
            "detuned_mismatch", "search_stalled", "budget_exhausted",
            "unmeasurable_axial_not_nested"} <= given
    assert given <= set(sizing.CAD_FALLBACK_REASONS), given - set(sizing.CAD_FALLBACK_REASONS)


def test_an_unlisted_fallback_reason_is_logged_not_raised(caplog) -> None:
    import logging

    from server.exports.sizing import _unaccepted_cad_plan

    with caplog.at_level(logging.WARNING, logger="server.exports.sizing"):
        plan = _unaccepted_cad_plan(STEP_SURFACE_TOLERANCE_MM, None, "a_future_reason")
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.fallback_reason == "a_future_reason"
    assert any("a_future_reason" in record.getMessage() for record in caplog.records)


def test_a_search_held_at_the_finest_sampling_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reading far outside the target drives the probe to the 1 mm floor, where it stops."""

    calls = _measure_with(monkeypatch, _reading(100.0, 100.0))
    plan = plan_cad_resolution(_config(), tolerance_mm=0.02)
    assert len(calls) < 6  # stopped before the attempt budget ran out
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.fallback_reason == "search_stalled"


def test_a_probe_too_coarse_to_measure_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import hornlab_mesher.config_builder as builder

    monkeypatch.setattr(builder, "resolve_geometry", lambda _config: SimpleNamespace(sampling_metadata={}))
    plan = plan_cad_resolution(_config())
    assert plan.resolution_mm == _FALLBACK_CAD_RESOLUTION_MM
    assert plan.fallback_reason == "probe_unsampled"


def _lattice(n_phi: int, n_length: int, *, shift: float = 0.0):
    """A plane lattice whose 2x refinement nests exactly unless ``shift`` moves it."""

    import numpy as np

    phi, z = np.meshgrid(np.arange(n_phi) / n_phi + shift, np.arange(n_length + 1) / n_length, indexing="ij")
    return np.stack([phi, z, np.zeros_like(phi)], axis=-1), n_phi, n_length


def _grids(monkeypatch: pytest.MonkeyPatch, *answers) -> None:
    """``_point_grid`` answers in call order; each answer is ``(angular, length) -> grid``."""

    import server.exports.sizing as sizing

    calls: list[int] = []

    def point_grid(_params, angular: int, length: int):
        answer = answers[min(len(calls), len(answers) - 1)]
        calls.append(1)
        return answer(angular, length)

    monkeypatch.setattr(sizing, "_point_grid", point_grid)


def test_a_detuned_lattice_that_resolves_to_the_nested_one_is_unmeasurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.exports.sizing import measure_deviation

    # coarse 8x4, its exact 16x8 refinement, then the detuned request snapped back to 16x8.
    _grids(monkeypatch, lambda a, n: _lattice(a, n), lambda a, n: _lattice(a, n), lambda a, n: _lattice(16, 8))
    why: list[str] = []
    assert measure_deviation({}, 8, 4, why) is None
    assert why == ["detuned_unresolved"]


def test_a_non_nested_reference_that_is_no_finer_is_unmeasurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.exports.sizing import measure_deviation

    _grids(
        monkeypatch,
        lambda a, n: _lattice(a, n),
        lambda a, n: _lattice(a, n, shift=0.01),  # the angular lattice does not nest
        lambda a, n: _lattice(a, n),              # the axial refinement does
        lambda a, n: _lattice(8, n),              # the dense reference resolved no finer
    )
    why: list[str] = []
    assert measure_deviation({}, 8, 4, why) is None
    assert why == ["reference_not_finer"]
