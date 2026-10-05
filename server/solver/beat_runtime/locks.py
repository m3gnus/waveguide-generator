"""Provisioning exclusion on a persistent inode; holder records are diagnostics."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import errno
import os
from pathlib import Path
import time

from . import paths, state

LOCK_FILENAME = "provision.lock"
HOLDER_FILENAME = "provision.holder.json"
_LOCK_POLL_S = 0.25
_WINDOWS = os.name == "nt"


def _try_lock(descriptor: int) -> bool:
    """Retry only contention; unsupported advisory locking must fail promptly."""
    try:
        if _WINDOWS:
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def _unlock(descriptor: int) -> None:
    # Closing the descriptor also releases the kernel lock, including on errors.
    with suppress(OSError):
        if _WINDOWS:
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)


def lock_holder(directory: Path | None = None) -> dict[str, object]:
    """Read a possibly stale diagnostic; never use its PID for signaling."""
    directory = paths.runtime_dir() if directory is None else Path(directory)
    record = state._read_json(directory / HOLDER_FILENAME)
    if record is None or record.get("provider") != paths.PROVIDER_ID:
        return {}
    return record


@contextmanager
def provisioning_lock(
    directory: Path | None = None, *, backend: str,
    status_cb: Callable[[str], None] | None = None,
) -> Iterator[None]:
    """Serialize all backends sharing portable Julia; never unlink the lock.

    Polling keeps waits interruptible. Death releases the kernel lock, even if
    holder JSON remains. Provisioning orchestration records acquisition errors.
    """
    directory = paths.runtime_dir() if directory is None else Path(directory)
    state.backend_state_path(directory, backend=backend)  # Validate before creating files.
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        # Windows locks exactly byte zero, which must exist. Never truncate:
        # a waiter may already have this inode open, and its contents stay put.
        if os.fstat(descriptor).st_size == 0:
            try:
                os.write(descriptor, b"\0")
            except OSError as exc:
                # A competing initializer may have filled and locked byte zero
                # since our size check. Proceed to the normal contention loop.
                if not (_WINDOWS and exc.errno in (errno.EACCES, errno.EAGAIN)
                        and os.fstat(descriptor).st_size >= 1):
                    raise
        announced = False
        while not _try_lock(descriptor):
            if not announced:
                announced = True
                other = lock_holder(directory).get("backend") or "another"
                if status_cb is not None:
                    # A diagnostic must not affect exclusion. PR 10 owns the
                    # reported-once guard for all provisioning status lines.
                    with suppress(Exception):
                        status_cb(f"Waiting for the BEAT {other} runtime provisioning to finish.")
            time.sleep(_LOCK_POLL_S)
        try:
            with suppress(OSError):
                state._atomic_write_json(directory / HOLDER_FILENAME, {
                    "provider": paths.PROVIDER_ID, "state_schema": paths.STATE_SCHEMA,
                    "pid": os.getpid(), "backend": backend,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                })
            yield
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)
