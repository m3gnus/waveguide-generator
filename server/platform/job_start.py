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

Two failure policies, chosen by the caller:

* ``required=True`` (the CAD child, the desktop's server): a job that cannot
  be made or assigned, or a child that cannot be resumed, stops the child
  before it ever ran and raises :class:`ContainedStartError`.
* ``required=False`` (the mesher child, the BEMPP worker): containment is
  best-effort, as it always was for them. A failed assignment resumes the
  child without a job; a child that cannot be resumed is stopped before it
  ran and started again the plain way, then assigned after the fact.

Outside Windows :func:`windows_job_start` does nothing, and its result reports
that it never fired.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
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
    """A required job could not hold the child; it was stopped before it ran."""


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
    it is terminated and closed.

    ``assign(pid, process_handle)`` runs while the child is suspended. It
    returns the job (anything with ``close()``) or ``None``; with
    ``required=True``, ``None`` or an exception stops the child. A failed
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
        # nobody: end its tree rather than leave it running in a job no one
        # will ever close.
        job, result.job = result.job, None
        _close_job(job)
        raise
    finally:
        _local.armed = previous


def start_in_windows_job(
    process: Any, *, confine: Callable[[int], Any], subject: str
) -> Any:
    """``process.start()`` a ``multiprocessing`` child inside a best-effort job.

    ``confine(pid)`` makes and assigns the job (``None`` when it cannot). It
    runs while the child is suspended when the start reaches ``CreateProcess``
    on this thread, and after ``start()`` otherwise -- off Windows, where it
    returns ``None``, and for a test double that starts nothing real.
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

    The child is owned here, and stopped on any exception, from the moment
    ``CreateProcess`` returns until the single statement that hands it to the
    caller. A Ctrl+C arriving meanwhile is held (:func:`_sigint_held`): it
    cannot land between two statements, and once the child is ready it is
    raised here, while the child is still ours to stop.
    """

    flags = int(args[_CREATION_FLAGS_INDEX])
    suspended = list(args)
    suspended[_CREATION_FLAGS_INDEX] = flags | CREATE_SUSPENDED
    child = _Owned()
    with _sigint_held() as interrupt:
        try:
            child.handles = real(*suspended)
            _confine_and_resume(real, args, armed, child)
            interrupt.raise_if_held()
            return child.hand_over(armed.result)
        except BaseException:
            if child.handles is None:
                # Nothing was started (CreateProcess itself failed), so a
                # retry by the caller is still the start this block is about.
                _local.armed = armed
            # Refused, interrupted, or failed: a child we still own never
            # reaches the caller, so stop it. A suspended one never ran.
            child.discard()
            raise


def _confine_and_resume(
    real: Callable[..., Any], args: tuple[Any, ...], armed: _Armed, child: _Owned
) -> None:
    """Make ``child`` ready to hand over, or raise with it still owned."""

    process_handle, thread_handle, pid, _tid = child.handles
    armed.result.fired = True
    armed.result.pid = int(pid)
    job = None
    try:
        job = armed.assign(int(pid), int(process_handle))
    except Exception as exc:  # noqa: BLE001 - the policy below decides
        if armed.required:
            raise ContainedStartError(
                f"could not confine {armed.subject} in a Windows job: {exc}"
            ) from exc
        logger.warning(
            "Could not confine %s in a Windows job object (%s); starting it "
            "without one, so processes it starts may outlive it.",
            armed.subject,
            exc,
        )
    child.job = job
    if job is None and armed.required:
        raise ContainedStartError(f"could not confine {armed.subject} in a Windows job")
    if _resume(thread_handle):
        return
    if armed.required:
        raise ContainedStartError(f"could not resume {armed.subject} after confining it")

    # Best effort, and the child could not be resumed. It never ran, so it
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
    try:
        child.job = armed.assign(int(pid), int(process_handle))
    except Exception:  # noqa: BLE001 - best effort, as before this module
        child.job = None


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
        # One line, so no line event and no statement boundary splits it:
        # from here the caller owns the handles and the job.
        self.released = True; result.job = self.job; return self.handles  # type: ignore[return-value]  # noqa: E702

    def discard(self) -> None:
        if self.released or self.handles is None:
            return
        self.released = True
        _close_job(self.job)
        _discard(self.handles[0], self.handles[1])


class _HeldInterrupt:
    def __init__(self, deliver_as_exception: bool) -> None:
        self.held = False
        self._deliver_as_exception = deliver_as_exception

    def hold(self, _signum: int, _frame: Any) -> None:
        self.held = True

    def raise_if_held(self) -> None:
        """Raise a held default Ctrl+C now, while the caller can still clean up."""

        if self.held and self._deliver_as_exception:
            self.held = False
            raise KeyboardInterrupt


@contextlib.contextmanager
def _sigint_held() -> Iterator[_HeldInterrupt]:
    """Hold Ctrl+C for the span of a contained start.

    ``KeyboardInterrupt`` is raised asynchronously, between any two bytecodes
    of the main thread, including right after ``CreateProcess`` returns and
    before its result is stored. No ``try`` can own a child across that gap,
    so the signal is recorded instead. Python's default handler is honoured
    by :meth:`_HeldInterrupt.raise_if_held` before the child is handed over;
    any other handler gets the signal again on the way out. Other threads
    never receive it, and a handler not installed from Python is left alone.
    """

    if threading.current_thread() is not threading.main_thread():
        yield _HeldInterrupt(False)
        return
    try:
        previous = signal.getsignal(signal.SIGINT)
        if previous is None:
            raise ValueError("SIGINT handler was not installed from Python")
        interrupt = _HeldInterrupt(previous is signal.default_int_handler)
        signal.signal(signal.SIGINT, interrupt.hold)
    except (ValueError, OSError, RuntimeError):
        yield _HeldInterrupt(False)
        return
    try:
        yield interrupt
    finally:
        signal.signal(signal.SIGINT, previous)
        if interrupt.held:
            signal.raise_signal(signal.SIGINT)


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
