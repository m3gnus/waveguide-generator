"""Authoritative solver-mesh construction.

The separation between viewport tessellation and the full OCC solve mesh ports
v1 ``server/services/simulation_runner.py:430-489``.
"""

from .builder import build_solver_mesh
from .gmsh_worker import run_on_gmsh_worker, shutdown_gmsh_worker
from .integrity import mesh_integrity_report

__all__ = ["build_solver_mesh", "mesh_integrity_report", "run_on_gmsh_worker", "shutdown_gmsh_worker"]
