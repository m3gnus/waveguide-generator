"""Let the Windows installer retire idle official hosts before it installs.

A host that broke away from the status window's job outlives Quit (design
PR 22). In the bundle it runs through the native stub, which holds the
``WaveguideGeneratorRunning`` mutex the installer waits on, so an idle host
would refuse a manual install, and fail an in-app update, until its idle
timeout. The installer sets this named, manual-reset event before it checks
that mutex (``installers/windows/bundle-setup.iss``,
``WaitForRunningApplicationExit``), and a host that sees it exits exactly as
on idle expiry: only with no client and no peer mid-handshake. A running app
keeps its connection, so its host stays, and the installer still refuses.

The name is a contract with the installer; ``bundle-setup.iss`` spells it too.
Off Windows this does nothing.
"""

from __future__ import annotations

import os

RETIRE_IDLE_EVENT = "WaveguideGeneratorRetireIdleBeatHosts"


class RetireSignal:
    """This process's handle on the installer's retire event, or nothing."""

    def __init__(self) -> None:
        # Resolve at construction so tests can replace the constant in-process.
        # Production deliberately does not read an environment override.
        event_name = RETIRE_IDLE_EVENT
        if not isinstance(event_name, str) or not event_name.strip() or "\\" in event_name or "\0" in event_name:
            raise ValueError("Retire event name must be a non-empty session-local name")
        self._kernel32 = None
        self._handle = None
        self.error: int | None = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        # Manual reset, initially clear; every host shares the one object.
        handle = kernel32.CreateEventW(None, True, False, event_name)
        if not handle:
            self.error = ctypes.get_last_error()
            return
        self._kernel32, self._handle = kernel32, handle

    def requested(self) -> bool:
        if self._handle is None:
            return False
        return self._kernel32.WaitForSingleObject(self._handle, 0) == 0  # WAIT_OBJECT_0

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)
