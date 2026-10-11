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
    expected_size = record.get("mesh_size_bytes")
    if expected_size is not None and len(msh_text.encode("utf-8")) != expected_size:
        raise ImportedMeshArtifactError("ingestion mesh size mismatch")
    return msh_text


def read_verified_import_mesh(record: Mapping[str, Any]) -> str:
    """Read and verify the imports-store copy for submit or execution fallback."""

    mesh_path = Path(str(record.get("mesh_store_path") or ""))
    try:
        # Region records carry an exact byte size. Preserve line endings for
        # their digest rather than normalising CRLF on Windows or after tampering.
        msh_text = (
            mesh_path.read_bytes().decode("utf-8")
            if "mesh_size_bytes" in record else mesh_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as exc:
        raise ImportedMeshArtifactError(
            f"cached ingestion mesh is unavailable at {mesh_path}"
        ) from exc
    return verify_record_mesh_text(record, msh_text)


def read_verified_fem_region_mesh(record: Mapping[str, Any], region_id: str) -> str:
    """Resolve a retained region by id and apply the exterior artifact checks."""

    regions = [region for region in record.get("fem_regions", ())
               if region.get("region_id") == region_id]
    if len(regions) != 1:
        raise ImportedMeshArtifactError(f"ingestion has no unique FEM region {region_id!r}")
    if not isinstance(regions[0].get("mesh_size_bytes"), int):
        raise ImportedMeshArtifactError("FEM region mesh has no size; re-ingest the CAD return")
    return read_verified_import_mesh(regions[0])


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
    "read_verified_fem_region_mesh",
    "read_verified_import_viewport_mesh",
    "verify_record_mesh_text",
]
