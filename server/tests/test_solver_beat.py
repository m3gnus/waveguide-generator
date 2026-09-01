"""BEAT engine adapter gating: GPU-only availability, honest refusals."""

from __future__ import annotations

import pytest

from server.solver import beat
from server.solver.context import SolverContext


def _context(**overrides) -> SolverContext:
    values = {
        "design": None,
        "frequency_range": (500.0, 2000.0),
        "num_frequencies": 4,
    }
    values.update(overrides)
    return SolverContext(**values)


def test_probe_reports_missing_package_as_capability_state(monkeypatch) -> None:
    monkeypatch.setattr(beat, "_load_api", lambda: None)
    beat.beat_status.cache_clear()
    try:
        status = beat.beat_status()
        assert status["available"] is False
        assert "not importable" in status["reason"]
    finally:
        beat.beat_status.cache_clear()


def test_unavailable_status_refuses_solve(monkeypatch) -> None:
    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: {"available": False, "reason": "no supported GPU", "version": None, "backend": None},
    )
    monkeypatch.setattr(beat, "_load_api", lambda: object())
    with pytest.raises(beat.BeatUnavailable, match="no supported GPU"):
        beat.solve_beat_from_msh_text("$MeshFormat\n", _context())


def test_circsym_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="circsym"):
        beat.solve_beat_from_msh_text(
            "$MeshFormat\n", _context(solver_mode="circsym")
        )


def test_infinite_baffle_is_rejected() -> None:
    with pytest.raises(ValueError, match="infinite-baffle"):
        beat.solve_beat_from_msh_text("$MeshFormat\n", _context(sim_type=1))


def test_surface_trace_support_is_detected_from_the_installed_package() -> None:
    """Detect the capability instead of assuming it.

    ``SolveConfig.surface_traces`` was added after the currently pinned build,
    so the adapter has to work against a package that has it and one that does
    not; passing the flag to a solver that ignores it would report retained
    traces that never arrive.
    """

    from dataclasses import dataclass

    @dataclass
    class WithTraces:
        surface_traces: bool = False

    @dataclass
    class WithoutTraces:
        quadrature_order: int = 4

    class Package:
        def __init__(self, solve_config):
            self.SolveConfig = solve_config

    assert beat._package_retains_surface_traces(Package(WithTraces)) is True
    assert beat._package_retains_surface_traces(Package(WithoutTraces)) is False
    assert beat._package_retains_surface_traces(Package(None)) is False
    # A stub whose signature cannot be read is a "no", not a crash.
    assert beat._package_retains_surface_traces(Package(object())) is False
