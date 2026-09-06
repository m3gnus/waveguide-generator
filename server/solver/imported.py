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


def imported_domain_planes(record: Mapping[str, Any]) -> tuple[str, ...]:
    """The planes an ingestion record's solve must mirror on.

    This is the one place the distinction is resolved, because two readers that
    disagree about it solve two different models. ``symmetry.domain_planes`` is
    the domain: the planes WG cut here *plus* the planes the CAD author had
    already cut before exporting. ``symmetry.cut_planes`` is only the first set,
    and a return that arrived already reduced has none of them -- reading it
    would resolve a declared half to ``full`` and solve an open shell.

    Records written before ``domain_planes`` existed carry only ``cut_planes``,
    where the two lists are the same.
    """

    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    planes = symmetry.get("domain_planes")
    if planes is None:
        planes = symmetry.get("cut_planes") or []
    return tuple(str(plane) for plane in planes)


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
    "imported_domain_planes",
    "imported_symmetry_from_cut_planes",
    "mesh_frequency_validation",
    "mesh_text_sha256",
    "read_verified_import_mesh",
    "verify_record_mesh_text",
]
