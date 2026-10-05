"""Fault-injected streams cover order, completeness and deterministic close."""

from __future__ import annotations

import numpy as np
import pytest

from server.solver.beat_adapter.results import ResultContractError
from .conftest import EventStream, wire


def row(raw_result, frequency, **kwargs):
    return {"type": "result", "result": raw_result(frequency, **kwargs)}


def test_unordered_execution_axis_and_trace_order_are_preserved(raw_result, run_sweep):
    stream = EventStream([{"type": "status"}, row(raw_result, 1000), {"type": "progress"},
                          row(raw_result, 500), {"type": "completed", "solved_count": 2}])
    result = run_sweep(stream, [1000, 500])
    np.testing.assert_array_equal(result.frequencies_hz, [1000, 500])
    assert result.pressure_complex.shape == (2, 3, 3)
    assert result.sphere_pressure_complex.shape == (2, 12)
    assert result.surface_pressure_complex.shape == (2, 4)
    assert result.surface_neumann_complex.shape == (2, 2)
    assert result.requested_frequency_count == 2
    assert not result.cancelled and not result.is_partial
    assert [entry["frequency_hz"] for entry in result.solver_log] == [1000, 500]
    assert stream.closed == 1


def test_float32_echo_keeps_exact_requested_float64_axis(raw_result, run_sweep):
    frequency = 1234.56789123
    # Julia JSON prints Float32's short decimal representation.
    echoed = float(str(np.float32(frequency)))
    stream = EventStream([row(raw_result, echoed, precision="float32"),
                          {"type": "completed", "solved_count": 1}])
    result = run_sweep(stream, [frequency], precision="float32")
    assert result.frequencies_hz.tolist() == [frequency]
    assert stream.closed == 1


@pytest.mark.parametrize("solved", [0, 1, 2])
def test_cancelled_prefix_is_marked_and_has_consistent_empty_shapes(raw_result, run_sweep, solved):
    stream = EventStream([row(raw_result, f) for f in [500, 1000][:solved]]
                         + [{"type": "cancelled", "solved_count": solved}])
    result = run_sweep(stream)
    assert result.cancelled
    assert result.is_partial is (solved < 2)  # HBB's after-last-result edge case
    assert result.requested_frequency_count == 2
    assert result.frequencies_hz.tolist() == [500, 1000][:solved]
    assert result.pressure_complex.shape == (solved, 3, 3)
    assert result.directivity_db.shape == (solved, 3, 3)
    assert result.sphere_pressure_complex.shape == (solved, 12)
    assert result.surface_pressure_complex.shape == (solved, 4)
    assert result.surface_neumann_complex.shape == (solved, 2)
    assert stream.closed == 1


@pytest.mark.parametrize("case", ["empty", "eof", "empty_completed", "short_completed", "extra",
                                  "out_of_order", "duplicate", "wrong_count", "bool_count",
                                  "missing_count", "cancelled_count", "after_terminal", "failed", "unknown"])
def test_sweep_contract_violations_close_stream(raw_result, run_sweep, case):
    first, second = row(raw_result, 500), row(raw_result, 1000)
    terminal = {"type": "completed", "solved_count": 2}
    cases = {
        "empty": [], "eof": [first], "empty_completed": [{"type": "completed", "solved_count": 0}],
        "short_completed": [first, {"type": "completed", "solved_count": 1}],
        "extra": [first, second, row(raw_result, 2000), terminal],
        "out_of_order": [second, first, terminal], "duplicate": [first, first, terminal],
        "wrong_count": [first, second, {"type": "completed", "solved_count": 3}],
        "bool_count": [first, {"type": "cancelled", "solved_count": True}],
        "missing_count": [first, second, {"type": "completed"}],
        "cancelled_count": [first, {"type": "cancelled", "solved_count": 0}],
        "after_terminal": [first, second, terminal, first],
        "failed": [first, {"type": "failed", "error": "injected failure"}],
        "unknown": [{"type": "unexpected"}],
    }
    stream = EventStream(cases[case])
    with pytest.raises(ResultContractError):
        run_sweep(stream)
    assert stream.closed == 1


@pytest.mark.parametrize("frequencies", [[], [0], [-1], [float("nan")], [float("inf")],
                                        [500, 500], [[500]], [1e40], [1e-50]])
def test_invalid_or_duplicate_float64_frequency_requests_are_refused(run_sweep, frequencies):
    stream = EventStream([])
    with pytest.raises(ValueError, match="[Ff]requencies"):
        run_sweep(stream, frequencies, precision="float32")
    assert stream.closed == 1


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_raising_result_callback_closes_owned_stream(raw_result, run_sweep, error):
    def explode(index, frequency, entry):
        raise error("consumer failed")
    stream = EventStream([row(raw_result, 500), {"type": "completed", "solved_count": 1}])
    with pytest.raises(error, match="consumer failed"):
        run_sweep(stream, [500], on_frequency_result=explode)
    assert stream.closed == 1


def test_raising_progress_callback_closes_owned_stream(raw_result, run_sweep):
    def explode(index, total, frequency):
        assert (index, total, frequency) == (0, 2, 500)
        raise KeyboardInterrupt("progress consumer failed")

    stream = EventStream([row(raw_result, 500)])
    with pytest.raises(KeyboardInterrupt, match="progress consumer failed"):
        run_sweep(stream, progress_callback=explode)
    assert stream.closed == 1


def test_callback_false_requests_cancel_and_returns_partial(raw_result, run_sweep):
    cancellations, seen = [], []

    def callback(index, frequency, entry):
        seen.append((index, frequency, entry))
        return False

    stream = EventStream([row(raw_result, 500), {"type": "cancelled", "solved_count": 1}])
    result = run_sweep(stream, on_frequency_result=callback, request_cancel=lambda: cancellations.append(True))
    assert result.cancelled and result.is_partial
    assert cancellations == [True]
    assert seen[0][:2] == (0, 500)
    np.testing.assert_array_equal(seen[0][2]["observation_pressure_complex"], result.pressure_complex[0])
    assert stream.closed == 1


def test_changed_trace_count_between_frequencies_fails_and_closes(raw_result, run_sweep):
    second = raw_result(1000)
    second["quantities"][5]["values"] = wire([[1j, 2j, 3j]])
    stream = EventStream([row(raw_result, 500), {"type": "result", "result": second}])
    with pytest.raises(ResultContractError, match="shape"):
        run_sweep(stream)
    assert stream.closed == 1


def test_generator_is_closed_when_decoding_fails(raw_result, run_sweep):
    closed = []

    def stream():
        try:
            yield row(raw_result, 1000)  # wrong first frequency
        finally:
            closed.append(True)

    with pytest.raises(ResultContractError, match="out of order"):
        run_sweep(stream())
    assert closed == [True]
