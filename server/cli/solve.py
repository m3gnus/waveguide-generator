"""Run one text design through the persistent headless job runtime."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine, Mapping
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import sys
import threading
from typing import Any, TextIO

from pydantic import ValidationError

from server.design.conventions import artifact_conventions
from server.design.textcfg import TextConfigError, parse
from server.engines.registry import EngineRegistry
from server.jobs.events import JobsProtocol
from server.jobs.runtime import (
    EngineUnavailableError,
    ImportedSolveRefusal,
    JobConflictError,
    JobResourceUnavailableError,
    JobRuntime,
    SymmetryValidationError,
    UnknownEngineError,
)
from server.jobs.store import JobStore
from server.platform.paths import ensure_data_layout
from server.platform.signal_rearm import (
    register_signal_rearm,
    unregister_signal_rearm,
)

from .request import build_request


_TERMINAL_STATUSES = {"complete", "error", "cancelled"}
_TERMINAL_EVENTS = {"completed", "failed", "cancelled"}


class _StreamTransport:
    def __init__(self, *, stdout: TextIO, stderr: TextIO) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self._closed = asyncio.Event()
        self._ready = asyncio.Event()
        self._terminal = asyncio.Event()

    async def receive(self) -> None:
        await self._closed.wait()
        return None

    async def close(self, code: int) -> None:
        del code
        self._closed.set()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    async def wait_terminal(self) -> None:
        await self._terminal.wait()

    def _observe(self, message: Mapping[str, Any]) -> None:
        if message.get("kind") == "hello":
            self._ready.set()
        if (
            message.get("kind") == "event"
            and message.get("type") in _TERMINAL_EVENTS
        ):
            self._terminal.set()


class NdjsonTransport(_StreamTransport):
    """Print every jobs-protocol message as strict JSON on stdout."""

    async def send_json(self, message: Mapping[str, Any]) -> None:
        print(
            json.dumps(dict(message), separators=(",", ":"), allow_nan=False),
            file=self.stdout,
            flush=True,
        )
        self._observe(message)


class TextTransport(_StreamTransport):
    """Render only useful live job lines; the command prints the final summary."""

    async def send_json(self, message: Mapping[str, Any]) -> None:
        kind = message.get("kind")
        if kind == "event":
            event_type = str(message.get("type") or "event")
            job_id = str(message.get("jobId") or "")
            payload = message.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            detail = payload.get("message") or payload.get("stage")
            suffix = f": {detail}" if detail else ""
            print(f"[{event_type}] {job_id}{suffix}", file=self.stderr, flush=True)
        elif kind == "partialResult":
            job_id = str(message.get("jobId") or "")
            revision = message.get("revision")
            print(
                f"[partialResult] {job_id} revision {revision}",
                file=self.stderr,
                flush=True,
            )
        self._observe(message)


@contextmanager
def _capture_sigint(interrupted: asyncio.Event):
    """Turn the first Ctrl-C into cooperative runtime shutdown."""

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    received = False

    def request_shutdown(signum: int, frame: Any) -> None:
        nonlocal received
        if received:
            if callable(previous):
                previous(signum, frame)
            else:
                raise KeyboardInterrupt
            return
        received = True
        interrupted.set()

    def install() -> None:
        signal.signal(signal.SIGINT, request_shutdown)

    token = register_signal_rearm(install)
    install()
    try:
        yield
    finally:
        unregister_signal_rearm(token)
        signal.signal(signal.SIGINT, previous)


def _validation_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        prefix = f"{location}: " if location else ""
        messages.append(prefix + str(error["msg"]))
    return messages


def _read_request(args: argparse.Namespace):
    path = args.design
    if path.suffix.lower() not in {".mwg", ".cfg"}:
        raise ValueError("design file must use the .mwg or .cfg extension")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read design file: {exc}") from exc
    try:
        parsed = parse(source)
    except (TextConfigError, TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return build_request(parsed, overlay=args.overlay, engine=args.engine).request


async def _wait_for_terminal(runtime: JobRuntime, job_id: str) -> dict[str, Any]:
    while True:
        job = await runtime.get_job(job_id)
        if job["status"] in _TERMINAL_STATUSES:
            return job
        await asyncio.sleep(0.02)


async def _wait_for_stream(
    waiter: Coroutine[Any, Any, None], protocol_task: asyncio.Task[None]
) -> None:
    event_task = asyncio.create_task(waiter)
    done, _pending = await asyncio.wait(
        {event_task, protocol_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if protocol_task in done:
        event_task.cancel()
        await asyncio.gather(event_task, return_exceptions=True)
        protocol_task.result()
        raise RuntimeError("job event stream closed unexpectedly")
    await event_task


def _summary(job: Mapping[str, Any]) -> dict[str, Any]:
    solve_options = job.get("solve_options")
    solve_options = solve_options if isinstance(solve_options, Mapping) else {}
    summary: dict[str, Any] = {
        "jobId": job["id"],
        "status": job["status"],
        "engine": solve_options.get("engine"),
        "conventions": artifact_conventions(),
        "timings": {
            "createdAt": job.get("created_at"),
            "queuedAt": job.get("queued_at"),
            "startedAt": job.get("started_at"),
            "completedAt": job.get("completed_at"),
            "solveWallTimeSeconds": job.get("solve_wall_time_seconds"),
        },
    }
    if job.get("run_number") is not None:
        summary["runNumber"] = job["run_number"]
    return summary


async def _write_output(
    output: Path,
    *,
    results: str,
    runtime: JobRuntime,
    job: Mapping[str, Any],
    stderr: TextIO,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    job_id = str(job["id"])
    (output / "results.json").write_text(
        results if results.endswith("\n") else results + "\n",
        encoding="utf-8",
    )
    try:
        mesh = await runtime.get_mesh_artifact(job_id)
    except JobResourceUnavailableError:
        print("Mesh artifact is unavailable; skipped mesh.msh.", file=stderr)
    else:
        (output / "mesh.msh").write_text(mesh, encoding="utf-8")
    (output / "job.log").write_text(await runtime.get_log(job_id), encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(_summary(job), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _print_summary(job: Mapping[str, Any], *, output: Path | None, stream: TextIO) -> None:
    run_number = (
        f" run #{job['run_number']}" if job.get("run_number") is not None else ""
    )
    destination = f"; output: {output}" if output is not None else ""
    print(
        f"Job {job['id']}{run_number}: {job['status']}{destination}",
        file=stream,
        flush=True,
    )


async def solve_path(
    args: argparse.Namespace,
    *,
    engine_registry: EngineRegistry,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        request = _read_request(args)
    except ValidationError as exc:
        for message in _validation_messages(exc):
            print(f"Solve refused: {message}", file=stderr)
        return 1
    except (TypeError, ValueError) as exc:
        print(f"Solve refused: {exc}", file=stderr)
        return 1

    if args.output is not None and args.output.exists():
        print(
            f"Solve refused: output directory already exists: {args.output}",
            file=stderr,
        )
        return 1

    try:
        data_dir = ensure_data_layout(args.data_dir).root
        runtime = JobRuntime(
            JobStore.for_data_dir(data_dir), engine_registry=engine_registry
        )
        await runtime.start()
    except JobConflictError as exc:
        print(
            f"{exc}. Use --data-dir DIR or stop the running GUI server.",
            file=stderr,
        )
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Waveguide Generator could not start the job runtime: {exc}", file=stderr)
        return 1

    ndjson = args.json_events or args.events == "ndjson"
    transport: _StreamTransport = (
        NdjsonTransport(stdout=stdout, stderr=stderr)
        if ndjson
        else TextTransport(stdout=stdout, stderr=stderr)
    )
    protocol = JobsProtocol(runtime)
    protocol_task = asyncio.create_task(protocol.run(transport))
    interrupted = asyncio.Event()
    shutdown_done = False
    try:
        await _wait_for_stream(transport.wait_ready(), protocol_task)
        try:
            job_id = await runtime.submit(request)
        except (
            EngineUnavailableError,
            ImportedSolveRefusal,
            SymmetryValidationError,
            UnknownEngineError,
            ValueError,
        ) as exc:
            print(f"Solve refused: {exc}", file=stderr)
            return 1

        terminal_task = asyncio.create_task(_wait_for_terminal(runtime, job_id))
        interrupt_task = asyncio.create_task(interrupted.wait())
        with _capture_sigint(interrupted):
            done, _pending = await asyncio.wait(
                {terminal_task, interrupt_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if interrupt_task in done and interrupted.is_set():
                terminal_task.cancel()
                await asyncio.gather(terminal_task, return_exceptions=True)
                await runtime.shutdown()
                shutdown_done = True
                return 130
            interrupt_task.cancel()
            await asyncio.gather(interrupt_task, return_exceptions=True)
            job = terminal_task.result()

        await _wait_for_stream(transport.wait_terminal(), protocol_task)
        results: str | None = None
        if job["status"] == "complete":
            try:
                results = await runtime.get_results_text(job_id)
            except (JobConflictError, JobResourceUnavailableError) as exc:
                print(f"Could not fetch solve results: {exc}", file=stderr)
                return 1
        if results is not None and args.output is not None:
            try:
                await _write_output(
                    args.output,
                    results=results,
                    runtime=runtime,
                    job=job,
                    stderr=stderr,
                )
            except FileExistsError:
                print(
                    f"Solve refused: output directory already exists: {args.output}",
                    file=stderr,
                )
                return 1
            except (OSError, ValueError) as exc:
                print(f"Could not write solve output: {exc}", file=stderr)
                return 1

        if not ndjson:
            _print_summary(job, output=args.output, stream=stdout)
        if job["status"] != "complete":
            message = job.get("error_message") or "solve did not complete"
            print(f"Solve failed: {message}", file=stderr)
            return 1
        return 0
    finally:
        await transport.close(1000)
        await asyncio.gather(protocol_task, return_exceptions=True)
        if not shutdown_done:
            await runtime.shutdown()


def solve_command(
    args: argparse.Namespace,
    *,
    engine_registry: EngineRegistry,
) -> int:
    return asyncio.run(
        solve_path(
            args,
            engine_registry=engine_registry,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    )


__all__ = [
    "NdjsonTransport",
    "TextTransport",
    "solve_command",
    "solve_path",
]
