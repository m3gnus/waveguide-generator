"""Kill a ``multiprocessing`` child *and everything it started*.

``server/cadlink/isolation.py`` already solves this for ``subprocess.Popen``
children, but it is welded to that module's ``ChildBudget`` and STEP refusal
vocabulary. The BEMPP worker is a ``multiprocessing.Process``, and it needs the
containment for a different reason: once the native sweep is allowed to split
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
``server/platform/job_start.py``, which refuses: a child outside every job also
holds no ``WaveguideGeneratorRunning`` mutex, so it can outlive a dead server
unseen by the installer. :func:`popen_in_windows_job` (the OpenCL check) still
runs its child without a job when none can be made. ``server.cadlink.isolation``
refuses as well.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import signal
import subprocess
import time
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

    def __init__(self, handle: Any, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    def terminate(self) -> None:
        with contextlib.suppress(Exception):
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._kernel32.CloseHandle(self._handle)


def confine_to_windows_job(
    pid: int, *, subject: str = "the BEMPP worker"
) -> WindowsJob | None:
    """Put ``pid`` and its future descendants in a kill-on-close job object.

    Returns ``None`` on non-Windows hosts and whenever the job API cannot be
    used; see the module docstring for what callers do with that.

    Only descendants created *after* this call join the job. A child that
    starts processes of its own straight away needs
    :func:`popen_in_windows_job`, which assigns it before it runs.
    """

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare_job_api(kernel32, ctypes, wintypes)

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
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

        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, int(pid))
        if not handle:
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not kernel32.AssignProcessToJobObject(job, handle):
                kernel32.CloseHandle(job)
                raise OSError(
                    ctypes.get_last_error(), "AssignProcessToJobObject failed"
                )
        finally:
            kernel32.CloseHandle(handle)
        return WindowsJob(job, kernel32)
    except Exception as exc:  # pragma: no cover - Windows-only failure paths
        logger.warning(
            "Could not confine %s in a Windows job object: %s. What happens "
            "to it next is the caller's policy.",
            subject,
            exc,
        )
        return None


#: ``CREATE_SUSPENDED``: the child's first thread does not run until resumed.
_CREATE_SUSPENDED = 0x00000004


def popen_in_windows_job(
    command: list[str], *, subject: str, **popen_kwargs: Any
) -> tuple[subprocess.Popen[Any], WindowsJob | None]:
    """Start ``command`` with every process it will ever start in one job.

    In the Windows bundle ``sys.executable`` is the native entry stub, and the
    stub starts the real interpreter as *its* child, handing it the same pipes
    (``launchers/windows/startup_hook.py``, ``launcher.c`` ``start()``). Killing
    the stub alone leaves that interpreter running and holding the pipes. A
    job assigned after ``Popen`` returns can lose that race, because the stub
    may already have started its child, so the child is created suspended,
    assigned, then resumed: nothing it starts can predate the job. The stub
    does not ask for ``CREATE_BREAKAWAY_FROM_JOB``, so its child inherits it.

    Elsewhere, and whenever the job cannot be made, this is a plain ``Popen``
    and the job is ``None``; containment is best-effort, as for the worker.
    A child that cannot be resumed is killed before it ran and started again
    the plain way: a host that blocks the thread snapshot loses containment,
    never the child.
    """

    if os.name != "nt":
        return subprocess.Popen(command, **popen_kwargs), None  # noqa: S603
    flags = int(popen_kwargs.pop("creationflags", 0))
    child = subprocess.Popen(command, creationflags=flags | _CREATE_SUSPENDED, **popen_kwargs)  # noqa: S603
    job = None
    resumed = False
    try:
        job = confine_to_windows_job(child.pid, subject=subject)
    finally:
        resumed = _resume_with_retries(child.pid)
        if not resumed:
            # It never ran, so it started nothing; a suspended child would
            # hold its pipes and our wait forever.
            if job is not None:
                job.terminate()
                job.close()
            with contextlib.suppress(Exception):
                child.kill()
            with contextlib.suppress(Exception):
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
    if resumed:
        if job is None:
            logger.warning(
                "Running %s without a Windows job object; processes it starts "
                "may outlive it.",
                subject,
            )
        return child, job
    logger.warning(
        "Could not resume %s after starting it suspended; starting it again "
        "without a Windows job object, so processes it starts may outlive it.",
        subject,
    )
    return subprocess.Popen(command, creationflags=flags, **popen_kwargs), None  # noqa: S603


#: A transient snapshot or thread-open failure is retried this often.
_RESUME_ATTEMPTS = 5
_RESUME_RETRY_SECONDS = 0.02


def _resume_with_retries(pid: int) -> bool:
    for attempt in range(_RESUME_ATTEMPTS):
        if _resume_windows_process(pid):
            return True
        if attempt + 1 < _RESUME_ATTEMPTS:
            time.sleep(_RESUME_RETRY_SECONDS)
    return False


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


def _resume_windows_process(pid: int) -> bool:
    """Resume the threads of a process created with ``CREATE_SUSPENDED``.

    ``Popen`` closes the thread handle ``CreateProcess`` returned, so the
    thread is found again through a ToolHelp snapshot. A suspended new process
    has exactly its initial thread; every thread found is resumed once.
    """

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        invalid = ctypes.c_void_p(-1).value
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if not snapshot or snapshot == invalid:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        resumed = 0
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == int(pid):
                    # THREAD_SUSPEND_RESUME
                    thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                    if thread:
                        try:
                            if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                                resumed += 1
                        finally:
                            kernel32.CloseHandle(thread)
                more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return resumed > 0
    except Exception as exc:  # pragma: no cover - Windows-only failure paths
        logger.warning("Could not resume suspended child process %s (%s)", pid, exc)
        return False


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
    "kill_own_process_group",
    "kill_process_group",
    "popen_in_windows_job",
    "resolve_process_group",
]
