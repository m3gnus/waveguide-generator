"""Faults installed inside the real disposable CAD child by focused tests."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np


def _rewrite_reduced_meshes(
    rewrite: Callable[
        [np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]
    ],
) -> None:
    """Rewrite the postprocessed mesh of reduced domains only.

    The seam is after the mesher's postprocess and before WG verifies what it
    returned, which is where a fault in a reduced mesh would first appear. A
    full domain passes through untouched, so an auto-cut fallback meshes clean.
    """

    import meshio
    from hornlab_mesher import step_import

    real_postprocess = step_import.postprocess_mesh

    def rewritten(mesh: Any, source_specs: Any, **kwargs: Any) -> Any:
        processed, repair, topology = real_postprocess(mesh, source_specs, **kwargs)
        if not kwargs.get("symmetry_planes"):
            return processed, repair, topology

        points = np.asarray(processed.points, dtype=float)
        triangles = np.asarray(processed.cells_dict["triangle"], dtype=np.int64)
        tags = np.asarray(
            processed.cell_data_dict["gmsh:physical"]["triangle"], dtype=np.int32
        )
        triangles, tags = rewrite(points, triangles, tags)
        return (
            meshio.Mesh(
                points=points,
                cells=[("triangle", triangles)],
                cell_data={
                    "gmsh:physical": [tags],
                    "gmsh:geometrical": [tags],
                },
                field_data=processed.field_data,
            ),
            repair,
            topology,
        )

    step_import.postprocess_mesh = rewritten


def install_reduced_domain_leak() -> None:
    """Remove one off-plane rigid triangle from reduced meshes only."""

    def puncture(
        points: np.ndarray, triangles: np.ndarray, tags: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        interior = next(
            index
            for index, triangle in enumerate(triangles)
            if tags[index] == 1
            and np.all(points[triangle, 0] > 1.0)
            and np.all(points[triangle, 1] > 1.0)
        )
        keep = np.ones(len(triangles), dtype=bool)
        keep[interior] = False
        return triangles[keep], tags[keep]

    _rewrite_reduced_meshes(puncture)


def install_reduced_domain_inversion() -> None:
    """Reverse the winding of every triangle of reduced meshes only.

    What a mesher that winds a reduced component from a rear-facing source
    returns: closed modulo its cut planes and perfectly consistent, so every
    check but the orientation one passes, and wound against the full model.
    """

    def invert(
        points: np.ndarray, triangles: np.ndarray, tags: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        return triangles[:, [0, 2, 1]], tags

    _rewrite_reduced_meshes(invert)
