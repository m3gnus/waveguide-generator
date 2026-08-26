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

Containment is best-effort by design. If the job API is unavailable the solve
must still run -- an orphaned worker after a Stop is a bad outcome, but refusing
to solve at all is a worse one. Callers that need containment to be mandatory
(untrusted input) should keep using ``server.cadlink.isolation``, which refuses.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
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


def adopt_process_group() -> None:
    """Claim a new POSIX session so this process and its children are one group.

    Call this first thing in the child. A no-op on Windows, where the job
    object created by the parent provides containment instead.
    """

    global _adopted_session_pid

    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        os.setsid()
        _adopted_session_pid = os.getpid()


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


def confine_to_windows_job(pid: int) -> WindowsJob | None:
    """Put ``pid`` and its future descendants in a kill-on-close job object.

    Returns ``None`` on non-Windows hosts and whenever the job API cannot be
    used; see the module docstring for why that is not an error here.
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
            "Could not confine the BEMPP worker in a Windows job object (%s). "
            "Stop will still kill the worker, but a parallel sweep's own worker "
            "processes may outlive it.",
            exc,
        )
        return None


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
    if group == os.getpgid(0):
        # The child never got its own session, so its "group" is ours; killing
        # it would take the server down too.
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
    "confine_to_windows_job",
    "kill_own_process_group",
    "kill_process_group",
    "resolve_process_group",
]
