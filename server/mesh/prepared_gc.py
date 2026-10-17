"""Collect prepared meshes nothing uses any more (CAD-OPERATIONS.md, "Retention").

A prepared mesh is an ingest row and the files its record names: a file
import's folder under ``<data>/imports/files/<ingest id>`` (``original.msh``,
``solve.msh``), or a CAD return's solve mesh (and its sidecar, FEM region and
display meshes) under ``<data>/imports/meshes`` and ``<data>/imports/viewports``,
with its retained snapshot under ``<data>/imports/bundles``. One is removed
only when nothing keeps it (:func:`keep_reasons`). Files go only after the row
commits, so a crash in between leaves at worst files no row names -- which a
later pass sweeps -- and never a row that names missing files.

This module is below the jobs and CAD Link layers: it is handed the two stores
and the operation states that are finished, and imports neither.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any

from server.platform.paths import data_paths

logger = logging.getLogger(__name__)

#: A prepared mesh made or used more recently than this is always kept.
GRACE_PERIOD = timedelta(hours=24)
#: A ``model-solve-press:<client id>`` answer older than this is forgotten: a
#: replay of that press id after it has gone is treated as a new press.
PRESS_BINDING_BOUND = timedelta(days=30)
PRESS_KEY_PREFIX = "model-solve-press:"

_INGEST_ID = re.compile(r"wgi_[0-9A-Za-z]+")
_HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_BUNDLE_NAME = re.compile(r"^([0-9a-f]{64})\.wgreturn$")
#: Where a producer's files may be removed from, under ``<data>/imports``.
_MESH_ROOTS = ("meshes", "viewports")


# -- one collection at a time, never beside an ingest ---------------------------


class _Gate:
    """Ingests and preparations share it; a collection holds it alone.

    A collection starts only when nothing is ingesting, and an ingest that
    starts while one runs waits for it to end -- so a cache hit can never adopt
    a mesh file a collection has decided nobody names.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._users = 0
        self._collecting = False

    @contextmanager
    def use(self) -> Iterator[None]:
        with self._condition:
            while self._collecting:
                self._condition.wait()
            self._users += 1
        try:
            yield
        finally:
            with self._condition:
                self._users -= 1
                self._condition.notify_all()

    def try_collect(self) -> bool:
        with self._condition:
            if self._collecting or self._users:
                return False
            self._collecting = True
            return True

    def end_collect(self) -> None:
        with self._condition:
            self._collecting = False
            self._condition.notify_all()


_GATE = _Gate()


def prepared_mesh_use() -> Any:
    """Hold off collection while preparing, ingesting or replaying a mesh."""

    return _GATE.use()


# -- what keeps a prepared mesh ---------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _producer(record: Mapping[str, Any]) -> str | None:
    """``mesh-file`` or ``cad``; None for anything this collector does not own."""

    if record.get("producer") == "mesh-file":
        return "mesh-file"
    if "producer" not in record and record.get("bundle_store_path") and record.get("mesh_store_path"):
        return "cad"
    return None


@dataclass
class Inventory:
    """Every reference, read once: which ingests are kept, and why."""

    ingests: list[dict[str, Any]]
    keep: dict[str, set[str]] = field(default_factory=dict)
    job_text: list[str] = field(default_factory=list)

    def kept(self, ingest_id: str, reason: str) -> None:
        self.keep.setdefault(ingest_id, set()).add(reason)


def keep_reasons(
    cad: Mapping[str, Any],
    jobs: Iterable[Mapping[str, str]],
    *,
    now: datetime,
    grace: timedelta = GRACE_PERIOD,
) -> Inventory:
    """Why each ingest is kept; an ingest with no reason may be collected.

    Kept when any of these refers to it: a job in any state (any ingest id in
    its request or metadata -- geometry, imported geometry, a CAD intent's
    ``bundle_path: ingest/<id>``, a preparation); a model's latest
    preparation; a preparation of an unfinished (or vanished) CAD operation,
    or any ingest id an unfinished operation's row names; a setup revision
    naming it; a press binding naming it; made or last used within the grace
    period. A record this build cannot read, or a producer it does not own,
    is always kept.
    """

    inventory = Inventory(ingests=list(cad["ingests"]))
    cutoff = now - grace
    for job in jobs:
        text = str(job.get("text") or "")
        inventory.job_text.append(text)
        for ingest_id in _INGEST_ID.findall(text):
            inventory.kept(ingest_id, "job")
    for ingest_id in cad["latest"]:
        inventory.kept(str(ingest_id), "latest")
    for preparation in cad["preparations"]:
        if preparation.get("state") is None or preparation["state"] not in cad["terminal"]:
            inventory.kept(str(preparation["ingest_id"]), "unfinished_operation")
    for operation in cad["unfinished_operations"]:
        for value in operation.values():
            for ingest_id in _INGEST_ID.findall(str(value or "")):
                inventory.kept(ingest_id, "unfinished_operation")
    for setup in cad["revisions"]:
        for ingest_id in _INGEST_ID.findall(setup):
            inventory.kept(ingest_id, "setup_revision")
    for setting in cad["settings"]:
        if str(setting["key"]).startswith(PRESS_KEY_PREFIX):
            for ingest_id in _INGEST_ID.findall(str(setting["value_json"])):
                inventory.kept(ingest_id, "press_binding")
    for row in inventory.ingests:
        ingest_id = str(row["ingest_id"])
        used = [moment for moment in (_parse_time(row.get("created_at")), _parse_time(row.get("last_used_at")))
                if moment is not None]
        if not used or max(used) >= cutoff:
            inventory.kept(ingest_id, "grace")
        try:
            record = json.loads(str(row["record_json"]))
        except ValueError:
            record = None
        if not isinstance(record, dict):
            inventory.kept(ingest_id, "unreadable")
        elif _producer(record) is None:
            inventory.kept(ingest_id, "other_producer")
    return inventory


# -- removing files, only under the imports roots ---------------------------------


class _Refused(Exception):
    pass


def _lexical(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(path)))


class _Roots:
    """``<data>/imports`` and the subfolders files may be removed from."""

    def __init__(self, data_dir: Path) -> None:
        self.imports = _lexical(data_paths(data_dir).root / "imports")
        self._spellings = {self.imports, _lexical(Path(os.path.realpath(self.imports)))}

    def relative(self, path: Path | str) -> Path:
        """``path`` relative to the imports root, refusing anything outside it."""

        candidate = _lexical(Path(path))
        for root in self._spellings:
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if relative.parts and ".." not in relative.parts:
                return relative
        raise _Refused(f"{path} is outside {self.imports}")

    def checked(self, path: Path | str, *, under: tuple[str, ...]) -> Path:
        """The real location of ``path`` under ``imports/<one of under>``, never through a link.

        Every component from ``<data>/imports`` down to ``path`` itself must be a
        real directory or file, not a symbolic link (or junction).
        """

        relative = self.relative(path)
        if relative.parts[0] not in under or len(relative.parts) < 2:
            raise _Refused(f"{path} is not under imports/{'|'.join(under)}")
        current = self.imports
        _refuse_link(current)
        for part in relative.parts:
            current = current / part
            _refuse_link(current)
        return current


def _refuse_link(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or _is_junction(path):
        raise _Refused(f"{path} is a link; collection never follows one")


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    return bool(checker(path)) if checker is not None else False


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        for name in files + directories:
            try:
                info = os.lstat(Path(root) / name)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _newest_mtime(path: Path) -> float:
    newest = os.lstat(path).st_mtime
    for root, directories, files in os.walk(path, followlinks=False):
        for name in files + directories:
            try:
                newest = max(newest, os.lstat(Path(root) / name).st_mtime)
            except OSError:
                continue
    return newest


def _remove_tree(path: Path) -> int:
    """Remove one real directory tree without following a link inside it; its bytes."""

    _refuse_link(path)
    if not path.is_dir():
        return 0
    for root, directories, files in os.walk(path, followlinks=False):
        for name in files + directories:
            _refuse_link(Path(root) / name)
    size = _tree_bytes(path)
    for root, directories, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            os.unlink(Path(root) / name)
        for name in directories:
            os.rmdir(Path(root) / name)
    os.rmdir(path)
    return size


def _remove_file(path: Path) -> int:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode):
        raise _Refused(f"{path} is not a regular file")
    os.unlink(path)
    return info.st_size


def _prune_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    while current != stop and stop in current.parents:
        try:
            os.rmdir(current)
        except OSError:
            return
        current = current.parent


def _named_paths(value: Any, keys: frozenset[str]) -> Iterator[str]:
    """Every string stored under one of ``keys``, anywhere in a record."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item:
                yield item
            else:
                yield from _named_paths(item, keys)
    elif isinstance(value, list):
        for item in value:
            yield from _named_paths(item, keys)


_MESH_KEYS = frozenset({"mesh_store_path", "store_path"})


# -- one pass -----------------------------------------------------------------------


@dataclass
class CollectionReport:
    skipped: str | None = None
    removed_ingests: list[str] = field(default_factory=list)
    removed_paths: list[str] = field(default_factory=list)
    removed_bytes: int = 0
    pruned_press_bindings: int = 0
    refused: list[str] = field(default_factory=list)
    kept: dict[str, list[str]] = field(default_factory=dict)


def collect_prepared_meshes(
    cad_store: Any,
    job_store: Any,
    data_dir: str | Path,
    *,
    terminal_states: Iterable[str],
    restart_pending: Callable[[], bool] = lambda: False,
    now: datetime | None = None,
    grace: timedelta = GRACE_PERIOD,
    press_bound: timedelta = PRESS_BINDING_BOUND,
    remove_files: Callable[[Mapping[str, Any], "_Pass"], None] | None = None,
) -> CollectionReport:
    """One collection pass. Skipped while a restart is pending or anything is ingesting."""

    report = CollectionReport()
    if restart_pending():
        report.skipped = "restart_pending"
        logger.info("Prepared-mesh collection skipped: an update restart is pending.")
        return report
    if not _GATE.try_collect():
        report.skipped = "busy"
        logger.info("Prepared-mesh collection skipped: a mesh is being prepared.")
        return report
    try:
        if restart_pending():
            report.skipped = "restart_pending"
            return report
        _Pass(
            cad_store, job_store, Path(data_dir), sorted(set(terminal_states)),
            now or datetime.now(timezone.utc), grace, press_bound, report, restart_pending,
        ).run(remove_files)
    finally:
        _GATE.end_collect()
    if report.removed_ingests or report.removed_paths or report.pruned_press_bindings:
        logger.info(
            "Prepared-mesh collection removed %d prepared mesh(es) and %d path(s), %d bytes; "
            "pruned %d press binding(s).",
            len(report.removed_ingests), len(report.removed_paths), report.removed_bytes,
            report.pruned_press_bindings,
        )
    return report


class _Pass:
    def __init__(
        self, cad_store: Any, job_store: Any, data_dir: Path, terminal: list[str],
        now: datetime, grace: timedelta, press_bound: timedelta, report: CollectionReport,
        restart_pending: Callable[[], bool],
    ) -> None:
        self.cad, self.jobs, self.terminal = cad_store, job_store, terminal
        self.now, self.grace, self.cutoff = now, grace, now - grace
        self.restart_pending = restart_pending
        self.press_cutoff = now - press_bound
        self.report = report
        self.roots = _Roots(data_dir)

    def run(self, remove_files: Callable[[Mapping[str, Any], _Pass], None] | None) -> None:
        try:
            for name in ("", "files", "bundles", *_MESH_ROOTS):
                _refuse_link(self.roots.imports / name if name else self.roots.imports)
        except _Refused as exc:
            self.report.skipped = "imports_root_is_a_link"
            logger.warning("Prepared-mesh collection refused: %s", exc)
            return
        jobs = self.jobs.prepared_mesh_references()
        cad = dict(self.cad.collection_inventory(self.terminal))
        cad["terminal"] = set(self.terminal)
        pruned = self._prune_press_bindings(cad["settings"], {str(job["id"]) for job in jobs})
        cad["settings"] = [row for row in cad["settings"] if str(row["key"]) not in pruned]
        inventory = keep_reasons(cad, jobs, now=self.now, grace=self.grace)
        self.job_hex = {match for text in inventory.job_text for match in _HEX64.findall(text)}
        self.report.kept = {key: sorted(value) for key, value in inventory.keep.items()}
        removed: list[dict[str, Any]] = []
        for row in inventory.ingests:
            ingest_id = str(row["ingest_id"])
            if ingest_id in inventory.keep:
                continue
            if self.restart_pending():
                self.report.skipped = "restart_pending"
                break
            deleted = self.cad.delete_unused_ingest(
                ingest_id, used_before=_stamp(self.cutoff), terminal_states=self.terminal
            )
            if deleted is not None:
                removed.append(deleted)
                self.report.removed_ingests.append(ingest_id)
        # Only now, with every row gone, are files read for what still names them.
        self.surviving = self.cad.ingest_records()
        for row in removed:
            try:
                if remove_files is not None:
                    remove_files(row, self)
                else:
                    self.remove_files(row)
            except Exception:  # noqa: BLE001 - a later pass sweeps what is left
                logger.warning(
                    "Could not remove the files of prepared mesh %s; a later pass sweeps them.",
                    row.get("ingest_id"), exc_info=True,
                )
        self.sweep_orphans()

    # -- press bindings --

    def _prune_press_bindings(self, settings: list[dict[str, Any]], job_ids: set[str]) -> set[str]:
        """Forget press answers whose job is gone or that are older than the bound."""

        stale: list[tuple[str, str]] = []
        for setting in settings:
            key = str(setting["key"])
            if not key.startswith(PRESS_KEY_PREFIX):
                continue
            try:
                value = json.loads(str(setting["value_json"]))
            except ValueError:
                value = {}
            written = _parse_time(setting.get("updated_at"))
            job_id = str(value.get("job_id") or "") if isinstance(value, dict) else ""
            if (written is not None and written < self.press_cutoff) or job_id not in job_ids:
                stale.append((key, str(setting["updated_at"])))
        if stale:
            self.report.pruned_press_bindings = self.cad.delete_settings_if_unchanged(stale)
        return {key for key, _ in stale}

    # -- files --

    def _named_elsewhere(self, value: str) -> bool:
        escaped = json.dumps(value)[1:-1]
        return any(value in text or escaped in text for text in self.surviving)

    def _bundle_referenced(self, digest: str) -> bool:
        return digest in self.job_hex or self.cad.mentions(digest)

    def _count(self, path: Path, size: int) -> None:
        self.report.removed_paths.append(str(path))
        self.report.removed_bytes += size
        logger.info("Prepared-mesh collection removed %s (%d bytes).", path, size)

    def remove_files(self, row: Mapping[str, Any]) -> None:
        record = json.loads(str(row["record_json"]))
        producer = _producer(record)
        if producer == "mesh-file":
            self._remove_file_folder(str(row["ingest_id"]), record)
        elif producer == "cad":
            self._remove_cad_files(record)

    def _refuse(self, exc: _Refused) -> None:
        self.report.refused.append(str(exc))
        logger.warning("Prepared-mesh collection refused: %s", exc)

    def _remove_file_folder(self, ingest_id: str, record: Mapping[str, Any]) -> None:
        try:
            folder = self.roots.checked(Path(str(record["mesh_store_path"])).parent, under=("files",))
        except _Refused as exc:
            self._refuse(exc)
            return
        if folder.name != ingest_id or folder.parent != self.roots.imports / "files":
            self._refuse(_Refused(f"{folder} is not prepared mesh {ingest_id}'s own folder"))
            return
        if self._named_elsewhere(str(folder)) or self._named_elsewhere(ingest_id):
            return
        try:
            size = _remove_tree(folder)
        except _Refused as exc:
            self._refuse(exc)
            return
        self._count(folder, size)

    def _remove_cad_files(self, record: Mapping[str, Any]) -> None:
        # A CAD mesh can be shared through its cache key: it goes only when no
        # surviving ingest names the same path.
        for name in sorted(set(_named_paths(record, _MESH_KEYS))):
            if self._named_elsewhere(name):
                continue
            for path in dict.fromkeys((Path(name), Path(name).with_suffix(".json"))):
                if self._named_elsewhere(str(path)):
                    continue
                try:
                    real = self.roots.checked(path, under=_MESH_ROOTS)
                    size = _remove_file(real)
                except _Refused as exc:
                    self._refuse(exc)
                    continue
                if size:
                    self._count(real, size)
                root = self.roots.imports / real.relative_to(self.roots.imports).parts[0]
                _prune_empty_parents(real, root)
        bundle = str(record.get("bundle_store_path") or "")
        if bundle and not self._named_elsewhere(bundle):
            self._remove_bundle(bundle)

    def _remove_bundle(self, bundle: str) -> None:
        try:
            real = self.roots.checked(bundle, under=("bundles",))
        except _Refused as exc:
            self._refuse(exc)
            return
        match = _BUNDLE_NAME.match(real.name)
        if match is None or real.parent != self.roots.imports / "bundles":
            self._refuse(_Refused(f"{real} is not a retained snapshot"))
            return
        # A retained snapshot is also what operations, receipts and runs
        # name by digest: it goes only when nothing anywhere names it.
        if self._bundle_referenced(match.group(1)):
            return
        try:
            size = _remove_tree(real)
        except _Refused as exc:
            self._refuse(exc)
            return
        self._count(real, size)

    # -- orphans --

    def sweep_orphans(self) -> None:
        """Remove folders no row names that are older than the grace period.

        These are crash leftovers: a file preparation published before its row
        committed, or files whose row a pass removed before it could.
        """

        cutoff = self.cutoff.timestamp()
        files = self.roots.imports / "files"
        for entry in sorted(_entries(files)):
            if entry.is_symlink() or not entry.is_dir() or _is_junction(entry):
                continue
            if self._named_elsewhere(str(entry)) or (
                entry.name.startswith("wgi_") and self._named_elsewhere(entry.name)
            ):
                continue
            self._sweep(entry, cutoff, under=("files",))
        bundles = self.roots.imports / "bundles"
        for entry in sorted(_entries(bundles)):
            match = _BUNDLE_NAME.match(entry.name)
            if match is None or entry.is_symlink() or not entry.is_dir() or _is_junction(entry):
                continue
            if self._named_elsewhere(str(entry)) or self._bundle_referenced(match.group(1)):
                continue
            self._sweep(entry, cutoff, under=("bundles",))

    def _sweep(self, entry: Path, cutoff: float, *, under: tuple[str, ...]) -> None:
        try:
            real = self.roots.checked(entry, under=under)
            if _newest_mtime(real) >= cutoff:
                return
            size = _remove_tree(real)
        except _Refused as exc:
            self._refuse(exc)
            return
        except OSError:
            logger.warning("Could not sweep %s; a later pass tries again.", entry, exc_info=True)
            return
        self._count(real, size)


def _entries(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


# -- when it runs ---------------------------------------------------------------------


class PreparedMeshCollector:
    """Runs collection off the request path: at startup and after jobs are deleted.

    Requests coalesce: while a pass runs, any number of requests make one more.
    Nothing waits for a pass; a failed one is logged and changes nothing more.
    """

    def __init__(
        self,
        cad_store: Any,
        job_store: Any,
        data_dir: str | Path,
        *,
        terminal_states: Iterable[str],
        restart_pending: Callable[[], bool],
        startup_delay: float = 30.0,
    ) -> None:
        self._arguments = (cad_store, job_store, Path(data_dir))
        self._terminal = tuple(terminal_states)
        self._restart_pending = restart_pending
        self.startup_delay = startup_delay
        self._task: asyncio.Task[None] | None = None
        self._again = False

    def collect_now(self) -> CollectionReport:
        return collect_prepared_meshes(
            *self._arguments, terminal_states=self._terminal, restart_pending=self._restart_pending,
        )

    def request(self, *, delay: float = 0.0) -> None:
        """Schedule a pass on the running loop; never waits for it."""

        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.get_running_loop().create_task(self._run(delay))

    async def _run(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        while True:
            self._again = False
            try:
                await asyncio.to_thread(self.collect_now)
            except Exception:  # noqa: BLE001 - collection never takes the app down
                logger.warning("Prepared-mesh collection failed.", exc_info=True)
            if not self._again:
                return

    async def startup(self) -> None:
        self.request(delay=self.startup_delay)

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "GRACE_PERIOD",
    "PRESS_BINDING_BOUND",
    "CollectionReport",
    "PreparedMeshCollector",
    "collect_prepared_meshes",
    "keep_reasons",
    "prepared_mesh_use",
]
