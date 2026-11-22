"""Ordered Gmsh 2.2 surfaces and official packed mesh buffers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np

from server.mesh.gmsh22 import MshFormatError, read_msh


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


def read_surface(msh_text: str, *, scale_to_m: float = 1.0) -> SurfaceMesh:
    """Retain node rows, triangle rows, winding and physical tags exactly.

    Only points (15), lines (1) and triangles (2) may appear; every triangle
    carries a positive physical tag, and only triangles enter the surface.
    """
    if not np.isfinite(scale_to_m) or scale_to_m <= 0:
        raise ValueError("Mesh scale must be finite and positive")
    try:
        artifact = read_msh(msh_text)
    except MshFormatError as exc:
        if exc.kind == "format":
            raise ValueError("Expected ASCII Gmsh 2.2") from exc
        raise
    if artifact.version != ("2.2", "0", "8"):
        raise ValueError("Expected ASCII Gmsh 2.2")
    if not artifact.has_nodes or not artifact.has_elements:
        raise ValueError("Invalid ASCII Gmsh 2.2 surface")
    if not artifact.node_ids:
        raise ValueError("Invalid Gmsh nodes")
    if min(artifact.node_ids) <= 0:
        raise ValueError("Invalid or duplicate Gmsh node IDs")
    if not len(artifact.element_types):
        raise ValueError("Invalid Gmsh elements")
    # The first offending row decides the message, as reading row by row would.
    types = artifact.element_types
    unsupported = np.flatnonzero((types != 1) & (types != 2) & (types != 15))
    untagged = artifact.triangle_rows[
        (artifact.triangle_tag_counts < 1) | (artifact.triangle_tags <= 0)
    ]
    first_unsupported = int(unsupported[0]) if unsupported.size else None
    first_untagged = int(untagged[0]) if untagged.size else None
    if first_unsupported is not None and (
        first_untagged is None or first_unsupported < first_untagged
    ):
        kind = int(types[first_unsupported])
        raise ValueError(
            f"Unsupported Gmsh element type {kind}; expected points (15), lines (1) or triangles (2)"
        )
    if first_untagged is not None:
        raise ValueError("Triangle requires a positive physical tag")
    points = np.array(artifact.points, dtype=float) * scale_to_m
    element_ids = tuple(artifact.triangle_ids.tolist())
    mesh = SurfaceMesh(points, np.array(artifact.triangles, dtype=np.int64),
                       np.array(artifact.triangle_tags, dtype=np.int64), artifact.node_ids, element_ids)
    if (not element_ids or not np.isfinite(points).all() or len(set(element_ids)) != len(element_ids)
            or min(element_ids) <= 0 or np.any(mesh.areas_m2 <= 0)):
        raise ValueError("Surface requires finite nodes and unique nondegenerate triangles")
    return mesh


def scale_msh_text(msh_text: str, scale_to_m: float) -> str:
    """Scale node coordinates while preserving tags, IDs and element rows."""
    mesh = read_surface(msh_text, scale_to_m=scale_to_m)
    lines = msh_text.splitlines()
    start = read_msh(msh_text).node_lines[0]
    for index, (node_id, point) in enumerate(zip(mesh.node_ids, mesh.points_m, strict=True)):
        lines[start + index] = f"{node_id} " + " ".join(format(float(value), ".17g") for value in point)
    return "\n".join(lines) + "\n"
