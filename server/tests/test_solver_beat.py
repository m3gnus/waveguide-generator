"""BEAT engine adapter gating: GPU-only availability, honest refusals."""

from __future__ import annotations

import sys

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


def test_no_warmup_message_when_nothing_is_warming() -> None:
    """The stage message must describe a wait that is actually happening."""

    stages: list[tuple[str, float, str]] = []

    assert beat.announce_beat_warmup_wait(None) is False
    assert (
        beat.announce_beat_warmup_wait(
            lambda stage, fraction, message: stages.append((stage, fraction, message))
        )
        is False
    )
    assert stages == []


def test_the_warmup_publishes_that_it_holds_the_worker(monkeypatch) -> None:
    """``_warm_beat`` is inside the one Julia worker every BEAT solve shares.

    A solve started while the boot warmup is still in there waits for that
    lock -- 10-16 s on the packaged 0.3.1 app -- so the warmup has to say it is
    running for the solve to be able to explain the wait. The flag must clear
    even when the warmup fails, or every later solve would blame a warmup that
    is long gone.
    """

    import types

    from server.solver import warmup as solver_warmup

    seen: list[bool] = []

    def warm_up(**kwargs) -> None:
        seen.append(solver_warmup.beat_warmup_in_progress())

    monkeypatch.setitem(
        sys.modules, "hornlab_beat_bem", types.SimpleNamespace(warm_up=warm_up)
    )

    assert solver_warmup.beat_warmup_in_progress() is False
    solver_warmup._warm_beat({"available": True, "backend": "metal"})
    assert seen == [True]
    assert solver_warmup.beat_warmup_in_progress() is False

    def explode(**kwargs) -> None:
        raise RuntimeError("Julia would not start")

    monkeypatch.setitem(
        sys.modules, "hornlab_beat_bem", types.SimpleNamespace(warm_up=explode)
    )
    with pytest.raises(RuntimeError):
        solver_warmup._warm_beat({"available": True, "backend": "metal"})
    assert solver_warmup.beat_warmup_in_progress() is False


def test_a_solve_during_the_warmup_says_what_it_is_waiting_for(monkeypatch) -> None:
    """Otherwise the user sits on "Configuring BEAT Engine BEM solve (metal)".

    That message is true and useless: nothing is being configured for those
    10-16 s, the engine is compiling itself in the boot warmup and this solve
    is behind it. Pre-empting the warmup was measured and is not the fix --
    solves that won the race still took 16-19 s -- so the fix is saying so,
    through the ordinary stage callback the job's events already carry.
    """

    from server.solver import warmup as solver_warmup

    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: {
            "available": True,
            "reason": "test",
            "version": "0.1.0",
            "backend": "metal",
            "surface_traces": False,
        },
    )
    monkeypatch.setattr(beat, "_load_api", lambda: object())

    stages: list[tuple[str, float, str]] = []

    def stage(stage_name: str, fraction: float, message: str) -> None:
        stages.append((stage_name, fraction, message))

    with solver_warmup.beat_warmup_recorded():
        # The stub package has no ObservationConfig, so the solve fails right
        # after configuration -- long after the point the user is told why the
        # worker is busy, which is what this asserts.
        with pytest.raises(Exception):
            beat.solve_beat_from_msh_text("$MeshFormat\n", _context(), stage_callback=stage)

    messages = [message for _, _, message in stages]
    assert "Configuring BEAT Engine BEM solve (metal)" in messages
    assert solver_warmup.BEAT_WARMUP_STAGE_MESSAGE in messages
    assert messages.index(solver_warmup.BEAT_WARMUP_STAGE_MESSAGE) == 1
