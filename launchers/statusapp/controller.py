"""Headless lifecycle owner for the Waveguide Generator status application."""

from __future__ import annotations

import atexit
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server.platform.instance import PORT_SCAN_COUNT, requested_port, select_port


HOST = "127.0.0.1"


class ServiceState(str, Enum):
    """A lamp's display state."""

    STARTING = "starting"
    OK = "ok"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class LampStatus:
    """State and human-readable explanation for one status lamp."""

    state: ServiceState
    reason: str


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    """One immutable controller observation for the view or a headless caller."""

    backend: LampStatus
    frontend: LampStatus
    url: str
    pid: int | None
    exit_code: int | None

    @property
    def running(self) -> bool:
        return self.pid is not None and self.exit_code is None


RequestProbe = Callable[[str, float], tuple[int, bytes]]
PortSelector = Callable[[int, str, int], int]


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "WaveguideGenerator-StatusApp"})
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read(4096)


def _select_port(preferred: int, host: str, scan_count: int) -> int:
    return select_port(preferred, host=host, scan_count=scan_count)


def _probe_failure(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, URLError):
        return str(exc.reason)
    if isinstance(exc, TimeoutError | socket.timeout):
        return "request timed out"
    return str(exc) or type(exc).__name__


def _installer_hint() -> str:
    if sys.platform == "darwin":
        return "installers/macos/install-wg.command"
    if os.name == "nt":
        return r"installers\windows\install-and-update.bat"
    return "installers/linux/install.sh"


def _windows_job_for(process: subprocess.Popen[str]) -> object | None:
    """Put the server tree in a kill-on-close Job Object when Win32 permits it."""

    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x00002000

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    configured = kernel32.SetInformationJobObject(
        handle,
        job_object_extended_limit_information,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    if not configured or not kernel32.AssignProcessToJobObject(handle, process_handle):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)

    class WindowsJob:
        def __init__(self, job_handle: object) -> None:
            self.handle = job_handle

        def close(self) -> None:
            if self.handle:
                kernel32.CloseHandle(self.handle)
                self.handle = None

    return WindowsJob(handle)


class StatusController:
    """Start, observe, and stop exactly one local WG server process tree.

    The class deliberately has no tkinter dependency. ``start()``, ``poll()``,
    and ``stop()`` are safe to call from view worker threads; ``close()`` is an
    alias for ``stop()`` for window-close and context-manager code.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        python_executable: str | Path | None = None,
        server_args: Sequence[str] = (),
        server_command: Sequence[str] | None = None,
        environ: dict[str, str] | None = None,
        request_probe: RequestProbe = _http_get,
        port_selector: PortSelector = _select_port,
        request_timeout: float = 0.35,
        shutdown_timeout: float = 8.0,
        port_scan_count: int = PORT_SCAN_COUNT,
    ) -> None:
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.python_executable = Path(python_executable or sys.executable).resolve()
        self.server_args = tuple(server_args)
        self.server_command = tuple(server_command) if server_command is not None else None
        self.environ = dict(os.environ if environ is None else environ)
        self.request_probe = request_probe
        self.port_selector = port_selector
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.port_scan_count = port_scan_count

        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._output: deque[str] = deque(maxlen=40)
        self._output_thread: threading.Thread | None = None
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._control_path: Path | None = None
        self._ready_path: Path | None = None
        self._windows_job: object | None = None
        self._registered_atexit = False
        self._port: int | None = None
        self._snapshot = StatusSnapshot(
            backend=LampStatus(ServiceState.STOPPED, "Not started"),
            frontend=LampStatus(ServiceState.STOPPED, "Not started"),
            url="",
            pid=None,
            exit_code=None,
        )

    @property
    def url(self) -> str:
        with self._lock:
            return self._snapshot.url

    @property
    def process(self) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._process

    def __enter__(self) -> StatusController:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _set_error(self, backend_reason: str, frontend_reason: str) -> StatusSnapshot:
        self._snapshot = StatusSnapshot(
            backend=LampStatus(ServiceState.ERROR, backend_reason),
            frontend=LampStatus(ServiceState.ERROR, frontend_reason),
            url=self._snapshot.url,
            pid=None,
            exit_code=self._snapshot.exit_code,
        )
        return self._snapshot

    def _preferred_port(self) -> int:
        cli_port: int | None = None
        for index, argument in enumerate(self.server_args):
            if argument == "--port" and index + 1 < len(self.server_args):
                cli_port = int(self.server_args[index + 1])
            elif argument.startswith("--port="):
                cli_port = int(argument.partition("=")[2])
        return requested_port(cli_port, environ=self.environ)

    def _command(self, port: int, control_path: Path) -> list[str]:
        if self.server_command is None:
            command = [str(self.python_executable), str(self.repo_root / "launch" / "serve.py")]
        else:
            command = list(self.server_command)
        command.extend(self.server_args)
        command.extend(
            (
                "--no-browser",
                "--port",
                str(port),
                "--status-control",
                str(control_path),
                "--parent-pid",
                str(os.getpid()),
            )
        )
        return command

    def _collect_output(self, stream: IO[str]) -> None:
        try:
            for line in stream:
                clean = line.strip()
                if clean:
                    with self._lock:
                        self._output.append(clean)
        finally:
            stream.close()

    def start(self) -> StatusSnapshot:
        """Run preflight checks and start the owned server process."""

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._snapshot

            dist_index = self.repo_root / "frontend" / "dist" / "index.html"
            if not dist_index.is_file():
                reason = (
                    f"frontend/dist missing — run {_installer_hint()} or scripts/fetch_spa.py"
                )
                return self._set_error("Backend not started because the interface is missing", reason)

            serve_script = self.repo_root / "launch" / "serve.py"
            if self.server_command is None and not serve_script.is_file():
                return self._set_error(
                    f"Server entry missing: {serve_script}",
                    "Frontend cannot be served without the backend",
                )

            try:
                preferred = self._preferred_port()
                port = self.port_selector(preferred, HOST, self.port_scan_count)
            except (OSError, TypeError, ValueError) as exc:
                return self._set_error(
                    str(exc),
                    "Frontend cannot be served because no local port is available",
                )

            self._temporary_directory = tempfile.TemporaryDirectory(prefix="wg2-statusapp-")
            self._control_path = Path(self._temporary_directory.name) / "stop"
            self._ready_path = Path(self._temporary_directory.name) / "ready.json"
            self._port = port
            url = f"http://{HOST}:{port}/"
            environment = dict(self.environ)
            environment["WG2_NO_BROWSER"] = "1"
            popen_options: dict[str, object] = {
                "cwd": str(self.repo_root),
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if os.name == "nt":
                popen_options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                popen_options["start_new_session"] = True

            try:
                process = subprocess.Popen(self._command(port, self._control_path), **popen_options)
            except OSError as exc:
                self._cleanup_temporary_directory()
                return self._set_error(
                    f"Could not start the backend: {exc}",
                    "Frontend cannot be served because the backend did not start",
                )

            self._process = process
            try:
                self._windows_job = _windows_job_for(process)
            except OSError as exc:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2.0)
                if process.stdout is not None:
                    process.stdout.close()
                self._process = None
                self._cleanup_temporary_directory()
                return self._set_error(
                    f"Could not establish Windows process-tree ownership: {exc}",
                    "Frontend not started because clean shutdown could not be guaranteed",
                )
            self._snapshot = StatusSnapshot(
                backend=LampStatus(ServiceState.STARTING, "Waiting for /health"),
                frontend=LampStatus(ServiceState.STARTING, "Waiting for the SPA route"),
                url=url,
                pid=process.pid,
                exit_code=None,
            )
            if process.stdout is not None:
                self._output_thread = threading.Thread(
                    target=self._collect_output,
                    args=(process.stdout,),
                    name="wg2-status-output",
                    daemon=True,
                )
                self._output_thread.start()
            if not self._registered_atexit:
                atexit.register(self.stop)
                self._registered_atexit = True
            return self._snapshot

    def _exit_reason(self, return_code: int) -> str:
        detail = self._output[-1] if self._output else "no diagnostic output"
        return f"Server exited with code {return_code}: {detail}"

    def _already_running_url(self) -> str | None:
        for line in reversed(self._output):
            match = re.search(r"http://127\.0\.0\.1:\d+/", line)
            if match is not None:
                return match.group()
        return None

    def _ready_url(self) -> str | None:
        ready_path = self._ready_path
        if ready_path is None:
            return None
        try:
            payload = json.loads(ready_path.read_text(encoding="utf-8"))
            host = str(payload["host"])
            port = int(payload["port"])
        except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if host != HOST or not 1 <= port <= 65535:
            return None
        self._port = port
        return f"http://{HOST}:{port}/"

    def poll(self) -> StatusSnapshot:
        """Poll the process, the real backend health route, and the served SPA."""

        with self._lock:
            process = self._process
            if process is None:
                return self._snapshot
            ready_url = self._ready_url()
            if ready_url is not None and ready_url != self._snapshot.url:
                self._snapshot = StatusSnapshot(
                    backend=self._snapshot.backend,
                    frontend=self._snapshot.frontend,
                    url=ready_url,
                    pid=self._snapshot.pid,
                    exit_code=self._snapshot.exit_code,
                )
            return_code = process.poll()
            observing_existing = False
            if return_code is not None:
                existing_url = self._already_running_url() if return_code == 2 else None
                if existing_url is None:
                    reason = self._exit_reason(return_code)
                    self._snapshot = StatusSnapshot(
                        backend=LampStatus(ServiceState.ERROR, reason),
                        frontend=LampStatus(ServiceState.ERROR, "Frontend unavailable: " + reason),
                        url=self._snapshot.url,
                        pid=None,
                        exit_code=return_code,
                    )
                    return self._snapshot
                observing_existing = True
                url = existing_url
            else:
                url = self._snapshot.url

        try:
            status, body = self.request_probe(url + "health", self.request_timeout)
            payload = json.loads(body)
            if status != 200 or not isinstance(payload, dict) or "version" not in payload:
                raise RuntimeError("unexpected /health response")
            suffix = " — already-running instance" if observing_existing else ""
            backend = LampStatus(ServiceState.OK, f"Healthy — v{payload['version']}{suffix}")
        except (OSError, ValueError, RuntimeError, HTTPError, URLError) as exc:
            backend = LampStatus(ServiceState.ERROR, "/health failed: " + _probe_failure(exc))

        dist_index = self.repo_root / "frontend" / "dist" / "index.html"
        if not dist_index.is_file():
            frontend = LampStatus(
                ServiceState.ERROR,
                f"frontend/dist missing — run {_installer_hint()} or scripts/fetch_spa.py",
            )
        else:
            try:
                status, body = self.request_probe(url, self.request_timeout)
                normalized = body.lower()
                if status != 200 or b"<html" not in normalized:
                    raise RuntimeError("SPA route did not return HTML")
                frontend = LampStatus(ServiceState.OK, "SPA is being served")
            except (OSError, RuntimeError, HTTPError, URLError) as exc:
                frontend = LampStatus(ServiceState.ERROR, "SPA route failed: " + _probe_failure(exc))

        with self._lock:
            if self._process is not process or (
                not observing_existing and process.poll() is not None
            ):
                return self.poll()
            self._snapshot = StatusSnapshot(
                backend=backend,
                frontend=frontend,
                url=url,
                pid=None if observing_existing else process.pid,
                exit_code=2 if observing_existing else None,
            )
            return self._snapshot

    def _posix_group_exists(self, process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _stop_posix_group(self, process_group: int) -> None:
        if not self._posix_group_exists(process_group):
            return
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self._posix_group_exists(process_group):
            time.sleep(0.05)
        if self._posix_group_exists(process_group):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _cleanup_temporary_directory(self) -> None:
        temporary_directory = self._temporary_directory
        self._temporary_directory = None
        self._control_path = None
        self._ready_path = None
        if temporary_directory is not None:
            temporary_directory.cleanup()

    def stop(self) -> StatusSnapshot:
        """Request graceful shutdown, then guarantee the entire tree is gone."""

        with self._lock:
            process = self._process
            if process is None:
                return self._snapshot
            self._snapshot = StatusSnapshot(
                backend=LampStatus(ServiceState.STARTING, "Shutting down cleanly"),
                frontend=LampStatus(ServiceState.STARTING, "Shutting down cleanly"),
                url=self._snapshot.url,
                pid=process.pid,
                exit_code=None,
            )
            control_path = self._control_path

        if control_path is not None:
            try:
                control_path.write_text("stop\n", encoding="utf-8")
            except OSError:
                pass
        try:
            process.wait(timeout=self.shutdown_timeout)
        except subprocess.TimeoutExpired:
            pass

        if os.name == "nt":
            job = self._windows_job
            if job is None:
                raise RuntimeError("Windows server process has no lifecycle Job Object")
            job.close()  # type: ignore[attr-defined]
        else:
            self._stop_posix_group(process.pid)
            if process.poll() is None:
                try:
                    process.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    pass

        return_code = process.poll()
        if return_code is None:
            process.kill()
            return_code = process.wait(timeout=2.0)
        output_thread = self._output_thread
        if output_thread is not None:
            output_thread.join(timeout=1.0)

        with self._lock:
            self._process = None
            self._output_thread = None
            self._windows_job = None
            self._cleanup_temporary_directory()
            self._snapshot = StatusSnapshot(
                backend=LampStatus(ServiceState.STOPPED, "Stopped"),
                frontend=LampStatus(ServiceState.STOPPED, "Stopped"),
                url=self._snapshot.url,
                pid=None,
                exit_code=return_code,
            )
            if self._registered_atexit:
                atexit.unregister(self.stop)
                self._registered_atexit = False
            return self._snapshot

    close = stop
