"""Run one text design through the persistent headless job runtime."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine, Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
from typing import Any, TextIO

from pydantic import ValidationError

from server.design.conventions import artifact_conventions
from server.design.textcfg import TextConfigError, parse
from server.engines.registry import EngineRegistry
from server.integration.provenance import canonical_json_sha256
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
from server.cadlink.store import CadLinkStore
from server.jobs.store import JobStore
from server.platform.paths import ensure_data_layout
from server.platform.signal_rearm import (
    register_signal_rearm,
    unregister_signal_rearm,
)

from .request import build_request
from .outcome import write_outcome
from .request import load_request_document


_TERMINAL_STATUSES = {"complete", "error", "cancelled"}
_TERMINAL_EVENTS = {"completed", "failed", "cancelled"}
_DATABASE_ONLY_WARNING = (
    "Warning: no --output directory was requested. Results are retained only in "
    "the WG job database and are subject to its retention policy; this CLI solve "
    "is not added to the GUI Workspace run archive. Use --output DIR for a "
    "durable, caller-owned artifact bundle."
)


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
    if args.request is not None:
        return load_request_document(
            args.request,
            overlay=args.overlay,
            engine=args.engine,
        ).request
    path = args.design
    assert path is not None
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_artifact_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_artifact(
    path: Path,
    content: bytes,
    *,
    canonical_sha256: str | None = None,
) -> dict[str, str]:
    """Write and hash the same bytes on every supported platform."""

    path.write_bytes(content)
    digest = {"sha256": hashlib.sha256(content).hexdigest()}
    if canonical_sha256 is not None:
        digest["canonical_sha256"] = canonical_sha256
    return digest


def _summary(
    job: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    results: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    solve_options = job.get("solve_options")
    solve_options = solve_options if isinstance(solve_options, Mapping) else {}
    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "jobId": job["id"],
        "status": job["status"],
        "engine": solve_options.get("engine"),
        "clientRequestId": request.get("client_request_id"),
        "resultKind": results.get("result_kind"),
        "resultContractVersion": results.get("result_contract_version"),
        "provenance": results.get("provenance") or {},
        "artifacts": dict(artifacts),
        "requestIdentity": {
            "submittedSha256": artifacts["request.json"]["canonical_sha256"],
            "effectiveSha256": artifacts["effective-request.json"][
                "canonical_sha256"
            ],
            "executionSha256": artifacts["execution-request.json"][
                "canonical_sha256"
            ],
            "provenanceScope": "execution",
        },
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
    submitted_request: Any,
    stderr: TextIO,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        job_id = str(job["id"])
        effective_request = await runtime.get_effective_request(job_id)
        execution_request = await runtime.get_execution_request(job_id)
        submitted_wire = submitted_request.model_dump(mode="json")
        effective_wire = effective_request.model_dump(mode="json")
        execution_wire = execution_request.model_dump(mode="json")
        artifacts: dict[str, dict[str, Any]] = {
            "results.json": _write_artifact(
                staging / "results.json",
                results.encode("utf-8"),
            ),
            "request.json": _write_artifact(
                staging / "request.json",
                _json_artifact_bytes(submitted_wire),
                canonical_sha256=canonical_json_sha256(submitted_wire),
            ),
            "effective-request.json": _write_artifact(
                staging / "effective-request.json",
                _json_artifact_bytes(effective_wire),
                canonical_sha256=canonical_json_sha256(effective_wire),
            ),
            "execution-request.json": _write_artifact(
                staging / "execution-request.json",
                _json_artifact_bytes(execution_wire),
                canonical_sha256=canonical_json_sha256(execution_wire),
            ),
        }
        try:
            mesh = await runtime.get_mesh_artifact(job_id)
        except JobResourceUnavailableError:
            print("Mesh artifact is unavailable; skipped mesh.msh.", file=stderr)
        else:
            artifacts["mesh.msh"] = _write_artifact(
                staging / "mesh.msh",
                mesh.encode("utf-8"),
            )
        log_content = await runtime.get_log(job_id)
        artifacts["job.log"] = _write_artifact(
            staging / "job.log",
            log_content.encode("utf-8"),
        )
        results_document = json.loads(results)
        summary = _summary(
            job,
            request=submitted_wire,
            results=results_document,
            artifacts=artifacts,
        )
        _write_artifact(
            staging / "summary.json",
            _json_artifact_bytes(summary),
        )
        staging.rename(output)
        return summary
    finally:
        if staging.exists():
            shutil.rmtree(staging)


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
    ndjson = args.json_events or args.events == "ndjson"
    try:
        request = _read_request(args)
    except ValidationError as exc:
        messages = _validation_messages(exc)
        for message in messages:
            print(f"Solve refused: {message}", file=stderr)
        if ndjson:
            write_outcome(
                stdout,
                status="refused",
                error_code="invalid_request",
                error_stage="input",
                error_message="; ".join(messages),
                error_details={"validation_messages": messages},
            )
        return 1
    except (TypeError, ValueError) as exc:
        print(f"Solve refused: {exc}", file=stderr)
        if ndjson:
            write_outcome(
                stdout,
                status="refused",
                error_code="invalid_input",
                error_stage="input",
                error_message=str(exc),
            )
        return 1

    if args.output is not None and args.output.exists():
        print(
            f"Solve refused: output directory already exists: {args.output}",
            file=stderr,
        )
        if ndjson:
            write_outcome(
                stdout,
                status="refused",
                client_request_id=request.client_request_id,
                output_directory=str(args.output),
                error_code="output_exists",
                error_stage="output",
                error_message=f"output directory already exists: {args.output}",
            )
        return 1

    try:
        data_dir = ensure_data_layout(args.data_dir).root
        # Imported geometry resolves its ingest through this registry, and a
        # runtime without one refuses every CAD-Link solve as
        # ``ingest_registry_unavailable`` -- so headless solves worked for
        # parametric designs only, and a CAD model could not be swept, laddered
        # or re-solved outside the GUI at all. It is the same store the app
        # mounts, opened from the same data directory.
        cadlink_store = CadLinkStore.for_data_dir(data_dir)
        cadlink_store.initialize()
        runtime = JobRuntime(
            JobStore.for_data_dir(data_dir),
            engine_registry=engine_registry,
            cadlink_store=cadlink_store,
        )
        await runtime.start()
    except JobConflictError as exc:
        print(
            f"{exc}. Use --data-dir DIR or stop the running GUI server.",
            file=stderr,
        )
        if ndjson:
            write_outcome(
                stdout,
                status="refused",
                client_request_id=request.client_request_id,
                error_code="runtime_conflict",
                error_stage="startup",
                error_message=str(exc),
                retryable=True,
            )
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Waveguide Generator could not start the job runtime: {exc}", file=stderr)
        if ndjson:
            write_outcome(
                stdout,
                status="failed",
                client_request_id=request.client_request_id,
                error_code="runtime_start_failed",
                error_stage="startup",
                error_message=str(exc),
                retryable=True,
            )
        return 1

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
        except EngineUnavailableError as exc:
            print(f"Solve refused: {exc}", file=stderr)
            if ndjson:
                write_outcome(
                    stdout,
                    status="refused",
                    client_request_id=request.client_request_id,
                    error_code="engine_unavailable",
                    error_stage="submission",
                    error_message=str(exc),
                )
            return 1
        except ImportedSolveRefusal as exc:
            print(f"Solve refused: {exc}", file=stderr)
            if ndjson:
                write_outcome(
                    stdout,
                    status="refused",
                    client_request_id=request.client_request_id,
                    error_code=exc.reason_code,
                    error_stage="submission",
                    error_message=str(exc),
                    error_details=exc.details,
                )
            return 1
        except (SymmetryValidationError, UnknownEngineError, ValueError) as exc:
            print(f"Solve refused: {exc}", file=stderr)
            if isinstance(exc, SymmetryValidationError):
                code = "invalid_symmetry"
            elif isinstance(exc, UnknownEngineError):
                code = "unknown_engine"
            else:
                code = "invalid_request"
            if ndjson:
                write_outcome(
                    stdout,
                    status="refused",
                    client_request_id=request.client_request_id,
                    error_code=code,
                    error_stage="submission",
                    error_message=str(exc),
                )
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
                if ndjson:
                    write_outcome(
                        stdout,
                        status="interrupted",
                        job_id=job_id,
                        client_request_id=request.client_request_id,
                        error_code="interrupted",
                        error_stage="cancellation",
                        error_message="Solve interrupted by SIGINT",
                    )
                return 130
            interrupt_task.cancel()
            await asyncio.gather(interrupt_task, return_exceptions=True)
            job = terminal_task.result()

        await _wait_for_stream(transport.wait_terminal(), protocol_task)
        results: str | None = None
        output_summary: dict[str, Any] | None = None
        if job["status"] == "complete":
            try:
                results = await runtime.get_results_text(job_id)
            except (JobConflictError, JobResourceUnavailableError) as exc:
                print(f"Could not fetch solve results: {exc}", file=stderr)
                if ndjson:
                    write_outcome(
                        stdout,
                        status="failed",
                        job_id=job_id,
                        client_request_id=request.client_request_id,
                        error_code="result_unavailable",
                        error_stage="result",
                        error_message=str(exc),
                        retryable=True,
                    )
                return 1
        if results is not None and args.output is not None:
            try:
                output_summary = await _write_output(
                    args.output,
                    results=results,
                    runtime=runtime,
                    job=job,
                    submitted_request=request,
                    stderr=stderr,
                )
            except FileExistsError:
                print(
                    f"Solve refused: output directory already exists: {args.output}",
                    file=stderr,
                )
                if ndjson:
                    write_outcome(
                        stdout,
                        status="failed",
                        job_id=job_id,
                        client_request_id=request.client_request_id,
                        output_directory=str(args.output),
                        error_code="output_exists",
                        error_stage="output",
                        error_message=f"output directory already exists: {args.output}",
                    )
                return 1
            except (OSError, ValueError) as exc:
                print(f"Could not write solve output: {exc}", file=stderr)
                if ndjson:
                    write_outcome(
                        stdout,
                        status="failed",
                        job_id=job_id,
                        client_request_id=request.client_request_id,
                        output_directory=str(args.output),
                        error_code="output_write_failed",
                        error_stage="output",
                        error_message=str(exc),
                        retryable=True,
                    )
                return 1

        if not ndjson:
            _print_summary(job, output=args.output, stream=stdout)
        if job["status"] != "complete":
            message = job.get("error_message") or "solve did not complete"
            print(f"Solve failed: {message}", file=stderr)
            if ndjson:
                cancelled = job["status"] == "cancelled"
                write_outcome(
                    stdout,
                    status="cancelled" if cancelled else "failed",
                    job_id=job_id,
                    client_request_id=request.client_request_id,
                    error_code="cancelled" if cancelled else "solve_failed",
                    error_stage=str(job.get("stage") or "solve"),
                    error_message=message,
                )
            return 1
        if args.output is None:
            print(_DATABASE_ONLY_WARNING, file=stderr, flush=True)
        if ndjson:
            write_outcome(
                stdout,
                status="complete",
                job_id=job_id,
                client_request_id=request.client_request_id,
                output_directory=str(args.output) if args.output is not None else None,
                # This is always the exact stored/API response body. Output
                # formatting and optional artifacts must not change result
                # identity.
                result_sha256=_sha256_text(results or ""),
                artifacts=(
                    output_summary["artifacts"]
                    if output_summary is not None
                    else None
                ),
            )
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
