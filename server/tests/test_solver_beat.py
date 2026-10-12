"""BEAT engine adapter gating: per-backend availability, honest refusals."""

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


def test_infinite_baffle_is_rejected() -> None:
    with pytest.raises(beat.BeatUnavailable, match="infinite-baffle"):
        beat.solve_beat_from_msh_text("$MeshFormat\n", _context(sim_type=1))


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
