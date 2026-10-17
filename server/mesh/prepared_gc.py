"""Collect prepared meshes nothing uses any more (CAD-OPERATIONS.md, "Retention").

A prepared mesh is an ingest row and the files its record names: a file
import's folder under ``<data>/imports/files/<ingest id>`` (``original.msh``,
``solve.msh``), or a CAD return's solve, coupled-exterior, FEM-region and
display meshes (each with its ``.json`` sidecar) under ``<data>/imports/meshes``
and ``<data>/imports/viewports``, a deferred display mesh found through its
``viewports/index`` entry, and its retained snapshot under
``<data>/imports/bundles``. One is removed only when nothing keeps it
(:func:`keep_reasons`). Its row is deleted first, in its own short store
transaction; its files are removed after that commits, with no store lock
held. A crash in between leaves at worst files no row names -- which a later
pass sweeps once they are older than the grace period -- and never a row that
names missing files. Index files are left: a stale entry is a cache miss.

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
import shutil
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
#: A pass skipped because a mesh was being prepared tries again after this.
RETRY_DELAY = 300.0

_INGEST_ID = re.compile(r"wgi_[0-9A-Za-z]+")
_HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_BUNDLE_NAME = re.compile(r"^([0-9a-f]{64})\.wgreturn$")
_VIEWPORT_NAME = re.compile(r"^([0-9a-f]{64})\.(msh|json)$")
#: Where a producer's mesh files may be removed from, under ``<data>/imports``.
_MESH_ROOTS = ("meshes", "viewports")
_MESH_KEYS = frozenset({"mesh_store_path", "store_path"})
_MAX_INDEX_BYTES = 256

#: Test seam: called with a path after it was checked and before it is removed.
_before_remove: Callable[[Path], None] | None = None


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


def _timestamps_readable(row: Mapping[str, Any]) -> bool:
    """``created_at`` parses, and so does ``last_used_at`` when it is set."""

    if _parse_time(row.get("created_at")) is None:
        return False
    used = row.get("last_used_at")
    return used is None or _parse_time(used) is not None


def is_stale(row: Mapping[str, Any], cutoff: datetime) -> bool:
    """Made and last used before ``cutoff``; an unreadable timestamp is never stale."""

    if not _timestamps_readable(row):
        return False
    moments = [_parse_time(row.get("created_at")), _parse_time(row.get("last_used_at"))]
    return all(moment < cutoff for moment in moments if moment is not None)


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
    period. A record or a timestamp this build cannot read, or a producer it
    does not own, is always kept.
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
        if not _timestamps_readable(row):
            inventory.kept(ingest_id, "unreadable_timestamp")
        elif not is_stale(row, cutoff):
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


# -- removing files, only under the imports roots, never through a link ----------


class _Refused(Exception):
    pass


def _lexical(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(path)))


def _fold(text: str) -> str:
    """Path keys compare case-insensitively where the file system does (Windows)."""

    return text.casefold() if os.name == "nt" else text


def _is_junction(path: Path) -> bool:
    """A Windows junction (or any reparse point there); POSIX has none.

    Before Python 3.12 there is no ``os.path.isjunction``, so Windows reads
    the reparse-point attribute itself rather than assuming there is none.
    """

    checker = getattr(os.path, "isjunction", None)
    if checker is not None and checker(path):
        return True
    if os.name == "nt":
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", None)
        except FileNotFoundError:
            return False
        if attributes is None:
            return True  # cannot tell: treat it as a link
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)  # type: ignore[attr-defined]
    return False


def _refuse_link(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or _is_junction(path):
        raise _Refused(f"{path} is a link; collection never follows one")


#: Removal walks real directories by descriptor, so a link swapped in after the
#: checks is never followed: every component is opened with O_NOFOLLOW from
#: ``<data>/imports`` down. Where the platform cannot (Windows), the path is
#: checked again immediately before it is removed.
_BY_DESCRIPTOR = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and shutil.rmtree.avoids_symlink_attacks
)


class _Roots:
    """``<data>/imports`` and the subfolders files may be removed from."""

    def __init__(self, data_dir: Path) -> None:
        self.imports = _lexical(data_paths(data_dir).root / "imports")
        self._real = _lexical(Path(os.path.realpath(self.imports)))
        self._spellings = {self.imports, self._real}

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

    def keys(self, path: Path | str) -> set[str]:
        """How ``path`` is compared: relative to imports, however the data folder is spelled.

        Both the spelling as written and the resolved location count, so two
        spellings of one file (a linked data folder, ``..``, letter case on
        Windows) are the same file. Empty for a path outside imports.
        """

        keys: set[str] = set()
        with _suppress_refusal():
            keys.add(_fold(self.relative(path).as_posix()))
        try:
            real = _lexical(Path(os.path.realpath(path)))
            relative = real.relative_to(self._real)
        except (OSError, ValueError):
            return keys
        if relative.parts:
            keys.add(_fold(relative.as_posix()))
        return keys

    def checked(self, path: Path | str, *, under: tuple[str, ...]) -> Path:
        """The location of ``path`` under ``imports/<one of under>``, never through a link.

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

    @contextmanager
    def parent_descriptor(self, path: Path) -> Iterator[int]:
        """A descriptor of ``path``'s parent, every component opened without following a link."""

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            descriptor = os.open(self.imports, flags)
        except OSError as exc:
            raise _Refused(f"{self.imports} cannot be opened as a real directory: {exc}") from exc
        try:
            for part in path.relative_to(self.imports).parts[:-1]:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except OSError as exc:
                    raise _Refused(f"{part} under {path.parent} is not a real directory: {exc}") from exc
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)


@contextmanager
def _suppress_refusal() -> Iterator[None]:
    try:
        yield
    except _Refused:
        pass


def _walk_lstat(path: Path) -> Iterator[tuple[Path, os.stat_result]]:
    for root, directories, files in os.walk(path, followlinks=False):
        for name in files + directories:
            try:
                yield Path(root) / name, os.lstat(Path(root) / name)
            except OSError:
                continue


def _tree_bytes(path: Path) -> int:
    return sum(info.st_size for _, info in _walk_lstat(path) if stat.S_ISREG(info.st_mode))


def _newest_mtime(path: Path) -> float:
    newest = os.lstat(path).st_mtime
    for _, info in _walk_lstat(path):
        newest = max(newest, info.st_mtime)
    return newest


def _remove_tree(roots: _Roots, path: Path, *, under: tuple[str, ...]) -> int | None:
    """Remove one real directory tree under ``imports/<under>``; its bytes, None if absent.

    A tree with a link anywhere in it is refused whole. The removal itself
    opens every component without following a link, so a link swapped in
    after the checks is removed as a link, never followed.
    """

    path = roots.checked(path, under=under)
    if not os.path.lexists(path):
        return None
    if not path.is_dir():
        raise _Refused(f"{path} is not a directory")
    for entry, info in _walk_lstat(path):
        if stat.S_ISLNK(info.st_mode) or _is_junction(entry):
            raise _Refused(f"{entry} is a link; collection never follows one")
    size = _tree_bytes(path)
    if _before_remove is not None:
        _before_remove(path)
    if _BY_DESCRIPTOR:
        with roots.parent_descriptor(path) as parent:
            try:
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISDIR(info.st_mode):
                raise _Refused(f"{path} is no longer a real directory")
            shutil.rmtree(path.name, dir_fd=parent)
        return size
    path = roots.checked(path, under=under)
    if not path.is_dir() or _is_junction(path):
        raise _Refused(f"{path} is no longer a real directory")
    shutil.rmtree(path)
    return size


def _remove_file(roots: _Roots, path: Path, *, under: tuple[str, ...]) -> int | None:
    """Remove one regular file under ``imports/<under>``, never through a link; its bytes."""

    path = roots.checked(path, under=under)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise _Refused(f"{path} is not a regular file")
    if _before_remove is not None:
        _before_remove(path)
    if _BY_DESCRIPTOR:
        with roots.parent_descriptor(path) as parent:
            try:
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(info.st_mode):
                raise _Refused(f"{path} is no longer a regular file")
            os.unlink(path.name, dir_fd=parent)
        return info.st_size
    path = roots.checked(path, under=under)
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise _Refused(f"{path} is no longer a regular file")
    os.unlink(path)
    return info.st_size


def _prune_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    while current != stop and stop in current.parents:
        if os.path.islink(current) or _is_junction(current):
            return
        try:
            os.rmdir(current)
        except OSError:
            return
        current = current.parent


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


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


def _pending_viewport_key(record: Mapping[str, Any]) -> str | None:
    viewport = record.get("viewport_mesh")
    if not isinstance(viewport, Mapping) or viewport.get("pending") is not True:
        return None
    key = viewport.get("lookup_key")
    return key if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key) else None


# -- one pass -----------------------------------------------------------------------


@dataclass
class CollectionReport:
    #: Why the pass did not start (``restart_pending``, ``busy``,
    #: ``imports_root_is_a_link``), or why it stopped early (``restart_pending``,
    #: ``stopping``).
    skipped: str | None = None
    #: Why no files were removed although rows may have been.
    files_kept: str | None = None
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
    should_stop: Callable[[], bool] = lambda: False,
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
            now or datetime.now(timezone.utc), grace, press_bound, report,
            restart_pending, should_stop,
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
        restart_pending: Callable[[], bool], should_stop: Callable[[], bool],
    ) -> None:
        self.cad, self.jobs, self.terminal = cad_store, job_store, terminal
        self.now, self.grace, self.cutoff = now, grace, now - grace
        self.restart_pending, self.should_stop = restart_pending, should_stop
        self.press_cutoff = now - press_bound
        self.report = report
        self.roots = _Roots(data_dir)
        self.surviving_keys: set[str] = set()
        self.surviving_strings: set[str] = set()
        self.protected_viewports: set[str] = set()
        self.job_hex: set[str] = set()
        self._referenced_digests: dict[str, bool] = {}

    def halted(self) -> bool:
        """Stop between steps when the app is quitting or an update restart was approved."""

        if self.report.skipped in {"stopping", "restart_pending"}:
            return True
        if self.should_stop():
            self.report.skipped = "stopping"
        elif self.restart_pending():
            self.report.skipped = "restart_pending"
        return self.report.skipped is not None

    def run(self, remove_files: Callable[[Mapping[str, Any], _Pass], None] | None) -> None:
        try:
            for name in ("", "files", "bundles", *_MESH_ROOTS):
                _refuse_link(self.roots.imports / name if name else self.roots.imports)
        except _Refused as exc:
            self.report.skipped = "imports_root_is_a_link"
            logger.warning("Prepared-mesh collection refused: %s", exc)
            return
        cad = dict(self.cad.collection_inventory(self.terminal))
        jobs = self.jobs.prepared_mesh_references()
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
            if self.halted():
                break
            # The jobs database is read again for this one id just before its
            # row goes, outside the CAD store's lock (so the two stores' locks
            # are never nested). A submit marks the ingest used before it reads
            # it, so one that names it after this re-check finds it kept by
            # the recheck inside the delete, or finds it gone and is refused.
            if self.jobs.names_prepared_mesh(ingest_id):
                self.report.kept.setdefault(ingest_id, []).append("job")
                continue
            deleted = self.cad.delete_unused_ingest(
                ingest_id, is_stale=lambda current: is_stale(current, self.cutoff),
                terminal_states=self.terminal,
            )
            if deleted is not None:
                removed.append(deleted)
                self.report.removed_ingests.append(ingest_id)
        # Only now, with every row gone, are files read for what still names them.
        if not self._index_survivors():
            return
        for row in removed:
            if self.halted():
                return
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

    def _index_survivors(self) -> bool:
        """What surviving records name; False (no files touched) if one cannot be read."""

        for text in self.cad.ingest_records():
            try:
                record = json.loads(text)
            except ValueError:
                record = None
            if not isinstance(record, dict):
                self.report.files_kept = "unreadable_record"
                logger.warning(
                    "Prepared-mesh collection removes no files this pass: a prepared mesh's "
                    "record cannot be read, so what it names is unknown."
                )
                return False
            for value in _strings(record):
                self.surviving_strings.add(value)
                if os.sep in value or "/" in value:
                    self.surviving_keys.update(self.roots.keys(value))
            lookup = _pending_viewport_key(record)
            if lookup is not None:
                key = self._read_viewport_index(lookup)
                if key is not None:
                    self.protected_viewports.add(key)
        return True

    # -- press bindings --

    def _prune_press_bindings(self, settings: list[dict[str, Any]], job_ids: set[str]) -> set[str]:
        """Forget press answers past the bound, or whose job is gone and that are past the grace.

        A binding younger than the grace period is never pruned for a missing
        job: its job may have been made after the jobs were read.
        """

        stale: list[tuple[str, str]] = []
        for setting in settings:
            key = str(setting["key"])
            if not key.startswith(PRESS_KEY_PREFIX):
                continue
            written = _parse_time(setting.get("updated_at"))
            if written is None:
                continue
            try:
                value = json.loads(str(setting["value_json"]))
            except ValueError:
                value = {}
            job_id = str(value.get("job_id") or "") if isinstance(value, dict) else ""
            if written < self.press_cutoff or (written < self.cutoff and job_id not in job_ids):
                stale.append((key, str(setting["updated_at"])))
        if stale:
            self.report.pruned_press_bindings = self.cad.delete_settings_if_unchanged(stale)
        return {key for key, _ in stale}

    # -- files --

    def _named(self, path: Path | str) -> bool:
        """Whether a surviving record names this path, under any spelling of the data folder."""

        return bool(self.roots.keys(path) & self.surviving_keys)

    def _id_named(self, name: str) -> bool:
        return any(name in value for value in self.surviving_strings)

    def _digest_referenced(self, digests: Iterable[str]) -> set[str]:
        unknown = [digest for digest in digests if digest not in self._referenced_digests]
        if unknown:
            found = self.cad.mentioned(unknown)
            for digest in unknown:
                self._referenced_digests[digest] = digest in found or digest in self.job_hex
        return {digest for digest in digests if self._referenced_digests[digest]}

    def _count(self, path: Path, size: int) -> None:
        self.report.removed_paths.append(str(path))
        self.report.removed_bytes += size
        logger.info("Prepared-mesh collection removed %s (%d bytes).", path, size)

    def _refuse(self, exc: _Refused) -> None:
        self.report.refused.append(str(exc))
        logger.warning("Prepared-mesh collection refused: %s", exc)

    def _remove(self, path: Path, *, under: tuple[str, ...], tree: bool = False) -> None:
        if self.halted():
            return
        try:
            size = (_remove_tree if tree else _remove_file)(self.roots, path, under=under)
        except _Refused as exc:
            self._refuse(exc)
            return
        except OSError:
            logger.warning("Could not remove %s; a later pass tries again.", path, exc_info=True)
            return
        if size is None:
            return
        self._count(path, size)
        if not tree:
            _prune_empty_parents(path, self.roots.imports / self.roots.relative(path).parts[0])

    def remove_files(self, row: Mapping[str, Any]) -> None:
        record = json.loads(str(row["record_json"]))
        producer = _producer(record)
        if producer == "mesh-file":
            self._remove_file_folder(str(row["ingest_id"]), record)
        elif producer == "cad":
            self._remove_cad_files(record)

    def _remove_file_folder(self, ingest_id: str, record: Mapping[str, Any]) -> None:
        try:
            folder = self.roots.checked(Path(str(record["mesh_store_path"])).parent, under=("files",))
        except _Refused as exc:
            self._refuse(exc)
            return
        if folder.name != ingest_id or folder.parent != self.roots.imports / "files":
            self._refuse(_Refused(f"{folder} is not prepared mesh {ingest_id}'s own folder"))
            return
        if self._named(folder) or self._id_named(ingest_id):
            return
        self._remove(folder, under=("files",), tree=True)

    def _remove_cad_files(self, record: Mapping[str, Any]) -> None:
        # A CAD mesh can be shared through its cache key: it goes only when no
        # surviving ingest names the same file.
        names = list(_named_paths(record, _MESH_KEYS))
        lookup = _pending_viewport_key(record)
        if lookup is not None and lookup not in self.surviving_strings:
            key = self._read_viewport_index(lookup)
            if key is not None and key not in self.protected_viewports:
                names.append(str(self.roots.imports / "viewports" / f"{key}.msh"))
        for name in sorted(set(names)):
            if self._named(name):
                continue
            for path in dict.fromkeys((Path(name), Path(name).with_suffix(".json"))):
                if not self._named(path):
                    self._remove(path, under=_MESH_ROOTS)
        bundle = str(record.get("bundle_store_path") or "")
        if bundle and not self._named(bundle):
            self._remove_bundle(bundle)

    def _read_viewport_index(self, lookup: str) -> str | None:
        """The content key ``viewports/index/<lookup>.txt`` names, read without a link."""

        try:
            index = self.roots.checked(
                self.roots.imports / "viewports" / "index" / f"{lookup}.txt", under=("viewports",)
            )
            info = os.lstat(index)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_INDEX_BYTES:
                return None
            text = index.read_text(encoding="ascii").strip()
        except (_Refused, OSError, UnicodeError):
            return None
        return text if re.fullmatch(r"[0-9a-f]{64}", text) else None

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
        if self._digest_referenced([match.group(1)]):
            return
        self._remove(real, under=("bundles",), tree=True)

    # -- orphans --

    def sweep_orphans(self) -> None:
        """Remove what no row names and that has not changed for the grace period.

        These are crash leftovers: a file preparation published before its row
        committed, files whose row a pass removed before it could, and CAD mesh
        and display files (with their sidecars) no record names.
        """

        cutoff = self.cutoff.timestamp()
        files = self.roots.imports / "files"
        for entry in sorted(_entries(files)):
            if self.halted():
                return
            if entry.is_symlink() or _is_junction(entry) or not entry.is_dir():
                continue
            if self._named(entry) or self._id_named(entry.name):
                continue
            self._sweep_tree(entry, cutoff, under=("files",))
        bundles = self.roots.imports / "bundles"
        candidates = {}
        for entry in sorted(_entries(bundles)):
            match = _BUNDLE_NAME.match(entry.name)
            if match is None or entry.is_symlink() or _is_junction(entry) or not entry.is_dir():
                continue
            if not self._named(entry):
                candidates[match.group(1)] = entry
        referenced = self._digest_referenced(candidates) if candidates else set()
        for digest, entry in candidates.items():
            if self.halted():
                return
            if digest not in referenced:
                self._sweep_tree(entry, cutoff, under=("bundles",))
        for root in _MESH_ROOTS:
            for path in self._mesh_files(self.roots.imports / root):
                if self.halted():
                    return
                if self._file_named(path, root):
                    continue
                try:
                    if os.lstat(path).st_mtime >= cutoff:
                        continue
                except OSError:
                    continue
                self._remove(path, under=(root,))

    def _file_named(self, path: Path, root: str) -> bool:
        if self._named(path) or (path.suffix == ".json" and self._named(path.with_suffix(".msh"))):
            return True
        if root == "viewports":
            match = _VIEWPORT_NAME.match(path.name)
            return match is not None and match.group(1) in self.protected_viewports
        return False

    def _mesh_files(self, root: Path) -> Iterator[Path]:
        """Regular files under a mesh root, skipping its index, staging and any link."""

        if not root.is_dir() or root.is_symlink() or _is_junction(root):
            return
        for current, directories, files in os.walk(root, followlinks=False):
            here = Path(current)
            directories[:] = sorted(
                name for name in directories
                if not name.startswith(".")
                and not (here == root and name == "index")
                and not os.path.islink(here / name)
                and not _is_junction(here / name)
            )
            for name in sorted(files):
                path = here / name
                if name.startswith("."):
                    continue
                try:
                    if stat.S_ISREG(os.lstat(path).st_mode):
                        yield path
                except OSError:
                    continue

    def _sweep_tree(self, entry: Path, cutoff: float, *, under: tuple[str, ...]) -> None:
        try:
            real = self.roots.checked(entry, under=under)
            if _newest_mtime(real) >= cutoff:
                return
        except _Refused as exc:
            self._refuse(exc)
            return
        except OSError:
            return
        self._remove(real, under=under, tree=True)


def _entries(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


# -- when it runs ---------------------------------------------------------------------


class PreparedMeshCollector:
    """Runs collection off the request path: at startup and after jobs are deleted.

    Requests coalesce: while a pass runs, any number of requests make one more.
    A pass skipped because a mesh was being prepared, or a restart was
    pending, tries again after :data:`RETRY_DELAY`. Nothing waits for a pass;
    a failed one is logged and changes nothing more. Shutdown sets a stop flag
    the pass reads between rows and between file removals.
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
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        self._arguments = (cad_store, job_store, Path(data_dir))
        self._terminal = tuple(terminal_states)
        self._restart_pending = restart_pending
        self.startup_delay = startup_delay
        self.retry_delay = retry_delay
        self._task: asyncio.Task[None] | None = None
        self._again = False
        self._stop = threading.Event()

    def collect_now(self) -> CollectionReport:
        return collect_prepared_meshes(
            *self._arguments, terminal_states=self._terminal,
            restart_pending=self._restart_pending, should_stop=self._stop.is_set,
        )

    def request(self, *, delay: float = 0.0) -> None:
        """Schedule a pass on the running loop; never waits for it."""

        if self._stop.is_set():
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.get_running_loop().create_task(self._run(delay))

    async def _run(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        while not self._stop.is_set():
            self._again = False
            report = None
            try:
                report = await asyncio.to_thread(self.collect_now)
            except Exception:  # noqa: BLE001 - collection never takes the app down
                logger.warning("Prepared-mesh collection failed.", exc_info=True)
            if self._again:
                continue
            if report is not None and report.skipped in {"busy", "restart_pending"}:
                await asyncio.sleep(self.retry_delay)
                continue
            return

    async def startup(self) -> None:
        self.request(delay=self.startup_delay)

    async def shutdown(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "GRACE_PERIOD",
    "PRESS_BINDING_BOUND",
    "RETRY_DELAY",
    "CollectionReport",
    "PreparedMeshCollector",
    "collect_prepared_meshes",
    "is_stale",
    "keep_reasons",
    "prepared_mesh_use",
]
