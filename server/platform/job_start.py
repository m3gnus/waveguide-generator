"""Put a Windows child in its job object before it can run a single instruction.

In the Windows bundle ``sys.executable`` is the native entry stub
(``launchers/windows/startup_hook.py``), and the stub starts the real
interpreter, ``wg-python.exe``, as *its* child, handing it the same pipes
(``launchers/windows/launcher.c`` ``start()``). Every site that starts a child
and then calls ``AssignProcessToJobObject`` on it races that stub: once the
stub has created its interpreter, assigning the stub no longer reaches the
interpreter, which then lives outside every job. Killing the stub, or the job,
leaves that interpreter running and holding the pipes the parent reads.

The cure is ordering, not speed: create the child suspended, assign it while
it cannot run, then resume it. Whatever it starts afterwards inherits the job,
because the stub does not ask for ``CREATE_BREAKAWAY_FROM_JOB``.

``subprocess.Popen`` would take ``CREATE_SUSPENDED`` itself, but it closes the
thread handle it would take to resume the child, and a ``multiprocessing``
spawn takes no creation flags at all. Both make exactly one
``_winapi.CreateProcess`` call, on the thread that asked for the process, so
this module arms *that thread* for *that one call*: :func:`windows_job_start`
adds ``CREATE_SUSPENDED``, hands the new process to the caller's ``assign``,
and resumes it with the thread handle ``CreateProcess`` returned. Every other
thread, and every call made while no start is armed, passes straight through
to the original function unchanged.

A ``multiprocessing`` child is not the stub today, by a CPython detail: on
Windows its spawn runs ``sys._base_executable`` whenever ``sys.executable``
differs from it (the venv redirect), and the bundle's start-up hook makes them
differ, so a bundled mesher child or BEMPP worker is ``wg-python.exe`` itself
(measured on an installed 0.3.4). Those two still start through here: their
containment then no longer rests on that detail, and nothing they start can
predate the job either.

A child is never handed over without a job. Whatever the caller asked for,
a job that cannot be made or assigned (``assign`` raising or returning
``None``) stops the child before it ever ran and raises
:class:`ContainedStartError`. Running it uncontained is not a fallback: a
mesher child or BEMPP worker outside every job, holding no
``WaveguideGeneratorRunning`` mutex either, can outlive a server that died
unseen by the installer, which then replaces ``app/`` under it.

``required`` chooses only what happens to a child that was confined but
cannot be resumed:

* ``required=True`` (the CAD child, the desktop's server): it is stopped
  before it ran and :class:`ContainedStartError` is raised.
* ``required=False`` (the mesher child, the BEMPP worker): it is stopped
  before it ran, started again the plain way and assigned after the fact.
  An assignment that then fails stops that child too and raises. That
  restart has run, so if its image were a launcher stub, an interpreter the
  stub started before the refusal would escape: the stop reaches the direct
  child only. The callers' direct child is ``wg-python.exe`` itself (above),
  and the path needs two failures in a row, a resume and then a job.

Outside Windows :func:`windows_job_start` does nothing, and its result reports
that it never fired.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("wg.process")

#: ``CREATE_SUSPENDED``: the primary thread does not run until resumed.
CREATE_SUSPENDED = 0x00000004

#: ``_winapi.CreateProcess(application, command, process_attributes,
#: thread_attributes, inherit_handles, creation_flags, environment,
#: current_directory, startup_info)``: the shape both standard callers use.
_CREATE_PROCESS_ARGUMENTS = 9
_CREATION_FLAGS_INDEX = 5

#: How long to wait for a never-resumed child to be gone after terminating it.
_DISCARD_WAIT_MS = 5000

#: ``assign(pid, process_handle)`` returns the job, or ``None`` when it has none.
Assign = Callable[[int, int], Any]


class ContainedStartError(RuntimeError):
    """No job could hold the child, so it was stopped and never handed over."""


@dataclass
class JobStart:
    """What an armed start produced.

    ``fired`` is False when no ``CreateProcess`` happened on the armed thread:
    off Windows, or when the caller's start never reached the real API (a test
    double). Callers then fall back to assigning after the fact.
    """

    job: Any = None
    fired: bool = False
    pid: int | None = None
    #: The handed-over child's process handle; the caller owns it.
    process_handle: Any = None


@dataclass
class _Armed:
    assign: Assign
    required: bool
    subject: str
    result: JobStart


_local = threading.local()
_install_lock = threading.Lock()


@contextlib.contextmanager
def windows_job_start(
    assign: Assign, *, required: bool, subject: str
) -> Iterator[JobStart]:
    """Arm this thread so the next process it creates starts inside a job.

    Use around exactly one ``subprocess.Popen(...)`` or
    ``multiprocessing.Process.start()``::

        with windows_job_start(assign, required=True, subject="the server") as started:
            process = subprocess.Popen(command)
        job = started.job

    The caller must not pass ``CREATE_SUSPENDED`` itself: the child is
    resumed once it is confined.

    The block must contain only that start: if it raises, the job made for
    it is terminated and closed, or without a job the child is terminated.

    ``assign(pid, process_handle)`` runs while the child is suspended. It
    returns the job (anything with ``close()``) or ``None``; ``None`` or an
    exception stops the child and raises :class:`ContainedStartError`,
    whatever ``required`` says. A failed
    ``CreateProcess`` (a refused breakaway, say) leaves the thread armed, so a
    caller's own retry inside the block is contained too.
    """

    result = JobStart()
    if os.name != "nt":
        yield result
        return
    _install()
    previous = getattr(_local, "armed", None)
    _local.armed = _Armed(assign, required, subject, result)
    try:
        yield result
    except BaseException:
        # The block is only the start, so a start that raised after the child
        # was handed over (an interrupt landing in Popen, say) left it with
        # nobody: end its tree, and without a job end the child itself. Its
        # handles are not closed here: they belong to the caller's Popen
        # now, which closes them itself, and a second close could hit a
        # reused handle value. Until that Popen is collected the process
        # handle is still open, so terminating through it is safe.
        job, result.job = result.job, None
        handle, result.process_handle = result.process_handle, None
        if job is not None:
            _close_job(job)
        elif handle is not None:
            import _winapi

            with contextlib.suppress(Exception):
                _winapi.TerminateProcess(int(handle), 1)
        raise
    else:
        result.process_handle = None
    finally:
        _local.armed = previous


def start_in_windows_job(
    process: Any, *, confine: Callable[[int], Any], subject: str
) -> Any:
    """``process.start()`` a ``multiprocessing`` child inside a job.

    ``confine(pid)`` makes and assigns the job (``None`` when it cannot). It
    runs while the child is suspended when the start reaches ``CreateProcess``
    on this thread; a ``None`` or an exception there stops the child before
    it ran and raises :class:`ContainedStartError` out of ``start()``, so
    ``process`` was never started. Otherwise ``confine`` runs after
    ``start()`` -- off Windows, where it returns ``None``, and for a test
    double that starts nothing real.
    """

    # A start that fails after CreateProcess (its arguments would not pickle,
    # say) leaves a child that never got its work; windows_job_start ends it.
    with windows_job_start(
        lambda pid, _handle: confine(pid), required=False, subject=subject
    ) as started:
        process.start()
    if started.fired:
        return started.job
    return confine(process.pid) if process.pid else None


def _install() -> None:
    import _winapi

    with _install_lock:
        current = _winapi.CreateProcess
        if getattr(current, "_wg_job_start", False):
            return
        _winapi.CreateProcess = _intercept(current)


def _intercept(real: Callable[..., Any]) -> Callable[..., Any]:
    def create_process(*args: Any, **kwargs: Any) -> Any:
        armed = getattr(_local, "armed", None)
        if armed is None:
            return real(*args, **kwargs)
        if kwargs or len(args) != _CREATE_PROCESS_ARGUMENTS:
            # Not the shape subprocess and multiprocessing use: start it as
            # asked, and say so, because the caller then falls back to
            # assigning after the start, which is the race this module exists
            # to close.
            logger.warning(
                "Starting %s without suspending it: CreateProcess was called "
                "with an unexpected shape (%d positional, %s keyword arguments).",
                armed.subject,
                len(args),
                sorted(kwargs),
            )
            return real(*args, **kwargs)
        # One shot: nothing else this thread starts inside the block, and no
        # call this one makes, is captured.
        _local.armed = None
        return _start_contained(real, args, armed)

    create_process._wg_job_start = True  # type: ignore[attr-defined]
    create_process.__wrapped__ = real  # type: ignore[attr-defined]
    return create_process


def _start_contained(real: Callable[..., Any], args: tuple[Any, ...], armed: _Armed) -> Any:
    """Create the child suspended, confine it, resume it, hand it over.

    The child is owned here from the moment ``CreateProcess`` returns until
    the single line that hands it to the caller. Any exception in between,
    an interrupt included, stops it and closes both its handles and its job.

    Accepted residual (Magnus, 2026-10-05): ``KeyboardInterrupt`` is raised
    asynchronously on the main thread, so one arriving in the few
    instructions between ``CreateProcess`` returning and its result being
    stored, or between the handoff and ``Popen`` storing the handles, can
    leak one child and its handles. Holding the signal across the start was
    tried and removed: re-delivering it opened a window of its own, and
    holding it around arbitrary ``assign`` code can deadlock, because signal
    handlers run only on the main thread. In the app such an interrupt comes
    only at server shutdown, where the job's kill-on-close takes the tree.

    The same class of window covers the cleanup below: an interrupt landing in
    it after a refusal can leave the suspended, never-resumed child and its
    handles behind. A refused child has no job for kill-on-close to take; it
    never ran, so it starts nothing, but it keeps its inherited handles.
    """

    flags = int(args[_CREATION_FLAGS_INDEX])
    suspended = list(args)
    suspended[_CREATION_FLAGS_INDEX] = flags | CREATE_SUSPENDED
    child = _Owned()
    try:
        child.handles = real(*suspended)
        _confine_and_resume(real, args, armed, child)
        return child.hand_over(armed.result)
    except BaseException:
        if child.handles is None:
            # Nothing was started (CreateProcess itself failed), so a retry
            # by the caller is still the start this block is about.
            _local.armed = armed
        # Refused, interrupted, or failed: a child we still own never reaches
        # the caller, so stop it and close its handles and job. A suspended
        # one never ran.
        child.discard()
        raise


def _confine_and_resume(
    real: Callable[..., Any], args: tuple[Any, ...], armed: _Armed, child: _Owned
) -> None:
    """Make ``child`` ready to hand over, or raise with it still owned."""

    process_handle, thread_handle, pid, _tid = child.handles
    armed.result.fired = True
    armed.result.pid = int(pid)
    # Never resumed without a job, whatever ``required`` says: raising leaves
    # the suspended child owned, so _start_contained stops it unrun.
    child.job = _confine(armed, int(pid), int(process_handle))
    if _resume(thread_handle):
        return
    if armed.required:
        raise ContainedStartError(f"could not resume {armed.subject} after confining it")

    # Not required, and the child could not be resumed. It never ran, so it
    # started nothing: stop it, then start it the plain way and confine it
    # afterwards, as before this module existed.
    child.discard()
    logger.warning(
        "Could not resume %s after starting it suspended; starting it again "
        "and confining it afterwards, so processes it starts first may outlive it.",
        armed.subject,
    )
    child.reset()
    child.handles = real(*args)
    process_handle, _thread, pid, _tid = child.handles
    armed.result.pid = int(pid)
    # The restarted child is already running; a failure here raises with it
    # still owned, so it is stopped rather than handed over without a job.
    child.job = _confine(armed, int(pid), int(process_handle))


def _confine(armed: _Armed, pid: int, process_handle: int) -> Any:
    """The child's job from ``armed.assign``, or :class:`ContainedStartError`."""

    try:
        job = armed.assign(pid, process_handle)
    except Exception as exc:  # noqa: BLE001 - any failure is a refusal
        raise ContainedStartError(
            f"could not confine {armed.subject} in a Windows job: {exc}"
        ) from exc
    if job is None:
        raise ContainedStartError(f"could not confine {armed.subject} in a Windows job")
    return job


class _Owned:
    """A child this module still owns: stopped on discard, exactly once.

    Until :meth:`hand_over` its handles never reach the caller, so this is
    the only place that may close them, and only once: a second close could
    hit a reused handle value.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.handles: tuple[Any, Any, Any, Any] | None = None
        self.job: Any = None
        self.released = False

    def hand_over(self, result: JobStart) -> tuple[Any, Any, Any, Any]:
        # One line and the last step, so no line event splits it: from here
        # the caller owns the handles and the job.
        self.released = True; result.job = self.job; result.process_handle = self.handles[0]; return self.handles  # type: ignore[index, return-value]  # noqa: E501, E702

    def discard(self) -> None:
        if self.released or self.handles is None:
            return
        self.released = True
        _close_job(self.job)
        _discard(self.handles[0], self.handles[1])


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    return kernel32


def _resume(thread_handle: Any) -> bool:
    try:
        previous = _kernel32().ResumeThread(int(thread_handle))
    except Exception as exc:  # noqa: BLE001 - reported as a failed resume
        logger.warning("ResumeThread raised for a suspended child: %s", exc)
        return False
    # The previous suspend count (1 for a thread created suspended), or
    # 0xFFFFFFFF on failure.
    return previous != 0xFFFFFFFF


def _discard(process_handle: Any, thread_handle: Any) -> None:
    import _winapi

    with contextlib.suppress(Exception):
        _winapi.TerminateProcess(int(process_handle), 1)
    with contextlib.suppress(Exception):
        _winapi.WaitForSingleObject(int(process_handle), _DISCARD_WAIT_MS)
    for handle in (thread_handle, process_handle):
        with contextlib.suppress(Exception):
            _winapi.CloseHandle(int(handle))


def _close_job(job: Any) -> None:
    if job is None:
        return
    for name in ("terminate", "close"):
        method = getattr(job, name, None)
        if callable(method):
            with contextlib.suppress(Exception):
                method()


__all__ = [
    "CREATE_SUSPENDED",
    "ContainedStartError",
    "JobStart",
    "start_in_windows_job",
    "windows_job_start",
]
