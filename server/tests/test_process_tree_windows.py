"""Windows containment under force-kill: the job object must take the tree.

``server/platform/process_tree.py`` claims ``KILL_ON_JOB_CLOSE`` kills the tree
"even if the parent dies without running any cleanup". That claim went unchecked
for as long as it existed, and this is the class of bug that ships precisely
because the platform half nobody runs has no test -- the POSIX side had exactly
the same hole and it took a macOS session to find it.

The measurement is only meaningful with its control. The harness parent can
itself be inside a job object (a CI runner is; so is a venv launcher shim), and
if that outer job tore the tree down the result would look like a pass no matter
what our code did. A previous "0.16 s" figure in ``bempp_process.py`` was
invalidated exactly that way. So both arms run:

    with the job object     -> heartbeats must freeze
    without the job object  -> heartbeats must keep climbing

Only the contrast attributes the teardown to our code. If the control does not
climb, the environment is tearing trees down on its own, nothing can be
concluded, and the test skips rather than claiming a pass it did not earn.

Safety, because this spawns processes and force-kills them:

* every spawned process carries a hard self-exit deadline, so nothing outlives
  the test even if it dies badly;
* every pid is recorded before use and force-killed in ``finally``;
* liveness is read with ``GetExitCodeProcess``, never with ``os.kill(pid, 0)``
  -- on Windows that signature terminates rather than probes.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows job objects; the POSIX path has its own file"
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every spawned process exits by itself after this long, whatever happens here.
SELF_EXIT_SECONDS = 45.0
#: How long to wait for all three workers to be visibly running.
STARTUP_TIMEOUT_SECONDS = 30.0
WORKER_COUNT = 3

_STILL_ACTIVE = 259
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

HARNESS = '''
"""Spawned by test_process_tree_windows; drives the real containment code."""
import json, multiprocessing, os, sys, time
from pathlib import Path

sys.path.insert(0, r"{repo_root}")

SCRATCH = Path(sys.argv[1])
USE_JOB = sys.argv[2] == "1"
DEADLINE = {deadline}
WORKERS = {workers}


def worker(path):
    target = Path(path)
    stop = time.time() + DEADLINE
    counter = 0
    burn = 0.0
    while time.time() < stop:
        counter += 1
        for _ in range(20000):
            burn += 1.0001
        target.write_text(f"{{counter}} {{os.getpid()}}", encoding="utf-8")
        time.sleep(0.02)


def child(paths):
    from server.platform.process_tree import adopt_process_group
    from server.solver.bempp_process import _exit_when_parent_does

    adopt_process_group()
    _exit_when_parent_does()
    ctx = multiprocessing.get_context("spawn")
    procs = []
    for path in paths:
        proc = ctx.Process(target=worker, args=(path,), daemon=False)
        proc.start()
        procs.append(proc)
    (SCRATCH / "workers.json").write_text(
        json.dumps([p.pid for p in procs]), encoding="utf-8"
    )
    for proc in procs:
        proc.join()


def main():
    from server.platform.process_tree import confine_to_windows_job

    paths = [str(SCRATCH / f"beat{{i}}.txt") for i in range(WORKERS)]
    for path in paths:
        Path(path).write_text("0 0", encoding="utf-8")
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=child, args=(paths,), daemon=False)
    proc.start()
    job = confine_to_windows_job(proc.pid) if USE_JOB else None
    (SCRATCH / "facts.json").write_text(
        json.dumps(
            {{"parent_pid": os.getpid(), "child_pid": proc.pid,
              "job_created": job is not None}}
        ),
        encoding="utf-8",
    )
    proc.join()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
'''


def _force_kill(pid: int) -> None:
    """TerminateProcess, ignoring anything already gone."""

    try:
        os.kill(int(pid), 9)
    except (OSError, ProcessLookupError, PermissionError):
        pass


def _is_running(pid: int) -> bool:
    """Liveness without signalling. ``os.kill(pid, 0)`` would terminate here."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x0400, False, int(pid))  # QUERY_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _counters(scratch: Path) -> list[int]:
    """Sample every heartbeat, retrying past a half-written file.

    ``write_text`` truncates before it writes, so a read can land on an empty
    file. Treating that as a value made the control look like it had stopped
    climbing and skipped the test at random -- the sampler manufacturing the
    very condition it was meant to detect.
    """

    values = []
    for index in range(WORKER_COUNT):
        target = scratch / f"beat{index}.txt"
        value = -1
        for _ in range(20):
            try:
                value = int(target.read_text(encoding="utf-8").split()[0])
                break
            except (OSError, ValueError, IndexError):
                time.sleep(0.01)
        values.append(value)
    return values


def _all_frozen(first: list[int], second: list[int]) -> bool:
    return all(a == b for a, b in zip(first, second))


def _any_climbed(first: list[int], second: list[int]) -> bool:
    """Element-wise, not lexicographic: a list comparison would be decided by
    whichever worker happened to be sampled first."""

    return any(b > a for a, b in zip(first, second))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_arm(tmp_path: Path, *, use_job: bool) -> dict:
    """Start the harness, force-kill its parent, and sample the workers."""

    scratch = tmp_path / ("job" if use_job else "control")
    scratch.mkdir(parents=True, exist_ok=True)
    harness = scratch / "harness.py"
    harness.write_text(
        HARNESS.format(
            repo_root=str(REPO_ROOT),
            deadline=SELF_EXIT_SECONDS,
            workers=WORKER_COUNT,
        ),
        encoding="utf-8",
    )

    pids: list[int] = []
    launched = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(harness), str(scratch), "1" if use_job else "0"],
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW,
        cwd=str(scratch),
    )
    pids.append(launched.pid)
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        facts = workers = None
        while time.monotonic() < deadline:
            facts = facts or _read_json(scratch / "facts.json")
            workers = workers or _read_json(scratch / "workers.json")
            if facts and workers and all(c > 2 for c in _counters(scratch)):
                break
            time.sleep(0.1)
        if not facts or not workers:
            pytest.skip("the harness did not reach a running state in time")
        pids.extend([facts["parent_pid"], facts["child_pid"], *workers])

        before = _counters(scratch)
        # TerminateProcess: no atexit, no lifespan shutdown, no cleanup at all.
        _force_kill(facts["parent_pid"])
        time.sleep(2.0)
        at_2s = _counters(scratch)
        time.sleep(3.0)
        at_5s = _counters(scratch)

        return {
            "job_created": facts["job_created"],
            "before": before,
            "at_2s": at_2s,
            "at_5s": at_5s,
            "workers_alive": [pid for pid in workers if _is_running(pid)],
        }
    finally:
        for pid in pids:
            _force_kill(pid)


def test_the_job_object_takes_the_tree_when_the_parent_is_force_killed(tmp_path):
    contained = _run_arm(tmp_path, use_job=True)
    assert contained["job_created"] is True, "the job object was never created"

    control = _run_arm(tmp_path, use_job=False)
    assert control["job_created"] is False

    # The control is what gives the measurement meaning. If the environment
    # tears the tree down on its own, a frozen counter proves nothing about our
    # code, so refuse to conclude rather than bank an unearned pass.
    if not _any_climbed(control["at_2s"], control["at_5s"]):
        pytest.skip(
            "control did not survive its parent "
            f"({control['at_2s']} -> {control['at_5s']}); this host tears down "
            "process trees on its own, so containment cannot be attributed"
        )

    assert _all_frozen(contained["at_2s"], contained["at_5s"]), (
        "workers kept running after the parent was force-killed: "
        f"{contained['at_2s']} -> {contained['at_5s']}; KILL_ON_JOB_CLOSE did "
        "not hold and Windows has the same hole the POSIX path had"
    )
    assert not contained["workers_alive"], (
        f"{len(contained['workers_alive'])} worker(s) outlived the job object"
    )


def test_without_the_job_object_the_workers_are_stranded(tmp_path):
    """The hole the job object closes, stated as behaviour.

    ``os._exit`` in the watchdog runs no multiprocessing cleanup, so the child
    never stops its own workers -- the same mechanism that stranded them on
    macOS. Windows differs only in having a backstop, so this documents what the
    backstop is for and fails loudly if the strand ever stops reproducing.
    """

    control = _run_arm(tmp_path, use_job=False)

    if not _any_climbed(control["at_2s"], control["at_5s"]):
        pytest.skip(
            "this host tears down process trees on its own; the strand cannot "
            "be observed here"
        )
    assert control["workers_alive"], (
        "expected stranded workers without containment, found none"
    )
