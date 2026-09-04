"""BEAT engine adapter gating: per-backend availability, honest refusals."""

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
    solver_warmup._warm_beat("metal")
    assert seen == [True]
    assert solver_warmup.beat_warmup_in_progress() is False

    def explode(**kwargs) -> None:
        raise RuntimeError("Julia would not start")

    monkeypatch.setitem(
        sys.modules, "hornlab_beat_bem", types.SimpleNamespace(warm_up=explode)
    )
    with pytest.raises(RuntimeError):
        solver_warmup._warm_beat("metal")
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

def _probe(available: bool, backend: str | None, reason: str) -> dict[str, object]:
    return {
        "available": available,
        "reason": reason,
        "version": "1",
        "backend": backend,
        "surface_traces": True,
    }


def test_backend_statuses_answer_for_every_backend_from_one_probe(monkeypatch) -> None:
    """The selector needs four answers; the package probe gives one.

    ``beat_engine_status`` reports *the* backend a solve would use, taking the
    first accelerator family whose hardware is present. That cannot populate a
    picker: it says nothing about the three backends it did not choose, and it
    never names the CPU path at all outside ``HORNLAB_BEAT_FORCE_CPU``, even
    though the CPU path is available wherever a Julia is.
    """

    monkeypatch.setattr(beat, "_load_api", lambda: object())
    monkeypatch.setattr(
        beat, "beat_status", lambda: _probe(True, "metal", "Apple Silicon GPU detected")
    )
    monkeypatch.setattr(beat, "_cpu_backend_status", lambda _package: (True, "Julia found"))

    statuses = beat.beat_backend_statuses()

    assert set(statuses) == set(beat.BEAT_BACKENDS)
    assert statuses["metal"]["available"] is True
    assert statuses["metal"]["reason"] == "Apple Silicon GPU detected"
    # The CPU path is available beside the accelerator, which is the whole
    # point: a Mac user can now choose between BEAT-Metal and BEAT-CPU.
    assert statuses["cpu"]["available"] is True
    # An unselected accelerator says what it would take and what was found,
    # rather than repeating a reason that is about different hardware.
    assert statuses["cuda"]["available"] is False
    assert "NVIDIA GPU" in statuses["cuda"]["reason"]
    assert "selected the metal backend" in statuses["cuda"]["reason"]
    # Surface-trace retention is a property of the package, so every backend
    # reports it, not just the one the probe named.
    assert all(status["surface_traces"] is True for status in statuses.values())


def test_backend_statuses_report_the_probe_reason_when_nothing_was_selected(
    monkeypatch,
) -> None:
    monkeypatch.setattr(beat, "_load_api", lambda: object())
    monkeypatch.setattr(
        beat, "beat_status", lambda: _probe(False, None, "No supported GPU was detected")
    )
    monkeypatch.setattr(beat, "_cpu_backend_status", lambda _package: (True, "Julia found"))

    statuses = beat.beat_backend_statuses()

    assert [name for name, item in statuses.items() if item["available"]] == ["cpu"]
    # A verdict about the whole engine names every family at once and belongs
    # on no single row: each already states its own prerequisite, and that
    # generic message also advises that BEAT is GPU-only, which stopped being
    # true when the CPU backend became a user-facing engine.
    assert statuses["rocm"]["reason"] == (
        "Needs an AMD ROCm runtime with a functional AMDGPU.jl."
    )


def test_a_diagnostic_about_one_family_is_quoted_on_that_row_only(monkeypatch) -> None:
    """A broken CUDA install is the message that actually helps someone.

    "An NVIDIA GPU is present but the CUDA path is not usable" tells a user
    what to fix. On the Metal row of the same Windows box it is noise, so the
    quote follows the family the probe was talking about.
    """

    monkeypatch.setattr(beat, "_load_api", lambda: object())
    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: _probe(
            False,
            None,
            "An NVIDIA GPU is present but the CUDA path is not usable: "
            "CUDA.functional() is false (no driver)",
        ),
    )
    monkeypatch.setattr(beat, "_cpu_backend_status", lambda _package: (True, "Julia found"))

    statuses = beat.beat_backend_statuses()

    assert "CUDA.functional() is false" in statuses["cuda"]["reason"]
    assert "CUDA" not in statuses["metal"]["reason"]
    assert statuses["metal"]["reason"] == (
        "Needs an Apple Silicon GPU with a functional Metal.jl."
    )


def test_a_missing_package_is_a_capability_state_for_every_backend(monkeypatch) -> None:
    monkeypatch.setattr(beat, "_load_api", lambda: None)

    statuses = beat.beat_backend_statuses()

    assert set(statuses) == set(beat.BEAT_BACKENDS)
    assert not any(item["available"] for item in statuses.values())
    assert all("not importable" in item["reason"] for item in statuses.values())


def test_a_chosen_backend_is_gated_on_its_own_availability(monkeypatch) -> None:
    """Not on BEAT's as a whole, which is a different question on a GPU host.

    On a Mac the package probe reports ``metal``. Asking "is BEAT available"
    before a CPU solve would have said yes and then run on Metal; asking it
    before a CUDA solve would have said yes and then failed on a device this
    host does not have. The backend the user picked is the one whose
    availability decides.
    """

    monkeypatch.setattr(beat, "_load_api", lambda: object())
    monkeypatch.setattr(
        beat,
        "beat_backend_statuses",
        lambda: {
            "metal": _probe(True, "metal", "Apple Silicon GPU detected"),
            "cuda": _probe(False, "cuda", "Needs an NVIDIA GPU"),
            "rocm": _probe(False, "rocm", "Needs an AMD ROCm runtime"),
            "cpu": _probe(True, "cpu", "Julia found"),
        },
    )

    with pytest.raises(beat.BeatUnavailable, match="Needs an NVIDIA GPU"):
        beat.solve_beat_from_msh_text("$MeshFormat\n", _context(), backend="cuda")
    with pytest.raises(beat.BeatUnavailable, match="Unknown BEAT backend"):
        beat.solve_beat_from_msh_text("$MeshFormat\n", _context(), backend="vulkan")


def test_engine_names_and_backends_round_trip() -> None:
    for backend in beat.BEAT_BACKENDS:
        name = beat.beat_engine_name(backend)
        assert name == f"beat-{backend}"
        assert beat.beat_engine_backend(name) == backend
        assert beat.is_beat_engine(name)
        assert beat.BEAT_BACKEND_LABELS[backend]
        assert beat.BeatEngine(backend).name == name

    # The bare family name is a valid request but names no backend: the caller
    # that can see the host's capabilities picks which variant it means.
    assert beat.beat_engine_backend(beat.LEGACY_BEAT_ENGINE) is None
    assert beat.is_beat_engine(beat.LEGACY_BEAT_ENGINE)
    assert beat.BeatEngine().name == beat.LEGACY_BEAT_ENGINE
    assert beat.BeatEngine().backend is None

    assert beat.beat_engine_backend("beat-vulkan") is None
    assert not beat.is_beat_engine("bempp")
    with pytest.raises(ValueError, match="Unknown BEAT backend"):
        beat.BeatEngine("vulkan")
