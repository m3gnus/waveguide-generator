"""Archive members the server copied for a run archive, kept for its writes.

A run archive reads every retention-managed input in one store transaction
(``JobStore.get_archive_snapshot``). The archive's files are then written in
separate ``POST /api/workspace/write-export`` calls. The mesh, the pressure
bases and the radiation matrix are bytes the server already holds, so instead
of sending them to the browser as base64 and receiving them back, the snapshot
keeps them here under a random hold id, and write-export names them by
``(hold, member)``. They are the very bytes that transaction copied, so a later
retention pass cannot change what the archive writes.

A hold expires after ``HOLD_TTL_SECONDS``, and the oldest holds go first when
more than ``MAX_HOLDS`` exist or their bytes pass ``MAX_HELD_BYTES``. A write
naming an expired hold is refused; the archive is retried and takes a new
snapshot.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
import secrets
import threading
import time

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


@dataclass(frozen=True)
class _Hold:
    job_id: str
    members: Mapping[str, bytes]
    expires_at: float
    size: int


class ArchiveSnapshotHolds:
    """Thread-safe, bounded store of snapshot members by hold id."""

    def __init__(
        self,
        *,
        ttl_seconds: float = HOLD_TTL_SECONDS,
        max_holds: int = MAX_HOLDS,
        max_bytes: int = MAX_HELD_BYTES,
        clock=time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_holds = max_holds
        self._max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._holds: OrderedDict[str, _Hold] = OrderedDict()

    def _expire(self, now: float) -> None:
        for token in [token for token, hold in self._holds.items() if hold.expires_at <= now]:
            del self._holds[token]
        while len(self._holds) > self._max_holds or (
            len(self._holds) > 1 and sum(hold.size for hold in self._holds.values()) > self._max_bytes
        ):
            self._holds.popitem(last=False)

    def hold(self, job_id: str, members: Mapping[str, bytes]) -> str:
        token = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._holds[token] = _Hold(
                job_id=job_id,
                members=dict(members),
                expires_at=now + self._ttl,
                size=sum(len(content) for content in members.values()),
            )
            self._expire(now)
        return token

    def get(self, token: str, job_id: str, member: str) -> bytes | None:
        """The held bytes, or ``None`` when the hold is gone or names another run."""

        with self._lock:
            self._expire(self._clock())
            hold = self._holds.get(token)
            if hold is None or hold.job_id != job_id:
                return None
            return hold.members.get(member)


__all__ = [
    "ArchiveSnapshotHolds",
    "MESH_MEMBER",
    "RADIATION_IMPEDANCE_MEMBER",
    "PRESSURE_BASIS_MEMBER_PREFIX",
    "pressure_basis_member",
]
