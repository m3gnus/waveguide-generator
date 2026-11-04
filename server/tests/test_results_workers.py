"""Result conversion keeps the event loop available without changing payloads."""

import asyncio
from contextlib import asynccontextmanager
import json
import threading
from types import SimpleNamespace

import pytest

from server.jobs import api
from server.jobs.models import ChannelCombineSpec
from server.jobs.runtime import core
from server.tests.test_pressure_basis import multichannel_basis


async def _heartbeat_during(operation, entered, release):
    task = asyncio.create_task(operation)
    try:
        if not await asyncio.to_thread(entered.wait, 5):
            if task.done():
                return await task
            pytest.fail("the worker did not reach the conversion")
        await asyncio.sleep(0)
        assert not task.done(), "the worker must still be blocked during the heartbeat"
    finally:
        release.set()
    return await task


@pytest.mark.parametrize("operation", ["recombine", "pressure_basis"])
def test_result_decoding_runs_inside_the_worker(monkeypatch, operation):
    from server.solver import recombine
    from server.solver.pressure_basis import export_pressure_basis

    bases, results, motions = multichannel_basis()
    results["worker_decode_marker"] = "retained results"
    text = json.dumps(results)
    spec = ChannelCombineSpec(members=["normal", "axial"], crossovers_hz=[1000])
    expected = export_pressure_basis(bases, results, "normal", motions)
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = json.loads
    writes = []

    def decode(value, *args, **kwargs):
        if value == text:
            assert threading.get_ident() != loop_thread, "result JSON decoded on the event loop"
            entered.set()
            assert release.wait(5), "heartbeat did not release decoding"
        return original(value, *args, **kwargs)

    async def start():
        pass

    @asynccontextmanager
    async def mutation(_job_id):
        yield

    runtime = SimpleNamespace(
        start=start, _require_job=lambda _: {"status": "complete"},
        _serialized_result_mutation=mutation,
        store=SimpleNamespace(get_results_text=lambda _: text, get_channel_bases=lambda _: bases,
                              store_results=lambda _job, value: writes.append(value)),
    )
    monkeypatch.setattr(core, "_request_motions", lambda _: motions)
    monkeypatch.setattr(core, "_replay_request", lambda _: "request")
    monkeypatch.setattr(json, "loads", decode)

    def rebuild(value, retained, combine_spec, request):
        assert value == results and retained == bases and combine_spec == spec and request == "request"
        return {**value, "recombined": True}

    monkeypatch.setattr(recombine, "recombine_stored_results", rebuild)
    if operation == "recombine":
        operation_task = core.JobRuntime.recombine_results(runtime, "done", spec)
    else:
        operation_task = core.JobRuntime.get_pressure_basis(runtime, "done", "normal")
    actual = asyncio.run(_heartbeat_during(operation_task, entered, release))
    if operation == "recombine":
        assert actual == {**results, "recombined": True}
        assert writes == [actual]
    else:
        assert actual == expected
        assert not writes


def _combine_endpoint(updated):
    async def start():
        pass

    async def shutdown():
        pass

    async def recombine_results(_job_id, _spec):
        return updated

    runtime = SimpleNamespace(start=start, shutdown=shutdown, recombine_results=recombine_results)
    return next(route.endpoint for route in api.create_jobs_router(runtime).routes
                if route.path == "/api/results/{job_id}/combine")


def test_recombine_response_encoding_is_off_loop_and_byte_identical(monkeypatch):
    updated = {"frequencies": [100, 200.25], "channels": {"drive": {"pressure": [-0.0, 1e-20]}},
               "metadata": {"label": "Björn", "nullable": None, "enabled": True}}
    expected = json.dumps(updated, allow_nan=False).encode()
    endpoint = _combine_endpoint(updated)
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = json.dumps

    def encode(value, *args, **kwargs):
        # The shared bounded encoder may encode the result in smaller pieces.
        assert threading.get_ident() != loop_thread, "response JSON encoded on the event loop"
        entered.set()
        assert release.wait(5), "heartbeat did not release encoding"
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", encode)
    response = asyncio.run(_heartbeat_during(endpoint("done", None), entered, release))
    assert response.body == expected
    assert response.media_type == "application/json"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_recombine_response_still_refuses_nonfinite_values(value):
    endpoint = _combine_endpoint({"pressure": [value]})
    with pytest.raises(ValueError, match="Out of range float"):
        asyncio.run(endpoint("done", None))
