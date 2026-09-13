"""The shutdown backstop, and the waits it bounds, exercised in-process.

Nothing here can end the test process. Every ``ShutdownBackstop`` built below
gets an ``exit_process`` that only records, and ``server/tests/conftest.py``
fails any test under which a default one fires. The process-level proof, a
real server that really exits, is ``test_bounded_server_shutdown.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from launch import serve
from server.mesh import gmsh_worker
from server.platform.shutdown_backstop import (
    BACKSTOP_EXIT_CODE,
    CLEANUP_RESERVE_SECONDS,
    DEFAULT_SHUTDOWN_BUDGET_SECONDS,
    FLUSH_TIMEOUT_SECONDS,
    ShutdownBackstop,
    shutdown_wait_limit,
)


class _RecordedExit:
    def __init__(self) -> None:
        self.codes: list[int] = []
        self.at: float | None = None
        self.called = threading.Event()

    def __call__(self, code: int) -> None:
        self.codes.append(code)
        self.at = time.monotonic()
        self.called.set()


def _quiet() -> None:
    return None


def test_the_budget_sits_inside_the_launchers_grace() -> None:
    from inspect import signature

    from launchers.statusapp.controller import StatusController

    grace = signature(StatusController.__init__).parameters["shutdown_timeout"].default
    # The budget, then at most one bounded log flush, before the launcher kills.
    assert DEFAULT_SHUTDOWN_BUDGET_SECONDS + FLUSH_TIMEOUT_SECONDS < grace
    assert CLEANUP_RESERVE_SECONDS < DEFAULT_SHUTDOWN_BUDGET_SECONDS
    assert BACKSTOP_EXIT_CODE == 0


def test_nothing_is_armed_until_a_stop_is_requested() -> None:
    exit_process = _RecordedExit()
    backstop = ShutdownBackstop(0.05, exit_process=exit_process, flush=_quiet)

    time.sleep(0.2)

    assert backstop.begun is False
    assert backstop.remaining() is None
    assert not exit_process.called.is_set()


def test_an_expired_budget_flushes_the_logs_then_exits() -> None:
    order: list[str] = []
    exited = threading.Event()

    def exit_process(code: int) -> None:
        order.append(f"exit {code}")
        exited.set()

    backstop = ShutdownBackstop(
        0.3, exit_process=exit_process, flush=lambda: order.append("flush")
    )
    started = time.monotonic()

    assert backstop.begin("a stop request") is True
    assert backstop.begin("a later stop request") is False
    assert exited.wait(5.0)

    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 2.0, f"fired after {elapsed:.2f} s for a 0.3 s budget"
    assert order == ["flush", f"exit {BACKSTOP_EXIT_CODE}"]
    assert backstop.fired


def test_exit_now_does_not_wait_for_the_rest_of_the_budget() -> None:
    exit_process = _RecordedExit()
    backstop = ShutdownBackstop(60.0, exit_process=exit_process, flush=_quiet)
    backstop.begin("SIGINT received")

    asked_at = time.monotonic()
    backstop.exit_now("a second Ctrl+C")

    assert exit_process.called.wait(5.0)
    assert exit_process.at is not None and exit_process.at - asked_at < 1.0
    assert exit_process.codes == [BACKSTOP_EXIT_CODE]


def test_exit_now_works_without_an_earlier_stop() -> None:
    exit_process = _RecordedExit()
    backstop = ShutdownBackstop(60.0, exit_process=exit_process, flush=_quiet)

    backstop.exit_now("cleanup finished with a gmsh call still running")

    assert backstop.begun
    assert exit_process.called.wait(5.0)


def test_a_wedged_log_flush_does_not_hold_the_exit() -> None:
    exit_process = _RecordedExit()
    wedged = threading.Event()
    backstop = ShutdownBackstop(
        0.0, exit_process=exit_process, flush=wedged.wait, flush_timeout_seconds=0.2
    )
    started = time.monotonic()
    try:
        backstop.begin("a stop request")
        assert exit_process.called.wait(5.0)
        assert exit_process.at is not None and exit_process.at - started < 1.5
    finally:
        wedged.set()


@pytest.mark.parametrize("ending", ["deadline", "exit_now"])
def test_a_blocked_log_handler_does_not_hold_the_exit(ending: str) -> None:
    """The watchdog's own log lines go through the same handlers as the flush.

    A handler wedged on a dead disk or console must not stop the thread that
    enforces the deadline before it ever reaches the exit.
    """

    release = threading.Event()

    class _Blocked(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            release.wait(30)

    handler = _Blocked(logging.INFO)
    logger = logging.getLogger("wg.launch")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    exit_process = _RecordedExit()
    backstop = ShutdownBackstop(
        0.05 if ending == "deadline" else 60.0,
        exit_process=exit_process,
        flush=_quiet,
        flush_timeout_seconds=0.2,
    )
    started = time.monotonic()
    try:
        if ending == "deadline":
            backstop.begin("a stop request")
        else:
            backstop.exit_now("a second Ctrl+C")
        assert exit_process.called.wait(5.0), "the exit waited on a blocked log handler"
        assert exit_process.at is not None and exit_process.at - started < 1.5
        assert exit_process.codes == [BACKSTOP_EXIT_CODE]
    finally:
        release.set()
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_waits_keep_their_own_bounds_until_a_stop_begins() -> None:
    now = [100.0]
    exit_process = _RecordedExit()
    backstop = ShutdownBackstop(
        10.0, exit_process=exit_process, flush=_quiet, clock=lambda: now[0]
    )
    assert shutdown_wait_limit(None) is None
    backstop.activate()
    try:
        # Active but not begun: the application is serving, nothing is bounded.
        assert shutdown_wait_limit(None) is None
        assert shutdown_wait_limit(10.0) == 10.0

        backstop.begin("a stop request")
        assert shutdown_wait_limit(None) == pytest.approx(10.0 - CLEANUP_RESERVE_SECONDS)
        assert shutdown_wait_limit(3.0) == 3.0

        now[0] += 9.0
        assert shutdown_wait_limit(None) == 0.0
        assert shutdown_wait_limit(3.0) == 0.0
    finally:
        backstop.deactivate()
        backstop.exit_now("test finished")
    assert exit_process.called.wait(5.0)
    assert shutdown_wait_limit(10.0) == 10.0


class _BackstopRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def begin(self, reason: str) -> bool:
        self.calls.append(("begin", reason))
        return True

    def exit_now(self, reason: str) -> None:
        self.calls.append(("exit_now", reason))


def test_a_stop_request_file_starts_the_budget(tmp_path: Path) -> None:
    reasons: list[str] = []
    server = SimpleNamespace(should_exit=False)
    control = tmp_path / "stop"
    control.write_text("stop\n", encoding="utf-8")

    serve._watch_statusapp(
        server, control, os.getpid(), threading.Event(), on_stop=reasons.append
    )

    assert server.should_exit is True
    assert reasons == ["status window requested quit"]


def test_a_status_window_that_vanished_starts_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reasons: list[str] = []
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(serve, "pid_is_running", lambda _pid: False)

    serve._watch_statusapp(
        server, tmp_path / "absent", 999999, threading.Event(), on_stop=reasons.append
    )

    assert server.should_exit is True
    assert reasons == ["status window exited"]


def test_a_first_signal_starts_the_budget_and_a_second_interrupt_ends_it() -> None:
    server = SimpleNamespace(should_exit=False, force_exit=False)
    recorder = _BackstopRecorder()

    with serve._capture_shutdown_signals(server, recorder):  # type: ignore[arg-type]
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert server.should_exit is True
        assert recorder.calls == [("begin", "SIGINT received")]

        handler(signal.SIGINT, None)
        assert server.force_exit is True
        assert recorder.calls[-1] == ("exit_now", "a second Ctrl+C")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is not deliverable there")
def test_a_terminate_signal_starts_the_budget_rather_than_ending_it() -> None:
    server = SimpleNamespace(should_exit=True, force_exit=False)
    recorder = _BackstopRecorder()

    with serve._capture_shutdown_signals(server, recorder):  # type: ignore[arg-type]
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # type: ignore[operator]

    assert server.force_exit is False
    assert recorder.calls == [("begin", "SIGTERM received")]


def _fake_gmsh(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"initialized": False}
    monkeypatch.setitem(
        sys.modules,
        "gmsh",
        SimpleNamespace(
            isInitialized=lambda: state["initialized"],
            initialize=lambda **_kwargs: state.__setitem__("initialized", True),
            finalize=lambda: state.__setitem__("initialized", False),
        ),
    )


def test_a_stop_budget_skips_the_join_on_a_gmsh_call_that_does_not_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_gmsh(monkeypatch)
    exit_process = _RecordedExit()

    async def scenario() -> tuple[float, bool, Any]:
        await gmsh_worker.shutdown_gmsh_worker()
        entered = threading.Event()
        release = threading.Event()

        def occ_build() -> str:
            entered.set()
            release.wait(30)
            return "done"

        work = asyncio.create_task(gmsh_worker.run_on_gmsh_worker(occ_build))
        assert await asyncio.to_thread(entered.wait, 5)
        backstop = ShutdownBackstop(
            CLEANUP_RESERVE_SECONDS + 0.3, exit_process=exit_process, flush=_quiet
        )
        backstop.activate()
        backstop.begin("a stop request")
        try:
            started = time.monotonic()
            await gmsh_worker.shutdown_gmsh_worker()
            elapsed = time.monotonic() - started
            abandoned = gmsh_worker.gmsh_call_abandoned()
        finally:
            backstop.deactivate()
            release.set()
            backstop.exit_now("test finished")
        # The call itself was never interrupted; only the wait for it was cut.
        result = await work
        assert gmsh_worker.gmsh_call_abandoned() is False
        return elapsed, abandoned, result

    elapsed, abandoned, result = asyncio.run(scenario())

    assert elapsed < 2.0, f"shutdown waited {elapsed:.2f} s on a call past its budget"
    assert abandoned is True
    assert result == "done"
    assert exit_process.called.wait(5.0)


def test_a_stop_budget_still_joins_a_gmsh_call_that_finishes_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_gmsh(monkeypatch)
    exit_process = _RecordedExit()

    async def scenario() -> bool:
        await gmsh_worker.shutdown_gmsh_worker()
        entered = threading.Event()

        def quick_build() -> str:
            entered.set()
            time.sleep(0.2)
            return "done"

        work = asyncio.create_task(gmsh_worker.run_on_gmsh_worker(quick_build))
        assert await asyncio.to_thread(entered.wait, 5)
        backstop = ShutdownBackstop(
            CLEANUP_RESERVE_SECONDS + 5.0, exit_process=exit_process, flush=_quiet
        )
        backstop.activate()
        backstop.begin("a stop request")
        try:
            await gmsh_worker.shutdown_gmsh_worker()
            abandoned = gmsh_worker.gmsh_call_abandoned()
        finally:
            backstop.deactivate()
            backstop.exit_now("test finished")
        assert await work == "done"
        return abandoned

    assert asyncio.run(scenario()) is False
    assert exit_process.called.wait(5.0)
