"""HBB's polar cuts and theta-major sphere as official arbitrary points."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ObservationLayout:
    """Ordered output points in the mesh's metre frame, plus durable axes."""

    angles_deg: np.ndarray
    planes: tuple[str, ...]
    points_m: dict[str, np.ndarray]
    sphere_theta_deg: np.ndarray | None = None
    sphere_phi_deg: np.ndarray | None = None

    def exterior_outputs(self) -> list[dict[str, Any]]:
        return [
            {"id": f"pressure:{name}", "quantity": "exterior_pressure",
             "target_ids": [], "options": {"points_m": points.tolist()}}
            for name, points in self.points_m.items()
        ]


def _count(value: int, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f"Observation count must be an integer >= {minimum}")
    return int(value)


def build_observations(
    *, angle_range: tuple[float, float, int] = (0.0, 180.0, 37),
    planes: Sequence[str] = ("horizontal", "vertical"), distance_m: float = 2.0,
    inclination_deg: float = 45.0, origin_m: Sequence[float] = (0.0, 0.0, 0.0),
    axis: Sequence[float] = (0.0, 0.0, 1.0), u: Sequence[float] = (1.0, 0.0, 0.0),
    v: Sequence[float] = (0.0, 1.0, 0.0), sphere_grid: tuple[int, int] | None = (37, 72),
    sphere_theta_max_deg: float = 180.0, precision: str = "float32",
) -> ObservationLayout:
    """Map local (+x horizontal, +y vertical, +z forward) points to the frame.

    Resolve mouth/throat origins before calling, using WG's authoritative
    frame. Points and mesh must use the same frame; no engine-side translation
    is needed. HBB's Float32 polar angles and selected geometry precision are
    retained; sphere axes are rebuilt in Float64 for DI/balloon endpoints.
    """
    if precision not in {"float32", "float64"}:
        raise ValueError("Observation precision must be float32 or float64")
    scalar = np.float32 if precision == "float32" else np.float64
    start, end, count = angle_range
    count = _count(count, 1)
    if (not math.isfinite(start) or not math.isfinite(end)
            or not -180.0 <= start <= 0.0 <= end <= 180.0
            or (count == 1 and start != end) or (count > 1 and start == end)):
        raise ValueError("Polar range must span zero within [-180, 180] with a valid count")
    if not math.isfinite(distance_m) or distance_m <= 0 or not math.isfinite(inclination_deg):
        raise ValueError("Distance must be finite and positive; inclination must be finite")
    origin = np.asarray(origin_m, dtype=float)
    basis = np.column_stack((u, v, axis)).astype(float)
    if (origin.shape != (3,) or basis.shape != (3, 3)
            or not np.isfinite(origin).all() or not np.isfinite(basis).all()
            or not np.allclose(basis.T @ basis, np.eye(3), atol=1e-6, rtol=0)
            or not np.isclose(np.linalg.det(basis), 1.0, atol=1e-6, rtol=0)):
        raise ValueError("Observation frame must be finite, right-handed and orthonormal")
    planes = tuple(planes)
    if (not planes or len(set(planes)) != len(planes)
            or set(planes) - {"horizontal", "vertical", "diagonal"}):
        raise ValueError("Planes must be unique horizontal/vertical/diagonal names")

    # BeatEngineDriver.polar_observation_points: range with a T-valued step,
    # followed by a Float32 endpoint append/clamp, even on the Float64 path.
    lo, hi = float(scalar(start)), float(scalar(end))
    step = float(scalar((end - start) / (count - 1) if count > 1 else 1.0))
    if step <= 0 or not math.isfinite(step):
        raise ValueError("Polar step is not representable at the selected precision")
    angles = (lo + np.arange(math.floor((hi - lo) / step) + 1) * step).astype(np.float32)
    if angles[-1] < np.float32(hi):
        angles = np.append(angles, np.float32(hi))
    angles = np.clip(angles, np.float32(lo), np.float32(hi))
    radians = scalar(np.pi) * angles.astype(scalar) / scalar(180.0)
    distance = scalar(distance_m)
    transverse = distance * np.sin(radians)
    forward = distance * np.cos(radians)
    zero = np.zeros_like(forward)
    inclination = scalar(np.pi) * scalar(inclination_deg) / scalar(180.0)
    local = {
        "horizontal": np.column_stack((transverse, zero, forward)),
        "vertical": np.column_stack((zero, transverse, forward)),
        "diagonal": np.column_stack((transverse * np.cos(inclination),
                                     transverse * np.sin(inclination), forward)),
    }
    points = {name: local[name] @ basis.T + origin for name in planes}

    theta_deg = phi_deg = None
    if sphere_grid is not None:
        n_theta, n_phi = sphere_grid
        n_theta, n_phi = _count(n_theta, 2), _count(n_phi, 3)
        if n_theta * n_phi > 100_000:
            raise ValueError("Sphere grid exceeds 100000 points")
        if not math.isfinite(sphere_theta_max_deg) or not 0 < sphere_theta_max_deg <= 180:
            raise ValueError("Sphere theta maximum must be in (0, 180]")
        theta_deg = np.repeat(np.linspace(0.0, sphere_theta_max_deg, n_theta), n_phi)
        phi_deg = np.tile(np.arange(n_phi) * (360.0 / n_phi), n_theta)
        # Match the driver's arithmetic/order; phi varies fastest, no wrap.
        theta = np.repeat(scalar(np.pi) * scalar(sphere_theta_max_deg) / scalar(180.0)
                          * np.arange(n_theta, dtype=scalar) / scalar(n_theta - 1), n_phi)
        phi = np.tile(scalar(2 * np.pi) * np.arange(n_phi, dtype=scalar) / scalar(n_phi), n_theta)
        local_sphere = np.column_stack((distance * np.sin(theta) * np.cos(phi),
                                       distance * np.sin(theta) * np.sin(phi),
                                       distance * np.cos(theta)))
        points["sphere"] = local_sphere @ basis.T + origin
    return ObservationLayout(angles.astype(float), planes, points, theta_deg, phi_deg)
