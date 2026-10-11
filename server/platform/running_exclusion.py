"""Keep bundled multiprocessing children visible to the Windows installer.

These children bypass the native stub and otherwise hold no Running mutex.
Holding it closes the window where a child outlives a dead server and the
installer replaces ``app/`` under it. Only Windows ``wg-python.exe`` children
participate; failure to obtain a handle refuses to run. This deliberately
does not look at the installer's admission mutex.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import sys


logger = logging.getLogger(__name__)
RUNNING_MUTEX_NAME = "WaveguideGeneratorRunning"
_handle: int | None = None


class RunningExclusionError(RuntimeError):
    """The child could not hold the Running mutex and must not run."""

    def __init__(self, winerror: int) -> None:
        self.winerror = winerror
        super().__init__(f"Could not hold the Running mutex (Win32 error {winerror})")


def _hold(name: str) -> None:
    """Keep a non-inheritable mutex handle until the process exits."""
    global _handle

    if _handle is not None:
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        winerror = ctypes.get_last_error()
        logger.error("Could not hold %s: Win32 error %d", name, winerror)
        raise RunningExclusionError(winerror)
    # An existing mutex is the normal case. Never close this handle: the OS
    # releases it at process exit, keeping the installer out for our lifetime.
    _handle = handle


def hold_running_exclusion() -> bool:
    """Hold the installer's Running mutex when using the bundled interpreter."""
    if os.name != "nt":
        return False
    executable = getattr(sys, "_base_executable", None)
    if not executable or Path(executable).name.casefold() != "wg-python.exe":
        return False
    _hold(RUNNING_MUTEX_NAME)
    return True
