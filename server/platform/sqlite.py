"""Shared SQLite connection setup, and the journal-mode capability probe.

``PRAGMA journal_mode = WAL`` is a *request*. SQLite answers with the mode it
actually selected, and it stays in ``delete`` when the file's filesystem cannot
give it what WAL needs -- shared memory and byte-range locks. A data directory
on an SMB/NFS share, a synced folder, or a container bind mount is the ordinary
way that happens, and ``WG2_DATA_DIR`` is exactly the knob that puts it there.

Discarding the answer left two beliefs unearned at once. The concurrency story
is the smaller one: without WAL a reader blocks a writer, so contention shows up
as ``database is locked`` after the busy timeout instead of never. The sharper
one is durability. ``synchronous = NORMAL`` is safe *because* of WAL -- a power
cut can lose the last commits but cannot corrupt the database. In rollback-
journal mode the same setting can leave a corrupt file, which is not a trade
anyone chose. So when WAL is refused this raises ``synchronous`` back to
``FULL``: slower, and correct, which is the right way round.

It is a warning rather than a failure because the application is fully correct
on a rollback journal; it is only slower. Refusing to start would turn a working
network home directory into an unusable install. It is reported the way the
solve backends report a missing runtime -- an ``available``/``reason`` probe
(see ``scripts/check_backends.py``) surfaced by ``/api/capabilities`` -- so a
degraded store is something you can read off the running app rather than
something you infer from a stall.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
import threading


logger = logging.getLogger("wg.sqlite")

MEMORY_DB = ":memory:"
_BUSY_TIMEOUT_MS = 5000

WAL_REFUSED_REMEDY = (
    "SQLite could not enable write-ahead logging for this database, which "
    "usually means the data directory is on a filesystem without shared "
    "memory or byte-range locks (a network share, a synced folder, or some "
    "container mounts). Waveguide Generator still runs correctly and now "
    "uses synchronous=FULL to keep the same crash guarantee, but writes are "
    "much slower and concurrent access can time out. Point WG2_DATA_DIR at a "
    "local disk to get the fast path back."
)


@dataclass(frozen=True, slots=True)
class JournalModeStatus:
    """One store's realized journal mode, in the shape the probes use."""

    label: str
    db_path: str
    journal_mode: str
    available: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.label,
            "path": self.db_path,
            "journalMode": self.journal_mode,
            "available": self.available,
            "reason": self.reason,
        }


_statuses_lock = threading.Lock()
_statuses: dict[tuple[str, str], JournalModeStatus] = {}


def _record(status: JournalModeStatus) -> None:
    """Keep the newest status per store, warning once per distinct outcome."""

    key = (status.label, status.db_path)
    with _statuses_lock:
        previous = _statuses.get(key)
        _statuses[key] = status
        already_warned = (
            previous is not None
            and not previous.available
            and previous.journal_mode == status.journal_mode
        )
    if not status.available and not already_warned:
        logger.warning(
            "%s: SQLite refused WAL for %s and is using journal_mode=%s. %s",
            status.label,
            status.db_path,
            status.journal_mode,
            WAL_REFUSED_REMEDY,
        )


def journal_mode_statuses() -> list[dict[str, object]]:
    """Report every store this process has opened, newest state per store."""

    with _statuses_lock:
        values = sorted(_statuses.values(), key=lambda status: (status.label, status.db_path))
    return [status.as_dict() for status in values]


def reset_journal_mode_statuses() -> None:
    """Drop recorded statuses so a test starts from a known slate."""

    with _statuses_lock:
        _statuses.clear()


def configure_connection(
    conn: sqlite3.Connection, *, db_path: str, label: str
) -> JournalModeStatus:
    """Apply the shared store pragmas and report the journal mode actually granted."""

    row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    mode = str(row[0]).strip().lower() if row is not None else "unknown"
    wal = mode == "wal"
    # An in-memory database has no journal file to write, so SQLite answers
    # "memory" and there is nothing degraded about it.
    in_memory = db_path == MEMORY_DB or mode == "memory"
    conn.execute(f"PRAGMA synchronous = {'NORMAL' if wal else 'FULL'}")
    # Without this any contention raises "database is locked" immediately. The
    # store's RLock makes that unreachable today, which is precisely why it
    # should not be the only thing standing between us and that error.
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")

    if wal:
        reason = "write-ahead logging enabled"
    elif in_memory:
        reason = "in-memory database; journaling does not apply"
    else:
        reason = WAL_REFUSED_REMEDY
    status = JournalModeStatus(
        label=label,
        db_path=db_path,
        journal_mode=mode,
        available=wal or in_memory,
        reason=reason,
    )
    _record(status)
    return status


__all__ = [
    "JournalModeStatus",
    "MEMORY_DB",
    "WAL_REFUSED_REMEDY",
    "configure_connection",
    "journal_mode_statuses",
    "reset_journal_mode_statuses",
]
