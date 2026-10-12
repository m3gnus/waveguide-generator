"""Infer full-driver completion and physical image orbits from moving skins."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from .beat_adapter.preflight import UnsupportedPhysics


SYMMETRY_FIELDS = frozenset({"symmetry_role", "fractional_symmetry_axes",
                             "surface_completion_factor", "physical_driver_orbit_count"})


class DriverSymmetryError(UnsupportedPhysics):
    """A geometry refusal with a stable admission code."""

    def __init__(self, message: str, *, code: str = "driver_symmetry_unsupported"):
        self.code = code
        super().__init__(message)


def infer_component_symmetry(
    meshes_m: Mapping[str, Sequence[tuple[np.ndarray, np.ndarray]]],
    motion_axis: Sequence[float], active_axes: tuple[int, ...] = (), *,
    channel_id: str = "unknown", tolerance_m: float = 1e-9,
) -> dict:
    """Union selected triangles per mesh before inspecting their perimeter.

    Coordinate identity joins neighbouring groups even when their face winding
    differs. Independent FEM/BEM meshes stay separate: coincident front and rear
    skins must not cancel each other's perimeter. Each mesh must contain one
    connected patch; different meshes must describe the same cut; otherwise one
    mechanical coordinate is ambiguous.
    """
    if len(set(active_axes)) != len(active_axes) or any(a not in (0, 1) for a in active_axes):
        raise DriverSymmetryError("Driver symmetry supports only x0 and y0 cut planes")
    axis = np.asarray(motion_axis, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or not np.any(axis):
        raise DriverSymmetryError(f"Channel {channel_id!r}: motion_axis must be finite and nonzero")
    axis = axis / np.max(np.abs(axis))
    axis /= np.linalg.norm(axis)
    cuts = []
    for mesh, groups in meshes_m.items():
        if not groups or not any(len(triangles) for _, triangles in groups):
            continue
        corners = np.concatenate([triangles for _, triangles in groups])
        points, inverse = np.unique(corners.reshape(-1, 3), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3)
        owners: dict[tuple[int, int], list[int]] = {}
        neighbours = [set() for _ in faces]
        for index, face in enumerate(faces):
            for a, b in zip(face, np.roll(face, -1)):
                edge = tuple(sorted((int(a), int(b))))
                for other in owners.setdefault(edge, []):
                    neighbours[index].add(other)
                    neighbours[other].add(index)
                owners[edge].append(index)
        if any(len(indices) > 2 for indices in owners.values()):
            raise DriverSymmetryError(f"Channel {channel_id!r}: moving surface union is nonmanifold")
        perimeter = [(edge, indices[0]) for edge, indices in owners.items() if len(indices) == 1]
        remaining = set(range(len(faces)))
        mesh_cuts = []
        while remaining:
            pending = [min(remaining)]
            patch = set(pending)
            remaining.difference_update(patch)
            while pending:
                adjacent = neighbours[pending.pop()] & remaining
                remaining.difference_update(adjacent)
                patch.update(adjacent)
                pending.extend(adjacent)
            edges = [edge for edge, owner in perimeter if owner in patch]
            mesh_cuts.append(tuple(a for a in active_axes if any(
                np.all(np.abs(points[list(edge), a]) <= tolerance_m) for edge in edges)))
        if len(mesh_cuts) > 1:
            raise DriverSymmetryError(
                f"Channel {channel_id!r}: its moving faces form {len(mesh_cuts)} separate surfaces "
                f"in {mesh}; give each driver its own drive channel (or connect the surfaces)",
                code="driver_disconnected_patches")
        cuts.extend(mesh_cuts)
    if not cuts:
        raise DriverSymmetryError(f"Channel {channel_id!r}: driver has no moving triangles")
    if len(set(cuts)) != 1:
        raise DriverSymmetryError(f"Channel {channel_id!r}: moving surfaces in different meshes imply different "
                         "symmetry cuts; split them into separate driver channels")
    fractional = cuts[0]
    for a in fractional:
        if abs(axis[a]) > 1e-8:
            raise DriverSymmetryError(f"Channel {channel_id!r}: motion_axis must lie in cutting plane "
                             f"{'xy'[a]}0 for this physical driver; prepare with Force full domain")
    completion = 2 ** len(fractional)
    return {"symmetry_role": "fractional_driver" if fractional else "complete_representative",
            "fractional_symmetry_axes": ['xy'[a] for a in fractional],
            "surface_completion_factor": completion,
            "physical_driver_orbit_count": 2 ** len(active_axes) // completion}
