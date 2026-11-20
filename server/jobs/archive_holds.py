"""Archive members the server copied for a run archive, kept for its writes.

A run archive reads every retention-managed input in one store transaction
(``JobStore.get_archive_snapshot``). The archive's files are then written by
``POST /api/workspace/write-export``. The mesh, the pressure bases and the
radiation matrix are bytes the server already holds, so instead of sending
them to the browser as base64 and receiving them back, the snapshot keeps them
here under a random hold id, and write-export names them by
``(hold, member)``. They are the very bytes that transaction copied, so a later
retention pass cannot change what the archive writes.

A hold lives until the archive releases it after its last write
(``DELETE /api/jobs/{id}/archive-holds/{hold}``), and at most
``HOLD_TTL_SECONDS``: a timer drops it then even if nothing else touches the
store. The oldest holds go first when more than ``MAX_HOLDS`` exist or their
bytes pass ``MAX_HELD_BYTES``; one hold larger than ``MAX_HELD_BYTES`` is
refused outright. A write naming a hold that is gone is refused, and the
archive takes a new snapshot.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import secrets
import threading
import time
from typing import Any, Protocol

HOLD_TTL_SECONDS = 30 * 60
MAX_HOLDS = 16
MAX_HELD_BYTES = 1024 * 1024 * 1024

#: The members a hold may contain: the mesh, the radiation matrix, and one
#: pressure basis per channel (``pressure_basis/<channel id>``).
MESH_MEMBER = "mesh"
RADIATION_IMPEDANCE_MEMBER = "radiation_impedance"
PRESSURE_BASIS_MEMBER_PREFIX = "pressure_basis/"


def pressure_basis_member(channel_id: str) -> str:
    return f"{PRESSURE_BASIS_MEMBER_PREFIX}{channel_id}"


class HoldTooLarge(ValueError):
    """One snapshot's members exceed what the server keeps for an archive."""


class HoldUnavailable(LookupError):
    """The hold expired, was released or evicted, or belongs to another run."""


class _Cancellable(Protocol):
    def cancel(self) -> None: ...


def _daemon_timer(seconds: float, callback: Callable[[], None]) -> _Cancellable:
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


@dataclass(frozen=True)
class _Hold:
    job_id: str
    members: Mapping[str, bytes]
    expires_at: float
    size: int
    timer: Any


class ArchiveSnapshotHolds:
    """Thread-safe, bounded store of snapshot members by hold id."""

    def __init__(
        self,
        *,
        ttl_seconds: float = HOLD_TTL_SECONDS,
        max_holds: int = MAX_HOLDS,
        max_bytes: int = MAX_HELD_BYTES,
        clock: Callable[[], float] = time.monotonic,
        schedule: Callable[[float, Callable[[], None]], _Cancellable] = _daemon_timer,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_holds = max_holds
        self._max_bytes = max_bytes
        self._clock = clock
        self._schedule = schedule
        self._lock = threading.Lock()
        self._holds: OrderedDict[str, _Hold] = OrderedDict()

    def _drop(self, token: str) -> None:
        hold = self._holds.pop(token, None)
        if hold is not None and hold.timer is not None:
            hold.timer.cancel()

    def _expire(self, now: float) -> None:
        for token in [token for token, hold in self._holds.items() if hold.expires_at <= now]:
            self._drop(token)
        while len(self._holds) > self._max_holds or (
            sum(hold.size for hold in self._holds.values()) > self._max_bytes
        ):
            self._drop(next(iter(self._holds)))

    def _expired(self, token: str) -> None:
        with self._lock:
            self._drop(token)

    def hold(self, job_id: str, members: Mapping[str, bytes]) -> str:
        size = sum(len(content) for content in members.values())
        if size > self._max_bytes:
            raise HoldTooLarge(
                f"This run’s saved files are larger than WG can archive at once "
                f"({size} bytes, over {self._max_bytes}). Use Export on the run to save them."
            )
        token = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._expire(now)
            timer = self._schedule(self._ttl, lambda: self._expired(token))
            self._holds[token] = _Hold(
                job_id=job_id,
                members=dict(members),
                expires_at=now + self._ttl,
                size=size,
                timer=timer,
            )
            self._expire(now)
        return token

    def lookup(self, token: str, job_id: str, member: str) -> bytes:
        """The held bytes of ``member``.

        Raises ``HoldUnavailable`` when the hold is gone or names another run,
        and ``KeyError`` when the live hold has no such member (a client fault).
        """

        with self._lock:
            self._expire(self._clock())
            hold = self._holds.get(token)
            if hold is None or hold.job_id != job_id:
                raise HoldUnavailable(token)
            return hold.members[member]

    def release(self, token: str, job_id: str) -> bool:
        """Drop a hold once its archive is written; ``False`` if it was already gone."""

        with self._lock:
            hold = self._holds.get(token)
            if hold is None or hold.job_id != job_id:
                return False
            self._drop(token)
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._holds)


__all__ = [
    "ArchiveSnapshotHolds",
    "HoldTooLarge",
    "HoldUnavailable",
    "MESH_MEMBER",
    "RADIATION_IMPEDANCE_MEMBER",
    "PRESSURE_BASIS_MEMBER_PREFIX",
    "pressure_basis_member",
]
