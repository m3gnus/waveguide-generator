"""Pure NumPy/SciPy CPU BEM backend (fp64 reference).

Implements the production standard-Neumann exterior Helmholtz BIE

    (K - 1/2 M) p = V q

in complex128 throughout, with deterministic (thread-count independent) dense
Galerkin assembly. See ``assembly`` for the formulation, quadrature and
determinism contract, ``geometry`` for the SoA buffer conventions and
``solve`` for the LU solve and exterior field evaluation.
"""

from __future__ import annotations

from .assembly import (
    AIR_DENSITY,
    REFERENCE_PRESSURE,
    SPEED_OF_SOUND,
    assemble_system,
    default_thread_count,
    neumann_from_tags,
    wavenumber,
)
from .geometry import MeshError, SurfaceMesh, build_surface_mesh, load_msh
from .solve import (
    SurfaceSolution,
    evaluate_field,
    solve_dense,
    solve_neumann,
    sound_pressure_level,
)

__all__ = [
    "AIR_DENSITY",
    "REFERENCE_PRESSURE",
    "SPEED_OF_SOUND",
    "MeshError",
    "SurfaceMesh",
    "SurfaceSolution",
    "assemble_system",
    "build_surface_mesh",
    "default_thread_count",
    "evaluate_field",
    "load_msh",
    "neumann_from_tags",
    "solve_dense",
    "solve_neumann",
    "sound_pressure_level",
    "wavenumber",
]
