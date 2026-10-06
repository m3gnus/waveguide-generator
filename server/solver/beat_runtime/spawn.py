"""Spawn one detached host under exclusion and authenticate its publication."""

from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
from collections.abc import Mapping

from server.platform.paths import app_root

from . import paths, registry as r
from .cleanup import cleanup_host, connect_authenticated, sweep_orphan_socket
from .host import DEFAULT_IDLE_TIMEOUT, read_private_json, ready_path, validate_key
from .ipc import Endpoint, remaining_time

HOST_MODULE = "server.solver.beat_runtime.host"
RECOVERY_INTERVAL = 0.2


def detached_options(*, windows: bool | None = None) -> dict[str, Any]:
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        # Remain in the launcher's Job Object: packaged Windows Quit still kills
        # the host. Breakaway permission/refusal belongs to design PR 22.
        return {"creationflags": 0x00000200 | 0x00000008}  # NEW_PROCESS_GROUP | DETACHED_PROCESS
    return {"start_new_session": True}


def _launch(key: dict[str, Any], directory: Path, idle_timeout: float, timeout: float,
            environment: Mapping[str, str] | None = None) -> subprocess.Popen:
    if not sys.executable:
        raise RuntimeError("Cannot launch BEAT host: sys.executable is empty")
    identifier = r.key_id(key)
    environment = {name: value for name, value in (os.environ if environment is None else environment).items()
                   if not name.upper().startswith("WG2_BEAT_TEST_")}
    root = app_root(environ=environment)
    # Packaged Windows Python can ignore cwd through its isolated ._pth file.
    # Use sys.executable through native admission; wg-python._pth includes app.
    # PYTHONPATH additionally supports source and nonisolated bundled runtimes.
    environment["WG2_APP_ROOT"] = str(root)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), environment.get("PYTHONPATH"))))
    command = [sys.executable, "-m", HOST_MODULE,
               "--key", str(r.launch_spec_path(identifier, directory)), "--dir", str(directory),
               "--idle-timeout", str(idle_timeout), "--ready", "--startup-timeout", str(timeout)]
    with contextlib.ExitStack() as stack:
        log = r.log_path(identifier, directory)
        fd = stack.enter_context(r._private_file(log, create=True, append=os.name != "nt"))
        os.ftruncate(fd, 0)
        if os.name == "nt":
            # The inherited handle must append at the OS level. It deliberately
            # lacks FILE_WRITE_DATA, so truncate through the separate fd above.
            fd = stack.enter_context(r._private_file(log, append=True))
        process = subprocess.Popen(command, cwd=str(root), env=environment, stdin=subprocess.DEVNULL,
                                   stdout=fd, stderr=subprocess.STDOUT, close_fds=True, **detached_options())
    # Detached children still need reaping. This thread also covers failures,
    # idle exit and authenticated shutdown, even after start_host has returned.
    threading.Thread(target=process.wait, daemon=True, name=f"beat-host-reap-{process.pid}").start()
    return process


def _bootstrap_record(
    raw: dict[str, Any], key: dict[str, Any], directory: Path, child_pid: int, child_start: str | None,
) -> r.HostRecord:
    data = raw["record"]
    record = r.HostRecord(data["key"], data["pid"], data["token"],
                          Endpoint.from_dict(data["endpoint"]), data["pid_start"])
    r.validate_record(record, key, directory)
    # Both a direct interpreter and an owned launcher/interpreter pair work,
    # regardless of the bundle's executable names.
    owned = (raw.get("launcher_pid") == child_pid and type(raw.get("launcher_pid")) is int
             and child_start is not None and raw.get("launcher_start") == child_start)
    if (data != record.as_dict() or record.pid_start != r.process_start_identity(record.pid)
            or not (record.pid == child_pid or owned)):
        raise r.RecordRefused("Bootstrap host process identity mismatch")
    return record


def start_host(
    key: dict[str, Any], directory: Path | None = None, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    timeout: float = 10.0, environment: Mapping[str, str] | None = None,
) -> r.HostRecord:
    """Return an authenticated lifecycle record; numerical adoption is PR 19.

    Never replace foreign records or signal their PIDs. The parent owns spawn
    exclusion through recheck, launch, private readiness and canonical publication.
    """
    key = validate_key(key)
    if not all(math.isfinite(v) and v > 0 for v in (timeout, idle_timeout)):
        raise ValueError("Host timeouts must be positive and finite")
    directory = r.private_directory(paths.worker_dir() if directory is None else directory)
    identifier = r.key_id(key)
    deadline = time.monotonic() + timeout
    with r.SpawnLock(r.spawn_lock_path(identifier, directory), timeout=remaining_time(deadline)) as lock:
        last_refusal: r.RecordRefused | None = None
        probe_timeout = RECOVERY_INTERVAL
        while (current := r.read_record(identifier, directory)) is not None:
            if time.monotonic() >= deadline:
                raise last_refusal or r.RecordRefused("Unverified live host retained; start deadline exceeded")
            try:
                with connect_authenticated(current, key, directory,
                                           timeout=min(probe_timeout, remaining_time(deadline))):
                    return current
            except (ConnectionError, r.RecordRefused, TimeoutError) as exc:
                # Recovery must never shut down another client's healthy host.
                # Uncertain/slow live hosts get increasingly long probes.
                if time.monotonic() >= deadline:
                    raise r.RecordRefused("Unverified live host retained; start deadline exceeded") from exc
                try:
                    cleanup_host(current, key, directory, lock=lock,
                                 timeout=min(RECOVERY_INTERVAL, remaining_time(deadline)), prune_only=True)
                except (r.RecordRefused, TimeoutError) as refused:
                    last_refusal = r.RecordRefused(str(refused))
                    if time.monotonic() >= deadline:
                        raise last_refusal from exc
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
                probe_timeout *= 2
        sweep_orphan_socket(identifier, directory, lock=lock, timeout=remaining_time(deadline))
        r.write_launch_spec(key, directory)
        ready = ready_path(identifier, directory)
        if ready.exists():
            read_private_json(ready)  # Refuse linked/nonprivate bootstrap residue.
            r.unlink_record(ready)
        launch_options = {} if environment is None else {"environment": environment}
        process = _launch(key, directory, idle_timeout, remaining_time(deadline), **launch_options)
        published: r.HostRecord | None = None
        owned_interpreter: r.HostRecord | None = None
        launcher_start = r.process_start_identity(process.pid)
        try:
            while True:
                remaining_time(deadline)
                try:
                    raw = read_private_json(ready)
                except FileNotFoundError:
                    if process.poll() is not None:
                        raise RuntimeError(f"BEAT host exited ({process.returncode}); see {r.log_path(identifier, directory)}")
                    time.sleep(0.02)
                    continue
                candidate = _bootstrap_record(raw, key, directory, process.pid, launcher_start)
                if candidate.pid != process.pid:
                    owned_interpreter = candidate
                r.write_record(candidate, directory)
                published = candidate
                with connect_authenticated(published, key, directory, deadline=deadline):
                    return published
        except BaseException:
            # Only this launch's verified interpreter and Popen child. Cleanup
            # failures must not mask the original startup error.
            if owned_interpreter is not None:
                with contextlib.suppress(Exception):
                    _terminate_owned_interpreter(owned_interpreter)
            with contextlib.suppress(Exception):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            with contextlib.suppress(Exception):
                current = r.read_record(identifier, directory)
                if current is not None and current == published:
                    cleanup_host(current, key, directory, lock=lock, timeout=2.0, prune_only=True)
                elif current is None:
                    sweep_orphan_socket(identifier, directory, lock=lock)
            raise
        finally:
            with contextlib.suppress(OSError, r.RecordRefused):
                r.unlink_record(ready)


def _terminate_owned_interpreter(record: r.HostRecord) -> None:
    """Stop a verified bootstrap interpreter using the same Windows handle."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "GetProcessTimes": ([w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)], w.BOOL),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, record.pid)
    if not handle:
        return
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        if record.pid_start == f"windows:{created}":
            if not kernel.TerminateProcess(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            kernel.WaitForSingleObject(handle, 2000)
    finally:
        kernel.CloseHandle(handle)
