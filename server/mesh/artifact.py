"""Integrity helpers for immutable imported Gmsh text artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


class ImportedMeshArtifactError(ValueError):
    """The immutable ingestion mesh is absent or no longer matches its record."""


def mesh_text_sha256(msh_text: str) -> str:
    """Digest the exact UTF-8 bytes persisted in the imports and jobs stores."""

    return "sha256:" + hashlib.sha256(msh_text.encode("utf-8")).hexdigest()


def verify_record_mesh_text(record: Mapping[str, Any], msh_text: str) -> str:
    """Verify exact mesh contents against the digest minted during ingestion."""

    expected = record.get("mesh_content_sha256")
    if not isinstance(expected, str) or not expected.startswith("sha256:"):
        raise ImportedMeshArtifactError(
            "ingestion record has no mesh content digest; re-ingest the CAD return"
        )
    actual = mesh_text_sha256(msh_text)
    if actual != expected:
        raise ImportedMeshArtifactError(
            f"ingestion mesh content digest mismatch: expected {expected}, got {actual}"
        )
    return msh_text


def read_verified_import_mesh(record: Mapping[str, Any]) -> str:
    """Read and verify the imports-store copy for submit or execution fallback."""

    mesh_path = Path(str(record.get("mesh_store_path") or ""))
    try:
        msh_text = mesh_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImportedMeshArtifactError(
            f"cached ingestion mesh is unavailable at {mesh_path}"
        ) from exc
    return verify_record_mesh_text(record, msh_text)


def read_verified_import_viewport_mesh(record: Mapping[str, Any]) -> str:
    """Read and verify the optional full-domain CAD display artifact."""

    viewport = record.get("viewport_mesh")
    if not isinstance(viewport, Mapping) or viewport.get("available") is not True:
        raise ImportedMeshArtifactError(
            "ingestion record has no CAD viewport artifact; re-ingest to create one"
        )
    expected = viewport.get("content_sha256")
    if not isinstance(expected, str) or not expected.startswith("sha256:"):
        raise ImportedMeshArtifactError(
            "ingestion record has no CAD viewport content digest; re-ingest the CAD return"
        )
    mesh_path = Path(str(viewport.get("store_path") or ""))
    try:
        msh_text = mesh_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImportedMeshArtifactError(
            f"cached CAD viewport mesh is unavailable at {mesh_path}"
        ) from exc
    actual = mesh_text_sha256(msh_text)
    if actual != expected:
        raise ImportedMeshArtifactError(
            "CAD viewport mesh content digest mismatch: "
            f"expected {expected}, got {actual}"
        )
    return msh_text


__all__ = [
    "ImportedMeshArtifactError",
    "mesh_text_sha256",
    "read_verified_import_mesh",
    "read_verified_import_viewport_mesh",
    "verify_record_mesh_text",
]
