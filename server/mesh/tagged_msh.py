"""Read tagged ASCII Gmsh 2.2 surface arrays without CAD interpretation."""

from __future__ import annotations

import numpy as np

from .gmsh22 import MshFormatError, read_msh


class TaggedMeshError(ValueError):
    """The mesh artifact cannot be read as tagged surface arrays."""


def parse_tagged_msh(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """Points, triangles, per-triangle physical tags and physical names of an ASCII MSH 2.2.

    Every triangle counts; one written without a tag has tag 0. Names are those
    of the two-dimensional physical groups.
    """

    try:
        artifact = read_msh(msh_text)
    except MshFormatError as exc:
        if exc.kind == "format":
            raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh") from exc
        raise TaggedMeshError("the mesh artifact does not parse") from exc
    if not artifact.has_nodes or not artifact.has_elements:
        raise TaggedMeshError("the mesh artifact does not parse")
    names: dict[int, str] = {}
    for row in artifact.physical_names:
        if len(row) == 3 and row[0] == "2":
            try:
                names[int(row[1])] = row[2].strip('"')
            except ValueError as exc:
                raise TaggedMeshError("the mesh artifact does not parse") from exc
    return (
        np.array(artifact.points, dtype=float),
        np.array(artifact.triangles, dtype=np.int64),
        np.array(artifact.triangle_tags, dtype=np.int64),
        names,
    )
