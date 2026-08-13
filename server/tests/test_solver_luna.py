"""Regression coverage for accepted Luna native-adapter/result findings."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.jobs.models import SolveRequest
from server.solver import bempp, circsym, metal
from server.solver.acoustics import solver_sound_speed_m_per_s
from server.solver.beam_shape import beam_shape_summary
from server.solver.context import SolverContext
from server.solver.directivity_index import calculate_di_from_spherical_grid
from server.solver.result_mapping import (
    REFERENCE_RHO_C,
    build_solver_response,
    directivity,
    spl_on_axis,
)


SOUND_SPEED_M_PER_S = solver_sound_speed_m_per_s("hornlab_metal_bem")


def _context(*, solver_mode: str = "full_3d", sim_type: int = 2) -> SolverContext:
    return SolverContext(
        design=DesignConfig.model_validate({"formula": "OSSE"}),
        frequency_range=(100.0, 200.0),
        num_frequencies=2,
        solver_mode=solver_mode,
        sim_type=sim_type,
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        observation=SimpleNamespace(distance_m=2.0, origin="mouth", sphere_grid=None)
    )


def _native_result(frequencies: list[float] | None = None) -> SimpleNamespace:
    axis = np.asarray(frequencies or [100.0, 200.0])
    return SimpleNamespace(
        frequencies_hz=axis,
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        observation_planes=["horizontal"],
        pressure_complex=np.ones((len(axis), 1, 3), dtype=np.complex128) * 20e-6,
        directivity_db=np.zeros((len(axis), 1, 3)),
        impedance=np.ones(len(axis), dtype=np.complex128) * (1j * REFERENCE_RHO_C),
        solver_log=[],
        timings={},
        native_diagnostics=[],
    )


@pytest.mark.parametrize("metadata", [{}, {"apertureTag": 0}, {"apertureTag": -3}])
def test_circsym_infinite_baffle_requires_positive_aperture_tag(metadata: dict) -> None:
    with pytest.raises(ValueError, match="aperture tag"):
        circsym._validated_aperture_tag(metadata, 1)
    assert circsym._validated_aperture_tag({"apertureTag": 12}, 1) == 12


def test_bempp_engine_rejects_design_circsym_before_meshing() -> None:
    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"solver_mode": "circsym"}},
            "options": {"engine": "bempp"},
        }
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="cannot run solver_mode='circsym'"):
            await bempp.BemppEngine().run(
                request, cancel_cb=lambda: None, stage_cb=lambda *_args: None
            )

    asyncio.run(scenario())


def test_old_native_helpers_get_explicit_unsupported_option_errors(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        metal,
        "native_config",
        lambda **_kwargs: (_ for _ in ()).throw(TypeError("unexpected keyword 'formulation'")),
    )
    monkeypatch.setattr(metal, "native_solve", lambda *_args: None)
    with pytest.raises(metal.MetalUnavailable, match="required BEM formulation"):
        metal.solve_metal_from_msh_text("msh", _context())

    class Meridian:
        baffle_z = 0.0
        metadata: dict[str, Any] = {}

        @staticmethod
        def as_metal_meridian(_cls: Any) -> object:
            return object()

    monkeypatch.setattr(circsym, "build_meridian", lambda _config: Meridian())
    monkeypatch.setattr(circsym, "circsym_rejection_reasons", lambda _config: [])
    monkeypatch.setattr(circsym, "MeridianMesh", object)
    monkeypatch.setattr(circsym, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(circsym, "solve_circsym", lambda *_args: None)
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        circsym,
        "native_config",
        lambda **_kwargs: (_ for _ in ()).throw(TypeError("unexpected 'circsym_baffle_z'")),
    )
    with pytest.raises(circsym.CircSymUnavailable, match="baffle position"):
        circsym.solve_circsym_design(_context(solver_mode="circsym"))


def test_optional_native_imports_treat_oserror_as_unavailable() -> None:
    script = r'''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(("hornlab_metal_bem", "hornlab_bempp_bem", "hornlab_mesher")):
        raise OSError("binary loader failed")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from server.solver import metal, bempp, circsym
assert metal.native_config is None
assert bempp.SolveConfig is None
assert circsym.build_meridian is None
'''
    completed = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("adapter", ["metal", "bempp"])
def test_temp_path_exists_before_write_and_cleanup_never_masks_solver_error(
    adapter: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = metal if adapter == "metal" else bempp
    path = tmp_path / f"{adapter}.msh"

    class Handle:
        name = str(path)

        def __enter__(self) -> "Handle":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def write(self, _text: str) -> None:
            assert path.name == Path(self.name).name
            raise OSError("write failed")

    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", lambda **_kwargs: Handle())
    unlinked: list[Path] = []
    monkeypatch.setattr(Path, "unlink", lambda self, **_kwargs: unlinked.append(self))
    if adapter == "metal":
        monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
        monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(metal, "native_solve", lambda *_args: None)
        call = lambda: metal.solve_metal_from_msh_text("msh", _context())
    else:
        monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(bempp, "SolveConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(bempp, "bempp_solve", lambda *_args: None)
        monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": True, "reason": "ok"})
        call = lambda: bempp.solve_bempp_from_msh_text("msh", _context())
    with pytest.raises(OSError, match="write failed"):
        call()
    assert unlinked == [path]


@pytest.mark.parametrize("adapter", ["metal", "bempp"])
def test_temp_cleanup_oserror_does_not_mask_native_solver_error(
    adapter: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_path: list[str] = []

    def fail_solve(path: str, _config: Any) -> None:
        captured_path.append(path)
        raise RuntimeError("native solve failed")

    monkeypatch.setattr(
        Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    if adapter == "metal":
        monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
        monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(metal, "native_solve", fail_solve)
        call = lambda: metal.solve_metal_from_msh_text("msh", _context())
    else:
        monkeypatch.setattr(bempp, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(bempp, "SolveConfig", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(bempp, "bempp_solve", fail_solve)
        monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": True, "reason": "ok"})
        call = lambda: bempp.solve_bempp_from_msh_text("msh", _context())
    with pytest.raises(RuntimeError, match="native solve failed"):
        call()
    assert len(captured_path) == 1
    os.unlink(captured_path[0])


def test_solver_context_rejects_nonfinite_frequency_bounds() -> None:
    with pytest.raises(ValueError, match="finite"):
        SolverContext(
            design=DesignConfig.model_validate({"formula": "OSSE"}),
            frequency_range=(float("nan"), 200.0),
            num_frequencies=2,
        )
    context = _context()
    context.frequency_range = (100.0, float("inf"))
    with pytest.raises(ValueError, match="finite"):
        context.validate()


def test_result_arrays_align_to_frequency_axis_with_nulls_and_failures() -> None:
    result = _native_result([100.0, 200.0, 300.0])
    result.pressure_complex = result.pressure_complex[:2]
    result.directivity_db = np.zeros((4, 1, 3))
    result.impedance = result.impedance[:1]
    response = build_solver_response(
        result=result,
        config=_config(),
        context=SolverContext(
            design=DesignConfig.model_validate({"formula": "OSSE"}),
            frequency_range=(100.0, 300.0),
            num_frequencies=3,
        ),
        start_time=0.0,
        metadata={},
        sound_speed_m_per_s=SOUND_SPEED_M_PER_S,
    )
    assert len(response["spl_on_axis"]["spl"]) == 3
    assert response["spl_on_axis"]["spl"][-1] is None
    assert len(response["directivity"]["horizontal"]) == 3
    assert len(response["impedance"]["real"]) == 3
    assert response["impedance"]["real"][1:] == [None, None]
    assert response["metadata"]["failure_count"] == 3
    assert response["metadata"]["partial_success"] is True


@pytest.mark.parametrize("malformation", ["short_phi", "nan_phi", "unsorted_phi", "wrong_rows"])
def test_malformed_spherical_grid_is_not_published(malformation: str) -> None:
    result = _native_result()
    result.sphere_theta_deg = np.asarray([0, 0, 0, 90, 90, 90], dtype=float)
    result.sphere_phi_deg = np.asarray([0, 120, 240, 0, 120, 240], dtype=float)
    result.sphere_pressure_complex = np.ones((2, 6), dtype=np.complex128)
    if malformation == "short_phi":
        result.sphere_phi_deg = result.sphere_phi_deg[:-1]
    elif malformation == "nan_phi":
        result.sphere_phi_deg[1] = np.nan
    elif malformation == "unsorted_phi":
        result.sphere_phi_deg = np.asarray([0, 240, 120, 0, 240, 120], dtype=float)
    else:
        result.sphere_pressure_complex = np.ones((1, 6), dtype=np.complex128)
    context = _context()
    context.polar_config["spherical_sampling"] = True
    config = _config()
    config.observation.sphere_grid = (2, 3)
    response = build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=0.0,
        metadata={},
        sound_speed_m_per_s=SOUND_SPEED_M_PER_S,
    )
    assert "balloon" not in response
    assert response["metadata"]["balloon_sampling"]["status"] == "missing_result"


def test_one_invalid_spherical_cell_only_rejects_affected_beam_rays() -> None:
    theta = np.linspace(0.0, 80.0, 17)
    phi = np.arange(0.0, 360.0, 5.0)
    grid = -12.0 * (theta[:, None] / 40.0) ** 2 * np.ones((1, phi.size))
    grid[7, 10] = np.nan
    summary = beam_shape_summary(theta, phi, grid[None, :, :], [1000.0])
    assert summary is not None
    assert summary["valid"] == [True]


def test_nonfinite_angles_are_dropped_and_do_not_drive_on_axis_selection() -> None:
    result = _native_result()
    result.observation_angles_deg = np.asarray([np.nan, 10.0, 0.0])
    result.pressure_complex[:, 0, 0] = 1.0e100
    result.pressure_complex[:, 0, 2] = 20.0e-6
    assert spl_on_axis(result) == pytest.approx([0.0, 0.0])
    coordinates = directivity(result)["horizontal"][0]
    assert [point[0] for point in coordinates] == [10.0, 0.0]


def test_log_domain_spl_and_di_stay_finite_for_huge_finite_inputs() -> None:
    result = _native_result()
    result.pressure_complex[:] = complex(1.0e308, 0.0)
    assert all(np.isfinite(spl_on_axis(result)))
    di = calculate_di_from_spherical_grid(
        [0.0, 90.0, 180.0],
        [0.0, 120.0, 240.0],
        np.full((1, 3, 3), 1.0e308),
    )
    assert di[0] == pytest.approx(0.0)
    assert np.isfinite(di[0])


def test_solver_context_derives_its_summary_from_an_explicit_list() -> None:
    from server.jobs.models import SolveRequest

    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 200, "f2": 20000}},
            "options": {"frequencies_hz": [500.0, 812.3, 1000.0]},
        }
    )
    context = SolverContext.from_request(request, solver_mode="full_3d")
    assert context.frequencies_hz == (500.0, 812.3, 1000.0)
    # The design's own 200-20000 Hz range must not leak into the summary.
    assert context.frequency_range == (500.0, 1000.0)
    assert context.num_frequencies == 3


def test_solver_context_allows_a_single_explicit_point_but_not_a_collapsed_grid() -> None:
    single = SolverContext(
        design=DesignConfig.model_validate({"formula": "OSSE"}),
        frequency_range=(812.3, 812.3),
        num_frequencies=1,
        frequencies_hz=(812.3,),
    )
    assert single.num_frequencies == 1
    with pytest.raises(ValueError, match="increasing"):
        SolverContext(
            design=DesignConfig.model_validate({"formula": "OSSE"}),
            frequency_range=(812.3, 812.3),
            num_frequencies=1,
        )


def test_solver_context_rejects_a_summary_that_drifts_from_its_explicit_list() -> None:
    context = SolverContext(
        design=DesignConfig.model_validate({"formula": "OSSE"}),
        frequency_range=(500.0, 1000.0),
        num_frequencies=2,
        frequencies_hz=(500.0, 1000.0),
    )
    context.num_frequencies = 3
    with pytest.raises(ValueError, match="must match the explicit frequency list"):
        context.validate()
    context.num_frequencies = 2
    context.frequency_range = (500.0, 2000.0)
    with pytest.raises(ValueError, match="must match the explicit frequency list"):
        context.validate()
    context.frequency_range = (500.0, 1000.0)
    context.frequencies_hz = (1000.0, 500.0)
    with pytest.raises(ValueError, match="strictly ascending"):
        context.validate()
