"""The run archive's folder rules and the captured CAD document.

The archive is what outlives the job database: results there are pruned after
thirty days unless a run is rated, while these folders are permanent. Runs group
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
from typing import Any, Mapping
import unicodedata


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
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("returnStateHash") or "") == digest


def _find_captured_document(directory: Path, digest: str) -> Path | None:
    """The archived file for one return state, new-style or legacy.

    New captures are named ``<name>_<stamp>_<fragment>.<ext>``: a fragment
    match is only ever trusted once the file's own sidecar confirms the full
    digest, since two designs' digests can share a twelve-character prefix.
    Legacy captures are named ``sha256_<full digest>.<ext>`` -- the whole
    digest is already in the name, so no sidecar check is needed to trust it.
    """

    if not directory.is_dir():
        return None
    fragment = _digest_fragment(digest)
    if fragment:
        for candidate in sorted(directory.glob(f"*_{fragment}.*")):
            if candidate.suffix == ".json" or candidate.is_symlink() or not candidate.is_file():
                continue
            sidecar = candidate.with_suffix(".json")
            if sidecar.is_file() and _sidecar_matches_digest(sidecar, digest):
                return candidate
    legacy_name = archive_folder_slug(digest, "return")
    for candidate in sorted(directory.glob(f"{legacy_name}.*")):
        if candidate.suffix == ".json" or candidate.is_symlink() or not candidate.is_file():
            continue
        return candidate
    return None


def _prune_other_captured_documents(directory: Path, keep_digest: str, keep_name: str) -> None:
    """Remove every other captured document once a newer model state lands.

    The project-level ``cad/`` folder keeps only the newest model state --
    each run folder still keeps its own permanent copy via
    ``place_run_cad_document``, so an old run stays reopenable in Fusion long
    after the shared project-level copy it started from is gone.

    A file is only ever removed once its own sidecar confirms it belongs to a
    *different* return state, new-style name or legacy ``sha256_<digest>``
    alike; anything without a readable, matching sidecar -- including a file
    this function has no naming convention for at all -- is left untouched.
    A deletion that fails is logged and otherwise ignored: the document that
    was just written is what matters, not the tidying afterwards.
    """

    if not directory.is_dir():
        return
    for candidate in sorted(directory.iterdir()):
        if candidate.name == keep_name or candidate.suffix == ".json":
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        sidecar = candidate.with_suffix(".json")
        if not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        digest = str(payload.get("returnStateHash") or "")
        if not digest or digest == keep_digest:
            continue
        for victim in (candidate, sidecar):
            try:
                victim.unlink()
            except OSError as exc:
                logger.warning(
                    "Could not prune the superseded CAD document %s: %s", victim.name, exc
                )


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
) -> str | None:
    """Copy a return's captured CAD document into the design's archive.

    Only the *newest* model state is kept here, not one file per return: a
    Fusion archive is tens of megabytes, so a project swept many times must
    not grow one project-level copy per solve. Writing a new state prunes
    every other captured document this design folder held, new-style or
    legacy-named alike -- an old run's own copy survives regardless, since
    ``place_run_cad_document`` puts that one beside the run itself, outside
    this pruning. Re-ingesting a return already stored here is a no-op that
    prunes nothing, since nothing new arrived.

    Returns the path relative to the design folder, or ``None`` when there is
    nothing to archive.
    """

    document = record.get("document")
    document = document if isinstance(document, Mapping) else {}
    member = str(document.get("file") or "")
    digest = str(document.get("return_state_hash") or "")
    if not member or not digest:
        return None
    source = (Path(bundle_path) / member).resolve()
    if source.is_symlink() or not source.is_file():
        return None

    destination_directory = design_archive_folder(runs_root, stem) / CAD_SUBDIRECTORY
    existing = _find_captured_document(destination_directory, digest)
    if existing is not None:
        return f"{CAD_SUBDIRECTORY}/{existing.name}"

    filename_stem = _captured_document_stem(document.get("name"), record.get("created_at"), digest)
    destination = destination_directory / f"{filename_stem}{source.suffix}"
    relative = f"{CAD_SUBDIRECTORY}/{destination.name}"

    destination_directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".wg2-cad-document-", dir=destination_directory))
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
    _prune_other_captured_documents(destination_directory, digest, destination.name)
    return relative


def captured_cad_document(runs_root: Path, stem: object, return_state_hash: str) -> Path | None:
    """The archived CAD document for one return state, if it was captured."""

    digest = str(return_state_hash or "").strip()
    if not digest:
        return None
    directory = design_archive_folder(runs_root, stem) / CAD_SUBDIRECTORY
    return _find_captured_document(directory, digest)


def place_run_cad_document(
    runs_root: Path,
    stem: object,
    run_subdirectory: str,
    run_stem: str,
    return_state_hash: str,
) -> str | None:
    """Put the run's CAD document beside the run, for the ``run`` capture mode.

    The project-level ``cad/`` copy stays the content-addressed original: a
    return that is ingested and never solved still has to keep its document
    somewhere. This adds the copy people actually look for -- open the folder
    for run 14 and the model that produced it is in it.

    A plain copy, deliberately. Hard-linking would make this free, but the
    archive commonly lives in a cloud-synced folder, where a shared inode is
    either desynchronised or silently propagates an edit of one file to the
    other; these are archives, and an archive that changes underneath you is
    worse than a second copy of a small file.

    Refuses to overwrite a file that is already there with different content,
    so re-archiving a run cannot clobber a document the user replaced. Returns
    the path relative to the design folder, or ``None`` when there is nothing
    to place.
    """

    source = captured_cad_document(runs_root, stem, return_state_hash)
    if source is None:
        return None
    design_folder = design_archive_folder(runs_root, stem)
    segments = [segment for segment in str(run_subdirectory).split("/") if segment]
    if not segments or any(segment in {".", ".."} for segment in segments):
        return None
    destination_directory = design_folder.joinpath(*segments)
    destination = destination_directory / f"{archive_folder_slug(run_stem, 'run')}{source.suffix}"
    source_sidecar = source.with_suffix(".json")
    destination_sidecar = destination.with_suffix(".cad.json")
    relative = f"{'/'.join(segments)}/{destination.name}"
    resolved_root = design_folder.resolve()
    if resolved_root not in destination_directory.resolve().parents and destination_directory.resolve() != resolved_root:
        return None
    if destination.is_symlink():
        return None
    if destination_sidecar.is_symlink():
        return None
    if destination.is_file():
        # Byte-identical means the archive already holds it: a retried archive
        # after a restart must be a no-op, not a second write.
        if destination.read_bytes() != source.read_bytes():
            return None
    if source_sidecar.is_file() and destination_sidecar.is_file():
        # A CAD sidecar is part of the same immutable archive copy. Refuse a
        # collision rather than replacing metadata that may belong to a file
        # the user put there.
        if destination_sidecar.read_bytes() != source_sidecar.read_bytes():
            return None

    if destination.is_file():
        if source_sidecar.is_file() and not destination_sidecar.is_file():
            shutil.copy2(source_sidecar, destination_sidecar)
        return relative

    destination_directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".wg2-run-document-", dir=destination_directory))
    try:
        staged = staging / destination.name
        shutil.copy2(source, staged)
        os.replace(staged, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if source_sidecar.is_file():
        shutil.copy2(source_sidecar, destination_sidecar)
    return relative


__all__ = [
    "CAD_SUBDIRECTORY",
    "archive_cad_document",
    "archive_folder_slug",
    "captured_cad_document",
    "design_archive_folder",
    "place_run_cad_document",
]
