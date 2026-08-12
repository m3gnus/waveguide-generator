from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from server.solver.context import SolverContext
from server.solver.frequency_sweep import (
    canonical_frequencies,
    live_execution_frequencies,
    order_frequencies_for_live_plotting,
    sort_native_result_frequencies,
)


def _context(*, spacing: str = "linear") -> SolverContext:
    return SolverContext(
        design=None,
        frequency_range=(100.0, 700.0),
        num_frequencies=7,
        frequency_spacing=spacing,
        polar_config={"enabled_axes": ["horizontal"]},
    )


def test_live_order_matches_boundary_lab_endpoint_then_vdc_scheduler() -> None:
    ordered = order_frequencies_for_live_plotting(range(1, 11))

    assert ordered.tolist() == [1.0, 10.0, 9.0, 5.0, 3.0, 7.0, 2.0, 6.0, 4.0, 8.0]
    assert sorted(ordered.tolist()) == [float(value) for value in range(1, 11)]


def test_context_grid_is_canonical_but_live_execution_is_progressive() -> None:
    context = _context()

    assert canonical_frequencies(context).tolist() == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0]
    assert live_execution_frequencies(context).tolist() == [100.0, 700.0, 500.0, 300.0, 200.0, 600.0, 400.0]


def test_native_result_is_restored_to_ascending_frequency_contract() -> None:
    result = SimpleNamespace(
        frequencies_hz=np.asarray([100.0, 700.0, 500.0, 300.0]),
        pressure_complex=np.asarray([[1], [7], [5], [3]]),
        directivity_db=np.asarray([[10], [70], [50], [30]]),
        impedance=np.asarray([1, 7, 5, 3]),
        sphere_pressure_complex=np.asarray([[101], [107], [105], [103]]),
        surface_pressure_avg={2: np.asarray([201, 207, 205, 203])},
        solver_log=[{"frequency_hz": 100.0}, {"frequency_hz": 700.0}],
    )

    returned = sort_native_result_frequencies(result)

    assert returned is result
    assert result.frequencies_hz.tolist() == [100.0, 300.0, 500.0, 700.0]
    assert result.pressure_complex[:, 0].tolist() == [1, 3, 5, 7]
    assert result.directivity_db[:, 0].tolist() == [10, 30, 50, 70]
    assert result.impedance.tolist() == [1, 3, 5, 7]
    assert result.sphere_pressure_complex[:, 0].tolist() == [101, 103, 105, 107]
    assert result.surface_pressure_avg[2].tolist() == [201, 203, 205, 207]
    assert result.solver_log == [{"frequency_hz": 100.0}, {"frequency_hz": 700.0}]
