"""The run archive's folder rules and the captured CAD document.

The archive is what outlives the job database: results there can be removed --
by the user, or after thirty days unless a run is rated when automatic cleanup
is on -- while these folders are permanent. Runs group
by design rather than by pipeline, so one design's parametric and CAD history
read as one story.

The folder slug here mirrors ``designNameSlug`` in
``frontend/src/stores/designName.ts`` for ordinary parametric archives. For CAD
lineages this server function is the authority: ``CadLinkStore`` persists its
ASCII result under a case-insensitive uniqueness constraint, and
``frontend/src/jobs/exportNaming.ts`` consumes that allocated key verbatim.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Callable, Collection, Mapping, NamedTuple
import unicodedata

from server.platform.acl_repair import path_has_reparse_point
from server.platform.staging import publish_staging_directory


logger = logging.getLogger(__name__)

CAD_SUBDIRECTORY = "cad"
UNTITLED_SLUG = "untitled"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_UNDERSCORE = re.compile(r"_+")
_EDGES = re.compile(r"^[._-]+|[._-]+$")
# How many hex characters of the return-state digest name a captured document.
# Short enough to read, long enough that two returns of one design never
# collide by fragment alone -- and a sidecar check backs it up regardless.
_DIGEST_FRAGMENT_LENGTH = 12
_FALLBACK_STAMP = "000000-0000"
# Capture publication, pinning and pruning must not race in worker threads.
_CAD_DOCUMENT_LOCK = threading.RLock()


def archive_folder_slug(name: object, fallback: str = UNTITLED_SLUG) -> str:
    """The portable folder name a design's runs are archived under."""

    decomposed = unicodedata.normalize("NFKD", str(name or "").strip())
    stripped = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) not in {"Mn", "Mc", "Me"}
    )
    slug = _EDGES.sub("", _REPEATED_UNDERSCORE.sub("_", _UNSAFE.sub("_", stripped)))
    return slug or fallback


def design_archive_folder(runs_root: Path, stem: object) -> Path:
    return runs_root / archive_folder_slug(stem, "design")


def _digest_hex(digest: str) -> str:
    """The hex half of a ``sha256:<hex>``-style digest, lowercased.

    Falls back to the whole string when there is no ``algo:`` prefix, so a
    bare hex digest still works.
    """

    _, _, hex_part = digest.partition(":")
    return (hex_part or digest).strip().lower()


def _digest_fragment(digest: str) -> str:
    return _digest_hex(digest)[:_DIGEST_FRAGMENT_LENGTH]


def _capture_stamp(captured_at: object) -> str:
    """The UTC ``YYMMDD-HHMM`` stamp a captured document's filename carries.

    UTC keeps the stamp deterministic regardless of the server's local zone;
    the sidecar's own ISO timestamp is still the source of truth for anything
    that needs more precision. An unparseable or missing timestamp falls back
    to a fixed placeholder rather than raising -- the digest fragment already
    keeps the filename unique, so a readable date is a nicety, not a key.
    """

    text = str(captured_at or "").strip()
    if not text:
        return _FALLBACK_STAMP
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _FALLBACK_STAMP
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%y%m%d-%H%M")


def _captured_document_stem(document_name: object, captured_at: object, digest: str) -> str:
    """The friendly, portable stem a newly captured document is filed under.

    ``<safe document name>_<capture stamp>_<digest fragment>`` -- the name
    people recognise in Finder, a UTC timestamp so two captures of the same
    document never look identical, and the digest fragment so the file still
    carries its return-state identity even after the sidecar is separated
    from it.
    """

    safe_name = archive_folder_slug(document_name, "document")
    stamp = _capture_stamp(captured_at)
    fragment = _digest_fragment(digest) or "return"
    return f"{safe_name}_{stamp}_{fragment}"


def _sidecar_matches_digest(sidecar: Path, digest: str) -> bool:
    if sidecar.is_symlink():
        return False
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("returnStateHash") or "") == digest


def cad_document_sidecar(document: Path, digest: str) -> Path | None:
    """The identifying capture record, for both current and legacy files."""

    for sidecar in (document.with_suffix(".cad.json"), document.with_suffix(".json")):
        if sidecar.is_file() and _sidecar_matches_digest(sidecar, digest):
            return sidecar
    return None


def _find_captured_document(
    directory: Path, digest: str,
    verify_document: Callable[[Path], bool] | None = None,
) -> Path | None:
    """Find the full return hash behind a friendly or legacy capture name."""

    if directory.is_symlink() or not directory.is_dir():
        return None
    fragment = _digest_fragment(digest)
    legacy_name = archive_folder_slug(digest, "return")
    candidates = sorted(set(directory.glob(f"*_{fragment}.*")) | set(directory.glob(f"{legacy_name}.*")))
    for candidate in candidates:
        if candidate.suffix == ".json" or candidate.is_symlink() or not candidate.is_file():
            continue
        if cad_document_sidecar(candidate, digest) is None:
            continue
        if verify_document is None or verify_document(candidate):
            return candidate
    return None


class _CapturedDocument(NamedTuple):
    """One identified capture in a project's ``cad/`` folder."""

    document: Path
    sidecar: Path
    return_state_hash: str
    captured_at: str


def _captured_documents(directory: Path) -> list[_CapturedDocument]:
    """Every captured document in one folder that its sidecar can identify.

    A file with no readable, well-formed sidecar is deliberately missing from
    this list: nothing here can name its return state, so no retention
    decision below may touch it. That is what keeps a file this module has no
    naming convention for -- including one a person put in the folder
    themselves -- out of every deletion.
    """

    if not directory.is_dir():
        return []
    entries: list[_CapturedDocument] = []
    for candidate in sorted(directory.iterdir()):
        if candidate.suffix == ".json" or candidate.is_symlink() or not candidate.is_file():
            continue
        sidecar = candidate.with_suffix(".json")
        if sidecar.is_symlink() or not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        digest = str(payload.get("returnStateHash") or "")
        if not digest:
            continue
        entries.append(
            _CapturedDocument(
                candidate, sidecar, digest, str(payload.get("capturedAt") or "")
            )
        )
    return entries


def _prune_captured_documents(
    directory: Path,
    keep_names: Collection[str],
    retained_return_states: Collection[str],
) -> None:
    """Prune only superseded, unpinned states no pending run still needs.

    Pinned records use .cad.json and are deliberately absent from
    _captured_documents, including when older WG runs that enumerator.
    """

    retained = {_digest_hex(value) for value in retained_return_states if value}
    for entry in _captured_documents(directory):
        if entry.document.name in keep_names:
            continue
        if _digest_hex(entry.return_state_hash) in retained:
            continue
        for victim in (entry.document, entry.sidecar):
            try:
                victim.unlink()
            except OSError as exc:
                logger.warning(
                    "Could not prune the superseded CAD document %s: %s", victim.name, exc
                )


def reclaim_captured_documents(
    runs_root: Path, stem: object, retained_return_states: Collection[str] = ()
) -> None:
    """Drop released unpinned captures, preserving the newest and all pins."""

    with _CAD_DOCUMENT_LOCK:
        directory = design_archive_folder(runs_root, stem) / CAD_SUBDIRECTORY
        entries = _captured_documents(directory)
        if len(entries) < 2 or any(not entry.captured_at for entry in entries):
            return
        newest = max(entries, key=lambda entry: (entry.captured_at, entry.document.name))
        _prune_captured_documents(directory, {newest.document.name}, retained_return_states)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_cad_document(
    bundle_path: Path,
    record: Mapping[str, Any],
    runs_root: Path,
    stem: object,
    retained_return_states: Collection[str] = (),
) -> str | None:
    """Capture once per return state; prune only unused superseded versions.

    A run pins the original in cad/ permanently. Re-ingesting an existing
    version (including a pinned version) changes neither bytes nor sidecar.
    """

    with _CAD_DOCUMENT_LOCK:
        document = record.get("document")
        document = document if isinstance(document, Mapping) else {}
        member = str(document.get("file") or "")
        digest = str(document.get("return_state_hash") or "")
        if not member or not digest:
            return None
        bundle_root = Path(bundle_path).resolve()
        member_path = bundle_root / member
        source = member_path.resolve()
        if bundle_root not in source.parents or member_path.is_symlink() or not source.is_file():
            return None

        destination_directory = design_archive_folder(runs_root, stem) / CAD_SUBDIRECTORY
        existing = _find_captured_document(destination_directory, digest)
        if existing is not None:
            return f"{CAD_SUBDIRECTORY}/{existing.name}"

        filename_stem = _captured_document_stem(document.get("name"), record.get("created_at"), digest)
        destination = destination_directory / f"{filename_stem}{source.suffix}"
        relative = f"{CAD_SUBDIRECTORY}/{destination.name}"

        destination_directory.mkdir(parents=True, exist_ok=True)
        staging = publish_staging_directory(destination_directory, ".wg2-cad-document-")
        try:
            staged = staging / destination.name
            shutil.copy2(source, staged)
            os.replace(staged, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        _write_json_atomic(
            destination.with_suffix(".json"),
            {
                "schemaVersion": 1,
                "documentName": document.get("name") or None,
                "nativeId": document.get("native_id") or None,
                "returnStateHash": digest,
                "ingestId": record.get("ingest_id"),
                "returnId": record.get("return_id"),
                "capturedAt": record.get("created_at"),
            },
        )
        _prune_captured_documents(
            destination_directory, {destination.name}, retained_return_states
        )
        return relative


def _unlinked_archive_path(path: Path, root: Path) -> bool:
    """Check lexical components before resolution can erase junctions or links."""

    try:
        return not path_has_reparse_point(path, root=root)
    except OSError:
        return False


def cad_document_sidecars(folder: Path) -> list[Path]:
    """WG's project and run capture records, confined to this project.

    Run records use ``.cad.json``; ordinary result JSON is never a capture.
    Do not follow linked folders or sidecars, even within the archive.
    """

    if not _unlinked_archive_path(folder, folder.parent) or not folder.is_dir():
        return []
    candidates = [*(folder / CAD_SUBDIRECTORY).glob("*.json"), *folder.rglob("*.cad.json")]
    return sorted({
        sidecar for sidecar in candidates
        if _unlinked_archive_path(sidecar, folder.parent) and sidecar.is_file()
    })


def captured_cad_document(
    runs_root: Path, stem: object, return_state_hash: str,
    *, sidecars: Collection[Path] | None = None,
    archived_paths: Collection[Path] = (),
    verify_run_document: Callable[[Path], bool] | None = None,
) -> Path | None:
    """The exact archived model, preferring cad/ then an identified run copy.

    HTTP callers supply the same trusted ingestion verifier for every candidate.
    """

    digest = str(return_state_hash or "").strip()
    if not digest:
        return None
    folder = design_archive_folder(runs_root, stem)
    if folder.exists() and not _unlinked_archive_path(folder, runs_root):
        return None
    directory = folder / CAD_SUBDIRECTORY
    document = _find_captured_document(directory, digest, verify_run_document) if _unlinked_archive_path(directory, runs_root) else None
    if document is not None and _unlinked_archive_path(document, runs_root):
        return document
    for sidecar in cad_document_sidecars(folder) if sidecars is None else sidecars:
        if sidecar.parent == directory or not sidecar.name.endswith(".cad.json") or not _unlinked_archive_path(sidecar, runs_root):
            continue
        document = _placed_run_document(sidecar.parent, sidecar.name[:-len(".cad.json")], digest)
        if (document is not None and _unlinked_archive_path(document, runs_root)
                and (verify_run_document is None or verify_run_document(document))):
            return document
    # A previous Runs root or a legacy cad-import folder can hold the only
    # remaining per-run copy. These paths come from the server's job database.
    for run in archived_paths:
        if not _unlinked_archive_path(run, Path(run.anchor)):
            continue
        directory = run.parent / CAD_SUBDIRECTORY
        document = _find_captured_document(directory, digest, verify_run_document) if _unlinked_archive_path(directory, Path(run.anchor)) else None
        if document is not None and verify_run_document is not None:
            return document
        for sidecar in cad_document_sidecars(run):
            if not sidecar.name.endswith('.cad.json'):
                continue
            document = _placed_run_document(sidecar.parent, sidecar.name[:-len('.cad.json')], digest)
            if (document is not None and _unlinked_archive_path(document, Path(run.anchor))
                    and verify_run_document is not None and verify_run_document(document)):
                return document
    return None


def _placed_run_document(
    destination_directory: Path, run_stem: str, return_state_hash: str
) -> Path | None:
    """The copy this run already holds of one model state, if it holds one.

    Identified by its own ``.cad.json`` sidecar rather than by name, because
    the name alone says which run a file belongs to and not which model state
    it is.
    """

    digest = str(return_state_hash or "").strip()
    if not digest or not destination_directory.is_dir():
        return None
    slug = archive_folder_slug(run_stem, "run")
    for candidate in sorted(destination_directory.glob(f"{slug}.*")):
        if candidate.suffix.lower() != ".f3d" or candidate.is_symlink() or not candidate.is_file():
            continue
        sidecar = candidate.with_suffix(".cad.json")
        if sidecar.is_file() and _sidecar_matches_digest(sidecar, digest):
            return candidate
    return None


def pin_cad_document(
    runs_root: Path, stem: object, return_state_hash: str,
    *, verify_document: Callable[[Path], bool],
) -> str | None:
    """Pin the verified original; several runs share it without copying bytes.

    Legacy per-run copies remain readable in place. Renaming a cad/ sidecar
    protects its model even from the older WG prune enumerator.
    """

    with _CAD_DOCUMENT_LOCK:
        folder = design_archive_folder(runs_root, stem)
        source = captured_cad_document(
            runs_root, stem, return_state_hash, verify_run_document=verify_document,
        )
        if source is None:
            return None
        if source.parent == folder / CAD_SUBDIRECTORY:
            ordinary = source.with_suffix(".json")
            pinned = source.with_suffix(".cad.json")
            if pinned.exists():
                if not _unlinked_archive_path(pinned, runs_root) or not _sidecar_matches_digest(pinned, return_state_hash):
                    return None
                if ordinary.exists():
                    if ordinary.read_bytes() != pinned.read_bytes():
                        return None
                    ordinary.unlink()
            else:
                os.rename(ordinary, pinned)
        return source.relative_to(folder).as_posix()


__all__ = [
    "CAD_SUBDIRECTORY",
    "archive_cad_document",
    "archive_folder_slug",
    "captured_cad_document",
    "cad_document_sidecars",
    "design_archive_folder",
    "pin_cad_document",
    "cad_document_sidecar",
    "reclaim_captured_documents",
]
