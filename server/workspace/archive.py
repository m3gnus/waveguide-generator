"""The run archive's folder rules and the captured CAD document.

The archive is what outlives the job database: results there are pruned after
thirty days unless a run is rated, while these folders are permanent. Runs group
by design rather than by pipeline, so one design's parametric and CAD history
read as one story.

The folder slug here mirrors ``designNameSlug`` in
``frontend/src/stores/designName.ts``. Two implementations of one name rule can
disagree, so ``test_run_archive.py`` and ``runArchive.test.ts`` assert the same
table of names; change both or neither.
"""

from __future__ import annotations

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

    One file per *return*, named by the return-state hash, rather than one per
    run: a Fusion archive is tens of megabytes and is identical across every
    solve of one geometry, so ten sweeps of one waveguide must not cost ten
    copies of the same document. Re-ingesting a return it already holds is a
    no-op.

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
    relative = f"{CAD_SUBDIRECTORY}/{archive_folder_slug(digest, 'return')}{source.suffix}"
    destination = destination_directory / Path(relative).name
    if destination.is_file():
        return relative

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
    return relative


__all__ = [
    "CAD_SUBDIRECTORY",
    "archive_cad_document",
    "archive_folder_slug",
    "design_archive_folder",
]
