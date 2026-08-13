"""Shared contracts for solving immutable CAD-ingestion mesh artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from server.mesh.artifact import (
    ImportedMeshArtifactError,
    mesh_text_sha256,
    read_verified_import_mesh,
    verify_record_mesh_text,
)


class ImportedSymmetryUnsupportedError(ValueError):
    """The ingestion artifact uses cut planes the native solver cannot mirror."""

    def __init__(self, cut_planes: Iterable[str]) -> None:
        self.cut_planes = tuple(str(plane) for plane in cut_planes)
        super().__init__(
            "unsupported imported symmetry cut-plane set: "
            + repr(list(self.cut_planes))
            + "; Phase 2 supports only no cuts, x0, y0, or x0+y0"
        )


@dataclass(frozen=True, slots=True)
class ImportedSymmetry:
    mode: str
    quadrants: int
    native_plane: str | None
    cut_planes: tuple[str, ...]


def imported_symmetry_from_cut_planes(cut_planes: Iterable[Any]) -> ImportedSymmetry:
    """Map actual CAD cuts to the one native symmetry vocabulary used everywhere."""

    ordered = tuple(str(plane) for plane in cut_planes)
    cuts = frozenset(ordered)
    if len(cuts) != len(ordered) or not cuts.issubset({"x0", "y0"}):
        raise ImportedSymmetryUnsupportedError(ordered)
    if cuts == {"x0", "y0"}:
        return ImportedSymmetry("quarter", 1, "yz+xz", ordered)
    if cuts == {"x0"}:
        return ImportedSymmetry("half_yz", 14, "yz", ordered)
    if cuts == {"y0"}:
        return ImportedSymmetry("half_xz", 12, "xz", ordered)
    if cuts:
        raise ImportedSymmetryUnsupportedError(ordered)
    return ImportedSymmetry("full", 1234, None, ordered)


def mesh_frequency_validation(record: Mapping[str, Any]) -> Mapping[str, Any]:
    mesh = record.get("mesh")
    mesh = mesh if isinstance(mesh, Mapping) else {}
    metadata = mesh.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    validation = metadata.get("mesh_frequency_validation")
    return validation if isinstance(validation, Mapping) else {}


__all__ = [
    "ImportedSymmetry",
    "ImportedSymmetryUnsupportedError",
    "ImportedMeshArtifactError",
    "imported_symmetry_from_cut_planes",
    "mesh_frequency_validation",
    "mesh_text_sha256",
    "read_verified_import_mesh",
    "verify_record_mesh_text",
]
