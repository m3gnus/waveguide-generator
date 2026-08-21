"""Reusable, killable process boundary for native BEMPP sweeps.

The BEMPP numerical stack cannot cooperatively stop while one dense frequency
case is inside assembly or factorisation.  Running that work in the API process
therefore made Stop wait for the current case.  This module keeps one warm child
alive across successful jobs (so bempp/numba import and JIT costs are amortised),
but terminates the child when the parent cancellation callback raises.

Only the native solve crosses this boundary.  Meshing and durable artifact
publication remain in the parent, and stage/provisional-result events are
forwarded over the same pipe in order.
"""

from __future__ import annotations

import atexit
import asyncio
import multiprocessing
from multiprocessing.connection import Connection
import threading
import traceback
from typing import Any, Callable, Mapping
import uuid

from .base import CancelCallback, ResultCallback, StageCallback
from .context import SolverContext


_POLL_SECONDS = 0.05
_JOIN_SECONDS = 0.5


class BemppWorkerError(RuntimeError):
    """A native BEMPP worker failed or exited without a result."""


def _bempp_worker_main(connection: Connection) -> None:
    """Serve native solves serially in one warm process."""

    from .bempp import solve_bempp_from_msh_text

    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, payload = command

            def stage(stage_name: str, progress: float, message: str) -> None:
                connection.send(
                    ("stage", job_id, (stage_name, float(progress), str(message)))
                )

            def result(index: int, response: dict[str, Any]) -> None:
                connection.send(("result", job_id, (int(index), response)))

            try:
                response = solve_bempp_from_msh_text(
                    payload["msh_text"],
                    payload["context"],
                    mesh_metadata=payload.get("mesh_metadata"),
                    mesh_stats=payload.get("mesh_stats"),
                    field_trace_cap_bytes=payload.get("field_trace_cap_bytes"),
                    stage_callback=stage,
                    result_callback=result,
                    force_serial=True,
                )
            except BaseException as exc:  # noqa: BLE001 - report native failures
                connection.send(
                    (
                        "error",
                        job_id,
                        {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )
                )
            else:
                connection.send(("done", job_id, response))
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


class BemppProcessHost:
    """Own one reusable native worker and provide an async event bridge."""

    def __init__(
        self,
        *,
        process_context: multiprocessing.context.BaseContext | None = None,
        target: Callable[[Connection], None] = _bempp_worker_main,
    ) -> None:
        self._context = process_context or multiprocessing.get_context("spawn")
        self._target = target
        self._connection: Connection | None = None
        self._process: multiprocessing.Process | None = None
        self._active_job_id: str | None = None
        self._state_lock = threading.Lock()

    def _ensure_started(self) -> Connection:
        process = self._process
        connection = self._connection
        if process is not None and process.is_alive() and connection is not None:
            return connection
        self._terminate_sync()
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._target,
            args=(child,),
            name="hornlab-bempp-worker",
            daemon=True,
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process
        return parent

    def _terminate_sync(self) -> None:
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(_JOIN_SECONDS)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(_JOIN_SECONDS)
        if not process.is_alive():
            process.join()

    def close(self) -> None:
        """Stop the reusable worker during application/interpreter shutdown."""

        with self._state_lock:
            connection = self._connection
            process = self._process
            if connection is not None and process is not None and process.is_alive():
                try:
                    connection.send(None)
                    process.join(_JOIN_SECONDS)
                except (BrokenPipeError, EOFError, OSError):
                    pass
            self._terminate_sync()
            self._active_job_id = None

    async def run(
        self,
        msh_text: str,
        context: SolverContext,
        *,
        mesh_metadata: Mapping[str, Any] | None,
        mesh_stats: Mapping[str, Any] | None,
        field_trace_cap_bytes: int | None = None,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        result_cb: ResultCallback | None,
    ) -> dict[str, Any]:
        """Run one solve, killing the worker if cancellation is requested."""

        with self._state_lock:
            if self._active_job_id is not None:
                raise BemppWorkerError(
                    "The BEMPP worker already has an active solve; the runtime "
                    "must serialize BEMPP jobs"
                )
            job_id = uuid.uuid4().hex
            self._active_job_id = job_id
            connection = self._ensure_started()

        payload = {
            "msh_text": msh_text,
            "context": context,
            "mesh_metadata": dict(mesh_metadata or {}),
            "mesh_stats": dict(mesh_stats or {}),
            "field_trace_cap_bytes": field_trace_cap_bytes,
        }
        try:
            await asyncio.to_thread(connection.send, (job_id, payload))
            while True:
                cancel_cb()
                if not self._process or not self._process.is_alive():
                    raise BemppWorkerError(
                        "The native BEMPP worker exited before returning a result"
                    )
                ready = await asyncio.to_thread(connection.poll, _POLL_SECONDS)
                if not ready:
                    continue
                kind, event_job_id, value = connection.recv()
                if event_job_id != job_id:
                    raise BemppWorkerError(
                        "The native BEMPP worker returned an event for the wrong job"
                    )
                if kind == "stage":
                    stage_cb(*value)
                elif kind == "result":
                    if result_cb is not None:
                        result_cb(*value)
                elif kind == "done":
                    return value
                elif kind == "error":
                    raise BemppWorkerError(
                        f"Native BEMPP worker failed ({value['type']}): "
                        f"{value['message']}\n{value['traceback']}"
                    )
                else:
                    raise BemppWorkerError(
                        f"Native BEMPP worker returned unknown event {kind!r}"
                    )
        except BaseException:
            # A callback exception includes the runtime's cancellation sentinel.
            # Killing is the only bounded way to interrupt assembly/factorisation.
            await asyncio.to_thread(self._terminate_sync)
            raise
        finally:
            with self._state_lock:
                self._active_job_id = None


_HOST = BemppProcessHost()
atexit.register(_HOST.close)


async def solve_bempp_in_process(
    msh_text: str,
    context: SolverContext,
    *,
    mesh_metadata: Mapping[str, Any] | None,
    mesh_stats: Mapping[str, Any] | None,
    field_trace_cap_bytes: int | None = None,
    cancel_cb: CancelCallback,
    stage_cb: StageCallback,
    result_cb: ResultCallback | None,
) -> dict[str, Any]:
    """Use the application-wide warm, killable BEMPP process."""

    return await _HOST.run(
        msh_text,
        context,
        mesh_metadata=mesh_metadata,
        mesh_stats=mesh_stats,
        field_trace_cap_bytes=field_trace_cap_bytes,
        cancel_cb=cancel_cb,
        stage_cb=stage_cb,
        result_cb=result_cb,
    )


__all__ = [
    "BemppProcessHost",
    "BemppWorkerError",
    "solve_bempp_in_process",
]
