"""Read tagged ASCII Gmsh 2.2 surface arrays without CAD interpretation."""

from __future__ import annotations

import numpy as np

from .gmsh22 import read_msh


class TaggedMeshError(ValueError):
    """The mesh artifact cannot be read as tagged surface arrays."""


def parse_tagged_msh(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """Points, triangles, per-triangle physical tags and physical names of an ASCII MSH 2.2.

    Every triangle counts; one written without a tag has tag 0. Names are those
    of the two-dimensional physical groups.
    """

    artifact = read_msh(msh_text)
    version = artifact.version
    if version is None:
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh")
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh")
    if artifact.physical_names_error is not None:
        kind, message = artifact.physical_names_error
        raise kind(message)
    names: dict[int, str] = {}
    for row in artifact.physical_names:
        if len(row) == 3 and row[0] == "2":
            names[int(row[1])] = row[2].strip('"')
    nodes, elements = artifact.nodes, artifact.elements
    if (
        nodes is None
        or elements is None
        or nodes.problem is not None
        or elements.problem is not None
        or any(kind == "integer" for _row, _count, kind in elements.triangle_problems)
        or np.any(artifact.corner_indices < 0)
    ):
        raise TaggedMeshError("the mesh artifact does not parse")
    if any(kind == "short" for _row, _count, kind in elements.triangle_problems):
        # A short row made the corner list ragged, which numpy refused.
        raise ValueError("the mesh artifact holds a triangle row with fewer than three nodes")
    return (
        np.array(nodes.points, dtype=float).reshape(-1, 3),
        np.array(artifact.corner_indices, dtype=np.int64).reshape(-1, 3),
        np.array(elements.triangle_tags, dtype=np.int64),
        names,
    )
