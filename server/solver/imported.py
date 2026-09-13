"""Shared contracts for solving immutable CAD-ingestion mesh artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np

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


def imported_anchor_frame(record: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """The observation frame an ingestion record's solve is measured in.

    The anchor's throat frame, built during normalisation from the anchor's
    transformed axis datum (``server/mesh/imported.py``), with the throat as
    the observation origin. A record with no anchor falls back to the assembly
    frame only when normalisation said that frame *is* the solver frame.

    Every engine reads the frame here, so two engines handed the same record
    measure on the same arc. Returns ``axis`` (unit), ``origin``, ``u``, ``v``,
    ``mouth_center`` and ``source_center`` as float 3-vectors.
    """

    anchor = record.get("anchor")
    anchor = anchor if isinstance(anchor, Mapping) else {}
    frame = anchor.get("throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        frame = normalisation.get("anchor_throat_frame")
    if not isinstance(frame, Mapping):
        normalisation = record.get("normalisation")
        normalisation = normalisation if isinstance(normalisation, Mapping) else {}
        if bool(normalisation.get("assembly_frame_is_solver_frame")):
            frame = {
                "axis": [0.0, 0.0, 1.0],
                "origin_m": [0.0, 0.0, 0.0],
                "u": [1.0, 0.0, 0.0],
                "v": [0.0, 1.0, 0.0],
                "mouth_center_m": [0.0, 0.0, 0.0],
                "source_center_m": [0.0, 0.0, 0.0],
            }
        else:
            raise ValueError(
                "ingestion record has no anchor throat frame; re-ingest the CAD return"
            )

    def vector(name: str, *fallback_names: str) -> np.ndarray:
        value = frame.get(name)
        if value is None:
            for fallback in fallback_names:
                value = frame.get(fallback)
                if value is not None:
                    break
        result = np.asarray(value, dtype=float)
        if result.shape != (3,) or not np.isfinite(result).all():
            raise ValueError(f"ingestion anchor throat frame {name!r} must be a finite 3-vector")
        return result

    axis = vector("axis", "normal")
    axis /= np.linalg.norm(axis)
    source_center = vector("source_center_m", "origin_m", "origin")
    mouth_center = vector("mouth_center_m", "origin_m", "origin")
    return {
        "axis": axis,
        "origin": source_center,
        "u": vector("u", "horizontal"),
        "v": vector("v", "vertical"),
        "mouth_center": mouth_center,
        "source_center": source_center,
    }


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
    "imported_anchor_frame",
    "imported_domain_planes",
    "imported_symmetry_from_cut_planes",
    "mesh_frequency_validation",
    "mesh_text_sha256",
    "read_verified_import_mesh",
    "verify_record_mesh_text",
]
