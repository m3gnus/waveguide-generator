"""Headless contracts for the tkinter status application's lifecycle owner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from launchers.statusapp.controller import ServiceState, StatusController
from scripts.frontend_freshness import installer_hint, vite_executable
from server.platform import instance


FAKE_SERVER = r'''from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--status-control", type=Path, required=True)
parser.add_argument("--parent-pid")
parser.add_argument("--pid-file", type=Path)
parser.add_argument("--child-pid-file", type=Path)
parser.add_argument("--ready-port", type=int)
parser.add_argument("--ready-delay", type=float, default=0)
parser.add_argument("--no-browser", action="store_true")
args, _unknown = parser.parse_known_args()

if args.pid_file:
    args.pid_file.write_text(str(os.getpid()), encoding="utf-8")

if args.child_pid_file:
    child_code = """import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
heartbeat = Path(sys.argv[1] + '.heartbeat')
while True:
    heartbeat.write_text(str(time.monotonic_ns()), encoding='utf-8')
    time.sleep(0.03)
"""
    subprocess.Popen((sys.executable, "-c", child_code, str(args.child_pid_file)))

ready_port = args.ready_port or args.port
time.sleep(args.ready_delay)
args.status_control.with_name("ready.json").write_text(
    '{"host":"127.0.0.1","port":%d}\n' % ready_port,
    encoding="utf-8",
)

print("fake server ready", flush=True)
while not args.status_control.is_file():
    time.sleep(0.02)
'''


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "Hornlab - Workspace" / "waveguide-generator-v2"
    dist = checkout / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return checkout


def _fake_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _controller(tmp_path: Path, **kwargs: object) -> StatusController:
    checkout = _checkout(tmp_path)
    fake_server = _fake_server(tmp_path)
    options: dict[str, object] = {
        "repo_root": checkout,
        "server_command": (sys.executable, str(fake_server)),
        "request_timeout": 0.2,
        "shutdown_timeout": 1.0,
        "request_probe": lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html><body>fake SPA</body></html>")
        ),
    }
    options.update(kwargs)
    return StatusController(
        **options,
    )


def _wait_for(predicate, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("condition did not become true")


def test_start_poll_quit_lifecycle_checks_health_and_spa_probes(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    started = controller.start()
    assert started.running

    def healthy_snapshot():
        snapshot = controller.poll()
        if (
            snapshot.backend.state is ServiceState.OK
            and snapshot.frontend.state is ServiceState.OK
        ):
            return snapshot
        return None

    healthy = _wait_for(healthy_snapshot)
    process = controller.process
    assert process is not None
    assert healthy.url.startswith("http://127.0.0.1:")
    assert healthy.backend.reason == "Healthy — vtest"
    assert healthy.frontend.reason == "SPA is being served"

    stopped = controller.close()
    assert stopped.backend.state is ServiceState.STOPPED
    assert process.poll() is not None


def _make_frontend_stale(controller: StatusController) -> None:
    dist_index = controller.repo_root / "frontend" / "dist" / "index.html"
    source = controller.repo_root / "frontend" / "src" / "main.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export {};\n", encoding="utf-8")
    # The unstamped-release fallback compares mtimes. Files created back-to-back
    # can receive the same timestamp on Windows, so make the stale ordering an
    # explicit part of the fixture instead of relying on filesystem resolution.
    os.utime(dist_index, ns=(1_000_000_000, 1_000_000_000))
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))


def _amber_frontend_reason(controller: StatusController) -> str:
    controller.start()
    try:
        def warning_snapshot():
            snapshot = controller.poll()
            return snapshot if snapshot.frontend.state is ServiceState.WARNING else None

        return _wait_for(warning_snapshot).frontend.reason
    finally:
        controller.close()


def test_stale_release_install_is_served_with_an_amber_installer_warning(
    tmp_path: Path,
) -> None:
    # A release install carries no node_modules and needs no Node runtime, so a
    # Vite build names a command it cannot run. Its dist is the installer's.
    controller = _controller(tmp_path)
    _make_frontend_stale(controller)

    reason = _amber_frontend_reason(controller)
    assert installer_hint() in reason
    assert "npm" not in reason, "a release install has no npm to offer"
    assert "launch-wg-dev" not in reason, (
        "the retired dev launcher must not be advised any more"
    )


def test_stale_source_checkout_is_still_advised_to_rebuild(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    _make_frontend_stale(controller)
    vite = vite_executable(controller.repo_root)
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")

    assert "npm run build" in _amber_frontend_reason(controller)


def test_status_owner_consumes_only_a_ready_valid_update_request(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.start()
    try:
        request_path = controller.update_request_path
        assert request_path is not None
        request_path.write_text(json.dumps({
            "schemaVersion": 1,
            "kind": "install_release",
            "tag": "v2.0.1",
            "readyAtEpoch": time.time() + 60,
        }), encoding="utf-8")

        assert controller.take_update_request() is None
        assert request_path.is_file()

        request_path.write_text(json.dumps({
            "schemaVersion": 1,
            "kind": "install_release",
            "tag": "v2.0.1",
            "readyAtEpoch": 0,
        }), encoding="utf-8")
        assert controller.take_update_request() == "v2.0.1"
        assert not request_path.exists()
    finally:
        controller.close()


def test_child_ready_file_publishes_the_authoritatively_reserved_port(tmp_path: Path) -> None:
    probed: list[str] = []
    actual_port = 43124

    def probe(url: str, _timeout: float) -> tuple[int, bytes]:
        probed.append(url)
        return (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<html></html>")
        )

    controller = _controller(
        tmp_path,
        server_args=("--ready-port", str(actual_port)),
        request_probe=probe,
    )
    started = controller.start()
    assert started.url == ""
    try:
        def ready_snapshot():
            snapshot = controller.poll()
            return snapshot if snapshot.url.endswith(f":{actual_port}/") else None

        snapshot = _wait_for(ready_snapshot)
        assert snapshot.url == f"http://127.0.0.1:{actual_port}/"
        assert probed and all(f":{actual_port}/" in url for url in probed[-2:])
    finally:
        controller.stop()


def test_poll_waits_for_the_child_to_publish_a_url_before_probing(tmp_path: Path) -> None:
    probed: list[str] = []
    controller = _controller(
        tmp_path,
        server_args=("--ready-delay", "0.3"),
        request_probe=lambda url, _timeout: (probed.append(url), (200, b""))[1],
    )
    try:
        controller.start()
        pending = controller.poll()

        assert pending.backend.state is ServiceState.STARTING
        assert pending.frontend.state is ServiceState.STARTING
        assert pending.url == ""
        assert probed == []
    finally:
        controller.stop()


def test_busy_preferred_port_is_left_for_the_child_instance_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = 43123
    monkeypatch.setattr(
        instance,
        "port_is_available",
        lambda candidate, _host: candidate == port + 1,
    )
    checkout = _checkout(tmp_path)
    existing = tmp_path / "existing.py"
    existing.write_text(
        "import argparse, sys\n"
        "parser = argparse.ArgumentParser(add_help=False)\n"
        "parser.add_argument('--port', type=int, action='append')\n"
        "args, _ = parser.parse_known_args()\n"
        f"assert args.port[-1] == {port}, args.port\n"
        "print('already running; use it at http://127.0.0.1:3199/.', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    controller = StatusController(
        repo_root=checkout,
        server_command=(sys.executable, str(existing)),
        server_args=("--port", str(port)),
        request_probe=lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html></html>")
        ),
    )
    try:
        started = controller.start()
        assert started.url == ""

        snapshot = _wait_for(
            lambda: (
                current
                if (current := controller.poll()).backend.state is ServiceState.OK
                else None
            )
        )
        assert snapshot.url == "http://127.0.0.1:3199/"
        assert "already-running instance" in snapshot.backend.reason
    finally:
        controller.close()


def test_dist_missing_is_reported_with_the_platform_installer(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    controller = StatusController(
        repo_root=checkout,
        server_command=(sys.executable, str(_fake_server(tmp_path))),
    )

    snapshot = controller.start()

    assert snapshot.frontend.state is ServiceState.ERROR
    assert "frontend/dist missing" in snapshot.frontend.reason
    # The install hint renders with the OS-native separator (backslashes on
    # Windows), so assert on the folder name alone.
    assert "installers" in snapshot.frontend.reason
    assert controller.process is None


def test_server_death_underneath_the_window_reports_exit_reason(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.start()
    process = controller.process
    assert process is not None
    _wait_for(lambda: controller.poll().backend.state is ServiceState.OK)

    process.kill()
    process.wait(timeout=3)
    snapshot = controller.poll()

    assert snapshot.backend.state is ServiceState.ERROR
    assert "Server exited with code" in snapshot.backend.reason
    controller.close()


def test_existing_instance_exit_two_keeps_its_real_url_healthy(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    already_running = tmp_path / "already_running.py"
    already_running.write_text(
        "import sys\n"
        "print('already running; use it at http://127.0.0.1:3199/.', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    controller = StatusController(
        repo_root=checkout,
        server_command=(sys.executable, str(already_running)),
        request_probe=lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html></html>")
        ),
    )
    controller.start()

    def existing_snapshot():
        current = controller.poll()
        return current if current.backend.state is ServiceState.OK and current.exit_code == 2 else None

    snapshot = _wait_for(existing_snapshot)

    assert snapshot.url == "http://127.0.0.1:3199/"
    assert snapshot.exit_code == 2
    assert "already-running instance" in snapshot.backend.reason
    controller.close()


def test_real_server_watcher_turns_control_file_into_graceful_exit(tmp_path: Path) -> None:
    from launch import serve

    server = SimpleNamespace(should_exit=False)
    stop = threading.Event()
    control = tmp_path / "stop"
    watcher = threading.Thread(
        target=serve._watch_statusapp,
        args=(server, control, os.getpid(), stop),
    )
    watcher.start()
    control.write_text("stop\n", encoding="utf-8")
    watcher.join(timeout=2.0)

    assert not watcher.is_alive()
    assert server.should_exit is True


def test_real_server_watcher_stops_if_status_parent_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from launch import serve

    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(serve, "pid_is_running", lambda _pid: False)

    serve._watch_statusapp(server, tmp_path / "absent", 999999, threading.Event())

    assert server.should_exit is True


@pytest.mark.skipif(os.name == "nt", reason="Win32 uses a kill-on-close Job Object")
def test_close_cannot_orphan_a_descendant_process(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    heartbeat = Path(str(child_pid_file) + ".heartbeat")
    controller = _controller(
        tmp_path,
        server_args=("--child-pid-file", str(child_pid_file)),
        shutdown_timeout=0.5,
    )
    controller.start()
    _wait_for(child_pid_file.is_file)
    _wait_for(heartbeat.is_file)
    first_heartbeat = heartbeat.read_text(encoding="utf-8")
    _wait_for(lambda: heartbeat.read_text(encoding="utf-8") != first_heartbeat)

    controller.close()

    stopped_heartbeat = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.15)
    assert heartbeat.read_text(encoding="utf-8") == stopped_heartbeat
