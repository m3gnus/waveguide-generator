"""Read tagged ASCII Gmsh 2.2 surface arrays without CAD interpretation."""

from __future__ import annotations

import numpy as np


class TaggedMeshError(ValueError):
    """The mesh artifact cannot be read as tagged surface arrays."""


def parse_tagged_msh(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """Points, triangles, per-triangle physical tags and physical names of an ASCII MSH 2.2."""

    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        version = lines[lines.index("$MeshFormat") + 1].split()
    except (ValueError, IndexError) as exc:
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh") from exc
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise TaggedMeshError("the mesh artifact is not an ASCII Gmsh 2.2 mesh")
    names: dict[int, str] = {}
    if "$PhysicalNames" in lines:
        start = lines.index("$PhysicalNames")
        for row in lines[start + 2 : start + 2 + int(lines[start + 1])]:
            parts = row.split(maxsplit=2)
            if len(parts) == 3 and parts[0] == "2":
                names[int(parts[1])] = parts[2].strip('"')
    try:
        nodes_start = lines.index("$Nodes")
        count = int(lines[nodes_start + 1])
        rows = [line.split() for line in lines[nodes_start + 2 : nodes_start + 2 + count]]
        index = {row[0]: position for position, row in enumerate(rows)}
        points = np.asarray([row[1:4] for row in rows], dtype=float).reshape(-1, 3)
        elements_start = lines.index("$Elements")
        element_count = int(lines[elements_start + 1])
        corners: list[list[int]] = []
        tags: list[int] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]) if tag_count >= 1 else 0)
    except (ValueError, IndexError, KeyError) as exc:
        raise TaggedMeshError("the mesh artifact does not parse") from exc
    return (
        points,
        np.asarray(corners, dtype=np.int64).reshape(-1, 3),
        np.asarray(tags, dtype=np.int64),
        names,
    )
