#!/usr/bin/env python3
"""Qualify Quit on an installed release candidate: the packaged shutdown gate.

``server/tests/test_bounded_server_shutdown.py`` proves the shutdown mechanism
against a checkout. This proves it against what a user installs, on each
platform's packaged runtime (``docs/reference/SHUTDOWN-AND-RECOVERY.md``):

1. **The memory ceiling.** The packaged interpreter resolves the dense-solver
   ceiling and reports its bytes, where they came from, and the probe that
   measured physical memory. Physical memory must be known, through the
   platform's own probe (``GlobalMemoryStatusEx`` on Windows).
2. **A Quit during a blocked mesh build.** The installed server is started the
   way the status window starts it, a solve is submitted, and its mesh build
   is parked in the gmsh worker through the test-only
   ``WG2_TEST_GMSH_BLOCK_FILE`` hook: a call Python cannot interrupt, which is
   what an OCC build is and what holds a process open. The stop is requested
   the way the status window requests it, through the control file. The
   server must exit on its own, with status 0, inside the status window's
   grace (read from the installed launcher), before this gate would kill it.
3. **No stray children.** Every process the server started, enumerated while
   it was alive, must be gone within two seconds of its exit.
4. **The next start.** A second start on the same data and temporary
   directories must read the job as interrupted by Quit and leave no ``wg2-*``
   temporary directory but its own; after its own clean stop, none at all.

Everything runs in this gate's private tree: data, temporary directory, a
sandboxed Fusion AddIns directory and every cache ``isolated_environment``
redirects. Only processes this gate started are ever signalled.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qualify_installed_cpu import (  # noqa: E402 - a sibling script, not a package
    DESIGN,
    QualificationError,
    http,
    isolated_environment,
    resolve_payload,
    wait_for,
)


START_TIMEOUT_S = 300.0
BLOCK_TIMEOUT_S = 180.0
#: The server's owned children leave with it; this only covers the kernel
#: reaping them after their parent went away.
CHILD_REAP_S = 2.0
PARKED_OPERATION_SUFFIX = "_build_sync"

#: The probe each platform is expected to answer with (``server/platform/memory.py``).
EXPECTED_MEMORY_PROBES = {
    "Windows": {"GlobalMemoryStatusEx"},
    "Linux": {"sysconf"},
    "Darwin": {"sysconf", "sysctl hw.memsize"},
}

#: Run in the packaged interpreter against the installed app layer.
_MEMORY_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from server.mesh.builder import resolve_dense_solver_memory_limit
limit = resolve_dense_solver_memory_limit()
print(json.dumps({
    "bytes": limit.bytes,
    "source": limit.source,
    "description": limit.describe(),
    "physical": limit.physical.to_dict(),
}))
"""


def launcher_grace(app: Path) -> float:
    """The status window's ``shutdown_timeout``, read from the installed launcher."""

    source = (app / "launchers" / "statusapp" / "controller.py").read_text(encoding="utf-8")
    match = re.search(r"shutdown_timeout:\s*float\s*=\s*([0-9]+(?:\.[0-9]+)?)", source)
    if match is None:
        raise QualificationError(
            "could not read the status window's shutdown_timeout from the installed launcher"
        )
    return float(match.group(1))


def memory_ceiling(interpreter: Path, app: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Resolve the ceiling in the packaged runtime, with no override in force."""

    probe_environment = {
        name: value
        for name, value in environment.items()
        if not name.startswith("WG2_DENSE_SOLVER_MEMORY_LIMIT")
    }
    completed = subprocess.run(  # noqa: S603 - packaged interpreter, fixed program
        [str(interpreter), "-c", _MEMORY_PROBE, str(app)],
        env=probe_environment,
        cwd=str(app),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise QualificationError(
            f"the memory probe exited {completed.returncode}: {completed.stderr[-1500:]}"
        )
    try:
        answer = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise QualificationError(f"unreadable memory probe output: {exc}") from exc
    physical = answer.get("physical") or {}
    system = platform.system()
    expected = EXPECTED_MEMORY_PROBES.get(system)
    if physical.get("known") is not True or not isinstance(physical.get("bytes"), int):
        raise QualificationError(f"physical memory is unknown on this host: {answer}")
    if expected is not None and physical.get("source") not in expected:
        raise QualificationError(
            f"physical memory came from {physical.get('source')!r}, not {sorted(expected)}: {answer}"
        )
    if answer.get("source") not in {"physical-memory", "physical-memory-capped"}:
        raise QualificationError(f"the ceiling was not derived from physical memory: {answer}")
    if not 0 < int(answer["bytes"]) <= int(physical["bytes"]) // 2:
        raise QualificationError(f"the ceiling is not within half of physical memory: {answer}")
    return answer


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def _windows_process_table() -> dict[int, tuple[int, bool]]:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        table: dict[int, tuple[int, bool]] = {}
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                int(entry.cntThreads) > 0,
            )
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return table
    finally:
        kernel32.CloseHandle(snapshot)


def process_table() -> dict[int, tuple[int, bool]]:
    """``pid -> (parent pid, running)`` for every process on the host."""

    if os.name == "nt":
        return _windows_process_table()
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    table: dict[int, tuple[int, bool]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = (int(fields[1]), not fields[2].startswith("Z"))
    return table


def descendants(root: int, table: dict[int, tuple[int, bool]] | None = None) -> set[int]:
    table = process_table() if table is None else table
    found: set[int] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, _running) in table.items():
            if ppid == parent and pid not in found:
                found.add(pid)
                frontier.append(pid)
    return found


def still_running(pids: set[int]) -> set[int]:
    table = process_table()
    return {pid for pid in pids if pid in table and table[pid][1]}


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False
        )
    else:
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# ---------------------------------------------------------------------------
# One run of the installed server
# ---------------------------------------------------------------------------


@dataclass
class Run:
    """One ``launch/serve.py`` run of the installed app, controlled like the status window."""

    process: subprocess.Popen[bytes]
    control: Path
    output: Path
    base: str = ""
    children: set[int] = field(default_factory=set)

    @property
    def pid(self) -> int:
        return self.process.pid

    @classmethod
    def start(
        cls,
        interpreter: Path,
        app: Path,
        environment: dict[str, str],
        data_dir: Path,
        root: Path,
    ) -> Run:
        control_dir = root / "control"
        control_dir.mkdir(parents=True)
        output = root / "server.out"
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        with output.open("wb") as sink:
            process = subprocess.Popen(  # noqa: S603 - packaged interpreter, fixed program
                [
                    str(interpreter),
                    str(app / "launch" / "serve.py"),
                    "--no-browser",
                    "--data-dir",
                    str(data_dir),
                    "--status-control",
                    str(control_dir / "stop"),
                ],
                cwd=str(app),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                **options,
            )
        run = cls(process, control_dir / "stop", output)
        ready = control_dir / "ready.json"

        def reserved() -> bool:
            run.fail_if_exited("reserving its port")
            return ready.is_file()

        wait_for(reserved, START_TIMEOUT_S, f"the server to reserve a port (log: {output})", interval=0.2)
        run.base = f"http://127.0.0.1:{int(json.loads(ready.read_text(encoding='utf-8'))['port'])}"

        def serving() -> bool:
            run.fail_if_exited("starting")
            return http(run.base, "/api/jobs", timeout=10.0) is not None

        wait_for(serving, START_TIMEOUT_S, f"the server to answer (log: {output})", interval=0.5)
        return run

    def fail_if_exited(self, doing: str) -> None:
        if self.process.poll() is not None:
            tail = self.output.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise QualificationError(
                f"the server exited {self.process.returncode} while {doing}:\n{tail}"
            )

    def stop_and_time(self, grace: float) -> float:
        """Request a stop through the control file and time the exit against ``grace``."""

        self.control.write_text("stop\n", encoding="utf-8")
        asked = time.monotonic()
        while self.process.poll() is None:
            # Whatever the server starts while it stops is its child too. One
            # failed listing is not a verdict about the server; the next try is.
            try:
                self.children |= descendants(self.pid)
            except (OSError, subprocess.SubprocessError):
                pass
            if time.monotonic() - asked >= grace:
                raise QualificationError(
                    f"the server was still running {grace:.1f} s after a stop request, "
                    "when the status window would have killed it"
                )
            time.sleep(0.1)
        elapsed = time.monotonic() - asked
        if self.process.returncode != 0:
            raise QualificationError(
                f"the server exited {self.process.returncode} after a stop request"
            )
        return elapsed

    def kill_if_alive(self) -> None:
        if self.process.poll() is not None:
            return
        for pid in descendants(self.pid) | self.children:
            _kill(pid)
        self.process.kill()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass


def _prefer_bempp(data_dir: Path) -> None:
    """Let the start's worker prewarm start a BEMPP child, so there is one to reap."""

    (data_dir / "ui_settings.json").write_text(
        json.dumps(
            {"schema_version": 1, "namespaces": {"solveOptions": {"state": {"engine": "bempp"}}}}
        ),
        encoding="utf-8",
    )


def _temporary_leftovers(temporary: Path, *, except_pid: int | None = None) -> list[str]:
    own = f"wg2-run-{except_pid}-" if except_pid is not None else None
    return sorted(
        path.name
        for path in temporary.iterdir()
        if path.name.startswith("wg2-") and not (own and path.name.startswith(own))
    )


def run_gate(
    app: Path,
    interpreter: Path,
    environment: dict[str, str],
    work: Path,
    output: Path,
    report: dict[str, Any],
) -> None:
    """Fill ``report`` in place, so a failure keeps everything established before it."""

    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    grace = launcher_grace(app)
    report["launcher_grace_seconds"] = grace
    report["memory_ceiling"] = memory_ceiling(interpreter, app, environment)

    data_dir = work / "data"
    temporary = work / "tmp"
    addins = work / "fusion-addins"
    marker = work / "gmsh-parked"
    for directory in (data_dir, temporary, addins):
        directory.mkdir(parents=True, exist_ok=True)
    _prefer_bempp(data_dir)
    base_environment = dict(environment)
    base_environment.update(
        WG2_NO_BROWSER="1",
        # This gate needs no BEAT runtime; the CPU gate prepares and tests one.
        WG2_SKIP_BEAT_CPU_PROVISION="1",
        # Never the add-in of whatever machine runs this.
        WG2_FUSION_ADDINS_DIR=str(addins),
        PYTHONUNBUFFERED="1",
    )
    for name in ("TMPDIR", "TEMP", "TMP"):
        base_environment[name] = str(temporary)
    parked_environment = dict(base_environment, WG2_TEST_GMSH_BLOCK_FILE=str(marker))

    runs: list[Run] = []
    try:
        first = Run.start(interpreter, app, parked_environment, data_dir, work / "first")
        runs.append(first)
        job = http(first.base, "/api/solve", {"design": DESIGN, "options": {}})["job_id"]
        report["job_id"] = job

        def parked() -> bool:
            first.fail_if_exited("building the parked mesh")
            return marker.is_file()

        wait_for(parked, BLOCK_TIMEOUT_S, "the mesh build to be parked in the gmsh worker", interval=0.2)
        operation = marker.read_text(encoding="utf-8").strip()
        if not operation.endswith(PARKED_OPERATION_SUFFIX):
            raise QualificationError(f"the gmsh worker parked {operation!r}, not the mesh build")
        first.children = descendants(first.pid)
        report["children_at_stop"] = len(first.children)

        elapsed = first.stop_and_time(grace)
        report["quit"] = {"seconds": round(elapsed, 2), "exit_code": first.process.returncode}
        report["children_seen_while_stopping"] = len(first.children)
        deadline = time.monotonic() + CHILD_REAP_S
        while still_running(first.children) and time.monotonic() < deadline:
            time.sleep(0.1)
        stray = still_running(first.children)
        if stray:
            raise QualificationError(f"child processes {sorted(stray)} outlived the server")
        report["left_behind_before_restart"] = _temporary_leftovers(temporary)

        second = Run.start(interpreter, app, base_environment, data_dir, work / "second")
        runs.append(second)
        status = http(second.base, f"/api/status/{job}")
        report["next_start_job"] = {
            key: status.get(key) for key in ("status", "stage_message", "error_message")
        }
        if status.get("status") != "cancelled" or status.get("stage_message") != "Interrupted by Quit":
            raise QualificationError(f"the next start did not read the job as interrupted by Quit: {status}")
        leftovers = _temporary_leftovers(temporary, except_pid=second.pid)
        report["left_behind_after_restart"] = leftovers
        if leftovers:
            raise QualificationError(f"the next start left stale temporary directories: {leftovers}")

        report["clean_stop_seconds"] = round(second.stop_and_time(grace), 2)
        remaining = _temporary_leftovers(temporary)
        if remaining:
            raise QualificationError(f"a clean stop left temporary directories: {remaining}")
    finally:
        for run in runs:
            run.kill_if_alive()
        for index, run in enumerate(runs, start=1):
            try:
                (output / f"server-{index}.out").write_bytes(run.output.read_bytes())
            except OSError:
                pass
        try:
            (output / "server.log").write_bytes((data_dir / "logs" / "server.log").read_bytes())
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--payload",
        type=Path,
        required=True,
        help="installed application root (holding 'app' and 'runtime'), or a macOS .app",
    )
    parser.add_argument("--payload-kind", default="unspecified", help="recorded in the report")
    parser.add_argument("--work", type=Path, required=True, help="private scratch directory")
    parser.add_argument("--output", type=Path, required=True, help="logs and the JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    started = time.time()
    output = arguments.output.expanduser().resolve()
    report: dict[str, Any] = {
        "payload_kind": arguments.payload_kind,
        "platform": {"system": platform.system(), "machine": platform.machine()},
    }
    failure: str | None = None
    try:
        resources, app, interpreter = resolve_payload(arguments.payload)
        work = arguments.work.expanduser().resolve()
        report.update({"payload": str(resources), "interpreter": str(interpreter)})
        run_gate(app, interpreter, isolated_environment(app, work), work, output, report)
    except QualificationError as exc:
        failure = str(exc)
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is still a failure
        failure = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    report["qualified"] = failure is None
    if failure is not None:
        report["error"] = failure
    report["seconds"] = round(time.time() - started)
    output.mkdir(parents=True, exist_ok=True)
    (output / "quit-qualification.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    if failure is not None:
        print(f"Quit qualification FAILED: {failure}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
