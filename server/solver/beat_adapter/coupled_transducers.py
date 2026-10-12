"""Electrodynamic coordinates shared by FEM and BEM moving boundaries."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import warnings

import numpy as np

from .preflight import UnsupportedPhysics
from .transducers import (DRIVER_SCALARS, ExteriorDriver, _projected_face_areas, driver_parameters,
                           projected_driver_area)


def _moving_patches(mesh_boundaries: Sequence[tuple[np.ndarray, np.ndarray]], axis: np.ndarray
                    ) -> tuple[np.ndarray, list[set[int]], list[list[int]]]:
    """Split edge connectivity at faces nearly parallel to the motion axis."""
    faces = np.concatenate([faces for faces, _ in mesh_boundaries])
    triangles = np.concatenate([triangles for _, triangles in mesh_boundaries])
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    sizes = np.linalg.norm(normals, axis=1)
    separators = np.abs(normals @ axis) < .3 * sizes
    owners: dict[tuple[int, int], list[int]] = {}
    neighbours = [set() for _ in faces]
    for index, face in enumerate(faces):
        for a, b in zip(face, np.roll(face, -1)):
            edge = tuple(sorted((int(a), int(b))))
            for owner in owners.setdefault(edge, []):
                neighbours[index].add(owner)
                neighbours[owner].add(index)
            owners[edge].append(index)
    remaining = set(np.flatnonzero(~separators))
    patches = []
    while remaining:
        pending, patch = [min(remaining)], []
        remaining.remove(pending[0])
        while pending:
            index = pending.pop()
            patch.append(index)
            adjacent = neighbours[index] & remaining
            remaining.difference_update(adjacent)
            pending.extend(sorted(adjacent))
        patches.append(patch)
    return triangles, neighbours, patches


def parameters(spec: Any, meshes_m: Mapping[str, Sequence[tuple[np.ndarray, np.ndarray]]], *,
               collected_warnings: list[str] | None = None, channel_id: str = "unknown") -> tuple[dict[str, Any], float]:
    """Derive Sd per radiating side, keeping source meshes separate.

    Faces with |normal_hat . axis| < 0.3 separate connected front/rear skins.
    After removing those faces, patches with |net signed projection| at least
    25% of the largest patch in the channel define sides by sign. Smaller
    folds/lips and separator faces retain their signed projection and join the
    nearest connected side (ties or disconnected patches use the dominant
    side). Thus separately tagged folds subtract, neutral rims do not create a
    side, and closed skins use the mean of two sides and forbid a lumped rear
    chamber. A skin with no resolvable net side is refused by channel name.
    """
    raw = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else dict(spec)
    axis = np.asarray(raw["motion_axis"], dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or not np.any(axis):
        raise ValueError("Electrodynamic motion_axis must be finite and nonzero")
    axis /= np.max(np.abs(axis))
    axis /= np.linalg.norm(axis)
    meshes = [_moving_patches(boundaries, axis) for boundaries in meshes_m.values()]
    nets = [float(_projected_face_areas(triangles[patch], axis).sum())
            for triangles, _, patches in meshes for patch in patches]
    largest = max(map(abs, nets), default=0.)
    total = sum(float(np.abs(_projected_face_areas(triangles, axis)).sum())
                for triangles, _, _ in meshes)
    if largest <= total * 1e-12:
        raise UnsupportedPhysics(f"Channel {channel_id!r}: moving skin has no resolvable "
                                 "radiating side; check front/rear moving-face tags and motion axis")
    dominant = max(nets, key=abs) > 0
    sides: dict[bool, list[np.ndarray]] = {True: [], False: []}
    for triangles, neighbours, patches in meshes:
        labels: dict[int, bool] = {}
        for patch in patches:
            net = float(_projected_face_areas(triangles[patch], axis).sum())
            if abs(net) >= .25 * largest:
                labels.update({index: net > 0 for index in patch})
        # Expand all seeded sides one edge at a time; ties use the dominant
        # side. Each small fold stays together, even when it spans many faces.
        small = [set(patch) for patch in patches if patch[0] not in labels]
        small_faces = set().union(*small) if small else set()
        while len(labels) < len(triangles):
            updates = {}
            remaining = set(range(len(triangles))) - labels.keys()
            units = small + [{index} for index in remaining if index not in small_faces]
            for unit in units:
                linked = {labels[j] for i in unit for j in neighbours[i] if j in labels}
                if linked:
                    side = next(iter(linked)) if len(linked) == 1 else dominant
                    updates.update({index: side for index in unit})
            if not updates:
                updates = dict.fromkeys(remaining, dominant)
            labels.update(updates)
            small = [patch for patch in small if not patch.intersection(updates)]
        for side in sides:
            selected = [index for index, label in labels.items() if label == side]
            if selected:
                sides[side].append(triangles[selected])
    front, rear = (np.concatenate(sides[key]) if sides[key] else None for key in (True, False))
    for side in (front, rear):
        if side is not None and abs(float(_projected_face_areas(side, axis).sum())) <= total * 1e-12:
            raise UnsupportedPhysics(f"Channel {channel_id!r}: moving skin has no resolvable "
                                     "radiating side; check front/rear moving-face tags and motion axis")
    if front is not None and rear is not None:
        chamber = raw.get("lumped_sealed_rear_chamber") or {}
        if chamber.get("enabled", False):
            raise UnsupportedPhysics(
                f"Channel {channel_id!r}: a lumped rear chamber replaces the rear radiation; "
                "this diaphragm already radiates on both sides")
        area, warning = projected_driver_area(front, axis, rear_triangles_m=rear)
        if warning:
            warning = (f"Channel {channel_id!r}: Front and rear moving-face projected areas differ "
                       "by more than 10 %; Sd uses their mean.")
    else:
        area, warning = projected_driver_area(front if front is not None else rear, axis)
    if warning:
        warnings.warn(warning, UserWarning, stacklevel=2)
        if collected_warnings is not None:
            collected_warnings.append(warning)
    driver = ExteriorDriver("", (), axis, **{key: raw[key] for key in DRIVER_SCALARS},
                            semi_inductance=raw.get("semi_inductance"),
                            lumped_sealed_rear_chamber=raw.get("lumped_sealed_rear_chamber"))
    return driver_parameters(driver, axis, projected_area_m2=area), area


def mechanical_impedance(params: Mapping[str, Any], frequency: float, *, density: float,
                         sound_speed: float) -> complex:
    """Dry mechanics and optional lumped chamber in the solver phasor convention."""
    s = -1j * 2 * np.pi * frequency
    stiffness = 1 / params["cms_m_per_n"]
    chamber = params.get("lumped_sealed_rear_chamber") or {}
    if chamber.get("enabled", False):
        stiffness += density * sound_speed**2 * chamber["projected_area_m2"]**2 / chamber["volume_m3"]
    return params["rms_n_s_per_m"] + s * params["mmd_kg"] + stiffness / s


def recover_load(components: Sequence[Mapping[str, Any]], ports: Sequence[Mapping[str, Any]],
                 velocity: np.ndarray, current: np.ndarray, frequency: float, *,
                 density: float, sound_speed: float) -> np.ndarray:
    """Recover generalized transducer loads using every independent drive basis.

    F = Bl I - Zm v = Zload v. Ideal coordinates contribute their prescribed
    unit velocities too. Rank loss or a condition number above 1e10 produces
    gaps, rather than an unstable diagonal divided by one driver's velocity.
    Rows are transducers; columns are all components in compiled order.
    """
    drivers = [c for c in components if c["kind"] == "electrodynamic_transducer"]
    motion = np.zeros((len(components), len(ports)), complex)
    force = np.empty((len(drivers), len(ports)), complex)
    for index, component in enumerate(components):
        if component["kind"] == "electrodynamic_transducer":
            driver = drivers.index(component)
            motion[index] = velocity[:, driver]
            p = component["parameters"]
            force[driver] = p["bl_n_per_a"] * current[:, driver] - mechanical_impedance(
                p, frequency, density=density, sound_speed=sound_speed) * velocity[:, driver]
        else:
            motion[index] = [float(port["component_id"] == component["id"]) for port in ports]
    singular = np.linalg.svd(motion, compute_uv=False)
    if (len(singular) < len(components) or not len(singular) or singular[-1] <= 0
            or singular[-1] <= singular[0] * 1e-10):
        return np.full((len(drivers), len(components)), complex(np.nan, np.nan))
    return force @ np.linalg.pinv(motion, rcond=1e-10)


FEM_DRIVER_SYMMETRY_MESSAGE = (
    "Electrodynamic drivers on FEM regions need a full model until driver symmetry is supported; prepare the return with Force full domain enabled, or export without symmetry planes if the CAD author cut it"
)


def channel_problem(channel: Any) -> tuple[str, str] | None:
    """Restrictions shared by early admission and direct request compilation."""
    if getattr(channel, "driver", None) is not None:
        return "fem_drive_unsupported", f"Channel {channel.id!r}: FEM legacy driver models are unsupported; use an electrodynamic transducer"
    if getattr(channel, "patch_weights", None) is not None:
        return "fem_drive_unsupported", f"Channel {channel.id!r}: FEM patch weights are not supported yet"
    if getattr(channel, "motion", "normal") != "normal" and getattr(channel, "exterior_transducer", None) is None:
        return "fem_drive_unsupported", f"Channel {channel.id!r}: axial ideal sources are unsupported; use an electrodynamic transducer"
    return None
