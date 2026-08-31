"""Headless contracts for the tkinter status application's lifecycle owner."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from launchers.statusapp.controller import (
    LampStatus,
    PooledProbe,
    ServiceState,
    StatusController,
    StatusSnapshot,
)
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


def _counting_probe():
    """A request probe that answers like a healthy server and records its calls."""

    seen: list[str] = []

    def probe(url: str, _timeout: float) -> tuple[int, bytes]:
        seen.append(url)
        return (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html><body>fake SPA</body></html>")
        )

    return seen, probe


def _settled(controller: StatusController) -> None:
    _wait_for(lambda: controller.poll().backend.state is ServiceState.OK)


def test_the_spa_body_is_fetched_once_rather_than_on_every_poll(tmp_path: Path) -> None:
    """Whether the server can serve ``<html>`` cannot change while it is running.

    Re-downloading six kilobytes of index.html and lowercasing it to look for
    "<html" was half of this launcher's idle HTTP traffic -- 610 requests in a
    181-second window -- and every one of them could only confirm what the
    first had already established.
    """

    seen, probe = _counting_probe()
    controller = _controller(tmp_path, request_probe=probe)
    controller.start()
    try:
        _wait_for(lambda: controller.poll().frontend.state is ServiceState.OK)
        for _ in range(5):
            assert controller.poll().frontend.state is ServiceState.OK
    finally:
        controller.close()

    assert sum(1 for url in seen if not url.endswith("/health")) == 1


def test_a_dead_child_is_reported_by_the_watcher_without_another_request(
    tmp_path: Path,
) -> None:
    """The replacement for the 4 Hz health poll, and strictly better than it.

    A thread parked in ``Popen.wait()`` costs no wakeups and returns the instant
    the child exits, where the poll it replaces cost two unpooled loopback
    connections every 250 ms and could still be up to 250 ms late.
    """

    seen, probe = _counting_probe()
    controller = _controller(tmp_path, request_probe=probe)
    controller.start()
    _settled(controller)
    process = controller.process
    assert process is not None

    lost: list[object] = []
    reported = threading.Event()
    watcher = controller.watch_backend(lambda snapshot: (lost.append(snapshot), reported.set()))
    assert watcher is not None
    quiet_from = len(seen)

    process.kill()
    try:
        assert reported.wait(10.0), "the watcher did not notice the child exiting"
    finally:
        controller.close()

    snapshot = lost[0]
    assert snapshot.backend.state is ServiceState.ERROR  # type: ignore[attr-defined]
    assert "Server exited with code" in snapshot.backend.reason  # type: ignore[attr-defined]
    # And it cost nothing on the wire: an exited child's reason comes from its
    # return code and its collected output, never from a socket it no longer owns.
    assert len(seen) == quiet_from


def test_a_deliberate_stop_is_not_reported_as_a_backend_loss(tmp_path: Path) -> None:
    """Quitting ends the same ``wait()`` that a crash does. Only one is news."""

    controller = _controller(tmp_path)
    controller.start()
    _settled(controller)
    lost: list[object] = []
    watcher = controller.watch_backend(lost.append)
    assert watcher is not None

    controller.close()
    watcher.join(timeout=10.0)

    assert not watcher.is_alive()
    assert lost == []


def test_a_loss_is_reported_once_and_the_watcher_refuses_to_re_arm(
    tmp_path: Path,
) -> None:
    """A dead handle stays dead; re-arming would rediscover it on every call."""

    controller = _controller(tmp_path)
    controller.start()
    _settled(controller)
    lost: list[object] = []
    reported = threading.Event()
    controller.watch_backend(lambda snapshot: (lost.append(snapshot), reported.set()))
    process = controller.process
    assert process is not None

    process.kill()
    try:
        assert reported.wait(10.0)
        assert controller.watch_backend(lost.append) is None
    finally:
        controller.close()
    assert len(lost) == 1


def test_an_adopted_instance_is_asked_rarely_and_believed_only_after_a_retry(
    tmp_path: Path,
) -> None:
    """Exit code 2 is the one case with no handle to block on.

    The server belongs to another launcher, so HTTP is the only question
    available -- which is exactly why it is asked every 30 seconds rather than
    four times a second, and why a single failure buys a second opinion instead
    of a dialog.
    """

    checkout = _checkout(tmp_path)
    already_running = tmp_path / "already_running.py"
    already_running.write_text(
        "import sys\n"
        "print('already running; use it at http://127.0.0.1:3199/.', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    _seen, probe = _counting_probe()
    #: True answers, then the blip that must not be believed, then a real loss.
    answers = [True, False, True, False, False]
    asked: list[str] = []

    def liveness(url: str, _timeout: float) -> tuple[int, bytes]:
        asked.append(url)
        if answers.pop(0):
            return 200, b'{"version":"test"}'
        raise OSError("connection refused")

    controller = StatusController(
        repo_root=checkout,
        server_command=(sys.executable, str(already_running)),
        request_probe=probe,
        liveness_probe=liveness,
        adopted_probe_interval=0.02,
        adopted_retry_delay=0.02,
    )
    controller.start()
    _wait_for(
        lambda: (
            current
            if (current := controller.poll()).exit_code == 2
            and current.backend.state is ServiceState.OK
            else None
        )
    )

    lost: list[object] = []
    reported = threading.Event()
    watcher = controller.watch_backend(lambda snapshot: (lost.append(snapshot), reported.set()))
    assert watcher is not None
    try:
        assert reported.wait(10.0), "the adopted instance was never called lost"
    finally:
        controller.close()

    assert asked == ["http://127.0.0.1:3199/health"] * 5
    assert lost[0].exit_code == 2  # type: ignore[attr-defined]
    assert "stopped answering" in lost[0].backend.reason  # type: ignore[attr-defined]


class _CountingServer(ThreadingHTTPServer):
    """Counts accepted connections so pooling can be asserted rather than assumed."""

    daemon_threads = True
    connections = 0

    def process_request(self, request: object, client_address: object) -> None:
        self.connections += 1
        super().process_request(request, client_address)  # type: ignore[arg-type]


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        body = b'{"version":"test"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def test_the_liveness_probe_reuses_one_connection(tmp_path: Path) -> None:
    """``urlopen`` cannot pool, and that is what made 8 requests cost 8 sockets."""

    server = _CountingServer(("127.0.0.1", 0), _HealthHandler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    probe = PooledProbe()
    url = f"http://127.0.0.1:{server.server_address[1]}/health"
    try:
        for _ in range(5):
            assert probe(url, 5.0) == (200, b'{"version":"test"}')
        assert server.connections == 1
    finally:
        probe.close()
        server.shutdown()
        server.server_close()
        serving.join(timeout=5.0)


def _view_snapshot(state: ServiceState, reason: str = "reason") -> StatusSnapshot:
    return StatusSnapshot(
        backend=LampStatus(state, reason),
        frontend=LampStatus(state, reason),
        url="http://127.0.0.1:3199/",
        pid=None if state is ServiceState.ERROR else 123,
        exit_code=1 if state is ServiceState.ERROR else None,
    )


class _ViewController:
    """Everything :class:`StatusView` asks of a controller, and nothing else."""

    def __init__(self, snapshot: StatusSnapshot) -> None:
        self.snapshot = snapshot
        self.polls = 0
        self.watchers: list[object] = []

    @property
    def url(self) -> str:
        return self.snapshot.url

    def poll(self) -> StatusSnapshot:
        self.polls += 1
        return self.snapshot

    def take_update_request(self) -> None:
        return None

    def watch_backend(self, on_lost: object) -> None:
        self.watchers.append(on_lost)


class _FakeRoot:
    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []
        self.destroyed = 0

    def after(self, delay: int, callback: object) -> None:
        self.scheduled.append((delay, callback))

    def destroy(self) -> None:
        self.destroyed += 1


class _Text:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def _headless_view(controller: _ViewController):
    """A StatusView with its widgets stubbed, so no display is needed.

    ``Tk()`` is the one call in this module that needs a windowing system, and
    the tick loop under test touches none of the widgets except through the
    three text variables and two lamps replaced here.
    """

    from launchers.statusapp import view as view_module

    view = view_module.StatusView.__new__(view_module.StatusView)
    view.root = _FakeRoot()  # type: ignore[assignment]
    view.controller = controller  # type: ignore[assignment]
    view._tick_ms = view_module.TICK_MS
    view._startup_poll_interval = 0.0
    view._closing = False
    view._starting = False
    view._settled = False
    view._poll_running = False
    view._next_poll_at = 0.0
    view._updates = queue.SimpleQueue()
    view._update_errors = queue.SimpleQueue()
    view._backend_reason = _Text()  # type: ignore[assignment]
    view._frontend_reason = _Text()  # type: ignore[assignment]
    view._url_text = _Text()  # type: ignore[assignment]
    view._open_button = SimpleNamespace(configure=lambda **_kwargs: None)  # type: ignore[assignment]
    view._backend_lamp = SimpleNamespace(itemconfigure=lambda *_a, **_k: None)  # type: ignore[assignment]
    view._frontend_lamp = SimpleNamespace(itemconfigure=lambda *_a, **_k: None)  # type: ignore[assignment]
    return view


def test_the_status_window_stops_polling_once_the_backend_answers(tmp_path: Path) -> None:
    """The same bug as the desktop window's, in the Tk path.

    A 100 ms tick plus a fresh thread every 0.55 s, each running the same two
    unpooled probes, for as long as the window stayed open. Two lamps and a URL
    do not need that, and once the backend has answered there is nothing left
    for a poll to discover.
    """

    controller = _ViewController(_view_snapshot(ServiceState.OK, "Healthy — vtest"))
    view = _headless_view(controller)
    view._updates.put(("snapshot", controller.snapshot))

    for _ in range(12):
        view._tick()

    assert controller.polls == 0
    assert len(controller.watchers) == 1
    assert view._backend_reason.value == "Healthy — vtest"
    # And the tick that remains is not a ten-times-a-second one.
    assert {delay for delay, _callback in view.root.scheduled} == {250}


def test_the_status_window_still_shows_a_backend_that_dies(tmp_path: Path) -> None:
    """The watcher replaces the poll as the source of the bad news, not as well."""

    controller = _ViewController(_view_snapshot(ServiceState.OK, "Healthy — vtest"))
    view = _headless_view(controller)
    view._updates.put(("snapshot", controller.snapshot))
    view._tick()
    (on_lost,) = controller.watchers

    gone = _view_snapshot(ServiceState.ERROR, "Server exited with code 3: solver worker died")
    on_lost(gone)  # type: ignore[operator]
    view._tick()

    assert "solver worker died" in view._backend_reason.value
    # Being told is not a reason to start asking again.
    assert controller.polls == 0
    assert len(controller.watchers) == 1


def test_the_status_window_does_poll_until_the_backend_answers(tmp_path: Path) -> None:
    """Startup is the part that genuinely needs HTTP, and it is left alone."""

    controller = _ViewController(_view_snapshot(ServiceState.STARTING, "Waiting for /health"))
    view = _headless_view(controller)

    view._tick()
    _wait_for(lambda: controller.polls > 0)

    assert view._settled is False
    assert controller.watchers == []


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


def test_a_dependency_print_is_not_reported_as_the_reason_the_server_died(
    tmp_path: Path,
) -> None:
    """The failure line wins over whatever happened to be written last.

    bempp prints a notice about a missing optional Gmsh executable on every
    start. With stdout and stderr merged, a block-buffered print like that can
    flush at exit -- after the logging that says what actually went wrong --
    and the window then blames the wrong thing entirely.
    """

    server = tmp_path / "noisy_failure.py"
    server.write_text(
        "import sys\n"
        "print('2026-08-25T18:02:03+0000 ERROR wg.launch: the data directory "
        "is not writable', file=sys.stderr)\n"
        "sys.stderr.flush()\n"
        "print('Could not find Gmsh.Interactive plotting and shapes module "
        "not available.')\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    controller = _controller(
        tmp_path, server_command=(sys.executable, str(server))
    )
    controller.start()
    try:
        snapshot = _wait_for(
            lambda: (
                controller.poll()
                if controller.poll().backend.state is ServiceState.ERROR
                else None
            )
        )
    finally:
        controller.close()

    assert "the data directory is not writable" in snapshot.backend.reason
    assert "Gmsh" not in snapshot.backend.reason
    assert "server.log" in snapshot.backend.reason


def test_a_clean_exit_with_no_error_says_so_rather_than_quoting_stdout(
    tmp_path: Path,
) -> None:
    server = tmp_path / "quiet_exit.py"
    server.write_text(
        "print('Could not find Gmsh.Interactive plotting and shapes module "
        "not available.')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    controller = _controller(
        tmp_path, server_command=(sys.executable, str(server))
    )
    controller.start()
    try:
        snapshot = _wait_for(
            lambda: (
                controller.poll()
                if controller.poll().backend.state is ServiceState.ERROR
                else None
            )
        )
    finally:
        controller.close()

    assert "without reporting an error" in snapshot.backend.reason
    assert "Gmsh" not in snapshot.backend.reason


#: Exits 2 the way ``launch/serve.py`` does when another process holds the
#: instance lock, for the first ``WG2_TEST_CONFLICTS`` attempts, and serves
#: afterwards. Two launchers sharing one machine is the real situation: the
#: previous one is inside its own shutdown and has not released the lock yet.
LOCK_CONFLICT_SERVER = r'''from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--status-control", type=Path, required=True)
args, _unknown = parser.parse_known_args()

counter = Path(os.environ["WG2_TEST_ATTEMPTS"])
seen = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(seen + 1), encoding="utf-8")

if seen < int(os.environ["WG2_TEST_CONFLICTS"]):
    print(
        "Waveguide Generator is already running (pid 4242, port 3199; lock lock). "
        "Close that instance or use it at http://127.0.0.1:3199/.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(2)

args.status_control.with_name("ready.json").write_text(
    '{"host":"127.0.0.1","port":3199}\n', encoding="utf-8"
)
print("fake server ready", flush=True)
while not args.status_control.is_file():
    time.sleep(0.02)
'''


class _Attempts:
    """The conflicting server's attempt counter, and a probe that agrees with it."""

    def __init__(self, path: Path, conflicts: int) -> None:
        self.path = path
        self.conflicts = conflicts

    def count(self) -> int:
        return int(self.path.read_text(encoding="utf-8")) if self.path.exists() else 0

    def probe(self, url: str, _timeout: float) -> tuple[int, bytes]:
        # Nothing is serving while the lock holder is still on its way out, so
        # the URL its message names cannot be reached either.
        if self.count() <= self.conflicts:
            raise OSError("connection refused")
        return (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html></html>")
        )


def _conflicted_controller(
    tmp_path: Path, attempts: _Attempts, **kwargs: object
) -> StatusController:
    script = tmp_path / "lock_conflict.py"
    script.write_text(LOCK_CONFLICT_SERVER, encoding="utf-8")
    options: dict[str, object] = {
        "repo_root": _checkout(tmp_path),
        "server_command": (sys.executable, str(script)),
        "server_args": ("--port", "3199"),
        "request_probe": attempts.probe,
        "environ": {
            **os.environ,
            "WG2_TEST_ATTEMPTS": str(attempts.path),
            "WG2_TEST_CONFLICTS": str(attempts.conflicts),
        },
    }
    options.update(kwargs)
    return StatusController(**options)


def test_a_lock_holder_that_is_still_leaving_is_waited_out_not_adopted(
    tmp_path: Path,
) -> None:
    """The reported restart loop: close a wedged session, reopen it too soon.

    Closing spends the launcher's whole ``shutdown_timeout`` before the Job
    Object takes the tree down, so a relaunch inside those seconds finds the
    lock still held. Adopting the instance the exit-2 message names would hand
    the window a server about to be killed; reporting it would tell the user
    their application is broken. Starting again is what actually works.
    """

    attempts = _Attempts(tmp_path / "attempts", conflicts=2)
    controller = _conflicted_controller(tmp_path, attempts, lock_conflict_interval=0.0)
    try:
        controller.start()
        snapshot = _wait_for(
            lambda: (
                current
                if (current := controller.poll()).backend.state is ServiceState.OK
                else None
            ),
            timeout=20.0,
        )
        # Our own server, with a live pid -- not the instance that was in the
        # way, which would come back as exit_code 2 and no pid of ours.
        assert snapshot.exit_code is None
        assert snapshot.pid is not None
        assert snapshot.url == "http://127.0.0.1:3199/"
        assert attempts.count() == 3
    finally:
        controller.close()


def test_a_lock_holder_that_never_serves_is_reported_in_the_end(tmp_path: Path) -> None:
    """The wait is bounded: a lock nobody releases is still a real conflict."""

    attempts = _Attempts(tmp_path / "attempts", conflicts=99)
    controller = _conflicted_controller(tmp_path, attempts, lock_conflict_timeout=0.0)
    try:
        controller.start()
        snapshot = _wait_for(
            lambda: (
                current
                if (current := controller.poll()).backend.state is ServiceState.ERROR
                else None
            ),
            timeout=20.0,
        )
        assert "already running" in snapshot.backend.reason
        assert snapshot.exit_code == 2
        # A budget of zero forbids even the first retry.
        assert attempts.count() == 1
    finally:
        controller.close()


def test_the_wait_starts_one_server_per_interval_rather_than_one_per_poll(
    tmp_path: Path,
) -> None:
    """``poll()`` runs four times a second; the retry must not follow it."""

    now = [1_000.0]
    attempts = _Attempts(tmp_path / "attempts", conflicts=99)
    controller = _conflicted_controller(
        tmp_path,
        attempts,
        lock_conflict_interval=0.75,
        lock_conflict_timeout=60.0,
        clock=lambda: now[0],
    )

    def exited() -> bool:
        process = controller.process
        return process is not None and process.poll() is not None

    try:
        controller.start()
        _wait_for(exited, timeout=20.0)
        assert attempts.count() == 1

        # The first conflict retries at once: waiting out an interval the lock
        # holder may already have finished would be a delay for nothing.
        controller.poll()
        _wait_for(exited, timeout=20.0)
        assert attempts.count() == 2

        for _ in range(6):
            controller.poll()
        assert attempts.count() == 2

        now[0] += 0.75
        controller.poll()
        _wait_for(exited, timeout=20.0)
        assert attempts.count() == 3
    finally:
        controller.close()
