"""Ordered Gmsh 2.2 surfaces and official packed mesh buffers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np

from server.mesh.gmsh22 import MeshArtifact, read_msh


@dataclass(frozen=True)
class SurfaceMesh:
    points_m: np.ndarray
    faces: np.ndarray
    tags: np.ndarray
    node_ids: tuple[int, ...]
    element_ids: tuple[int, ...]

    @property
    def areas_m2(self) -> np.ndarray:
        triangles = np.asarray(self.points_m[self.faces], dtype=np.float64)
        return np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                       triangles[:, 2] - triangles[:, 0]), axis=1) / 2

    def packed(self) -> dict[str, Any]:
        def array(values: np.ndarray, dtype: str) -> dict[str, Any]:
            values = np.asarray(values, dtype=dtype)
            return {"dtype": dtype, "shape": list(values.shape),
                    "data": base64.b64encode(values.tobytes(order="C")).decode("ascii")}

        return {"schema_version": 1, "points": array(self.points_m, "<f8"),
                "cells": [{"type": "triangle", "connectivity": array(self.faces, "<i8"),
                           "physical_tags": array(self.tags, "<i8")}],
                "physical_names": {f"tag:{tag}": [int(tag), 2] for tag in np.unique(self.tags)}}


_SURFACE = "Invalid ASCII Gmsh 2.2 surface"
_TAG = "Triangle requires a positive physical tag"


def _require_counted(artifact: MeshArtifact, section: Any, marker: str, invalid: str) -> None:
    """The counted section is present and closes where its count says."""
    if section is None:
        raise ValueError(f"'{marker}' is not in list")
    if section.problem is not None and section.problem[0] == "index":
        raise ValueError(_SURFACE)
    if section.problem is not None and section.problem[0] == "value":
        raise ValueError(section.problem[1])
    if section.count <= 0:
        raise ValueError(invalid)
    if section.header_line + section.count + 2 >= artifact.line_count:
        raise ValueError(_SURFACE)
    if not section.end_ok:
        raise ValueError(invalid)


def _first_bad_element(artifact: MeshArtifact) -> str | None:
    """The message for the first element row that cannot enter the surface."""
    elements = artifact.elements
    found: dict[int, str] = {}
    for row, message in elements.integer_errors:
        found.setdefault(row, message)
    types, lengths = elements.element_types, elements.row_lengths
    for row in np.flatnonzero(lengths < 3).tolist():
        found.setdefault(row, "Invalid Gmsh element row")
    for row in np.flatnonzero((types != 1) & (types != 2) & (types != 15)).tolist():
        found.setdefault(row, f"Unsupported Gmsh element type {int(types[row])}; "
                              "expected points (15), lines (1) or triangles (2)")
    for row in np.flatnonzero((types == 2) & (lengths < 4)).tolist():
        found.setdefault(row, _TAG)
    for row, _tags, _kind in elements.triangle_problems:
        found.setdefault(row, _TAG)
    bad_tag = ((elements.triangle_tag_counts < 1) | (elements.triangle_tags <= 0)
               | elements.triangle_extra)
    for row in elements.triangle_rows[bad_tag].tolist():
        found.setdefault(row, _TAG)
    undeclared = np.any(artifact.corner_indices < 0, axis=1)
    for row in elements.triangle_rows[undeclared].tolist():
        found.setdefault(row, _SURFACE)
    return found[min(found)] if found else None


def read_surface(msh_text: str, *, scale_to_m: float = 1.0) -> SurfaceMesh:
    """Retain node rows, triangle rows, winding and physical tags exactly.

    Only points (15), lines (1) and triangles (2) may appear; every triangle
    carries a positive physical tag, and only triangles enter the surface.
    The checks run in the order a row-by-row read meets them.
    """
    if not np.isfinite(scale_to_m) or scale_to_m <= 0:
        raise ValueError("Mesh scale must be finite and positive")
    artifact = read_msh(msh_text)
    if not artifact.has_format_line:
        raise ValueError("'$MeshFormat' is not in list")
    if artifact.version is None:
        raise ValueError(_SURFACE)
    if list(artifact.version) != ["2.2", "0", "8"]:
        raise ValueError("Expected ASCII Gmsh 2.2")
    nodes = artifact.nodes
    _require_counted(artifact, nodes, "$Nodes", "Invalid Gmsh nodes")
    if (nodes.problem is not None and nodes.problem[0] == "row") or not nodes.rows_exact:
        raise ValueError("Invalid Gmsh node row")
    if nodes.problem is not None and nodes.problem[0] == "id":
        raise ValueError(nodes.problem[1])
    if min(nodes.node_ids) <= 0 or not nodes.unique:
        raise ValueError("Invalid or duplicate Gmsh node IDs")
    if nodes.problem is not None:
        raise ValueError(nodes.problem[1])
    points = np.array(nodes.points, dtype=float) * scale_to_m
    _require_counted(artifact, artifact.elements, "$Elements", "Invalid Gmsh elements")
    message = _first_bad_element(artifact)
    if message is not None:
        raise ValueError(message)
    elements = artifact.elements
    element_ids = tuple(elements.triangle_ids.tolist())
    mesh = SurfaceMesh(points, np.array(artifact.corner_indices, dtype=np.int64),
                       np.array(elements.triangle_tags, dtype=np.int64), nodes.node_ids, element_ids)
    if (not element_ids or not np.isfinite(points).all() or len(set(element_ids)) != len(element_ids)
            or min(element_ids) <= 0 or np.any(mesh.areas_m2 <= 0)):
        raise ValueError("Surface requires finite nodes and unique nondegenerate triangles")
    return mesh


def scale_msh_text(msh_text: str, scale_to_m: float) -> str:
    """Scale node coordinates while preserving tags, IDs and element rows."""
    mesh = read_surface(msh_text, scale_to_m=scale_to_m)
    lines = msh_text.splitlines()
    start = read_msh(msh_text).nodes.first_line
    for index, (node_id, point) in enumerate(zip(mesh.node_ids, mesh.points_m, strict=True)):
        lines[start + index] = f"{node_id} " + " ".join(format(float(value), ".17g") for value in point)
    return "\n".join(lines) + "\n"
