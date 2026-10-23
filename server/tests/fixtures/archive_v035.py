"""Frozen v0.3.5 archive functions for downgrade regression evidence.

Copied verbatim from tag v0.3.5, server/workspace/archive.py.
Only imports, constants, and the exercised functions are retained.
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


from typing import Any, Collection, Mapping, NamedTuple


import unicodedata


from server.platform.staging import publish_staging_directory


logger = logging.getLogger(__name__)


CAD_SUBDIRECTORY = "cad"


UNTITLED_SLUG = "untitled"


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


_REPEATED_UNDERSCORE = re.compile(r"_+")


_EDGES = re.compile(r"^[._-]+|[._-]+$")


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
    """Remove captured documents that are neither current nor still needed.

    The project-level ``cad/`` folder is meant to show one current model
    state, and each run folder keeps its own permanent copy via
    ``place_run_cad_document``. But that run copy is made *from this folder*,
    so a capture may only be dropped once every run that still has to be given
    it has taken it: ``retained_return_states`` names those, and a document
    whose return state is in it survives however superseded it is. Without
    that, ingesting a changed model deleted the only source the previous
    model's queued or unarchived runs could ever be archived from.

    A file is only ever removed once its own sidecar identifies it, new-style
    name or legacy ``sha256_<digest>`` alike. A deletion that fails is logged
    and otherwise ignored: the document that was just written is what matters,
    not the tidying afterwards.
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
    """Copy a return's captured CAD document into the design's archive.

    Only the newest model state and the states runs have not released are kept
    here, not one file per return: a Fusion archive is tens of megabytes, so a
    project swept many times must not grow one project-level copy per solve.
    Writing a new state prunes the captured documents this design folder held,
    new-style or legacy-named alike -- except any named in
    ``retained_return_states``, which are the states queued, running or
    unarchived runs still have to be archived from. An already-archived run's
    own copy survives regardless, since ``place_run_cad_document`` puts that
    one beside the run itself, outside this pruning. Re-ingesting a return
    already stored here is a no-op that prunes nothing, since nothing new
    arrived.

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
        if candidate.suffix == ".json" or candidate.is_symlink() or not candidate.is_file():
            continue
        sidecar = candidate.with_suffix(".cad.json")
        if sidecar.is_file() and _sidecar_matches_digest(sidecar, digest):
            return candidate
    return None
