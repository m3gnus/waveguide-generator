"""Opt-in v3 bridge exercises the production admission and session lifetime."""
from dataclasses import replace
from pathlib import Path

import pytest

from server.solver import official_beat as bridge
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.transducers import ExteriorDriver, build_transducer_request
from server.solver.beat_adapter.preflight import UnsupportedPhysics
from server.tests.beat_adapter.test_transducers import FRAME, raw
from server.tests.test_official_beat_bridge import runtime as runtime_fixture

runtime = runtime_fixture


@pytest.fixture
def built(runtime):
    mesh = Path(__file__).parent.parent / "solver" / "warmup_mesh.msh"
    result = build_transducer_request(
        mesh.read_text(), drivers=[ExteriorDriver("driver", (2,), [0, 0, 1], 6, .0005, 7, .02, .001, 1.5)],
        layout=build_observations(sphere_grid=None), frame=FRAME, frequencies_hz=[500, 100])
    def mutate(data):
        frequency = data["freq_hz"]
        replacement = raw(result, frequency)
        # This fixture has one driver rather than the two-driver unit fixture.
        m = replacement["quantities"][-1]["metadata"]
        m["effective_volume_area_m2"] = [.02]
        data.clear()
        data.update(replacement)
    runtime.mutate = mutate
    return result


def test_v3_managed_bridge_returns_rms_and_reuses_worker(runtime, built):
    first = bridge.solve_transducer_compiled(built)
    second = bridge.solve_transducer_compiled(replace(built, wire=dict(built.wire, frequencies_hz=[500])))
    assert len(first.rows) == 2 and len(second.rows) == 1
    assert first.rows[0].velocity_rms_m_per_s[0, 0] == 1 - 2j
    assert first.rows[0].input_impedance_ohm("voltage:driver") == pytest.approx(2.83 / (.4 + .2j))
    assert len(runtime.workers) == 1 and len(runtime.requests) == 2
    assert runtime.calls.index("validate") < runtime.calls.index("start") < runtime.calls.index("submit")
    assert runtime.calls.count("negotiate") == 2
    assert all(not path.exists() for path in runtime.paths)


def test_unsupported_physics_never_imports_or_acquires_worker(runtime, built):
    built.wire["compiled_system"]["components"][0]["parameters"]["mms_kg"] = .023
    with pytest.raises(UnsupportedPhysics, match="bare driver"):
        bridge.solve_transducer_compiled(built)
    assert runtime.calls == [] and runtime.workers == []


def test_cancelled_prefix_keeps_received_rms_rows_and_cleans_staging(runtime, built):
    runtime.cancel_after = 1
    result = bridge.solve_transducer_compiled(built)
    assert result.cancelled and len(result.rows) == 1 and result.requested_frequency_count == 2
    assert all(stream.closed for stream in runtime.streams)
    assert all(not path.exists() for path in runtime.paths)


def test_callback_error_closes_stream_and_preserves_exception(runtime, built):
    def status(message):
        if "compiling solve" in message:
            raise LookupError("caller stopped")
    with pytest.raises(LookupError, match="caller stopped"):
        bridge.solve_transducer_compiled(built, status_callback=status)
    assert all(not path.exists() for path in runtime.paths)


def test_decoder_failure_closes_stream(runtime, built):
    original = runtime.mutate
    def mutate(data):
        original(data)
        data["quantities"][-2]["metadata"]["component_ids"] = ["wrong"]
    runtime.mutate = mutate
    with pytest.raises(bridge.OfficialBeatProtocolError, match="identity"):
        bridge.solve_transducer_compiled(built)
    assert all(stream.closed for stream in runtime.streams)


def test_cancellation_callback_after_result_preserves_error_and_closes(runtime, built):
    def cancel():
        if runtime.result_count:
            raise LookupError("cancel at first row")
    with pytest.raises(LookupError, match="cancel at first row"):
        bridge.solve_transducer_compiled(built, cancellation_callback=cancel)
    assert runtime.result_count == 1
    assert all(stream.closed for stream in runtime.streams)
    assert all(not path.exists() for path in runtime.paths)
