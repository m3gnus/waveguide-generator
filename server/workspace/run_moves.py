"""Explicit, previewed archive moves. The journal is the filesystem/DB coordinator.

Only server-discovered WG run records are candidates. A saved plan is immutable;
execution rechecks its byte manifests under the same exclusion as archive writes.
No run.json member is edited, including cad.modelFile (lookup uses the state hash).
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Callable, Iterator
import uuid

from server.platform.acl_repair import path_has_reparse_point
from server.platform.json_file import json_bytes, write_json_atomic
from server.platform.rename import rename_absent
from server.workspace import archive_identity as identity
from server.workspace.archive import _capture_sidecars, _sidecar_document, archive_folder_slug
from server.workspace.portable_paths import path_segments


class MoveRefused(OSError):
    """A recoverable refusal, suitable for the dialog and HTTP 409."""


_GATE = threading.RLock()
_WRITERS: dict[str, Path] = {}
_BLOCKED: dict[str, list[Path]] = {}


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


@contextmanager
def archive_write(path: Path) -> Iterator[None]:
    """Track a writer for its whole resolve/write operation, without waiting on moves."""
    path = path.absolute()
    token = uuid.uuid4().hex
    with _GATE:
        if any(_overlap(path, blocked) for paths in _BLOCKED.values() for blocked in paths):
            raise MoveRefused("Runs are being moved")
        _WRITERS[token] = path
    try:
        yield
    finally:
        with _GATE:
            _WRITERS.pop(token, None)


def _safe(path: Path) -> None:
    # Check before reading; resolve() would erase the link evidence. A not-yet
    # created destination still needs every existing ancestor checked.
    existing = path
    while not existing.exists() and not existing.is_symlink():
        existing = existing.parent
    if path_has_reparse_point(existing):
        raise MoveRefused("Symbolic links and junctions cannot be moved.")
    for part in [existing, *existing.parents]:
        if (getattr(part.lstat(), "st_flags", 0) or 0) & 0x40000000:
            raise MoveRefused("Some files are not downloaded. Download them in Finder first.")


def _hash(path: Path) -> str:
    _safe(path)
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _tree(path: Path) -> list[Path]:
    """Preflight every entry and propagate directory-read errors before hashing."""
    _safe(path)
    result = [path]
    if path.is_dir():
        for child in sorted(path.iterdir()):
            result.extend(_tree(child))
    return result


def inventory(path: Path) -> dict[str, Any]:
    """Hash every regular file, preserve empty directories, refuse placeholders/links."""
    _safe(path)
    if not path.exists():
        raise MoveRefused(f"Folder is unavailable: {path}")
    entries = _tree(path)
    # Preflight the entire tree before opening even its first file.
    for entry in entries:
        _safe(entry)
        if not (entry.is_file() or entry.is_dir()):
            raise MoveRefused("Only regular files and folders can be moved.")
    files = {}
    directories = []
    for entry in entries:
        relative = str(entry.relative_to(path)) if entry != path else "."
        if entry.is_dir():
            directories.append(relative)
        else:
            files[relative] = {"size": entry.stat().st_size, "sha256": _hash(entry)}
    return {"files": files, "directories": directories}


def _verify(path: Path, manifest: dict[str, Any]) -> None:
    if inventory(path) != manifest:
        raise MoveRefused(f"Verification failed; originals were kept: {path}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _save(path: Path, record: dict[str, Any]) -> None:
    write_json_atomic(path, record)
    _sync_dir(path.parent)


def _mkdir(path: Path) -> None:
    missing = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for directory in reversed(missing):
        _safe(directory)
        directory.mkdir(exist_ok=True)
        _sync_dir(directory.parent)


def _remove_members(path: Path, manifest: dict) -> None:
    """Unlink only journaled, currently matching files; never recursively remove."""
    _safe(path)
    for name, evidence in manifest["files"].items():
        member = path if name == "." else path / name
        _safe(member)
        if member.exists():
            _verify(member, {"files": {".": evidence}, "directories": []})
            member.unlink()
            _sync_dir(member.parent)
    for name in sorted(manifest["directories"], key=lambda value: len(Path(value).parts), reverse=True):
        directory = path if name == "." else path / name
        _safe(directory)
        if directory.exists():
            directory.rmdir()  # Unexpected contents are preserved and reported.
            _sync_dir(directory.parent)
    if path.exists():
        raise MoveRefused(f"Unjournaled partial contents were kept: {path}")


@contextmanager
def submission_location(store: Any, config: dict, metadata: dict) -> Iterator[None]:
    """Assign and persist under the short reference-publication CAD lock.

    Pending moves never delay submissions. Publication reconciles assignments
    made before the registry update; archive writes independently refuse with 409.
    """
    if store is None:
        metadata["archive"] = identity.submission_archive(store, config, metadata)
        yield
        return
    with store._lock:
        archive = identity.submission_archive(store, config, metadata)
        metadata["archive"] = archive
        if metadata.get("imported_geometry") is not None:
            metadata["imported_geometry"]["archive_stem"] = archive["folder"]
        yield


class RunMoves:
    """One move owner per app. Stores are duck typed to keep workspace a lower layer."""

    def __init__(self, data_dir: Path, jobs: Any, cad: Any, runs_root: Callable[[], Path],
                 *, cad_source: Callable[[dict], dict] = lambda row: {},
                 verify_document: Callable[[str, str, Path], bool] = lambda lineage, digest, path: False,
                 known_roots: Callable[[], list[Path]] = lambda: []) -> None:
        self.directory = data_dir / "moves"
        self.jobs, self.cad, self.runs_root = jobs, cad, runs_root
        self.cad_source, self.verify_document, self.known_roots = cad_source, verify_document, known_roots
        self._mutex = threading.RLock()
        self._events: list[dict] = []
        self.recovered_reports: list[dict] = []
        # Tests inject a BaseException here, emulating process death after durable steps.
        self.after_step: Callable[[str], None] = lambda step: None

    def take_events(self) -> list[dict]:
        with self._mutex:
            result, self._events = self._events, []
            return result

    def _journal_path(self, move_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", move_id):
            raise MoveRefused("Unknown move.")
        return self.directory / f"{move_id}.json"

    def read(self, move_id: str) -> dict:
        try:
            return json.loads(self._journal_path(move_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MoveRefused("Move plan is unavailable. Preview it again.") from exc

    def _step(self, record: dict, phase: str) -> None:
        record["phase"] = phase
        _save(self._journal_path(record["id"]), record)
        self.after_step(phase)

    def _rows(self) -> list[dict]:
        return self.jobs.list_jobs(limit=2147483647)[0]

    def _location(self, row: dict, root: Path) -> Path:
        recorded = (row.get("task_metadata") or {}).get("archived_path") or row.get("_move_location")
        location = identity.job_archive_location(row, self.cad_source(row))
        return Path(recorded) if recorded else root / location.folder / location.run

    def _roots(self, rows: list[dict], root: Path) -> set[Path]:
        roots = {root, *self.known_roots()}
        for row in rows:
            path = (row.get("task_metadata") or {}).get("archived_path")
            if path and Path(path).is_absolute():
                roots.add(Path(path).parent.parent)
        return {p.absolute() for p in roots}

    def _discover(self, rows: list[dict], roots: set[Path]) -> list[dict]:
        # Older WG did not record an absolute path. Discover only a schema-1
        # run.json for a still-known job, under a Runs root WG already knows.
        legacy = {row["id"]: row for row in rows if not (row.get("task_metadata") or {}).get("archived_path")}
        if not legacy:
            return rows
        found: dict[str, list[Path]] = {}
        for root in sorted(roots):
            if not root.is_dir():
                continue
            _safe(root)
            for path in (run / "run.json" for folder in sorted(root.iterdir()) if folder.is_dir()
                         for run in sorted(folder.iterdir()) if run.is_dir() and (run / "run.json").is_file()):
                if not re.fullmatch(r"\d+_.+", path.parent.name):
                    continue
                _safe(path)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    job_id = (data.get("run") or {}).get("jobId")
                    if data.get("schemaVersion") == 1 and job_id in legacy:
                        found.setdefault(job_id, []).append(path.parent)
                except (FileNotFoundError, ValueError, AttributeError):
                    continue
        result = []
        for row in rows:
            locations = found.get(row["id"], [])
            if len(locations) > 1:
                raise MoveRefused("More than one WG folder records the same run. Resolve those copies before moving it.")
            result.append({**row, "_move_location": str(locations[0])} if locations else row)
        return result

    def _busy(self, rows: list[dict], paths: list[Path], root: Path) -> None:
        for row in rows:
            metadata = row.get("task_metadata") or {}
            busy = row.get("status") not in {"complete", "error", "failed", "cancelled", "interrupted"}
            busy |= row.get("status") == "complete" and not metadata.get("archived_at")
            busy |= bool(metadata.get("archiving") or metadata.get("auto_exporting"))
            if busy and any(_overlap(self._location(row, root), path) for path in paths):
                raise MoveRefused("A run in these folders is queued, solving, archiving or exporting. Wait for it to finish.")

    def _busy_publication(self, record: dict, rows: list[dict], *, undo: bool = False) -> None:
        known = {ref["job_id"] for ref in record["references"] if not ref.get("reconciled")} | set(record.get("destination_jobs", []))
        original = set(record.get("undo_jobs" if undo else "journal_jobs", known))
        if undo and not record.get("undoing"):
            original = {row["id"] for row in rows}
        keys = record.get("folder_change", {}).get("keys", [record["key"]])
        root = Path(record["root"])
        paths = list(_BLOCKED[record["id"]])
        busy_rows = []
        for row in rows:
            later = row["id"] not in original
            metadata = row.get("task_metadata") or {}
            # Later queued jobs have no writer; their assignment can follow the
            # move. Active solve/archive/export work must finish before replay.
            if later and row.get("status") == "queued" and not (metadata.get("archiving") or metadata.get("auto_exporting")):
                continue
            busy_rows.append(row)
            if later and identity.job_archive_location(row, self.cad_source(row)).key in keys:
                paths.append(self._location(row, root).parent)
        self._busy(busy_rows, paths, root)

    def _candidate(self, path: Path, row: dict, root: Path) -> bool:
        if not re.fullmatch(r"\d+_.+", path.name) or path != self._location(row, root):
            return False
        _safe(path)
        _safe(path / "run.json")
        try:
            record = json.loads((path / "run.json").read_text(encoding="utf-8"))
            return record.get("schemaVersion") == 1 and (record.get("run") or {}).get("jobId") == row["id"]
        except (FileNotFoundError, ValueError, AttributeError):
            return False

    def _owned_project(self, folder: Path, rows: list[dict], root: Path, key: str) -> None:
        """Whole-folder rename needs positive evidence for every child, including CAD-only."""
        inventory(folder)
        pointer = folder / "design.json"
        if not pointer.is_file():
            raise MoveRefused("Only a model/design folder created by WG can be renamed.")
        record = json.loads(pointer.read_text(encoding="utf-8"))
        aliases = {str(r[0]) for r in self.cad._connect().execute(
            "SELECT owner_key FROM archive_folders WHERE folder=? COLLATE NOCASE", (folder.name,))}
        if identity._record_key(record) not in {key, *aliases}:
            raise MoveRefused("This folder belongs to another design.")
        known = {self._location(row, root): row for row in rows}
        for child in folder.iterdir():
            if child.name in {"design.json", ".DS_Store"} and child.is_file():
                continue
            if child in known and self._candidate(child, known[child], root):
                continue
            if child.name == "cad" and child.is_dir():
                if set(child.iterdir()) <= self._verified_cad_members(child, key):
                    continue
            raise MoveRefused(f"Folder contains something WG cannot identify: {child.name}")

    def _owned_cad_only(self, folder: Path, key: str) -> None:
        _safe(folder)
        if not key.startswith("lineage:") or any(child.name != "cad" for child in folder.iterdir()):
            raise MoveRefused("Destination contains files WG cannot identify.")
        directory = folder / "cad"
        if not directory.is_dir():
            raise MoveRefused("Destination was not created by WG.")
        if not set(directory.iterdir()) <= self._verified_cad_members(directory, key):
            raise MoveRefused("Destination contains files WG cannot identify.")

    def _verified_cad_members(self, directory: Path, key: str) -> set[Path]:
        inventory(directory)  # Refuse dataless files before opening sidecars.
        allowed = set()
        for sidecar in _capture_sidecars(directory):
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            digest = data.get("returnStateHash", "")
            document = _sidecar_document(sidecar)
            if document and self.verify_document(key.removeprefix("lineage:"), digest, document):
                allowed.update({document, sidecar})
        return allowed

    def preview(self, key: str, name: str, *, selected: list[str] | None = None,
                gather: bool = True, rename: bool = False) -> dict:
        with self._mutex:
            root = Path(self.runs_root()).absolute()
            _safe(root)
            if not root.is_dir():
                raise MoveRefused("The Runs folder is unavailable. Reconnect it first.")
            folder_name = identity.registered_folder(self.cad, key)
            if not folder_name:
                # Opening an old design can precede its first new solve. Reserve
                # its future folder through the existing allocator, without
                # creating or moving a filesystem folder.
                if not key.startswith(("design:", "lineage:", "model:")):
                    raise MoveRefused("This design has no assigned run folder yet.")
                folder_name = identity.claim_project_folder(self.cad, key, name)
            rows = self._rows()
            roots = self._roots(rows, root)
            rows = self._discover(rows, roots)
            if path_segments(folder_name, "assigned folder") != [folder_name]:
                raise MoveRefused("The assigned folder is invalid.")
            folder = root / folder_name
            target_name = archive_folder_slug(name, "design") if rename else folder_name
            path_segments(target_name, "folder")
            target = root / target_name
            if target != folder and (target.exists() or any(str(r[0]).casefold() == target_name.casefold() for r in self.cad._connect().execute(
                    "SELECT folder FROM archive_folders WHERE folder != ? COLLATE NOCASE", (folder_name,)))):
                raise MoveRefused("A folder with that name already exists. Choose another design name.")
            operations = []
            if folder.exists() and (folder / "design.json").exists():
                self._owned_project(folder, rows, root, key)
            elif folder.exists():
                # F2 permits a CAD-only legacy folder without a pointer. It must
                # contain only an empty cad/ or retained-verified CAD captures.
                self._owned_cad_only(folder, key)
            if target != folder and folder.exists():
                self._owned_project(folder, rows, root, key)
                operations.append(self._operation(folder, target, "rename"))
            candidates = []
            chosen = []
            for row in rows:
                source = self._location(row, root)
                if source.parent == folder or source.parent == target or source.parent.parent not in roots:
                    continue
                source_key = identity.job_archive_location(row, self.cad_source(row)).key
                same = source_key == key
                label = str(row.get("label") or "")
                by_name = bool(re.fullmatch(r"(?:\d{6}|\d{4}-\d{2}-\d{2})?_?" + re.escape(name) + r"\d*(?:_(?:\d{6}|\d{4}-\d{2}-\d{2}))?", label, re.IGNORECASE))
                legacy = not source_key.startswith(("design:", "lineage:", "model:"))
                if not (same or (by_name and legacy)) or not source.exists():
                    continue
                if not self._candidate(source, row, root):
                    continue
                manifest = inventory(source)
                checked = same and not name.casefold().startswith("untitled")
                candidate = {"job_id": row["id"], "source": str(source), "destination": "", "preselected": checked,
                             "reason": "Same design or model" if same else "Name only — check before moving",
                             "files": len(manifest["files"]), "bytes": sum(f["size"] for f in manifest["files"].values())}
                candidates.append(candidate)
                if (row["id"] in selected) if selected is not None else checked:
                    chosen.append((row, source, manifest))
            occupied = {p.name.casefold() for p in folder.iterdir()} if folder.is_dir() else set()
            for row, source, manifest in chosen:
                run_name = source.name
                count = 2
                while run_name.casefold() in occupied:
                    run_name = f"{source.name}-{count}"
                    count += 1
                occupied.add(run_name.casefold())
                dest = target / run_name
                op = self._operation(source, dest, "run", manifest)
                op["job_id"] = row["id"]
                operations.append(op)
                next(c for c in candidates if c["job_id"] == row["id"])["destination"] = str(dest)
            for candidate in candidates:
                candidate["destination"] = candidate["destination"] or str(target / Path(candidate["source"]).name)
                ancestor = Path(candidate["source"])
                candidate["method"] = "rename" if ancestor.stat().st_dev == root.stat().st_dev else "copy_verify_remove"
            affected = [folder, target, *[Path(op["src"]).parent for op in operations if op["kind"] != "rename"]]
            with self.cad._lock:
                self._busy(self._rows(), affected, root)
            # Gather existing destination runs as well, even if no earlier runs were selected.
            gather_ops = self._gather(operations, rows, root, folder, target, key) if gather else []
            operations += gather_ops
            for op in operations:
                if len(op["dst"]) > 220 or any(len(str(Path(op["dst"]) / p)) > 240 for p in op["manifest"]["files"]):
                    raise MoveRefused("The destination path is too long. Choose a shorter WG folder or design name.")
            record = {"schemaVersion": 1, "id": uuid.uuid4().hex, "created_at": _now(), "phase": "planned",
                      "key": key, "name": name, "root": str(root), "folder": str(folder), "target": str(target),
                      "pointer_before_bytes": base64.b64encode((folder / "design.json").read_bytes()).decode() if (folder / "design.json").is_file() else None,
                      "pointer_before": (folder / "design.json").read_text(encoding="utf-8") if (folder / "design.json").is_file() else None,
                      "destination_jobs": [row["id"] for row in rows if self._location(row, root).parent in {folder, target}],
                      "operations": operations, "candidates": candidates, "selected": [row["id"] for row, _, _ in chosen],
                      "gather": gather, "rename": rename, "error": None, "references": [], "cleanup": []}
            record["journal_jobs"] = [row["id"] for row in rows]
            # Assignment publication is independent of filesystem operations: an
            # allocated project can be renamed before its first archive exists.
            record["folder_change"] = {"before": str(folder), "after": str(target), "keys": sorted({key, *(
                str(row[0]) for row in self.cad._connect().execute(
                    "SELECT owner_key FROM archive_folders WHERE folder=? COLLATE NOCASE", (folder_name,)))})}
            self._references(record, rows)
            moved_sources = {Path(op["src"]) for op in operations if op["kind"] == "run"}
            record["old_folders"] = []
            for parent in sorted({path.parent for path in moved_sources}):
                leftovers = [child for child in parent.iterdir() if child not in moved_sources]
                if not all(child.name in {"design.json", ".DS_Store"} or (child.name == "cad" and child.is_dir() and not any(child.iterdir())) for child in leftovers):
                    continue
                source_keys = {ref["source_key"] for ref in record["references"] if Path(ref["before"]).parent == parent}
                pointer = parent / "design.json"
                if pointer.exists():
                    data = json.loads(pointer.read_text(encoding="utf-8"))
                    if data.get("schemaVersion") != 1 or identity._record_key(data) not in source_keys:
                        continue
                record["old_folders"].append(str(parent))
                for child in leftovers:
                    _safe(child)
                    if child.name == "design.json" and child.is_file():
                        record["cleanup"].append({"path": str(child), "source_bytes": base64.b64encode(child.read_bytes()).decode()})
            record["summary"] = {"runs": len(chosen), "files": sum(c["files"] for c in candidates if c["job_id"] in record["selected"]),
                                 "bytes": sum(c["bytes"] for c in candidates if c["job_id"] in record["selected"]),
                                 "fusion_files": len([op for op in gather_ops if op["kind"] == "cad"]),
                                 "duplicates": sum(op.get("duplicate", False) for op in gather_ops if op["kind"] == "cad"),
                                 "saved_bytes": sum(sum(f["size"] for f in op["manifest"]["files"].values()) for op in gather_ops if op["kind"] == "cad" and op.get("duplicate")),
                                 "rename_runs": sum(Path(ref["before"]).parent == folder for ref in record["references"]) if rename else 0,
                                 "rename_files": len(operations[0]["manifest"]["files"]) if operations and operations[0]["kind"] == "rename" else 0,
                                 "rename_bytes": sum(f["size"] for f in operations[0]["manifest"]["files"].values()) if operations and operations[0]["kind"] == "rename" else 0}
            _mkdir(self.directory)
            _save(self._journal_path(record["id"]), record)
            return record

    def _operation(self, src: Path, dst: Path, kind: str, manifest: dict | None = None) -> dict:
        ancestor = dst.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        _safe(ancestor)
        return {"src": str(src), "dst": str(dst), "kind": kind, "manifest": manifest or inventory(src),
                "method": "rename" if src.stat().st_dev == ancestor.stat().st_dev else "copy_verify_remove", "state": "pending",
                "source_identity": [src.stat().st_dev, src.stat().st_ino]}

    def _gather(self, operations: list[dict], rows: list[dict], root: Path, folder: Path, target: Path, key: str) -> list[dict]:
        if not key.startswith("lineage:"):
            return []
        lineage = key.removeprefix("lineage:")
        result = []
        seen: dict[str, Path] = {}
        sidecars: dict[tuple[str, str], tuple[Path, dict, bytes]] = {}
        protected: dict[Path, Path] = {}
        for sidecar in _capture_sidecars(folder / "cad"):
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            digest = str(data.get("returnStateHash") or "")
            document = _sidecar_document(sidecar)
            if document is None or not self.verify_document(lineage, digest, document):
                raise MoveRefused("A Fusion file could not be verified. Originals were kept.")
            checksum = _hash(document)
            # Protected records enumerate first, so an ordinary copy recreated by
            # an older writer deduplicates into the existing protected original.
            dest = seen.get(checksum) or target / "cad" / document.name
            if not dest.stem.endswith(".pinned"):
                dest = dest.with_name(dest.stem + ".pinned" + dest.suffix)
            if document not in protected and dest != target / "cad" / document.name:
                duplicate = checksum in seen
                pin = self._operation(document, dest, "cad" if duplicate else "pin")
                pin.update(live_src=str(target / "cad" / document.name),
                           method="gather" if duplicate else "rename", duplicate=duplicate)
                result.append(pin)
            protected[document] = dest
            seen.setdefault(checksum, dest)
            dest_sidecar = target / "cad" / sidecar.name
            content = sidecar.read_bytes()
            if (checksum, digest) in sidecars:
                dest_sidecar, _, content = sidecars[(checksum, digest)]
            elif document.name != dest.name or not sidecar.name.endswith(".cad.json"):
                own_pinned_name = document.stem.removesuffix(".pinned") + ".pinned" + document.suffix
                if data.get("modelFile") is None and dest.name == own_pinned_name:
                    dest_sidecar = dest.with_suffix(".cad.json")
                else:
                    dest_sidecar, _, content = self._gathered_sidecar(data, dest, digest)
            live_sidecar = target / "cad" / sidecar.name
            if dest_sidecar != live_sidecar:
                if content == sidecar.read_bytes() and (checksum, digest) not in sidecars:
                    pin = self._operation(sidecar, dest_sidecar, "pin")
                    pin.update(live_src=str(live_sidecar), method="rename")
                else:
                    pin = self._sidecar_operation(sidecar, dest_sidecar, live_sidecar, content)
                result.append(pin)
            manifest = self._content_manifest(content)
            sidecars[(checksum, digest)] = (dest_sidecar, manifest, content)
        sources = []
        for row in rows:
            source = self._location(row, root)
            op = next((op for op in operations if op["kind"] == "run" and op.get("job_id") == row["id"]), None)
            if source.parent == folder or op:
                moved = Path(op["dst"]) if op else target / source.name
                if source.exists() and self._candidate(source, row, root):
                    sources.append((source, moved))
        for source, moved in sources:
            for document in sorted(path for path in source.iterdir() if path.suffix == ".f3d"):
                sidecar = document.with_suffix(".cad.json")
                if not sidecar.is_file():
                    continue
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                digest = str(data.get("returnStateHash") or "")
                if not self.verify_document(lineage, digest, document):
                    raise MoveRefused("A Fusion file could not be verified. Originals were kept.")
                checksum = _hash(document)
                duplicate = checksum in seen
                dest = seen.get(checksum) or target / "cad" / f"{document.stem.removesuffix('.pinned')}_{checksum[:12]}.pinned.f3d"
                seen[checksum] = dest
                if (checksum, digest) not in sidecars:
                    sidecars[(checksum, digest)] = self._gathered_sidecar(data, dest, digest)
                sidecar_dest, _, content = sidecars[(checksum, digest)]
                for src, dst, kind in ((document, dest, "cad"), (sidecar, sidecar_dest, "sidecar")):
                    op = (self._sidecar_operation(src, dst, moved / src.name, content) if kind == "sidecar"
                          else self._operation(src, dst, kind))
                    op.update({"live_src": str(moved / src.name), "method": "gather", "duplicate": duplicate,
                               "digest": digest, "lineage": lineage})
                    result.append(op)
        return result

    @staticmethod
    def _content_manifest(content: bytes) -> dict:
        return {"files": {".": {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}}, "directories": []}

    def _gathered_sidecar(self, data: dict, document: Path, digest: str) -> tuple[Path, dict, bytes]:
        # Every state retains independent ingestion evidence even when model
        # bytes are shared. This is the one alias writer for both source tiers.
        digest_name = hashlib.sha256(digest.encode()).hexdigest()[:16]
        destination = document.with_name(f"{document.stem.removesuffix('.pinned')}_{digest_name}.pinned.cad.json")
        data = {**data, "modelFile": document.name}
        content = json_bytes(data)
        return destination, self._content_manifest(content), content

    def _sidecar_operation(self, src: Path, dst: Path, live_src: Path, content: bytes) -> dict:
        op = self._operation(src, dst, "sidecar")
        op.update(live_src=str(live_src), method="gather",
                  source_bytes=base64.b64encode(src.read_bytes()).decode(),
                  content=base64.b64encode(content).decode(), destination_manifest=self._content_manifest(content))
        return op

    def _references(self, record: dict, rows: list[dict], *, undo: bool = False) -> None:
        for row in rows:
            before = self._location(row, Path(record["root"]))
            after = before
            location = identity.job_archive_location(row, self.cad_source(row))
            change = record.get("folder_change", {"before": record["folder"], "after": record["target"], "keys": [record["key"]]})
            source_folder = Path(change["after"] if undo else change["before"])
            destination_folder = Path(change["before"] if undo else change["after"])
            original_jobs = record.get("undo_jobs" if undo else "journal_jobs", {
                ref["job_id"] for ref in record["references"]} | set(record.get("destination_jobs", [])))
            later = row["id"] not in original_jobs
            if location.key in change["keys"] and (before.parent == source_folder or later):
                if later and before.parent != destination_folder and before.exists():
                    raise MoveRefused("A later run has written an archive outside the final folder. Originals were kept.")
                after = destination_folder / location.run
            for op in record["operations"]:
                if op["kind"] in {"rename", "run"}:
                    src = Path(op["dst"] if undo else op["src"])
                    if before == src or src in before.parents:
                        after = Path(op["src"] if undo else op["dst"]) / before.relative_to(src)
            if before != after:
                if undo:
                    before, after = after, before
                archive = dict((row.get("task_metadata") or {}).get("archive", {}))
                imported = {key: value for key, value in ((row.get("task_metadata") or {}).get("imported_geometry") or {}).items() if key == "archive_stem"}
                if undo:
                    archive.update(folder=before.parent.name, run=before.name)
                    if imported:
                        imported["archive_stem"] = before.parent.name
                record["references"].append({"job_id": row["id"], "before": str(before), "after": str(after),
                                             "assignment_only": not self._location(row, Path(record["root"])).exists(),
                                             "archive": archive,
                                             "exported_files": (row.get("task_metadata") or {}).get("exported_files", []),
                                             "source_key": identity.job_archive_location(row, self.cad_source(row)).key,
                                             "imported_location": imported})

    def _block(self, record: dict) -> None:
        paths = [Path(record["folder"]), Path(record["target"])]
        paths += [Path(op["src"]).parent for op in record["operations"] if op["kind"] != "rename"]
        with _GATE:
            if any(_overlap(path, writer) for path in paths for writer in _WRITERS.values()):
                raise MoveRefused("A run in these folders is exporting. Wait for it to finish.")
            if any(_overlap(path, other) for token, blocked in _BLOCKED.items() if token != record["id"] for path in paths for other in blocked):
                raise MoveRefused("Runs are being moved")
            _BLOCKED[record["id"]] = paths

    def execute(self, move_id: str, *, confirmed: bool = False) -> dict:
        if not confirmed:
            raise MoveRefused("Confirm the preview before moving any files.")
        with self._mutex:
            record = self.read(move_id)
            if record["phase"] != "planned":
                return record
            self._block(record)
            try:
                with self.cad._lock:
                    rows = self._rows()
                    self._busy(rows, _BLOCKED[record["id"]], Path(record["root"]))
                if Path(self.runs_root()).absolute() != Path(record["root"]):
                    raise MoveRefused("The Runs folder changed. Preview the move again.")
                current_folder = Path(record["folder"])
                if current_folder.exists():
                    if (current_folder / "design.json").exists():
                        self._owned_project(current_folder, rows, Path(record["root"]), record["key"])
                    else:
                        self._owned_cad_only(current_folder, record["key"])
                for ref in record["references"]:
                    fresh = self.jobs.get_job_row(ref["job_id"])
                    if fresh and not ref.get("assignment_only") and not (fresh.get("task_metadata") or {}).get("archived_path"):
                        fresh = {**fresh, "_move_location": ref["before"]}
                        if Path(ref["before"]).parent.parent not in self._roots(rows, Path(record["root"])) or not self._candidate(Path(ref["before"]), fresh, Path(record["root"])):
                            raise MoveRefused("An earlier run folder is no longer available. Preview it again.")
                    if not fresh or self._location(fresh, Path(record["root"])) != Path(ref["before"]):
                        raise MoveRefused("A run location changed. Preview the move again.")
                for op in record["operations"]:
                    _verify(Path(op["src"]), op["manifest"])
                    if op["kind"] in {"run", "rename"} and Path(op["dst"]).exists():
                        raise MoveRefused("A destination now exists. Preview the move again.")
                self._step(record, "prepared")
                self._resume(record)
            except Exception as exc:
                if record["phase"] == "planned":
                    record["phase"] = "refused"
                self._attention(record, exc)
            return record

    def _attention(self, record: dict, exc: Exception) -> None:
        # Keep the replayable phase, but never strand archive writers behind a
        # failed attempt. A retry must acquire exclusion and reverify everything.
        record["error"] = "Move needs attention. " + str(exc)
        try:
            _save(self._journal_path(record["id"]), record)
        finally:
            with _GATE:
                _BLOCKED.pop(record["id"], None)

    def _resume(self, record: dict) -> None:
        # Publication is replayable from physical evidence; state is always persisted
        # BEFORE mutation. A crash between rename and state update is unambiguous.
        if record["folder"] != record["target"] and not any(op["kind"] == "rename" for op in record["operations"]) and Path(record["folder"]).exists():
            raise MoveRefused("The original folder appeared after preview. Its contents were kept; preview the move again.")
        for index, op in enumerate(record["operations"]):
            if op["state"] in {"published", "removed"}:
                continue
            src = Path(op.get("live_src", op["src"]))
            dst = Path(op["dst"])
            _safe(src)
            _safe(dst)
            _mkdir(dst.parent)
            self._step(record, f"publish:{index}")
            if op["method"] == "gather":
                if dst.exists():
                    _verify(dst, op.get("destination_manifest", op["manifest"]))
                    op["created"] = op.get("created", False)
                else:
                    self._publish_copy(record, op, src, dst)
            elif dst.exists() and not src.exists():
                _verify(dst, op["manifest"])
            elif op["method"] == "rename":
                if dst.exists():
                    raise MoveRefused("Both source and destination exist; manual review is required.")
                _verify(src, op["manifest"])
                rename_absent(src, dst)
                _sync_dir(src.parent)
                _sync_dir(dst.parent)
                self.after_step(f"renamed:{index}")
            else:
                self._publish_copy(record, op, src, dst)
            op["state"] = "published"
            self._step(record, f"published:{index}")
        self._step(record, "verify_all")
        self._verify_destinations(record)
        if not record.get("all_verified"):
            for op in record["operations"]:
                if op["method"] == "copy_verify_remove":
                    _verify(Path(op["src"]), op["manifest"])
        record["all_verified"] = True
        self._step(record, "verified")
        self._commit_references(record)
        self._step(record, "references_committed")
        # The *whole* move has verified before the first original is removed.
        for index, op in enumerate(record["operations"]):
            if op["state"] == "removed":
                continue
            if op["method"] in {"copy_verify_remove", "gather"}:
                src = Path(op.get("live_src", op["src"]))
                self._step(record, f"remove:{index}")
                if src.exists():
                    self._remove_original(record, op, src)
                self.after_step(f"removed:{index}")
            op["state"] = "removed"
            self._step(record, f"removed_recorded:{index}")
        self._cleanup(record)
        record["final"] = {str(p): inventory(p) for p in {Path(record["target"]), *[Path(op["dst"]) for op in record["operations"]]} if p.exists()}
        record["error"] = None
        self._step(record, "done")
        with _GATE:
            _BLOCKED.pop(record["id"], None)

    def _publish_copy(self, record: dict, op: dict, src: Path, dst: Path) -> None:
        partial = dst.with_name(dst.name + ".partial-" + record["id"])
        if dst.exists():
            _verify(dst, op.get("destination_manifest", op["manifest"]))
            return
        # A safe source must exist before discarding even our own partial bytes.
        manifest = op.get("destination_manifest", op["manifest"])
        content = base64.b64decode(op["content"]) if op.get("content") else None
        _verify(src, op["manifest"])
        if partial.exists():
            if not op.get("created") or "partial_manifest" not in op:
                raise MoveRefused(f"Unjournaled partial contents were kept: {partial}")
            _remove_members(partial, op["partial_manifest"])
        op["created"] = True
        self._write_partial(record, op, partial, src, manifest, content=content)
        self.after_step("copied:" + op["dst"])
        _verify(partial, op.get("destination_manifest", op["manifest"]))
        rename_absent(partial, dst)
        _sync_dir(dst.parent)
        self.after_step("copy_published:" + op["dst"])

    def _write_partial(self, record: dict, owner: dict, partial: Path, src: Path,
                       manifest: dict, *, content: bytes | None = None) -> None:
        # Only completed, fsynced members are deletion evidence. A process dying
        # during a write leaves unverified bytes for manual attention.
        evidence: dict = {"files": {}, "directories": []}
        owner["partial_manifest"] = evidence
        self._step(record, record["phase"])
        for name in manifest["directories"]:
            directory = partial if name == "." else partial / name
            directory.mkdir()
            _sync_dir(directory.parent)
            evidence["directories"].append(name)
            self._step(record, record["phase"])
        for name, expected in manifest["files"].items():
            target = partial if name == "." else partial / name
            source = src if name == "." else src / name
            with target.open("xb") as output:
                if content is not None:
                    output.write(content)
                else:
                    with source.open("rb") as incoming:
                        shutil.copyfileobj(incoming, output)
                output.flush()
                if content is None:
                    shutil.copystat(source, target)
                os.fsync(output.fileno())
            _verify(target, {"files": {".": expected}, "directories": []})
            _sync_dir(target.parent)
            evidence["files"][name] = expected
            self._step(record, record["phase"])

        for name in reversed(manifest["directories"]):
            source = src if name == "." else src / name
            target = partial if name == "." else partial / name
            shutil.copystat(source, target)
            _sync_dir(target)

    def _expected_destinations(self, record: dict) -> dict[Path, dict]:
        expected = {}
        transforms = [op for op in record["operations"] if op["kind"] in {"pin", "cad", "sidecar"}]
        for op in record["operations"]:
            for name, evidence in op.get("destination_manifest", op["manifest"])["files"].items():
                path = Path(op["dst"]) if name == "." else Path(op["dst"]) / name
                for transform in transforms:
                    if path == Path(transform.get("live_src", transform["src"])):
                        path = Path(transform["dst"])
                        evidence = transform.get("destination_manifest", transform["manifest"])["files"]["."]
                if path in expected and expected[path] != evidence:
                    raise MoveRefused("The plan contains conflicting Fusion evidence. Preview it again.")
                expected[path] = evidence
        return expected

    def _verify_destinations(self, record: dict) -> None:
        # Never trust a past verification after a restart. This is repeated
        # before resuming deletion, even if reference commits already landed.
        for member, expected in self._expected_destinations(record).items():
            if member == Path(record["target"]) / "design.json":
                self._verify_pointer(record, member)
                continue
            if not member.is_file() or member.stat().st_size != expected["size"] or _hash(member) != expected["sha256"]:
                raise MoveRefused(f"Verification failed; remaining originals were kept: {member}")

    def _verify_pointer(self, record: dict, path: Path, *, undo: bool = False) -> None:
        """Accept only previewed bytes or an exact journaled replacement."""
        _safe(path)
        original = record.get("pointer_before")
        content = base64.b64decode(record["pointer_before_bytes"]) if record.get("pointer_before_bytes") is not None else original.encode() if original is not None else None
        allowed = [self._content_manifest(content)["files"]["."] if content is not None else None]
        for op in record["operations"]:
            if op["kind"] == "rename":
                allowed = [op["manifest"]["files"].get("design.json")]
        if undo:
            final = record["final"].get(str(Path(record["target"])), {})
            allowed = [final.get("files", {}).get("design.json")]
        allowed.extend(item["after"] for item in record.get("pointer_writes", []))
        current = None
        if path.exists():
            if not path.is_file():
                raise MoveRefused(f"Changed design.json was kept: {path}")
            current = {"size": path.stat().st_size, "sha256": _hash(path)}
        if current not in allowed:
            raise MoveRefused(f"Changed design.json was kept: {path}")

    def _replace_pointer(self, record: dict, path: Path, data: dict, *, undo: bool = False) -> None:
        self._verify_pointer(record, path, undo=undo)
        evidence = self._content_manifest(json_bytes(data))["files"]["."]
        writes = record.setdefault("pointer_writes", [])
        if not any(item["after"] == evidence for item in writes):
            writes.append({"path": str(path), "after": evidence})
            self._step(record, "pointer_intent")
        # The journal fsync (and any crash hook) precedes this last byte check.
        self._verify_pointer(record, path, undo=undo)
        _save(path, data)
        self.after_step("pointer_replaced:" + str(path))

    def _remove_original(self, record: dict, op: dict, src: Path) -> None:
        actual = inventory(src)
        original = op["manifest"]
        if (not op.get("removal_started") and actual != original) or any(
                name not in original["files"] or original["files"][name] != evidence
                for name, evidence in actual["files"].items()) or not set(actual["directories"]) <= set(original["directories"]):
            raise MoveRefused("An original changed during removal. Remaining originals were kept.")
        op["removal_started"] = True
        self._step(record, record["phase"])
        for name in actual["files"]:
            member = src if name == "." else src / name
            _safe(member)
            # Validate the retained counterpart immediately before each unlink.
            self._verify_destinations(record)
            expected = original["files"][name]
            if not member.is_file() or member.stat().st_size != expected["size"] or _hash(member) != expected["sha256"]:
                raise MoveRefused("An original changed during removal. Remaining originals were kept.")
            member.unlink()
            _sync_dir(member.parent)
            self.after_step("source_file_removed:" + str(member))
        if src.is_dir():
            for name in sorted((name for name in original["directories"] if name != "."), key=lambda name: len(Path(name).parts), reverse=True):
                directory = src / name
                _safe(directory)
                if directory.exists():
                    directory.rmdir()
            src.rmdir()
            _sync_dir(src.parent)

    def _commit_references(self, record: dict, *, undo: bool = False) -> None:
        with self.cad._lock:
            rows = self._rows()
            self._busy_publication(record, rows, undo=undo)
            known = {ref["job_id"] for ref in record["references"]}
            self._references(record, [row for row in rows if row["id"] not in known], undo=undo)
            for ref in record["references"]:
                if ref["job_id"] not in known:
                    ref["reconciled"] = True
            self._step(record, "reference_intent")
            self._publish_references(record, undo=undo)
        self._publish_pointers(record, undo=undo)

    def _publish_references(self, record: dict, *, undo: bool = False) -> None:
        # Separate SQLite owners: journal replays both transactions idempotently.
        pointer_folder = record["folder"] if undo and any(op["kind"] == "rename" for op in record["operations"]) else record["target"]
        self._verify_pointer(record, Path(pointer_folder) / "design.json", undo=undo)
        events = []
        with self.jobs._lock, self.jobs._transaction() as conn:
            for ref in record["references"]:
                row = conn.execute("SELECT task_metadata_json FROM simulation_jobs WHERE id=?", (ref["job_id"],)).fetchone()
                if not row:
                    raise MoveRefused("A run was removed from history during the move.")
                metadata = json.loads(row[0] or "{}")
                previous_metadata = dict(metadata)
                destination = Path(ref["before"] if undo else ref["after"])
                archive = dict(ref["archive"])
                if not undo:
                    archive.update({"key": record["key"], "folder": destination.parent.name, "run": destination.name})
                    moved = list(archive.get("moved") or [])
                    if not any(item.get("move") == record["id"] for item in moved):
                        moved.append({"from": ref["before"], "at": record["created_at"], "move": record["id"]})
                    archive["moved"] = moved
                metadata.update({"archive": archive, "archived_path": str(destination)})
                if ref.get("imported_location"):
                    imported = dict(metadata.get("imported_geometry") or {})
                    imported["archive_stem"] = ref["imported_location"]["archive_stem"] if undo else destination.parent.name
                    metadata["imported_geometry"] = imported
                # This is WG's exports index. Absolute recorded members and relative
                # project/run members both follow; one-off exports elsewhere do not.
                before, after = Path(ref["before"]), Path(ref["after"])
                indexed = []
                for value in metadata.get("exported_files", []):
                    updated = value
                    old_run, new_run = (after, before) if undo else (before, after)
                    for old_path, new_path in ((old_run, new_run), (Path(old_run.parent.name) / old_run.name, Path(new_run.parent.name) / new_run.name)):
                        member = Path(value)
                        if member == old_path or old_path in member.parents:
                            moved_member = new_path / member.relative_to(old_path)
                            # Keep the recorded spelling: relative members are stored
                            # with "/" (archive.py writes as_posix()), and str() of a
                            # Windows path would rewrite them with backslashes.
                            updated = str(moved_member) if "\\" in value else moved_member.as_posix()
                            break
                    indexed.append(updated)
                metadata["exported_files"] = indexed
                conn.execute("UPDATE simulation_jobs SET task_metadata_json=? WHERE id=?", (json.dumps(metadata), ref["job_id"]))
                if metadata != previous_metadata:
                    events.append(self.jobs._append_event(conn, ref["job_id"], "metadata", {"changed": {
                        "archive_key": archive.get("key"), "archive_folder": destination.parent.name,
                        "archive_run": destination.name, "archived_path": str(destination), "exported_files": indexed}}))
        self._events.extend(events)
        self.after_step("jobs_committed")
        old = Path(record["target"] if undo else record["folder"]).name
        new = Path(record["folder"] if undo else record["target"]).name
        with self.cad._transaction() as conn:
            # Aliases move together; mirrors are compatibility readers in older WG.
            # Allocation can repair a temporarily pointerless folder while the
            # move is stopped. Restore its journaled logical assignment as well.
            folders = {old}
            for key in record.get("folder_change", {}).get("keys", [record["key"]]):
                previous = conn.execute("SELECT folder FROM archive_folders WHERE owner_key=?", (key,)).fetchone()
                if previous:
                    folders.add(previous[0])
            for previous in sorted(folders):
                conn.execute("UPDATE archive_folders SET folder=? WHERE folder=? COLLATE NOCASE", (new, previous))
                conn.execute("UPDATE lineage_cad_names SET archive_stem=? WHERE archive_stem=? COLLATE NOCASE", (new, previous))
                conn.execute("UPDATE imported_models SET archive_stem=? WHERE archive_stem=? COLLATE NOCASE", (new, previous))
        self.after_step("registry_committed")

    def _publish_pointers(self, record: dict, *, undo: bool = False) -> None:
        if not undo:
            for ref in record["references"]:
                row = self.jobs.get_job_row(ref["job_id"])
                identity._write_project_record(Path(ref["after"]).parent.parent, row, self.cad, self.cad_source(row),
                                               publish=lambda path, data: self._replace_pointer(record, path, data))
        # A CAD-only rename has no job from which to reconstruct a pointer.
        folder = Path(record["folder"] if undo else record["target"])
        pointer = folder / "design.json"
        if undo and record.get("pointer_before") is not None:
            self._replace_pointer(record, pointer, json.loads(record["pointer_before"]), undo=True)
        elif pointer.is_file():
            data = json.loads(pointer.read_text(encoding="utf-8"))
            data["folder"] = folder.name
            data["name"] = record["name"]
            self._replace_pointer(record, pointer, data, undo=undo)
        self.after_step("pointers_committed")

    def _cleanup(self, record: dict) -> None:
        # A manifest proves bytes, not WG creation. Only owned design pointers
        # are removable remnants; Finder metadata and unknown files stay visible.
        leftovers = record["leftovers"] = []
        for value in record["old_folders"]:
            folder = Path(value)
            _safe(folder)
            if not folder.exists():
                continue
            expected = {Path(item["path"]): base64.b64decode(item["source_bytes"])
                        for item in record["cleanup"] if Path(item["path"]).parent == folder
                        and Path(item["path"]).name == "design.json"}
            self._step(record, "cleanup")
            for child in list(folder.iterdir()):
                try:
                    _safe(child)
                    if child in expected:
                        # Re-read after journal publication, immediately before unlink.
                        if not child.is_file() or child.read_bytes() != expected[child]:
                            raise MoveRefused("Changed cleanup file was kept")
                        child.unlink()
                        _sync_dir(child.parent)
                    elif child.name == "cad" and child.is_dir() and not any(child.iterdir()):
                        child.rmdir()
                        _sync_dir(child.parent)
                    else:
                        leftovers.append({"path": str(child), "reason": "No WG-creation evidence; kept",
                                          "attention": child.name != ".DS_Store"})
                except OSError as exc:
                    leftovers.append({"path": str(child), "reason": str(exc), "attention": True})
            # Also report entries that appeared while journaled members were
            # removed; an unexpected leftover must never be silently skipped.
            reported = {item["path"] for item in leftovers}
            for child in folder.iterdir():
                if str(child) not in reported:
                    leftovers.append({"path": str(child), "reason": "Unexpected cleanup contents; kept",
                                      "attention": child.name != ".DS_Store"})
            if not any(folder.iterdir()):
                folder.rmdir()
                _sync_dir(folder.parent)
        if any(item["attention"] for item in leftovers):
            raise MoveRefused("Move needs attention. Preserved leftovers: " + "; ".join(
                item["path"] + " (" + item["reason"] + ")" for item in leftovers))

    def recover(self) -> list[dict]:
        reports = []
        if not self.directory.exists():
            return reports
        with self._mutex:
            for path in sorted(path for path in self.directory.iterdir() if path.suffix == ".json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["phase"] in {"planned", "done", "undone", "refused"}:
                    with _GATE:
                        _BLOCKED.pop(record["id"], None)
                    continue
                try:
                    self._block(record)
                    if record.get("undoing"):
                        self._undo(record)
                    else:
                        with self.cad._lock:
                            self._busy_publication(record, self._rows())
                        self._resume(record)
                except Exception as exc:
                    self._attention(record, exc)
                reports.append(record)
        if reports:
            self.recovered_reports = reports
        return reports

    def undo(self, move_id: str) -> dict:
        with self._mutex:
            record = self.read(move_id)
            if record["phase"] != "done" or any(op["method"] == "copy_verify_remove" for op in record["operations"]):
                raise MoveRefused("Only a completed move on this drive can be undone.")
            self._block(record)
            try:
                self._verify_undo(record)
                for op in record["operations"]:
                    if op["kind"] in {"run", "rename"} and Path(op["src"]).exists():
                        raise MoveRefused("An original location is occupied. Undo would replace it.")
                with self.cad._lock:
                    # Snapshot IDs under the submission lock; timestamps from old
                    # job databases do not provide a reliable chronology. Repeat
                    # only the job checks here, keeping tree reads outside it.
                    record["undo_jobs"] = [row["id"] for row in self._verify_undo_jobs(record)]
                    record["undoing"] = True
                    self._step(record, "undo_prepared")
                self._undo(record)
            except Exception as exc:
                self._attention(record, exc)
                raise
            return record

    def _verify_undo_jobs(self, record: dict) -> list[dict]:
        with self.cad._lock:
            rows = self._rows()
            self._busy_publication(record, rows, undo=True)
            expected_jobs = {ref["job_id"] for ref in record["references"]} | set(record.get("destination_jobs", []))
            for row in rows:
                if record.get("undoing") and row["id"] not in record.get("undo_jobs", expected_jobs):
                    continue  # Admitted after undo began; reconcile at publication.
                if row["id"] not in expected_jobs and self._location(row, Path(record["root"])).parent in {Path(record["target"]), Path(record["folder"])}:
                    raise MoveRefused("Another run has used this folder since the move.")
            return rows

    def _verify_undo(self, record: dict) -> None:
        """Validate the final tree plus only the journal's landed undo mutations.

        Build one current manifest so startup and interactive undo use the same
        checks, including a rename that landed before its completion was saved.
        """
        self._verify_undo_jobs(record)
        self._verify_cleanup_paths(record)
        files, directories, roots = {}, set(), set()
        for value, manifest in record["final"].items():
            root = Path(value)
            roots.add(root)
            files.update({root if name == "." else root / name: evidence for name, evidence in manifest["files"].items()})
            directories.update(root if name == "." else root / name for name in manifest["directories"])
        # Gather restores may land just before their completion marker.
        for op in record["operations"]:
            if op["method"] == "gather":
                source = Path(op.get("live_src", op["src"]))
                # A reverse parent rename can already have carried this restore.
                original = Path(op["src"])
                if op.get("undone") or source.exists() or original.exists():
                    evidence = op["manifest"]["files"]["."]
                    files[source] = evidence
                    roots.add(source)
                    parent = source.parent
                    while any(root == parent or root in parent.parents for root in roots if root != source):
                        directories.add(parent)
                        parent = parent.parent
        for op in record["operations"]:
            partial = Path(op.get("live_src", op["src"]))
            partial = partial.with_name(partial.name + ".undo-partial-" + record["id"])
            if op.get("undo_created") and partial.exists():
                manifest = op["undo_partial"]["partial_manifest"]
                roots.add(partial)
                files.update({partial if name == "." else partial / name: value for name, value in manifest["files"].items()})
                directories.update(partial if name == "." else partial / name for name in manifest["directories"])
        for op in reversed(record["operations"]):
            if op["method"] == "gather":
                continue
            source, destination = Path(op.get("live_src", op["src"])), Path(op["dst"])
            reversed_on_disk = source.exists() and not destination.exists()
            if op.get("undone") or reversed_on_disk:
                def remap(path: Path) -> Path:
                    return source / path.relative_to(destination) if path == destination or destination in path.parents else path
                files = {remap(path): value for path, value in files.items()}
                directories = {remap(path) for path in directories}
                roots = {remap(path) for path in roots}
            current = source if reversed_on_disk or op.get("undone") else destination
            _safe(current)
            if op.get("source_identity") and op["kind"] in {"run", "rename"}:
                stat = current.stat()
                if [stat.st_dev, stat.st_ino] != op["source_identity"]:
                    raise MoveRefused("A moved folder was replaced. Undo needs attention.")
        for value in record.get("undo_removed", []):
            if not Path(value).exists():
                files.pop(Path(value), None)
                roots.discard(Path(value))
        if all(op.get("undone") for op in record["operations"]):
            # Reference replay may already have restored the journaled pointer.
            pointer = Path(record["folder"]) / "design.json"
            if record.get("pointer_before") is not None and pointer.is_file():
                content = json_bytes(json.loads(record["pointer_before"]))
                if pointer.read_bytes() == content:
                    files[pointer] = self._content_manifest(content)["files"]["."]
        # Verify maximal roots; nested snapshots have been merged above.
        for root in sorted(path for path in roots if not any(parent in roots for parent in path.parents)):
            manifest = {
                "files": {str(path.relative_to(root)): evidence for path, evidence in files.items() if path == root or root in path.parents},
                "directories": sorted(str(path.relative_to(root)) for path in directories if path == root or root in path.parents),
            }
            actual = inventory(root)
            actual["directories"].sort()
            if actual != manifest:
                raise MoveRefused(f"Verification failed. Undo needs attention: {root}")

    def _verify_cleanup_paths(self, record: dict) -> None:
        for removed in record["cleanup"]:
            path = Path(removed["path"])
            _safe(path)
            if path.exists() and (not path.is_file() or path.read_bytes() != base64.b64decode(removed["source_bytes"])):
                raise MoveRefused("An original cleanup file is occupied or changed. Undo needs attention.")

    def _undo(self, record: dict) -> None:
        # Recovery also refuses a recreated/changed pointer before moving files.
        self._verify_undo(record)
        # Restore gathered originals before reversing directory renames.
        for index in reversed(range(len(record["operations"]))):
            op = record["operations"][index]
            if op.get("undone"):
                continue
            self._step(record, f"undo:{index}")
            src, dst = Path(op.get("live_src", op["src"])), Path(op["dst"])
            _safe(src)
            _safe(dst)
            if op["method"] == "gather":
                _mkdir(src.parent)
                if not src.exists():
                    partial = src.with_name(src.name + ".undo-partial-" + record["id"])
                    _safe(partial)
                    content = base64.b64decode(op["source_bytes"]) if op["kind"] == "sidecar" else None
                    _verify(dst, op.get("destination_manifest", op["manifest"]))
                    if partial.exists():
                        if not op.get("undo_created") or "undo_partial" not in op:
                            raise MoveRefused(f"Unjournaled partial contents were kept: {partial}")
                        _remove_members(partial, op["undo_partial"]["partial_manifest"])
                    op["undo_created"] = True
                    owner = op["undo_partial"] = {}
                    self._write_partial(record, owner, partial, dst, op["manifest"], content=content)
                    self.after_step("undo_copied:" + str(src))
                    _verify(partial, op["manifest"])
                    rename_absent(partial, src)
                    _sync_dir(src.parent)
                _verify(src, op["manifest"])
                # Shared copies stay until all duplicates have been restored.
            else:
                if src.exists() and dst.exists():
                    raise MoveRefused("An original location is occupied. Undo would replace it.")
                if not src.exists():
                    self._verify_undo(record)
                    _mkdir(src.parent)
                    rename_absent(dst, src)
                    _sync_dir(src.parent)
                    _sync_dir(dst.parent)
                elif not dst.exists():
                    # A reverse rename landed before its completion was journaled.
                    # Verify every original member, allowing only WG's new cad/
                    # entries which are removed after all restores below.
                    for name, expected in op["manifest"]["files"].items():
                        member = src if name == "." else src / name
                        if member.name == "design.json" and op["kind"] == "rename":
                            continue
                        if not member.is_file() or _hash(member) != expected["sha256"]:
                            raise MoveRefused("The restored original changed. Undo needs attention.")
            op["undone"] = True
            self._step(record, f"undone:{index}")
        def verify_restored() -> None:
            for restored in record["operations"]:
                if restored["method"] == "gather":
                    _verify(Path(restored["src"]), restored["manifest"])

        # A restored file may have changed while WG was stopped. Never discard
        # the shared version based only on a journal's past restoration marker.
        verify_restored()
        for op in record["operations"]:
            if op["method"] == "gather" and op.get("created"):
                dst = Path(op["dst"])
                if Path(record["folder"]) != Path(record["target"]):
                    dst = Path(record["folder"]) / dst.relative_to(Path(record["target"]))
                _safe(dst)
                if dst.exists():
                    verify_restored()
                    _verify(dst, op.get("destination_manifest", op["manifest"]))
                    removed = record.setdefault("undo_removed", [])
                    if str(dst) not in removed:
                        removed.append(str(dst))
                    # Persist intent: on replay an absent file is accounted for;
                    # a still-present file must still match and be removed below.
                    self._step(record, record["phase"])
                    _remove_members(dst, op.get("destination_manifest", op["manifest"]))
        for removed in record["cleanup"]:
            path = Path(removed["path"])
            _safe(path)
            _mkdir(path.parent)
            if not path.exists():
                partial = path.with_name(path.name + ".undo-partial-" + record["id"])
                _safe(partial)
                with partial.open("xb") as stream:
                    stream.write(base64.b64decode(removed["source_bytes"]))
                    stream.flush()
                    os.fsync(stream.fileno())
                rename_absent(partial, path)
                _sync_dir(path.parent)
        self._verify_cleanup_paths(record)
        self._commit_references(record, undo=True)
        record["error"] = None
        self._step(record, "undone")
        with _GATE:
            _BLOCKED.pop(record["id"], None)
