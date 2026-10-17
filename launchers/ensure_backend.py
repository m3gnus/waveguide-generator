"""JSON-only installed backend admission; no GUI or application import."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import threading

from launchers.backend_start import backend_argv, backend_environment
from server.platform.backend_owner import (
    BackendDataDirMismatch,
    BackendOwnerError,
    BackendStartTimeout,
    ensure_backend,
)
from server.platform.instance import requested_port
from server.platform.paths import app_root, migrate_legacy_data_dir


class BackendStartCancelled(BackendOwnerError):
    """The caller stopped waiting; an unconfirmed spawned child was cleaned up."""


@contextmanager
def _cancellation_signals():
    previous = {}
    stopped = threading.Event()

    def cancel(signum, _frame):
        raise BackendStartCancelled(f"Backend start cancelled by {signal.Signals(signum).name}.")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, cancel)
        if sys.platform == "win32" and getattr(sys, "_wg_native_start_admitted", False):
            # TerminateProcess on the public Windows stub cannot send Python a
            # signal. Its admitted interpreter watches that exact parent and
            # uses the same cancellation path when the caller cancels the stub.
            threading.Thread(target=_watch_native_parent, args=(stopped,), daemon=True).start()
        yield
    finally:
        stopped.set()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _watch_native_parent(stopped: threading.Event) -> None:
    import _thread
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    parent = getattr(sys, "_wg_native_parent_handle", None)
    if parent is None:
        parent = kernel.OpenProcess(0x00100000, False, os.getppid())
    if not parent:
        if not stopped.is_set():
            _thread.interrupt_main(signal.SIGTERM)
        return
    try:
        while not stopped.is_set():
            if kernel.WaitForSingleObject(parent, 100) != 258:
                if not stopped.is_set():
                    _thread.interrupt_main(signal.SIGTERM)
                return
    finally:
        kernel.CloseHandle(parent)
        if getattr(sys, "_wg_native_parent_handle", None) == parent:
            sys._wg_native_parent_handle = None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(f"Invalid --ensure-backend arguments: {message}")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _Parser(prog="waveguide-generator --ensure-backend", allow_abbrev=False,
                         add_help=False)
        parser.add_argument("--data-dir", type=Path)
        options = parser.parse_args(argv)
        if options.data_dir is not None and not options.data_dir.is_absolute():
            raise ValueError("--data-dir must be an absolute path.")
        with _cancellation_signals():
            # Admission creates locks/logs under this root. Migrate first,
            # exactly as GUI serve.py does, before a new root can mask legacy
            # data. Discovery resolving a default is not an isolation override.
            data_dir = migrate_legacy_data_dir(options.data_dir, emit_log=False).new.resolve()
            root = app_root()
            environment = backend_environment(os.environ, data_dir=options.data_dir)
            handle = ensure_backend(
                data_dir,
                backend_argv(
                    repo_root=root,
                    headless=True,
                    data_dir=options.data_dir,
                    port=requested_port(None, environ=environment),
                ),
                start_env=environment,
            )
        payload = {
            "ok": True,
            "base_url": handle.base_url,
            "pid": handle.pid,
            "data_dir": str(handle.data_dir),
            "version": handle.version,
            "commit": handle.commit,
        }
        if handle.contained:
            payload["lifetime"] = "contained"
        code = 0
    except BaseException as exc:
        code = (
            3
            if isinstance(exc, BackendDataDirMismatch)
            else 4
            if isinstance(exc, (BackendStartTimeout, BackendStartCancelled))
            else 5
        )
        payload = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    print(json.dumps(payload, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
