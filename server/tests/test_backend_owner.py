"""Backend-owner discovery and exactly-once start (server/platform/backend_owner.py)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from server.platform import backend_owner
from server.platform.backend_owner import (
    BackendDataDirMismatch,
    BackendOwnerError,
    BackendSpawnError,
    BackendStartTimeout,
    discover_backend,
    ensure_backend,
)
from server.platform.instance import LOCK_FILENAME, InstanceLock, pid_is_running, read_lock_info
from server.tests.backend_breakaway import WINDOWS_BREAKAWAY_REFUSAL, require_backend_breakaway

ROOT = Path(__file__).resolve().parents[2]

FAKE_BACKEND = textwrap.dedent(
    """
    import json, os, signal, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    data_dir, pids, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    reported, delay = sys.argv[5], float(sys.argv[6])
    # Record the spawn before any server import: that import can take seconds
    # on hosted Windows runners, longer than a test waits for the owner.
    with open(pids, "a") as handle:
        handle.write(str(os.getpid()) + "\\n")
    sys.path.insert(0, sys.argv[1])
    from server.platform.instance import InstanceAlreadyRunning, InstanceLock
    if mode == "hang-ignore-term" and os.name == "posix":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode in ("hang", "hang-ignore-term"):
        time.sleep(600)
    if mode == "fail":
        print("intentional startup failure", flush=True)
        sys.exit(1)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"version": "9.9", "commit": "abc", "data_dir": reported,
                       "pid": os.getpid()}
            if os.environ.get("WG2_BACKEND_CONTAINED") == "1":
                payload["lifetime"] = "contained"
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    # Publish the port only when serving starts, so discovery does not block on
    # a socket already listening but not yet handling requests.
    lock = InstanceLock(data_dir / "locks")
    time.sleep(float(os.environ.get("FAKE_STARTUP_DELAY", "0")))
    try:
        lock.acquire(1)
    except InstanceAlreadyRunning:
        sys.exit(2)
    with open(pids.with_suffix(".owners"), "a") as handle:
        handle.write(str(os.getpid()) + "\\n")
    if mode == "hold":
        time.sleep(600)
    time.sleep(delay)
    class Server(HTTPServer):
        # HTTPServer.server_bind resolves the host's FQDN (reverse DNS), which
        # can stall for many seconds on hosted runners; a loopback fake needs none.
        def server_bind(self):
            import socketserver
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    server = Server(("127.0.0.1", 0), Handler)
    lock.update_port(server.server_address[1])
    server.serve_forever()
    """
)

CLIENT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from server.platform.backend_owner import ensure_backend
    handle = ensure_backend(sys.argv[2], sys.argv[3:], timeout=15)
    print(handle.pid)
    """
)


def _reap_if_child(pid: int) -> None:
    if os.name == "posix":
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def _terminate_pid(pid: int) -> None:
    _reap_if_child(pid)
    if not pid_is_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        if pid_is_running(pid):
            raise
        return
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        _reap_if_child(pid)
        if not pid_is_running(pid):
            return
        time.sleep(0.02)
    if os.name == "posix":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _reap_if_child(pid)


@pytest.fixture
def data_dir(tmp_path: Path):
    path = tmp_path / "data"
    (path / "locks").mkdir(parents=True)
    return path


@pytest.fixture
def fake(tmp_path: Path, data_dir: Path, monkeypatch):
    script = tmp_path / "fake_backend.py"
    script.write_text(FAKE_BACKEND)
    pids_file = tmp_path / "pids.txt"
    processes: list[subprocess.Popen] = []
    real_spawn = backend_owner._spawn

    def tracked_spawn(argv, root, env=None, **kwargs):
        process, log = real_spawn(argv, root, env, **kwargs)
        processes.append(process)
        return process, log

    monkeypatch.setattr(backend_owner, "_spawn", tracked_spawn)

    def argv(mode: str = "serve", reported: str | None = None, delay: float = 0) -> list[str]:
        return [
            sys.executable, str(script), str(ROOT), str(data_dir), str(pids_file),
            mode, str(data_dir) if reported is None else reported, str(delay),
        ]

    def recorded_pids():
        return [int(pid) for pid in pids_file.read_text().split()] if pids_file.exists() else []

    def start(args, **kwargs):
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, **kwargs)
        processes.append(process)
        return process

    argv.pids = recorded_pids
    argv.spawns = lambda: len(recorded_pids())
    argv.owners = lambda: (
        pids_file.with_suffix(".owners").read_text().split()
        if pids_file.with_suffix(".owners").exists() else []
    )
    argv.start = start
    argv.processes = processes
    yield argv
    # Every fake records its own pid, including detached children of the three
    # clients. Never infer ownership from metadata or search process commands.
    for process in processes:
        if process.poll() is None:
            _terminate_pid(process.pid)
        process.wait(timeout=5)
    for pid in recorded_pids():
        _terminate_pid(pid)


def _wait_until(
    predicate, timeout: float = 10, *, process: subprocess.Popen | None = None
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process is not None:
            assert process.poll() is None, "fake backend exited before reaching the expected state"
        time.sleep(0.02)
    raise AssertionError("fake backend did not reach the expected state")


def test_discover_none_without_lock_file(data_dir: Path) -> None:
    assert discover_backend(data_dir) is None
    assert not (data_dir / "locks" / LOCK_FILENAME).exists()


@pytest.mark.parametrize("contents", ["", "invalid", '{"pid": 1, "port": 0}'])
def test_discover_ignores_incomplete_metadata(data_dir: Path, contents: str) -> None:
    (data_dir / "locks" / LOCK_FILENAME).write_text(contents)
    assert discover_backend(data_dir) is None


def test_discover_ignores_stale_lock_metadata(data_dir: Path) -> None:
    (data_dir / "locks" / LOCK_FILENAME).write_text(
        json.dumps({"pid": os.getpid(), "port": 1}) + "\n"
    )
    assert discover_backend(data_dir) is None


def test_discover_running_backend(data_dir: Path, fake) -> None:
    process = fake.start(fake())
    _wait_until(lambda: discover_backend(data_dir), process=process)
    handle = discover_backend(data_dir)
    assert handle is not None
    assert handle.pid == process.pid
    assert handle.base_url == f"http://127.0.0.1:{handle.port}"
    assert handle.version == "9.9"
    assert handle.commit == "abc"
    assert handle.data_dir == data_dir.resolve()


def test_data_dir_mismatch_waits_until_deadline(data_dir: Path, tmp_path: Path, fake) -> None:
    require_backend_breakaway()
    owner = fake.start(fake(reported=str(tmp_path / "elsewhere")))
    _wait_until(
        lambda: (info := read_lock_info(data_dir / "locks" / LOCK_FILENAME)) and info.port > 1,
        process=owner,
    )
    assert discover_backend(data_dir) is None
    started = time.monotonic()
    with pytest.raises(BackendDataDirMismatch, match="startup deadline"):
        ensure_backend(data_dir, fake(), timeout=0.8)
    assert time.monotonic() - started >= 0.8
    assert fake.spawns() == 2  # one lock loser; no respawn or folder workaround
    assert fake.owners() == [str(owner.pid)]


def test_unrelated_pid_on_metadata_port_times_out(data_dir: Path, tmp_path: Path, fake) -> None:
    require_backend_breakaway()
    unrelated = fake.start(fake(reported=str(tmp_path / "elsewhere")))
    _wait_until(
        lambda: (info := read_lock_info(data_dir / "locks" / LOCK_FILENAME)) and info.port > 1,
        process=unrelated,
    )
    # The preferred port belongs to an unrelated server, not the lock holder.
    info = read_lock_info(data_dir / "locks" / LOCK_FILENAME)
    assert info is not None
    (data_dir / "locks" / LOCK_FILENAME).write_text(
        json.dumps({"pid": os.getpid(), "port": info.port})
    )
    assert discover_backend(data_dir) is None
    with pytest.raises(BackendStartTimeout, match=f"pid {os.getpid()}, port {info.port}"):
        ensure_backend(data_dir, fake(), timeout=0.8)
    assert fake.spawns() == 2
    assert fake.owners() == [str(unrelated.pid)]


def test_pid_mismatch_is_unconfirmed(data_dir: Path, monkeypatch) -> None:
    lock = InstanceLock(data_dir / "locks")
    info = lock.acquire(4000)
    monkeypatch.setattr(
        backend_owner, "_fetch_health", lambda port: {"data_dir": str(data_dir), "pid": 1}
    )
    try:
        assert info.pid != 1
        assert discover_backend(data_dir) is None
    finally:
        lock.release()


def test_ensure_returns_running_backend_without_spawning(data_dir: Path, fake) -> None:
    process = fake.start(fake())
    _wait_until(lambda: discover_backend(data_dir), process=process)
    spawned = fake.spawns()
    handle = ensure_backend(data_dir, fake(), timeout=10)
    assert handle.pid == process.pid
    assert fake.spawns() == spawned


def test_ensure_starts_when_lock_is_stale(data_dir: Path, fake) -> None:
    require_backend_breakaway()
    (data_dir / "locks" / LOCK_FILENAME).write_text(json.dumps({"pid": 1, "port": 1}) + "\n")
    handle = ensure_backend(data_dir, fake(), timeout=10)
    assert handle.pid != os.getpid()
    assert fake.spawns() == 1
    assert discover_backend(data_dir) == handle
    assert pid_is_running(handle.pid)  # successful backend stays detached
    assert (data_dir / "logs" / backend_owner.LOG_FILENAME).exists()


def test_concurrent_clients_start_exactly_one_backend(data_dir: Path, fake, monkeypatch) -> None:
    require_backend_breakaway()
    monkeypatch.setenv("FAKE_STARTUP_DELAY", "0.5")
    clients = [
        fake.start(
            [sys.executable, "-c", CLIENT, str(ROOT), str(data_dir), *fake()],
            stdout=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    pids = []
    for client in clients:
        out, _ = client.communicate(timeout=20)
        assert client.returncode == 0
        pids.append(int(out.strip()))
    assert len(set(pids)) == 1
    assert fake.spawns() == 1
    assert fake.owners() == [str(pids[0])]


def test_spawn_that_never_becomes_healthy_times_out(data_dir: Path, fake) -> None:
    require_backend_breakaway()
    started = time.monotonic()
    with pytest.raises(BackendStartTimeout, match="Terminated and waited for.*child pid"):
        ensure_backend(data_dir, fake("hang"), timeout=0.8)
    assert time.monotonic() - started < 5
    assert len(fake.pids()) == 1
    assert not pid_is_running(fake.pids()[0])
    assert fake.processes[-1].poll() is not None
    # A following client can acquire the released start lock.
    with pytest.raises(BackendStartTimeout):
        ensure_backend(data_dir, fake("hang"), timeout=0.5)
    assert all(not pid_is_running(pid) for pid in fake.pids())


def test_spawn_failure_reports_exit_code_and_log_tail(data_dir: Path, fake) -> None:
    require_backend_breakaway()
    with pytest.raises(BackendSpawnError, match="exited with code 1.*") as error:
        ensure_backend(data_dir, fake("fail"), timeout=10)
    assert "intentional startup failure" in str(error.value)
    assert fake.spawns() == 1
    with pytest.raises(BackendSpawnError, match="Could not start"):
        ensure_backend(data_dir, [str(data_dir / "no-such-binary")], timeout=10)


def test_spawn_exiting_2_adopts_delayed_owner(data_dir: Path, fake) -> None:
    require_backend_breakaway()
    # Delay is injectable in the fake; exceed the removed five-second grace.
    owner = fake.start(fake(delay=5.3))
    _wait_until(lambda: read_lock_info(data_dir / "locks" / LOCK_FILENAME))
    assert discover_backend(data_dir) is None
    handle = ensure_backend(data_dir, fake(), timeout=10)
    assert handle.pid == owner.pid
    # ensure_backend returns once the owner is healthy; our spawned child may
    # still be starting. It must lose the lock and exit 2 without owning.
    assert fake.processes[-1].wait(timeout=30) == 2
    assert fake.spawns() == 2
    assert fake.owners() == [str(owner.pid)]


def test_probe_loop_never_contends_with_instance_lock(data_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(backend_owner, "_fetch_health", lambda port: None)
    stop = threading.Event()
    failures = []
    probes = []

    def probe():
        try:
            while not stop.is_set():
                discover_backend(data_dir)
                probes.append(True)
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=probe)
    thread.start()
    cycles = 0
    deadline = time.monotonic() + 1.5
    try:
        while time.monotonic() < deadline:
            lock = InstanceLock(data_dir / "locks")
            try:
                lock.acquire(4000)
                cycles += 1
            except Exception as exc:
                failures.append(exc)
            finally:
                lock.release()
    finally:
        stop.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert not failures
    assert cycles > 10
    assert probes


def test_case_variant_data_folder_is_accepted(data_dir: Path, monkeypatch) -> None:
    variant = data_dir.with_name(data_dir.name.upper())
    if not variant.exists():
        pytest.skip("temporary filesystem is case-sensitive")
    assert os.path.samefile(variant, data_dir)
    lock = InstanceLock(data_dir / "locks")
    info = lock.acquire(4000)
    monkeypatch.setattr(
        backend_owner, "_fetch_health",
        lambda port: {"data_dir": str(variant), "pid": info.pid},
    )
    try:
        assert ensure_backend(data_dir, ["unused"], timeout=5).pid == info.pid
    finally:
        lock.release()


def test_same_path_falls_back_for_missing_directory(tmp_path: Path) -> None:
    assert backend_owner._same_path(str(tmp_path / "missing"), tmp_path / "missing")
    assert not backend_owner._same_path(None, tmp_path)


@pytest.mark.parametrize("operation", ["mkdir", "open"])
def test_start_lock_filesystem_errors_are_actionable(data_dir: Path, monkeypatch, operation) -> None:
    def denied(*args, **kwargs):
        raise PermissionError("access denied")

    if operation == "mkdir":
        monkeypatch.setattr(Path, "mkdir", denied)
    else:
        monkeypatch.setattr(backend_owner.os, "open", denied)
    lock = backend_owner._StartLock(data_dir / "locks" / backend_owner.START_LOCK_FILENAME)
    with pytest.raises(BackendOwnerError, match="Check that the data folder is writable"):
        lock.acquire(time.monotonic() + 1)


def test_windows_creation_flags(data_dir: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from server.platform import process_tree

    captured = {}
    monkeypatch.setattr(process_tree, "confine_to_windows_job", lambda *args, **kwargs:
                        SimpleNamespace(terminate=lambda: None, close=lambda: None))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", lambda: 0)
    monkeypatch.setattr(process_tree, "require_windows_job_detached", lambda handle: None)

    def popen(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=123, _handle=42)

    monkeypatch.setattr(backend_owner.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "Popen", popen)
    backend_owner._spawn(["backend", "--no-browser"], data_dir)
    assert captured["creationflags"] == 0x200 | 0x8000000
    assert "start_new_session" not in captured


@pytest.mark.parametrize("refusal_stage", ["probe", "outer"])
@pytest.mark.parametrize("suspended", [False, True])
@pytest.mark.parametrize("prepared_env", [False, True])
def test_windows_contained_fallback_keeps_start_job(
    data_dir, monkeypatch, refusal_stage, suspended, prepared_env,
):
    from server.platform import job_start, process_tree

    calls, spawns, stopped = [], [], []
    job = SimpleNamespace(terminate=lambda: calls.append("terminate job"), close=lambda: None)
    active = {}

    def probe():
        calls.append("probe")
        if refusal_stage == "probe":
            raise process_tree.BreakawayRefused(WINDOWS_BREAKAWAY_REFUSAL)
        return 0x01000000

    def detached(handle):
        calls.append("detached")
        raise process_tree.BreakawayRefused("The backend could not detach from the caller's Windows job chain.")

    def confine(pid, **kwargs):
        calls.append("confine")
        assert kwargs == {"subject": "the starting backend", "kill_on_close": False, "allow_breakaway": True}
        return job

    @contextmanager
    def start(admit, **kwargs):
        assert kwargs == {"required": True, "subject": "the starting backend"}
        state = SimpleNamespace(fired=False, job=None)
        active.update(admit=admit, state=state)
        yield state

    def popen(argv, **kwargs):
        spawns.append(kwargs)
        pid = 120 + len(spawns)
        if suspended:
            active["state"].fired = True
            try:
                active["state"].job = active["admit"](pid, 42)
            except process_tree.BreakawayRefused as exc:
                # The real suspended-start adapter discards the unrun child
                # and wraps admission failures in ContainedStartError.
                stopped.append(pid)
                raise job_start.ContainedStartError("admission refused") from exc
        code = [None]
        return SimpleNamespace(
            pid=pid, _handle=42, poll=lambda: code[0],
            terminate=lambda: (stopped.append(pid), code.__setitem__(0, -1)),
            wait=lambda **kwargs: code[0],
        )

    monkeypatch.setattr(backend_owner, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", probe)
    monkeypatch.setattr(process_tree, "require_windows_job_detached", detached)
    monkeypatch.setattr(process_tree, "confine_to_windows_job", confine)
    monkeypatch.setattr(job_start, "windows_job_start", start)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("WG2_BACKEND_CONTAINED", "inherited")
    environment = {"CUSTOM": "retained"} if prepared_env else None
    child, log = backend_owner._spawn(["backend"], data_dir, environment, allow_contained=True)
    assert child._wg_start_job is job
    assert calls.count("probe") == 1
    assert calls.count("detached") == (1 if refusal_stage == "outer" else 0)
    assert calls.count("confine") == 1
    assert not spawns[-1]["creationflags"] & 0x01000000
    assert spawns[-1]["env"]["WG2_BACKEND_CONTAINED"] == "1"
    if prepared_env:
        assert environment == {"CUSTOM": "retained"}  # Caller env was not mutated.
        assert spawns[-1]["env"]["CUSTOM"] == "retained"
    if refusal_stage == "outer":
        assert len(spawns) == 2 and stopped == [121]
        assert spawns[0]["creationflags"] & 0x01000000
        assert "WG2_BACKEND_CONTAINED" not in spawns[0]["env"]
    else:
        assert len(spawns) == 1 and stopped == []
    assert log.read_text().count("job breakaway: refused (contained)") == 1


@pytest.mark.parametrize("lifetime,contained", [(None, False), ("contained", True), ("detached", False)])
def test_discovery_reads_lifetime_without_changing_owner(data_dir, monkeypatch, lifetime, contained):
    lock = InstanceLock(data_dir / "locks")
    info = lock.acquire(4000)
    health = {"pid": info.pid, "data_dir": str(data_dir)}
    if lifetime is not None:
        health["lifetime"] = lifetime
    monkeypatch.setattr(backend_owner, "_fetch_health", lambda port: health)
    monkeypatch.setattr(backend_owner, "_spawn", lambda *a, **kw: pytest.fail("must adopt owner"))
    try:
        found = ensure_backend(data_dir, ["unused"])
        assert found.pid == info.pid and found.contained is contained
    finally:
        lock.release()


def test_ensure_forwards_contained_permission_only_when_requested(data_dir, monkeypatch):
    found = backend_owner.BackendHandle("http://127.0.0.1:4000", 1, 4000, data_dir, "", "", True)
    monkeypatch.setattr(backend_owner, "_discover", lambda root: None)
    def start(root, argv, deadline, env, *, allow_contained=False):
        assert allow_contained is True
        return found
    monkeypatch.setattr(backend_owner, "_start_and_wait", start)
    assert ensure_backend(data_dir, ["unused"], allow_contained=True) is found


@pytest.mark.parametrize("flags", [0, 0x2000])
@pytest.mark.parametrize("allow_contained", [False, True])
def test_forbidding_windows_job_requires_contained_permission(data_dir, monkeypatch, flags, allow_contained):
    import ctypes
    from server.platform import process_tree

    def member(process, job, result):
        result._obj.value = True
        return True
    def query(job, kind, result, size, returned):
        result._obj.LimitFlags = flags
        return True
    kernel = SimpleNamespace(GetCurrentProcess=lambda: 42, IsProcessInJob=member, QueryInformationJobObject=query)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(backend_owner, "sys", SimpleNamespace(platform="win32"))
    job = SimpleNamespace(terminate=lambda: None, close=lambda: None)
    monkeypatch.setattr(process_tree, "confine_to_windows_job", lambda *a, **kw: job)
    captured = []
    def popen(argv, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(pid=123, _handle=42)
    monkeypatch.setattr(subprocess, "Popen", popen)
    environment = {"WG2_BACKEND_CONTAINED": "1", "CUSTOM": "retained"}
    if not allow_contained:
        with pytest.raises(backend_owner.BackendSpawnError, match="does not allow backend breakaway"):
            backend_owner._spawn(["backend"], data_dir, environment)
        assert captured == []
        assert not (data_dir / "logs" / backend_owner.LOG_FILENAME).exists()
        return
    child, log = backend_owner._spawn(["backend"], data_dir, environment, allow_contained=True)
    assert child._wg_start_job is job
    assert len(captured) == 1
    assert not captured[0]["creationflags"] & 0x01000000
    assert captured[0]["env"] == {"CUSTOM": "retained", "WG2_BACKEND_CONTAINED": "1"}
    assert environment["WG2_BACKEND_CONTAINED"] == "1"
    assert log.read_text().count("job breakaway: refused (contained)") == 1


def test_contained_fallback_still_refuses_failed_nested_job(data_dir, monkeypatch):
    from server.platform import process_tree

    def refuse():
        raise process_tree.BreakawayRefused(WINDOWS_BREAKAWAY_REFUSAL)
    monkeypatch.setattr(backend_owner, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refuse)
    monkeypatch.setattr(process_tree, "require_windows_job_detached", lambda *a: pytest.fail("must skip detach check"))
    monkeypatch.setattr(process_tree, "confine_to_windows_job", lambda *a, **kw: None)
    stopped = []
    code = [None]
    def popen(argv, **kwargs):
        assert kwargs["env"]["WG2_BACKEND_CONTAINED"] == "1"
        return SimpleNamespace(
            pid=123, _handle=42, poll=lambda: code[0],
            terminate=lambda: (stopped.append(123), code.__setitem__(0, -1)),
            wait=lambda **kwargs: code[0],
        )
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(BackendSpawnError, match="Could not contain the backend process tree"):
        backend_owner._spawn(["backend"], data_dir, allow_contained=True)
    assert stopped == [123]


@pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows job lifetime")
def test_contained_owner_dies_with_caller_job_and_next_start_adopts_stale_lock(data_dir, tmp_path, fake):
    from server.platform import process_tree

    ready, record = tmp_path / "ready", tmp_path / "contained.json"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "from server.platform.backend_owner import ensure_backend\n"
        "ready,record,data=map(Path,sys.argv[1:4])\nready.write_text('ready')\n"
        "while not ready.with_suffix('.go').exists(): time.sleep(.01)\n"
        "handle=ensure_backend(data,sys.argv[4:],allow_contained=True,timeout=15)\n"
        "record.write_text(json.dumps({'pid':handle.pid,'contained':handle.contained}))\n"
        "time.sleep(60)\n"
    )
    caller = fake.start([sys.executable, "-c", script, str(ready), str(record), str(data_dir), *fake()])
    job = None
    try:
        _wait_until(ready.exists, process=caller)
        job = process_tree.confine_to_windows_job(caller.pid, allow_breakaway=False)
        assert job is not None
        ready.with_suffix(".go").touch()
        _wait_until(record.exists, timeout=20, process=caller)
        first = json.loads(record.read_text())
        assert first["contained"] is True
        assert discover_backend(data_dir).pid == first["pid"]
        assert discover_backend(data_dir).contained is True
        job.close()
        job = None
        caller.wait(timeout=5)
        _wait_until(lambda: not pid_is_running(first["pid"]), timeout=5)
        assert read_lock_info(data_dir / "locks" / LOCK_FILENAME).pid == first["pid"]
        assert discover_backend(data_dir) is None
        second = ensure_backend(data_dir, fake(), allow_contained=True, timeout=15)
        assert second.pid != first["pid"]
        assert discover_backend(data_dir) == second
        assert fake.spawns() == 2
    finally:
        if job is not None:
            job.terminate()
            job.close()


@pytest.mark.skipif(os.name != "posix", reason="Windows SIGTERM already terminates the process")
def test_timeout_kills_child_ignoring_termination(data_dir: Path, fake, monkeypatch) -> None:
    monkeypatch.setattr(backend_owner, "TERMINATE_GRACE", 0.1)
    with pytest.raises(BackendStartTimeout, match="Terminated and waited"):
        ensure_backend(data_dir, fake("hang-ignore-term"), timeout=0.8)
    assert len(fake.pids()) == 1
    assert not pid_is_running(fake.pids()[0])
    assert fake.processes[-1].returncode == -signal.SIGKILL


def test_transient_folder_mismatch_does_not_abort_adoption(data_dir: Path, fake, monkeypatch) -> None:
    require_backend_breakaway()
    owner = fake.start(fake("hold"))
    _wait_until(lambda: read_lock_info(data_dir / "locks" / LOCK_FILENAME))
    calls = []

    def health(port):
        calls.append(port)
        return {
            "pid": owner.pid,
            "data_dir": str(data_dir) if len(calls) > 5 else str(data_dir / "other"),
        }

    monkeypatch.setattr(backend_owner, "_fetch_health", health)
    handle = ensure_backend(data_dir, fake(), timeout=5)
    assert handle.pid == owner.pid
    assert fake.spawns() == 2
    assert fake.owners() == [str(owner.pid)]
    assert fake.processes[-1].wait(timeout=2) == 2


@pytest.mark.parametrize("matching_pid", [True, False])
def test_lock_loser_waits_to_deadline_and_reports_conflict(
    data_dir: Path, fake, monkeypatch, matching_pid: bool
) -> None:
    require_backend_breakaway()
    lock = InstanceLock(data_dir / "locks")
    info = lock.acquire(4000)
    monkeypatch.setattr(
        backend_owner, "_fetch_health",
        lambda port: {"data_dir": str(data_dir / "other"), "pid": info.pid if matching_pid else 1},
    )
    try:
        assert discover_backend(data_dir) is None
        error_type = BackendDataDirMismatch if matching_pid else BackendStartTimeout
        started = time.monotonic()
        with pytest.raises(error_type) as error:
            ensure_backend(data_dir, fake(), timeout=0.6)
        assert time.monotonic() - started >= 0.6
        assert str(info.pid) in str(error.value)
        assert str(info.port) in str(error.value)
        assert fake.spawns() == 1
        assert fake.processes[-1].wait(timeout=2) == 2
    finally:
        lock.release()


def test_validate_does_not_adopt_a_retiring_backend(data_dir):
    from types import SimpleNamespace

    info = SimpleNamespace(pid=123, port=3100)
    health = {"pid": 123, "data_dir": str(data_dir), "retiring": True}
    assert backend_owner._validate(data_dir, info, health) is None


def test_exit_2_respawns_after_a_retiring_owner_disappears(data_dir, monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    calls = []
    closed = []
    waited = []
    owner = SimpleNamespace(pid=123, port=3100)
    replacement = SimpleNamespace(pid=456, port=3101)
    observations = iter([
        (owner, {"pid": 123, "data_dir": str(data_dir), "retiring": True}),
        (None, None),
        (replacement, {"pid": 456, "data_dir": str(data_dir)}),
    ])

    def process(pid, code):
        return SimpleNamespace(
            pid=pid, poll=lambda: code, wait=lambda: waited.append(pid),
            _wg_start_job=SimpleNamespace(close=lambda: closed.append(pid)),
        )

    children = iter([process(321, 2), process(654, 2), process(456, None)])

    def spawn(argv, root, env):
        calls.append((argv, root, env, clock[0]))
        return next(children), data_dir / "start.log"

    monkeypatch.setattr(backend_owner, "_spawn", spawn)
    monkeypatch.setattr(backend_owner, "_observe", lambda _: next(observations))
    monkeypatch.setattr(backend_owner, "pid_is_running", lambda _: True)
    monkeypatch.setattr(backend_owner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(backend_owner.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    handle = backend_owner._start_and_wait(data_dir, ["backend"], 5.0, {"KEEP": "yes"})
    assert handle.pid == 456
    assert [call[3] for call in calls] == [0.0, 0.5, 1.0]
    assert waited == [321, 654] and closed == [321, 654, 456]
    assert all(call[2] == {"KEEP": "yes"} for call in calls)


@pytest.mark.parametrize("metadata", [False, True])
def test_exit_2_retries_when_owner_metadata_is_missing_or_dead(data_dir, monkeypatch, metadata):
    from types import SimpleNamespace

    clock = [0.0]
    calls = []
    info = SimpleNamespace(pid=123, port=3100)
    observations = iter([(info if metadata else None, None), (info, {"pid": 123, "data_dir": str(data_dir)})])

    def spawn(*args):
        calls.append(args)
        return SimpleNamespace(pid=321, poll=lambda: 2, wait=lambda: None), data_dir / "start.log"

    monkeypatch.setattr(backend_owner, "_spawn", spawn)
    monkeypatch.setattr(backend_owner, "_observe", lambda _: next(observations))
    monkeypatch.setattr(backend_owner, "pid_is_running", lambda _: False)
    monkeypatch.setattr(backend_owner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(backend_owner.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert backend_owner._start_and_wait(data_dir, ["backend"], 5.0).pid == 123
    assert len(calls) == 2


@pytest.mark.parametrize("executable, displayed", [
    (r"\\?\C:\Program Files\WG\python.exe", r"C:\Program Files\WG\python.exe"),
    (r"\\?\UNC\server\share\python.exe", r"\\server\share\python.exe"),
])
@pytest.mark.parametrize("message, detail", [
    ("Breakaway refused", "Breakaway refused."),
    ("Breakaway refused.", "Breakaway refused."),
    ("Breakaway refused..", "Breakaway refused."),
    ("", "OSError."),
    ("Breakaway refused?", "Breakaway refused?"),
    ("  Breakaway refused!  ", "Breakaway refused!"),
])
def test_spawn_refusal_uses_display_path_and_one_period(data_dir, monkeypatch, executable, displayed, message, detail):
    from types import SimpleNamespace
    from server.platform import paths, process_tree

    def refused():
        raise OSError(message)

    monkeypatch.setattr(backend_owner.sys, "platform", "win32")
    monkeypatch.setattr(paths, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refused)
    with pytest.raises(BackendSpawnError) as error:
        backend_owner._spawn([executable], data_dir)
    assert str(error.value) == (
        f'Could not start the backend ("{displayed}"): {detail} '
        "Check the installation, or start Waveguide Generator manually."
    )
    assert ".." not in str(error.value) and "\\\\?\\" not in str(error.value)


@pytest.mark.parametrize("format_failure", ["path", "exception"])
def test_spawn_refusal_formatting_preserves_original_exception(data_dir, monkeypatch, format_failure):
    from server.platform import process_tree

    class Refusal(OSError):
        def __str__(self):
            if format_failure == "exception":
                raise ValueError("bad exception formatting")
            return super().__str__()

    original = Refusal("Breakaway refused")

    def refused():
        raise original

    def bad_path(_path):
        raise ValueError("bad path formatting")

    monkeypatch.setattr(backend_owner.sys, "platform", "win32")
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refused)
    if format_failure == "path":
        monkeypatch.setattr(backend_owner, "display_path", bad_path)
    executable = data_dir / "python.exe"
    with pytest.raises(BackendSpawnError) as error:
        backend_owner._spawn([executable], data_dir)
    assert error.value.__cause__ is original
    assert f'("{executable}"): {original!r}.' in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_respawn_cleanup_targets_only_the_current_child(data_dir, monkeypatch, failure):
    from types import SimpleNamespace

    clock = [0.0]
    closed, reaped, terminated = [], [], []

    def child(pid, code):
        return SimpleNamespace(
            pid=pid, poll=lambda: code, wait=lambda: reaped.append(pid),
            _wg_start_job=SimpleNamespace(close=lambda: closed.append(pid)),
        )

    children = iter([child(100, 2), child(200, None)])
    monkeypatch.setattr(backend_owner, "_spawn", lambda *args: (next(children), data_dir / "start.log"))
    monkeypatch.setattr(backend_owner, "_discover", lambda _: None)
    observations = [0]

    def observe(_root):
        observations[0] += 1
        if observations[0] > 1:
            if failure == "cancel":
                raise KeyboardInterrupt()
            clock[0] = 5.0
        return None, None

    monkeypatch.setattr(backend_owner, "_observe", observe)
    monkeypatch.setattr(backend_owner, "_terminate_child", lambda process: (terminated.append(process.pid), "")[1])
    monkeypatch.setattr(backend_owner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(backend_owner.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else BackendStartTimeout):
        backend_owner._start_and_wait(data_dir, ["backend"], 5.0)
    assert reaped == [100] and closed == [100, 200]
    assert terminated and set(terminated) == {200}
