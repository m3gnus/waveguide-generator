"""Native adapters under mocked package boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.solver.context import SolverContext
from server.solver import bempp, circsym, metal


def _context(*, axial: bool = False, sim_type: int = 2) -> SolverContext:
    return SolverContext(
        design=DesignConfig.model_validate(
            {
                "formula": "OSSE",
                "mesh": {"quadrants": 14},
                "source": {"velocity_convention": "axial" if axial else "normal"},
            }
        ),
        frequency_range=(500.0, 1000.0),
        num_frequencies=2,
        quadrants=14,
        sim_type=sim_type,
        source_motion="axial" if axial else "normal",
    )


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        frequencies_hz=np.asarray([500.0, 1000.0]),
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        observation_planes=["horizontal", "vertical"],
        pressure_complex=np.ones((2, 2, 3), dtype=np.complex128) * 20e-6,
        directivity_db=np.asarray(
            [[[0.0, -6.0, -20.0], [0.0, -6.0, -20.0]]] * 2
        ),
        impedance=np.asarray([1j, 1j]),
        timings={"solve": 0.01},
        solver_log=[],
        native_diagnostics=[],
    )


def test_metal_readiness_smoke_is_cached(monkeypatch) -> None:
    probes = 0

    def discover_runtime(*, run_smoke_test: bool):
        nonlocal probes
        assert run_smoke_test is True
        probes += 1
        return SimpleNamespace(
            available=True,
            helper_executable_path=__file__,
            unavailable_reasons=(),
            smoke_test_error=None,
        )

    monkeypatch.setattr(
        metal,
        "discover_metal_backend",
        lambda: SimpleNamespace(
            available=True,
            native_helper_available=True,
            reason="",
            supported_platform=True,
            native_executable=__file__,
        ),
    )
    monkeypatch.setattr(metal, "discover_native_runtime", discover_runtime)
    monkeypatch.setattr(metal, "native_config", object())
    monkeypatch.setattr(metal, "native_solve", object())
    metal.metal_status.cache_clear()
    try:
        first = metal.metal_status()
        first["available"] = False
        assert metal.metal_status()["available"] is True
        assert probes == 1
    finally:
        metal.metal_status.cache_clear()


def test_metal_readiness_failure_is_retried(monkeypatch) -> None:
    probes = 0

    def discover_backend():
        nonlocal probes
        probes += 1
        return SimpleNamespace(
            available=probes > 1,
            native_helper_available=probes > 1,
            reason="temporarily unavailable",
            supported_platform=True,
            native_executable=__file__ if probes > 1 else None,
        )

    monkeypatch.setattr(metal, "discover_metal_backend", discover_backend)
    monkeypatch.setattr(
        metal,
        "discover_native_runtime",
        lambda **_kwargs: SimpleNamespace(
            available=True,
            helper_executable_path=__file__,
            unavailable_reasons=(),
            smoke_test_error=None,
        ),
    )
    monkeypatch.setattr(metal, "native_config", object())
    monkeypatch.setattr(metal, "native_solve", object())
    metal.metal_status.cache_clear()
    try:
        assert metal.metal_status()["available"] is False
        assert metal.metal_status()["available"] is True
        assert metal.metal_status()["available"] is True
        assert probes == 2
    finally:
        metal.metal_status.cache_clear()


class _Config(SimpleNamespace):
    pass


def test_metal_adapter_maps_quadrants_axial_drive_stages_and_cancellation(monkeypatch) -> None:
    captured = {}
    callbacks = []

    def config_factory(**kwargs):
        captured.update(kwargs)
        return _Config(**kwargs)

    def solve(path, config):
        config.progress_callback(0, 2, 500.0)
        config.progress_callback(1, 2, 1000.0)
        return _result()

    monkeypatch.setattr(metal, "native_config", config_factory)
    monkeypatch.setattr(metal, "native_solve", solve)
    monkeypatch.setattr(
        metal,
        "metal_status",
        lambda: {"available": True, "reason": "mock helper loadable", "version": "test"},
    )
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    cancellations = 0

    def cancel():
        nonlocal cancellations
        cancellations += 1

    response = metal.solve_metal_from_msh_text(
        "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
        _context(axial=True),
        stage_callback=lambda *values: callbacks.append(values),
        cancellation_callback=cancel,
    )
    assert captured["native_symmetry_plane"] == "yz"
    assert captured["source_motion"] == "axial"
    assert captured["native_check_open_edges"] is False
    assert cancellations == 3
    assert [stage for stage, _, _ in callbacks] == [
        "setup",
        "frequency_solve",
        "frequency_solve",
        "finalizing",
    ]
    assert response["metadata"]["source_motion"] == "axial"


def test_metal_infinite_baffle_requires_and_maps_aperture_tag(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: captured.update(kwargs) or _Config(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda path, config: _result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    with pytest.raises(RuntimeError, match="aperture tag"):
        metal.solve_metal_from_msh_text("msh", _context(sim_type=1), mesh_metadata={})
    response = metal.solve_metal_from_msh_text(
        "msh", _context(sim_type=1), mesh_metadata={"apertureTag": 12}
    )
    assert captured["aperture_tag"] == 12
    assert captured["mesh_validate"] is True
    assert response["metadata"]["infinite_baffle"]["backend"] == "full_3d_coupled"


def test_bempp_adapter_is_cpu_fallback_and_rejects_infinite_baffle(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(bempp, "SolveConfig", lambda **kwargs: captured.update(kwargs) or _Config(**kwargs))
    monkeypatch.setattr(bempp, "bempp_solve", lambda path, config: _result())
    monkeypatch.setattr(bempp, "BIEFormulation", SimpleNamespace(COMPLEX_K="complex_k"))
    monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": True, "reason": "mock CPU", "assembly_backend": "numba"},
    )
    response = bempp.solve_bempp_from_msh_text("msh", _context())
    assert captured["assembly_backend"] == "numba"
    assert captured["native_symmetry_plane"] == "yz"
    assert response["metadata"]["solver_backend"] == "bempp"
    with pytest.raises(ValueError, match="cannot solve coupled infinite-baffle"):
        bempp.solve_bempp_from_msh_text("msh", _context(sim_type=1))


def test_circsym_adapter_uses_meridian_cancellation_stages_and_coupled_ib(monkeypatch) -> None:
    captured = {}
    stages = []

    class MeridianBuild:
        baffle_z = 0.06
        metadata = {"apertureTag": 12, "segment_count": 8}

        @staticmethod
        def as_metal_meridian(cls):
            return "native-meridian"

    def config_factory(**kwargs):
        captured.update(kwargs)
        return _Config(**kwargs)

    def solve(meridian, config):
        assert meridian == "native-meridian"
        config.progress_callback(0, 2, 500.0)
        config.on_frequency_result(0, 500.0, {})
        return _result()

    monkeypatch.setattr(circsym, "build_meridian", lambda config: MeridianBuild())
    monkeypatch.setattr(circsym, "circsym_rejection_reasons", lambda config: [])
    monkeypatch.setattr(circsym, "MeridianMesh", object)
    monkeypatch.setattr(circsym, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(circsym, "native_config", config_factory)
    monkeypatch.setattr(circsym, "solve_circsym", solve)
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "mock"})
    monkeypatch.setattr(circsym, "metal_status", lambda: {"available": True, "reason": "mock"})
    cancellations = 0

    def cancel():
        nonlocal cancellations
        cancellations += 1

    context = _context(axial=True, sim_type=1)
    context.solver_mode = "circsym"
    response = circsym.solve_circsym_design(
        context,
        stage_callback=lambda *values: stages.append(values),
        cancellation_callback=cancel,
    )
    assert captured["circsym_baffle_z"] == 0.06
    assert captured["circsym_aperture_tag"] == 12
    assert captured["source_motion"] == "axial"
    assert cancellations == 4
    assert [stage for stage, _, _ in stages] == [
        "mesh_prepare",
        "setup",
        "frequency_solve",
        "finalizing",
    ]
    assert response["metadata"]["solver_mode"] == "circsym"
    assert response["metadata"]["infinite_baffle"]["backend"] == "circsym_coupled"
