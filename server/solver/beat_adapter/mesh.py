"""Ordered Gmsh 2.2 surfaces and official packed mesh buffers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SurfaceMesh:
    points_m: np.ndarray
    faces: np.ndarray
    tags: np.ndarray
    node_ids: tuple[int, ...]
    element_ids: tuple[int, ...]

    @property
    def areas_m2(self) -> np.ndarray:
        triangles = self.points_m[self.faces]
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
    """Retain node rows, triangle rows, winding and physical tags exactly."""
    if not np.isfinite(scale_to_m) or scale_to_m <= 0:
        raise ValueError("Mesh scale must be finite and positive")
    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        header = lines.index("$MeshFormat")
        if lines[header + 1].split() != ["2.2", "0", "8"]:
            raise ValueError("Expected ASCII Gmsh 2.2")
        start = lines.index("$Nodes") + 1
        count = int(lines[start])
        if count <= 0 or lines[start + count + 1] != "$EndNodes":
            raise ValueError("Invalid Gmsh nodes")
        nodes = [row.split() for row in lines[start + 1:start + count + 1]]
        if any(len(row) != 4 for row in nodes):
            raise ValueError("Invalid Gmsh node row")
        node_ids = tuple(int(row[0]) for row in nodes)
        if min(node_ids) <= 0 or len(set(node_ids)) != count:
            raise ValueError("Invalid or duplicate Gmsh node IDs")
        points = np.asarray([row[1:] for row in nodes], dtype=float) * scale_to_m
        lookup = {node_id: i for i, node_id in enumerate(node_ids)}
        start = lines.index("$Elements") + 1
        count = int(lines[start])
        if count <= 0 or lines[start + count + 1] != "$EndElements":
            raise ValueError("Invalid Gmsh elements")
        faces, tags, element_ids = [], [], []
        for line in lines[start + 1:start + count + 1]:
            row = [int(value) for value in line.split()]
            if len(row) < 3:
                raise ValueError("Invalid Gmsh element row")
            if row[1] != 2:
                if row[1] in {9, 4, 11}:
                    raise ValueError("Only linear surface triangles are supported")
                continue  # Physical lines/points do not enter the BEM surface.
            n_tags = row[2]
            if n_tags < 1 or len(row) != 6 + n_tags or row[3] <= 0:
                raise ValueError("Triangle requires a positive physical tag")
            faces.append([lookup[node] for node in row[3 + n_tags:]])
            tags.append(row[3])
            element_ids.append(row[0])
        mesh = SurfaceMesh(points, np.asarray(faces, dtype=np.int64),
                           np.asarray(tags, dtype=np.int64), node_ids, tuple(element_ids))
        if (not faces or not np.isfinite(points).all() or len(set(element_ids)) != len(faces)
                or min(element_ids) <= 0 or np.any(mesh.areas_m2 <= 0)):
            raise ValueError("Surface requires finite nodes and unique nondegenerate triangles")
        return mesh
    except (IndexError, KeyError, OverflowError) as exc:
        raise ValueError("Invalid ASCII Gmsh 2.2 surface") from exc
