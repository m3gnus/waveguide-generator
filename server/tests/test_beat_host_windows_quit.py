"""Check what a packaged Windows Quit does to BEAT's persistent hosts.

The packaged launcher runs the server in a kill-on-close Job Object that it
closes at the end of every stop (``launchers/statusapp/controller.py``). The
job allows breakaway (``JOB_OBJECT_LIMIT_BREAKAWAY_OK``) but not silent
breakaway, so only a child that asks for ``CREATE_BREAKAWAY_FROM_JOB`` leaves
it; everything else the server starts dies when the job closes.

* The **official** host (``server/solver/beat_runtime/spawn.py``) asks for
  breakaway, so a warm one survives Quit and the next launch adopts it through
  the registry. If the job the server is directly in forbids breakaway (CI,
  without the status window), ``CreateProcess`` refuses with
  ``ERROR_ACCESS_DENIED``; the spawn then retries once inside that job,
  records ``job_breakaway: refused``, and the host dies with the job, its
  stale record pruned at the next start. A host that broke away still ends:
  its suspend-aware idle expiry, authenticated cleanup, or the installer's
  retire request while it is idle. It never runs from the app layer, which an
  update must be able to move aside.
* The legacy **HBB** host is started with
  ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` and no breakaway, so it stays
  in the job and dies with it. Clean server exit stops it first through
  ``shutdown_workers()``; the job is the backstop if that fails or the server
  crashes.

``docs/reference/SHUTDOWN-AND-RECOVERY.md`` states both. The Windows tests
drive the real ``_windows_job_for``; the tripwires run everywhere, so a change
to either side's flags fails here and the record gets revisited.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest


#: What ``HostedBeatWorker._launch`` passes on Windows at the pinned commit.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
BEAT_HOST_CREATION_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

#: Every process this starts exits by itself after this long, whatever happens.
SELF_EXIT_SECONDS = 60.0

_STILL_ACTIVE = 259
_ROOT = Path(__file__).resolve().parents[2]


def test_clean_server_exit_requests_shutdown_of_its_persistent_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the pinned package's real registry and shutdown dispatch."""

    from hornlab_beat_bem import worker
    from hornlab_beat_bem.worker_client import HostedBeatWorker
    from server.app import create_app

    called: list[object] = []
    host = HostedBeatWorker.__new__(HostedBeatWorker)
    monkeypatch.setattr(host, "refuse_submissions", lambda: called.append("refuse"))
    monkeypatch.setattr(
        host, "_say_and_close",
        lambda message, terminal: called.append((message, terminal)),
    )
    monkeypatch.setattr(host, "detach", lambda: called.append("detach"))
    owned_workers = {"test-host": host}
    monkeypatch.setattr(worker, "_WORKERS", owned_workers)
    application = create_app(data_dir=tmp_path)
    hook = next(
        handler for handler in application.router.on_shutdown
        if handler.__name__ == "shutdown_beat_worker"
    )

    asyncio.run(hook())

    assert called == ["refuse", ({"op": "shutdown"}, "shutdown_ok")]
    assert owned_workers == {}


def test_the_pinned_beat_host_is_started_without_breaking_away_from_a_job() -> None:
    """The HBB record below is only true while the package starts its host this way."""

    worker_client = pytest.importorskip("hornlab_beat_bem.worker_client")
    source = inspect.getsource(worker_client.HostedBeatWorker._launch)

    assert "0x00000008 | 0x00000200" in source, (
        "hornlab_beat_bem changed how it starts its persistent host on Windows; "
        "re-record what a packaged Quit does to it"
    )
    assert "BREAKAWAY" not in source.upper()
    assert "0x01000000" not in source


def test_the_launcher_job_allows_explicit_breakaway_only() -> None:
    """Tripwire for the status window's job and the official host's spawn flags."""

    from launchers.statusapp import controller
    from server.solver.beat_runtime import spawn

    assert controller.WINDOWS_JOB_LIMIT_FLAGS == (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    ), "the launcher's job flags changed; re-record what a packaged Quit does"
    assert not controller.WINDOWS_JOB_LIMIT_FLAGS & JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
    job_source = inspect.getsource(controller._windows_job_for)
    assert "LimitFlags = WINDOWS_JOB_LIMIT_FLAGS" in job_source

    assert spawn.detached_options(windows=True) == {
        "creationflags": BEAT_HOST_CREATION_FLAGS | CREATE_BREAKAWAY_FROM_JOB
    }
    assert spawn.detached_options(windows=True, breakaway=False) == {
        "creationflags": BEAT_HOST_CREATION_FLAGS
    }
    assert spawn.detached_options(windows=False) == {"start_new_session": True}


def _alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _terminate(pid: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


def _wait_dead(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _alive(pid)


#: Stands in for the server: starts a "host" the way the package does, reports
#: its pid, then waits for its stdin to close.
_SERVER = r"""
import subprocess, sys, time
flags = int(sys.argv[1])
host = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(%s)" % sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=flags,
    close_fds=True,
)
print(host.pid, flush=True)
sys.stdin.read()
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_packaged_quit_on_windows_terminates_the_hbb_persistent_host() -> None:
    from launchers.statusapp.controller import _windows_job_for

    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(BEAT_HOST_CREATION_FLAGS), str(SELF_EXIT_SECONDS)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    host_pid: int | None = None
    job = None
    try:
        # The launcher assigns the job right after starting the server, before
        # it can start anything; the stand-in waits for its stdin to be read,
        # so assigning first reproduces that order.
        job = _windows_job_for(server)  # type: ignore[arg-type]
        assert job is not None, "the launcher's Job Object could not be created here"
        assert server.stdin is not None and server.stdout is not None
        server.stdin.write("")
        host_pid = int(server.stdout.readline().strip())
        assert _alive(host_pid)

        # A Quit: the server exits on its own, then the launcher closes the job
        # (``StatusController.stop`` does both, in that order).
        server.stdin.close()
        server.wait(timeout=30)
        assert _alive(host_pid), "the host died with the server itself, before the job closed"
        job.close()  # type: ignore[attr-defined]
        job = None

        assert _wait_dead(host_pid), (
            "the HBB host survived the launcher's job close; the record in "
            "docs/reference/SHUTDOWN-AND-RECOVERY.md is wrong"
        )
    finally:
        if job is not None:
            job.close()  # type: ignore[attr-defined]
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        if host_pid is not None and _alive(host_pid):
            _terminate(host_pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_without_the_launchers_job_the_detached_host_survives() -> None:
    """The control: the same host, with no launcher job closed, outlives its parent.

    Without it a pass above could be the environment tearing trees down, not
    the launcher's job. The stand-in stays in whatever job the test runs in --
    a windows-latest runner's job refuses ``CREATE_BREAKAWAY_FROM_JOB`` with
    ERROR_ACCESS_DENIED -- and that job stays open for the whole test, so the
    only difference from the test above is the launcher's job and its close.
    """

    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(BEAT_HOST_CREATION_FLAGS), str(SELF_EXIT_SECONDS)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    host_pid: int | None = None
    try:
        assert server.stdin is not None and server.stdout is not None
        host_pid = int(server.stdout.readline().strip())
        server.stdin.close()
        server.wait(timeout=30)
        time.sleep(1.0)
        if not _alive(host_pid):
            pytest.skip("this runner tears process trees down by itself; nothing to compare")
        assert _alive(host_pid)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        if host_pid is not None and _alive(host_pid):
            _terminate(host_pid)


# -- The official host -------------------------------------------------------

#: Stands in for the server on the official route: starts the host through the
#: real ``spawn.start_host`` (with the tests' engine-free host), a plain child
#: the way the mesher starts gmsh, and a child the way CAD Link starts its
#: isolated child (no breakaway, its own kill-on-close job nested in the
#: server's). Reports the pids, then waits for its stdin to close.
_OFFICIAL_SERVER = r"""
import json, subprocess, sys
from pathlib import Path
from server.cadlink import isolation
from server.platform.job_start import windows_job_start
from server.solver.beat_runtime import spawn
spawn.HOST_MODULE = "server.tests.beat_runtime.fake_host_main"
key = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
record = spawn.start_host(key, Path(sys.argv[2]), timeout=60.0)
sleeper = [sys.executable, "-c", "import time; time.sleep(%s)" % sys.argv[3]]
quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
gmsh = subprocess.Popen(sleeper, close_fds=True, **quiet)
with windows_job_start(lambda _pid, handle: isolation._assign_windows_job(handle, isolation.INSPECT_BUDGET),
                       required=True, subject="the CAD-style child") as started:
    cad = isolation.start_child_process(
        sleeper, {"creationflags": isolation._windows_creation_flags(breakaway=False), "close_fds": True, **quiet}, stage="test")
cad_job = started.job
print(json.dumps({"host_pid": record.pid, "gmsh_pid": gmsh.pid, "cad_pid": cad.pid}), flush=True)
sys.stdin.read()
"""


def _breakaway_permitted_here() -> bool:
    """Whether the job this test runs in, if any, lets a child break away."""

    try:
        probe = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            creationflags=BEAT_HOST_CREATION_FLAGS | CREATE_BREAKAWAY_FROM_JOB,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 5:
            return False
        raise
    probe.wait(timeout=30)
    return True


@pytest.fixture
def official(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retire_event: str) -> Any:
    """One official host slot; tears down whatever host is left in it."""

    from server.solver.beat_runtime import cleanup, registry as r

    monkeypatch.setenv("WG2_BEAT_WORKER_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    fixture = tmp_path / "fixture"
    key = r.host_key({"backend": "cpu", "julia_executable": str(fixture / "julia"),
                      "julia_identity": "binary-content", "solver_script": str(fixture / "solver.jl"),
                      "julia_project": str(fixture / "project"), "julia_sysimage": str(fixture / "sysimage"),
                      "julia_threads": 2, "engine_fingerprint": "engine-content",
                      "runtime_fingerprint": "runtime-content",
                      "environment": {"TEST_EVENTS": str(tmp_path / "events"), "JULIA_NUM_THREADS": "2"}})
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps(key), encoding="utf-8")
    directory = tmp_path / "registry"
    pids: list[int] = []
    yield key, key_file, directory, pids
    record = r.read_record(r.key_id(key), directory) if directory.exists() else None
    if record is not None:
        try:
            cleanup.cleanup_host(record, key, directory, timeout=3)
        except Exception:  # noqa: BLE001 - the pid sweep below still runs
            pass
    for pid in pids:
        if _alive(pid):
            _terminate(pid)


def _start_server_in_launcher_job(key_file: Path, directory: Path) -> tuple[subprocess.Popen, object]:
    """Start the stand-in server suspended and owned, as ``StatusController.start`` does."""

    from launchers.statusapp.controller import _windows_job_for
    from server.platform.job_start import windows_job_start

    # The base interpreter, not a venv's python.exe: that redirector runs the
    # real interpreter in a job of its own with SILENT_BREAKAWAY_OK, which
    # would grant the host's breakaway whatever the launcher's job allows.
    interpreter = getattr(sys, "_base_executable", sys.executable)
    import site

    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
        filter(None, (str(_ROOT), *site.getsitepackages(), os.environ.get("PYTHONPATH")))
    ))
    with windows_job_start(
        lambda _pid, handle: _windows_job_for(handle), required=True, subject="the stand-in server"
    ) as started:
        server = subprocess.Popen(
            [interpreter, "-c", _OFFICIAL_SERVER, str(key_file), str(directory), str(SELF_EXIT_SECONDS)],
            cwd=str(_ROOT), env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
    assert started.fired and started.job is not None
    return server, started.job


def _quit(server: subprocess.Popen, job: Any, *, crash: bool) -> None:
    """A status-window stop: the server exits (or dies), then the job closes."""

    assert server.stdin is not None
    if crash:
        server.kill()
    else:
        server.stdin.close()
    server.wait(timeout=30)
    job.close()


def _host_log(key: dict, directory: Path) -> str:
    from server.solver.beat_runtime import registry as r

    return r.log_path(r.key_id(key), r.private_directory(directory)).read_text(
        encoding="utf-8", errors="replace"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
@pytest.mark.parametrize("crash", [False, True], ids=["quit", "crash"])
def test_a_granted_official_host_survives_quit_and_the_next_start_adopts_it(
    official: Any, monkeypatch: pytest.MonkeyPatch, crash: bool
) -> None:
    from server.solver.beat_runtime import cleanup, registry as r, spawn

    if not _breakaway_permitted_here():
        pytest.skip("this runner's own job forbids breakaway; the refused test covers it")
    key, key_file, directory, pids = official
    server, job = _start_server_in_launcher_job(key_file, directory)
    try:
        assert server.stdout is not None
        started = json.loads(server.stdout.readline())
        pids.extend(started.values())
        host_pid = started["host_pid"]
        assert "job breakaway: granted" in _host_log(key, directory)

        _quit(server, job, crash=crash)

        assert _wait_dead(started["gmsh_pid"]), "a plain server child survived the job close"
        assert _wait_dead(started["cad_pid"]), "the CAD-style child survived the server"
        time.sleep(0.5)
        assert _alive(host_pid), "the official host did not survive the launcher's job close"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        job.close()  # type: ignore[attr-defined]

    # The next start adopts the detached host through the registry and its
    # authenticated probe; neither this start nor the one after spawns.
    def no_spawn(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a relaunch spawned a second host instead of adopting")

    monkeypatch.setattr(spawn, "_launch", no_spawn)
    adopted = spawn.start_host(key, directory)
    assert adopted.pid == host_pid
    assert spawn.start_host(key, directory) == adopted

    # Authenticated cleanup still finds and stops a host that left the job.
    assert cleanup.cleanup_host(adopted, key, directory, timeout=5)
    assert _wait_dead(host_pid)
    assert r.read_record(r.key_id(key), directory) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_refused_breakaway_keeps_the_host_in_the_job_and_the_next_start_recovers(
    official: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from launchers.statusapp import controller
    from server.solver.beat_runtime import registry as r, spawn

    # A server directly in a job without BREAKAWAY_OK, as on a CI runner.
    monkeypatch.setattr(controller, "WINDOWS_JOB_LIMIT_FLAGS", JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    key, key_file, directory, pids = official
    server, job = _start_server_in_launcher_job(key_file, directory)
    try:
        assert server.stdout is not None
        started = json.loads(server.stdout.readline())
        pids.extend(started.values())
        host_pid = started["host_pid"]
        assert "job breakaway: refused" in _host_log(key, directory)

        _quit(server, job, crash=False)

        assert _wait_dead(started["gmsh_pid"])
        assert _wait_dead(started["cad_pid"])
        assert _wait_dead(host_pid), "a host whose breakaway was refused survived the job close"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        job.close()  # type: ignore[attr-defined]

    # Its record is stale now; the next start prunes it and spawns afresh.
    stale = r.read_record(r.key_id(key), directory)
    assert stale is not None and stale.pid == host_pid
    monkeypatch.setattr(spawn, "HOST_MODULE", "server.tests.beat_runtime.fake_host_main")
    fresh = spawn.start_host(key, directory, timeout=60.0)
    pids.append(fresh.pid)
    assert fresh.pid != host_pid
    assert r.read_record(r.key_id(key), directory) == fresh


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_host_that_broke_away_still_expires_when_idle(
    official: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 1800 s suspend-aware idle expiry ends a detached host by itself.

    The tests' host jumps its suspend-aware clock a day forward once a marker
    file exists (``fake_host_main``), as a resume from sleep would.
    """

    from server.solver.beat_runtime import registry as r

    if not _breakaway_permitted_here():
        pytest.skip("this runner's own job forbids breakaway")
    marker = tmp_path / "resumed"
    monkeypatch.setenv("BEAT_FAKE_HOST_SUSPEND_FILE", str(marker))
    key, key_file, directory, pids = official
    server, job = _start_server_in_launcher_job(key_file, directory)
    try:
        assert server.stdout is not None
        started = json.loads(server.stdout.readline())
        pids.extend(started.values())
        host_pid = started["host_pid"]
        _quit(server, job, crash=False)
        time.sleep(1.0)
        assert _alive(host_pid), "the host did not leave the launcher's job"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        job.close()  # type: ignore[attr-defined]

    marker.write_text("", encoding="utf-8")
    assert _wait_dead(host_pid, 20.0), "a detached idle host outlived its suspend-aware expiry"
    assert "idle exit" in _host_log(key, directory)
    assert r.read_record(r.key_id(key), directory) is None


def _move_aside(path: Path) -> int:
    """``MoveFileExW`` the way an update's native commit moves ``app`` aside; 0 or the error."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    aside = path.with_name(path.name + ".previous")
    if not kernel32.MoveFileExW(str(path), str(aside), 0):
        return ctypes.get_last_error()
    assert kernel32.MoveFileExW(str(aside), str(path), 0)
    return 0


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_host_that_survived_quit_does_not_pin_the_app_layer(
    official: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An update or reinstall right after Quit must still be able to move ``app`` aside.

    A Windows process's current directory cannot be renamed, and a warm host
    now outlives Quit, so neither it, its native stub nor its Julia worker may
    run from the app layer (``host.working_directory``).
    """

    import shutil

    if not _breakaway_permitted_here():
        pytest.skip("this runner's own job forbids breakaway")
    app = tmp_path / "bundle" / "app"
    for relative in ("server/__init__.py", "server/solver/__init__.py", "server/platform/__init__.py",
                     "server/platform/paths.py", "server/tests/beat_runtime/fake_host_worker.py",
                     "server/tests/beat_runtime/fake_host_main.py"):
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_ROOT / relative, target)
    shutil.copytree(_ROOT / "server/solver/beat_runtime", app / "server/solver/beat_runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("WG2_APP_ROOT", str(app))
    key, key_file, directory, pids = official
    server, job = _start_server_in_launcher_job(key_file, directory)
    try:
        assert server.stdout is not None
        started = json.loads(server.stdout.readline())
        pids.extend(started.values())
        host_pid = started["host_pid"]
        _quit(server, job, crash=False)
        time.sleep(0.5)
        assert _alive(host_pid), "the host did not survive the launcher's job close"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=30)
        job.close()  # type: ignore[attr-defined]

    assert _move_aside(app) == 0, "a warm host pins the app layer against an update"
    assert _alive(host_pid)


def _signal_retire(event_name: str, *, reset: bool = False) -> bool:
    """Open and set, or reset, only a private test event."""
    from server.tests.beat_runtime.fake_host_main import _test_retire_event_name

    event_name = _test_retire_event_name(event_name)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    handle = kernel32.OpenEventW(0x0002, False, event_name)  # EVENT_MODIFY_STATE
    if not handle:
        return False
    try:
        (kernel32.ResetEvent if reset else kernel32.SetEvent)(wintypes.HANDLE(handle))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    return True


@pytest.mark.parametrize("event_name", [
    "WaveguideGeneratorRetireIdleBeatHosts", "", None,
    "Global\\WaveguideGeneratorRetireIdleBeatHosts", "Local\\WaveguideGeneratorRetireIdleBeatHosts",
    "WaveguideGeneratorTestRetireIdleBeatHosts-", "WaveguideGeneratorTestRetireIdleBeatHosts-unsafe\\name",
])
@pytest.mark.parametrize("reset", [False, True])
def test_signal_retire_refuses_unsafe_names_before_loading_windows_api(monkeypatch, event_name, reset):
    import ctypes

    def forbidden(*args, **kwargs):
        pytest.fail("An unsafe retire name reached the Windows API")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    with pytest.raises(ValueError, match="private test retire event"):
        _signal_retire(event_name, reset=reset)


def test_the_installer_and_the_host_name_the_same_retire_event() -> None:
    from server.solver.beat_runtime.retire import RETIRE_IDLE_EVENT

    script = (_ROOT / "installers" / "windows" / "bundle-setup.iss").read_text(encoding="utf-8")
    assert RETIRE_IDLE_EVENT == "WaveguideGeneratorRetireIdleBeatHosts"
    assert f"RetireIdleBeatHostsEvent = '{RETIRE_IDLE_EVENT}';" in script
    assert "OpenEventW(EVENT_MODIFY_STATE, 0, RetireIdleBeatHostsEvent)" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows named events")
def test_the_installers_retire_request_ends_idle_hosts_but_not_one_in_use(
    official: Any, monkeypatch: pytest.MonkeyPatch, retire_event: str
) -> None:
    """A warm host holds the installer's Running mutex through its native stub.

    The installer sets the retire event before it checks that mutex: an idle
    host exits at once, but a host whose app is still connected stays, so the
    installer still refuses a running app.
    """

    from server.solver.beat_runtime import client, registry as r, spawn

    monkeypatch.setattr(spawn, "HOST_MODULE", "server.tests.beat_runtime.fake_host_main")
    key, _key_file, directory, pids = official
    record = spawn.start_host(key, directory, timeout=60.0)
    pids.append(record.pid)
    connection = client.connect_client(record, r.private_directory(directory), timeout=10.0)
    try:
        assert _signal_retire(retire_event), "the host did not create the test retire event"
        time.sleep(1.0)
        assert _alive(record.pid), "a host with a connected client retired"
    finally:
        connection.close()
    try:
        assert _wait_dead(record.pid, 10.0), "an idle host ignored the installer's retire request"
        assert "idle exit: the installer asked idle hosts to retire" in _host_log(key, directory)
        assert r.read_record(r.key_id(key), directory) is None
    finally:
        _signal_retire(retire_event, reset=True)


def test_the_record_is_documented() -> None:
    doc = (_ROOT / "docs" / "reference" / "SHUTDOWN-AND-RECOVERY.md").read_text(encoding="utf-8")

    assert "persistent host" in doc
    assert "CREATE_BREAKAWAY_FROM_JOB" in doc
    assert "BREAKAWAY_OK" in doc
    assert "job_breakaway" in doc
