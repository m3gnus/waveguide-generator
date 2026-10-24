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
            f"{self.limit:,} triangles before asking you to continue. "
        )
        if self.mesh_sizes is None:
            return (f"This model meshes to {self.count:,} triangles; WG's limit is "
                    f"{self.limit:,} for the part it solves. "
                    "Use coarser throat/mouth/rear resolution before solving again.")
        wall = self.mesh_sizes["rigid_size_mm"]
        default_wall = default_cad_wall_size_mm()
        return (
            message + "In Fusion, open Waveguide Generator ▸ Sources & Mesh…, "
            f"raise Walls (now {wall:g} mm; default {default_wall:g} mm) "
            "or add a Coarse surface group. Check the model size, or choose Solve anyway."
        )


class SolverMemoryTooLarge(RuntimeError):
    """The solver mesh needs more dense-solver memory than this machine allows.

    Raised by the dense memory guard even after the triangle ceiling was
    overridden ("Solve anyway"): only a coarser mesh can help, so callers end
    the request calmly with :data:`USER_MESSAGE` instead of asking again.
    """

    #: The one wording of the guard on every surface (the API, a job's read
    #: model, the source editor). The next step is the same for a Fusion,
    #: Onshape or WG model. The raw detail (DOFs, GiB) is a developer
    #: diagnostic and never reaches the user.
    NEXT_STEP = "Use a coarser mesh resolution, then prepare it again."
    USER_MESSAGE = "This model is too large for this machine's memory. " + NEXT_STEP

    @staticmethod
    def user_message(document_name: Any = None) -> str:
        """The guard's sentence for one model, without its next step."""

        name = str(document_name or "").strip() or "This model"
        return f"{name} is too large for this machine's memory."
