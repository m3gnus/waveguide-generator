"""A temporary directory per server process, and the startup sweep of dead ones.

A server that ends through the shutdown backstop's ``os._exit`` or the
launcher's tree kill runs no cleanup. Every ``tempfile.TemporaryDirectory``
open at that moment -- a mesh build's ``wg2-solver-mesh-*``, an STL export's,
an imported mesh's -- then stays in the system temporary directory for good,
and a Quit during a build always ends that way.

So ``launch/serve.py`` gives the process one directory of its own,
``wg2-run-<pid>-<random>``, and holds an OS lock on the ``.owner.lock`` inside
it for as long as the process lives. WG's own ``TemporaryDirectory`` call
sites make their directories inside it (``dir=temporary_directory_root()``).
The operating system releases that lock however the process ends, so a later
start can tell a dead owner's directory from a live one exactly -- whichever
build, checkout or data directory the owner belonged to -- and it sweeps only
the dead ones.

Nothing global moves: ``tempfile.gettempdir()`` still answers the system
directory. Libraries keep state there that must outlive this process --
``hornlab_beat_bem`` the registry through which the next launch adopts the
persistent BEAT host, ``hornlab_mesher`` its publish lock -- and a swept
registry would strand a live host.

Directories that have no session to belong to -- an earlier release's, or a
call site outside WG's own code -- have no owner to ask, and one may belong to
a server that is running right now. They are removed only once nothing has
changed them for a day.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import tempfile
import time

from server.platform.instance import LOCK_OPEN_FLAGS, lock_exclusive, pid_is_running, unlock


SESSION_PREFIX = "wg2-run-"
OWNER_LOCK_NAME = ".owner.lock"

#: What releases before per-process sessions left directly in the temporary
#: directory: the server's own ``TemporaryDirectory`` prefixes.
LEGACY_PREFIXES = (
    "wg2-solver-mesh-",
    "wg2-imported-mesh-",
    "wg2-imported-viewport-",
    "wg2-field-plane-",
    "wg2-stl-mesh-",
)
LEGACY_MIN_AGE_SECONDS = 24 * 3600.0

#: A session whose lock is free while its pid runs is either still being
#: created (between creating its lock file and locking it) or belongs to a
#: reused pid. Only age tells those apart.
CREATION_GRACE_SECONDS = 60.0

#: A starting server's sweep tests another session's lock for an instant, so a
#: creator that meets it held tries again rather than giving up its directory.
LOCK_ATTEMPTS = 50
LOCK_RETRY_SECONDS = 0.01

log = logging.getLogger("wg.temp")

#: The directory :func:`temporary_directory_root` answers, while a session is active.
_active_root: str | None = None


def temporary_directory_root() -> str | None:
    """Where WG makes its own temporary directories: ``dir=`` for ``tempfile``.

    The active session's directory in a launched server, so a directory a
    stop leaves behind is swept by the next start; ``None`` (the system
    temporary directory) everywhere else -- tests, the CLI, the CAD child.
    """

    return _active_root


class TemporarySession:
    """This process's own temporary directory, locked for its lifetime."""

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self._descriptor: int | None = descriptor
        self._active = False

    @classmethod
    def create(cls, base: Path | None = None) -> TemporarySession:
        """Create and lock a new session directory in ``base`` (default: the system's)."""

        parent = Path(tempfile.gettempdir() if base is None else base)
        path = Path(tempfile.mkdtemp(prefix=f"{SESSION_PREFIX}{os.getpid()}-", dir=parent))
        try:
            # Non-inheritable (PEP 446), so no child can keep a dead owner's
            # lock held and its directory unswept.
            descriptor = os.open(path / OWNER_LOCK_NAME, LOCK_OPEN_FLAGS, 0o600)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)
            raise
        for attempt in range(LOCK_ATTEMPTS):
            try:
                lock_exclusive(descriptor)
                break
            except BlockingIOError:
                # Another start's sweep testing this new lock; it lets go at once.
                if attempt + 1 == LOCK_ATTEMPTS:
                    os.close(descriptor)
                    shutil.rmtree(path, ignore_errors=True)
                    raise
                time.sleep(LOCK_RETRY_SECONDS)
            except OSError:
                os.close(descriptor)
                shutil.rmtree(path, ignore_errors=True)
                raise
        return cls(path, descriptor)

    def activate(self) -> None:
        """Make this the directory :func:`temporary_directory_root` answers."""

        global _active_root
        _active_root = str(self.path)
        self._active = True

    def close(self, *, remove: bool) -> None:
        """Stop answering for WG's temporary directories, release the lock and,
        if asked, remove the directory.

        Remove only when nothing can still be writing there. A process that is
        about to end with a native call still running leaves it for the next
        start's sweep instead.
        """

        global _active_root
        if self._active:
            if _active_root == str(self.path):
                _active_root = None
            self._active = False
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            try:
                unlock(descriptor)
            except OSError:
                pass
            os.close(descriptor)
        if remove:
            shutil.rmtree(self.path, ignore_errors=True)


def _owner_pid(name: str) -> int | None:
    pid, _separator, _rest = name[len(SESSION_PREFIX):].partition("-")
    return int(pid) if pid.isdigit() else None


def _session_is_stale(path: Path, age: float) -> bool:
    try:
        descriptor = os.open(path / OWNER_LOCK_NAME, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        # Created and not yet locked, or damaged: only age can tell.
        return age >= LEGACY_MIN_AGE_SECONDS
    except OSError:
        # Another user's, or unreadable. Not this process's to judge.
        return False
    try:
        try:
            lock_exclusive(descriptor)
        except OSError:
            return False
        try:
            unlock(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)
    owner = _owner_pid(path.name)
    if owner is not None and pid_is_running(owner) and age < CREATION_GRACE_SECONDS:
        return False
    return True


def sweep_stale_temporary_directories(
    base: Path, *, keep: Path | None = None, now: float | None = None
) -> list[Path]:
    """Remove what dead server processes left in ``base``; return what went.

    Never raises and never follows a symlink. Anything whose name it does not
    own is left alone.
    """

    current = time.time() if now is None else now
    removed: list[Path] = []
    try:
        entries = sorted(os.scandir(base), key=lambda entry: entry.name)
    except OSError:
        return removed
    for entry in entries:
        name = entry.name
        session = name.startswith(SESSION_PREFIX)
        if not session and not name.startswith(LEGACY_PREFIXES):
            continue
        path = Path(entry.path)
        if keep is not None and path == keep:
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            age = current - entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        if session:
            if not _session_is_stale(path, age):
                continue
        elif age < LEGACY_MIN_AGE_SECONDS:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.lexists(path):
            removed.append(path)
    return removed


__all__ = [
    "CREATION_GRACE_SECONDS",
    "LEGACY_MIN_AGE_SECONDS",
    "LEGACY_PREFIXES",
    "OWNER_LOCK_NAME",
    "SESSION_PREFIX",
    "TemporarySession",
    "sweep_stale_temporary_directories",
    "temporary_directory_root",
]
