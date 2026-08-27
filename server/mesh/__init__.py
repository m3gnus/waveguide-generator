"""Authoritative solver-mesh construction.

The separation between viewport tessellation and the full OCC solve mesh ports
v1 ``server/services/simulation_runner.py:430-489``.

Re-exported lazily. Importing a submodule runs this file, so eagerly importing
``.builder`` here meant that merely reaching for the gmsh worker -- which
``server/app.py`` does at import time, to register the prewarm -- pulled in
meshio and numpy: 145 ms of every server start, for code that first runs when
somebody solves or exports. PEP 562 keeps every existing ``from server.mesh
import build_solver_mesh`` working and defers the cost to the first use.
"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .builder import build_solver_mesh, clear_solver_mesh_cache, solver_mesh_cache_info
    from .gmsh_worker import run_on_gmsh_worker, shutdown_gmsh_worker
    from .integrity import mesh_element_quality_report, mesh_integrity_report


_EXPORTS = {
    "build_solver_mesh": ".builder",
    "clear_solver_mesh_cache": ".builder",
    "solver_mesh_cache_info": ".builder",
    "run_on_gmsh_worker": ".gmsh_worker",
    "shutdown_gmsh_worker": ".gmsh_worker",
    "mesh_element_quality_report": ".integrity",
    "mesh_integrity_report": ".integrity",
}


def __getattr__(name: str) -> object:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = [
    "build_solver_mesh",
    "clear_solver_mesh_cache",
    "mesh_element_quality_report",
    "mesh_integrity_report",
    "run_on_gmsh_worker",
    "shutdown_gmsh_worker",
    "solver_mesh_cache_info",
]
