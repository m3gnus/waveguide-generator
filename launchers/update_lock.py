"""One exclusive claim on an installation's update, taken by everyone who moves it.

A dwell is not an interlock. An update in progress and an interrupted one look
identical from outside -- ``app`` really is absent between two renames -- so a
bootstrap recovery that only *waits* still races an updater slower than the
wait, and the two then rename the same directories from two processes.

This is the claim both sides take instead. It is an OS-level exclusive lock on
one file per installation, which matters more than the file: the kernel drops
it when the holder's descriptor closes, so a killed updater releases it by
dying rather than leaving a stale claim that blocks recovery forever -- which
is the failure a lock file with a pid inside it would introduce into exactly
the case this exists to fix.

Who takes it:

* the updater CLI, for the whole of an ``apply``/``rollback`` transaction --
  the only operations that move a layer on purpose;
* the bootstrap recovery in :mod:`bundle_recovery`, before it runs anything,
  and it **fails closed**: an installation whose update is still owned by a
  live process is left alone and the user is told to start again in a moment.

Deliberately not taken by ``--recover`` itself, because the bootstrap holds it
across that subprocess; a helper that re-acquired it would deadlock against its
own caller.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator


LOCK_DIRECTORY = "updates"
LOCK_NAME = "update.lock"

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - taken only on Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - taken only on POSIX
    msvcrt = None  # type: ignore[assignment]


class UpdateInProgress(RuntimeError):
    """Another process owns this installation's update right now."""


def lock_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / LOCK_DIRECTORY / LOCK_NAME


def _acquire(handle: int) -> bool:
    """Take the exclusive lock without blocking, or report that it is held."""

    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    if msvcrt is not None:  # pragma: no cover - Windows only
        try:
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    # No locking primitive at all. Refusing is the only safe answer: the caller
    # asked for exclusion and cannot be told it has it.
    return False  # pragma: no cover - no supported platform lacks both


def _release(handle: int) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        return
    if msvcrt is not None:  # pragma: no cover - Windows only
        try:
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def claim_update(data_dir: str | os.PathLike[str]) -> Iterator[Path]:
    """Own this installation's update for the duration, or refuse to start.

    Raises :class:`UpdateInProgress` when another process holds it, and
    ``OSError`` when the lock file itself cannot be created -- neither of which
    may be answered by proceeding, because the caller is about to move
    directories another process may be moving.
    """

    path = lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not _acquire(descriptor):
            raise UpdateInProgress(
                f"another process is updating this installation ({path})"
            )
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            # The claim is the lock, not the note in the file. A pid that could
            # not be written costs a diagnostic and nothing else.
            pass
        try:
            yield path
        finally:
            _release(descriptor)
    finally:
        os.close(descriptor)


def locking_is_available() -> bool:
    """Whether this interpreter has a locking primitive at all."""

    return fcntl is not None or msvcrt is not None


__all__ = [
    "UpdateInProgress",
    "claim_update",
    "lock_path",
    "locking_is_available",
]
