"""Opt-in, truth-blind batched sweep planning for exterior BEAT results.

The tolerance is fit disagreement in dB equivalents, not an error certificate.
Coverage guards cannot detect every resonance narrower than the query spacing.
"""

from __future__ import annotations

from copy import copy
from typing import Any, Callable

import numpy as np

from .adaptive_rational import SweepModel
from .frequency_sweep import _set_frequency_shaped_field, canonical_frequencies


# A geometric coverage floor, independent of fit agreement. Kept here so the
# acquisition grid, planner and published metadata use the same bound.
MAX_SOLVED_GAP_OCTAVES = 1 / 6


def _nearest_log_point(frequencies, candidates, target):
    distances = abs(np.log(frequencies[candidates]) - target)
    # Geometric midpoints can be equidistant to rounding precision. Prefer the
    # lower frequency instead of letting the host's log implementation decide.
    return int(candidates[np.flatnonzero(distances <= distances.min() + 1e-12)[0]])


def _largest_gaps(gaps):
    # Equal log gaps on a geometric grid differ by a few ulps across hosts.
    # Match the coverage guard's precision, with lower-frequency gaps first.
    return np.argsort(-np.round(gaps, 12), kind="stable")


def _largest_disagreements(estimate):
    # The difference/peak path can produce dB scores separated by host ulps.
    # Scores within 1e-12 dB of a group's maximum prefer the lower index.
    # Use a distance guard as for midpoints: decimal rounding alone can split
    # an ulp-scale tie across a rounding boundary. Convergence is unrounded.
    order = np.argsort(-estimate, kind="stable")
    scores = np.asarray(estimate)[order]
    start = 0
    for stop in range(1, len(order) + 1):
        if stop == len(order) or scores[start] - scores[stop] > 1e-12:
            order[start:stop] = np.sort(order[start:stop])
            start = stop
    return order


def enabled(context) -> bool:
    return (
        context.adaptive_frequency_sampling
        and context.sim_type == 2
        and context.ground_plane is None
        and context.num_frequencies >= 24
    )


def difference_db(a, b):
    peak = np.maximum(np.max(np.abs(a), axis=0), np.max(np.abs(b), axis=0))
    denominator = np.maximum(np.maximum(np.abs(a), np.abs(b)), peak[None] * 10 ** (-30 / 20))
    relative = np.abs(a - b) / np.maximum(denominator, 1e-30)
    return np.nan_to_num(np.max(20 * np.log10(1 + relative), axis=1), nan=1e9, posinf=1e9)


class SweepPlanner:
    """Acquire rows on a density-ready grid, starting with eight log-spaced points.

    Four queries per refinement round: three disagreement maxima and a largest
    log-gap midpoint. Oversized gaps take priority over disagreement queries.
    Accept convergence only after two stable, guarded rounds and the density floor.
    All returned solved rows are restored exactly from the observations.
    """

    def __init__(self, frequencies, *, delays_s, tolerance_db=0.1, batch_size=4):
        self.frequencies = np.asarray(frequencies, dtype=float)
        f = self.frequencies
        if (
            f.ndim != 1
            or len(f) < 2
            or not np.all(np.isfinite(f))
            or f[0] <= 0
            or np.any(np.diff(f) <= 0)
        ):
            raise ValueError("adaptive frequencies must be positive and strictly ascending")
        if not np.isfinite(tolerance_db) or tolerance_db <= 0 or batch_size < 1:
            raise ValueError("adaptive tolerance and batch size must be positive")
        targets = np.geomspace(f[0], f[-1], min(8, len(f)))
        seeds = {0, len(f) - 1}
        for target in targets[1:-1]:
            available = np.asarray([i for i in range(1, len(f) - 1) if i not in seeds])
            seeds.add(_nearest_log_point(f, available, np.log(target)))
        self.pending = np.asarray(sorted(seeds), dtype=int)
        self.observed: dict[int, np.ndarray] = {}
        self.delays_s = delays_s
        self.tolerance_db = tolerance_db
        self.batch_size = batch_size
        self.previous = None
        self.stable = 0
        self.prediction = None
        self.safe = False
        self.estimate_db = None
        self.stop_reason = "full_sweep"

    def add(self, indices, values):
        values = np.asarray(values, dtype=complex)
        if values.ndim != 2 or values.shape[0] != len(indices) or not np.all(np.isfinite(values)):
            raise ValueError("adaptive solver returned invalid rows")
        for i, row in zip(indices, values, strict=True):
            self.observed[int(i)] = row.copy()
        ids = np.asarray(sorted(self.observed))
        data = np.stack([self.observed[int(i)] for i in ids])
        f = self.frequencies
        if len(ids) == len(f):
            self.prediction = data
            self.safe = True
            self.estimate_db = 0.0
            self.pending = np.array([], dtype=int)
            return
        degree = min(30, max(2, len(ids) - 2))
        try:

            def fit(keep, cap, physical=False):
                return SweepModel(
                    f[ids[keep]], data[keep], (f[0], f[-1]), cap, self.delays_s, physical=physical
                )

            keep = np.ones(len(ids), dtype=bool)
            model = fit(keep, degree, physical=True)
            prediction = model(f)
            self.safe = model.safe() and bool(np.all(np.isfinite(prediction)))
            estimates = [difference_db(prediction, fit(keep, max(1, degree - 1))(f))]
            if self.previous is not None:
                estimates.append(difference_db(prediction, self.previous))
            for drop in np.unique(np.round(np.linspace(1, len(ids) - 2, 3)).astype(int)):
                mask = keep.copy()
                mask[drop] = False
                estimates.append(difference_db(prediction, fit(mask, degree)(f)))
            sampled_error = float(np.max(difference_db(prediction[ids], data)))
            estimates.append(np.full(len(f), sampled_error))
            estimate = np.max(estimates, axis=0)
            estimate[ids] = 0
            self.estimate_db = float(np.max(estimate))
            self.stable = (
                self.stable + 1
                if self.safe and self.estimate_db < self.tolerance_db and self.previous is not None
                else 0
            )
            self.prediction = prediction
            self.prediction[ids] = data
            self.previous = prediction.copy()
        except (np.linalg.LinAlgError, FloatingPointError, ValueError):
            self.safe = False
            self.stable = 0
            estimate = np.ones(len(f))
        gaps_octaves = np.diff(np.log2(f[ids]))
        density_met = bool(np.all(gaps_octaves <= MAX_SOLVED_GAP_OCTAVES + 1e-12))
        if self.stable >= 2 and density_met:
            self.stop_reason = "estimated_convergence"
            self.pending = np.array([], dtype=int)
            return
        remaining = set(range(len(f))) - set(ids)
        selected = []
        # Coverage is based on log-frequency distances, including explicit and
        # linear grids. It never queries a frequency outside the acquisition grid.
        gaps = np.diff(np.log(f[ids]))
        # Fill every oversized refinable gap before tolerance-driven queries.
        # Include pending queries when splitting so a batch can fill one large
        # gap several times, without wasting queries on smaller gaps.
        coverage_ids = list(ids)
        while len(selected) < min(self.batch_size, len(remaining)):
            coverage_ids.sort()
            oversized = _largest_gaps(np.diff(np.log2(f[coverage_ids])))
            candidate = None
            for gap in oversized:
                left, right = coverage_ids[gap : gap + 2]
                if np.log2(f[right] / f[left]) <= MAX_SOLVED_GAP_OCTAVES + 1e-12:
                    break
                candidates = np.arange(left + 1, right)
                if len(candidates):
                    target = (np.log(f[left]) + np.log(f[right])) / 2
                    candidate = _nearest_log_point(f, candidates, target)
                    break
            if candidate is None:
                break
            selected.append(candidate)
            coverage_ids.append(candidate)
        for gap in _largest_gaps(gaps):
            candidates = np.arange(ids[gap] + 1, ids[gap + 1])
            if len(candidates) and not selected:
                target = (np.log(f[ids[gap]]) + np.log(f[ids[gap + 1]])) / 2
                selected.append(_nearest_log_point(f, candidates, target))
                break
        for i in _largest_disagreements(estimate):
            if len(selected) >= min(self.batch_size, len(remaining)):
                break
            if int(i) in remaining and int(i) not in selected:
                selected.append(int(i))
            if len(selected) >= min(self.batch_size, len(remaining)):
                break
        self.pending = np.asarray(sorted(selected), dtype=int)

    @property
    def status(self):
        return [
            "solved" if i in self.observed else "interpolated" for i in range(len(self.frequencies))
        ]


# Every complex native quantity used by downstream response/driver/field
# consumers participates in the same fit, including retained surface traces.
_COMPLEX_FIELDS = (
    "pressure_complex",
    "impedance",
    "sphere_pressure_complex",
    "surface_pressure_complex",
    "surface_neumann_complex",
)


def _cancelled_batches(batches: list[Any]) -> Any:
    """Keep every acquired row, including batches completed before cancellation."""
    current = copy(batches[-1])
    frequencies = np.concatenate([np.asarray(batch.frequencies_hz) for batch in batches])
    order = np.argsort(frequencies, kind="stable")
    current.frequencies_hz = frequencies[order]
    for name in _COMPLEX_FIELDS:
        if getattr(current, name, None) is not None:
            values = np.concatenate([np.asarray(getattr(batch, name)) for batch in batches])
            setattr(current, name, values[order])
    if isinstance(getattr(current, "surface_pressure_avg", None), dict):
        current.surface_pressure_avg = {
            tag: np.concatenate([batch.surface_pressure_avg[tag] for batch in batches])[order]
            for tag in current.surface_pressure_avg
        }
    with np.errstate(divide="ignore"):
        spl = 20 * np.log10(np.abs(current.pressure_complex) / 20e-6)
    _set_frequency_shaped_field(current, "directivity_db", spl)
    return current


def native_acquisition_frequencies(context) -> np.ndarray:
    """Requested rows plus geometric coverage queries; also the progress budget."""
    requested = canonical_frequencies(context)
    coverage = []
    for left, right in zip(requested[:-1], requested[1:], strict=True):
        intervals = int(np.ceil(np.log2(right / left) / MAX_SOLVED_GAP_OCTAVES - 1e-12))
        if intervals > 1:
            coverage.extend(np.geomspace(left, right, intervals + 1)[1:-1])
    return np.unique(np.r_[requested, coverage])


def solve_native_adaptively(
    context,
    solve_batch: Callable,
    *,
    distance_m: float,
    sound_speed: float,
    publish: Callable | None = None,
    cancel: Callable | None = None,
) -> Any:
    """Reuse the native batch solver; publish complete replacement snapshots."""
    requested = canonical_frequencies(context)
    # Sparse explicit/linear requests can themselves exceed the density floor.
    # Add native coverage queries, then publish only the original requested grid.
    f = native_acquisition_frequencies(context)
    requested_ids = np.searchsorted(f, requested)
    planner = None
    template = None
    layout = []
    logs = []
    timings = {}
    batches = []
    while planner is None or len(planner.pending):
        if cancel:
            cancel()
        ids = SweepPlanner(f, delays_s=0).pending if planner is None else planner.pending
        result = solve_batch(f[ids].tolist())
        batches.append(result)
        if getattr(result, "cancelled", False):
            # Shortened batches carry real rows, including earlier acquisitions.
            # Do not fit or call cancel again before packaging partial results.
            return _cancelled_batches(batches)
        if not np.array_equal(np.asarray(result.frequencies_hz), f[ids]):
            raise ValueError("adaptive batch returned a different frequency grid")
        if template is None:
            template = result
            for name in _COMPLEX_FIELDS:
                value = getattr(result, name, None)
                if value is not None:
                    shape = np.asarray(value).shape[1:]
                    width = int(np.prod(shape)) if shape else 1
                    delay = (
                        distance_m / sound_speed
                        if name in {"pressure_complex", "sphere_pressure_complex"}
                        else 0
                    )
                    layout.append((name, shape, width, delay))
            averages = getattr(result, "surface_pressure_avg", None)
            if isinstance(averages, dict):
                for tag in averages:
                    shape = np.asarray(averages[tag]).shape[1:]
                    layout.append(((tag,), shape, int(np.prod(shape)) if shape else 1, 0))
            delays = np.concatenate([np.full(width, delay) for _, _, width, delay in layout])
            planner = SweepPlanner(f, delays_s=delays)
        data = np.concatenate(
            [
                np.asarray(
                    getattr(result, name)
                    if isinstance(name, str)
                    else result.surface_pressure_avg[name[0]]
                ).reshape(len(ids), width)
                for name, _, width, _ in layout
            ],
            axis=1,
        )
        planner.add(ids, data)
        logs.extend(getattr(result, "solver_log", []) or [])
        for key, value in (getattr(result, "timings", {}) or {}).items():
            if isinstance(value, (int, float)):
                timings[key] = timings.get(key, 0) + value
        if planner.safe:
            current = copy(template)
            current.frequencies_hz = requested.copy()
            offset = 0
            if isinstance(getattr(template, "surface_pressure_avg", None), dict):
                current.surface_pressure_avg = {}
            for name, shape, width, _ in layout:
                values = planner.prediction[requested_ids, offset : offset + width].reshape(
                    (len(requested), *shape)
                )
                if isinstance(name, str):
                    setattr(current, name, values)
                else:
                    current.surface_pressure_avg[name[0]] = values
                offset += width
            with np.errstate(divide="ignore"):
                spl = 20 * np.log10(np.abs(current.pressure_complex) / 20e-6)
            _set_frequency_shaped_field(current, "directivity_db", spl)
            current.solver_log = list(logs)
            current.timings = dict(timings)
            # Native classes can use slots; adapter-owned additions live in a
            # proxy rather than changing their public dataclass contract.
            from types import SimpleNamespace

            fields = {
                name: getattr(current, name)
                for name in dir(current)
                if not name.startswith("_") and not callable(getattr(current, name))
            }
            fields["frequency_status"] = [planner.status[i] for i in requested_ids]
            fields["adaptive_sampling"] = dict(
                solved_count=sum(i in planner.observed for i in requested_ids),
                requested_count=len(requested),
                native_solved_count=len(planner.observed),
                solved_frequencies_hz=f[sorted(planner.observed)].tolist(),
                max_gap_octaves=float(np.max(np.diff(np.log2(f[sorted(planner.observed)])))),
                max_allowed_gap_octaves=MAX_SOLVED_GAP_OCTAVES,
                tolerance_db=planner.tolerance_db,
                estimate_db=planner.estimate_db,
                stop_reason=planner.stop_reason,
            )
            current = SimpleNamespace(**fields)
            if publish:
                publish(current)
    return current
