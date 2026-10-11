"""HTTP/WS client contracts without binding sockets (real ASGI and job runtime)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
import json
import os
import signal
from urllib.error import HTTPError
from urllib.parse import urlsplit

from server.cli import backend_client
from server.cli import solve as cli
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.events import JobsProtocol
from server.platform.backend_owner import BackendHandle
from server.tests.test_cli_backend_client import CancellableEngine, _app, _args, _solve
from server.tests.test_cli_solve import _design, _registry, _request
from server.tests.test_jobs_api import _request_with_headers


@asynccontextmanager
async def _transports(data_dir, monkeypatch, registry=None):
    app, runtime = _app(data_dir, registry or _registry())
    await runtime.start()
    loop = asyncio.get_running_loop()
    handle = BackendHandle("http://127.0.0.1:3100", os.getpid(), 3100, data_dir, "test", "test")

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Origin") is None
            body = json.loads(request.data) if request.data is not None else None
            path = urlsplit(request.full_url).path
            response = asyncio.run_coroutine_threadsafe(
                _request_with_headers(app, request.method, path, body=body), loop,
            ).result(timeout=timeout)
            status, content, _ = response
            if status >= 400:
                raise HTTPError(request.full_url, status, "HTTP refusal", {}, BytesIO(content))
            return BytesIO(content)

    @asynccontextmanager
    async def connect(url, **kwargs):
        assert url == "ws://127.0.0.1:3100/ws/jobs"
        assert kwargs["origin"] is None and kwargs["proxy"] is None
        messages = asyncio.Queue()

        class Bridge:
            async def receive(self):
                await asyncio.Event().wait()

            async def send_json(self, message):
                await messages.put(json.dumps(message))

            async def close(self, code):
                pass

            async def recv(self):
                return await messages.get()

        bridge = Bridge()
        task = asyncio.create_task(JobsProtocol(runtime).run(bridge))
        try:
            yield bridge
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    monkeypatch.setattr(backend_client.urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(backend_client, "connect", connect)
    monkeypatch.setattr(cli, "discover_backend", lambda root: handle)
    try:
        yield app, runtime, handle
    finally:
        await runtime.shutdown()


def test_http_requests_route_and_client_preserve_artifact_bytes(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    output, direct = tmp_path / "output", tmp_path / "direct"
    args = _args(_design(tmp_path), data_dir, "--output", str(output))

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (app, runtime, handle):
            code, messages, stderr = await _solve(args)
            assert code == 0, stderr
            outcome = messages[-1]
            assert outcome["job_url"] == handle.base_url + "/?job=" + outcome["job_id"]
            assert any(message.get("type") == "completed" for message in messages)
            job_id = outcome["job_id"]
            await cli._write_output(
                direct, results=await runtime.get_results_text(job_id), runtime=runtime,
                job=await runtime.get_job(job_id), submitted_request=cli._read_request(args),
                stderr=StringIO(),
            )
            for artifact in output.iterdir():
                assert artifact.read_bytes() == (direct / artifact.name).read_bytes(), artifact.name
            status, body, headers = await _request_with_headers(app, "GET", f"/api/jobs/{job_id}/requests")
            assert status == 200 and headers["cache-control"] == "no-store"
            requests = json.loads(body)
            assert requests["effective_request"] == json.loads((output / "effective-request.json").read_bytes())
            assert requests["execution_request"] == json.loads((output / "execution-request.json").read_bytes())
            status, _, _ = await _request_with_headers(app, "GET", "/api/jobs/missing/requests")
            assert status == 404

    asyncio.run(scenario())


def test_concurrent_relays_and_replayed_jobs_finish_with_outcome(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    design = _design(tmp_path)
    request = _request(tmp_path)

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (_, runtime, _):
            results = await asyncio.gather(_solve(_args(design, data_dir)), _solve(_args(design, data_dir)))
            assert [code for code, _, _ in results] == [0, 0]
            assert len({messages[-1]["job_id"] for _, messages, _ in results}) == 2
            args = _args(design, data_dir)
            args.design, args.request = None, request
            first, messages, _ = await _solve(args)
            again, replay, _ = await _solve(args)
            assert first == again == 0
            assert messages[-1]["job_id"] == replay[-1]["job_id"]
            assert (await runtime.list_jobs(status=None, limit=50, offset=0))[1] == 3

    asyncio.run(scenario())


def test_http_refusal_keeps_backend_error_envelope(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    args = _args(_design(tmp_path), data_dir)
    args.engine = "quantum"

    async def scenario():
        async with _transports(data_dir, monkeypatch):
            code, messages, _ = await _solve(args)
            assert code == 1
            assert messages[-1]["status"] == "refused"
            assert messages[-1]["error"]["code"] == "unknown_engine"
            assert messages[-1]["error"]["stage"] == "submission"

    asyncio.run(scenario())


def test_stream_failure_is_a_cli_outcome_and_does_not_shutdown_backend(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"

    async def scenario():
        async with _transports(data_dir, monkeypatch) as (_, runtime, handle):
            @asynccontextmanager
            async def unavailable(*args, **kwargs):
                raise OSError("event connection unavailable")
                yield

            monkeypatch.setattr(backend_client, "connect", unavailable)
            code, messages, _ = await _solve(_args(_design(tmp_path), data_dir))
            assert code == 1
            assert messages[-1]["error"]["code"] == "backend_connection_failed"
            assert (await runtime.list_jobs(status=None, limit=50, offset=0))[1] == 0

    asyncio.run(scenario())


def test_sigint_calls_stop_route_and_preserves_another_queued_job(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    args = _args(_design(tmp_path), data_dir)
    registry = EngineRegistry(
        detector=lambda: [EngineInfo("metal", True, "test", "test")],
        factory=lambda _: CancellableEngine(),
    )

    async def scenario():
        async with _transports(data_dir, monkeypatch, registry) as (app, runtime, _):
            async def interrupt():
                async with asyncio.timeout(5):
                    while not (await runtime.list_jobs(status="running", limit=50, offset=0))[0]:
                        await asyncio.sleep(0.01)
                other = await runtime.submit(cli._read_request(args))
                signal.raise_signal(signal.SIGINT)
                return other

            task = asyncio.create_task(interrupt())
            previous = signal.getsignal(signal.SIGINT)
            code, messages, _ = await _solve(args)
            other = await task
            assert code == 130
            assert signal.getsignal(signal.SIGINT) == previous
            assert messages[-1]["status"] == "interrupted"
            job_id = messages[-1]["job_id"]
            assert job_id != other
            assert ("POST", f"/api/stop/{job_id}", None) in app.state.seen_requests
            async with asyncio.timeout(5):
                while (await runtime.get_job(job_id))["status"] != "cancelled":
                    await asyncio.sleep(0.01)
            assert (await runtime.get_job(other))["status"] in {"queued", "running"}

    asyncio.run(scenario())
