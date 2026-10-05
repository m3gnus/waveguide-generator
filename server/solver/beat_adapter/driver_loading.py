"""Historical pressure loading from compiled P1 boundary traces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .mesh import SurfaceMesh


@dataclass(frozen=True)
class BoundaryLoading:
    """Physical source faces in compiled node/face order, without image copies."""

    node_count: int
    face_count: int
    source_faces: np.ndarray
    areas_m2: np.ndarray

    @classmethod
    def from_mesh(cls, mesh: SurfaceMesh, tags: Sequence[int]) -> BoundaryLoading:
        if not tags or len(set(tags)) != len(tags) or set(tags) - set(mesh.tags):
            raise ValueError("Loading requires unique source tags present in the mesh")
        selected = np.isin(mesh.tags, tags)
        return cls(len(mesh.points_m), len(mesh.faces), mesh.faces[selected], mesh.areas_m2[selected])

    @property
    def area_m2(self) -> float:
        return float(np.sum(self.areas_m2))

    def mean_pressure(self, boundary_pressure: np.ndarray) -> complex:
        """Integrate P1 pressure, without the signed normal/axis force projection.

        Input may be velocity- or acceleration-basis pressure; the returned
        mean has the same basis. Real symmetry copies cancel in numerator and
        denominator. A fictitious ground image contributes to neither.
        """
        pressure = np.asarray(boundary_pressure, dtype=complex)
        if pressure.shape != (self.node_count,) or not np.isfinite(pressure).all():
            raise ValueError("Boundary pressure must be finite in compiled node order")
        if (self.source_faces.ndim != 2 or self.source_faces.shape[1:] != (3,)
                or self.areas_m2.shape != (len(self.source_faces),)
                or not np.isfinite(self.areas_m2).all() or np.any(self.areas_m2 <= 0)
                or not len(self.source_faces) or np.any(self.source_faces < 0)
                or np.any(self.source_faces >= self.node_count)):
            raise ValueError("Invalid source loading topology or areas")
        averages = np.mean(pressure[self.source_faces], axis=1)
        return complex(np.sum(averages * self.areas_m2) / self.area_m2)
