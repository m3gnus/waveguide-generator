"""Kill a ``multiprocessing`` child *and everything it started*.

The shared child-process runner uses this module for Windows jobs and POSIX
containment. Both ``subprocess.Popen`` and ``multiprocessing.Process`` children
need the same tree ownership: once the native sweep is allowed to split
across worker processes, ``Process.terminate()`` reaches only the direct child
and leaves its sweep workers running -- orphans that keep burning every core
the user just pressed Stop to reclaim.

The two platforms need opposite ownership:

* **POSIX** -- the *child* claims a new session at startup (``adopt_process_group``),
  so the child and its workers share one process group and ``killpg`` is exact.
  It has to happen in the child: the parent cannot retroactively move a process
  that has already forked its own children.
* **Windows** -- the *parent* creates a job object and assigns the child to it
  (``confine_to_windows_job``). ``KILL_ON_JOB_CLOSE`` means the tree dies with
  the parent even if the parent dies without running any cleanup, which is the
  property a ``TerminateProcess``-based Stop cannot otherwise get.

:func:`confine_to_windows_job` itself is best-effort: it returns ``None`` when
the job API is unavailable. What a caller does with that ``None`` is the
caller's policy. The mesher child and the BEMPP worker start through
``server/platform/job_start.py``, which refuses: a child outside every job can
outlive a dead server. When bundled, the mesher, BEMPP and sweep children hold
``WaveguideGeneratorRunning`` through ``running_exclusion``, so the installer
waits on such a child, but nothing would kill it.
:func:`popen_in_windows_job` (the OpenCL check) and
``server.cadlink.intake.isolation`` also refuse when no job can hold the child.
The BEAT host uses :func:`confine_to_job_owned_by`: its stub owns the job,
so killing the stub ends the interpreter; it refuses to start without one.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import signal
import subprocess
from typing import Any


logger = logging.getLogger("wg.solve")

#: ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


#: The pid that successfully called :func:`adopt_process_group`, if any.
#:
#: :func:`kill_own_process_group` refuses to signal a session this process did
#: not create, and "did we create it?" cannot be re-derived after the fact:
#: ``getpgid(0) == getpid()`` is also true of any job-control group leader --
#: which is what ``pytest`` is when it is run from a terminal -- so a check
#: like that would let a test run SIGKILL the developer's shell job. Recording
#: the adoption is the only guard that distinguishes the session we made from
#: one we merely lead. The pid is stored rather than a bool so a fork cannot
#: inherit the flag and act on its parent's session.
_adopted_session_pid: int | None = None


def adopt_process_group() -> int | None:
    """Claim a new POSIX session so this process and its children are one group.

    Call this first thing in the child. A no-op on Windows, where the job
    object created by the parent provides containment instead.

    Returns the group this process now leads (its pid), or ``None`` when it
    claimed none -- Windows, not a spawned child, or a ``setsid`` that failed.
    The child reports it to its parent, which must not have to work it out from
    the outside: ``getpgid`` on a child that has already died and been reaped
    answers nothing, and that is exactly the child whose workers need killing.
    """

    global _adopted_session_pid

    if os.name != "posix":
        return None
    if multiprocessing.parent_process() is None:
        # Not a spawned child, so not the process this containment is for.
        # ``_bempp_worker_main`` is driven in-process by the loop tests, and
        # under a test runner that is not already a session leader the setsid
        # below SUCCEEDS -- recording the runner's own pid as an adopted
        # session and handing kill_own_process_group a live target. The runner
        # then kills itself mid-suite: measured as pytest exiting 137 with no
        # test named, and invisible to any runner that leads its own session.
        # ``_exit_when_parent_does`` already declines on exactly this test,
        # two lines below the call to this function; it is the same question.
        return None
    try:
        os.setsid()
    except OSError:
        return None
    _adopted_session_pid = os.getpid()
    return _adopted_session_pid


class WindowsJob:
    """A job object holding one child process and everything it starts."""

    def __init__(self, handle: Any, kernel32: Any, *, suppress_errors: bool = True) -> None:
        self._handle = handle
        self._kernel32 = kernel32
        self._suppress_errors = suppress_errors

    def contains(self, pid: int) -> bool:
        """Whether a confirmed backend pid is in this spawned stub's tree."""
        import ctypes
        from ctypes import wintypes

        kernel = self._kernel32
        kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
        kernel.IsProcessInJob.restype = wintypes.BOOL
        process = kernel.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            member = wintypes.BOOL()
            return bool(kernel.IsProcessInJob(process, self._handle, ctypes.byref(member)) and member.value)
        finally:
            kernel.CloseHandle(process)

    def terminate(self) -> None:
        if not self._suppress_errors:
            self._kernel32.TerminateJobObject(self._handle, 1)
            return
        with contextlib.suppress(Exception):
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if not self._suppress_errors:
            self._kernel32.CloseHandle(self._handle)
            return
        with contextlib.suppress(Exception):
            self._kernel32.CloseHandle(self._handle)


def windows_backend_breakaway_flags() -> int:
    """Detach a backend from the caller's job, or refuse before creating it.

    A nested non-killing job does not release the caller's kill-on-close job.
    Query the immediate job. The owner also checks the suspended child for
    residual membership, because a non-breakaway outer job can retain it.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel.IsProcessInJob.restype = wintypes.BOOL
    member = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(member)):
        raise OSError("Could not check the caller's Windows job; cannot detach the backend")
    if not member.value:
        return 0

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    kernel.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    )
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    limits = BasicLimits()
    if not kernel.QueryInformationJobObject(None, 2, ctypes.byref(limits), ctypes.sizeof(limits), None):
        raise OSError("Could not query the caller's Windows job; cannot detach the backend")
    if limits.LimitFlags & 0x00000800:  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
        return 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    if limits.LimitFlags & 0x00001000:  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        return 0
    raise OSError(
        "The caller's Windows job does not allow backend breakaway. "
        "Start Waveguide Generator outside that job, or enable job breakaway."
    )


def require_windows_job_detached(process_handle: Any) -> None:
    """Refuse a suspended backend retained by any caller job in the chain."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel.IsProcessInJob.restype = wintypes.BOOL
    member = wintypes.BOOL()
    if not kernel.IsProcessInJob(process_handle, None, ctypes.byref(member)) or member.value:
        raise OSError(
            "The backend could not detach from the caller's Windows job chain. "
            "Start Waveguide Generator outside that job, or enable breakaway in every parent job."
        )


def confine_to_windows_job(
    pid: int, *, subject: str = "the BEMPP worker",
    kill_on_close: bool = True, allow_breakaway: bool = False
) -> WindowsJob | None:
    """Put ``pid`` and its future descendants in a job object.

    Normally kill-on-close. A backend admission client uses explicit termination
    until confirmation and closes without killing after handing ownership over.
    ``allow_breakaway`` permits only children explicitly requesting breakaway.

    Returns ``None`` on non-Windows hosts and whenever the job API cannot be
    used; see the module docstring for what callers do with that.

    Only descendants created *after* this call join the job. A child that
    starts processes of its own straight away needs
    :func:`popen_in_windows_job`, which assigns it before it runs.
    """

    if os.name != "nt":
        return None
    try:
        return create_windows_job(
            pid, by_pid=True, suppress_errors=True, error_before_close=False,
            creation_error_before_close=True, suppress_creation_errors=True,
            kill_on_close=kill_on_close, allow_breakaway=allow_breakaway,
        )
    except Exception as exc:  # pragma: no cover - Windows-only failure paths
        logger.warning(
            "Could not confine %s in a Windows job object: %s. What happens "
            "to it next is the caller's policy.",
            subject,
            exc,
        )
        return None


def create_windows_job(
    process: Any, *, memory_bytes: int | None = None,
    active_process_limit: int | None = None, by_pid: bool = False,
    suppress_errors: bool = False, error_before_close: bool = True,
    creation_error_before_close: bool | None = None,
    suppress_creation_errors: bool = False,
    kill_on_close: bool = True, allow_breakaway: bool = False,
) -> WindowsJob:
    """Create and assign one job with optional kernel limits and lifetime policies.

    CAD supplies a process handle and resource caps; reusable workers supply
    a pid and request best-effort cleanup. Creation and assignment have
    separate error policies to preserve each wrapper's Win32 diagnostics.
    Only a process handle opened here is closed after assignment. Jobs kill
    their tree on close by default; backend admission can disable that and
    allow descendants to request explicit breakaway.
    """

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:  # pragma: no cover - Windows only
        if by_pid:
            raise
        raise RuntimeError("Windows job-object support is unavailable") from exc

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    _declare_job_api(kernel32, ctypes, wintypes)
    handle = _create_windows_job(
        kernel32, ctypes, wintypes,
        memory_bytes=memory_bytes, active_process_limit=active_process_limit,
        kill_on_close=kill_on_close, allow_breakaway=allow_breakaway,
        error_before_close=(
            error_before_close if creation_error_before_close is None
            else creation_error_before_close
        ),
        suppress_errors=suppress_creation_errors,
    )
    process_handle = process if isinstance(process, int) else int(process._handle)
    if by_pid:
        process_handle = kernel32.OpenProcess(0x0100 | 0x0001, False, int(process))
        if not process_handle:
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        if not kernel32.AssignProcessToJobObject(handle, process_handle):
            error = ctypes.get_last_error() if error_before_close else None
            kernel32.CloseHandle(handle)
            raise OSError(error if error_before_close else ctypes.get_last_error(),
                          "AssignProcessToJobObject failed")
    finally:
        if by_pid:
            kernel32.CloseHandle(process_handle)
    return WindowsJob(handle, kernel32, suppress_errors=suppress_errors)


def _create_kill_on_close_job(kernel32: Any, ctypes: Any, wintypes: Any) -> Any:
    """Create a job with kill-on-close as its only limit."""
    return _create_windows_job(kernel32, ctypes, wintypes, suppress_errors=True)


def _create_windows_job(
    kernel32: Any, ctypes: Any, wintypes: Any, *,
    memory_bytes: int | None = None, active_process_limit: int | None = None,
    kill_on_close: bool = True, allow_breakaway: bool = False,
    error_before_close: bool = True, suppress_errors: bool = False,
) -> Any:
    """Create the job handle using explicit resource and failure policies.

    By default, kill-on-close is the only limit. Resource caps and explicit
    breakaway are optional. The caller owns the returned handle and decides
    how to assign it and transfer ownership.
    """

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    limit_job_memory = 0x00000200
    limit_process_memory = 0x00000100
    limit_active_process = 0x00000008

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        (_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0)
        | (0x00000800 if allow_breakaway else 0)  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
    )
    if memory_bytes is not None:
        limits.BasicLimitInformation.LimitFlags |= limit_job_memory | limit_process_memory
        limits.ProcessMemoryLimit = memory_bytes
        limits.JobMemoryLimit = memory_bytes
    if active_process_limit is not None:
        limits.BasicLimitInformation.LimitFlags |= limit_active_process
        limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
    try:
        if not kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error() if error_before_close else None
            if not suppress_errors:
                kernel32.CloseHandle(handle)
            raise OSError(error if error_before_close else ctypes.get_last_error(),
                          "SetInformationJobObject failed")
    except Exception:
        if suppress_errors:
            with contextlib.suppress(Exception):
                kernel32.CloseHandle(handle)
        raise
    return handle


def confine_to_job_owned_by(process_handle: int, *, subject: str) -> WindowsJob | None:
    """Confine a suspended process to a job whose last handle it will own.

    The target's copy is non-inheritable: a launcher stub's interpreter joins
    the job but cannot keep it alive after the stub exits. The caller closes
    its own copy after startup, or terminates the job when startup fails.
    """
    if os.name != "nt":
        return None
    job = None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare_job_api(kernel32, ctypes, wintypes)
        _declare_job_owner_api(kernel32, ctypes, wintypes)
        job = _create_kill_on_close_job(kernel32, ctypes, wintypes)
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        target = wintypes.HANDLE()
        if not kernel32.DuplicateHandle(
            kernel32.GetCurrentProcess(), job, process_handle, ctypes.byref(target),
            0, False, 0x00000002,  # DUPLICATE_SAME_ACCESS
        ):
            raise OSError(ctypes.get_last_error(), "DuplicateHandle failed")
        return WindowsJob(job, kernel32)
    except Exception as exc:
        if job is not None:
            with contextlib.suppress(Exception):
                kernel32.CloseHandle(job)
        logger.warning(
            "Could not confine %s in a Windows job object: %s. What happens "
            "to it next is the caller's policy.", subject, exc,
        )
        return None


def popen_in_windows_job(
    command: list[str], *, subject: str, **popen_kwargs: Any
) -> tuple[subprocess.Popen[Any], WindowsJob | None]:
    """Start ``command`` with every process it will ever start in one job.

    On Windows, ``job_start`` creates it suspended, assigns its job, then
    resumes it so a launcher stub's interpreter inherits the job. Refuses
    with ``ContainedStartError`` when no job can hold it or it cannot resume;
    never returns without a job. The OpenCL caller maps refusal to
    ``probe_error``. An unfired start is confined after the fact or stopped.

    Elsewhere this is a plain ``Popen`` and the job is ``None``.
    """

    if os.name != "nt":
        return subprocess.Popen(command, **popen_kwargs), None  # noqa: S603
    from server.platform.job_start import ContainedStartError, windows_job_start

    with windows_job_start(
        lambda pid, _handle: confine_to_windows_job(pid, subject=subject),
        required=True,
        subject=subject,
    ) as started:
        child = subprocess.Popen(command, **popen_kwargs)  # noqa: S603
    try:
        if not started.fired:
            started.job = confine_to_windows_job(child.pid, subject=subject)
        if started.job is None:
            raise ContainedStartError(f"could not confine {subject} in a Windows job")
    except Exception as exc:
        with contextlib.suppress(Exception):
            child.kill()
        with contextlib.suppress(Exception):
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
        if isinstance(exc, ContainedStartError):
            raise
        raise ContainedStartError(
            f"could not confine {subject} in a Windows job: {exc}"
        ) from exc
    return child, started.job


def cancel_blocked_reads(
    threads: list[Any], *, attempts: int = 10, wait_seconds: float = 0.05
) -> list[Any]:
    """End reader threads blocked on a pipe another process still holds.

    On Windows a synchronous ``ReadFile`` on an anonymous pipe returns only at
    EOF, and EOF needs every writer gone, including a grandchild that inherited
    the handle. ``CancelSynchronousIo`` makes the blocked read fail instead, so
    a reader that treats ``OSError`` as EOF ends and its stream can be closed.
    Retried briefly, because a reader may be between reads when cancelled.
    Bounded by ``attempts * wait_seconds``; never waits on the other process.
    Returns the threads still alive (all of them, elsewhere).
    """

    alive = [thread for thread in threads if thread.is_alive()]
    if os.name != "nt" or not alive:
        return alive
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
        kernel32.CancelSynchronousIo.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except Exception as exc:  # pragma: no cover - Windows-only failure paths
        logger.warning("Could not cancel blocked reads (%s)", exc)
        return alive
    for _ in range(attempts):
        for thread in alive:
            native = getattr(thread, "native_id", None)
            if native is None or not thread.is_alive():
                continue
            handle = kernel32.OpenThread(0x0001, False, native)  # THREAD_TERMINATE
            if handle:
                try:
                    kernel32.CancelSynchronousIo(handle)
                finally:
                    kernel32.CloseHandle(handle)
        for thread in alive:
            thread.join(wait_seconds)
        alive = [thread for thread in alive if thread.is_alive()]
        if not alive:
            break
    return alive


def _declare_job_api(kernel32: Any, ctypes: Any, wintypes: Any) -> None:
    """Declare the pointer-sized Win32 ABI the job wrapper uses.

    ``ctypes`` otherwise assumes ``c_int`` for every argument and return value,
    which truncates HANDLEs on 64-bit Windows.
    """

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _declare_job_owner_api(kernel32: Any, ctypes: Any, wintypes: Any) -> None:
    """Declare the ABI for transferring a job handle into its owner."""

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL


def resolve_process_group(pid: int) -> int | None:
    """The process group ``pid`` leads, or ``None`` if it is not its own.

    Must be called while the child is still unreaped: once ``join()`` has
    collected it, ``getpgid`` can no longer resolve it and its workers become
    unreachable. Callers therefore resolve the group *before* terminating and
    pass the result to :func:`kill_process_group`.
    """

    if os.name != "posix":
        return None
    try:
        group = os.getpgid(int(pid))
    except (ProcessLookupError, PermissionError, OSError):
        return None
    if group != int(pid):
        # Not a group this child leads, so not one adopt_process_group made.
        # Asking "is it ours?" instead of "is it not the server's?" is the same
        # correction applied in kill_own_process_group: a negative comparison
        # only rules out the failure it names. It rules out the child never
        # having got its own session -- where the "group" is the server's and
        # killing it would take the server down -- but it says nothing about a
        # child sitting in some third group, which it would happily kill.
        # Leading the group is the invariant setsid actually establishes, so
        # test for that.
        return None
    return group


def kill_process_group(group: int | None) -> bool:
    """SIGKILL a POSIX process group resolved by :func:`resolve_process_group`."""

    if group is None or os.name != "posix":
        return False
    try:
        os.killpg(int(group), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def kill_own_process_group() -> bool:
    """SIGKILL the session this process claimed in :func:`adopt_process_group`.

    For the one case the rest of this module cannot reach: the *parent* is
    force-killed. The child notices through its parent-sentinel watchdog and
    leaves via ``os._exit``, which runs no ``multiprocessing`` cleanup -- so a
    parallel sweep's workers are not terminated by that exit. They used to be
    reachable anyway, because the child shared the launcher's process group;
    ``adopt_process_group`` deliberately took it out of that group, so nothing
    else can reap them either. Measured on macOS 15 before this existed: three
    sweep workers reparented to init and kept burning ~9% CPU each, their
    counters still climbing five seconds after the launcher died.

    Only ever signals a session this process created. ``killpg`` includes the
    caller, so this does not return on success -- callers keep their ``os._exit``
    as the path taken when containment does not apply (Windows, or a ``setsid``
    that failed). Nothing observes the exit code in the case this fires: the
    only process that could read it is the parent, whose death is the trigger.
    """

    if os.name != "posix" or _adopted_session_pid != os.getpid():
        return False
    try:
        if os.getsid(0) != os.getpid():
            # We recorded an adoption but are not the session leader any more,
            # so the group is no longer ours to kill.
            return False
        os.killpg(0, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


__all__ = [
    "WindowsJob",
    "adopt_process_group",
    "cancel_blocked_reads",
    "confine_to_windows_job",
    "confine_to_job_owned_by",
    "create_windows_job",
    "kill_own_process_group",
    "kill_process_group",
    "popen_in_windows_job",
    "resolve_process_group",
]
