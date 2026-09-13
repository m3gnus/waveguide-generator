"""Per-process temporary directories, and the startup sweep of dead ones.

A server that ends through the shutdown backstop or the launcher's tree kill
runs no cleanup, so a mesh build's ``TemporaryDirectory`` outlives it. These
tests hold the sweep to two rules: a directory whose owner is alive is never
touched, and one whose owner is gone is removed. The process-level proof, with
a real server killed mid-build, is ``test_bounded_server_shutdown.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from server.platform.temp_session import (
    LEGACY_MIN_AGE_SECONDS,
    LEGACY_PREFIXES,
    OWNER_LOCK_NAME,
    SESSION_PREFIX,
    TemporarySession,
    sweep_stale_temporary_directories,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

#: Holds a session open in another process until its stdin closes, then exits
#: without any cleanup, which is what the backstop's ``os._exit`` does.
_HOLDER = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from server.platform.temp_session import TemporarySession
session = TemporarySession.create(Path(sys.argv[1]))
print(session.path, flush=True)
sys.stdin.read()
os._exit(0)
"""


def _hold_session(base: Path) -> tuple[subprocess.Popen[str], Path]:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(base), str(REPO_ROOT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    line = holder.stdout.readline().strip()
    assert line, "the holder did not report its session"
    return holder, Path(line)


def _release(holder: subprocess.Popen[str]) -> None:
    assert holder.stdin is not None
    holder.stdin.close()
    holder.wait(timeout=30)


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_session_is_private_locked_and_removed_on_close(tmp_path: Path) -> None:
    session = TemporarySession.create(tmp_path)

    assert session.path.parent == tmp_path
    assert session.path.name.startswith(f"{SESSION_PREFIX}{os.getpid()}-")
    assert (session.path / OWNER_LOCK_NAME).is_file()
    (session.path / "work.msh").write_text("x", encoding="utf-8")

    session.close(remove=True)

    assert not session.path.exists()


def test_the_sweep_never_touches_a_live_processs_session(tmp_path: Path) -> None:
    holder, held = _hold_session(tmp_path)
    try:
        (held / "wg2-solver-mesh-build").mkdir()
        # Old by the clock, and still alive: age never overrides a live owner.
        _age(held, LEGACY_MIN_AGE_SECONDS * 2)

        removed = sweep_stale_temporary_directories(tmp_path)

        assert removed == []
        assert (held / "wg2-solver-mesh-build").is_dir()
    finally:
        _release(holder)


def test_a_dead_processs_session_is_swept_at_once(tmp_path: Path) -> None:
    holder, held = _hold_session(tmp_path)
    (held / "wg2-solver-mesh-build").mkdir()
    (held / "wg2-solver-mesh-build" / "waveguide.msh").write_text("x", encoding="utf-8")
    # os._exit: no cleanup ran, exactly as after the backstop or a tree kill.
    _release(holder)
    assert held.is_dir()

    removed = sweep_stale_temporary_directories(tmp_path)

    assert removed == [held]
    assert not held.exists()


def test_the_sweep_keeps_the_callers_own_session(tmp_path: Path) -> None:
    session = TemporarySession.create(tmp_path)
    try:
        assert sweep_stale_temporary_directories(tmp_path, keep=session.path) == []
        # Even unkept, its own lock is held, so it is still alive.
        assert sweep_stale_temporary_directories(tmp_path) == []
        assert session.path.is_dir()
    finally:
        session.close(remove=True)


def test_a_session_still_being_created_is_left_alone(tmp_path: Path) -> None:
    """The instant between ``mkdtemp`` and the lock: no lock file yet."""

    young = tmp_path / f"{SESSION_PREFIX}999999-abcdef"
    young.mkdir()

    assert sweep_stale_temporary_directories(tmp_path) == []
    assert young.is_dir()

    _age(young, LEGACY_MIN_AGE_SECONDS + 60)
    assert sweep_stale_temporary_directories(tmp_path) == [young]


def test_an_unlocked_session_of_a_live_pid_is_left_alone_while_young(tmp_path: Path) -> None:
    """The lock file exists but is not locked yet, and the pid is running.

    That is a process between creating its lock file and locking it. Only once
    the directory is too old for that (a reused pid) is it swept.
    """

    racing = tmp_path / f"{SESSION_PREFIX}{os.getpid()}-abcdef"
    racing.mkdir()
    (racing / OWNER_LOCK_NAME).write_bytes(b"")

    assert sweep_stale_temporary_directories(tmp_path) == []
    _age(racing, 120)
    assert sweep_stale_temporary_directories(tmp_path) == [racing]


@pytest.mark.parametrize("prefix", LEGACY_PREFIXES)
def test_an_earlier_releases_leftovers_are_swept_only_when_old(
    tmp_path: Path, prefix: str
) -> None:
    """Directories from before per-process sessions have no owner to ask.

    One could belong to an older server that is running right now, so only
    age can say it is stale.
    """

    recent = tmp_path / f"{prefix}recent"
    stale = tmp_path / f"{prefix}stale"
    recent.mkdir()
    stale.mkdir()
    (stale / "waveguide.msh").write_text("x", encoding="utf-8")
    _age(stale, LEGACY_MIN_AGE_SECONDS + 60)

    assert sweep_stale_temporary_directories(tmp_path) == [stale]
    assert recent.is_dir()
    assert not stale.exists()


def test_the_sweep_ignores_everything_it_does_not_own(tmp_path: Path) -> None:
    for name in ("wg2-statusapp-x", "wg2-wglink-source-x", "wg-test-data-x", "pymp-x", "other"):
        path = tmp_path / name
        path.mkdir()
        _age(path, LEGACY_MIN_AGE_SECONDS * 10)
    file_with_the_prefix = tmp_path / f"{SESSION_PREFIX}file"
    file_with_the_prefix.write_text("x", encoding="utf-8")

    assert sweep_stale_temporary_directories(tmp_path) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        ["wg2-statusapp-x", "wg2-wglink-source-x", "wg-test-data-x", "pymp-x", "other"]
        + [file_with_the_prefix.name]
    )


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs a privilege on Windows")
def test_the_sweep_never_follows_a_symlink(tmp_path: Path) -> None:
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("x", encoding="utf-8")
    link = tmp_path / f"{LEGACY_PREFIXES[0]}link"
    link.symlink_to(precious, target_is_directory=True)
    _age(precious, LEGACY_MIN_AGE_SECONDS * 2)

    sweep_stale_temporary_directories(tmp_path)

    assert (precious / "keep.txt").is_file()


def test_a_missing_base_sweeps_nothing(tmp_path: Path) -> None:
    assert sweep_stale_temporary_directories(tmp_path / "absent") == []
