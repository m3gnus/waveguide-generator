"""Regression coverage for accepted Luna launcher/platform findings."""

from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from launch import serve
from server import app as app_module
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo
from server.mesh import gmsh_worker
from server.mesh.gmsh_worker import shutdown_gmsh_worker
from server.platform import console, instance, logging_setup
from server.platform.instance import (
    InstanceAlreadyRunning,
    InstanceInfo,
    InstanceLock,
    InstanceLockError,
)
from server.platform.origin import (
    local_origin,
    parse_extra_websocket_origins,
    websocket_request_allowed,
)
from server.platform.paths import default_runs_dir, ensure_data_layout
from server.platform.signal_rearm import (
    rearm_registered_signals,
    register_signal_rearm,
    restore_sigpipe_ignore,
    unregister_signal_rearm,
)
from server.protocol.frame import DEFAULT_MAX_FRAME_BYTES


def test_console_close_handler_waits_for_shutdown_completion() -> None:
    shutdown_requested = threading.Event()
    shutdown_complete = threading.Event()
    result: list[bool] = []
    handler = console._make_close_handler(
        shutdown_requested.set,
        shutdown_complete,
        timeout_seconds=2.0,
    )

    worker = threading.Thread(
        target=lambda: result.append(handler(console.CTRL_CLOSE_EVENT)),
        daemon=True,
    )
    worker.start()

    assert shutdown_requested.wait(1.0)
    assert worker.is_alive()
    shutdown_complete.set()
    worker.join(1.0)

    assert not worker.is_alive()
    assert result == [True]


def test_console_close_handler_returns_and_logs_after_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown_requested = threading.Event()
    timeout_seconds = 0.02
    handler = console._make_close_handler(
        shutdown_requested.set,
        threading.Event(),
        timeout_seconds=timeout_seconds,
    )
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(handler(console.CTRL_SHUTDOWN_EVENT)),
        daemon=True,
    )

    with caplog.at_level(logging.WARNING, logger="wg.console"):
        worker.start()
        worker.join(1.0)

    assert not worker.is_alive(), "the handler exceeded its injected timeout"
    assert result == [True]
    assert shutdown_requested.is_set()
    assert "graceful shutdown window exceeded" in caplog.text


@pytest.mark.parametrize("event", [console.CTRL_C_EVENT, console.CTRL_BREAK_EVENT])
def test_console_close_handler_leaves_interrupt_events_unhandled(event: int) -> None:
    def unexpected_shutdown() -> None:
        raise AssertionError("Ctrl+C and Ctrl+Break belong to the signal handlers")

    handler = console._make_close_handler(unexpected_shutdown, threading.Event())
    assert handler(event) is False


def test_advisory_lock_rejects_partially_written_live_owner(tmp_path: Path) -> None:
    # Lock the file the way another instance would, but write no metadata, so
    # the conflict is decided by the lock alone rather than by what it contains.
    path = tmp_path / "server.pid"
    descriptor = os.open(path, instance.LOCK_OPEN_FLAGS, 0o600)
    instance.lock_exclusive(descriptor)
    try:
        with pytest.raises(InstanceAlreadyRunning):
            InstanceLock(tmp_path).acquire(3100)
    finally:
        instance.unlock(descriptor)
        os.close(descriptor)


def test_instance_metadata_write_retries_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = instance.os.write
    write_sizes: list[int] = []

    def short_write(descriptor: int, payload: bytes) -> int:
        chunk = payload[:3]
        write_sizes.append(len(chunk))
        return real_write(descriptor, chunk)

    monkeypatch.setattr(instance.os, "write", short_write)
    lock = InstanceLock(tmp_path)
    try:
        assert lock.acquire(3100) == InstanceInfo(pid=os.getpid(), port=3100)
    finally:
        lock.release()

    assert len(write_sizes) > 1
    assert json.loads((tmp_path / "server.pid").read_text(encoding="utf-8")) == {
        "pid": os.getpid(),
        "port": 3100,
    }


def test_instance_metadata_write_refuses_zero_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instance.os, "write", lambda _descriptor, _payload: 0)

    with pytest.raises(InstanceLockError, match="made no progress"):
        InstanceLock(tmp_path).acquire(3100)


def test_pid_liveness_probe_answers_without_killing() -> None:
    """The probe must report on a process, never terminate it.

    os.kill(pid, 0) is a liveness check on POSIX but resolves to
    TerminateProcess on Windows, so the survival assertion matters as much as
    the answer does.
    """

    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        assert instance._pid_is_running(child.pid) is True
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()

    assert instance._pid_is_running(child.pid) is False
    assert instance._pid_is_running(os.getpid()) is True
    assert instance._pid_is_running(0) is False


def test_lock_filesystem_oserror_is_wrapped(tmp_path: Path) -> None:
    (tmp_path / "server.pid").mkdir()
    with pytest.raises(InstanceLockError, match="Could not open instance lock"):
        InstanceLock(tmp_path).acquire(3100)


def test_python_m_server_forwards_to_launcher() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "server", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--no-browser" in completed.stdout
    assert "--data-dir" in completed.stdout


def test_runtime_file_handler_rotates_during_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging_setup, "MAX_LOG_BYTES", 256)
    paths = ensure_data_layout(tmp_path)
    logger = logging_setup.setup_logging(paths)
    # The writing handlers moved behind a QueueListener so that formatting and
    # disk I/O leave the calling thread -- which for every request and job
    # event is the event loop. They are still exactly one rotating file
    # handler, they are just no longer attached to the root logger.
    file_handlers = [
        handler
        for handler in logging_setup.log_sinks()
        if isinstance(handler, RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert not any(
        isinstance(handler, RotatingFileHandler)
        for handler in logging.getLogger().handlers
    )
    logger.warning("x" * 220)
    logger.warning("y" * 220)
    # Delivery is asynchronous now, so rotation cannot be asserted until the
    # queue has drained. Stopping the listener is the drain, and it is what the
    # server itself does on the way out.
    logging_setup.flush_logs()
    assert (paths.logs / "server.log.1").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frequency_start_hz": 0.0, "frequency_end_hz": 100.0, "num_frequencies": 2, "frequency_spacing": "log"},
        {"frequency_start_hz": float("nan"), "frequency_end_hz": 100.0, "num_frequencies": 2, "frequency_spacing": "log"},
        {"frequency_start_hz": 100.0, "frequency_end_hz": 50.0, "num_frequencies": 2, "frequency_spacing": "linear"},
        {"frequency_start_hz": 100.0, "frequency_end_hz": 200.0, "num_frequencies": 0, "frequency_spacing": "log"},
        {"frequency_start_hz": 100.0, "frequency_end_hz": 200.0, "num_frequencies": 402, "frequency_spacing": "log"},
        {"frequency_start_hz": 100.0, "frequency_end_hz": 200.0, "num_frequencies": 2, "frequency_spacing": "octave"},
    ],
)
def test_dryrun_validates_all_axis_inputs(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        DryRunEngine().solve({"formula": "OSSE"}, **kwargs)


def test_launcher_checks_lock_conflict_before_port_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ensure_data_layout(tmp_path)
    bound = False

    class ConflictLock:
        def __init__(self, _path: Path) -> None:
            pass

        def acquire(self, _port: int) -> None:
            raise InstanceAlreadyRunning(InstanceInfo(pid=123, port=3100), paths.locks / "server.pid")

    def reserve(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal bound
        bound = True
        raise AssertionError("port binding must not run")

    monkeypatch.setattr(serve, "ensure_data_layout", lambda: paths)
    monkeypatch.setattr(serve, "setup_logging", lambda _paths: None)
    monkeypatch.setattr(serve, "InstanceLock", ConflictLock)
    monkeypatch.setattr(serve, "reserve_port", reserve)
    assert serve.main(["--no-browser"]) == 2
    assert bound is False


def test_launcher_stops_log_listener_on_early_port_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port validation happens after logging starts and must still drain it."""

    monkeypatch.setenv("WG2_PORT", "not-a-port")

    assert serve.main(["--no-browser", "--data-dir", str(tmp_path)]) == 1
    assert logging_setup._listener is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("0", False),
        ("true", False),
        ("yes", False),
        ("1", True),
    ],
)
def test_launcher_requires_exact_solver_warmup_opt_in(
    value: str | None,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if value is None:
        monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    else:
        monkeypatch.setenv("WG2_SOLVER_WARMUP", value)

    assert serve._solver_warmup_enabled() is expected


def test_launcher_aligns_websocket_transport_limits_with_frame_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ensure_data_layout(tmp_path)
    config_kwargs: dict[str, Any] = {}
    app_kwargs: dict[str, Any] = {}
    listener_closed = False
    lock_released = False
    lock_acquired = False
    migration_checked = False
    console_shutdown_complete: threading.Event | None = None

    class FakeListener:
        def close(self) -> None:
            nonlocal listener_closed
            listener_closed = True

    listener = FakeListener()

    class FakeLock:
        def acquire(self, _port: int) -> None:
            nonlocal lock_acquired
            lock_acquired = True

        def update_port(self, _port: int) -> None:
            pass

        def release(self) -> None:
            nonlocal lock_released
            lock_released = True

    real_config = serve.uvicorn.Config

    def capture_config(*args: Any, **kwargs: Any) -> Any:
        config_kwargs.update(kwargs)
        return real_config(*args, **kwargs)

    def migrate_before_start(_root: Path, data_dir: Path, _lock: FakeLock) -> list[Any]:
        nonlocal migration_checked
        assert lock_acquired is True
        assert data_dir == paths.root
        migration_checked = True
        return []

    def create_application(**kwargs: Any) -> object:
        app_kwargs.update(kwargs)
        return object()

    def capture_console_handler(
        _request_shutdown: Any, shutdown_complete: threading.Event
    ) -> None:
        nonlocal console_shutdown_complete
        assert not shutdown_complete.is_set()
        console_shutdown_complete = shutdown_complete

    class FakeServer:
        def __init__(self, _config: Any) -> None:
            self.should_exit = False

        def run(self, *, sockets: list[Any]) -> None:
            assert sockets == [listener]

    monkeypatch.delenv("WG2_PORT", raising=False)
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    monkeypatch.setattr(serve, "ensure_data_layout", lambda: paths)
    monkeypatch.setattr(serve, "setup_logging", lambda _paths: None)
    monkeypatch.setattr(serve, "flush_logs", lambda: None)
    monkeypatch.setattr(serve, "InstanceLock", lambda _path: FakeLock())
    monkeypatch.setattr(serve, "auto_migrate_v1", migrate_before_start)

    def reserve_after_migration(*_args: Any, **_kwargs: Any) -> tuple[FakeListener, int]:
        assert migration_checked is True
        return listener, 3100

    monkeypatch.setattr(serve, "reserve_port", reserve_after_migration)
    monkeypatch.setattr(serve, "create_app", create_application)
    monkeypatch.setattr(serve.uvicorn, "Config", capture_config)
    monkeypatch.setattr(serve.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(serve, "harden_console", capture_console_handler)

    assert serve.main(["--no-browser"]) == 0
    assert config_kwargs["ws_max_size"] == DEFAULT_MAX_FRAME_BYTES
    assert config_kwargs["ws_max_queue"] == 1
    assert app_kwargs["solver_warmup"] is False
    # Runs default beside the user's documents, not inside the checkout: an
    # install directory does not survive a reinstall and is not anywhere a
    # user looks for their own output.
    assert app_kwargs["workspace_dir"] == default_runs_dir()
    assert listener_closed is True
    assert lock_released is True
    assert migration_checked is True
    assert console_shutdown_complete is not None
    assert console_shutdown_complete.is_set()


def test_registered_native_signal_rearm_callbacks_are_removable() -> None:
    calls: list[str] = []
    token = register_signal_rearm(lambda: calls.append("rearmed"))
    try:
        rearm_registered_signals()
    finally:
        unregister_signal_rearm(token)
    rearm_registered_signals()

    assert calls == ["rearmed"]


@pytest.mark.skipif(os.name != "posix", reason="SIGPIPE is a POSIX signal")
def test_sigpipe_restore_overrides_a_native_change_hidden_by_pythons_cache() -> None:
    """A C-level handler change must be repaired even when getsignal lies."""

    import ctypes
    import signal

    c_signal = ctypes.CDLL(None).signal
    c_signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
    c_signal.restype = ctypes.c_void_p
    try:
        c_signal(signal.SIGPIPE, ctypes.c_void_p(int(signal.SIG_DFL)))
        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN

        restore_sigpipe_ignore()

        previous = c_signal(signal.SIGPIPE, ctypes.c_void_p(int(signal.SIG_DFL)))
        assert previous == int(signal.SIG_IGN)
    finally:
        restore_sigpipe_ignore()


def test_public_gmsh_worker_rearms_registered_signals_on_the_main_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_thread = threading.get_ident()
    state = {"initialized": False, "initialize_kwargs": [], "finalized": 0}
    callback_threads: list[int] = []
    work_threads: list[int] = []

    def initialize(**kwargs: Any) -> None:
        state["initialize_kwargs"].append(kwargs)
        state["initialized"] = True

    def finalize() -> None:
        state["initialized"] = False
        state["finalized"] += 1

    monkeypatch.setitem(
        sys.modules,
        "gmsh",
        SimpleNamespace(
            isInitialized=lambda: state["initialized"],
            initialize=initialize,
            finalize=finalize,
        ),
    )

    async def scenario() -> None:
        token = register_signal_rearm(
            lambda: callback_threads.append(threading.get_ident())
        )
        try:
            def observe() -> str:
                work_threads.append(threading.get_ident())
                assert state["initialized"] is True
                assert callback_threads == [main_thread]
                return "ok"

            assert await gmsh_worker.run_on_gmsh_worker(observe) == "ok"
        finally:
            unregister_signal_rearm(token)

    asyncio.run(scenario())

    assert state["initialize_kwargs"] == [{"interruptible": False}]
    assert state["finalized"] == 1
    assert work_threads and work_threads[0] != main_thread
    assert callback_threads == [main_thread, main_thread]


def test_port_reservation_retries_bind_failures_without_real_sockets(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    class FakeSocket:
        def setsockopt(self, *_args: Any) -> None:
            pass

        def bind(self, address: tuple[str, int]) -> None:
            attempts.append(address[1])
            if address[1] < 3102:
                raise OSError("taken")

        def close(self) -> None:
            pass

    monkeypatch.setattr(instance.socket, "socket", lambda *_args: FakeSocket())
    listener, port = instance.reserve_port(3100)
    assert isinstance(listener, FakeSocket)
    assert port == 3102
    assert attempts == [3100, 3101, 3102]


def test_windows_port_checks_require_exclusive_address_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    exclusive = -5

    class FakeSocket:
        def __init__(self) -> None:
            self.options: list[tuple[int, int, int]] = []
            self.closed = False
            created.append(self)

        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

        def setsockopt(self, level: int, option: int, value: int) -> None:
            self.options.append((level, option, value))

        def bind(self, _address: tuple[str, int]) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(instance.sys, "platform", "win32")
    monkeypatch.setattr(
        instance.socket, "SO_EXCLUSIVEADDRUSE", exclusive, raising=False
    )
    monkeypatch.setattr(instance.socket, "socket", lambda *_args: FakeSocket())

    assert instance.port_is_available(3100) is True
    listener, port = instance.reserve_port(3100)

    assert port == 3100
    assert listener is created[1]
    assert [item.options for item in created] == [
        [(instance.socket.SOL_SOCKET, exclusive, 1)],
        [(instance.socket.SOL_SOCKET, exclusive, 1)],
    ]


def test_capability_probe_runs_off_thread_and_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    main_thread = threading.get_ident()

    def probe() -> list[EngineInfo]:
        calls.append(threading.get_ident())
        return [EngineInfo("mock", True, "ready", "1")]

    monkeypatch.setattr(app_module, "detect_engines", probe)
    application = app_module.create_app(data_dir=tmp_path)
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/api/capabilities"
    )

    async def scenario() -> None:
        first, second = await asyncio.gather(endpoint(), endpoint())
        assert first == second
        # Two blocks here describe the host rather than the mocked engine, so
        # neither can be a fixture: ``dependencies`` measures this venv, and
        # ``storage`` reports whichever SQLite stores this process has opened.
        # The equality above still holds both to being identical across the two
        # calls, which is the caching claim this test is about.
        assert set(first["dependencies"]) == {"pinned", "installed", "drift"}
        assert isinstance(first["storage"], list)
        _host_scoped = {"dependencies", "storage"}
        assert {
            key: value for key, value in first.items() if key not in _host_scoped
        } == {
            "engines": [
                {
                    "name": "mock",
                    "available": True,
                    "reason": "ready",
                    "version": "1",
                    "fast_paths": (),
                    "formulations": (),
                    "mountings": (),
                    "geometry_sources": ("parametric",),
                    "symmetry_domains": (),
                    "field_traces": False,
                    "di_sphere": True,
                    "cancellation_granularity": "between-frequencies",
                }
            ],
            "engineSelection": {
                "default": "auto",
                "resolvedDefault": None,
                "full3dOrder": ["metal", "beat", "bempp", "dryrun"],
                "axisymmetricRunner": "axisym",
            },
        }

    asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0] != main_thread


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:abc", "http://localhost:0", "http://localhost:65536"],
)
def test_local_origin_rejects_invalid_ports(origin: str) -> None:
    assert local_origin(origin) is False
    assert local_origin("http://localhost:3100") is True


def test_websocket_origin_must_match_the_bound_host_and_port() -> None:
    assert websocket_request_allowed(
        origin="http://127.0.0.1:3100", host="127.0.0.1:3100",
        scheme="ws", bound_port=3100,
    )
    assert websocket_request_allowed(
        origin=None, host="127.0.0.1:3100", scheme="ws", bound_port=3100,
    )
    assert not websocket_request_allowed(
        origin="http://127.0.0.1:5173", host="127.0.0.1:3100",
        scheme="ws", bound_port=3100,
    )
    assert not websocket_request_allowed(
        origin="http://localhost:3100", host="127.0.0.1:3100",
        scheme="ws", bound_port=3100,
    )
    assert not websocket_request_allowed(
        origin=None, host="127.0.0.1:5173", scheme="ws", bound_port=3100,
    )


def test_extra_websocket_origins_allow_only_exact_listed_loopback_origins() -> None:
    extra_origins = parse_extra_websocket_origins(
        " http://localhost:3101,https://example.com "
    )

    assert extra_origins == frozenset({"http://localhost:3101"})
    assert websocket_request_allowed(
        origin="http://localhost:3101",
        host="127.0.0.1:3100",
        scheme="ws",
        bound_port=3100,
        extra_origins=extra_origins,
    )
    assert not websocket_request_allowed(
        origin="http://localhost:3102",
        host="127.0.0.1:3100",
        scheme="ws",
        bound_port=3100,
        extra_origins=extra_origins,
    )
    assert not websocket_request_allowed(
        origin="https://example.com",
        host="127.0.0.1:3100",
        scheme="ws",
        bound_port=3100,
        extra_origins={"https://example.com"},
    )


def test_unset_extra_websocket_origins_preserve_strict_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WG2_EXTRA_WS_ORIGINS", raising=False)
    extra_origins = parse_extra_websocket_origins(
        os.environ.get("WG2_EXTRA_WS_ORIGINS")
    )

    assert extra_origins == frozenset()
    assert not websocket_request_allowed(
        origin="http://localhost:3101",
        host="127.0.0.1:3100",
        scheme="ws",
        bound_port=3100,
        extra_origins=extra_origins,
    )


@pytest.mark.parametrize("path", ["/ws/jobs", "/ws/preview"])
def test_app_threads_extra_websocket_origins_to_both_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str
) -> None:
    monkeypatch.setenv("WG2_EXTRA_WS_ORIGINS", "http://localhost:3101")
    application = app_module.create_app(data_dir=tmp_path)
    monkeypatch.setenv("WG2_EXTRA_WS_ORIGINS", "http://localhost:9999")
    sent: list[dict[str, Any]] = []

    async def connect() -> None:
        incoming = [{"type": "websocket.connect"}]

        async def receive() -> dict[str, Any]:
            if incoming:
                return incoming.pop()
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await application(
            {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "scheme": "ws",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"127.0.0.1:3100"),
                    (b"origin", b"http://localhost:3101"),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 3100),
                "subprotocols": [],
            },
            receive,
            send,
        )

    asyncio.run(connect())
    assert sent[0]["type"] == "websocket.accept"


def _idle_child() -> subprocess.Popen[bytes]:
    """A child that lives exactly until its stdin is closed."""

    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )


def _start_status_watchdog(
    control: Path, parent_pid: int | None, stop: threading.Event, **kwargs: Any
) -> tuple[SimpleNamespace, threading.Thread]:
    server = SimpleNamespace(should_exit=False)
    watcher = threading.Thread(
        target=serve._watch_statusapp,
        args=(server, control, parent_pid, stop),
        kwargs=kwargs,
        daemon=True,
    )
    watcher.start()
    return server, watcher


def _release_stop(stop: threading.Event, watcher: threading.Thread) -> None:
    """Wake the watchdog and only then let its stop handle go.

    Closing a handle another thread is still waiting on is undefined on Win32,
    so a test that failed early must leak the handle rather than close it.
    """

    stop.set()
    watcher.join(timeout=5.0)
    if not watcher.is_alive() and isinstance(stop, instance.StopSignal):
        stop.close()


def test_stop_signal_is_still_an_ordinary_threading_event() -> None:
    stop = instance.StopSignal()
    assert not stop.is_set()
    stop.set()
    assert stop.is_set()
    stop.clear()
    assert not stop.is_set()
    # Losing the kernel event must not cost the Python flag its meaning.
    stop.close()
    stop.set()
    assert stop.is_set()


def test_wait_for_pid_exit_reports_a_process_that_has_already_exited() -> None:
    child = _idle_child()
    assert child.stdin is not None
    child.stdin.close()
    child.wait(timeout=30)

    assert instance.wait_for_pid_exit(child.pid, timeout=1.0) == instance.PID_EXITED


def test_wait_for_pid_exit_returns_the_moment_a_child_dies() -> None:
    child = _idle_child()
    stop = instance.StopSignal()
    assert child.stdin is not None

    def end_the_child() -> None:
        assert child.stdin is not None
        child.stdin.close()
        # Reaping is the point, not tidiness. An exited child nobody has waited
        # on is a zombie, and a zombie still answers `os.kill(pid, 0)` -- which
        # is how `_pid_is_running` asks on POSIX. Without this the pid never
        # appears to go away and the wait below runs until pytest's timeout
        # kills the whole session. Every real caller watches a pid that is not
        # its child, where the question does not arise.
        child.wait(timeout=30)

    try:
        threading.Timer(0.1, end_the_child).start()
        started = time.monotonic()
        # Off Windows `timeout=None` is one bounded poll_interval tick, not an
        # unbounded wait -- `wait_for_pid_exit` documents exactly that, and says
        # the caller's loop supplies the rest, as `launch/serve.py` does. A
        # single call therefore asserts that the child dies, is reaped and is
        # observed inside one 0.15 s tick, against a timer that fires at 0.1 s.
        # A loaded runner does not promise the remaining 50 ms: macos-latest
        # failed here on `next` at bd342b4b, before this branch existed.
        while True:
            outcome = instance.wait_for_pid_exit(child.pid, stop, timeout=None)
            if outcome != instance.WAIT_ELAPSED or time.monotonic() - started > 5.0:
                break
        elapsed = time.monotonic() - started
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=30)
        stop.close()

    assert outcome == instance.PID_EXITED
    assert elapsed < 5.0


def test_wait_for_pid_exit_is_released_by_a_waitable_stop() -> None:
    stop = instance.StopSignal()
    threading.Timer(0.05, stop.set).start()

    # timeout=None is a genuinely unbounded wait on Windows, so only the stop
    # handle can end this call.
    assert instance.wait_for_pid_exit(os.getpid(), stop, timeout=None) == instance.STOP_REQUESTED
    stop.close()


def test_wait_for_pid_exit_bounds_an_unwaitable_plain_event() -> None:
    started = time.monotonic()
    outcome = instance.wait_for_pid_exit(
        os.getpid(), threading.Event(), timeout=None, poll_interval=0.05
    )

    # A plain Event is invisible to a kernel wait, so "wait forever" has to be
    # substituted with a tick the caller's loop can re-check the flag on.
    assert outcome == instance.WAIT_ELAPSED
    assert time.monotonic() - started < 5.0


def test_watch_directory_entries_declines_a_directory_it_cannot_watch(tmp_path: Path) -> None:
    assert instance.watch_directory_entries(tmp_path / "absent") is None


def test_directory_change_wakeup_signals_when_a_file_appears(tmp_path: Path) -> None:
    wakeup = instance.watch_directory_entries(tmp_path)
    if wakeup is None:
        pytest.skip("this platform has no waitable directory change notification")
    with wakeup:
        (tmp_path / "stop").write_text("stop\n", encoding="utf-8")
        outcome = instance.wait_for_pid_exit(None, None, timeout=5.0, wakeups=(wakeup,))

    assert outcome == instance.WAKEUP_SIGNALLED


def test_status_watchdog_stops_the_server_when_the_parent_exits(tmp_path: Path) -> None:
    child = _idle_child()
    stop = instance.StopSignal()
    server, watcher = _start_status_watchdog(
        tmp_path / "stop", child.pid, stop, poll_interval=0.05
    )
    try:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(timeout=30)
        watcher.join(timeout=10.0)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=30)
        _release_stop(stop, watcher)

    assert server.should_exit is True


def test_status_watchdog_blocks_instead_of_polling_for_the_control_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = instance.watch_directory_entries(tmp_path)
    if probe is None:
        pytest.skip("this platform has no waitable directory change notification")
    probe.close()

    probed: list[int] = []
    real_pid_is_running = serve.pid_is_running

    def counting_pid_is_running(pid: int) -> bool:
        probed.append(pid)
        return real_pid_is_running(pid)

    monkeypatch.setattr(serve, "pid_is_running", counting_pid_is_running)
    control = tmp_path / "stop"
    stop = instance.StopSignal()
    # A poll interval no test could ever wait out: anything this watchdog
    # notices from here on has to have arrived as an event.
    server, watcher = _start_status_watchdog(control, os.getpid(), stop, poll_interval=600.0)
    try:
        time.sleep(0.3)
        assert server.should_exit is False
        assert probed == [os.getpid()], "the watchdog woke on a timer instead of blocking"

        control.write_text("stop\n", encoding="utf-8")
        watcher.join(timeout=10.0)
    finally:
        _release_stop(stop, watcher)

    assert server.should_exit is True


def test_status_watchdog_polls_the_control_file_without_a_waitable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback every non-Windows launcher takes, exercised everywhere.
    monkeypatch.setattr(serve, "watch_directory_entries", lambda _directory: None)
    control = tmp_path / "stop"
    stop = threading.Event()
    server, watcher = _start_status_watchdog(control, os.getpid(), stop, poll_interval=0.02)
    try:
        control.write_text("stop\n", encoding="utf-8")
        watcher.join(timeout=10.0)
    finally:
        _release_stop(stop, watcher)

    assert server.should_exit is True


def test_status_watchdog_shutdown_leaves_the_server_alone(tmp_path: Path) -> None:
    stop = instance.StopSignal()
    server, watcher = _start_status_watchdog(
        tmp_path / "stop", os.getpid(), stop, poll_interval=600.0
    )
    _release_stop(stop, watcher)

    assert not watcher.is_alive()
    assert server.should_exit is False


def test_app_shutdown_stops_jobs_before_gmsh_worker(tmp_path: Path) -> None:
    application = app_module.create_app(data_dir=tmp_path)
    shutdown_handlers = list(application.router.on_shutdown)
    gmsh_index = shutdown_handlers.index(shutdown_gmsh_worker)
    jobs_index = next(
        index
        for index, handler in enumerate(shutdown_handlers)
        if getattr(handler, "__self__", None) is application.state.jobs_runtime
    )
    assert jobs_index < gmsh_index
