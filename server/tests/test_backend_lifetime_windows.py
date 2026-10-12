"""Detached Windows backend retirement through the installer's private test event."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from server.platform import backend_owner
from server.platform.instance import InstanceLock, pid_is_running
from server.solver.beat_runtime import retire
from server.tests.backend_breakaway import require_backend_breakaway

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer retirement")
ROOT = Path(__file__).resolve().parents[2]


def _until(check, deadline, message):
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    pytest.fail(message)


@pytest.fixture
def backend(tmp_path, retire_event):
    require_backend_breakaway()
    from ctypes import wintypes

    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    control = retire.RetireSignal()
    assert control.error is None
    kernel32 = control._kernel32
    for name in ("SetEvent", "ResetEvent"):
        function = getattr(kernel32, name)
        function.argtypes = (wintypes.HANDLE,)
        function.restype = wintypes.BOOL
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ)
    for key in ("WG2_BUNDLE", "WG2_DATA_DIR", "WG2_APP_ROOT", "WG2_PORT", "WG2_UPDATE_STAGING_ROOT"):
        env.pop(key, None)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
        "WG2_TEST_RETIRE_EVENT": retire_event, "WG2_SOLVER_WARMUP": "0",
        "WG2_SKIP_BEAT_CPU_PROVISION": "1", "WG2_WGLINK_REFRESH": "0", "WG2_NO_BROWSER": "1",
        "WG2_ENABLE_DRYRUN": "0",
    })
    process = None
    watchdog = None
    expired = threading.Event()
    try:
        process, log = backend_owner._spawn([
            sys.executable, str(ROOT / "server" / "tests" / "backend_lifetime_main.py"),
            "--lifetime=headless", "--no-browser", "--data-dir", str(data), "--port", str(port),
        ], data, env)

        def timeout():
            expired.set()
            # Only this fixture's recorded child and start job are terminated.
            backend_owner._terminate_child(process)

        watchdog = threading.Timer(115.0, timeout)
        watchdog.daemon = True
        watchdog.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path="/health", body=None):
            payload = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with opener.open(req, timeout=1) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)

        def healthy():
            assert process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
            try:
                status, health = request()
            except (OSError, ValueError):
                return False
            return status == 200 and health.get("lifetime") == "headless"

        # Application imports can be slow on hosted Windows runners.
        _until(healthy, time.monotonic() + 90, "backend never became healthy")

        def signal(reset=False):
            assert (kernel32.ResetEvent if reset else kernel32.SetEvent)(control._handle)

        yield process, data, port, request, signal
        assert not expired.is_set(), "backend test exceeded its hard overall timeout"
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=3)
        if process is not None:
            backend_owner._terminate_child(process)
            job = getattr(process, "_wg_start_job", None)
            if job is not None:
                job.close()
        control.close()


def test_idle_backend_retires_within_installer_grace(backend):
    process, data, port, _request, signal = backend
    started = time.monotonic()
    signal()
    process.wait(timeout=12)
    elapsed = time.monotonic() - started
    print(f"retire_seconds={elapsed:.2f}")
    assert elapsed < 12 and process.returncode == 0
    lock = InstanceLock(data / "locks")
    try:
        lock.acquire(port)
    finally:
        lock.release()
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_websocket_keeps_backend_busy_and_reset_reopens_submissions(backend):
    from websockets.sync.client import connect

    process, _data, port, request, signal = backend
    websocket = connect(
        f"ws://127.0.0.1:{port}/ws/jobs", origin=f"http://127.0.0.1:{port}",
        proxy=None, open_timeout=5, close_timeout=2,
    )
    try:
        signal()
        # A valid body reaches the hold; an unknown engine avoids starting work after reset.
        body = {
            "design": {"formula": "OSSE", "L": 120, "a": 45},
            "options": {"engine": "retirement-test-missing"},
        }

        def refused():
            status, response = request("/api/solve", body)
            error = response.get("error", {})
            return status == 409 and error.get("code") == "update_restart_pending" and error

        error = _until(refused, time.monotonic() + 3, "installer did not hold submissions")
        assert error["retryable"] is True and error["stage"] == "submission"
        assert error["message"] == (
            "This backend is shutting down (the installer is replacing it); "
            "submit again and a new one will start."
        )
        assert pid_is_running(process.pid)
        assert request()[1]["retiring"] is False
        signal(reset=True)

        def released():
            # Validation would mask a stale route hold, so keep the input valid.
            status, response = request("/api/solve", body)
            return status == 422 and response.get("error", {}).get("code") == "unknown_engine"

        _until(released, time.monotonic() + 3, "installer reset did not release the hold")
        assert pid_is_running(process.pid)
    finally:
        websocket.close()
    # Retirement itself supplies the clean stop after the held socket closes.
    signal()
    process.wait(timeout=12)
    assert process.returncode == 0
