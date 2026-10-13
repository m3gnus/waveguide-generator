"""Reusable, killable process boundary for native BEMPP sweeps.

The BEMPP numerical stack cannot cooperatively stop while one dense frequency
case is inside assembly or factorisation.  Running that work in the API process
therefore made Stop wait for the current case.  This module keeps one warm child
alive across successful jobs (so bempp/numba import and JIT costs are amortised),
but terminates the child when the parent cancellation callback raises.

Native solves and retained-field evaluation cross this boundary. Meshing and
durable artifact publication remain in the parent, and stage/provisional-result events are
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
import logging
import multiprocessing
from multiprocessing.connection import Connection
import threading
import time
import traceback
from typing import Any, Callable, Mapping
import uuid

from server.platform import child_process
from server.platform.child_process import close_quietly as _close_quietly
from server.platform.job_start import ContainedStartError, start_in_windows_job
from server.platform.process_tree import (
    adopt_process_group,
    confine_to_windows_job,
    kill_process_group,
)

from .base import CancelCallback, ResultCallback, StageCallback
from .context import SolverContext


log = logging.getLogger("wg.solve")

_POLL_SECONDS = 0.05
_JOIN_SECONDS = 0.5

#: Sent as a command's job id to ask the child to pay its one-off native
#: initialization cost now.  A distinct command rather than an argument to the
#: process target keeps the spawn plumbing -- and the ``target=`` seam the
#: tests use -- untouched, and makes "is this worker warm yet" observable.
_WARMUP_JOB_ID = "__warmup__"

#: The worker's first message, sent before it reads any command:
#: ``(_GROUP_EVENT, None, group)``, where ``group`` is the POSIX process group it
#: claimed (its pid) or ``None``. Sweep children only exist once a command has
#: been read, so the announcement is always ahead of them in the pipe, and it
#: stays readable there after the worker has died and been reaped. The parent
#: therefore never resolves the group from outside: ``getpgid`` races the
#: worker's ``setsid`` and fails once the worker is reaped, and a worker that
#: claimed its group, started a sweep and died before the parent's first poll
#: left that sweep running.
_GROUP_EVENT = "group"

#: How long teardown waits for an in-flight pipe read to finish before it
#: decides which group the dead worker announced. The worker is dead by then,
#: so the read ends with its message or with EOF; this only bounds a surprise.
_PIPE_LOCK_SECONDS = 5.0

#: Exit status the worker uses when it leaves because the parent went away.
#: Nothing reads it -- the parent is gone -- but it keeps the reason legible in
#: a process monitor rather than looking like a clean exit or a crash.
_PARENT_GONE_EXIT_CODE = 3


class BemppWorkerError(RuntimeError):
    """A native BEMPP worker failed or exited without a result."""


UNCONTAINED_MESSAGE = (
    "The BEMPP solver could not start: Windows would not place its worker in a job "
    "object, and without one it could keep running after the app quits. It was "
    "stopped, so nothing was solved. Restart the app and try again; if this keeps "
    "happening, the server log names the reason."
)


# -- what a dead worker leaves behind ------------------------------------------
#
# A native crash inside the worker -- PoCL's LLVM aborting with "Cannot select
# ... fsqrt" on some CPUs, an access violation in a kernel -- raises no Python
# exception, so nothing crosses the pipe. The parent only sees the pipe close
# (EOFError, whose ``str()`` is empty: that empty string once became the job's
# whole error message) or the process gone. What says *why* is the exit status
# and whatever the native code printed on its way down, and the printing goes to
# file descriptor 2, not to Python. So the worker's fd 2 goes to a file the
# parent reads (a file, not a pipe: a pipe nobody drains while the parent is
# busy would block the child once full, and the bytes still in a pipe die with
# a crashed reader). The parent forwards it to its own stderr as it arrives, so
# nothing that used to reach the console stops reaching it, and keeps a bounded
# tail for the failure message.

#: Bytes of the worker's most recent stderr kept in memory for a failure message.
_STDERR_TAIL_BYTES = 16 * 1024
#: Above this, the parent empties the file after forwarding it, so a chatty
#: worker that lives for the whole app session cannot fill the disk.
_STDERR_FILE_CAP_BYTES = 4 * 1024 * 1024
#: How much of that tail a job's error message may carry.
_STDERR_MESSAGE_LINES = 12
_STDERR_MESSAGE_LINE_CHARS = 300
_STDERR_MESSAGE_CHARS = 2000
#: How long to wait for a worker whose pipe closed to be reaped, so its exit
#: status can be read. A crashed process is gone within milliseconds.
_DEATH_JOIN_SECONDS = 2.0

#: Windows NTSTATUS values a crashed worker is likely to exit with.
_NTSTATUS_NAMES = {
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC0000017: "out of memory",
    0xC0000094: "integer division by zero",
    0xC00000FD: "stack overflow",
    0xC000013A: "interrupted with Ctrl+C",
    0xC0000374: "heap corruption",
    0xC0000409: "fail-fast exception (stack buffer overrun)",
    0xC0000420: "assertion failure",
}


_EXIT_WORDING = child_process.ExitWording(
    unknown="its exit status could not be read",
    signal="it was stopped by signal {name} ({number})",
    unknown_signal="it was stopped by signal {number}",
    code="it ended with exit code {code}",
    status="it ended with status 0x{code:08X}",
    status_names=_NTSTATUS_NAMES,
)


def _describe_exit(exitcode: int | None, *, windows: bool | None = None) -> str:
    return child_process.describe_exit(exitcode, wording=_EXIT_WORDING, windows=windows)


def _stderr_excerpt(raw: bytes, *, truncated: bool = False) -> str:
    return child_process.stderr_excerpt(
        raw, truncated=truncated, lines_limit=_STDERR_MESSAGE_LINES,
        line_chars=_STDERR_MESSAGE_LINE_CHARS, chars_limit=_STDERR_MESSAGE_CHARS,
    )


def _worker_death_message(exitcode: int | None, stderr_tail: str) -> str:
    """The job's error for a worker that died without reporting a result.

    Never empty: an unknown exit still says what stopped.
    """

    message = (
        "The BEMPP solve process exited unexpectedly before returning a result: "
        f"{_describe_exit(exitcode)}."
    )
    if stderr_tail:
        message += f"\nLast output from the solve process:\n{stderr_tail}"
    else:
        message += " It printed nothing before it stopped."
    return message


class _WorkerStderr(child_process.FileStderr):
    def __init__(self, path: str) -> None:
        super().__init__(
            path, tail_bytes=_STDERR_TAIL_BYTES, file_cap_bytes=_STDERR_FILE_CAP_BYTES,
            excerpt=_stderr_excerpt,
        )


def _new_stderr_file() -> str | None:
    return child_process.new_stderr_file(prefix="wg2-bempp-stderr-", suffix=".log")


def _poll_and_recv(connection: Connection) -> tuple[Any, ...] | None:
    """Wait briefly for and deserialize one worker event off the event loop."""

    if not connection.poll(_POLL_SECONDS):
        return None
    return connection.recv()


def _poll_and_drain(
    connection: Connection, stderr: _WorkerStderr | None
) -> tuple[Any, ...] | None:
    """:func:`_poll_and_recv`, forwarding the worker's stderr between events."""

    if stderr is not None:
        stderr.drain()
    return _poll_and_recv(connection)


def _group_in(event: Any) -> tuple[bool, int | None]:
    """``(True, group)`` for a group announcement, ``(False, None)`` otherwise."""

    if isinstance(event, tuple) and len(event) == 3 and event[0] == _GROUP_EVENT:
        group = event[2]
        return True, group if isinstance(group, int) else None
    return False, None


def _worker_death_error(
    process: multiprocessing.process.BaseProcess | None, stderr: _WorkerStderr | None
) -> BemppWorkerError:
    """Describe a worker that stopped without a result. Blocks briefly; call off-loop.

    Reaps the worker to read its exit status. That costs nothing on the way to
    its POSIX process group: the worker announced the group over the pipe, and
    ``_terminate_sync`` uses that announcement, not the reaped pid.
    """

    exitcode = child_process.worker_exitcode(process, join_seconds=_DEATH_JOIN_SECONDS)
    excerpt = stderr.excerpt() if stderr is not None else ""
    return BemppWorkerError(_worker_death_message(exitcode, excerpt))


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
    child_process.exit_when_parent_does(
        exit_code=_PARENT_GONE_EXIT_CODE, thread_name="wg2-bempp-parent-watch"
    )


def _solve_payload(
    payload: Mapping[str, Any],
    *,
    stage: Callable[[str, float, str], None],
    result: Callable[[int, dict[str, Any]], None],
) -> Any:
    """Run native design/imported solves or retained-field evaluation here."""

    if payload.get("kind") == "field":
        from .bempp_field import evaluate_bempp_field_payload

        return evaluate_bempp_field_payload(payload)
    if payload.get("kind") == "imported":
        from .bempp_imported import solve_imported_bempp_from_msh_text

        return solve_imported_bempp_from_msh_text(
            payload["msh_text"],
            payload["request"],
            payload["record"],
            field_trace_cap_bytes=payload.get("field_trace_cap_bytes"),
            stage_callback=stage,
            result_callback=result,
        )
    from .bempp import solve_bempp_from_msh_text

    return solve_bempp_from_msh_text(
        payload["msh_text"],
        payload["context"],
        mesh_metadata=payload.get("mesh_metadata"),
        mesh_stats=payload.get("mesh_stats"),
        field_trace_cap_bytes=payload.get("field_trace_cap_bytes"),
        stage_callback=stage,
        result_callback=result,
        # Not forced serial any more. The original reason was that Stop could
        # not cancel a frequency already inside a worker process -- but that
        # predates this module. Stop now kills this child, and
        # ``server/platform/process_tree.py`` makes that reach the sweep
        # workers too, so the sweep can use every core without giving up a
        # bounded cancel.
        force_serial=False,
    )


def _start_in_parent_session(
    target: Callable[[Connection], None],
    session_root: str | None,
    connection: Connection,
    stderr_path: str | None = None,
) -> None:
    """The spawned worker's entry: capture stderr, adopt the server's session, then serve."""

    from server.platform.running_exclusion import hold_running_exclusion
    from server.platform.temp_session import adopt_parent_session

    child_process.send_stderr_to(stderr_path)
    hold_running_exclusion()
    adopt_parent_session(session_root)
    target(connection)


def _bempp_worker_main(connection: Connection) -> None:
    """Serve native solves in one warm process, one job at a time."""

    # Claim a POSIX session before any sweep worker is forked, so Stop can kill
    # this process *and* its workers as one group. No-op on Windows, where the
    # parent's job object contains the tree instead.
    group = adopt_process_group()
    _exit_when_parent_does()

    try:
        # Tell the parent which group to kill before reading any command, so
        # the announcement precedes every sweep child (see _GROUP_EVENT).
        connection.send((_GROUP_EVENT, None, group))
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
                response = _solve_payload(payload, stage=stage, result=result)
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
        #: The worker's redirected stderr; None when it could not be set up.
        self._stderr: _WorkerStderr | None = None
        #: ``(connection, group)`` once the worker on that pipe announced its
        #: POSIX process group (``_GROUP_EVENT``). Recorded by the thread that
        #: read it, so a cancelled await cannot lose it.
        self._announcement: tuple[Connection, int | None] | None = None
        #: Held across every receive and the record that follows it, so
        #: teardown never sees a message read but its announcement unrecorded.
        self._pipe_lock = threading.Lock()
        self._warm_requested = False
        self._state_lock = threading.Lock()
        self._close_generation = 0

    def _ensure_started(self) -> Connection:
        process = self._process
        connection = self._connection
        if process is not None and process.is_alive() and connection is not None:
            return connection
        self._terminate_sync()
        parent, child = self._context.Pipe(duplex=True)
        from server.platform.temp_session import temporary_directory_root

        stderr_path = _new_stderr_file()
        stderr = _WorkerStderr(stderr_path) if stderr_path else None
        try:
            process = self._context.Process(
                # The worker has no session of its own: hand it the server's, so
                # the directories it makes (its OpenCL check's) live and die there.
                target=_start_in_parent_session,
                args=(self._target, temporary_directory_root(), child, stderr_path),
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
            # Contain the tree before the child can run: a parallel sweep
            # forks its own workers, and Stop must reclaim all of them, whether
            # or not the image started is a launcher stub
            # (server/platform/job_start.py). A worker no job can hold is
            # stopped before it ran, and this solve fails with that reason.
            job = start_in_windows_job(
                process, confine=confine_to_windows_job, subject="the BEMPP worker"
            )
        except BaseException as exc:
            if stderr is not None:
                stderr.discard()
            if isinstance(exc, ContainedStartError):
                _close_quietly(parent)
                _close_quietly(child)
                raise BemppWorkerError(UNCONTAINED_MESSAGE) from exc
            raise
        child.close()
        self._job = job
        self._connection = parent
        self._process = process
        self._stderr = stderr
        return parent

    def _terminate_sync(self) -> None:
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        job, self._job = self._job, None
        stderr, self._stderr = self._stderr, None
        self._warm_requested = False
        if process is None:
            _close_quietly(connection)
            if job is not None:
                job.terminate()
                job.close()
            if stderr is not None:
                stderr.discard()
            return
        child_process.terminate_worker(process, join_seconds=_JOIN_SECONDS)
        group = (
            self._announced_group(connection)
            if job is None and connection is not None
            else None
        )
        self._announcement = None
        _close_quietly(connection)
        # The direct child is gone, but a parallel sweep's workers are not its
        # children by then -- they are siblings under the same job/group.
        # Reaching them is what keeps Stop honest once workers > 1.
        child_process.finish_worker_tree(
            process, job, group, kill_group=kill_process_group
        )
        if stderr is not None:
            stderr.discard()

    def _record_if_announcement(self, connection: Connection, event: Any) -> bool:
        """Record a group announcement read off ``connection``. Caller holds the pipe lock."""

        announced, group = _group_in(event)
        if announced:
            self._announcement = (connection, group)
        return announced

    def _receive(
        self, connection: Connection, stderr: _WorkerStderr | None
    ) -> tuple[Any, ...] | None:
        """The next event for the solve loop; runs on a worker thread.

        A group announcement is recorded here, in the thread that read it, and
        never handed back: an ``await`` cancelled while this thread was reading
        discards its result, and with it any assignment a caller would make.
        """

        with self._pipe_lock:
            event = _poll_and_drain(connection, stderr)
            if event is not None and self._record_if_announcement(connection, event):
                return None
            return event

    def _announced_group(self, connection: Connection) -> int | None:
        """The group the (now dead) worker on ``connection`` announced, if any.

        Waits out a receive still in flight, then trusts only an announcement:
        if none was recorded, everything consumed so far went through
        ``_receive`` and was not one, so the first unread message is the
        worker's first message. Only that message is read, and only a group
        announcement names a group. A worker that died before sending it never
        read a command, so it never started a sweep child.
        """

        locked = self._pipe_lock.acquire(timeout=_PIPE_LOCK_SECONDS)
        try:
            announcement = self._announcement
            if announcement is not None and announcement[0] is connection:
                return announcement[1]
            if not locked:
                return None
            try:
                if not connection.poll(0):
                    return None
                event = connection.recv()
            except Exception:  # noqa: BLE001 - a torn pipe means nothing was announced
                return None
            announced, group = _group_in(event)
            return group if announced else None
        finally:
            if locked:
                self._pipe_lock.release()

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
        except BemppWorkerError as exc:
            # No job could hold the worker. A prewarm also runs after a failed
            # solve, where raising would mask that solve's own error; the next
            # solve reports this one.
            log.warning("BEMPP worker not prewarmed: %s (%s)", exc, exc.__cause__)
            return
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
            self._close_generation += 1
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

        return await self._run_payload(
            {
                "msh_text": msh_text,
                "context": context,
                "mesh_metadata": dict(mesh_metadata or {}),
                "mesh_stats": dict(mesh_stats or {}),
                "field_trace_cap_bytes": field_trace_cap_bytes,
            },
            cancel_cb=cancel_cb,
            stage_cb=stage_cb,
            result_cb=result_cb,
        )

    async def run_imported(
        self,
        msh_text: str,
        request: Any,
        record: Mapping[str, Any],
        *,
        field_trace_cap_bytes: int | None = None,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        result_cb: ResultCallback | None,
    ) -> dict[str, Any]:
        """Run one imported-CAD solve in the same killable worker."""

        return await self._run_payload(
            {
                "kind": "imported",
                "msh_text": msh_text,
                "request": request,
                "record": dict(record),
                "field_trace_cap_bytes": field_trace_cap_bytes,
            },
            cancel_cb=cancel_cb,
            stage_cb=stage_cb,
            result_cb=result_cb,
        )

    async def _run_payload(
        self,
        payload: dict[str, Any],
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        result_cb: ResultCallback | None,
        rewarm_on_cancel: bool = False,
    ) -> Any:
        with self._state_lock:
            generation = self._close_generation
            if self._active_job_id is not None:
                raise BemppWorkerError(
                    "The BEMPP worker already has an active solve; the runtime "
                    "must serialize BEMPP jobs"
                )
            job_id = uuid.uuid4().hex
            self._active_job_id = job_id
            try:
                connection = self._ensure_started()
            except BaseException:
                # Not started, so not active: otherwise every later solve is
                # refused as overlapping this one.
                self._active_job_id = None
                raise
            process = self._process
            stderr = self._stderr

        try:
            if stderr is not None:
                await asyncio.to_thread(stderr.clear_tail)
            try:
                await asyncio.to_thread(connection.send, (job_id, payload))
            except (EOFError, BrokenPipeError, OSError) as exc:
                raise await asyncio.to_thread(
                    _worker_death_error, process, stderr
                ) from exc
            while True:
                cancel_cb()
                if process is None or not process.is_alive():
                    raise await asyncio.to_thread(_worker_death_error, process, stderr)
                try:
                    event = await asyncio.to_thread(self._receive, connection, stderr)
                except (EOFError, BrokenPipeError, OSError) as exc:
                    # A native crash closes the pipe without a word; the
                    # exception's own text is empty. Say what stopped and why.
                    raise await asyncio.to_thread(
                        _worker_death_error, process, stderr
                    ) from exc
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
            if rewarm_on_cancel or not isinstance(exc, asyncio.CancelledError):
                # Rebuild the warm child now.  Waiting until the next solve puts
                # the whole initialization cost the kill just threw away onto
                # the user's next action -- 24.5 s on the reference Windows VM.
                # Solver task cancellation usually means shutdown. Field replay
                # also cancels on its HTTP deadline, so it opts into rewarming.
                # A close generation prevents that rewarm resurrecting a worker
                # after application shutdown has closed this host.
                def rewarm_if_open():
                    with self._state_lock:
                        if self._close_generation == generation:
                            self._prewarm_locked()
                await asyncio.to_thread(rewarm_if_open)
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


async def evaluate_field_bempp_in_process(payload: Mapping[str, Any]) -> Any:
    """Evaluate in the solve worker; cancellation/timeout kills its native work."""
    return await _HOST._run_payload(
        {**payload, "kind": "field"},
        cancel_cb=lambda: None, stage_cb=lambda *_args: None, result_cb=None,
        rewarm_on_cancel=True,
    )


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


async def solve_imported_bempp_in_process(
    msh_text: str,
    request: Any,
    record: Mapping[str, Any],
    *,
    field_trace_cap_bytes: int | None = None,
    cancel_cb: CancelCallback,
    stage_cb: StageCallback,
    result_cb: ResultCallback | None,
) -> dict[str, Any]:
    """Solve an imported CAD record in the application-wide killable worker."""

    return await _HOST.run_imported(
        msh_text,
        request,
        record,
        field_trace_cap_bytes=field_trace_cap_bytes,
        cancel_cb=cancel_cb,
        stage_cb=stage_cb,
        result_cb=result_cb,
    )


__all__ = [
    "BemppProcessHost",
    "BemppWorkerError",
    "UNCONTAINED_MESSAGE",
    "prewarm_bempp_process",
    "shutdown_bempp_process",
    "solve_bempp_in_process",
    "solve_imported_bempp_in_process",
]
