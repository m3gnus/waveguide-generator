"""Bounded ephemeral ownership of generated export bytes pending publication."""

from collections import OrderedDict
import secrets
import threading
import time
from typing import Callable


class StagedArtifactStore:
    def __init__(
        self, *, ttl_seconds: float = 300, max_entries: int = 16,
        max_bytes: int = 256 * 1024 * 1024, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry[1])

    def _prune(self) -> None:
        now = self._clock()
        for key, (created, _) in list(self._entries.items()):
            if now - created >= self._ttl:
                self._remove(key)

    def stage(self, payload: bytes) -> str:
        if len(payload) > self._max_bytes:
            raise ValueError("This export exceeds the retained-file size limit. Reduce its size, then export again.")
        with self._lock:
            self._prune()
            while self._entries and (
                len(self._entries) >= self._max_entries or self._bytes + len(payload) > self._max_bytes
            ):
                self._remove(next(iter(self._entries)))
            key = secrets.token_urlsafe(32)
            self._entries[key] = (self._clock(), bytes(payload))
            self._bytes += len(payload)
            return key

    def read(self, key: str) -> bytes:
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None:
                raise ValueError("The generated export is no longer available. Export again to rebuild it.")
            return entry[1]

    def discard(self, key: str) -> None:
        with self._lock:
            self._remove(key)


STAGED_EXPORTS = StagedArtifactStore()
