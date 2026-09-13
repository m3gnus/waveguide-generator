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


def test_activating_a_session_moves_only_wgs_own_temporary_directories(tmp_path: Path) -> None:
    import tempfile

    from server.platform.temp_session import temporary_directory_root

    system = tempfile.gettempdir()
    session = TemporarySession.create(tmp_path)
    try:
        assert temporary_directory_root() is None
        session.activate()
        assert temporary_directory_root() == str(session.path)
        # Nothing global moves: libraries keep cross-process state there.
        assert tempfile.gettempdir() == system
    finally:
        session.close(remove=True)
    assert temporary_directory_root() is None


def test_beats_worker_registry_stays_where_the_next_launch_looks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persistent BEAT host is adopted through a registry under the system
    temporary directory. A session must not move it: a swept registry strands
    a live host and makes every launch pay a cold start.
    """

    registry = pytest.importorskip("hornlab_beat_bem.worker_registry")
    monkeypatch.delenv("HORNLAB_BEAT_WORKER_DIR", raising=False)
    before = Path(registry.worker_dir())
    session = TemporarySession.create(tmp_path)
    session.activate()
    try:
        during = Path(registry.worker_dir())
    finally:
        session.close(remove=True)

    assert during == before
    assert not during.is_relative_to(session.path)


def test_a_creator_waits_out_a_sweep_testing_its_new_lock(tmp_path: Path) -> None:
    """Two starts at once: one's sweep may hold the other's fresh lock for an instant."""

    from server.platform import temp_session as module

    real_lock = module.lock_exclusive
    attempts: list[int] = []

    def held_by_a_sweep_twice(descriptor: int) -> None:
        attempts.append(descriptor)
        if len(attempts) <= 2:
            raise BlockingIOError("held by another start's sweep")
        real_lock(descriptor)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "lock_exclusive", held_by_a_sweep_twice)
    try:
        session = TemporarySession.create(tmp_path)
    finally:
        monkeypatch.undo()
    try:
        assert len(attempts) == 3
        assert session.path.is_dir()
        assert (session.path / OWNER_LOCK_NAME).is_file()
    finally:
        session.close(remove=True)


def test_a_lock_that_stays_held_is_a_failure_that_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.platform import temp_session as module

    def always_held(_descriptor: int) -> None:
        raise BlockingIOError("held")

    monkeypatch.setattr(module, "lock_exclusive", always_held)
    monkeypatch.setattr(module, "LOCK_RETRY_SECONDS", 0.0)

    with pytest.raises(BlockingIOError):
        TemporarySession.create(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_every_wg_temporary_directory_is_made_in_the_session() -> None:
    """A ``wg2-*`` ``TemporaryDirectory`` outside the session is swept a day late.

    Each call site that names a ``wg2-`` prefix must pass
    ``dir=temporary_directory_root()``. The one exception is owned by the
    solver work and is covered by the one-day rule instead.
    """

    import ast

    exempt = {
        "server/solver/field_plane.py": "solver-owned; its wg2-field-plane-* sweep after a day",
    }

    def callee(node: ast.AST) -> str | None:
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
        return None

    offenders: list[str] = []
    sites = 0
    for path in sorted((REPO_ROOT / "server").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("server/tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or callee(node.func) not in {
                "TemporaryDirectory",
                "mkdtemp",
            }:
                continue
            prefix = next(
                (
                    keyword.value.value
                    for keyword in node.keywords
                    if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant)
                ),
                None,
            )
            if not (isinstance(prefix, str) and prefix.startswith("wg2-")):
                continue
            sites += 1
            if relative in exempt:
                assert prefix.startswith(LEGACY_PREFIXES), f"{relative}: {prefix} is never swept"
                continue
            directory = next((k.value for k in node.keywords if k.arg == "dir"), None)
            if not (
                isinstance(directory, ast.Call)
                and callee(directory.func) == "temporary_directory_root"
            ):
                offenders.append(f"{relative}:{node.lineno} ({prefix})")

    assert sites >= 4, "the scan found none of WG's own temporary directories"
    assert offenders == []
    for relative in exempt:
        assert (REPO_ROOT / relative).is_file(), f"stale exemption: {relative}"
