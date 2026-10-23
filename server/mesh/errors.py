"""Mesh refusals shared by builders and the isolated CAD intake boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.design.defaults import default_cad_wall_size_mm


class MeshTooLarge(RuntimeError):
    """The generated solver artifact exceeds the triangle limit."""

    def __init__(self, count: int, limit: int, *, mesh_sizes: Mapping[str, Any] | None = None) -> None:
        self.count = count
        self.limit = limit
        self.mesh_sizes = dict(mesh_sizes) if mesh_sizes is not None else None
        super().__init__(self.message())

    def message(self) -> str:
        message = (
            f"This model meshes to {self.count:,} triangles; WG's limit is "
            f"{self.limit:,} for the part it solves. "
        )
        if self.mesh_sizes is None:
            return message + "Use coarser throat/mouth/rear resolution before solving again."
        wall = self.mesh_sizes["rigid_size_mm"]
        default_wall = default_cad_wall_size_mm()
        return (
            message + "In Fusion, open Waveguide Generator ▸ Sources & Mesh…, "
            f"raise Walls (now {wall:g} mm; default {default_wall:g} mm) "
            "or add a Coarse surface group, and Send again."
        )
