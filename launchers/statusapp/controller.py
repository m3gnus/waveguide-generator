"""Headless lifecycle owner for the Waveguide Generator status application."""

from __future__ import annotations

import atexit
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from http.client import HTTPConnection, HTTPException
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
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.frontend_freshness import (
    frontend_freshness,
    installer_hint,
    refresh_hint,
)
from server.platform.instance import requested_port
from server.platform.paths import app_root, resolve_data_dir
from .updater import (
    BundleUpdateRequest,
    UpdateHandoffError,
    UpdateRequest,
    consume_update_request,
    launch_bundle_update_handoff,
    launch_update_handoff,
)


HOST = "127.0.0.1"

#: How often the adopted-instance path asks a server it does not own whether it
#: is still there. Deliberately far apart: the only cost of noticing late is
#: that the lamps stay green a little longer than the truth, and the thing that
#: was measured -- an idle application issuing 8.2 HTTP requests a second -- was
#: entirely made of probes far more frequent than they needed to be.
ADOPTED_PROBE_INTERVAL = 30.0
#: How long to wait before asking a second time. A probe is a fallible reading
#: in a way a process handle is not, so a single failure is treated as a
#: question rather than an answer.
ADOPTED_RETRY_DELAY = 2.0


class ServiceState(str, Enum):
    """A lamp's display state."""

    STARTING = "starting"
    OK = "ok"
    WARNING = "warning"
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


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "WaveguideGenerator-StatusApp"})
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read(4096)


class PooledProbe:
    """One kept-alive loopback connection, reused by every liveness probe.

    ``urlopen`` cannot pool. Each call opens a socket, completes a handshake,
    and tears the whole thing down again, which is how a poll loop asking two
    questions four times a second turned into eight fresh TCP connections a
    second on loopback for the entire life of the application.

    Liveness is rare now, but rare is not a reason to pay a connection per
    probe -- and an already-open connection is what makes the immediate second
    opinion in :meth:`StatusController._await_adopted_loss` free, which is the
    probe that decides whether a server is really gone.

    One lock, because a pooled connection is a single-request-at-a-time
    resource and nothing about the callers guarantees they are serialised.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: HTTPConnection | None = None
        self._origin: tuple[str, int] | None = None

    def __call__(self, url: str, timeout: float) -> tuple[int, bytes]:
        parts = urlsplit(url)
        origin = (parts.hostname or HOST, parts.port or 80)
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        with self._lock:
            # A kept-alive connection is closed from the other end long before
            # the next probe is due -- uvicorn drops an idle one after five
            # seconds -- so a first attempt that fails on a socket this call
            # merely inherited says nothing at all about the server. Only a
            # failure on a connection this call opened itself is evidence.
            for attempt in (1, 2):
                inherited = self._connection is not None and self._origin == origin
                connection = self._connect(origin, timeout)
                try:
                    connection.request(
                        "GET", target, headers={"User-Agent": "WaveguideGenerator-StatusApp"}
                    )
                    response = connection.getresponse()
                    body = response.read(4096)
                except (OSError, HTTPException):
                    self._drop()
                    if not inherited or attempt == 2:
                        raise
                    continue
                if not response.isclosed():
                    # A body larger than the read above would leave the socket
                    # parked mid-response for the next caller. Draining it is
                    # unbounded work for a probe; dropping the connection is not.
                    self._drop()
                return int(response.status), body
        raise OSError("the pooled connection could not be established")

    def _connect(self, origin: tuple[str, int], timeout: float) -> HTTPConnection:
        if self._connection is None or self._origin != origin:
            self._drop()
            self._connection = HTTPConnection(origin[0], origin[1], timeout=timeout)
            self._origin = origin
            return self._connection
        connection = self._connection
        # ``timeout`` is only consulted when the connection is (re)established,
        # so an inherited socket needs telling separately.
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)
        return connection

    def _drop(self) -> None:
        connection, self._connection = self._connection, None
        self._origin = None
        if connection is not None:
            connection.close()

    def close(self) -> None:
        with self._lock:
            self._drop()


def _probe_failure(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, URLError):
        return str(exc.reason)
    if isinstance(exc, TimeoutError | socket.timeout):
        return "request timed out"
    return str(exc) or type(exc).__name__


def missing_frontend_reason() -> str:
    """Why the interface is missing and what fixes it.

    Shared so the status window and terminal mode cannot describe the same
    condition differently -- terminal mode reaches this through __main__, which
    guards before handing over to a server that would otherwise raise a
    starlette error from several frames deep.
    """

    return f"frontend/dist missing — run {installer_hint()} or scripts/fetch_spa.py"


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
        liveness_probe: RequestProbe | None = None,
        request_timeout: float = 0.35,
        shutdown_timeout: float = 8.0,
        lock_conflict_timeout: float = 15.0,
        lock_conflict_interval: float = 0.75,
        adopted_probe_interval: float = ADOPTED_PROBE_INTERVAL,
        adopted_retry_delay: float = ADOPTED_RETRY_DELAY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.environ = dict(os.environ if environ is None else environ)
        self.repo_root = Path(repo_root or app_root(environ=self.environ)).resolve()
        self.python_executable = Path(python_executable or sys.executable).resolve()
        self.server_args = tuple(server_args)
        self.server_command = tuple(server_command) if server_command is not None else None
        self.request_probe = request_probe
        # Separate from ``request_probe`` on purpose. That one runs a handful of
        # times during startup and is the seam every existing test replaces;
        # this one is the only probe that recurs for as long as the application
        # is open, so it is the only one whose connection cost matters.
        self.liveness_probe = PooledProbe() if liveness_probe is None else liveness_probe
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        # How long an unserving lock holder is treated as one that is still
        # leaving rather than one that is in the way. Comfortably longer than
        # ``shutdown_timeout``, because that is the wait the *other* launcher
        # is sitting in, and it is the whole reason this window exists.
        self.lock_conflict_timeout = lock_conflict_timeout
        self.lock_conflict_interval = lock_conflict_interval
        self.adopted_probe_interval = adopted_probe_interval
        self.adopted_retry_delay = adopted_retry_delay
        self.clock = clock

        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._output: deque[str] = deque(maxlen=40)
        self._output_thread: threading.Thread | None = None
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._control_path: Path | None = None
        self._ready_path: Path | None = None
        self._update_request_path: Path | None = None
        self._windows_job: object | None = None
        self._registered_atexit = False
        self._port: int | None = None
        self._frontend_source_warning: str | None = None
        self._frontend_served: LampStatus | None = None
        self._lock_conflict_deadline: float | None = None
        self._lock_conflict_next = 0.0
        self._watcher: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._backend_lost = False
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

    @property
    def update_request_path(self) -> Path | None:
        with self._lock:
            return self._update_request_path

    @property
    def data_dir(self) -> Path:
        return self._data_dir()

    @property
    def logs_dir(self) -> Path:
        return self._data_dir() / "logs"

    def open_logs_folder(self) -> Path:
        """Show the logs in the platform's file manager.

        The in-application report dialog is the better route to a log and is
        the one the user guide sends people to -- but it needs a backend that
        started. This is the same folder from the one window that is still
        there when the backend did not, which makes it the only route that
        covers "it will not open at all", the report that is hardest to act on
        and commonest to receive.
        """

        target = self.logs_dir
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            command = ["open", str(target)]
        elif sys.platform == "win32":
            command = ["explorer", str(target)]
        else:
            command = ["xdg-open", str(target)]
        options: dict[str, object] = {}
        if os.name == "nt":
            # ``explorer`` is a console-subsystem launcher; without this the
            # status window flashes a CMD box every time this is pressed.
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # The return code is deliberately not checked: ``explorer`` exits 1 even
        # when it opened the window, and a missing file manager is not a reason
        # to disturb the status window somebody is reading.
        subprocess.Popen(command, **options)
        return target

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

    def _data_dir(self) -> Path:
        override: str | None = None
        for index, argument in enumerate(self.server_args):
            if argument == "--data-dir" and index + 1 < len(self.server_args):
                override = self.server_args[index + 1]
            elif argument.startswith("--data-dir="):
                override = argument.partition("=")[2]
        return resolve_data_dir(override, environ=self.environ)

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
                    missing_frontend_reason()
                )
                return self._set_error("Backend not started because the interface is missing", reason)
            frontend_is_fresh, freshness_reason = frontend_freshness(self.repo_root)
            self._frontend_source_warning = None if frontend_is_fresh else freshness_reason
            # The remembered SPA verdict below belongs to the server that was
            # serving it, and the freshness caveat baked into it was computed on
            # the line above. Neither survives into a new process.
            self._frontend_served = None
            self._backend_lost = False

            serve_script = self.repo_root / "launch" / "serve.py"
            if self.server_command is None and not serve_script.is_file():
                return self._set_error(
                    f"Server entry missing: {serve_script}",
                    "Frontend cannot be served without the backend",
                )

            try:
                preferred = self._preferred_port()
            except (TypeError, ValueError) as exc:
                return self._set_error(
                    str(exc),
                    "Frontend cannot be served because the local port is invalid",
                )

            self._temporary_directory = tempfile.TemporaryDirectory(prefix="wg2-statusapp-")
            self._control_path = Path(self._temporary_directory.name) / "stop"
            self._ready_path = Path(self._temporary_directory.name) / "ready.json"
            self._update_request_path = Path(self._temporary_directory.name) / "update.json"
            environment = dict(self.environ)
            environment["WG2_NO_BROWSER"] = "1"
            # stdout and stderr are merged into one pipe, where stdout is block
            # buffered and stderr is not, so a dependency's start-up print
            # otherwise lands after the logging that explains a failure. This
            # keeps the collected output in the order it was written.
            environment["PYTHONUNBUFFERED"] = "1"
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
                process = subprocess.Popen(
                    self._command(preferred, self._control_path), **popen_options
                )
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
                # The child performs the authoritative instance-lock check and
                # reserves the socket without a probe/bind race. Publishing a
                # URL before its ready file arrives can advertise a fallback
                # that will never be bound when an existing WG instance owns
                # the preferred port.
                url="",
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

    def _diagnostic_line(self) -> str | None:
        """The last line the server wrote that actually reports a failure.

        Taking the last line of the merged stream is wrong, and reliably so.
        The child's stdout is a pipe, so ``print`` from any imported package is
        block-buffered and flushes when the process exits -- after every log
        record, which goes to stderr unbuffered. A dependency's harmless
        start-up notice therefore arrives last no matter when it was written,
        and gets reported as the reason the server died. bempp announces a
        missing optional Gmsh executable that way on every start.

        So look for a line that claims to be a failure: the level field of the
        server's own log format, or one of the plain messages ``launch/serve``
        prints to stderr before logging exists.
        """

        for line in reversed(self._output):
            if " ERROR " in line or " CRITICAL " in line:
                return line
            if line.startswith("Waveguide Generator did not start"):
                return line
        return None

    def _exit_reason(self, return_code: int) -> str:
        detail = self._diagnostic_line()
        if detail is None and return_code == 0:
            # Exit 0 is a clean shutdown. Naming an arbitrary output line here
            # would attribute the stop to whatever happened to be on stdout.
            detail = "the server shut down without reporting an error"
        elif detail is None:
            detail = self._output[-1] if self._output else "no diagnostic output"
        return f"Server exited with code {return_code}: {detail}. {self._log_location()}"

    def _log_location(self) -> str:
        """Where the whole story is, spelled out rather than described.

        A user told only that the server exited should not also have to work
        out where their platform keeps the log that says why.
        """

        try:
            from server.platform.logging_setup import LOG_FILENAME
            from server.platform.paths import data_paths

            return f"The full log is at {data_paths(self._data_dir()).logs / LOG_FILENAME}"
        except (ImportError, OSError, ValueError):  # pragma: no cover - unnameable path
            return "The full log is in server.log in the Waveguide Generator log folder"

    def _already_running_url(self) -> str | None:
        for line in reversed(self._output):
            match = re.search(r"http://127\.0\.0\.1:\d+/", line)
            if match is not None:
                return match.group()
        return None

    def _instance_answers(self, url: str, probe: RequestProbe | None = None) -> bool:
        """Whether the instance holding the lock is actually serving."""

        try:
            status, body = (probe or self.request_probe)(url + "health", self.request_timeout)
            payload = json.loads(body)
        except (OSError, ValueError, HTTPError, URLError, HTTPException):
            return False
        return status == 200 and isinstance(payload, dict) and "version" in payload

    def _discard_dead_child(self) -> None:
        """Release everything the exited child still owns, before another starts.

        ``start()`` overwrites these fields, so without this a retry would
        strand the Job Object handle -- which is what guarantees the tree dies
        -- along with the temporary directory holding the stop and ready files.
        The collected output goes too: the next attempt must not be diagnosed
        from the previous attempt's lines.
        """

        job, self._windows_job = self._windows_job, None
        if job is not None:
            job.close()  # type: ignore[attr-defined]
        thread, self._output_thread = self._output_thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        process, self._process = self._process, None
        if process is not None and process.stdout is not None:
            process.stdout.close()
        self._output.clear()
        self._frontend_served = None
        self._cleanup_temporary_directory()

    def _await_lock_release(self) -> StatusSnapshot | None:
        """Keep starting while the lock holder is still on its way out.

        Exit 2 means another process owns the instance lock, and adopting that
        instance is usually right -- launching twice is a request to use the
        application, not an error. It is wrong when the named instance does not
        answer, because the overwhelmingly common cause is the *previous*
        session still inside its own shutdown: closing a wedged server spends
        the launcher's whole ``shutdown_timeout`` before the Job Object takes
        the tree down, and a user who reopens the application in those seconds
        lands squarely in the middle of it.

        Adopting a server that is about to be killed hands the window a dead
        backend; reporting the conflict tells the user their application is
        broken when waiting a moment would have done. So wait, and say nothing:
        ``None`` once the wait is no longer plausible, and the caller reports
        the conflict properly.
        """

        now = self.clock()
        if self._lock_conflict_deadline is None:
            self._lock_conflict_deadline = now + self.lock_conflict_timeout
            self._lock_conflict_next = now
        if now >= self._lock_conflict_deadline:
            return None
        if now < self._lock_conflict_next:
            return self._snapshot
        self._lock_conflict_next = now + self.lock_conflict_interval
        self._discard_dead_child()
        return self.start()

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
        """Poll the process, the real backend health route, and the served SPA.

        A startup and transition operation, not a heartbeat. Callers used to run
        this on a timer for the whole life of the application; the process
        handle answers "is it still there" for free, so :meth:`watch_backend` is
        what a caller wants once the lamps have settled.
        """

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
                if existing_url is not None and not self._instance_answers(existing_url):
                    waiting = self._await_lock_release()
                    if waiting is not None:
                        return waiting
                    existing_url = None
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
                if not url:
                    # The child has not yet published the socket it actually
                    # reserved. Keep the startup lamps intact instead of
                    # probing an invented or empty address.
                    return self._snapshot

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
                missing_frontend_reason(),
            )
        elif self._frontend_served is not None:
            # Asked once per server, and never again. Whether the process can
            # return the SPA cannot change while it is running: the route is
            # registered at import time and the file behind it is the one just
            # checked on the line above. Re-fetching six kilobytes of index.html
            # and lowercasing the lot to look for "<html" was half of this
            # launcher's idle HTTP traffic, and every one of those requests
            # could only confirm what the first had already established.
            frontend = self._frontend_served
        else:
            try:
                status, body = self.request_probe(url, self.request_timeout)
                normalized = body.lower()
                if status != 200 or b"<html" not in normalized:
                    raise RuntimeError("SPA route did not return HTML")
                if self._frontend_source_warning is None:
                    frontend = LampStatus(ServiceState.OK, "SPA is being served")
                else:
                    frontend = LampStatus(
                        ServiceState.WARNING,
                        "SPA is being served, but "
                        f"{self._frontend_source_warning}. Run {refresh_hint(self.repo_root)}.",
                    )
            except (OSError, RuntimeError, HTTPError, URLError) as exc:
                frontend = LampStatus(ServiceState.ERROR, "SPA route failed: " + _probe_failure(exc))

        with self._lock:
            if self._process is not process or (
                not observing_existing and process.poll() is not None
            ):
                return self.poll()
            if backend.state is ServiceState.OK:
                # Only a serving backend ends the wait. A child that is merely
                # alive is not evidence: the retry starts one every time, so
                # resetting on that would renew the budget on each attempt and
                # never stop.
                self._lock_conflict_deadline = None
            if frontend.state in {ServiceState.OK, ServiceState.WARNING}:
                self._frontend_served = frontend
            self._snapshot = StatusSnapshot(
                backend=backend,
                frontend=frontend,
                url=url,
                pid=None if observing_existing else process.pid,
                exit_code=2 if observing_existing else None,
            )
            return self._snapshot

    @staticmethod
    def _is_adopted(snapshot: StatusSnapshot) -> bool:
        """Whether this snapshot describes a server another launcher owns."""

        return snapshot.exit_code == 2 and snapshot.backend.state is ServiceState.OK

    def watch_backend(
        self, on_lost: Callable[[StatusSnapshot], None]
    ) -> threading.Thread | None:
        """Report the backend going away, instead of repeatedly asking whether it has.

        The old answer to "is the backend still there" was two HTTP requests
        every 250 ms, for as long as the application was open: 8.2 requests a
        second on a completely idle machine, none of which could say anything
        the child's process handle did not already know. A thread parked in
        ``Popen.wait()`` costs no wakeups whatsoever and returns the instant the
        child dies, so it is both cheaper and faster than any poll interval.

        The exception is the adopted instance -- exit code 2, where the server
        belongs to some other launcher and this process holds no handle for it.
        That one genuinely has to be asked, so it is asked rarely, over one
        pooled connection, and believed only after a second opinion.

        Registration is idempotent, and refuses once a loss has been reported:
        the dead handle would otherwise be rediscovered on every call. Returns
        the watcher thread, so a caller (in practice a test) can join it rather
        than race it, or ``None`` when there is nothing left to watch.
        """

        with self._lock:
            if self._backend_lost or self._process is None:
                return None
            if self._watcher is not None and self._watcher.is_alive():
                return self._watcher
            # A fresh event per watcher, rather than clearing the old one. Stop
            # and start can overlap -- ``_restart_after_failed_handoff`` does
            # exactly that -- and a watcher which slept through its own stop
            # must not be woken into a world where the flag has been cleared
            # again and read a deliberate shutdown as a loss.
            stopping = self._watch_stop = threading.Event()
            self._watcher = threading.Thread(
                target=self._watch_backend,
                args=(on_lost, stopping),
                name="wg2-backend-watch",
                daemon=True,
            )
            self._watcher.start()
            return self._watcher

    def _watch_backend(
        self, on_lost: Callable[[StatusSnapshot], None], stopping: threading.Event
    ) -> None:
        while not stopping.is_set():
            with self._lock:
                process = self._process
                snapshot = self._snapshot
            if process is None:
                return
            if self._is_adopted(snapshot):
                lost = self._await_adopted_loss(snapshot, stopping)
                if lost is None:
                    return
                self._report_lost(lost, on_lost)
                return
            if process.poll() is None:
                # The whole point of this class of fix: no timeout, no interval,
                # no wakeups, and it returns the moment the child exits.
                process.wait()
                if stopping.is_set():
                    return
            # ``poll()`` on an exited child reaches no network at all -- it
            # reads the exit code and the collected output -- so this costs
            # nothing and produces the same reason the status lamps would show.
            snapshot = self.poll()
            if snapshot.running:
                # The lock-conflict retry inside ``poll()`` started a
                # replacement child. Watch that one instead.
                continue
            if self._is_adopted(snapshot):
                continue
            if stopping.is_set():
                # A quit that landed while the reason above was being read.
                # Narrow, but the symptom would be a dialog explaining that the
                # application the user just closed has stopped.
                return
            self._report_lost(snapshot, on_lost)
            return

    def _report_lost(
        self, snapshot: StatusSnapshot, on_lost: Callable[[StatusSnapshot], None]
    ) -> None:
        with self._lock:
            self._backend_lost = True
        on_lost(snapshot)

    def _await_adopted_loss(
        self, snapshot: StatusSnapshot, stopping: threading.Event
    ) -> StatusSnapshot | None:
        """Watch a server this process does not own, as rarely as it can.

        Exit code 2 means another launcher owns the server and this one merely
        uses it, so there is no handle to block on and HTTP is the only question
        available. It is asked every 30 seconds rather than four times a second:
        the window between a foreign server dying and this window saying so is
        not worth 240 requests a minute, and unlike the process handle a probe
        can be wrong, so a lone failure buys a second opinion rather than a
        dialog. ``None`` when the wait ended because the application is closing.
        """

        url = snapshot.url
        while not stopping.wait(self.adopted_probe_interval):
            if self._instance_answers(url, self.liveness_probe):
                continue
            if stopping.wait(self.adopted_retry_delay):
                return None
            if self._instance_answers(url, self.liveness_probe):
                continue
            reason = (
                f"The already-running Waveguide Generator instance at {url} "
                "stopped answering. " + self._log_location()
            )
            with self._lock:
                self._snapshot = StatusSnapshot(
                    backend=LampStatus(ServiceState.ERROR, reason),
                    frontend=LampStatus(ServiceState.ERROR, "Frontend unavailable: " + reason),
                    url=url,
                    pid=None,
                    exit_code=2,
                )
                return self._snapshot
        return None

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
        self._update_request_path = None
        if temporary_directory is not None:
            temporary_directory.cleanup()

    def take_update_request(self) -> UpdateRequest | None:
        """Return one delayed, validated UI request when it is ready to run."""

        with self._lock:
            path = self._update_request_path
        if path is None:
            return None
        try:
            return consume_update_request(path, data_dir=self._data_dir())
        except UpdateHandoffError as exc:
            with self._lock:
                self._output.append(str(exc))
            return None

    def launch_update(self, request: UpdateRequest) -> None:
        """Start the independent updater before this status owner shuts down."""

        if isinstance(request, BundleUpdateRequest):
            launch_bundle_update_handoff(
                self.repo_root,
                request,
                os.getpid(),
                environ=self.environ,
                server_args=self.server_args,
            )
            return
        launch_update_handoff(
            self.repo_root,
            request,
            os.getpid(),
            environ=self.environ,
            server_args=self.server_args,
        )

    def stop(self) -> StatusSnapshot:
        """Request graceful shutdown, then guarantee the entire tree is gone."""

        # Before anything else, and outside the branch below: the watcher is
        # parked in ``wait()`` on the very process this is about to end, and a
        # shutdown somebody asked for is not a backend loss to report.
        self._watch_stop.set()
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
        watcher = self._watcher
        if watcher is not None:
            watcher.join(timeout=1.0)
        close_probe = getattr(self.liveness_probe, "close", None)
        if callable(close_probe):
            close_probe()

        with self._lock:
            self._process = None
            self._output_thread = None
            self._watcher = None
            self._windows_job = None
            self._frontend_served = None
            self._lock_conflict_deadline = None
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
