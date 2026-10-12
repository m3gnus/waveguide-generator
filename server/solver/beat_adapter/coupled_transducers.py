"""Electrodynamic coordinates shared by FEM and BEM moving boundaries."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import warnings

import numpy as np

from .request import _axis
from .transducers import DRIVER_SCALARS, ExteriorDriver, driver_parameters, resolve_driver_sd


def parameters(spec: Any, meshes_m: Mapping[str, Sequence[tuple[np.ndarray, np.ndarray]]], *,
               collected_warnings: list[str] | None = None, channel_id: str = "unknown",
               sd_metadata: dict[str, Any] | None = None,
               face_weights: Mapping[str, Sequence[np.ndarray]] | None = None,
               surface_completion_factor: int = 1) -> tuple[dict[str, Any], float | None]:
    """Use declared Sd, with per-mesh geometry as a fallback and cross-check."""
    raw = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else dict(spec)
    axis = _axis(raw["motion_axis"])
    area, metadata, notes = resolve_driver_sd(raw, meshes_m, axis, channel_id, face_weights=face_weights,
                                              surface_completion_factor=surface_completion_factor)
    if sd_metadata is not None:
        sd_metadata.update(metadata)
    for note in notes:
        warnings.warn(note, UserWarning, stacklevel=2)
    if collected_warnings is not None:
        collected_warnings.extend(notes)
    driver = ExteriorDriver("", (), axis, **{key: raw[key] for key in DRIVER_SCALARS},
                            sd_m2=raw.get("sd_m2"), semi_inductance=raw.get("semi_inductance"),
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


def channel_problem(channel: Any) -> tuple[str, str] | None:
    """Restrictions shared by early admission and direct request compilation."""
    if getattr(channel, "driver", None) is not None:
        return "fem_drive_unsupported", f"Channel {channel.id!r}: FEM legacy driver models are unsupported; use an electrodynamic transducer"
    weights = getattr(channel, "patch_weights", None)
    if weights is not None:
        if set(weights) != set(channel.source_ids):
            return "fem_drive_unsupported", f"Channel {channel.id!r}: patch weights must cover exactly source_ids"
        if any(isinstance(w, bool) or not isinstance(w, (int, float)) or not np.isfinite(w) or w <= 0
               for w in weights.values()):
            return "fem_drive_unsupported", f"Channel {channel.id!r}: FEM patch weights must be finite and positive"
    if getattr(channel, "motion", "normal") != "normal" and getattr(channel, "exterior_transducer", None) is None:
        return "fem_drive_unsupported", f"Channel {channel.id!r}: axial ideal sources are unsupported; use an electrodynamic transducer"
    return None
