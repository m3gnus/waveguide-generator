"""Frequency execution plans for useful live plots without changing results.

Boundary Lab solves the two endpoints first and then visits the interior in a
base-2 Van der Corput order.  That low-discrepancy sequence makes the shape of
the whole response appear early instead of painting it from left to right.
The durable result remains frequency-sorted; only native execution order is
changed while a caller is consuming streamed rows.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from .context import SolverContext


_FREQUENCY_SHAPED_RESULT_FIELDS = (
    "pressure_complex",
    "directivity_db",
    "impedance",
    "surface_pressure_complex",
    "sphere_pressure_complex",
)


def _set_frequency_shaped_field(result: Any, field: str, value: np.ndarray) -> None:
    """Assign a sorted native array, respecting solver compatibility aliases.

    ``hornlab_bempp_bem.SolveResult.directivity_db`` is a read-only alias for
    its stored ``spl_db`` field, while Metal stores ``directivity_db``
    directly.  Keep that difference at this adapter boundary rather than
    requiring either native package to imitate the other's dataclass layout.
    """

    descriptor = getattr(type(result), field, None)
    if (
        field == "directivity_db"
        and isinstance(descriptor, property)
        and descriptor.fset is None
        and hasattr(result, "spl_db")
    ):
        result.spl_db = value
        return
    setattr(result, field, value)


def canonical_frequencies(context: SolverContext) -> np.ndarray:
    """Return the ascending frequency axis described by ``context``."""

    if context.frequencies_hz is not None:
        return np.asarray(context.frequencies_hz, dtype=np.float64)
    start, end = context.frequency_range
    if context.frequency_spacing == "log":
        return np.geomspace(start, end, context.num_frequencies, dtype=np.float64)
    return np.linspace(start, end, context.num_frequencies, dtype=np.float64)


def order_frequencies_for_live_plotting(
    frequencies: Iterable[float], *, vdc_base: int = 2
) -> np.ndarray:
    """Return endpoints first, followed by low-discrepancy interior points."""

    values = np.asarray(list(frequencies), dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("frequencies must be one-dimensional")
    if values.size <= 2:
        return values.copy()
    sorted_values = np.unique(np.sort(values))
    if sorted_values.size <= 2:
        return sorted_values
    endpoint_indices = np.asarray([0, sorted_values.size - 1], dtype=np.int64)
    interior_indices = _van_der_corput_index_order(
        sorted_values.size - 2, base=vdc_base
    ) + 1
    return sorted_values[np.concatenate([endpoint_indices, interior_indices])]


def live_execution_frequencies(context: SolverContext) -> np.ndarray:
    """Return Boundary Lab's native solve order for a validated context."""

    return order_frequencies_for_live_plotting(canonical_frequencies(context))


def sort_native_result_frequencies(result: Any) -> Any:
    """Restore frequency-shaped native arrays to ascending contract order.

    Native result dataclasses are mutable in both solver packages.  Keeping the
    chronological solver logs untouched is intentional: diagnostics should say
    what ran when, while numerical arrays and exported results stay canonical.
    """

    frequencies = np.asarray(result.frequencies_hz, dtype=np.float64).reshape(-1)
    order = np.argsort(frequencies, kind="stable")
    if frequencies.size < 2 or np.array_equal(order, np.arange(frequencies.size)):
        return result
    result.frequencies_hz = frequencies[order]
    for field in _FREQUENCY_SHAPED_RESULT_FIELDS:
        value = getattr(result, field, None)
        if value is None:
            continue
        array = np.asarray(value)
        if array.ndim >= 1 and array.shape[0] == frequencies.size:
            _set_frequency_shaped_field(result, field, array[order])
    surface_pressure = getattr(result, "surface_pressure_avg", None)
    if isinstance(surface_pressure, dict):
        result.surface_pressure_avg = {
            tag: (
                np.asarray(values)[order]
                if np.asarray(values).ndim >= 1
                and np.asarray(values).shape[0] == frequencies.size
                else values
            )
            for tag, values in surface_pressure.items()
        }
    return result


def _van_der_corput_index_order(count: int, *, base: int = 2) -> np.ndarray:
    if count <= 0:
        return np.asarray([], dtype=np.int64)
    if base < 2:
        raise ValueError("Van der Corput base must be >= 2")
    sequence = np.asarray(
        [_van_der_corput(index, base=base) for index in range(1, count + 1)]
    )
    return np.argsort(sequence, kind="stable").astype(np.int64, copy=False)


def _van_der_corput(index: int, *, base: int = 2) -> float:
    value = 0.0
    denominator = float(base)
    while index:
        index, remainder = divmod(index, base)
        value += remainder / denominator
        denominator *= base
    return value


__all__ = [
    "canonical_frequencies",
    "live_execution_frequencies",
    "order_frequencies_for_live_plotting",
    "sort_native_result_frequencies",
]
