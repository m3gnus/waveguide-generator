"""Caches for immutable solver-mesh artifacts.

The authoritative artifact remains the exported Gmsh text. The process-local
cache avoids rebuilding that same artifact while the server process is alive;
it is bounded so a series of large designs cannot grow server memory
indefinitely. The disk cache keeps the same artifacts, under the same keys,
across restarts.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_CACHE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_CACHE_ENTRIES = 8

#: Under the application data folder: solver meshes kept across restarts.
SOLVER_MESH_DISK_CACHE_DIRNAME = "solver-mesh-cache"
DEFAULT_MAX_DISK_CACHE_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_DISK_CACHE_ENTRIES = 256
_DISK_FORMAT = 1

log = logging.getLogger("wg.mesh")


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
        self._builds: dict[str, tuple[threading.Lock, int]] = {}

    @asynccontextmanager
    async def build_slot(self, key: str, check: Callable[[], None]) -> AsyncIterator[None]:
        """Serialize cache misses across requests, threads and event loops.

        Waiters keep their own cancellation checkpoints. A cancelled builder
        releases ownership so another request can retry using its own callback.
        """
        with self._lock:
            lock, users = self._builds.get(key, (threading.Lock(), 0))
            self._builds[key] = lock, users + 1
        acquired = False
        try:
            while True:
                check()
                acquired = lock.acquire(blocking=False)
                if acquired:
                    break
                await asyncio.sleep(0.05)
            check()
            yield
        finally:
            if acquired:
                lock.release()
            with self._lock:
                _, users = self._builds[key]
                if users == 1:
                    del self._builds[key]
                else:
                    self._builds[key] = lock, users - 1

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


def _same_json(value: Any, loaded: Any) -> bool:
    """Whether ``loaded`` is ``value`` read back from JSON, types included.

    Equality alone is not enough: ``(1, 2) != [1, 2]`` but ``1 == 1.0`` and
    ``True == 1``. A disk hit must hand back exactly what the build returned.
    """

    if type(value) is not type(loaded):
        return False
    if isinstance(value, dict):
        return (
            all(isinstance(key, str) for key in value)
            and list(value) == list(loaded)
            and all(_same_json(value[key], loaded[key]) for key in value)
        )
    if isinstance(value, list):
        return len(value) == len(loaded) and all(
            _same_json(item, other) for item, other in zip(value, loaded)
        )
    if isinstance(value, float) and value != value:
        return loaded != loaded
    return value == loaded


class SolverMeshDiskCache:
    """Solver artifacts on disk, keyed like :class:`SolverMeshArtifactCache`.

    One JSON file per key holds the whole artifact and the digest of its mesh
    text. Files live in a folder named for the generator (mesher version and
    source identity, gmsh version, cache format), so a mesher pin change starts
    an empty folder and :meth:`open` deletes the others. A file that does not
    parse, names another key or fails its digest is deleted and read as a
    miss, so the mesh is rebuilt. The folder is held under ``max_bytes`` and
    ``max_entries`` by evicting the least recently used files.

    An artifact is written only when it reads back identical, types included;
    anything JSON cannot carry exactly stays memory-only.
    """

    def __init__(
        self,
        root: Path,
        generator: dict[str, Any],
        *,
        max_entries: int = DEFAULT_MAX_DISK_CACHE_ENTRIES,
        max_bytes: int = DEFAULT_MAX_DISK_CACHE_BYTES,
    ) -> None:
        self._generator = {"disk_format": _DISK_FORMAT, **generator}
        from server.contracts.canonical import canonical_ascii_strict_json

        canonical = canonical_ascii_strict_json(self._generator)
        self._root = Path(root)
        self._folder = self._root / hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._lock = threading.Lock()

    @property
    def folder(self) -> Path:
        return self._folder

    def open(self) -> None:
        """Create this generator's folder and delete every other generator's."""

        self._folder.mkdir(parents=True, exist_ok=True)
        for entry in self._root.iterdir():
            if entry != self._folder and entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("solver mesh disk keys are 64 lowercase hex digits")
        return self._folder / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("solver mesh disk cache: cannot read %s (%s)", path.name, exc)
            return None
        try:
            record = json.loads(raw)
            result = record["artifact"]
            msh_text = result["msh_text"]
            if (
                record["format"] != _DISK_FORMAT
                or record["key"] != key
                or record["generator"] != self._generator
                or not isinstance(msh_text, str)
                or hashlib.sha256(msh_text.encode("utf-8")).hexdigest() != record["msh_sha256"]
            ):
                raise ValueError("record does not match its key or digest")
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("solver mesh disk cache: discarding damaged %s (%s)", path.name, exc)
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
        return result

    def put(self, key: str, result: dict[str, Any]) -> bool:
        msh_text = result.get("msh_text")
        if not isinstance(msh_text, str):
            return False
        record = {
            "format": _DISK_FORMAT,
            "key": key,
            "generator": self._generator,
            "msh_sha256": hashlib.sha256(msh_text.encode("utf-8")).hexdigest(),
            "artifact": result,
        }
        try:
            encoded = json.dumps(record, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return False
        if len(encoded) > self._max_bytes or not _same_json(result, json.loads(encoded)["artifact"]):
            return False
        from server.platform.json_file import write_json_atomic

        path = self._path(key)
        with self._lock:
            try:
                write_json_atomic(path, encoded, sync_file=False, temporary_prefix=".wg2-solver-mesh-")
            except OSError as exc:
                log.warning("solver mesh disk cache: cannot write %s (%s)", path.name, exc)
                return False
            self._evict()
        return True

    def _evict(self) -> None:
        entries = []
        try:
            for path in self._folder.glob("*.json"):
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    entries.append((stat.st_mtime_ns, path.name, stat.st_size, path))
        except OSError:
            return
        entries.sort()
        total = sum(size for _, _, size, _ in entries)
        while entries and (len(entries) > self._max_entries or total > self._max_bytes):
            _, _, size, path = entries.pop(0)
            with contextlib.suppress(OSError):
                path.unlink()
            total -= size

    def clear(self) -> None:
        with self._lock:
            for path in self._folder.glob("*.json"):
                with contextlib.suppress(OSError):
                    path.unlink()


__all__ = [
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_MAX_CACHE_ENTRIES",
    "DEFAULT_MAX_DISK_CACHE_BYTES",
    "DEFAULT_MAX_DISK_CACHE_ENTRIES",
    "SOLVER_MESH_DISK_CACHE_DIRNAME",
    "SolverMeshArtifactCache",
    "SolverMeshCacheInfo",
    "SolverMeshDiskCache",
]
