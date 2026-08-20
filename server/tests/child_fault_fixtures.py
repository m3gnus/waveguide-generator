"""Faults installed inside the real disposable CAD child by focused tests."""

from __future__ import annotations

from typing import Any

import numpy as np


def install_reduced_domain_leak() -> None:
    """Remove one off-plane rigid triangle from reduced meshes only."""

    import meshio
    from hornlab_mesher import step_import

    real_postprocess = step_import.postprocess_mesh

    def puncture(mesh: Any, source_specs: Any, **kwargs: Any) -> Any:
        processed, repair, topology = real_postprocess(mesh, source_specs, **kwargs)
        if not kwargs.get("symmetry_planes"):
            return processed, repair, topology

        points = np.asarray(processed.points, dtype=float)
        triangles = np.asarray(processed.cells_dict["triangle"], dtype=np.int64)
        tags = np.asarray(
            processed.cell_data_dict["gmsh:physical"]["triangle"], dtype=np.int32
        )
        interior = next(
            index
            for index, triangle in enumerate(triangles)
            if tags[index] == 1
            and np.all(points[triangle, 0] > 1.0)
            and np.all(points[triangle, 1] > 1.0)
        )
        keep = np.ones(len(triangles), dtype=bool)
        keep[interior] = False
        return (
            meshio.Mesh(
                points=points,
                cells=[("triangle", triangles[keep])],
                cell_data={
                    "gmsh:physical": [tags[keep]],
                    "gmsh:geometrical": [tags[keep]],
                },
                field_data=processed.field_data,
            ),
            repair,
            topology,
        )

    step_import.postprocess_mesh = puncture
