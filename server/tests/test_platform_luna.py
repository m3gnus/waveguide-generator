"""Regression coverage for accepted Luna launcher/platform findings."""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import pytest

from launch import serve
from server import app as app_module
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo
from server.mesh.gmsh_worker import shutdown_gmsh_worker
from server.platform import console, instance, logging_setup
from server.platform.instance import (
    InstanceAlreadyRunning,
    InstanceInfo,
    InstanceLock,
    InstanceLockError,
)
from server.platform.origin import local_origin
from server.platform.paths import ensure_data_layout
from server.platform.signal_rearm import (
    rearm_registered_signals,
    register_signal_rearm,
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

    with caplog.at_level(logging.WARNING, logger="wg2.console"):
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
    console_shutdown_complete: threading.Event | None = None

    class FakeListener:
        def close(self) -> None:
            nonlocal listener_closed
            listener_closed = True

    listener = FakeListener()

    class FakeLock:
        def acquire(self, _port: int) -> None:
            pass

        def update_port(self, _port: int) -> None:
            pass

        def release(self) -> None:
            nonlocal lock_released
            lock_released = True

    real_config = serve.uvicorn.Config

    def capture_config(*args: Any, **kwargs: Any) -> Any:
        config_kwargs.update(kwargs)
        return real_config(*args, **kwargs)

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
    monkeypatch.setattr(serve, "reserve_port", lambda *_args, **_kwargs: (listener, 3100))
    monkeypatch.setattr(serve, "create_app", create_application)
    monkeypatch.setattr(serve.uvicorn, "Config", capture_config)
    monkeypatch.setattr(serve.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(serve, "harden_console", capture_console_handler)

    assert serve.main(["--no-browser"]) == 0
    assert config_kwargs["ws_max_size"] == DEFAULT_MAX_FRAME_BYTES
    assert config_kwargs["ws_max_queue"] == 1
    assert app_kwargs["solver_warmup"] is False
    assert listener_closed is True
    assert lock_released is True
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
        assert first == second == {
            "engines": [{"name": "mock", "available": True, "reason": "ready", "version": "1", "fast_paths": ()}],
            "engineSelection": {
                "default": "auto",
                "resolvedDefault": None,
                "full3dOrder": ["metal", "bempp", "dryrun"],
                "metalFastPath": "axisymmetric-meridian",
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
