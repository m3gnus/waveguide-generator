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
from server.platform.instance import InstanceLock, InstanceLockError, LOCK_FILENAME, read_lock_info

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX backend retirement")
ROOT = Path(__file__).resolve().parents[2]


def _until(check, deadline, message):
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    pytest.fail(message() if callable(message) else message)


def _backend_command(entry, arguments):
    from server.tests.backend_startup_trace import startup_trace_command

    return startup_trace_command([sys.executable, str(entry), *arguments])


def _wait_for_backend(process, data, log, *, timeout=90, request_timeout=1):
    lock_path = data / "locks" / LOCK_FILENAME
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    port = None
    last_health = "not requested"
    last_owner = "not read"

    def text(path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-32768:]
        except OSError as exc:
            return f"unavailable: {exc!r}"

    def diagnostic():
        return (f"backend never became healthy: owned pid={process.pid}, exit={process.poll()}, "
                f"data={data}, port={port}\nLast owner: {last_owner}\nLast health: {last_health}"
                f"\nInstance metadata ({lock_path}):\n{text(lock_path)}"
                f"\nBackend start log ({log}):\n{text(log)}")

    def request():
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=request_timeout) as response:
            return response.status, json.load(response)

    def healthy():
        nonlocal port, last_health, last_owner
        if process.poll() is not None:
            pytest.fail(diagnostic())
        try:
            owner = read_lock_info(lock_path)
            last_owner = repr(owner)
        except InstanceLockError as exc:
            last_owner = repr(exc)
            return False
        if owner is None or owner.pid != process.pid or not 1 <= owner.port <= 65535:
            return False
        # serve.py can reserve a fallback after our preferred socket is released.
        port = owner.port
        try:
            status, health = request()
            last_health = repr((status, health))
        except (OSError, ValueError) as exc:
            last_health = repr(exc)
            return False
        return (status == 200 and isinstance(health, dict)
                and health.get("lifetime") == "headless" and health.get("pid") == process.pid
                and isinstance(health.get("data_dir"), str)
                and Path(health["data_dir"]).resolve() == data.resolve())

    _until(healthy, time.monotonic() + timeout, diagnostic)
    return port, request


@pytest.fixture
def backend(tmp_path, request):
    options = getattr(request, "param", 600)
    idle_timeout = options.get("idle_timeout", 600) if isinstance(options, dict) else options
    occupy_preferred = isinstance(options, dict) and options.get("occupy_preferred", False)
    home, data = tmp_path / "home", tmp_path / "data"
    home.mkdir()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    if not occupy_preferred:
        listener.close()
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
        process, log = backend_owner._spawn(_backend_command(
            ROOT / "server" / "tests" / "backend_lifetime_main.py", [
            "--lifetime=headless", "--no-browser", "--data-dir", str(data), "--port", str(port),
            "--idle-timeout", str(idle_timeout),
        ]), data, env)

        def timeout():
            expired.set()
            backend_owner._terminate_child(process)

        watchdog = threading.Timer(115, timeout)
        watchdog.daemon = True
        watchdog.start()
        port, request = _wait_for_backend(process, data, log)
        yield process, data, port, request
        assert not expired.is_set(), "backend test exceeded its hard overall timeout"
    finally:
        listener.close()
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


@pytest.mark.parametrize("backend", [{"occupy_preferred": True}], indirect=True)
def test_backend_readiness_follows_owned_fallback_port(backend):
    process, data, port, request = backend
    assert f"busy; using port {port} instead" in (data / "logs" / "backend-start.log").read_text()
    status, health = request()
    assert status == 200 and health["pid"] == process.pid
    assert Path(health["data_dir"]).resolve() == data.resolve()


def test_backend_startup_timeout_keeps_owned_child_diagnostics(tmp_path):
    data = tmp_path / "data"
    (data / "locks").mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        # Keep this port bound but not listening: health fails without contacting
        # another process while the intentionally stalled child remains alive.
        entry = tmp_path / "stalled_main.py"
        entry.write_text("import faulthandler,json,os,pathlib,time\n"
                         f"pathlib.Path({str(data / 'locks' / LOCK_FILENAME)!r}).write_text("
                         f"json.dumps(dict(pid=os.getpid(),port={port})))\n"
                         "faulthandler.dump_traceback_later(.1)\n"
                         "print('intentional startup stall',flush=True)\n"
                         "time.sleep(60)\n")
        process, log = backend_owner._spawn(_backend_command(entry, []), data)
        try:
            _until(lambda: "intentional startup stall" in log.read_text(),
                   time.monotonic() + 5, "diagnostic child did not start")
            with pytest.raises(pytest.fail.Exception) as failure:
                _wait_for_backend(process, data, log, timeout=.3, request_timeout=.05)
            message = str(failure.value)
            assert process.poll() is None
            assert f"owned pid={process.pid}" in message
            assert f'"pid": {process.pid}' in message
            assert f"port={port}" in message
            assert "URLError" in message and "Last health:" in message
            assert "intentional startup stall" in message
            assert "Timeout (" in message and "stalled_main.py" in message
        finally:
            backend_owner._terminate_child(process)


@pytest.mark.parametrize("mismatch", ["owner", "health-pid", "health-data"])
def test_backend_readiness_refuses_foreign_identity(tmp_path, monkeypatch, mismatch):
    import io
    from types import SimpleNamespace

    data = tmp_path / "data"
    (data / "locks").mkdir(parents=True)
    process = SimpleNamespace(pid=123, poll=lambda: None)
    (data / "locks" / LOCK_FILENAME).write_text(json.dumps({
        "pid": 456 if mismatch == "owner" else 123, "port": 49123,
    }))
    health = {"pid": 456 if mismatch == "health-pid" else 123,
              "data_dir": str(tmp_path if mismatch == "health-data" else data), "lifetime": "headless"}
    calls = []
    def open_health(*args, **kwargs):
        calls.append(args)
        response = io.BytesIO(json.dumps(health).encode())
        response.status = 200
        return response
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: SimpleNamespace(open=open_health))
    with pytest.raises(pytest.fail.Exception, match="backend never became healthy"):
        _wait_for_backend(process, data, tmp_path / "missing.log", timeout=.01)
    assert bool(calls) == (mismatch != "owner")
