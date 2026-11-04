"""Small, process-local cache for immutable solver-mesh artifacts.

The authoritative artifact remains the exported Gmsh text.  This cache only
avoids rebuilding that same artifact while the server process is alive; it is
bounded so a series of large designs cannot grow server memory indefinitely.
"""

from __future__ import annotations

import copy
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_CACHE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_CACHE_ENTRIES = 8


@dataclass(frozen=True, slots=True)
class SolverMeshCacheInfo:
    entries: int
    bytes: int
    max_entries: int
    max_bytes: int


class SolverMeshArtifactCache:
    """Thread-safe byte- and entry-bounded LRU of solver artifact mappings."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        if max_entries < 0 or max_bytes < 0:
            raise ValueError("solver mesh cache limits must not be negative")
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._entries: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def _estimated_size(result: dict[str, Any]) -> int:
        # MSH text dominates by orders of magnitude.  ``getsizeof`` reflects
        # CPython's compact ASCII representation without allocating a second
        # encoded copy; the metadata allowance covers the small nested stats.
        msh_text = result.get("msh_text", "")
        text_size = sys.getsizeof(msh_text) if isinstance(msh_text, str) else 0
        return text_size + 32 * 1024

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            # Validation policy mutates warnings/stats after lookup.  A copy
            # keeps those request-local changes out of future cache hits.
            return copy.deepcopy(entry[0])

    def put(self, key: str, result: dict[str, Any]) -> bool:
        stored = copy.deepcopy(result)
        size = self._estimated_size(stored)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]

            if self._max_entries == 0 or self._max_bytes == 0 or size > self._max_bytes:
                return False

            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._bytes + size > self._max_bytes
            ):
                _, (_, evicted_size) = self._entries.popitem(last=False)
                self._bytes -= evicted_size

            self._entries[key] = (stored, size)
            self._bytes += size
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def info(self) -> SolverMeshCacheInfo:
        with self._lock:
            return SolverMeshCacheInfo(
                entries=len(self._entries),
                bytes=self._bytes,
                max_entries=self._max_entries,
                max_bytes=self._max_bytes,
            )


__all__ = [
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_MAX_CACHE_ENTRIES",
    "SolverMeshArtifactCache",
    "SolverMeshCacheInfo",
]
