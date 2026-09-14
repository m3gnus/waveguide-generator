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
import stat
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
    remove_tree,
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


def test_a_dead_processs_cad_sandbox_is_swept_with_its_read_only_step(tmp_path: Path) -> None:
    """A forced exit during an external-STEP import leaves the isolated CAD
    child's sandbox in the session. Its staged STEP is read-only, which on
    Windows a plain ``rmtree`` cannot delete."""

    holder, held = _hold_session(tmp_path)
    staged = held / "wg-cad-child-import" / "input" / "source.step"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"ISO-10303-21;")
    staged.chmod(0o400)
    _release(holder)

    removed = sweep_stale_temporary_directories(tmp_path)

    assert removed == [held]
    assert not held.exists()


def test_a_tree_that_cannot_be_removed_raises_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The isolated CAD child's cleanup runs in a ``finally``: a failure to
    remove its sandbox must not replace the child's own outcome or refusal."""

    tree = tmp_path / "wg-cad-child-stuck"
    (tree / "input").mkdir(parents=True)
    (tree / "input" / "source.step").write_bytes(b"ISO-10303-21;")

    def refuse(path: object, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "held open by another process", str(path))

    monkeypatch.setattr(os, "unlink", refuse)

    remove_tree(tree)

    monkeypatch.undo()
    assert (tree / "input" / "source.step").is_file()


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions and symlinks")
def test_a_permission_retry_never_changes_what_a_symlink_points_at(tmp_path: Path) -> None:
    """Removal fixes the entry it failed on, never the entry's target.

    An unlink fails with ``PermissionError`` when the directory holding the
    entry is not writable. The retry made the entry 0600 first, and a plain
    ``os.chmod`` follows a symlink: a link in a locked directory handed its
    target -- a file, or a home directory losing its search bit -- a new mode.
    It is the pattern CPython fixed in ``tempfile`` as CVE-2023-6597.
    """

    outside = tmp_path / "outside"
    outside.mkdir()
    target_file = outside / "notes.txt"
    target_file.write_text("x", encoding="utf-8")
    target_file.chmod(0o444)
    target_dir = outside / "home"
    target_dir.mkdir()
    target_dir.chmod(0o755)
    tree = tmp_path / f"{LEGACY_PREFIXES[0]}planted"
    locked = tree / "locked"
    locked.mkdir(parents=True)
    (locked / "to-file").symlink_to(target_file)
    (locked / "to-dir").symlink_to(target_dir, target_is_directory=True)
    locked.chmod(0o500)
    try:
        remove_tree(tree)

        assert stat.S_IMODE(target_file.stat().st_mode) == 0o444
        assert stat.S_IMODE(target_dir.stat().st_mode) == 0o755
        assert target_file.read_text(encoding="utf-8") == "x"
    finally:
        if locked.is_dir():
            locked.chmod(0o700)
        target_file.chmod(0o644)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
def test_the_sweep_leaves_another_users_entries_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The system temporary directory is shared on POSIX.

    An old directory under one of WG's names that another user made is still
    theirs: this process cannot ask its owner, may not remove it, and must not
    change modes inside it. That holds for a leftover from before sessions and
    for a session directory with no lock to test alike.
    """

    legacy = tmp_path / f"{LEGACY_PREFIXES[0]}theirs"
    session = tmp_path / f"{SESSION_PREFIX}999999-theirs"
    for path in (legacy, session):
        path.mkdir()
        (path / "waveguide.msh").write_text("x", encoding="utf-8")
        _age(path, LEGACY_MIN_AGE_SECONDS * 2)
    real_uid = os.getuid()

    with monkeypatch.context() as another_user:
        another_user.setattr(os, "getuid", lambda: real_uid + 1)
        assert sweep_stale_temporary_directories(tmp_path) == []
    assert legacy.is_dir()
    assert session.is_dir()

    # The same entries, this user's own, are swept exactly as before.
    assert sorted(sweep_stale_temporary_directories(tmp_path)) == sorted([legacy, session])


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


def test_no_server_temporary_file_lands_loose_in_the_system_temporary_directory() -> None:
    """A forced exit leaves every temporary file and directory open at that moment.

    Loose in the system temporary directory, no rule removes one -- unless it
    carries a legacy ``wg2-`` directory prefix, and then only a day later. So
    every ``tempfile`` call in the server names its ``dir=``: the session's
    (``temporary_directory_root()``) for scratch space -- a mesh build, the mesh
    a solver reads, a STEP round trip, the isolated CAD child's sandbox -- or
    the destination's own parent for a file staged beside where it is
    published. A ``wg2-`` directory and a ``wg-cad-child-`` sandbox always go
    in the session.
    """

    import ast

    #: Each call's positional slot for ``dir``: a ``dir`` passed there cannot be
    #: read by name, while an earlier positional (a ``mode``) is harmless.
    dir_slot = {"TemporaryDirectory": 2, "mkdtemp": 2, "mkstemp": 2, "NamedTemporaryFile": 6}

    def callee(node: ast.AST) -> str | None:
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
        return None

    def calls(node: ast.AST | None, name: str) -> bool:
        return isinstance(node, ast.Call) and callee(node.func) == name

    offenders: list[str] = []
    sites = 0
    for path in sorted((REPO_ROOT / "server").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("server/tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = callee(node.func) if isinstance(node, ast.Call) else None
            if name not in dir_slot:
                continue
            sites += 1
            where = f"{relative}:{node.lineno}"
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            directory = keywords.get("dir")
            prefix = keywords.get("prefix")
            if directory is None and (
                len(node.args) > dir_slot[name]
                or any(isinstance(argument, ast.Starred) for argument in node.args)
            ):
                offenders.append(f"{where}: pass dir= by keyword so this check can read it")
            elif directory is None or (
                isinstance(directory, ast.Constant) and directory.value is None
            ):
                offenders.append(f"{where}: no dir=, so the system temporary directory")
            elif calls(directory, "gettempdir"):
                offenders.append(f"{where}: dir=gettempdir() is the system temporary directory")
            elif (
                isinstance(prefix, ast.Constant)
                and isinstance(prefix.value, str)
                and prefix.value.startswith(("wg2-", "wg-cad-child-"))
                and not calls(directory, "temporary_directory_root")
            ):
                offenders.append(f"{where}: {prefix.value} outside the session")

    assert sites >= 15, f"the scan found only {sites} temporary-file calls in the server"
    assert offenders == []


def _active_session(tmp_path: Path) -> TemporarySession:
    session = TemporarySession.create(tmp_path)
    session.activate()
    return session


def test_a_solvers_mesh_file_is_written_inside_the_active_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``.msh`` a native solver reads is ``delete=False``: a forced exit
    mid-solve leaves it. Inside the session, the next start sweeps it."""

    from types import SimpleNamespace

    from server.solver import metal

    seen: list[Path] = []

    def solve_frequencies(path: str, _frequencies: list[float], _config: object) -> object:
        seen.append(Path(path))
        assert Path(path).read_text(encoding="utf-8") == "msh"
        return SimpleNamespace()

    monkeypatch.setattr(metal, "native_solve_frequencies", solve_frequencies)
    session = _active_session(tmp_path)
    try:
        metal._native_solve_mesh("msh", [1000.0], object(), sort_after=False)
        assert [path.parent for path in seen] == [session.path]
        assert not seen[0].exists()
    finally:
        session.close(remove=True)


def test_a_field_plane_mesh_is_staged_inside_the_active_session(tmp_path: Path) -> None:
    from collections import OrderedDict
    from types import SimpleNamespace

    from server.solver.field_plane import FieldPlaneService
    from server.solver.field_traces_store import METAL_FIELD_TRACE_BACKEND

    loaded: list[Path] = []

    def load_mesh(path: Path, **_kwargs: object) -> object:
        loaded.append(Path(path))
        return object()

    cache = SimpleNamespace(_mesh_cache=OrderedDict(), _mesh_cache_entries=1)
    session = _active_session(tmp_path)
    try:
        FieldPlaneService._cached_mesh(
            cache,
            METAL_FIELD_TRACE_BACKEND,
            SimpleNamespace(load_mesh=load_mesh),
            "job",
            "sha",
            "msh",
            None,
        )
        assert len(loaded) == 1
        assert loaded[0].parent.parent == session.path
        assert loaded[0].parent.name.startswith("wg2-field-plane-")
    finally:
        session.close(remove=True)
