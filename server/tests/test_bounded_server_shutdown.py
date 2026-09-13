"""Process-level proof that a stopping server ends on its own, inside its budget.

A coroutine returning is not evidence here. The property under test is whether
the *process* ends while a gmsh call is still running, and Python joins
executor threads at interpreter exit, so ``shutdown()`` can return and the
process still live forever. These tests therefore start the real
``launch/serve.py`` in a child process, park its gmsh worker in the middle of a
real job's mesh build through the test-only hook in
``server/mesh/gmsh_worker.py`` (``WG2_TEST_GMSH_BLOCK_FILE``), stop it the way
each real path does, and time the exit from outside.

Every process these tests signal or kill is one they started: the server, the
descendants it spawned (enumerated while it is still alive), and the stand-in
parent of the orphan test. Nothing is found by name, so a sibling worktree
running the same binaries is never touched.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request

import pytest

from launchers.statusapp.controller import StatusController
from server.platform.paths import data_paths


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE = REPO_ROOT / "launch" / "serve.py"

#: How long the status window waits for its server before killing the tree.
#: Read from the controller rather than restated, so the bound these tests hold
#: the server to is the one the launcher actually enforces.
LAUNCHER_GRACE_SECONDS = float(
    inspect.signature(StatusController.__init__).parameters["shutdown_timeout"].default
)
#: A second Ctrl+C means "now". Well inside the budget the first one started,
#: so a pass cannot be the budget expiring instead.
PROMPT_EXIT_SECONDS = 2.0
#: A cold start imports the whole server and mounts the SPA; Windows runners
#: are the slow ones.
START_TIMEOUT_SECONDS = 120.0
#: Submission to "the mesh build is parked": engine resolution plus the queue.
BLOCK_TIMEOUT_SECONDS = 90.0
#: An owned child that the server closed is gone by the time the server is;
#: this only covers the kernel reaping it after its parent went away.
CHILD_REAP_SECONDS = 2.0

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="signals and process-tree enumeration are POSIX here"
)

_SOLVE_BODY: dict[str, Any] = {
    "design": {
        "formula": "OSSE",
        "L": 120,
        "a": 45,
        "mesh": {"wall_thickness": 0},
        "enclosure": {"depth": 0},
        "simulation": {
            "f1": 250,
            "f2": 8000,
            "num_frequencies": 2,
            "sim_type": "freestanding",
        },
    },
    # BEMPP meshes on the gmsh worker before anything else, and it is the
    # engine every CI platform can run (numba assembly when OpenCL has no CPU
    # device), so the parked build is reached the same way everywhere.
    "options": {"engine": "bempp", "stage_delay_ms": 0},
}

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class _Server:
    process: subprocess.Popen[bytes]
    data_dir: Path
    control_path: Path
    output: Path
    marker: Path | None
    tmp_dir: Path
    addins_dir: Path
    port: int = 0
    preview_marker: Path | None = None

    def request_stop(self) -> float:
        """Ask for a stop exactly as the status window does, and when."""

        self.control_path.write_text("stop\n", encoding="utf-8")
        return time.monotonic()

    def evidence(self) -> str:
        lines: list[str] = []
        for path in (self.output, self.data_dir / "logs" / "server.log"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines.append(f"--- {path.name} (tail) ---")
            lines.extend(text.splitlines()[-40:])
        return "\n".join(lines)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _http(
    port: int, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with _NO_PROXY.open(request, timeout=10.0) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def _wait_for(
    predicate: Callable[[], bool], timeout: float, what: str, server: _Server
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if server.process.poll() is not None:
            pytest.fail(
                f"the server exited ({server.process.returncode}) while waiting for "
                f"{what}\n{server.evidence()}"
            )
        time.sleep(0.1)
    pytest.fail(f"timed out after {timeout:.0f} s waiting for {what}\n{server.evidence()}")


def _server_log_contains(server: _Server, text: str) -> bool:
    try:
        log = (server.data_dir / "logs" / "server.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return text in log


def _child_environment(
    data_dir: Path,
    marker: Path | None,
    *,
    prewarm: bool,
    tmp_dir: Path,
    addins_dir: Path,
    preview_marker: Path | None = None,
) -> dict[str, str]:
    # The suite's own WG2_* settings describe the test process, not this
    # server; start from none of them and say exactly what the server gets.
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("WG2_")
    }
    environment["WG2_DATA_DIR"] = str(data_dir)
    environment["WG2_NO_BROWSER"] = "1"
    environment["WG2_SKIP_BEAT_CPU_PROVISION"] = "1"
    # The add-in reconciliation every start runs must find a sandbox, never
    # the add-in installed on this machine (the root conftest's guard).
    environment["WG2_FUSION_ADDINS_DIR"] = str(addins_dir)
    # A private temporary directory: what the server leaves there, and what
    # its next start sweeps, is then this test's alone to inspect.
    for name in ("TMPDIR", "TEMP", "TMP"):
        environment[name] = str(tmp_dir)
    environment["PYTHONUNBUFFERED"] = "1"
    if not prewarm:
        environment["WG2_SOLVER_WARMUP"] = "0"
    if marker is not None:
        environment["WG2_TEST_GMSH_BLOCK_FILE"] = str(marker)
    if preview_marker is not None:
        environment["WG2_TEST_PREVIEW_BLOCK_FILE"] = str(preview_marker)
    return environment


def _prefer_bempp(data_dir: Path) -> None:
    """Make the boot prewarm start a BEMPP worker child on every platform.

    AUTO would warm Metal on a Mac, which has no child process; the saved
    preference is what the prewarm honours first (``server/app.py``).
    """

    settings = data_paths(data_dir).root / "ui_settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {"schema_version": 1, "namespaces": {"solveOptions": {"state": {"engine": "bempp"}}}}
        ),
        encoding="utf-8",
    )


def _launch(
    root: Path,
    started: list[subprocess.Popen[bytes]],
    *,
    data_dir: Path | None = None,
    block: bool,
    prewarm_bempp: bool = False,
    parent_pid: int | None = None,
    tmp_dir: Path | None = None,
    block_preview: bool = False,
) -> _Server:
    root.mkdir(parents=True, exist_ok=True)
    data = data_dir or (root / "data")
    data.mkdir(parents=True, exist_ok=True)
    if prewarm_bempp:
        _prefer_bempp(data)
    temporary = tmp_dir or (root / "tmp")
    temporary.mkdir(parents=True, exist_ok=True)
    addins = root / "fusion-addins"
    addins.mkdir(parents=True, exist_ok=True)
    control_dir = root / "control"
    control_dir.mkdir()
    marker = root / "gmsh-parked" if block else None
    preview_marker = root / "preview-parked" if block_preview else None
    output = root / "server.out"
    command = [
        sys.executable,
        str(SERVE),
        "--no-browser",
        "--port",
        str(_free_port()),
        "--status-control",
        str(control_dir / "stop"),
        "--parent-pid",
        str(parent_pid if parent_pid is not None else os.getpid()),
    ]
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Its own session: a Ctrl+C at the terminal running pytest must not
        # reach it, and the signals below must not reach pytest.
        options["start_new_session"] = True
    with output.open("wb") as sink:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=_child_environment(
                data,
                marker,
                prewarm=prewarm_bempp,
                tmp_dir=temporary,
                addins_dir=addins,
                preview_marker=preview_marker,
            ),
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            **options,
        )
    started.append(process)
    server = _Server(
        process,
        data,
        control_dir / "stop",
        output,
        marker,
        temporary,
        addins,
        preview_marker=preview_marker,
    )
    ready = control_dir / "ready.json"
    _wait_for(ready.is_file, START_TIMEOUT_SECONDS, "the reserved port", server)
    server.port = int(json.loads(ready.read_text(encoding="utf-8"))["port"])

    def serving() -> bool:
        try:
            return _http(server.port, "GET", "/api/jobs")[0] == 200
        except OSError:
            return False

    _wait_for(serving, START_TIMEOUT_SECONDS, "the server to answer", server)
    return server


def _submit_parked_job(server: _Server) -> str:
    status, body = _http(server.port, "POST", "/api/solve", _SOLVE_BODY)
    if status == 503:
        reason = f"no BEMPP engine on this host: {body.decode(errors='replace')}"
        if os.environ.get("CI"):
            # Every CI platform installs bempp-cl; a skip there would pass the
            # harness without running it.
            pytest.fail(reason)
        pytest.skip(reason)
    assert status == 200, body.decode(errors="replace")
    job_id = str(json.loads(body)["job_id"])
    marker = server.marker
    assert marker is not None
    _wait_for(marker.is_file, BLOCK_TIMEOUT_SECONDS, "the mesh build to be parked", server)
    parked = marker.read_text(encoding="utf-8").strip()
    assert parked.endswith("_build_sync"), f"the gmsh worker parked {parked!r}"
    return job_id


def _wait_for_exit(server: _Server, since: float, bound: float, cause: str) -> float:
    remaining = bound - (time.monotonic() - since)
    try:
        server.process.wait(timeout=max(0.0, remaining))
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the server was still alive {time.monotonic() - since:.2f} s after "
            f"{cause}; the bound is {bound:.1f} s\n{server.evidence()}"
        )
    elapsed = time.monotonic() - since
    print(f"server exited {elapsed:.2f} s after {cause} (bound {bound:.1f} s)")
    return elapsed


def _process_table() -> dict[int, tuple[int, str]]:
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    table: dict[int, tuple[int, str]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = (int(fields[1]), fields[2])
    return table


def _descendants(root_pid: int) -> set[int]:
    table = _process_table()
    found: set[int] = set()
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, _stat) in table.items():
            if ppid == parent and pid not in found:
                found.add(pid)
                frontier.append(pid)
    return found


def _still_running(pids: set[int]) -> set[int]:
    table = _process_table()
    return {pid for pid in pids if pid in table and not table[pid][1].startswith("Z")}


@pytest.fixture()
def started() -> Any:
    """Every process a test starts, reaped (and killed only if still alive)."""

    processes: list[subprocess.Popen[bytes]] = []
    yield processes
    for process in reversed(processes):
        if process.poll() is None:
            if os.name != "nt":
                # Enumerated now, while this server is alive, so every PID is
                # one of its own descendants rather than a recycled number.
                for pid in _descendants(process.pid):
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(pid, signal.SIGKILL)
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=15)


def test_a_stop_request_during_a_blocked_build_ends_the_process_in_time(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """The status window's Quit, with a job's mesh build parked in the worker.

    Runs on every platform. The owned-children check needs a process table,
    which this harness reads with ``ps``, so on Windows it is not made here:
    there the server's BEMPP child sits in a kill-on-close Job Object and
    also exits when its parent does.
    """

    server = _launch(tmp_path / "first", started, block=True, prewarm_bempp=True)
    job_id = _submit_parked_job(server)
    owned: set[int] = set()
    if os.name != "nt":
        _wait_for(
            lambda: bool(_descendants(server.process.pid)),
            BLOCK_TIMEOUT_SECONDS,
            "the BEMPP worker child",
            server,
        )
        owned = _descendants(server.process.pid)

    stopped_at = server.request_stop()
    _wait_for_exit(server, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")
    assert server.process.returncode == 0, server.evidence()

    if owned:
        deadline = time.monotonic() + CHILD_REAP_SECONDS
        while _still_running(owned) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _still_running(owned), (
            f"owned children {sorted(_still_running(owned))} outlived the server\n"
            f"{server.evidence()}"
        )

    # The next start reads the job as interrupted by Quit and does not requeue
    # it. It also has published nothing for that job. That checks publication,
    # not a torn write: the build was parked before it produced anything, and
    # results only ever appear through ``complete_job``'s single transaction.
    relaunch = _launch(tmp_path / "second", started, data_dir=server.data_dir, block=False)
    status, body = _http(relaunch.port, "GET", f"/api/status/{job_id}")
    assert status == 200, body.decode(errors="replace")
    job = json.loads(body)
    assert job["status"] == "cancelled", job
    assert job["stage_message"] == "Interrupted by Quit", job
    assert str(job["error_message"]).startswith("Interrupted by Quit"), job
    assert job["cancellation_requested"] is False, job
    assert job["has_results"] is False, job
    assert job["has_mesh_artifact"] is False, job
    assert _http(relaunch.port, "GET", f"/api/results/{job_id}")[0] != 200

    stopped_at = relaunch.request_stop()
    _wait_for_exit(relaunch, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request with nothing running")
    assert relaunch.process.returncode == 0, relaunch.evidence()


@POSIX_ONLY
def test_a_second_interrupt_during_a_blocked_build_exits_promptly(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    server = _launch(tmp_path, started, block=True)
    _submit_parked_job(server)

    os.kill(server.process.pid, signal.SIGINT)
    # The second only once the first is known to be handled: two SIGINTs
    # pending together can reach the handler as one.
    _wait_for(
        lambda: _server_log_contains(server, "Shutdown requested (SIGINT received)"),
        30.0,
        "the first Ctrl+C to be handled",
        server,
    )
    os.kill(server.process.pid, signal.SIGINT)
    second_at = time.monotonic()

    _wait_for_exit(server, second_at, PROMPT_EXIT_SECONDS, "a second Ctrl+C")
    assert server.process.returncode == 0, server.evidence()


@POSIX_ONLY
def test_an_orphaned_server_exits_within_its_own_deadline(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """The launcher crashed: nobody is left to kill the tree after 8 s.

    POSIX only by design. The packaged Windows server lives in the launcher's
    kill-on-close Job Object, so a launcher crash takes it down too; on macOS
    and Linux it runs in its own session and is left to its own deadline.
    """

    parent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3600)"],
        stdin=subprocess.DEVNULL,
    )
    started.append(parent)
    server = _launch(tmp_path, started, block=True, parent_pid=parent.pid)
    _submit_parked_job(server)

    parent.kill()
    parent.wait(timeout=15)
    orphaned_at = time.monotonic()

    elapsed = _wait_for_exit(
        server, orphaned_at, LAUNCHER_GRACE_SECONDS, "its status-window parent died"
    )
    assert server.process.returncode == 0, server.evidence()

    from server.platform.shutdown_backstop import DEFAULT_SHUTDOWN_BUDGET_SECONDS

    # Its own deadline, not merely the launcher's: parent-death detection is a
    # kernel wait (or a 0.15 s poll), then the budget, then a bounded log flush.
    assert elapsed < DEFAULT_SHUTDOWN_BUDGET_SECONDS + 1.5, (
        f"exited {elapsed:.2f} s after the parent died; the server's own budget is "
        f"{DEFAULT_SHUTDOWN_BUDGET_SECONDS:.1f} s"
    )


def _sessions(temporary: Path) -> set[Path]:
    """The per-process temporary directories in ``temporary`` (``server/platform/temp_session.py``)."""

    return {path for path in temporary.iterdir() if path.name.startswith("wg2-run-")}


#: A live process of any build or checkout holding a temporary session open
#: until its stdin closes, then leaving without cleanup.
_SESSION_HOLDER = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from server.platform.temp_session import TemporarySession
session = TemporarySession.create(Path(sys.argv[1]))
print(session.path, flush=True)
sys.stdin.read()
os._exit(0)
"""


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_started_server_reconciles_the_add_in_in_its_sandbox(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """The positive proof that a real server never resolves the real AddIns directory.

    The reconciliation takes the installer's operation lock in whatever
    directory it resolved, so the lock appearing in the sandbox is the child
    saying where it looked.
    """

    server = _launch(tmp_path, started, block=False)
    lock = server.addins_dir / ".WGLink-install.lock"
    _wait_for(lock.is_file, 60.0, "the add-in reconciliation to lock its sandbox", server)

    stopped_at = server.request_stop()
    _wait_for_exit(server, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")


def test_a_quit_cut_short_mid_budget_still_reads_as_interrupted_by_quit(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """The reason is on disk the moment the stop begins, before any wait.

    An export queued behind the parked build keeps a request in flight, so
    Uvicorn's graceful drain holds the lifespan shutdown -- and with it the job
    runtime's own shutdown handler -- for its full 3 s. The process is then
    ended well inside that window, as the backstop, the launcher's kill or a
    force quit would end it. The next start must still read the job as
    interrupted by Quit, not as "Server restarted during execution".
    """

    server = _launch(tmp_path / "first", started, block=True)
    job_id = _submit_parked_job(server)

    def export_behind_the_build() -> None:
        with contextlib.suppress(Exception):
            _http(
                server.port,
                "POST",
                "/api/export/stl",
                {"design": _SOLVE_BODY["design"], "designRevision": 1},
            )

    threading.Thread(target=export_behind_the_build, daemon=True).start()
    # Accepted before the stop: an accepted request stays in flight however
    # far it has got, and closing the listener does not end it.
    time.sleep(1.0)

    server.request_stop()
    _wait_for(
        lambda: _server_log_contains(server, "Shutdown requested"),
        30.0,
        "the stop to begin",
        server,
    )
    time.sleep(0.5)
    assert server.process.poll() is None, "the server ended before the test could cut it short"
    server.process.kill()
    server.process.wait(timeout=30)

    relaunch = _launch(tmp_path / "second", started, data_dir=server.data_dir, block=False)
    status, body = _http(relaunch.port, "GET", f"/api/status/{job_id}")
    assert status == 200, body.decode(errors="replace")
    job = json.loads(body)
    assert job["status"] == "cancelled", job
    assert job["stage_message"] == "Interrupted by Quit", job
    assert job["has_results"] is False, job

    stopped_at = relaunch.request_stop()
    _wait_for_exit(relaunch, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")


def test_the_next_start_sweeps_what_a_stopped_build_left_behind(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """A Quit during a build ends without cleanup; the next start removes what it left.

    Also held: nothing a live process owns is touched, whichever build it is,
    and an earlier release's leftovers go only once they are too old to be in
    use.
    """

    temporary = tmp_path / "tmp"
    temporary.mkdir()
    earlier_stale = temporary / "wg2-solver-mesh-earlier-release"
    earlier_stale.mkdir()
    (earlier_stale / "waveguide.msh").write_text("x", encoding="utf-8")
    _age(earlier_stale, 3 * 24 * 3600)
    earlier_recent = temporary / "wg2-solver-mesh-in-use-by-an-older-build"
    earlier_recent.mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c", _SESSION_HOLDER, str(temporary), str(REPO_ROOT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdin is not None
        held = Path(holder.stdout.readline().strip())
        assert held.is_dir()

        server = _launch(tmp_path / "first", started, block=True, tmp_dir=temporary)
        _submit_parked_job(server)
        own = _sessions(temporary) - {held}
        assert len(own) == 1, f"expected one session for the server, found {sorted(own)}"
        (killed_session,) = own

        stopped_at = server.request_stop()
        _wait_for_exit(server, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")
        # The exit ran no cleanup: that is what the sweep exists for.
        assert killed_session.is_dir()

        relaunch = _launch(
            tmp_path / "second",
            started,
            data_dir=server.data_dir,
            block=False,
            tmp_dir=temporary,
        )
        assert not killed_session.exists(), "the next start left the stopped build's directory"
        assert not earlier_stale.exists(), "the next start left an earlier release's leftover"
        assert earlier_recent.is_dir(), "the sweep removed a directory that may be in use"
        assert held.is_dir(), "the sweep removed a live process's directory"
        (relaunch_session,) = _sessions(temporary) - {held}

        stopped_at = relaunch.request_stop()
        _wait_for_exit(relaunch, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")
        assert relaunch.process.returncode == 0, relaunch.evidence()
        # A clean exit removes its own directory rather than leaving it to a sweep.
        assert not relaunch_session.exists()
        assert held.is_dir()
    finally:
        if holder.stdin is not None:
            holder.stdin.close()
        holder.wait(timeout=30)


def test_a_stop_request_with_a_blocked_preview_ends_the_process_in_time(
    tmp_path: Path, started: list[subprocess.Popen[bytes]]
) -> None:
    """The preview executor's threads hold interpreter exit exactly as gmsh's does.

    A process-wide claim needs this case too (``WG-ARCHITECTURE-PLAN.md``
    Phase 0.2). The preview build is parked through the test-only hook in
    ``server/preview/core.py`` over a real ``/ws/preview`` socket, the way the
    viewport asks for one. The server must exit on its own, before the
    launcher's kill, and once its cleanup is done rather than when the budget
    runs out.
    """

    from websockets.sync.client import connect

    server = _launch(tmp_path, started, block=False, block_preview=True)
    marker = server.preview_marker
    assert marker is not None
    origin = f"http://127.0.0.1:{server.port}"
    socket_ = connect(
        f"ws://127.0.0.1:{server.port}/ws/preview",
        origin=origin,  # type: ignore[arg-type]
        proxy=None,
        open_timeout=30,
    )
    try:
        hello = json.loads(socket_.recv(timeout=30))
        assert hello["kind"] == "hello", hello
        socket_.send(
            json.dumps(
                {
                    "v": 1,
                    "kind": "preview",
                    "epoch": hello["epoch"],
                    "seq": 1,
                    "designRevision": 1,
                    "design": _SOLVE_BODY["design"],
                    "lod": "coarse",
                }
            )
        )
        _wait_for(marker.is_file, BLOCK_TIMEOUT_SECONDS, "the preview build to be parked", server)

        stopped_at = server.request_stop()
        elapsed = _wait_for_exit(server, stopped_at, LAUNCHER_GRACE_SECONDS, "a stop request")
    finally:
        with contextlib.suppress(Exception):
            socket_.close()
    assert server.process.returncode == 0, server.evidence()

    from server.platform.shutdown_backstop import DEFAULT_SHUTDOWN_BUDGET_SECONDS

    log = (server.data_dir / "logs" / "server.log").read_text(encoding="utf-8", errors="replace")
    assert "did not finish within its" not in log, (
        "the process waited out its whole budget for a thread its cleanup had already "
        f"let go of\n{server.evidence()}"
    )
    assert elapsed < DEFAULT_SHUTDOWN_BUDGET_SECONDS, server.evidence()
