"""The BEMPP sweep runs in one cancellable process unless asked otherwise.

Parallel sweeps were the default until they were measured on an M1 Max (10
cores, numba assembly backend, 766-triangle quarter-domain mesh, 3 repeats).
They bought 1.5x on a 200-frequency sweep and nothing measurable at 80, where
the engine's auto mode first splits -- and they cost Stop everything it means:
a cancelled 200-frequency sweep returned after 88 s instead of 0.3 s, having
solved the whole sweep anyway, because the parent has to join every sibling
chunk before it can raise.  ``server/solver/bempp.py:DEFAULT_SOLVE_WORKERS``
carries the numbers.

These pin the default, the escape hatch in both directions, and the promise
that a sweep which is going to split says so first.
"""

from __future__ import annotations

from types import SimpleNamespace

import hornlab_bempp_bem
import pytest

from server.solver import bempp


def test_the_sweep_is_serial_unless_asked(monkeypatch):
    """The default has to be the cancellable one."""

    monkeypatch.delenv("WG2_SOLVE_WORKERS", raising=False)

    assert bempp._resolved_workers() == 1
    assert bempp.DEFAULT_SOLVE_WORKERS == 1


def test_zero_still_selects_the_engines_auto_mode(monkeypatch):
    """Turning parallel sweeps back on must not require a code change."""

    monkeypatch.setenv("WG2_SOLVE_WORKERS", "0")

    assert bempp._resolved_workers() == 0


@pytest.mark.parametrize("raw, expected", [("1", 1), ("2", 2), ("8", 8), (" 4 ", 4)])
def test_an_explicit_count_is_honoured(monkeypatch, raw, expected):
    monkeypatch.setenv("WG2_SOLVE_WORKERS", raw)

    assert bempp._resolved_workers() == expected


@pytest.mark.parametrize("raw", ["", "   ", "auto", "2.5", "-", "many"])
def test_an_unusable_value_falls_back_to_the_default_not_to_auto(monkeypatch, raw):
    """A typo must not silently opt the user into the uncancellable mode."""

    monkeypatch.setenv("WG2_SOLVE_WORKERS", raw)

    assert bempp._resolved_workers() == bempp.DEFAULT_SOLVE_WORKERS


def test_a_negative_count_is_clamped_to_auto_not_to_a_crash(monkeypatch):
    """max(0, ...) is the existing contract; -1 is nonsense, not a request."""

    monkeypatch.setenv("WG2_SOLVE_WORKERS", "-3")

    assert bempp._resolved_workers() == 0


@pytest.mark.parametrize(
    "workers, frequencies, expected",
    [
        (1, 400, False),      # the default: one process, whatever the sweep
        (0, 79, False),       # auto keeps short sweeps in one warm process
        (0, 80, True),        # ...and splits at two workers of 40
        (0, 200, True),
        (2, 4, True),         # an explicit count overrides the engine's arithmetic
        (8, 1, True),
    ],
)
def test_the_split_is_announced_exactly_when_it_happens(
    monkeypatch, workers, frequencies, expected
):
    """Claiming a split that will not happen is as wrong as staying silent."""

    monkeypatch.setattr(hornlab_bempp_bem, "_detect_worker_count", lambda: 8)
    actual = bempp._sweep_will_split(workers, frequencies)
    assert actual is expected
    assert actual is (hornlab_bempp_bem._resolve_worker_count(workers, frequencies) > 1)


def test_auto_worker_prediction_uses_the_engines_core_limit(monkeypatch) -> None:
    monkeypatch.setattr(hornlab_bempp_bem, "_detect_worker_count", lambda: 1)

    assert hornlab_bempp_bem._resolve_worker_count(0, 200) == 1
    assert bempp._sweep_will_split(0, 200) is False


def test_an_importable_but_incompatible_engine_worker_resolver_fails_loudly(
    monkeypatch,
) -> None:
    def incompatible(_workers, _frequencies):
        raise TypeError("pinned worker resolver API changed")

    monkeypatch.setattr(hornlab_bempp_bem, "_resolve_worker_count", incompatible)

    with pytest.raises(TypeError, match="resolver API changed"):
        bempp._sweep_will_split(0, 200)


def test_explicit_frequency_lists_are_reported_as_the_serial_sweeps_they_are(
    monkeypatch,
) -> None:
    class WorkerConfig(SimpleNamespace):
        def __init__(self, **kwargs):
            super().__init__(workers=1, **kwargs)

    class ExplicitContext:
        solver_mode = "full_3d"
        frequencies_hz = (500.0, 812.3, 1000.0)
        frequency_range = (500.0, 1000.0)
        num_frequencies = 3
        frequency_spacing = "log"
        source_motion = "normal"
        mesh_validation_mode = "warn"
        verbose = False
        sim_type = 2

        @staticmethod
        def validate():
            return None

    stages: list[tuple[str, float, str]] = []
    native_workers: list[int] = []

    monkeypatch.setenv("WG2_SOLVE_WORKERS", "4")
    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "SolveConfig", WorkerConfig)
    monkeypatch.setattr(bempp, "BIEFormulation", SimpleNamespace(COMPLEX_K="complex_k"))
    monkeypatch.setattr(bempp, "ObservationConfig", object)
    monkeypatch.setattr(bempp, "ObservationFrame", object)
    monkeypatch.setattr(bempp, "bempp_solve", lambda *_args: pytest.fail("grid path ran"))
    monkeypatch.setattr(
        bempp,
        "bempp_solve_frequencies",
        lambda _path, _frequencies, config: native_workers.append(config.workers)
        or SimpleNamespace(timings={}),
    )
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {
            "available": True,
            "reason": "ready",
            "assembly_backend": "numba",
            "warning": None,
        },
    )
    monkeypatch.setattr(
        bempp,
        "observation_config",
        lambda *_args, **_kwargs: SimpleNamespace(distance_m=2.0, origin="mouth"),
    )
    monkeypatch.setattr(bempp, "native_observation_frame", lambda *_args: None)
    monkeypatch.setattr(bempp, "native_symmetry_plane", lambda _context: None)
    monkeypatch.setattr(
        bempp,
        "build_solver_response",
        lambda **kwargs: {"metadata": kwargs["metadata"]},
    )

    response = bempp.solve_bempp_from_msh_text(
        "msh",
        ExplicitContext(),
        stage_callback=lambda *values: stages.append(values),
        force_serial=True,
    )

    assert native_workers == [1]
    assert response["metadata"]["bempp"]["workers"] == 1
    assert not any("splits this sweep" in message for _, _, message in stages)
    assert any("Ignoring WG2_SOLVE_WORKERS=4" in message for _, _, message in stages)
