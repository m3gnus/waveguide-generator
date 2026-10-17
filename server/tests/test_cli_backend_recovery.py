"""Production CLI regression coverage for shared-backend review findings."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from io import StringIO
import json
import signal
from types import SimpleNamespace

import pytest

from server.cli import backend_client
from server.cli import solve as cli
from server.engines.registry import EngineInfo, EngineRegistry
from server.tests.test_cli_backend_client import CancellableEngine, _args, _solve
from server.tests.test_cli_backend_transport import _transports
from server.tests.test_cli_solve import _design, _registry, _request


@pytest.mark.parametrize("recovery", ["resume", "poll_only"])
def test_disconnect_resumes_cursor_while_http_waits_for_running_job(tmp_path, monkeypatch, recovery):
    data_dir = tmp_path / "data"
    released = asyncio.Event()

    class WaitingEngine(CancellableEngine):
        async def run(self, request, *, cancel_cb, stage_cb):
            stage_cb("frequency_solve", 0.5, "Waiting for reconnect")
            while not released.is_set():
                cancel_cb()
                await asyncio.sleep(0.01)
            from server.tests.test_cli_solve import TinyEngine
            return await TinyEngine().run(request, cancel_cb=cancel_cb, stage_cb=stage_cb)

    registry = EngineRegistry(
        detector=lambda: [EngineInfo("metal", True, "test", "test")],
        factory=lambda _: WaitingEngine(),
    )

    async def scenario():
        async with _transports(data_dir, monkeypatch, registry) as (_, runtime, _):
            original_connect = backend_client.connect
            connections, resumes, cursors = [], [], []

            @asynccontextmanager
            async def flaky(*args, **kwargs):
                index = len(connections)
                connections.append(index)
                if index > 0 and recovery == "poll_only":
                    released.set()
                    raise OSError("event endpoint stays unavailable")
                async with original_connect(*args, **kwargs) as bridge:
                    class Socket:
                        async def recv(self):
                            if index == 0 and cursors:
                                assert (await runtime.get_job(cursors[0]["jobId"]))["status"] == "running"
                                raise OSError("transient disconnect")
                            raw = await bridge.recv()
                            message = json.loads(raw)
                            if index == 0 and message.get("type") == "stage":
                                cursors.append(message)
                            return raw

                        async def send(self, raw):
                            resumes.append(json.loads(raw))
                            await bridge.send(raw)
                            released.set()

                    yield Socket()

            monkeypatch.setattr(backend_client, "connect", flaky)
            code, messages, stderr = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 0, stderr
            assert len(connections) >= 2
            if recovery == "resume":
                assert resumes[0]["kind"] == "resume"
                assert resumes[0]["cursor"] == cursors[0]["cursor"]
                assert resumes[0]["epoch"] != cursors[0]["epoch"]
            else:
                assert not resumes
            assert messages[-1]["status"] == "complete"

    asyncio.run(scenario())


def test_stream_failure_cannot_override_available_terminal_http_result(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (_, runtime, _):
            async def fail_after_completion(self, transport):
                await transport.send_json({"v": 1, "kind": "hello", "epoch": 1})
                while True:
                    jobs, _ = await runtime.list_jobs(status="complete", limit=50, offset=0)
                    if jobs:
                        raise backend_client.BackendClientError("lost terminal frame")
                    await asyncio.sleep(0.001)

            monkeypatch.setattr(backend_client.BackendClient, "run_events", fail_after_completion)
            code, messages, stderr = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 0, stderr
            assert messages[-1]["status"] == "complete"

    asyncio.run(scenario())


@pytest.mark.parametrize("result_delay", [0, 2.1])
def test_missing_terminal_event_has_bounded_http_completion(tmp_path, monkeypatch, result_delay):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch):
            original_connect = backend_client.connect
            original_get_job = backend_client.BackendClient.get_job
            original_results = backend_client.BackendClient.get_results_text
            terminal_http_jobs = []

            async def observe_http_completion(self, job_id):
                job = await original_get_job(self, job_id)
                if job["status"] == "complete":
                    terminal_http_jobs.append(job_id)
                return job

            async def delayed_results(self, job_id):
                # Valid result retrieval may outlast the old total 2s ceiling;
                # it must not be mistaken for an unbounded terminal-event wait.
                await asyncio.sleep(result_delay)
                return await original_results(self, job_id)

            @asynccontextmanager
            async def drop_terminal(*args, **kwargs):
                async with original_connect(*args, **kwargs) as bridge:
                    original_recv = bridge.recv

                    async def recv():
                        while True:
                            raw = await original_recv()
                            if json.loads(raw).get("type") not in cli._TERMINAL_EVENTS:
                                return raw

                    bridge.recv = recv
                    yield bridge

            monkeypatch.setattr(backend_client, "connect", drop_terminal)
            monkeypatch.setattr(backend_client.BackendClient, "get_job", observe_http_completion)
            monkeypatch.setattr(backend_client.BackendClient, "get_results_text", delayed_results)
            # _solve retains its deadlock ceiling. Geometry/admission, SQLite,
            # HTTP and results work are not the missing-event grace period.
            code, messages, stderr = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 0, stderr
            assert not any(message.get("type") == "completed" for message in messages)
            assert messages[-1]["status"] == "complete"
            assert messages[-1]["job_id"] in terminal_http_jobs

    asyncio.run(scenario())


def test_missing_terminal_drain_keeps_its_half_second_bound_and_settles_waiter(monkeypatch):
    async def scenario():
        transport = cli.NdjsonTransport(stdout=StringIO(), stderr=StringIO())
        protocol = asyncio.create_task(asyncio.Event().wait())
        waiters, waits = [], []

        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            waiters.append(task)
            return task

        async def timeout_without_clock_delay(tasks, *, timeout, return_when):
            assert timeout == 0.5
            assert return_when is asyncio.FIRST_COMPLETED
            assert tasks == {protocol, waiters[0]}
            await asyncio.sleep(0)
            assert all(not task.done() for task in tasks)
            waits.append(tasks)
            return set(), tasks

        # Patch only the CLI's module binding; the event loop and other tasks
        # retain real asyncio. Simulate the wait's timeout, not a terminal event.
        facade = SimpleNamespace(create_task=create_task, wait=timeout_without_clock_delay,
                                 FIRST_COMPLETED=asyncio.FIRST_COMPLETED, gather=asyncio.gather)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(cli, "asyncio", facade)
                async with asyncio.timeout(2):
                    await cli._drain_terminal_events(transport, protocol)
            assert len(waits) == len(waiters) == 1
            assert waiters[0].done() and waiters[0].cancelled()
            assert not protocol.done()
            assert not transport._terminal.is_set()
        finally:
            for task in [protocol, *waiters]:
                task.cancel()
            await asyncio.gather(protocol, *waiters, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("hello_arrives", [False, True])
def test_sigint_before_hello_never_submits(tmp_path, monkeypatch, hello_arrives):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (app, runtime, _):
            @asynccontextmanager
            async def delayed_hello(*args, **kwargs):
                class Socket:
                    signalled = False

                    async def recv(self):
                        if not self.signalled:
                            self.signalled = True
                            signal.raise_signal(signal.SIGINT)
                            if hello_arrives:
                                return json.dumps({"v": 1, "kind": "hello", "epoch": 1})
                        await asyncio.Event().wait()
                yield Socket()

            monkeypatch.setattr(backend_client, "connect", delayed_hello)
            async with asyncio.timeout(1):
                code, messages, _ = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 130
            assert messages[-1]["status"] == "interrupted"
            assert (await runtime.list_jobs(status=None, limit=50, offset=0))[1] == 0
            assert not any(path == "/api/solve" for _, path, _ in app.state.seen_requests)

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_races_stop", [False, True])
def test_sigint_finished_job_uses_real_terminal_state(tmp_path, monkeypatch, finish_races_stop):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (app, _, _):
            original_get = backend_client.BackendClient.get_job
            original_stop = backend_client.BackendClient.stop
            interrupted, attempted_stop = False, False

            async def get_job(self, job_id):
                nonlocal interrupted
                job = await original_get(self, job_id)
                if not interrupted:
                    while job["status"] not in cli._TERMINAL_STATUSES:
                        await asyncio.sleep(0.01)
                        job = await original_get(self, job_id)
                    interrupted = True
                    signal.raise_signal(signal.SIGINT)
                if finish_races_stop and not attempted_stop:
                    return dict(job, status="running")
                return job

            async def stop(self, job_id):
                nonlocal attempted_stop
                attempted_stop = True
                await original_stop(self, job_id)  # Real route refuses the finished job with 400.

            monkeypatch.setattr(backend_client.BackendClient, "get_job", get_job)
            monkeypatch.setattr(backend_client.BackendClient, "stop", stop)
            code, messages, stderr = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 0, stderr
            assert messages[-1]["status"] == "complete"
            assert attempted_stop == finish_races_stop
            assert any(path.startswith("/api/stop/") for _, path, _ in app.state.seen_requests) == finish_races_stop

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["download", "output"])
def test_sigint_during_results_or_output_interrupts_normally(tmp_path, monkeypatch, phase):
    data_dir = tmp_path / "data"
    args = _args(_design(tmp_path), data_dir, "--output", str(tmp_path / "output"))

    async def scenario():
        async with _transports(data_dir, monkeypatch):
            if phase == "download":
                original = backend_client.BackendClient.get_results_text

                async def interrupt(self, job_id):
                    signal.raise_signal(signal.SIGINT)
                    return await original(self, job_id)

                monkeypatch.setattr(backend_client.BackendClient, "get_results_text", interrupt)
            else:
                original = cli._write_output

                async def interrupt(*args, **kwargs):
                    signal.raise_signal(signal.SIGINT)
                    return await original(*args, **kwargs)

                monkeypatch.setattr(cli, "_write_output", interrupt)
            previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                with pytest.raises(KeyboardInterrupt):
                    await _solve(args)
            finally:
                signal.signal(signal.SIGINT, previous)
            assert not args.output.exists()

    asyncio.run(scenario())


def test_http_submission_conflict_preserves_cli_code_and_recovery_hint(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    request = _request(tmp_path)
    args = _args(_design(tmp_path), data_dir)
    args.design, args.request = None, request

    async def scenario():
        async with _transports(data_dir, monkeypatch):
            assert (await _solve(args))[0] == 0
            payload = json.loads(request.read_text())
            payload["options"]["frequencies_hz"] = [600.0]
            request.write_text(json.dumps(payload))
            code, messages, stderr = await _solve(args)
            assert code == 1
            error = messages[-1]["error"]
            assert error["code"] == "submission_conflict"
            assert "choose a new clientRequestId" in error["message"]
            assert "choose a new clientRequestId" in stderr
            assert error["stage"] == "submission" and not error["retryable"]

    asyncio.run(scenario())


@pytest.mark.parametrize("http_status,retryable", [(400, False), (503, True)])
def test_http_non_envelope_refusals_have_http_code(tmp_path, monkeypatch, http_status, retryable):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (app, _, _):
            from starlette.responses import Response

            @app.middleware("http")
            async def refuse(request, call_next):
                if request.url.path == "/api/solve":
                    return Response("plain HTTP refusal", status_code=http_status)
                return await call_next(request)

            code, messages, _ = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 1
            error = messages[-1]["error"]
            assert error["code"] == "backend_http_error"
            assert error["retryable"] == retryable
            assert error["details"]["http_status"] == http_status

    asyncio.run(scenario())


@pytest.mark.parametrize("events", ["ndjson", "text"])
def test_relay_filters_other_jobs_but_keeps_snapshot_and_own_early_events(tmp_path, monkeypatch, events):
    data_dir = tmp_path / "data"
    args = _args(_design(tmp_path), data_dir)
    args.events = events

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (_, runtime, _):
            other = await runtime.submit(cli._read_request(args))
            await cli._wait_for_terminal(runtime, other)
            original = backend_client.BackendClient.submit

            async def delayed_acceptance(self, request):
                job_id = await original(self, request)
                # Ensure own events arrive before watch_job knows the accepted id.
                await cli._wait_for_terminal(runtime, job_id)
                await asyncio.sleep(0.03)
                runtime.events.publish({"v": 1, "kind": "event", "type": "stage",
                                        "jobId": other, "payload": {"message": "unrelated"}})
                runtime.events.publish({"v": 1, "kind": "partialResult", "jobId": other,
                                        "revision": 1, "payload": {}})
                await asyncio.sleep(0.03)
                return job_id

            monkeypatch.setattr(backend_client.BackendClient, "submit", delayed_acceptance)
            stdout, stderr = StringIO(), StringIO()
            code = await cli.solve_path(args, engine_registry=_registry(), stdout=stdout, stderr=stderr)
            assert code == 0
            if events == "ndjson":
                messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
                own = messages[-1]["job_id"]
                assert other in {job["id"] for message in messages if message.get("kind") == "snapshot"
                                 for job in message["jobs"]}
                assert any(message.get("type") == "completed" and message["jobId"] == own for message in messages)
                assert all(message.get("jobId") in {None, own} for message in messages)
            else:
                assert other not in stderr.getvalue()
                assert "[completed]" in stderr.getvalue()

    asyncio.run(scenario())
