"""Real detached POSIX backend retirement; run by the external test driver."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request

import pytest

from launchers.backend_retire_request import REQUEST_FILENAME, write_request
from server.platform import backend_owner
from server.platform.instance import InstanceLock

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX backend retirement")
ROOT = Path(__file__).resolve().parents[2]


def _until(check, deadline, message):
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    pytest.fail(message)


@pytest.fixture
def backend(tmp_path, request):
    idle_timeout = getattr(request, "param", 600)
    home, data = tmp_path / "home", tmp_path / "data"
    home.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ)
    for key in ("WG2_BUNDLE", "WG2_DATA_DIR", "WG2_APP_ROOT", "WG2_PORT", "WG2_UPDATE_STAGING_ROOT", "WG2_TEST_RETIRE_EVENT"):
        env.pop(key, None)
    env.update({
        "HOME": str(home), "WG2_SOLVER_WARMUP": "0", "WG2_SKIP_BEAT_CPU_PROVISION": "1",
        "WG2_WGLINK_REFRESH": "0", "WG2_NO_BROWSER": "1", "WG2_ENABLE_DRYRUN": "0",
    })
    process = None
    watchdog = None
    expired = threading.Event()
    try:
        process, log = backend_owner._spawn([
            sys.executable, str(ROOT / "server" / "tests" / "backend_lifetime_main.py"),
            "--lifetime=headless", "--no-browser", "--data-dir", str(data), "--port", str(port),
            "--idle-timeout", str(idle_timeout),
        ], data, env)

        def timeout():
            expired.set()
            backend_owner._terminate_child(process)

        watchdog = threading.Timer(115, timeout)
        watchdog.daemon = True
        watchdog.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request():
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                return response.status, json.load(response)

        def healthy():
            assert process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
            try:
                status, health = request()
            except (OSError, ValueError):
                return False
            return status == 200 and health.get("lifetime") == "headless"

        _until(healthy, time.monotonic() + 90, "backend never became healthy")
        yield process, data, port, request
        assert not expired.is_set(), "backend test exceeded its hard overall timeout"
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=3)
        if process is not None:
            backend_owner._terminate_child(process)


@pytest.mark.parametrize("backend", [3], indirect=True)
def test_idle_backend_exits_and_releases_lock_and_port(backend):
    process, data, port, _request = backend
    process.wait(timeout=15)
    assert process.returncode == 0
    lock = InstanceLock(data / "locks")
    try:
        lock.acquire(port)
    finally:
        lock.release()
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("mode", ["retire", "release"])
def test_request_file_retires_backend(backend, mode):
    process, data, _port, request = backend
    health_pid = request()[1]["pid"]
    started = time.monotonic()
    write_request(data / "locks", pid=health_pid, mode=mode, reason="test")
    process.wait(timeout=15)
    if mode == "release":
        elapsed = time.monotonic() - started
        print(f"release_seconds={elapsed:.2f}")
        assert elapsed < 15
    assert process.returncode == 0


@pytest.mark.parametrize("backend", [3], indirect=True)
def test_websocket_keeps_backend_past_timeout(backend):
    from websockets.sync.client import connect

    process, _data, port, _request = backend
    with connect(f"ws://127.0.0.1:{port}/ws/jobs", origin=f"http://127.0.0.1:{port}", proxy=None):
        time.sleep(5)
        assert process.poll() is None
    process.wait(timeout=15)
    assert process.returncode == 0


def test_pid_mismatch_is_deleted_and_backend_stays_alive(backend):
    process, data, _port, request = backend
    health_pid = request()[1]["pid"]
    write_request(data / "locks", pid=health_pid + 1, mode="release", reason="gui-quit")
    _until(lambda: not (data / "locks" / REQUEST_FILENAME).exists(), time.monotonic() + 10, "request was not removed")
    assert process.poll() is None


def test_second_websocket_after_release_cancels_quit(backend):
    from websockets.sync.client import connect

    process, data, port, request = backend
    health_pid = request()[1]["pid"]
    url = f"ws://127.0.0.1:{port}/ws/jobs"
    kwargs = {"origin": f"http://127.0.0.1:{port}", "proxy": None}
    with connect(url, **kwargs):
        write_request(data / "locks", pid=health_pid, mode="release", reason="gui-quit")
        time.sleep(2.5)  # The second client must start beyond the unload margin.
        with connect(url, **kwargs):
            _until(lambda: not (data / "locks" / REQUEST_FILENAME).exists(), time.monotonic() + 10, "request was not removed")
            time.sleep(0.5)
            assert process.poll() is None
    time.sleep(9)  # Three seconds of quiet plus a full five-second poll.
    assert process.poll() is None
