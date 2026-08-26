"""Reusable, killable process boundary for native BEMPP sweeps.

The BEMPP numerical stack cannot cooperatively stop while one dense frequency
case is inside assembly or factorisation.  Running that work in the API process
therefore made Stop wait for the current case.  This module keeps one warm child
alive across successful jobs (so bempp/numba import and JIT costs are amortised),
but terminates the child when the parent cancellation callback raises.

Only the native solve crosses this boundary.  Meshing and durable artifact
publication remain in the parent, and stage/provisional-result events are
forwarded over the same pipe in order.

Two things follow from the child being the process that actually solves, and
both are what makes the first solve fast:

* **The initialization cost belongs to the child, so the warmup does too.**
  ``server/solver/warmup.py`` originally ran its warmup solve in the API
  process.  Measured on the reference Windows VM that bought exactly nothing:
  the parent spent 24.2 s warming itself and the first *child* solve still took
  24.3 s, because bempp-cl's hot numba kernels are declared without
  ``cache=True`` and the JIT therefore runs again in every new interpreter.
  ``prewarm()`` hands the child a ``_WARMUP_JOB_ID`` command as its first work
  item, so the child pays that cost at boot, in the process that will reuse it.

* **The child answers warmup.py's shutdown objection.**  That module keeps its
  in-parent warmup opt-in because a daemon thread abandoned inside native code
  cannot be stopped, so Quit could hang for the rest of the initialization
  block.  A child can: ``_terminate_sync`` reaches ``TerminateProcess`` (and
  ``SIGKILL``) which do not need the native code to cooperate, and ``close()``
  bounds the graceful attempt at ``_JOIN_SECONDS`` before doing so.  The
  objection is answered for this path and only for this path -- Metal still
  solves in-parent, so warmup.py still gates that branch.

A cancelled solve rebuilds the warm child eagerly rather than at the next
solve.  Killing the child is the only bounded way to interrupt assembly, and
without an eager respawn the whole initialization cost simply moves onto
whatever the user does next -- measured at 24.5 s on the solve that followed a
Stop.
"""

from __future__ import annotations

import atexit
import asyncio
import multiprocessing
from multiprocessing.connection import Connection
import os
import threading
import time
import traceback
from typing import Any, Callable, Mapping
import uuid

from server.platform.process_tree import (
    adopt_process_group,
    kill_own_process_group,
    confine_to_windows_job,
    kill_process_group,
    resolve_process_group,
)

from .base import CancelCallback, ResultCallback, StageCallback
from .context import SolverContext


_POLL_SECONDS = 0.05
_JOIN_SECONDS = 0.5

#: Sent as a command's job id to ask the child to pay its one-off native
#: initialization cost now.  A distinct command rather than an argument to the
#: process target keeps the spawn plumbing -- and the ``target=`` seam the
#: tests use -- untouched, and makes "is this worker warm yet" observable.
_WARMUP_JOB_ID = "__warmup__"

#: Exit status the worker uses when it leaves because the parent went away.
#: Nothing reads it -- the parent is gone -- but it keeps the reason legible in
#: a process monitor rather than looking like a clean exit or a crash.
_PARENT_GONE_EXIT_CODE = 3


class BemppWorkerError(RuntimeError):
    """A native BEMPP worker failed or exited without a result."""


def _poll_and_recv(connection: Connection) -> tuple[Any, ...] | None:
    """Wait briefly for and deserialize one worker event off the event loop."""

    if not connection.poll(_POLL_SECONDS):
        return None
    return connection.recv()


def _warm_this_process() -> dict[str, Any]:
    """Pay the native initialization cost here, in the process that will reuse it.

    Never raises.  A host with no usable assembly backend will fail the user's
    solve with a message attached to their job; failing the warmup instead
    would take the worker down before it ever served one, and would report the
    same fact somewhere nobody is looking.
    """

    began = time.monotonic()
    try:
        from .bempp import bempp_status
        from .warmup import warm_bempp_in_this_process

        warm_bempp_in_this_process(bempp_status())
    except BaseException as exc:  # noqa: BLE001 - a warmup is an optimisation
        return {"warmed": False, "detail": f"{type(exc).__name__}: {exc}"}
    return {"warmed": True, "seconds": round(time.monotonic() - began, 3)}


def _exit_when_parent_does() -> None:
    """Leave with the app, even from inside a native block.

    A launcher force-killed with TerminateProcess (or SIGKILL) runs no atexit
    hook and no lifespan shutdown, so nothing tells this worker to stop. It
    would find out at its next ``recv()`` -- but a worker in the middle of its
    warmup does not reach one until the warmup ends. Measured on the reference
    Windows VM with the server detached from the measuring process tree: the
    child kept burning CPU for **22.8 s** after the parent was killed. An
    earlier measurement of 0.16 s was wrong; it was the harness's own job
    object tearing the tree down, not this process noticing anything.

    Before the boot prewarm this only stranded a worker that was mid-solve, so
    it was at least finishing work somebody asked for. Now a worker exists from
    startup, so a force-kill of a session that never solved could leave one
    warming for nothing.

    Waiting on the parent's sentinel from a thread is not blocked by whatever
    the main thread is doing in native code, and ``os._exit`` does not wait for
    it either. Returns without arming when there is no parent process, which is
    how the tests drive this loop in-process.

    Leaving is no longer enough on POSIX. Now that the sweep may split, this
    process has children of its own, and ``os._exit`` does not terminate them;
    they survive as orphans burning every core the launcher's death was
    supposed to free. ``kill_own_process_group`` closes that, and only for a
    session this process actually claimed. Windows needs nothing here: the
    parent's job object is ``KILL_ON_JOB_CLOSE``, so the tree dies with it.
    """

    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def wait_and_exit() -> None:
        from multiprocessing.connection import wait

        wait([parent.sentinel])
        # Take the sweep's workers with us. ``os._exit`` runs no multiprocessing
        # cleanup, so without this they outlive the launcher -- and since this
        # process claimed its own session, the launcher's own group kill cannot
        # reach them either. No-op unless this process really did adopt a
        # session, so it cannot fire inside a test or on Windows, where the
        # parent's job object already tears the tree down.
        kill_own_process_group()
        os._exit(_PARENT_GONE_EXIT_CODE)

    threading.Thread(
        target=wait_and_exit, name="wg2-bempp-parent-watch", daemon=True
    ).start()


def _bempp_worker_main(connection: Connection) -> None:
    """Serve native solves in one warm process, one job at a time."""

    # Claim a POSIX session before any sweep worker is forked, so Stop can kill
    # this process *and* its workers as one group. No-op on Windows, where the
    # parent's job object contains the tree instead.
    adopt_process_group()
    _exit_when_parent_does()
    from .bempp import solve_bempp_from_msh_text

    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, payload = command
            if job_id == _WARMUP_JOB_ID:
                if connection.poll():
                    # A real solve is already queued behind this. It pays the
                    # same initialization and then keeps it, so warming first
                    # would only make that user wait out a stand-in solve
                    # before their own starts. This is the case an eager
                    # respawn hits: Stop, then immediately solve again.
                    connection.send(
                        ("warm", job_id, {"warmed": False, "detail": "superseded"})
                    )
                    continue
                connection.send(("warm", job_id, _warm_this_process()))
                continue

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
                    # Not forced serial any more. The original reason was that
                    # Stop could not cancel a frequency already inside a worker
                    # process -- but that predates this module. Stop now kills
                    # this child, and ``server/platform/process_tree.py`` makes
                    # that reach the sweep workers too, so the sweep can use
                    # every core without giving up a bounded cancel.
                    force_serial=False,
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
        #: Windows containment for the worker and its sweep workers; None
        #: elsewhere, where the POSIX process group serves the same purpose.
        self._job: Any = None
        self._warm_requested = False
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
            # NOT daemon. A daemonic multiprocessing process is forbidden from
            # having children at all -- ``start()`` asserts on it -- and the
            # native sweep splits with a ProcessPoolExecutor. With daemon=True
            # every split sweep dies with "daemonic processes are not allowed to
            # have children", which made the parallel default unusable for
            # exactly the long sweeps it was meant to speed up.
            #
            # What daemon=True bought was teardown when the parent exits, and
            # three mechanisms already cover that better: atexit -> _HOST.close()
            # for a normal exit (registered after multiprocessing's own hook, so
            # it runs first and terminates rather than joins), the sentinel
            # watchdog in _exit_when_parent_does for a force-kill, and the job
            # object / process group for the workers underneath.
            daemon=False,
        )
        process.start()
        child.close()
        # Contain the tree before any work reaches the child: a parallel sweep
        # forks its own workers, and Stop must reclaim all of them.
        self._job = confine_to_windows_job(process.pid) if process.pid else None
        self._connection = parent
        self._process = process
        return parent

    def _terminate_sync(self) -> None:
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        job, self._job = self._job, None
        self._warm_requested = False
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            if job is not None:
                job.terminate()
                job.close()
            return
        # Resolve the group while the child is still reapable: once it has been
        # joined, ``getpgid`` can no longer find it and the workers would be
        # unreachable.
        group = resolve_process_group(process.pid) if job is None and process.pid else None
        if process.is_alive():
            process.terminate()
            process.join(_JOIN_SECONDS)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(_JOIN_SECONDS)
        # The direct child is gone, but a parallel sweep's workers are not its
        # children by then -- they are siblings under the same job/group.
        # Reaching them is what keeps Stop honest once workers > 1.
        if job is not None:
            job.terminate()
            job.close()
        else:
            kill_process_group(group)
        if not process.is_alive():
            process.join()

    def _prewarm_locked(self) -> None:
        """Start the worker and queue its warmup.  Caller holds ``_state_lock``.

        Returns as soon as the command is on the pipe: the native work happens
        in the child, so nothing here blocks a caller, a startup handler or the
        event loop for the length of the warmup.
        """

        if self._active_job_id is not None or self._warm_requested:
            return
        try:
            connection = self._ensure_started()
            connection.send((_WARMUP_JOB_ID, None))
        except (BrokenPipeError, EOFError, OSError):
            # The worker died between spawn and send.  The next solve spawns a
            # fresh one and pays the cost then, exactly as it did before.
            return
        self._warm_requested = True

    def prewarm(self) -> None:
        """Have a warm worker ready before the first solve arrives.

        Idempotent per child: a worker that has already been asked to warm is
        left alone, so repeated calls (a second ``create_app``, a respawn that
        raced a startup handler) cost nothing and never queue a second warmup
        ahead of a user's job.
        """

        with self._state_lock:
            self._prewarm_locked()

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
                event = await asyncio.to_thread(_poll_and_recv, connection)
                if event is None:
                    continue
                kind, event_job_id, value = event
                if kind == "warm":
                    # A prewarm acknowledgement from before this job was sent.
                    # It is diagnostic only; the solve is unaffected either way.
                    continue
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
        except BaseException as exc:
            # A callback exception includes the runtime's cancellation sentinel.
            # Killing is the only bounded way to interrupt assembly/factorisation.
            await asyncio.to_thread(self._terminate_sync)
            with self._state_lock:
                self._active_job_id = None
            if not isinstance(exc, asyncio.CancelledError):
                # Rebuild the warm child now.  Waiting until the next solve puts
                # the whole initialization cost the kill just threw away onto
                # the user's next action -- 24.5 s on the reference Windows VM.
                # asyncio.CancelledError is the exception: that is the server
                # going away, and resurrecting a worker into a closing app only
                # gives shutdown something else to kill.
                await asyncio.to_thread(self.prewarm)
            raise
        finally:
            with self._state_lock:
                self._active_job_id = None


_HOST = BemppProcessHost()
atexit.register(_HOST.close)


def prewarm_bempp_process() -> None:
    """Warm the application-wide BEMPP worker.  Never blocks, never raises."""

    _HOST.prewarm()


def shutdown_bempp_process() -> None:
    """Stop the application-wide BEMPP worker.  Bounded by ``_JOIN_SECONDS``."""

    _HOST.close()


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
    "prewarm_bempp_process",
    "shutdown_bempp_process",
    "solve_bempp_in_process",
]
