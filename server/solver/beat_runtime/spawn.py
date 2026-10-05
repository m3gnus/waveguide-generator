"""Spawn one detached host under exclusion and authenticate its publication."""

from __future__ import annotations

import contextlib
import errno
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from server.platform.paths import app_root

from . import paths, registry as r
from .cleanup import cleanup_host, connect_authenticated, sweep_orphan_socket
from .host import DEFAULT_IDLE_TIMEOUT, read_private_json, ready_path, validate_key
from .ipc import Endpoint, remaining_time


def detached_options(*, windows: bool | None = None) -> dict[str, Any]:
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        # Remain in the launcher's Job Object: packaged Windows Quit still kills
        # the host. Breakaway permission/refusal belongs to design PR 22.
        return {"creationflags": 0x00000200 | 0x00000008}  # NEW_PROCESS_GROUP | DETACHED_PROCESS
    return {"start_new_session": True}


def _launch(key: dict[str, Any], directory: Path, idle_timeout: float, timeout: float) -> subprocess.Popen:
    identifier = r.key_id(key)
    environment = os.environ.copy()
    root = app_root(environ=environment)
    # Packaged Windows Python can ignore cwd through its isolated ._pth file.
    # Use sys.executable through native admission; wg-python._pth includes app.
    # PYTHONPATH additionally supports source and nonisolated bundled runtimes.
    environment["WG2_APP_ROOT"] = str(root)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), environment.get("PYTHONPATH"))))
    command = [sys.executable, "-m", "server.solver.beat_runtime.host",
               "--key", str(r.launch_spec_path(identifier, directory)), "--dir", str(directory),
               "--idle-timeout", str(idle_timeout), "--ready", "--startup-timeout", str(timeout)]
    with r._private_file(r.log_path(identifier, directory), create=True) as fd:
        os.lseek(fd, 0, os.SEEK_END)
        process = subprocess.Popen(command, cwd=str(root), env=environment, stdin=subprocess.DEVNULL,
                                   stdout=fd, stderr=subprocess.STDOUT, close_fds=True, **detached_options())
    # Detached children still need reaping. This thread also covers failures,
    # idle exit and authenticated shutdown, even after start_host has returned.
    threading.Thread(target=process.wait, daemon=True, name=f"beat-host-reap-{process.pid}").start()
    return process


def _native_launcher() -> bool:
    return os.name == "nt" and Path(sys.executable).name.casefold() == "waveguide generator.exe"


def _bootstrap_record(
    raw: dict[str, Any], key: dict[str, Any], directory: Path, child_pid: int, child_start: str | None,
) -> r.HostRecord:
    data = raw["record"]
    record = r.HostRecord(data["key"], data["pid"], data["token"],
                          Endpoint.from_dict(data["endpoint"]), data["pid_start"])
    r.validate_record(record, key, directory)
    # Native Windows sys.executable is an admission stub. Its interpreter has
    # another PID; preserve native admission and verify the parent/start link.
    owned = (raw.get("launcher_pid") == child_pid and type(raw.get("launcher_pid")) is int
             and child_start is not None and raw.get("launcher_start") == child_start)
    if (data != record.as_dict() or record.pid_start != r.process_start_identity(record.pid)
            or not (owned if _native_launcher() else record.pid == child_pid)):
        raise r.RecordRefused("Bootstrap host process identity mismatch")
    return record


def start_host(
    key: dict[str, Any], directory: Path | None = None, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    timeout: float = 10.0,
) -> r.HostRecord:
    """Return an authenticated lifecycle record; numerical adoption is PR 19.

    Never replace foreign records or signal their PIDs. The parent owns spawn
    exclusion through recheck, launch, private readiness and canonical publication.
    """
    validate_key(key)
    if not all(math.isfinite(v) and v > 0 for v in (timeout, idle_timeout)):
        raise ValueError("Host timeouts must be positive and finite")
    directory = r.private_directory(paths.worker_dir() if directory is None else directory)
    identifier = r.key_id(key)
    deadline = time.monotonic() + timeout
    with r.SpawnLock(r.spawn_lock_path(identifier, directory), timeout=remaining_time(deadline)) as lock:
        current = r.read_record(identifier, directory)
        if current is not None:
            try:
                with connect_authenticated(current, key, directory, deadline=deadline):
                    return current
            except ConnectionError as exc:
                if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED}:
                    raise r.RecordRefused("Unverified host retained") from exc
                cleanup_host(current, key, directory, lock=lock, timeout=remaining_time(deadline))
        sweep_orphan_socket(identifier, directory, lock=lock, timeout=remaining_time(deadline))
        r.write_launch_spec(key, directory)
        ready = ready_path(identifier, directory)
        if ready.exists():
            read_private_json(ready)  # Refuse linked/nonprivate bootstrap residue.
            r.unlink_record(ready)
        process = _launch(key, directory, idle_timeout, remaining_time(deadline))
        published: r.HostRecord | None = None
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
                r.write_record(candidate, directory)
                published = candidate
                with connect_authenticated(published, key, directory, deadline=deadline):
                    return published
        except BaseException:
            # Only our recorded Popen child, never a PID taken from a registry.
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2.0)
            current = r.read_record(identifier, directory)
            if current is not None and current == published:
                cleanup_host(current, key, directory, lock=lock, timeout=2.0)
            elif current is None:
                sweep_orphan_socket(identifier, directory, lock=lock)
            raise
        finally:
            with contextlib.suppress(FileNotFoundError):
                r.unlink_record(ready)
