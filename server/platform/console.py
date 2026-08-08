"""Windows console behaviour that otherwise makes a healthy server look broken.

Two separate problems, both specific to the console a double-clicked launcher
gets:

1. **QuickEdit mode.** Legacy ``conhost`` enables it by default, so one stray
   click inside the window enters text-selection mode and *blocks the process
   on its next write to stderr* once the console buffer fills. This server logs
   a line per request to stderr, so the window a user clicked on to bring it
   forward is exactly the window that then appears to freeze mid-session. The
   remedy is to clear ``ENABLE_QUICK_EDIT_MODE``, which requires also setting
   ``ENABLE_EXTENDED_FLAGS`` -- without it the call is silently ignored.

2. **Closing the window.** Windows delivers that as ``CTRL_CLOSE_EVENT``, which
   CPython does not surface as a signal, so ``launch/serve.py``'s SIGINT /
   SIGTERM / SIGBREAK handlers never see it and the process dies abruptly.
   ``SetConsoleCtrlHandler`` is the only way to observe it. Windows' grace
   period is the time spent inside that callback: once it returns, Windows may
   terminate the process. The callback therefore waits, within a conservative
   deadline, while Uvicorn stops and the launcher's cleanup finishes.

Every function here is best-effort. A server started without a console -- from
``pythonw``, a service wrapper, CI -- has no console handle to configure, and
that is a normal state, not a failure.

The ctypes prototypes are explicit for the reason recorded in
``docs/WINDOWS-VALIDATION.md`` §4.1a: ctypes assumes a C ``int`` return, but a
``HANDLE`` is pointer-sized, so an unprototyped call truncates the handle on
64-bit Windows and then operates on a bogus value.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable


log = logging.getLogger("wg2.console")

STD_INPUT_HANDLE = -10
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6
CLOSE_HANDLER_TIMEOUT_SECONDS = 4.0
_INVALID_HANDLE_VALUE = -1

#: The registered console-control callback. ctypes callbacks are only kept
#: alive by a Python reference, and Windows will call this one from a thread we
#: do not own long after the registering frame has returned -- losing the
#: reference would let it be garbage collected and crash the process.
_ctrl_handler_ref: object | None = None


def disable_quick_edit() -> bool:
    """Stop a stray click from suspending the server. Returns whether it changed."""

    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(wintypes.DWORD(STD_INPUT_HANDLE & 0xFFFFFFFF))
        if not handle or handle == wintypes.HANDLE(_INVALID_HANDLE_VALUE).value:
            return False

        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # No console attached: pythonw, a service, a redirected stdin.
            return False
        if not mode.value & ENABLE_QUICK_EDIT_MODE:
            return False

        updated = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        if not kernel32.SetConsoleMode(handle, wintypes.DWORD(updated)):
            return False
    except Exception as exc:  # noqa: BLE001 - console tuning is never worth failing over
        log.debug("Could not adjust console mode: %s", exc)
        return False
    log.info(
        "Disabled console QuickEdit mode so a click in this window cannot pause the server"
    )
    return True


def _make_close_handler(
    request_shutdown: Callable[[], None],
    shutdown_complete: threading.Event,
    *,
    timeout_seconds: float = CLOSE_HANDLER_TIMEOUT_SECONDS,
) -> Callable[[int], bool]:
    """Build the callback independently of Win32 registration for direct tests."""

    def _handler(event: int) -> bool:
        if event not in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            # Ctrl+C and Ctrl+Break already have signal handlers; claiming them
            # here would take them away from those.
            return False
        log.info("Console close requested; shutting down")
        try:
            request_shutdown()
        except Exception:  # noqa: BLE001 - we are already on the way out
            return True
        if not shutdown_complete.wait(timeout_seconds):
            log.warning(
                "Windows console graceful shutdown window exceeded %.1f seconds; "
                "returning control to Windows",
                timeout_seconds,
            )
        # The Win32 grace period is time spent inside this callback. Returning
        # TRUE says the event is handled and lets Windows terminate the process,
        # so wait above until cleanup finishes or our safe budget expires.
        return True

    return _handler


def install_close_handler(
    request_shutdown: Callable[[], None],
    shutdown_complete: threading.Event,
) -> bool:
    """Turn closing the console window into a graceful shutdown request.

    ``request_shutdown`` is invoked from a Windows-owned thread, so it must be
    thread-safe and must return promptly. Setting Uvicorn's ``should_exit`` is
    both; anything more involved belongs on the event loop. The launcher thread
    must set ``shutdown_complete`` after Uvicorn has stopped and launcher
    cleanup has finished so the Windows callback can return.
    """

    global _ctrl_handler_ref
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        callback = handler_type(_make_close_handler(request_shutdown, shutdown_complete))
        kernel32.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        if not kernel32.SetConsoleCtrlHandler(callback, True):
            return False
        _ctrl_handler_ref = callback
    except Exception as exc:  # noqa: BLE001
        log.debug("Could not install the console close handler: %s", exc)
        return False
    return True


def harden_console(
    request_shutdown: Callable[[], None],
    shutdown_complete: threading.Event,
) -> None:
    """Apply every console fix that this platform and session supports."""

    disable_quick_edit()
    install_close_handler(request_shutdown, shutdown_complete)


__all__ = [
    "disable_quick_edit",
    "harden_console",
    "install_close_handler",
]
