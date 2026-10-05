"""A child is in its job before it runs, so a stub's interpreter cannot escape.

In the Windows bundle ``sys.executable`` is the native entry stub, which starts
the real interpreter as its own child (``launchers/windows/launcher.c``
``start()``). A job assigned after the start can miss that grandchild, and
killing the stub then leaves it running. ``server/platform/job_start.py``
creates the child suspended and assigns it first.

Every test here builds a real two-level tree: a stub that starts a grandchild
interpreter and waits for it, as the native entry does. Both levels run
``sys._base_executable``, never a venv's ``python.exe``: that shim is itself a
launcher that puts its child in a kill-on-close job, so a kill of it would take
the grandchild down and a test would pass whatever the code under test did.

The controls run first and must hold: a bare kill of the stub, and a job
assigned once the grandchild exists, both leave the grandchild alive. Without
them a pass here could come from the environment rather than from the fix.

The site tests make the old race deterministic: the job assignment they wrap
first waits until the grandchild exists (or a few seconds pass). Assigned after
the start, as before, that wait ends with the grandchild already outside the
job; assigned while the stub is suspended, the grandchild cannot appear at all.

Liveness is read with OpenProcess + GetExitCodeProcess. ``os.kill(pid, 0)``
terminates on Windows. Every grandchild also exits by itself after a minute.
"""

from __future__ import annotations

import contextlib
import ctypes
import multiprocessing
import multiprocessing.spawn
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job objects")

BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)
GRANDCHILD_ENV = "WG_TEST_GRANDCHILD_PIDFILE"
SELF_EXIT_SECONDS = 60
#: How long a wrapped assignment waits for the grandchild to appear.
RACE_WINDOW_SECONDS = 3.0
WAIT_SECONDS = 30.0

STUB = r'''"""A launcher stub: start the real interpreter as a child and wait for it.

Like the bundle's native entry it hands the child its own standard handles and
uses no job object, so killing it alone leaves the child running.
"""
import os
import subprocess
import sys


def _handle(fd):
    try:
        os.fstat(fd)
    except OSError:
        return None
    return fd


child = subprocess.Popen(
    [sys._base_executable, *sys.argv[1:]],
    stdin=_handle(0),
    stdout=_handle(1),
    stderr=_handle(2),
)
sys.exit(child.wait())
'''

GRANDCHILD = (
    "import os, sys, time\n"
    "path = sys.argv[1]\n"
    "with open(path + '.tmp', 'w') as f: f.write(str(os.getpid()))\n"
    "os.replace(path + '.tmp', path)\n"
    f"time.sleep({SELF_EXIT_SECONDS})\n"
)


# -- Win32 ------------------------------------------------------------------

_STILL_ACTIVE = 259
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _kernel32 is not None:
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _kernel32.IsProcessInJob.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _alive(pid: int) -> bool:
    handle = _kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while _alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _terminate(pid: int) -> None:
    """Cleanup of a grandchild this test started; never anything else."""

    handle = _kernel32.OpenProcess(0x0001 | 0x1000, False, int(pid))
    if handle:
        try:
            _kernel32.TerminateProcess(handle, 1)
        finally:
            _kernel32.CloseHandle(handle)


def _job_handle(job: Any) -> int:
    value = getattr(job, "_handle", None)
    if value is None:
        value = getattr(job, "handle")
    return int(value)


def _in_job(pid: int, job: Any) -> bool:
    handle = _kernel32.OpenProcess(0x1000, False, int(pid))
    assert handle, f"cannot open {pid}"
    try:
        result = wintypes.BOOL()
        assert _kernel32.IsProcessInJob(handle, _job_handle(job), ctypes.byref(result))
        return bool(result.value)
    finally:
        _kernel32.CloseHandle(handle)


def _read_pid(path: Path, timeout: float = WAIT_SECONDS) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError, ValueError):
            return int(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError(f"the grandchild never wrote {path}")


@pytest.fixture
def tree(tmp_path: Path):
    """The stub script, a pid file, and cleanup of whatever grandchild wrote it."""

    stub = tmp_path / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    pidfile = tmp_path / "grandchild.pid"
    yield stub, pidfile
    with contextlib.suppress(OSError, ValueError):
        _terminate(int(pidfile.read_text(encoding="utf-8")))


def _stub_command(stub: Path, pidfile: Path) -> list[str]:
    return [BASE_PYTHON, str(stub), "-c", GRANDCHILD, str(pidfile)]


def _late(assign: Any, pidfile: Path, seen: list[bool]) -> Any:
    """``assign``, but only once the grandchild exists or the window passes."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        deadline = time.monotonic() + RACE_WINDOW_SECONDS
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        seen.append(pidfile.exists())
        return assign(*args, **kwargs)

    return wrapper


# -- the mechanism, through subprocess ----------------------------------------


def test_control_a_bare_kill_of_the_stub_leaves_its_interpreter_running(tree) -> None:
    stub, pidfile = tree
    process = subprocess.Popen(_stub_command(stub, pidfile))
    grandchild = _read_pid(pidfile)
    process.kill()
    process.wait(timeout=WAIT_SECONDS)
    time.sleep(0.5)
    assert _alive(grandchild), "the environment tore the tree down; nothing here can be measured"


def test_control_a_job_assigned_after_the_stub_started_misses_its_interpreter(tree) -> None:
    from server.platform.process_tree import confine_to_windows_job

    stub, pidfile = tree
    process = subprocess.Popen(_stub_command(stub, pidfile))
    grandchild = _read_pid(pidfile)
    job = confine_to_windows_job(process.pid, subject="the test stub")
    assert job is not None
    try:
        assert not _in_job(grandchild, job)
        job.terminate()
        process.wait(timeout=WAIT_SECONDS)
        time.sleep(0.5)
        assert _alive(grandchild), "the old ordering contained the grandchild after all"
    finally:
        job.close()


def test_a_suspended_start_puts_the_interpreter_in_the_job_and_the_job_kills_it(tree) -> None:
    from server.platform.job_start import windows_job_start
    from server.platform.process_tree import confine_to_windows_job

    stub, pidfile = tree
    with windows_job_start(
        lambda pid, _handle: confine_to_windows_job(pid, subject="the test stub"),
        required=True,
        subject="the test stub",
    ) as started:
        process = subprocess.Popen(_stub_command(stub, pidfile))
    assert started.fired and started.pid == process.pid
    job = started.job
    try:
        grandchild = _read_pid(pidfile)
        assert _in_job(grandchild, job)
        job.terminate()
        assert _wait_dead(grandchild)
        process.wait(timeout=WAIT_SECONDS)
    finally:
        job.close()


def test_closing_the_last_job_handle_kills_the_interpreter_without_any_cleanup(tree) -> None:
    """What a force-killed parent leaves behind: only kill-on-close."""

    from server.platform.job_start import windows_job_start
    from server.platform.process_tree import confine_to_windows_job

    stub, pidfile = tree
    with windows_job_start(
        lambda pid, _handle: confine_to_windows_job(pid, subject="the test stub"),
        required=True,
        subject="the test stub",
    ) as started:
        process = subprocess.Popen(_stub_command(stub, pidfile))
    grandchild = _read_pid(pidfile)
    started.job.close()
    assert _wait_dead(grandchild)
    process.wait(timeout=WAIT_SECONDS)


def test_a_required_job_that_cannot_be_assigned_stops_the_child_before_it_runs(tree) -> None:
    from server.platform.job_start import ContainedStartError, windows_job_start

    stub, pidfile = tree
    seen: list[int] = []

    def refuse(pid: int, _handle: int) -> None:
        seen.append(pid)
        raise OSError(5, "refused")

    with pytest.raises(ContainedStartError, match="could not confine the test stub"):
        with windows_job_start(refuse, required=True, subject="the test stub"):
            subprocess.Popen(_stub_command(stub, pidfile))
    assert seen and not _alive(seen[0])
    time.sleep(0.5)
    assert not pidfile.exists(), "the refused child ran"


def test_a_best_effort_job_that_cannot_be_assigned_still_starts_the_child(tree) -> None:
    from server.platform.job_start import windows_job_start

    stub, pidfile = tree
    with windows_job_start(lambda _pid, _handle: None, required=False, subject="the test stub") as started:
        process = subprocess.Popen(_stub_command(stub, pidfile))
    try:
        assert started.fired and started.job is None
        _read_pid(pidfile)
    finally:
        process.kill()
        process.wait(timeout=WAIT_SECONDS)


@pytest.mark.parametrize("required", [True, False])
def test_a_child_that_cannot_be_resumed_never_runs(tree, monkeypatch, required: bool) -> None:
    from server.platform import job_start
    from server.platform.process_tree import confine_to_windows_job

    stub, pidfile = tree
    monkeypatch.setattr(job_start, "_resume", lambda _thread: False)
    assigned: list[int] = []

    def assign(pid: int, _handle: int) -> Any:
        assigned.append(pid)
        return confine_to_windows_job(pid, subject="the test stub")

    if required:
        with pytest.raises(job_start.ContainedStartError, match="could not resume"):
            with job_start.windows_job_start(assign, required=True, subject="the test stub"):
                subprocess.Popen(_stub_command(stub, pidfile))
        assert len(assigned) == 1 and not _alive(assigned[0])
        time.sleep(0.5)
        assert not pidfile.exists()
        return
    with job_start.windows_job_start(assign, required=False, subject="the test stub") as started:
        process = subprocess.Popen(_stub_command(stub, pidfile))
    try:
        # The suspended attempt was discarded; the plain restart is confined afterwards.
        assert len(assigned) == 2 and not _alive(assigned[0])
        assert process.pid == assigned[1] == started.pid
        assert started.job is not None
        _read_pid(pidfile)
    finally:
        if started.job is not None:
            started.job.terminate()
            started.job.close()
        process.wait(timeout=WAIT_SECONDS)


def test_only_the_armed_thread_and_only_its_first_start_are_captured(tree) -> None:
    from server.platform.job_start import windows_job_start

    calls: list[int] = []
    other: list[subprocess.Popen[bytes]] = []
    with windows_job_start(
        lambda pid, _handle: calls.append(pid), required=False, subject="the test stub"
    ) as started:
        thread = threading.Thread(
            target=lambda: other.append(subprocess.Popen([BASE_PYTHON, "-c", "pass"]))
        )
        thread.start()
        thread.join()
        first = subprocess.Popen([BASE_PYTHON, "-c", "pass"])
        second = subprocess.Popen([BASE_PYTHON, "-c", "pass"])
    for process in (other[0], first, second):
        assert process.wait(timeout=WAIT_SECONDS) == 0
    assert calls == [first.pid] and started.pid == first.pid


def test_a_start_that_fails_leaves_the_thread_armed_for_the_retry(tree) -> None:
    from server.platform.job_start import windows_job_start

    calls: list[int] = []
    with windows_job_start(
        lambda pid, _handle: calls.append(pid), required=False, subject="the test stub"
    ) as started:
        with pytest.raises(OSError):
            subprocess.Popen([str(Path(BASE_PYTHON).with_name("no-such-python.exe"))])
        retry = subprocess.Popen([BASE_PYTHON, "-c", "pass"])
    assert retry.wait(timeout=WAIT_SECONDS) == 0
    assert calls == [retry.pid] and started.fired


def test_the_interception_is_inert_outside_an_armed_block() -> None:
    import _winapi

    from server.platform.job_start import windows_job_start

    with windows_job_start(lambda _pid, _handle: None, required=False, subject="nothing"):
        pass
    assert getattr(_winapi.CreateProcess, "_wg_job_start", False)
    assert subprocess.Popen([BASE_PYTHON, "-c", "pass"]).wait(timeout=WAIT_SECONDS) == 0


# -- the mechanism, through a multiprocessing spawn ---------------------------


def report_pid(path: str) -> None:
    """A spawn target; module level so the grandchild can import it."""

    target = Path(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(str(os.getpid()), encoding="utf-8")
    os.replace(tmp, target)
    time.sleep(SELF_EXIT_SECONDS)


@pytest.fixture
def spawn_through_stub(tree, monkeypatch):
    """Make the spawn context start the stub, which starts the interpreter.

    The stdlib builds the command, then may swap its first word for the base
    interpreter; the stub is inserted after it, so the spawned image is a base
    interpreter running the stub, and the stub's child is the spawn child.
    """

    stub, pidfile = tree
    original = multiprocessing.spawn.get_command_line

    def through_stub(**kwargs: Any) -> list[str]:
        command = original(**kwargs)
        return [command[0], str(stub), *command[1:]]

    monkeypatch.setattr(multiprocessing.spawn, "get_command_line", through_stub)
    monkeypatch.setenv(GRANDCHILD_ENV, str(pidfile))
    return multiprocessing.get_context("spawn"), pidfile


def test_control_a_spawned_stub_killed_alone_leaves_its_interpreter(spawn_through_stub) -> None:
    ctx, pidfile = spawn_through_stub
    process = ctx.Process(target=report_pid, args=(str(pidfile),), daemon=True)
    process.start()
    grandchild = _read_pid(pidfile)
    assert grandchild != process.pid, "the spawn did not go through the stub"
    process.kill()
    process.join(WAIT_SECONDS)
    time.sleep(0.5)
    assert _alive(grandchild), "the environment tore the tree down; nothing here can be measured"


def test_a_spawned_stubs_interpreter_starts_inside_the_job(spawn_through_stub) -> None:
    from server.platform.job_start import start_in_windows_job
    from server.platform.process_tree import confine_to_windows_job

    ctx, pidfile = spawn_through_stub
    process = ctx.Process(target=report_pid, args=(str(pidfile),), daemon=True)
    job = start_in_windows_job(
        process,
        confine=lambda pid: confine_to_windows_job(pid, subject="the test stub"),
        subject="the test stub",
    )
    assert job is not None
    try:
        grandchild = _read_pid(pidfile)
        assert grandchild != process.pid
        assert _in_job(grandchild, job)
        job.terminate()
        process.kill()
        assert _wait_dead(grandchild)
        process.join(WAIT_SECONDS)
    finally:
        job.close()


# -- the four sites ------------------------------------------------------------


def mesh_target(connection: Any, _session_root: Any) -> None:
    """The mesher child's stand-in: say who it is, then serve until EOF."""

    Path(os.environ[GRANDCHILD_ENV]).write_text(str(os.getpid()), encoding="utf-8")
    connection.send(("pid", os.getpid()))
    with contextlib.suppress(EOFError, OSError):
        while True:
            connection.recv()


def bempp_target(connection: Any) -> None:
    Path(os.environ[GRANDCHILD_ENV]).write_text(str(os.getpid()), encoding="utf-8")
    connection.send(("pid", None, os.getpid()))
    with contextlib.suppress(EOFError, OSError):
        while True:
            connection.recv()


def test_the_mesher_child_kill_takes_the_stubs_interpreter(spawn_through_stub, monkeypatch) -> None:
    from server.mesh import child as mesh_child

    ctx, pidfile = spawn_through_stub
    seen: list[bool] = []
    monkeypatch.setattr(
        mesh_child, "confine_to_windows_job", _late(mesh_child.confine_to_windows_job, pidfile, seen)
    )
    host = mesh_child.MesherChildHost(process_context=ctx, target=mesh_target)
    try:
        with host._state:
            channel = host._ensure_locked()
        kind, grandchild = channel.events.get(timeout=WAIT_SECONDS)
        assert kind == "pid" and grandchild != channel.process.pid
        assert seen == [False], "the job was assigned after the interpreter existed"
        assert _in_job(grandchild, channel.job)
    finally:
        host.close()
    assert _wait_dead(grandchild)
    assert not channel.reader.is_alive(), "the reader never saw EOF"


def test_the_bempp_worker_stop_takes_the_stubs_interpreter(spawn_through_stub, monkeypatch) -> None:
    from server.solver import bempp_process

    ctx, pidfile = spawn_through_stub
    seen: list[bool] = []
    monkeypatch.setattr(
        bempp_process,
        "confine_to_windows_job",
        _late(bempp_process.confine_to_windows_job, pidfile, seen),
    )
    host = bempp_process.BemppProcessHost(process_context=ctx, target=bempp_target)
    try:
        connection = host._ensure_started()
        assert connection.poll(WAIT_SECONDS)
        kind, _none, grandchild = connection.recv()
        assert kind == "pid" and grandchild != host._process.pid
        assert seen == [False], "the job was assigned after the interpreter existed"
        assert _in_job(grandchild, host._job)
    finally:
        host._terminate_sync()
    assert _wait_dead(grandchild)


def test_the_cad_child_deadline_takes_the_stubs_interpreter(tree, tmp_path, monkeypatch) -> None:
    from server.cadlink import isolation

    stub, pidfile = tree
    seen: list[bool] = []
    jobs: list[Any] = []
    real_assign = isolation._assign_windows_job

    def assign(process: Any, budget: Any) -> Any:
        job = real_assign(process, budget)
        jobs.append(job)
        return job

    monkeypatch.setattr(isolation, "_assign_windows_job", _late(assign, pidfile, seen))
    monkeypatch.setattr(isolation, "child_command", lambda _entry: _stub_command(stub, pidfile))
    in_job: list[bool] = []
    real_supervise = isolation._supervise

    def supervise(process: Any, budget: Any, *, job: Any = None, group: Any = None) -> Any:
        in_job.append(_in_job(_read_pid(pidfile), job))
        return real_supervise(process, budget, job=job, group=group)

    monkeypatch.setattr(isolation, "_supervise", supervise)
    staging = tmp_path / "staging"
    for directory in (staging / "tmp", staging / "home", staging / "out"):
        directory.mkdir(parents=True)
    budget = isolation.ChildBudget(wall_time_s=3.0, memory_bytes=1024**3)
    with pytest.raises(isolation.ChildRefusal, match="deadline"):
        isolation._run_child(
            {"task": "test"},
            staging=staging,
            out_dir=staging / "out",
            budget=budget,
            allowed_artifacts=(),
            stage="test",
            entrypoint="unused",
        )
    assert seen == [False], "the job was assigned after the interpreter existed"
    assert in_job == [True]
    assert _wait_dead(_read_pid(pidfile))


FAKE_SERVER = r'''import argparse, json, os, time
from pathlib import Path

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--status-control", type=Path, required=True)
parser.add_argument("--grandchild-pid", type=Path, required=True)
args, _ = parser.parse_known_args()
args.grandchild_pid.write_text(str(os.getpid()), encoding="utf-8")
args.status_control.with_name("ready.json").write_text(
    json.dumps({"host": "127.0.0.1", "port": args.port}), encoding="utf-8"
)
print("ready", flush=True)
# A wedged server: it never honours the stop file.
time.sleep(%d)
''' % SELF_EXIT_SECONDS


def test_the_desktop_quit_takes_the_stubs_server(tree, tmp_path, monkeypatch) -> None:
    from launchers.statusapp import controller as controller_module

    stub, pidfile = tree
    checkout = tmp_path / "checkout"
    (checkout / "frontend" / "dist").mkdir(parents=True)
    (checkout / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    server = tmp_path / "server.py"
    server.write_text(FAKE_SERVER, encoding="utf-8")
    seen: list[bool] = []
    monkeypatch.setattr(
        controller_module,
        "_windows_job_for",
        _late(controller_module._windows_job_for, pidfile, seen),
    )
    controller = controller_module.StatusController(
        repo_root=checkout,
        server_command=(BASE_PYTHON, str(stub), str(server)),
        server_args=("--grandchild-pid", str(pidfile)),
        request_timeout=0.2,
        shutdown_timeout=1.0,
        request_probe=lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html><body>SPA</body></html>")
        ),
    )
    try:
        controller.start()
        grandchild = _read_pid(pidfile)
        assert controller.process is not None and grandchild != controller.process.pid
        assert seen == [False], "the job was assigned after the server existed"
        assert _in_job(grandchild, controller._windows_job)
    finally:
        controller.stop()
    assert _wait_dead(grandchild)
