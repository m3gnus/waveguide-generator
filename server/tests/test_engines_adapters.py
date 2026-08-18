"""Native adapters under mocked package boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.solver.context import SolverContext
from server.solver import bempp, circsym, metal


def _context(
    *, axial: bool = False, sim_type: int = 2, field_plane: bool = True
) -> SolverContext:
    context = SolverContext(
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
    context.polar_config["field_plane"] = field_plane
    return context


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


def _cabinet_msh(*, aperture: bool = False) -> str:
    aperture_element = "2 2 2 12 12 4 5 6" if aperture else "2 2 2 1 1 4 5 6"
    far_z = 0.50 if aperture else -0.28
    return f"""$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
9
1 0 0 0
2 0.02 0 0
3 0 0.02 0
4 0 0 0.05
5 0.12 0 0.05
6 0 0.12 0.05
7 0 0 {far_z}
8 0.20 0 {far_z}
9 0 0.20 {far_z}
$EndNodes
$Elements
3
1 2 2 2 2 1 2 3
{aperture_element}
3 2 2 1 1 7 8 9
$EndElements
"""


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
        _cabinet_msh(),
        _context(axial=True),
        stage_callback=lambda *values: callbacks.append(values),
        cancellation_callback=cancel,
    )
    assert captured["native_symmetry_plane"] == "yz"
    assert captured["source_motion"] == "axial"
    assert captured["frame_override"].axis.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert captured["frame_override"].origin.tolist() == pytest.approx([0.0, 0.04, 0.05])
    assert getattr(captured["observation"], "custom_points", None) is None
    assert captured["native_check_open_edges"] is False
    assert captured["return_surface_traces"] is True
    assert cancellations == 3
    assert [stage for stage, _, _ in callbacks] == [
        "setup",
        "frequency_solve",
        "frequency_solve",
        "finalizing",
    ]
    assert response["metadata"]["source_motion"] == "axial"


def test_metal_adapter_emits_each_frequency_through_the_canonical_mapper(monkeypatch) -> None:
    captured = {}
    streamed: list[tuple[int, dict]] = []

    def config_factory(**kwargs):
        captured.update(kwargs)
        return _Config(**kwargs)

    def solve(path, config):
        del path
        complete = _result()
        entry = {
            "observation_angles_deg": complete.observation_angles_deg,
            "observation_planes": complete.observation_planes,
            "observation_pressure_complex": complete.pressure_complex[0],
            "observation_directivity_db": complete.directivity_db[0],
            "impedance": complete.impedance[0],
        }
        assert config.on_frequency_result(0, 500.0, entry) is True
        return complete

    monkeypatch.setattr(metal, "native_config", config_factory)
    monkeypatch.setattr(metal, "native_solve", solve)
    monkeypatch.setattr(
        metal,
        "native_solve_frequencies",
        lambda path, frequencies, config: solve(path, config),
    )
    monkeypatch.setattr(
        metal,
        "metal_status",
        lambda: {"available": True, "reason": "mock helper loadable", "version": "test"},
    )
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))

    metal.solve_metal_from_msh_text(
        _cabinet_msh(),
        _context(),
        result_callback=lambda index, result: streamed.append((index, result)),
    )

    assert "on_frequency_result" in captured
    assert len(streamed) == 1
    assert streamed[0][0] == 0
    assert streamed[0][1]["frequencies"] == [500.0]
    assert streamed[0][1]["metadata"]["provisional"] == {
        "completed_frequency_count": 1,
        "expected_frequency_count": 2,
    }


def test_metal_trace_size_cap_skips_retention_before_mocked_solve(monkeypatch) -> None:
    captured = {}

    def config_factory(**kwargs):
        captured.update(kwargs)
        return _Config(**kwargs)

    monkeypatch.setattr(metal, "native_config", config_factory)
    monkeypatch.setattr(metal, "native_solve", lambda _path, _config: _result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))

    response = metal.solve_metal_from_msh_text(
        _cabinet_msh(),
        _context(),
        field_trace_cap_bytes=1,
    )

    assert captured["return_surface_traces"] is False
    assert response["_field_traces"] is None
    assert response["_field_trace_unavailable_reason"] == "size_cap_exceeded"
    assert response["metadata"]["field_trace_retention"] == {
        "estimated_bytes": 384,
        "cap_bytes": 1,
    }


def test_metal_field_plane_option_disables_trace_retention(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        metal,
        "native_config",
        lambda **kwargs: captured.update(kwargs) or _Config(**kwargs),
    )
    monkeypatch.setattr(metal, "native_solve", lambda _path, _config: _result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))

    response = metal.solve_metal_from_msh_text(
        _cabinet_msh(), _context(field_plane=False)
    )

    assert captured["return_surface_traces"] is False
    assert response["_field_traces"] is None
    assert response["_field_trace_unavailable_reason"] == "disabled_by_option"
    assert response["metadata"]["field_trace_retention"]["estimated_bytes"] is None


def test_metal_infinite_baffle_requires_and_maps_aperture_tag(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: captured.update(kwargs) or _Config(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda path, config: _result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    with pytest.raises(RuntimeError, match="aperture tag"):
        metal.solve_metal_from_msh_text("msh", _context(sim_type=1), mesh_metadata={})
    response = metal.solve_metal_from_msh_text(
        _cabinet_msh(aperture=True),
        _context(sim_type=1),
        mesh_metadata={"apertureTag": 12},
    )
    assert captured["aperture_tag"] == 12
    assert captured["mesh_validate"] is True
    assert captured["return_surface_traces"] is False
    assert captured["frame_override"].origin.tolist() == pytest.approx([0.0, 0.04, 0.05])
    assert response["metadata"]["infinite_baffle"]["backend"] == "full_3d_coupled"
    assert response["_field_trace_unavailable_reason"] == "unsupported_solve_mode"


def test_bempp_adapter_is_cpu_fallback_and_rejects_infinite_baffle(monkeypatch) -> None:
    captured = {}
    created = {}

    def config_factory(**kwargs):
        captured.update(kwargs)
        created["config"] = _Config(source_motion="normal", **kwargs)
        return created["config"]

    def solve(_path, _config):
        result = _result()
        result.surface_pressure_complex = np.ones((2, 9), dtype=np.complex128)
        result.surface_neumann_complex = np.ones((2, 3), dtype=np.complex128)
        return result

    monkeypatch.setattr(bempp, "SolveConfig", config_factory)
    monkeypatch.setattr(bempp, "bempp_solve", solve)
    monkeypatch.setattr(bempp, "BIEFormulation", SimpleNamespace(COMPLEX_K="complex_k"))
    monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": True, "reason": "mock CPU", "assembly_backend": "numba"},
    )
    response = bempp.solve_bempp_from_msh_text(_cabinet_msh(), _context(axial=True))
    assert captured["assembly_backend"] == "numba"
    assert captured["native_symmetry_plane"] == "yz"
    assert captured["return_surface_traces"] is True
    assert created["config"].source_motion == "axial"
    assert captured["frame_override"].axis.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert captured["frame_override"].origin.tolist() == pytest.approx([0.0, 0.04, 0.05])
    assert getattr(captured["observation"], "custom_points", None) is None
    assert response["metadata"]["solver_backend"] == "bempp"
    assert response["metadata"]["field_trace_retention"] == {
        "estimated_bytes": 384,
        "cap_bytes": 256 * 1024 * 1024,
    }
    assert response["_field_traces"].backend == "bempp"
    with pytest.raises(ValueError, match="cannot solve coupled infinite-baffle"):
        bempp.solve_bempp_from_msh_text("msh", _context(sim_type=1))


def test_bempp_field_plane_option_disables_trace_retention(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        bempp,
        "SolveConfig",
        lambda **kwargs: captured.update(kwargs) or _Config(**kwargs),
    )
    monkeypatch.setattr(bempp, "bempp_solve", lambda _path, _config: _result())
    monkeypatch.setattr(bempp, "BIEFormulation", SimpleNamespace(COMPLEX_K="complex_k"))
    monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": True, "reason": "mock CPU", "assembly_backend": "numba"},
    )

    response = bempp.solve_bempp_from_msh_text(
        _cabinet_msh(), _context(field_plane=False)
    )

    assert captured["return_surface_traces"] is False
    assert response["_field_traces"] is None
    assert response["_field_trace_unavailable_reason"] == "disabled_by_option"
    assert response["metadata"]["field_trace_retention"]["estimated_bytes"] is None


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


def _explicit_context(frequencies: tuple[float, ...]) -> SolverContext:
    context = _context()
    context.frequencies_hz = frequencies
    context.frequency_range = (frequencies[0], frequencies[-1])
    context.num_frequencies = len(frequencies)
    context.validate()
    return context


def _result_at(frequencies: tuple[float, ...]) -> SimpleNamespace:
    count = len(frequencies)
    return SimpleNamespace(
        frequencies_hz=np.asarray(frequencies),
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        observation_planes=["horizontal", "vertical"],
        pressure_complex=np.ones((count, 2, 3), dtype=np.complex128) * 20e-6,
        directivity_db=np.asarray([[[0.0, -6.0, -20.0], [0.0, -6.0, -20.0]]] * count),
        impedance=np.asarray([1j] * count),
        timings={"solve": 0.01},
        solver_log=[],
        native_diagnostics=[],
    )


@pytest.mark.parametrize("frequencies", [(500.0, 812.3, 1000.0), (1234.5,)])
def test_metal_adapter_solves_an_explicit_list_verbatim(monkeypatch, frequencies) -> None:
    seen: dict[str, object] = {}

    def solve_frequencies(path, freqs, config):
        seen["freqs"] = list(freqs)
        return _result_at(frequencies)

    monkeypatch.setattr(metal, "native_config", lambda **kwargs: _Config(**kwargs))
    monkeypatch.setattr(
        metal, "native_solve", lambda path, config: pytest.fail("grid path must not run")
    )
    monkeypatch.setattr(metal, "native_solve_frequencies", solve_frequencies)
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "mock"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))

    response = metal.solve_metal_from_msh_text("msh", _explicit_context(frequencies))
    assert seen["freqs"] == list(frequencies)
    assert response["frequencies"] == list(frequencies)
    assert response["metadata"]["frequency_spacing"] == "explicit"
    assert response["metadata"]["frequency_source"] == "explicit_list"


def test_metal_adapter_refuses_an_explicit_list_the_pin_cannot_solve(monkeypatch) -> None:
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: _Config(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda path, config: _result())
    monkeypatch.setattr(metal, "native_solve_frequencies", None)
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "mock"})
    with pytest.raises(metal.MetalUnavailable, match="explicit frequency lists"):
        metal.solve_metal_from_msh_text("msh", _explicit_context((500.0, 1000.0)))


def test_bempp_adapter_solves_an_explicit_list_verbatim(monkeypatch) -> None:
    frequencies = (500.0, 812.3, 1000.0)
    seen: dict[str, object] = {}
    monkeypatch.setattr(bempp, "SolveConfig", lambda **kwargs: _Config(**kwargs))
    monkeypatch.setattr(
        bempp, "bempp_solve", lambda path, config: pytest.fail("grid path must not run")
    )

    def solve_frequencies(path, freqs, config):
        seen["freqs"] = list(freqs)
        return _result_at(frequencies)

    monkeypatch.setattr(bempp, "bempp_solve_frequencies", solve_frequencies)
    monkeypatch.setattr(bempp, "BIEFormulation", SimpleNamespace(COMPLEX_K="complex_k"))
    monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": True, "reason": "mock CPU", "assembly_backend": "numba"},
    )
    response = bempp.solve_bempp_from_msh_text("msh", _explicit_context(frequencies))
    assert seen["freqs"] == list(frequencies)
    assert response["metadata"]["frequency_source"] == "explicit_list"


def test_circsym_adapter_solves_an_explicit_list_verbatim(monkeypatch) -> None:
    frequencies = (500.0, 812.3, 1000.0)
    seen: dict[str, object] = {}

    class MeridianBuild:
        baffle_z = 0.06
        metadata = {"segment_count": 8}

        @staticmethod
        def as_metal_meridian(cls):
            return "native-meridian"

    def solve_circsym_frequencies(meridian, freqs, config):
        seen["freqs"] = list(freqs)
        return _result_at(frequencies)

    monkeypatch.setattr(circsym, "build_meridian", lambda config: MeridianBuild())
    monkeypatch.setattr(circsym, "circsym_rejection_reasons", lambda config: [])
    monkeypatch.setattr(circsym, "MeridianMesh", object)
    monkeypatch.setattr(circsym, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(circsym, "native_config", lambda **kwargs: _Config(**kwargs))
    monkeypatch.setattr(
        circsym, "solve_circsym", lambda meridian, config: pytest.fail("grid path must not run")
    )
    monkeypatch.setattr(circsym, "solve_circsym_frequencies", solve_circsym_frequencies)
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "mock"})
    monkeypatch.setattr(circsym, "metal_status", lambda: {"available": True, "reason": "mock"})

    context = _explicit_context(frequencies)
    context.solver_mode = "circsym"
    response = circsym.solve_circsym_design(context)
    assert seen["freqs"] == list(frequencies)
    assert response["metadata"]["frequency_source"] == "explicit_list"
