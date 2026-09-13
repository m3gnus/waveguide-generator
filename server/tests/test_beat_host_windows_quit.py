"""Record what a packaged Quit on Windows does to BEAT's persistent host.

``hornlab_beat_bem`` keeps its Julia worker in a *persistent host* process
meant to outlive the application, so the next launch adopts a warm runtime
(``server/app.py``, ``shutdown_beat_worker``). On macOS and Linux the host
calls ``setsid`` and does outlive it. On Windows it is started with
``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` and without
``CREATE_BREAKAWAY_FROM_JOB``, and the packaged launcher runs the server in a
kill-on-close Job Object that it closes at the end of every stop
(``launchers/statusapp/controller.py``). A process a job member starts is in
the job, so the host is terminated with the rest of the tree.

That is recorded, not fixed: surviving would need both a breakaway flag in the
BEAT package and ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` on the launcher's job, and
the cost of not surviving is one cold BEAT start on the next launch, not a
wrong result. ``docs/reference/SHUTDOWN-AND-RECOVERY.md`` states it.

The Windows test drives the real ``_windows_job_for`` and a stand-in host
started with the package's exact flags. The tripwire below it runs everywhere,
so a pin that changes those flags fails here and the record gets revisited.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


#: What ``HostedBeatWorker._launch`` passes on Windows at the pinned commit.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
BEAT_HOST_CREATION_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

#: Every process this starts exits by itself after this long, whatever happens.
SELF_EXIT_SECONDS = 60.0

_STILL_ACTIVE = 259


def test_the_pinned_beat_host_is_started_without_breaking_away_from_a_job() -> None:
    """The record below is only true while the package starts its host this way."""

    worker_client = pytest.importorskip("hornlab_beat_bem.worker_client")
    source = inspect.getsource(worker_client.HostedBeatWorker._launch)

    assert "0x00000008 | 0x00000200" in source, (
        "hornlab_beat_bem changed how it starts its persistent host on Windows; "
        "re-record what a packaged Quit does to it"
    )
    assert "BREAKAWAY" not in source.upper()
    assert "0x01000000" not in source


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
def test_a_packaged_quit_on_windows_terminates_the_beat_persistent_host() -> None:
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

        deadline = time.monotonic() + 10.0
        while _alive(host_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(host_pid), (
            "the detached host survived the launcher's job close; the record in "
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
    """The control: the same host, no job, outlives its parent.

    Without it a pass above could be the CI runner's own job tearing trees
    down, not the launcher's.
    """

    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(BEAT_HOST_CREATION_FLAGS), str(SELF_EXIT_SECONDS)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=CREATE_BREAKAWAY_FROM_JOB,
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


def test_the_record_is_documented() -> None:
    doc = (
        Path(__file__).resolve().parents[2] / "docs" / "reference" / "SHUTDOWN-AND-RECOVERY.md"
    ).read_text(encoding="utf-8")

    assert "persistent host" in doc
    assert "CREATE_BREAKAWAY_FROM_JOB" in doc
