"""Re-apply application signal handlers after native libraries replace them.

Gmsh changes the process SIGTERM disposition inside ``gmsh.initialize()`` on
macOS even with ``interruptible=False``. ``signal.getsignal`` still reports
Python's cached callable afterwards, while the OS has actually restored its
default terminate action. Native work runs on the Gmsh owner thread, where
Python forbids calling ``signal.signal``; callbacks registered here are run on
the main event loop immediately after each initialize/finalize boundary.
"""

from __future__ import annotations

from collections.abc import Callable
import threading


_lock = threading.Lock()
_callbacks: dict[object, Callable[[], None]] = {}


def register_signal_rearm(callback: Callable[[], None]) -> object:
    """Register a main-thread callback and return its opaque removal token."""

    token = object()
    with _lock:
        _callbacks[token] = callback
    return token


def unregister_signal_rearm(token: object) -> None:
    with _lock:
        _callbacks.pop(token, None)


def signal_rearm_is_needed() -> bool:
    with _lock:
        return bool(_callbacks)


def rearm_registered_signals() -> None:
    """Run every registered callback; callers must be on the main thread."""

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("signal handlers can only be re-armed on the main thread")
    with _lock:
        callbacks = tuple(_callbacks.values())
    for callback in callbacks:
        callback()


__all__ = [
    "rearm_registered_signals",
    "register_signal_rearm",
    "signal_rearm_is_needed",
    "unregister_signal_rearm",
]
