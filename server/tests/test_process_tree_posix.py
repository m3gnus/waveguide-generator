"""The POSIX half of ``server/platform/process_tree.py``, actually executed.

These run only on POSIX, which is the point: the Windows job object had a test
from the day it was written, while ``adopt_process_group`` and its group kill
shipped unexercised. The asymmetry is what let a force-killed launcher strand a
sweep's workers -- reparented to init, still burning a core each -- on the one
platform nobody could run.
"""

from __future__ import annotations

import multiprocessing
import os
import pathlib
import time

import pytest

from server.platform import process_tree


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")

_TIMEOUT = 30.0
_SAFETY = 60.0  # nothing a test starts may outlive the run


def _beat(beat_path: str) -> None:
    """A sweep worker: stay alive and keep saying so."""

    began = time.monotonic()
    count = 0
    while time.monotonic() - began < _SAFETY:
        count += 1
        pathlib.Path(beat_path).write_text(str(count))
        time.sleep(0.02)


def _adopting_child(beat_path: str, ready_path: str) -> None:
    """Claim a session, start a worker, then tear the session down."""

    process_tree.adopt_process_group()
    worker = multiprocessing.get_context("spawn").Process(
        target=_beat, args=(beat_path,)
    )
    worker.start()
    pathlib.Path(ready_path).write_text(str(worker.pid))
    while not pathlib.Path(beat_path).exists():
        time.sleep(0.01)
    process_tree.kill_own_process_group()
    time.sleep(_SAFETY)  # only reached if the kill silently did nothing


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_the_group_kill_refuses_a_session_this_process_did_not_claim() -> None:
    """The guard that keeps this out of the test runner's own session.

    ``getpgid(0) == getpid()`` is true of any job-control group leader, which is
    what ``pytest`` is when run from a terminal -- so deriving ownership that way
    would let this SIGKILL the developer's shell job. Ownership is recorded at
    adoption instead, and nothing here has adopted anything.
    """

    assert process_tree._adopted_session_pid != os.getpid()
    assert process_tree.kill_own_process_group() is False


def test_the_group_kill_refuses_a_flag_inherited_across_a_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forked child inherits the flag but must not act on its parent's session."""

    monkeypatch.setattr(process_tree, "_adopted_session_pid", os.getpid() + 1)
    assert process_tree.kill_own_process_group() is False


def test_killing_the_claimed_session_reaps_the_sweep_workers(
    tmp_path: pathlib.Path,
) -> None:
    """The property a force-killed launcher depends on.

    ``os._exit`` in the parent-sentinel watchdog runs no multiprocessing
    cleanup, so the workers only die if the session goes with it.
    """

    beat = tmp_path / "beat"
    ready = tmp_path / "ready"
    child = multiprocessing.get_context("spawn").Process(
        target=_adopting_child, args=(str(beat), str(ready))
    )
    child.start()
    worker_pid: int | None = None
    try:
        deadline = time.monotonic() + _TIMEOUT
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "child never started its worker"
        worker_pid = int(ready.read_text())

        child.join(_TIMEOUT)
        assert not child.is_alive(), "the session kill did not take the child"

        deadline = time.monotonic() + _TIMEOUT
        while time.monotonic() < deadline and _pid_alive(worker_pid):
            time.sleep(0.05)
        assert not _pid_alive(worker_pid), "the sweep worker outlived its session"

        # Frozen, not merely unreachable: a pid can be reused.
        first = beat.read_text()
        time.sleep(0.5)
        assert beat.read_text() == first
    finally:
        if worker_pid is not None and _pid_alive(worker_pid):
            os.kill(worker_pid, 9)
        if child.is_alive():
            child.kill()
            child.join(_TIMEOUT)
