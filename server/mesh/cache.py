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
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
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

    One file per key: a JSON header line (the artifact without its mesh text,
    the key, the generator and the digest of the mesh text), then the mesh
    text's exact bytes. Files live in a folder named for the generator, which
    names everything that shapes an artifact besides the key's mesher config:
    the mesher and gmsh, WG's own mesh code, and the dense-solver memory
    ceiling that admitted it. Another generator's folder is left alone while it
    is in use (two WG builds can share one data folder) and deleted once it has
    gone unused for :attr:`stale_seconds`. A file that does not parse, names
    another key or generator, or fails its digest is deleted and read as a
    miss, so the mesh is rebuilt. The folder, unfinished writes included, is
    held under ``max_bytes`` and ``max_entries`` by evicting the least
    recently used entries; the one just written is never the one evicted.

    An artifact is written only when its non-text part reads back from JSON
    identical, types and key order included; anything else stays memory-only.
    Writes run on one background thread, after the caller has its artifact.
    """

    ENTRY_SUFFIX = ".mesh"
    STAGE_PREFIX = ".wg2-solver-mesh-"
    #: An unfinished write older than this is a crash leftover.
    ORPHAN_SECONDS = 10 * 60.0
    #: Another generator's folder unused this long is deleted.
    stale_seconds = 7 * 24 * 60 * 60.0

    def __init__(
        self,
        root: Path,
        generator: dict[str, Any],
        *,
        max_entries: int = DEFAULT_MAX_DISK_CACHE_ENTRIES,
        max_bytes: int = DEFAULT_MAX_DISK_CACHE_BYTES,
    ) -> None:
        from server.contracts.canonical import canonical_ascii_strict_json

        self._generator = json.loads(json.dumps({"disk_format": _DISK_FORMAT, **generator}))
        canonical = canonical_ascii_strict_json(self._generator)
        self._root = Path(root)
        self._folder = self._root / hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wg2-solver-mesh-disk")
        self._pending: set[Future[bool]] = set()

    @property
    def folder(self) -> Path:
        return self._folder

    @property
    def generator(self) -> dict[str, Any]:
        return self._generator

    def open(self) -> None:
        """Create this generator's folder; tidy the rest in the background."""

        self._folder.mkdir(parents=True, exist_ok=True)
        os.utime(self._folder)
        self._submit(self._tidy)

    def _tidy(self) -> bool:
        """Delete stale generators' folders and this folder's crash leftovers."""

        now = time.time()
        with contextlib.suppress(OSError):
            for entry in self._root.iterdir():
                if (
                    entry == self._folder
                    or entry.is_symlink()
                    or not entry.is_dir()
                    or not _is_hex(entry.name, 32)
                ):
                    continue
                with contextlib.suppress(OSError):
                    if now - entry.stat().st_mtime > self.stale_seconds:
                        shutil.rmtree(entry, ignore_errors=True)
        with self._lock:
            self._evict(keep=None)
        return True

    def _path(self, key: str) -> Path:
        if not _is_hex(key, 64):
            raise ValueError("solver mesh disk keys are 64 lowercase hex digits")
        return self._folder / f"{key}{self.ENTRY_SUFFIX}"

    def get(self, key: str) -> dict[str, Any] | None:
        from server.mesh.artifact import mesh_text_sha256

        path = self._path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("solver mesh disk cache: cannot read %s (%s)", path.name, exc)
            return None
        try:
            header_bytes, separator, body = raw.partition(b"\n")
            if not separator:
                raise ValueError("no header line")
            header = json.loads(header_bytes)
            msh_text = body.decode("utf-8")
            if (
                header["format"] != _DISK_FORMAT
                or header["key"] != key
                or header["generator"] != self._generator
                or mesh_text_sha256(msh_text) != header["msh_sha256"]
            ):
                raise ValueError("entry does not match its key, generator or digest")
            artifact = header["artifact"]
            result = {
                name: (msh_text if name == "msh_text" else artifact[name])
                for name in header["order"]
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
            log.warning("solver mesh disk cache: discarding damaged %s (%s)", path.name, exc)
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
            os.utime(self._folder)  # this generator is in use
        return result

    def put_later(self, key: str, result: dict[str, Any]) -> None:
        """Write ``result`` on the background thread; the caller may change it after."""

        msh_text = result.get("msh_text")
        snapshot = {name: copy.deepcopy(value) for name, value in result.items() if name != "msh_text"}
        order = list(result)
        self._submit(lambda: self._put(key, snapshot, order, msh_text))

    def put(self, key: str, result: dict[str, Any]) -> bool:
        """Write ``result`` now; False when it cannot be kept exactly."""

        snapshot = {name: value for name, value in result.items() if name != "msh_text"}
        return self._put(key, snapshot, list(result), result.get("msh_text"))

    def _put(self, key: str, artifact: dict[str, Any], order: list[str], msh_text: Any) -> bool:
        from server.mesh.artifact import mesh_text_sha256
        from server.platform.json_file import write_json_atomic

        if not isinstance(msh_text, str):
            return False
        try:
            body = msh_text.encode("utf-8")
            header = json.dumps(
                {
                    "format": _DISK_FORMAT,
                    "key": key,
                    "generator": self._generator,
                    "msh_sha256": mesh_text_sha256(msh_text),
                    "order": order,
                    "artifact": artifact,
                },
                separators=(",", ":"),
            ).encode("ascii")
            if not _same_json(artifact, json.loads(header)["artifact"]):
                return False
        except (TypeError, ValueError, UnicodeError):
            return False
        if len(header) + 1 + len(body) > self._max_bytes:
            return False
        path = self._path(key)
        with self._lock:
            try:
                write_json_atomic(
                    path, header + b"\n" + body, sync_file=False,
                    temporary_prefix=self.STAGE_PREFIX,
                    replace_attempts=3, retry_seconds=(0.05, 0.2),
                )
            except OSError as exc:
                log.warning("solver mesh disk cache: cannot write %s (%s)", path.name, exc)
                return False
            self._evict(keep=path)
        return True

    def _evict(self, *, keep: Path | None) -> None:
        """Hold the folder under its bounds; caller holds ``_lock``."""

        now = time.time()
        entries = []
        total = 0
        try:
            listing = list(self._folder.iterdir())
        except OSError:
            return
        for path in listing:
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.name.startswith(self.STAGE_PREFIX):
                if now - stat.st_mtime > self.ORPHAN_SECONDS:
                    with contextlib.suppress(OSError):
                        path.unlink()
                    continue
                total += stat.st_size  # a write in progress still uses the disk
                continue
            if path.suffix != self.ENTRY_SUFFIX:
                continue
            total += stat.st_size
            if path != keep:
                entries.append((stat.st_mtime_ns, path.name, stat.st_size, path))
        count = len(entries) + (1 if keep is not None and keep.exists() else 0)
        entries.sort()
        while entries and (count > self._max_entries or total > self._max_bytes):
            _, _, size, path = entries.pop(0)
            with contextlib.suppress(OSError):
                path.unlink()
            total -= size
            count -= 1

    def _submit(self, work: Callable[[], bool]) -> None:
        def run() -> bool:
            try:
                return work()
            except Exception:  # noqa: BLE001 - a cache write never fails a build
                log.warning("solver mesh disk cache: background work failed", exc_info=True)
                return False

        try:
            future = self._writer.submit(run)
        except RuntimeError:  # shut down
            return
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(self._forget)

    def _forget(self, future: Future[bool]) -> None:
        with self._lock:
            self._pending.discard(future)

    def flush(self, timeout: float | None = None) -> None:
        """Wait for queued background writes (tests, shutdown)."""

        with self._lock:
            pending = list(self._pending)
        wait(pending, timeout=timeout)

    def close(self) -> None:
        """Finish queued writes, then stop the writer thread."""

        self._writer.shutdown(wait=True)

    def clear(self) -> None:
        self.flush()
        with self._lock:
            for path in self._folder.glob(f"*{self.ENTRY_SUFFIX}"):
                with contextlib.suppress(OSError):
                    path.unlink()


def _is_hex(text: str, length: int) -> bool:
    return len(text) == length and all(character in "0123456789abcdef" for character in text)


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
