"""BEMPP retained-field evaluation owned by its isolated native worker."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
import tempfile
from typing import Any

import numpy as np

from server.mesh.artifact import mesh_text_sha256
from server.platform.temp_session import spawned_directory_root

from .field_plane_result import FieldPlaneEvaluation


# Native grids never cross the IPC boundary or enter the shared field service.
_BEMPP_MESH_CACHE: OrderedDict[tuple[str, str | None], Any] = OrderedDict()


def evaluate_bempp_field_payload(payload: Mapping[str, Any]) -> FieldPlaneEvaluation:
    """Native retained-field work; dispatched only inside the BEMPP worker."""
    from .bempp_opencl import execution_route, guard_execution, native_call

    (mesh_text, frequency_hz, k_real, symmetry_plane, pressure, neumann,
     _backend, synthesis_revision) = payload["traces"]
    points = payload["points"]
    backend, _device = execution_route()
    from .bempp import bempp_status

    status = bempp_status()
    if not status.get("available"):
        raise RuntimeError(f"BEMPP field backend unavailable: {status.get('reason')}")
    import hornlab_bempp_bem as api

    guard_execution(backend, "cpu")
    key = (mesh_text_sha256(mesh_text), symmetry_plane)
    mesh = _BEMPP_MESH_CACHE.pop(key, None)
    if mesh is None:
        with tempfile.TemporaryDirectory(dir=spawned_directory_root()) as directory:
            path = Path(directory) / "mesh.msh"
            path.write_text(mesh_text, encoding="utf-8")
            mesh = api.load_mesh(path, native_symmetry_plane=symmetry_plane)
    _BEMPP_MESH_CACHE[key] = mesh
    while len(_BEMPP_MESH_CACHE) > 4:
        _BEMPP_MESH_CACHE.popitem(last=False)
    values = np.asarray(native_call(api.evaluate_exterior_from_traces,
        mesh, frequency_hz, k_real, pressure, neumann, points,
        symmetry_plane=symmetry_plane, assembly_backend=backend, opencl_device="cpu",
    ))
    if values.shape != (points.shape[0],):
        raise RuntimeError("field evaluator returned an unexpected pressure grid shape")
    return FieldPlaneEvaluation(
        frequency_hz=float(frequency_hz),
        pressure=np.ascontiguousarray(values, dtype=np.complex64),
        geometry_sha256=mesh_text_sha256(mesh_text),
        synthesis_revision=synthesis_revision,
        symmetry_plane=symmetry_plane,
    )
