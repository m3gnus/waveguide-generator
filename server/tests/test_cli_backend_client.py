"""CLI client acceptance contracts against real HTTP/WS and instance ownership."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

from fastapi import FastAPI, Request
import pytest
import uvicorn

from launch.backend_argv import headless_backend_argv
from server.cli.args import build_parser
from server.cli import solve as cli
from server.cli.backend_client import BackendClient
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.api import create_jobs_router
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.platform import backend_owner
from server.platform.backend_owner import (
    BackendDataDirMismatch, BackendSpawnError, BackendStartTimeout, discover_backend,
)
from server.platform.instance import InstanceLock
from server.tests.test_cli_solve import MESH, TinyEngine, _design, _registry, _request


ROOT = Path(__file__).resolve().parents[2]


class NoSignalsServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Test servers share the CLI's main thread; the CLI must own SIGINT.
        yield


def _app(data_dir, registry):
    runtime = JobRuntime(JobStore.for_data_dir(data_dir), engine_registry=registry)
    app = FastAPI()
    app.include_router(create_jobs_router(runtime))
    app.state.seen_requests = []

    @app.middleware("http")
    async def observe(request: Request, call_next):
        app.state.seen_requests.append((request.method, request.url.path, request.headers.get("origin")))
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"pid": os.getpid(), "data_dir": str(data_dir), "version": "test", "commit": "test"}

    return app, runtime


@asynccontextmanager
async def _backend(data_dir, registry=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    server = None
    task = None
    lock = InstanceLock(data_dir / "locks")
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        lock.acquire(port)
        app, runtime = _app(data_dir, registry or _registry())
        server = NoSignalsServer(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield runtime, app, port
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            await asyncio.wait_for(task, 10)
        lock.release()
        sock.close()


def _args(design, data_dir, *extra):
    return build_parser().parse_args([
        "solve", str(design), "--data-dir", str(data_dir), "--events", "ndjson", *extra,
    ])


async def _solve(args):
    stdout, stderr = StringIO(), StringIO()
    async with asyncio.timeout(15):
        code = await cli.solve_path(args, engine_registry=_registry(), stdout=stdout, stderr=stderr)
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert messages[-1]["kind"] == "outcome"
    return code, messages, stderr.getvalue()


def test_cli_submits_to_running_owner_and_appears_in_its_job_list(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    design = _design(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("production CLI must use the owner, without constructing or starting a runtime")

    monkeypatch.setattr(cli, "JobRuntime", forbidden)
    monkeypatch.setattr(cli, "ensure_backend", forbidden)

    async def scenario():
        async with _backend(data_dir) as (runtime, app, port):
            code, messages, _ = await _solve(_args(design, data_dir))
            assert code == 0
            outcome = messages[-1]
            assert outcome["job_url"] == f"http://127.0.0.1:{port}/?job={outcome['job_id']}"
            assert any(message.get("type") == "completed" for message in messages)
            client = BackendClient(await asyncio.to_thread(discover_backend, data_dir))
            listing = json.loads(await client._http("/api/jobs"))
            assert [job["id"] for job in listing["items"]] == [outcome["job_id"]]
            assert (await runtime.get_job(outcome["job_id"]))["status"] == "complete"
            assert all(origin is None for _, _, origin in app.state.seen_requests)
            # Human output exposes the same deep link, and the backend stays alive.
            args = _args(design, data_dir)
            args.events = "text"
            stdout = StringIO()
            assert await cli.solve_path(args, engine_registry=_registry(), stdout=stdout, stderr=StringIO()) == 0
            assert f"http://127.0.0.1:{port}/?job=" in stdout.getvalue()

    asyncio.run(scenario())


def test_two_concurrent_cli_solves_use_one_owner_and_create_two_jobs(tmp_path):
    data_dir = tmp_path / "data"
    design = _design(tmp_path)

    async def scenario():
        async with _backend(data_dir) as (runtime, _, port):
            results = await asyncio.gather(_solve(_args(design, data_dir)), _solve(_args(design, data_dir)))
            assert [code for code, _, _ in results] == [0, 0]
            ids = {messages[-1]["job_id"] for _, messages, _ in results}
            assert len(ids) == 2
            items, total = await runtime.list_jobs(status=None, limit=50, offset=0)
            assert total == 2
            assert {job["id"] for job in items} == ids
            assert all(job["status"] == "complete" for job in items)
            handle = await asyncio.to_thread(discover_backend, data_dir)
            assert handle.port == port
            assert handle.pid == os.getpid()

    asyncio.run(scenario())


def test_backend_output_matches_in_process_bundle_byte_for_byte(tmp_path):
    data_dir = tmp_path / "data"
    request_path = _request(tmp_path)
    output = tmp_path / "http-output"
    direct = tmp_path / "direct-output"
    args = build_parser().parse_args([
        "solve", "--request", str(request_path), "--data-dir", str(data_dir),
        "--json-events", "--output", str(output),
    ])

    async def scenario():
        async with _backend(data_dir) as (runtime, app, _):
            code, messages, _ = await _solve(args)
            assert code == 0
            job_id = messages[-1]["job_id"]
            await cli._write_output(
                direct, results=await runtime.get_results_text(job_id), runtime=runtime,
                job=await runtime.get_job(job_id), submitted_request=cli._read_request(args), stderr=StringIO(),
            )
            assert sorted(path.name for path in output.iterdir()) == sorted(path.name for path in direct.iterdir())
            for path in output.iterdir():
                assert path.read_bytes() == (direct / path.name).read_bytes(), path.name
            assert (output / "mesh.msh").read_bytes() == MESH.encode()
            assert messages[-1]["result_sha256"] == hashlib.sha256((output / "results.json").read_bytes()).hexdigest()
            assert sum(path == f"/api/jobs/{job_id}/requests" for _, path, _ in app.state.seen_requests) == 1
            # Replayed complete jobs terminate from the initial WS snapshot.
            args.output = None
            again, replay, _ = await _solve(args)
            assert again == 0 and replay[-1]["job_id"] == job_id
            client = BackendClient(await asyncio.to_thread(discover_backend, data_dir))
            assert (json.loads(await client._http(f"/api/jobs/{job_id}/requests")))["execution_request"] == json.loads((output / "execution-request.json").read_bytes())

    asyncio.run(scenario())


def _serve():
    """A small supported-protocol backend for ensure_backend's real subprocess."""
    data_dir = Path(sys.argv[1]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    lock = InstanceLock(data_dir / "locks")
    lock.acquire(sock.getsockname()[1])
    app, _ = _app(data_dir, _registry())
    try:
        asyncio.run(uvicorn.Server(uvicorn.Config(app, log_level="error")).serve(sockets=[sock]))
    finally:
        lock.release()
        sock.close()


def test_no_backend_concurrent_clients_ensure_starts_exactly_one(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    design = _design(tmp_path)
    processes = []
    real_spawn = backend_owner._spawn
    real_ensure = cli.ensure_backend

    def track(argv, root):
        process, log = real_spawn(argv, root)
        processes.append(process)
        return process, log

    monkeypatch.setattr(backend_owner, "_spawn", track)
    monkeypatch.setattr(cli, "ensure_backend", lambda root, argv: real_ensure(root, argv, timeout=10))
    monkeypatch.setattr(cli, "headless_backend_argv", lambda root: [
        sys.executable, "-c", "from server.tests.test_cli_backend_client import _serve; _serve()", str(root),
    ])

    async def scenario():
        assert await asyncio.to_thread(discover_backend, data_dir) is None
        results = await asyncio.gather(_solve(_args(design, data_dir)), _solve(_args(design, data_dir)))
        assert [code for code, _, _ in results] == [0, 0], [stderr for _, _, stderr in results]
        assert len(processes) == 1
        handle = await asyncio.to_thread(discover_backend, data_dir)
        assert handle.pid == processes[0].pid
        assert handle.data_dir == data_dir.resolve()
        listing = json.loads(await BackendClient(handle)._http("/api/jobs"))
        assert listing["total"] == 2

    try:
        asyncio.run(scenario())
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("error,code,name", [
    (BackendDataDirMismatch, 3, "backend_data_dir_mismatch"),
    (BackendStartTimeout, 4, "backend_start_timeout"),
    (BackendSpawnError, 5, "backend_spawn_failed"),
])
def test_typed_startup_errors_do_not_change_folder(tmp_path, monkeypatch, error, code, name):
    data_dir = tmp_path / "data"
    calls = []
    monkeypatch.setattr(cli, "discover_backend", lambda root: None)

    def refuse(root, argv):
        calls.append((root, argv))
        raise error("Check the backend installation and this data folder.")

    monkeypatch.setattr(cli, "ensure_backend", refuse)
    result, messages, stderr = asyncio.run(_solve(_args(_design(tmp_path), data_dir)))
    assert result == code
    assert messages[-1]["error"]["code"] == name
    assert "Check the backend installation" in stderr
    assert len(calls) == 1 and calls[0][0] == data_dir
    assert calls[0][1] == headless_backend_argv(data_dir)
    assert calls[0][1][-1] == "--no-browser"
    assert str(data_dir.resolve()) in calls[0][1]


class CancellableEngine(TinyEngine):
    async def run(self, request, *, cancel_cb, stage_cb):
        stage_cb("frequency_solve", 0.5, "Waiting for cancellation")
        while True:
            cancel_cb()
            await asyncio.sleep(0.02)


def test_sigint_stops_only_the_submitted_job_and_keeps_owner_alive(tmp_path):
    data_dir = tmp_path / "data"
    design = _design(tmp_path)
    registry = EngineRegistry(
        detector=lambda: [EngineInfo("metal", True, "test", "test")],
        factory=lambda _: CancellableEngine(),
    )

    async def scenario():
        async with _backend(data_dir, registry) as (runtime, app, _):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "server.cli", "solve", str(design),
                "--data-dir", str(data_dir), "--json-events", cwd=ROOT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(10):
                    while True:
                        items, _ = await runtime.list_jobs(status="running", limit=50, offset=0)
                        if items:
                            break
                        await asyncio.sleep(0.02)
                # Consume a stage event to ensure the CLI's signal handler is installed.
                lines = []
                async with asyncio.timeout(10):
                    while True:
                        line = await process.stdout.readline()
                        assert line, "CLI exited before cancellation"
                        lines.append(line)
                        if json.loads(line).get("type") == "stage":
                            break
                process.send_signal(signal.SIGINT)
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                messages = [json.loads(line) for line in b"".join(lines).splitlines() + stdout.splitlines()]
                assert process.returncode == 130, stderr.decode()
                assert messages[-1]["kind"] == "outcome"
                assert messages[-1]["status"] == "interrupted"
                job_id = messages[-1]["job_id"]
                assert messages[-1]["job_url"].endswith(f"/?job={job_id}")
                async with asyncio.timeout(5):
                    while (await runtime.get_job(job_id))["status"] != "cancelled":
                        await asyncio.sleep(0.02)
                assert ("POST", f"/api/stop/{job_id}", None) in app.state.seen_requests
                assert await asyncio.to_thread(discover_backend, data_dir) is not None
            finally:
                if process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 10)

    asyncio.run(scenario())
