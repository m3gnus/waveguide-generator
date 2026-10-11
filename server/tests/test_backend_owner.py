"""Backend-owner discovery and exactly-once start (server/platform/backend_owner.py)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from server.platform import backend_owner
from server.platform.backend_owner import (
    BackendDataDirMismatch,
    BackendSpawnError,
    BackendStartTimeout,
    discover_backend,
    ensure_backend,
)
from server.platform.instance import LOCK_FILENAME, InstanceLock

ROOT = Path(__file__).resolve().parents[2]

FAKE_BACKEND = textwrap.dedent(
    """
    import json, os, socket, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])
    from server.platform.instance import InstanceAlreadyRunning, InstanceLock

    data_dir, counter, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    reported = sys.argv[5] if len(sys.argv) > 5 else str(data_dir)
    with open(counter, "a") as handle:
        handle.write("spawn\\n")
    if mode == "hang":
        time.sleep(600)
    if mode == "fail":
        sys.exit(1)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"version": "9.9", "commit": "abc", "data_dir": reported,
                               "pid": os.getpid()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    time.sleep(float(os.environ.get("FAKE_STARTUP_DELAY", "0")))
    lock = InstanceLock(data_dir / "locks")
    try:
        lock.acquire(server.server_address[1])
    except InstanceAlreadyRunning:
        sys.exit(2)
    server.serve_forever()
    """
)

CLIENT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from server.platform.backend_owner import ensure_backend
    handle = ensure_backend(sys.argv[2], sys.argv[3:], timeout=30)
    print(handle.pid)
    """
)


@pytest.fixture
def data_dir(tmp_path: Path):
    path = tmp_path / "data"
    (path / "locks").mkdir(parents=True)
    yield path
    # Kill whatever fake backends this test left behind.
    for process in list(_spawned):
        _terminate(process)
    _spawned.clear()
    # Detached children (including "hang" ones that never take the lock) carry
    # this test's unique data folder in their argv.
    subprocess.run(["pkill", "-9", "-f", f"fake_backend.py .* {path}"], check=False)
    lock_file = path / "locks" / LOCK_FILENAME
    try:
        pid = json.loads(lock_file.read_text())["pid"]
        # Only a backend ensure_backend detached; tests also write fake metadata
        # naming pid 1 or this process, which must never be signalled.
        if pid not in (1, os.getpid()) and _is_fake_backend(pid):
            os.kill(pid, signal.SIGKILL)
    except (OSError, ValueError, KeyError):
        pass


_spawned: list[subprocess.Popen] = []


def _is_fake_backend(pid: int) -> bool:
    try:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
        ).stdout
    except OSError:
        return False
    return "fake_backend.py" in command


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


@pytest.fixture
def fake(tmp_path: Path, data_dir: Path):
    script = tmp_path / "fake_backend.py"
    script.write_text(FAKE_BACKEND)
    counter = tmp_path / "spawns.txt"

    def argv(mode: str = "serve", reported: str | None = None) -> list[str]:
        out = [sys.executable, str(script), str(ROOT), str(data_dir), str(counter), mode]
        if reported is not None:
            out.append(reported)
        return out

    argv.counter = counter  # type: ignore[attr-defined]
    argv.spawns = lambda: len(counter.read_text().split()) if counter.exists() else 0  # type: ignore[attr-defined]
    return argv


def _start_owner(argv: list[str]) -> subprocess.Popen:
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
    _spawned.append(process)
    return process


def _wait_for_owner(data_dir: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if discover_backend(data_dir) is not None:
                return
        except BackendDataDirMismatch:
            return
        time.sleep(0.05)
    raise AssertionError("fake backend never became discoverable")


def test_discover_none_without_lock_file(data_dir: Path) -> None:
    assert discover_backend(data_dir) is None


def test_discover_ignores_stale_lock_metadata(data_dir: Path) -> None:
    # Metadata naming a live pid (ours) and a port, but no process holds the lock.
    (data_dir / "locks" / LOCK_FILENAME).write_text(
        json.dumps({"pid": os.getpid(), "port": 1}) + "\n"
    )
    assert discover_backend(data_dir) is None


def test_discover_running_backend(data_dir: Path, fake) -> None:
    process = _start_owner(fake())
    _wait_for_owner(data_dir)
    handle = discover_backend(data_dir)
    assert handle is not None
    assert handle.pid == process.pid
    assert handle.base_url == f"http://127.0.0.1:{handle.port}"
    assert handle.version == "9.9"
    assert handle.commit == "abc"
    assert handle.data_dir == data_dir.resolve()


def test_data_dir_mismatch_is_a_refusal(data_dir: Path, tmp_path: Path, fake) -> None:
    _start_owner(fake(reported=str(tmp_path / "elsewhere")))
    _wait_for_owner(data_dir)
    with pytest.raises(BackendDataDirMismatch, match="data folder"):
        discover_backend(data_dir)
    # ensure_backend must not start a second backend or switch folders.
    before = fake.spawns()
    with pytest.raises(BackendDataDirMismatch):
        ensure_backend(data_dir, fake(), timeout=5)
    assert fake.spawns() == before


def test_pid_mismatch_is_a_refusal(data_dir: Path, fake, monkeypatch) -> None:
    _start_owner(fake())
    _wait_for_owner(data_dir)
    real = backend_owner._fetch_health
    monkeypatch.setattr(
        backend_owner, "_fetch_health", lambda port: {**(real(port) or {}), "pid": 1}
    )
    with pytest.raises(BackendDataDirMismatch, match="pid"):
        discover_backend(data_dir)


def test_ensure_returns_running_backend_without_spawning(data_dir: Path, fake) -> None:
    process = _start_owner(fake())
    _wait_for_owner(data_dir)
    spawned = fake.spawns()
    handle = ensure_backend(data_dir, fake(), timeout=10)
    assert handle.pid == process.pid
    assert fake.spawns() == spawned


def test_ensure_starts_when_lock_is_stale(data_dir: Path, fake) -> None:
    (data_dir / "locks" / LOCK_FILENAME).write_text(json.dumps({"pid": 1, "port": 1}) + "\n")
    handle = ensure_backend(data_dir, fake(), timeout=30)
    assert handle.pid != os.getpid()
    assert fake.spawns() == 1
    assert discover_backend(data_dir) == handle
    assert (data_dir / "logs" / backend_owner.LOG_FILENAME).exists()


def test_concurrent_clients_start_exactly_one_backend(data_dir: Path, fake, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_STARTUP_DELAY", "1.0")
    clients = [
        subprocess.Popen(
            [sys.executable, "-c", CLIENT, str(ROOT), str(data_dir), *fake()],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    pids = []
    for client in clients:
        out, _ = client.communicate(timeout=60)
        assert client.returncode == 0
        pids.append(int(out.strip()))
    assert len(set(pids)) == 1
    assert fake.spawns() == 1


def test_spawn_that_never_becomes_healthy_times_out(data_dir: Path, fake) -> None:
    started = time.monotonic()
    with pytest.raises(BackendStartTimeout, match="did not become ready"):
        ensure_backend(data_dir, fake("hang"), timeout=1.5)
    assert time.monotonic() - started < 15
    # The start lock was released, so a following client is not wedged behind it.
    with pytest.raises(BackendStartTimeout):
        ensure_backend(data_dir, fake("hang"), timeout=1.0)


def test_spawn_failure_reports_exit_code(data_dir: Path, fake) -> None:
    with pytest.raises(BackendSpawnError, match="exited with code 1"):
        ensure_backend(data_dir, fake("fail"), timeout=10)
    with pytest.raises(BackendSpawnError, match="Could not start"):
        ensure_backend(data_dir, [str(data_dir / "no-such-binary")], timeout=10)


def test_spawn_exiting_2_adopts_existing_owner(data_dir: Path, fake, monkeypatch) -> None:
    # Another starter (e.g. the GUI launcher) wins the InstanceLock after our
    # discover() looked: model it by hiding the owner until the spawn has happened.
    owner = _start_owner(fake())
    _wait_for_owner(data_dir)
    real = backend_owner._discover
    spawned = fake.spawns()

    def hidden(root):
        # Invisible until the spawned process has run (and lost the lock race).
        return real(root) if fake.spawns() > spawned else None

    monkeypatch.setattr(backend_owner, "_discover", hidden)
    handle = ensure_backend(data_dir, fake(), timeout=30)
    assert handle.pid == owner.pid
    assert fake.spawns() == spawned + 1  # one attempt, which exited 2


def test_lock_probe_does_not_disturb_a_held_lock(data_dir: Path) -> None:
    lock = InstanceLock(data_dir / "locks")
    lock.acquire(4000)
    try:
        assert backend_owner._lock_is_held(data_dir / "locks" / LOCK_FILENAME) is True
    finally:
        lock.release()
    assert backend_owner._lock_is_held(data_dir / "locks" / LOCK_FILENAME) is False
