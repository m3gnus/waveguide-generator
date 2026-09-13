"""How much physical memory this host has, and how that was found out.

Resource admission (the dense-solver memory ceiling) must not treat a guess as
a measurement, so every answer carries its probe and whether it is known:

- Windows: ``GlobalMemoryStatusEx`` through ``ctypes``. Windows has no
  ``os.sysconf``, so a POSIX-only probe reports nothing there.
- macOS: ``sysconf``, falling back to ``sysctl hw.memsize``.
- Other POSIX hosts: ``sysconf``.

A probe that fails, or answers zero or a negative number, yields
``known=False`` with ``bytes=None``; the caller chooses the conservative
behaviour. Detection never raises.

This reports installed physical memory only. It is not current availability
and not a container or job-object limit.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass


SOURCE_WINDOWS = "GlobalMemoryStatusEx"
SOURCE_SYSCONF = "sysconf"
SOURCE_SYSCTL = "sysctl hw.memsize"
SOURCE_UNKNOWN = "unknown"

_SYSCTL = "/usr/sbin/sysctl"
# A mesh build waits on this, so a wedged sysctl must not hold it up.
_SYSCTL_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class PhysicalMemory:
    """Installed physical memory: ``bytes`` is None exactly when not ``known``."""

    bytes: int | None
    source: str
    known: bool
    detail: str = ""

    def to_dict(self) -> dict[str, int | str | bool | None]:
        return {"bytes": self.bytes, "source": self.source, "known": self.known}


class _MemoryStatusEx(ctypes.Structure):
    """Win32 ``MEMORYSTATUSEX``.

    DWORD is spelled ``c_uint32`` rather than ``c_ulong``: ``c_ulong`` is 8
    bytes on LP64 hosts, which would give the structure a different layout
    everywhere except Windows and hide a layout bug from off-Windows tests.
    """

    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_total_bytes() -> int:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("ctypes.WinDLL is not available on this interpreter")
    kernel32 = win_dll("kernel32", use_last_error=True)
    query = kernel32.GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    query.restype = ctypes.c_int  # BOOL
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not query(ctypes.pointer(status)):
        get_last_error = getattr(ctypes, "get_last_error", None)
        code = get_last_error() if get_last_error is not None else 0
        raise OSError(f"GlobalMemoryStatusEx failed (Win32 error {code})")
    return int(status.ullTotalPhys)


def _sysconf_total_bytes() -> int:
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        raise OSError("os.sysconf is not available on this platform")
    return int(sysconf("SC_PAGE_SIZE")) * int(sysconf("SC_PHYS_PAGES"))


def _sysctl_memsize_bytes() -> int:
    completed = subprocess.run(
        [_SYSCTL, "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        timeout=_SYSCTL_TIMEOUT_S,
        check=True,
    )
    return int(completed.stdout.strip())


def _probes(system: str) -> tuple[tuple[str, Callable[[], int]], ...]:
    if system == "Windows":
        return ((SOURCE_WINDOWS, _windows_total_bytes),)
    if system == "Darwin":
        return ((SOURCE_SYSCONF, _sysconf_total_bytes), (SOURCE_SYSCTL, _sysctl_memsize_bytes))
    return ((SOURCE_SYSCONF, _sysconf_total_bytes),)


def physical_memory(*, system: str | None = None) -> PhysicalMemory:
    """Probe this host's installed physical memory. Never raises.

    ``system`` is a ``platform.system()`` value; tests pass it to exercise
    another platform's probe order.
    """

    resolved = platform.system() if system is None else system
    failures: list[str] = []
    for source, probe in _probes(resolved):
        try:
            total = probe()
        except Exception as exc:  # detection must degrade to unknown, never fail a build
            failures.append(f"{source}: {type(exc).__name__}: {exc}")
            continue
        if total > 0:
            return PhysicalMemory(bytes=total, source=source, known=True)
        failures.append(f"{source}: reported {total} bytes")
    return PhysicalMemory(
        bytes=None,
        source=SOURCE_UNKNOWN,
        known=False,
        detail="; ".join(failures) or f"no memory probe for platform {resolved!r}",
    )


__all__ = [
    "SOURCE_SYSCONF",
    "SOURCE_SYSCTL",
    "SOURCE_UNKNOWN",
    "SOURCE_WINDOWS",
    "PhysicalMemory",
    "physical_memory",
]
