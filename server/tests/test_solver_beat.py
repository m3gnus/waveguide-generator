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
