"""Offline acquisition tests: reference truth is accessed only at requested rows."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.signal import find_peaks

from server.jobs.models import SolveOptions
from server.jobs.result_contracts import ParametricResultEnvelope
from server.jobs.runtime import merge_provisional_results
from server.solver.adaptive_rational import SweepModel, aaa
from server.solver.adaptive_sweep import SweepPlanner, enabled, solve_native_adaptively
from server.solver.context import SolverContext
from server.solver.result_mapping import build_solver_response


def reference():
    with np.load(Path(__file__).parent / "fixtures" / "adaptive-ref-S.npz") as data:
        return data["frequencies_hz"], data["values"]


def test_reference_fit_and_batched_acquisition():
    f, truth = reference()
    planner = SweepPlanner(f, delays_s=np.r_[np.full(37, 2 / 343), 0])
    batches = []
    while len(planner.pending):
        ids = planner.pending.copy()
        batches.append(ids)
        planner.add(ids, truth[ids])
    assert batches[0].size == 8
    assert batches[0][0] == 0 and batches[0][-1] == len(f) - 1
    assert all(0 < len(ids) <= 4 for ids in batches[1:])
    assert sum(map(len, batches)) == len(planner.observed) <= 32
    assert planner.stop_reason == "estimated_convergence"
    ids = np.array(sorted(planner.observed))
    np.testing.assert_array_equal(planner.prediction[ids], truth[ids])
    errors = abs(20 * np.log10(abs(planner.prediction[:, :-1]) / abs(truth[:, :-1])))
    mask = abs(truth[:, :-1]) >= abs(truth[:, :-1]).max(axis=0) * 10 ** (-30 / 20)
    assert errors[mask].max() < 0.1
    for a, b in [
        (abs(truth[:, 0]), abs(planner.prediction[:, 0])),
        (abs(truth[:, -1]), abs(planner.prediction[:, -1])),
        (truth[:, -1].real, planner.prediction[:, -1].real),
    ]:
        for sign in [-1, 1]:
            np.testing.assert_array_equal(find_peaks(sign * a)[0], find_peaks(sign * b)[0])
    assert planner.status[0] == planner.status[-1] == "solved"
    assert "interpolated" in planner.status


def test_real_axis_and_wrong_half_plane_poles_are_rejected():
    f = np.linspace(200, 1000, 24)
    for pole in [600 + 0j, 600 + 20j]:
        values = (1 / (f - pole))[:, None]
        # Move the exact real pole off sample points.
        model = SweepModel(f, values, (200, 1000), 5, 0)
        assert not model.safe()
    values = (1 / (f - (600 - 20j)))[:, None]
    assert SweepModel(f, values, (200, 1000), 5, 0).safe()
    # The same guard and interpolation work after conjugating time convention.
    model = SweepModel(f, values.conj(), (200, 1000), 5, 0, time_sign=1)
    assert model.safe()
    np.testing.assert_allclose(model(f), values.conj(), rtol=1e-10)


def test_unphysical_data_falls_back_to_full_sweep():
    f = np.geomspace(200, 1000, 24)
    truth = (1 / (f - (600 + 20j)))[:, None]
    planner = SweepPlanner(f, delays_s=0)
    while len(planner.pending):
        planner.add(planner.pending, truth[planner.pending])
    assert len(planner.observed) == len(f)
    assert planner.status == ["solved"] * len(f)
    np.testing.assert_array_equal(planner.prediction, truth)


def test_shared_weights_and_froissart_cleanup_keep_samples():
    x = np.linspace(-1, 1, 32)
    y = np.column_stack([1 / (x - 2j), 3 / (x - 2j), np.zeros(len(x))])
    model = aaa(x, y, max_support=12)
    np.testing.assert_allclose(model(x), y, atol=1e-12)
    assert len(model.support) < 12
    assert model.values.shape[1] == 3


@pytest.mark.parametrize("f", [[0, 1], [1, 1], [2, 1], [1, np.nan]])
def test_bad_grid_is_refused(f):
    with pytest.raises(ValueError):
        SweepPlanner(f, delays_s=0)


def test_preference_off_small_sweeps_and_other_formulations():
    assert not SolveOptions().adaptive_frequency_sampling
    context = SolverContext(None, (200, 1000), 24)
    assert not enabled(context)
    context.adaptive_frequency_sampling = True
    assert enabled(context)
    context.num_frequencies = 23
    assert not enabled(context)
    context.num_frequencies = 24
    context.sim_type = 1
    assert not enabled(context)


def test_native_batches_reconstruct_auxiliary_fields_and_stream_full_grid():
    f, truth = reference()
    context = SolverContext(
        None, (f[0], f[-1]), len(f), frequencies_hz=tuple(f), adaptive_frequency_sampling=True
    )
    batches = []
    snapshots = []

    def solve(batch):
        ids = np.searchsorted(f, batch)
        batches.append(ids)
        pressure = truth[ids, :-1, None].transpose(0, 2, 1)
        return SimpleNamespace(
            frequencies_hz=np.asarray(batch),
            pressure_complex=pressure,
            directivity_db=np.zeros_like(pressure.real),
            impedance=truth[ids, -1],
            observation_angles_deg=np.arange(37) * 5,
            observation_planes=["horizontal"],
            surface_pressure_complex=truth[ids, -1, None],
            surface_neumann_complex=truth[ids, -1, None] * 2,
            surface_pressure_avg={2: truth[ids, -1]},
            timings={"solve": len(ids)},
            solver_log=[{"count": len(ids)}],
        )

    result = solve_native_adaptively(
        context, solve, distance_m=2, sound_speed=343, publish=snapshots.append
    )
    assert len(batches) > 1 and snapshots
    np.testing.assert_array_equal(result.frequencies_hz, f)
    assert result.surface_pressure_complex.shape == (len(f), 1)
    np.testing.assert_allclose(result.surface_pressure_avg[2], result.impedance)
    assert result.timings["solve"] == sum(map(len, batches))
    assert all(len(s.frequency_status) == len(f) for s in snapshots)
    config = SimpleNamespace(observation=SimpleNamespace(distance_m=2, origin="mouth"))
    payload = build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=0,
        metadata={},
        sound_speed_m_per_s=343,
    )
    assert payload["frequency_status"] == result.frequency_status
    assert payload["metadata"]["adaptive_sampling"]["solved_count"] == sum(map(len, batches))


def test_snapshot_merge_replaces_rows_and_keeps_other_channels():
    current = {"channels": {"a": {"frequencies": [1, 2], "spl_on_axis": {"spl": [0, 1]}}}}
    delta = {
        "channels": {
            "b": {
                "frequencies": [1, 2],
                "frequency_status": ["solved", "interpolated"],
                "spl_on_axis": {"spl": [5, 6]},
            }
        }
    }
    current = merge_provisional_results(current, delta)
    delta["channels"]["b"]["spl_on_axis"]["spl"] = [7, 8]
    result = merge_provisional_results(current, delta)
    assert result["channels"]["a"]["frequencies"] == [1, 2]
    assert result["channels"]["b"]["frequencies"] == [1, 2]
    assert result["channels"]["b"]["spl_on_axis"]["spl"] == [7, 8]


def test_result_schema_flags_are_optional_and_aligned():
    schema = ParametricResultEnvelope.model_json_schema()
    assert "frequency_status" in schema["properties"]
    digest = "a" * 64
    provenance = dict(
        schema_version=1,
        wg_version="test",
        dependency_shas={},
        request_identity="execution",
        resolved_engine="beat-metal",
    )
    for key in (
        "request_sha256",
        "geometry_sha256",
        "solve_options_sha256",
        "execution_request_sha256",
        "execution_geometry_sha256",
        "execution_solve_options_sha256",
        "effective_request_sha256",
        "effective_geometry_sha256",
        "effective_solve_options_sha256",
    ):
        provenance[key] = digest
    payload = dict(
        result_kind="parametric",
        result_contract_version=1,
        frequencies=[1, 2],
        metadata={},
        client_request_id=None,
        client_metadata={},
        provenance=provenance,
    )
    assert ParametricResultEnvelope.model_validate(payload).frequency_status is None
    payload["frequency_status"] = ["solved", "interpolated"]
    assert (
        ParametricResultEnvelope.model_validate(payload).frequency_status
        == payload["frequency_status"]
    )
    for bad in [["solved"], ["solved", "unknown"]]:
        with pytest.raises(ValueError):
            ParametricResultEnvelope.model_validate({**payload, "frequency_status": bad})


def test_channel_bases_keep_flags_across_recombination():
    from server.solver.combine import (
        serialize_channel_bases,
        deserialize_channel_bases,
        combine_drive_channels,
    )

    f = np.geomspace(100, 1000, 24)
    bases = {}
    for name, flags in [
        ("a", ["solved"] * 24),
        ("b", ["solved"] + ["interpolated"] * 22 + ["solved"]),
    ]:
        bases[name] = SimpleNamespace(
            frequencies_hz=f,
            observation_angles_deg=np.array([0.0, 5.0]),
            observation_planes=["horizontal"],
            pressure_complex=np.ones((24, 1, 2), complex),
            frequency_status=flags,
        )
    restored = deserialize_channel_bases(serialize_channel_bases(bases))["results_by_id"]
    assert restored["b"].frequency_status == bases["b"].frequency_status
    combined, _ = combine_drive_channels(
        restored, members=["a", "b"], crossovers_hz=[500.0], level_match=False, align=False
    )
    assert combined.frequency_status == bases["b"].frequency_status


def test_many_trace_channels_use_the_same_shared_fit():
    x = np.linspace(-1, 1, 24)
    y = (1 / (x - 2j))[:, None] * np.linspace(1, 3, 300)[None, :]
    fit = aaa(x, y, max_support=10)
    np.testing.assert_allclose(fit(x), y, atol=1e-11)


def test_nonuniform_grid_still_starts_with_eight_distinct_requested_points():
    f = np.r_[np.linspace(200, 201, 23), 20000.0]
    planner = SweepPlanner(f, delays_s=0, batch_size=1)
    assert len(planner.pending) == 8
    assert planner.pending[0] == 0 and planner.pending[-1] == len(f) - 1
    ids = planner.pending.copy()
    planner.add(ids, (1 / (f[ids] - 400j))[:, None])
    assert len(planner.pending) == 1
    assert planner.pending[0] not in ids


def test_svd_failure_falls_back_to_exact_full_sweep(monkeypatch):
    f = np.geomspace(200, 1000, 24)
    truth = (1 / (f - (600 - 20j)))[:, None]

    def fail(*args, **kwargs):
        raise np.linalg.LinAlgError("injected SVD failure")

    monkeypatch.setattr(np.linalg, "svd", fail)
    planner = SweepPlanner(f, delays_s=0)
    while len(planner.pending):
        planner.add(planner.pending, truth[planner.pending])
    assert planner.stop_reason == "full_sweep"
    assert planner.estimate_db == 0
    assert planner.status == ["solved"] * len(f)
    np.testing.assert_array_equal(planner.prediction, truth)


@pytest.mark.parametrize("failure", ["nan", "batch", "cancel"])
def test_native_invalid_rows_batch_failure_and_cancellation_propagate(failure):
    context = SolverContext(None, (200, 1000), 24, adaptive_frequency_sampling=True)
    batches = []

    def solve(batch):
        batches.append(batch)
        if failure == "batch" and len(batches) == 2:
            raise RuntimeError("batch failed")
        f = np.asarray(batch)
        pressure = (np.exp(2j * np.pi * f * 2 / 343) / (f - 600j))[:, None, None]
        if failure == "nan" and len(batches) == 2:
            pressure[:] = np.nan
        return SimpleNamespace(frequencies_hz=f, pressure_complex=pressure)

    def cancel():
        if failure == "cancel" and batches:
            raise RuntimeError("cancelled")

    with pytest.raises(ValueError if failure == "nan" else RuntimeError,
                       match={"nan": "invalid rows", "batch": "batch failed", "cancel": "cancelled"}[failure]):
        solve_native_adaptively(context, solve, distance_m=2, sound_speed=343, cancel=cancel)
    assert len(batches) == (1 if failure == "cancel" else 2)


@pytest.mark.parametrize("f", [np.geomspace(100, 20000, 193), np.linspace(1000, 20000, 401)])
def test_density_floor_blocks_early_agreement(f):
    from server.solver.adaptive_sweep import MAX_SOLVED_GAP_OCTAVES

    planner = SweepPlanner(f, delays_s=0)
    while len(planner.pending):
        ids = planner.pending.copy()
        # Exactly smooth data still must meet the independent geometric floor.
        planner.add(ids, np.ones((len(ids), 2), dtype=complex))
    solved = f[sorted(planner.observed)]
    assert np.max(np.diff(np.log2(solved))) <= MAX_SOLVED_GAP_OCTAVES + 1e-12
    assert planner.stop_reason == "estimated_convergence"
    assert len(solved) > 16


@pytest.mark.parametrize("f", [np.geomspace(100, 20000, 24), np.linspace(100, 20000, 401)])
def test_sparse_requests_add_native_density_queries_and_keep_requested_output(f):
    from server.solver.adaptive_sweep import MAX_SOLVED_GAP_OCTAVES

    batches = []
    context = SolverContext(None, (f[0], f[-1]), len(f), frequencies_hz=tuple(f),
                            adaptive_frequency_sampling=True)

    def solve(batch):
        batches.extend(batch)
        return SimpleNamespace(frequencies_hz=np.asarray(batch),
            pressure_complex=np.ones((len(batch), 1, 1), dtype=complex),
            impedance=np.ones(len(batch), dtype=complex), timings={}, solver_log=[])

    result = solve_native_adaptively(context, solve, distance_m=0, sound_speed=343)
    np.testing.assert_array_equal(result.frequencies_hz, f)
    assert len(result.frequency_status) == len(f)
    assert set(batches) - set(f)  # Native queries fill gaps in a sparse request.
    assert np.max(np.diff(np.log2(sorted(batches)))) <= MAX_SOLVED_GAP_OCTAVES + 1e-12
    assert result.adaptive_sampling["native_solved_count"] == len(batches)
    assert result.adaptive_sampling["solved_count"] == result.frequency_status.count("solved")


def test_cancelled_short_batch_keeps_rows_from_previous_acquisitions():
    from types import SimpleNamespace

    from server.solver.adaptive_sweep import solve_native_adaptively

    context = SimpleNamespace(frequencies_hz=None, frequency_range=(500., 600.),
                              num_frequencies=48, frequency_spacing='log')
    acquired = []

    def batch(frequencies):
        cancelled = bool(acquired)
        f = np.asarray(frequencies[:1] if cancelled else frequencies)
        acquired.extend(f.tolist())
        return SimpleNamespace(
            frequencies_hz=f, pressure_complex=np.ones((len(f), 1, 1), complex),
            impedance=f.astype(complex), cancelled=cancelled,
        )

    result = solve_native_adaptively(context, batch, distance_m=2, sound_speed=343)
    assert result.cancelled
    assert result.frequencies_hz.tolist() == sorted(acquired)
    assert result.impedance.tolist() == [complex(f) for f in sorted(acquired)]
    assert len(result.pressure_complex) == len(result.directivity_db) == len(acquired)


@pytest.mark.parametrize("direction", [-1, 1])
def test_geometric_selection_ignores_host_log_roundoff(direction):
    from server.solver.adaptive_sweep import _largest_gaps, _nearest_log_point

    f = np.geomspace(100, 10000, 65)
    shifted = f.copy()
    for _ in range(3):
        shifted = np.nextafter(shifted, np.inf if direction > 0 else -np.inf)
    np.testing.assert_array_equal(SweepPlanner(f, delays_s=0).pending,
                                  SweepPlanner(shifted, delays_s=0).pending)
    gaps = np.ones(4)
    gaps[[1, 3]] = np.nextafter(1., np.inf)
    np.testing.assert_array_equal(_largest_gaps(gaps), np.arange(4))
    for grid in [f, shifted]:
        # An exact geometric midpoint between neighboring grid points.
        target = (np.log2(grid[20]) + np.log2(grid[21])) / 2
        assert _nearest_log_point(grid, np.array([20, 21]), target) == 20
        assert _nearest_log_point(grid, np.array([21, 20]), target) == 20


def test_gap_ties_use_the_group_maximum_and_preserve_larger_differences():
    from server.solver.adaptive_sweep import _largest_gaps

    # Nearby neighbors must not chain into a tie wider than the anchor guard.
    np.testing.assert_array_equal(_largest_gaps([1., 1. + .75e-12, 1. + 1.5e-12]), [1, 2, 0])
    np.testing.assert_array_equal(_largest_gaps([1., 1. + 1e-10, 1.]), [1, 0, 2])


@pytest.mark.parametrize("values, expected", [
    ([np.nan, 1., 2., np.inf, -np.inf], [2, 1, 0, 3, 4]),
    ([np.nan, 1., 1. + .75e-12, np.inf, -np.inf], [1, 2, 0, 3, 4]),
    ([np.inf, np.nan, -np.inf], [0, 1, 2]),
    ([], []),
])
def test_score_order_keeps_nonfinite_values_last_outside_finite_ties(values, expected):
    from server.solver.adaptive_sweep import _largest_scores

    np.testing.assert_array_equal(_largest_scores(values), expected)


@pytest.mark.parametrize("seed", [0, 17, 91])
@pytest.mark.parametrize("bounds", [(20, 20000), (100, 1000), (100, 10000), (200, 2000), (100, 101)])
def test_geometric_gap_order_and_samples_ignore_random_log2_ulps(monkeypatch, seed, bounds):
    from server.solver import adaptive_sweep as sweep

    log2 = np.log2
    rng = np.random.default_rng(seed)

    def noisy_log2(values):
        result = log2(values)
        steps = rng.integers(-2, 3, size=np.shape(result))
        for step in range(2):
            result = np.where(
                abs(steps) > step,
                np.nextafter(result, np.where(steps < 0, -np.inf, np.inf)),
                result,
            )
        return result

    class UnavailableModel:
        def __init__(self, *args, **kwargs):
            # Isolate acquisition ordering from numerical fitting; the normal
            # fallback still exercises coverage, midpoints and disagreement ties.
            raise ValueError("no fit for this ordering regression")

    monkeypatch.setattr(sweep, "SweepModel", UnavailableModel)

    def sampled_batches(f):
        planner = sweep.SweepPlanner(f, delays_s=0)
        batches = []
        for _ in range(3):
            ids = planner.pending.copy()
            batches.append(ids)
            if not len(ids):
                break
            planner.add(ids, np.ones((len(ids), 1), complex))
        return batches

    # Includes the reported 36/76/151/165/211/327-point boundary failures,
    # plus every intervening grid size rather than one lucky geometric step.
    for n in range(8, 401):
        f = np.geomspace(*bounds, n)
        expected_order = sweep._largest_gaps(np.diff(log2(f)))
        np.testing.assert_array_equal(expected_order, np.arange(n - 1))
        expected_batches = sampled_batches(f)
        with monkeypatch.context() as patch:
            patch.setattr(sweep.np, "log2", noisy_log2)
            np.testing.assert_array_equal(sweep._largest_gaps(np.diff(np.log2(f))), expected_order)
            actual_batches = sampled_batches(f)
        assert len(actual_batches) == len(expected_batches)
        for actual, expected in zip(actual_batches, expected_batches, strict=True):
            np.testing.assert_array_equal(actual, expected, err_msg=f"{bounds=}, {n=}, {seed=}")


@pytest.mark.parametrize("direction", [-1, 1])
def test_difference_peak_selection_prefers_lower_index_at_db_resolution(direction):
    from server.solver.adaptive_sweep import _largest_disagreements, difference_db

    a = np.ones((8, 2), dtype=complex)
    b = a.copy()
    b[[1, 3, 5]] += .01
    baseline = difference_db(a, b)
    perturbed = b.copy()
    for _ in range(3):
        perturbed[3] = np.nextafter(perturbed[3].real, np.inf if direction > 0 else -np.inf)
    scores = difference_db(a, perturbed)
    np.testing.assert_array_equal(_largest_disagreements(baseline), _largest_disagreements(scores))
    np.testing.assert_array_equal(_largest_disagreements(scores)[:3], [1, 3, 5])
    # Differences larger than the stated resolution retain their priority.
    scores[5] += 1e-10
    assert _largest_disagreements(scores)[0] == 5


@pytest.mark.parametrize("direction", [-1, 1])
def test_planner_disagreement_queries_ignore_peak_ulp_noise(monkeypatch, direction):
    from server.solver import adaptive_sweep as sweep

    f = np.geomspace(100, 101, 16)  # Density met: exercise disagreement acquisition.
    a = np.ones((len(f), 1), complex)
    b = a.copy()
    b[[1, 3, 5]] += .01
    scores = sweep.difference_db(a, b)
    shifted = b.copy()
    for _ in range(3):
        shifted[3] = np.nextafter(shifted[3].real, np.inf if direction > 0 else -np.inf)
    noisy_scores = sweep.difference_db(a, shifted)

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, grid):
            return np.ones((len(grid), 1), complex)

        def safe(self):
            return True

    monkeypatch.setattr(sweep, "SweepModel", Model)
    selections = []
    for estimate in (scores, noisy_scores):
        monkeypatch.setattr(sweep, "difference_db", lambda left, right:
                            estimate.copy() if len(left) == len(f) else np.zeros(len(left)))
        planner = SweepPlanner(f, delays_s=0, batch_size=2)
        planner.add(planner.pending, a[planner.pending])
        selections.append(planner.pending.copy())
    np.testing.assert_array_equal(selections[0], selections[1])
    assert 1 in selections[0] and 3 not in selections[0]
