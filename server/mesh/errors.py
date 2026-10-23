"""Mesh refusals shared by builders and the isolated CAD intake boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MeshTooLarge(RuntimeError):
    """The generated solver artifact exceeds the triangle limit."""

    def __init__(self, count: int, limit: int, *, mesh_sizes: Mapping[str, Any] | None = None) -> None:
        self.count = count
        self.limit = limit
        self.mesh_sizes = dict(mesh_sizes) if mesh_sizes is not None else None
        super().__init__(self.message())

    def message(self, source_roles: Mapping[str, str] | None = None) -> str:
        message = (
            f"This model meshes to {self.count:,} triangles; WG's limit is "
            f"{self.limit:,} for the part it solves. "
        )
        if self.mesh_sizes is None:
            return message + "Use coarser throat/mouth/rear resolution before solving again."
        sizes = self.mesh_sizes
        used = [
            f"walls {sizes['rigid_size_mm']:g} mm",
            f"grading distance {sizes['transition_mm']:g} mm",
        ]
        for source_id, size in sorted(sizes["source_size_mm"].items()):
            label = (source_roles or {}).get(source_id, source_id)
            used.append(f"{label} source {size:g} mm")
        return (
            message + "Mesh sizes used: " + ", ".join(used) + ". "
            "Larger sizes give fewer triangles. Use coarser wall or source sizes before solving again."
        )
