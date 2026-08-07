"""Mesher-authoritative mirror-symmetry resolution for solve domains."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

import numpy as np

from server.design.schema import DesignConfig, Expr
from server.preview.translate import design_to_mesher_config


SYMMETRY_RELATIVE_TOLERANCE = 2.0e-4
SYMMETRY_ABSOLUTE_TOLERANCE_MM = 1.0e-7
SYMMETRY_ANGULAR_SEGMENTS = 128
SYMMETRY_LENGTH_SEGMENTS = 16

SYMMETRY_MODE_QUADRANTS: dict[str, int] = {
    "auto": 1234,
    "full": 1234,
    "half_xz": 12,
    "half_yz": 14,
    "quarter": 1,
}


@dataclass(frozen=True, slots=True)
class SymmetryResolution:
    quadrants: int
    xz: bool
    yz: bool
    reasons: dict[str, list[str]]
    tolerance_mm: float
    relative_tolerance: float = SYMMETRY_RELATIVE_TOLERANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scalar(value: Expr | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    if value.value is None or not math.isfinite(value.value):
        return None
    return float(value.value)


def _reshape_surface(raw: Any, n_phi: int, n_length: int) -> np.ndarray | None:
    if raw is None:
        return None
    points = np.asarray(raw, dtype=np.float64)
    expected = n_phi * (n_length + 1) * 3
    if points.size != expected or not np.isfinite(points).all():
        raise ValueError("mesher point grid contains invalid surface points")
    return points.reshape(n_phi, n_length + 1, 3)


def _point_segment_distances(points: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Distance from each point to the nearest segment of one closed ring."""

    starts = ring
    deltas = np.roll(ring, -1, axis=0) - starts
    lengths_sq = np.einsum("ij,ij->i", deltas, deltas)
    offsets = points[:, None, :] - starts[None, :, :]
    fractions = np.divide(
        np.einsum("pij,ij->pi", offsets, deltas),
        lengths_sq[None, :],
        out=np.zeros((len(points), len(ring)), dtype=np.float64),
        where=lengths_sq[None, :] > 1.0e-24,
    )
    fractions = np.clip(fractions, 0.0, 1.0)
    nearest = starts[None, :, :] + fractions[:, :, None] * deltas[None, :, :]
    return np.min(np.linalg.norm(points[:, None, :] - nearest, axis=2), axis=1)


def _surface_mirror_deviation(surface: np.ndarray, coordinate: int) -> float:
    maximum = 0.0
    for axial_index in range(surface.shape[1]):
        ring = surface[:, axial_index, :]
        reflected = ring.copy()
        reflected[:, coordinate] *= -1.0
        distances = _point_segment_distances(reflected, ring)
        maximum = max(maximum, float(np.max(distances, initial=0.0)))
    return maximum


def _margin_pair(
    design: DesignConfig,
    left_name: str,
    right_name: str,
) -> tuple[float | None, float | None]:
    enclosure = design.root.enclosure
    if enclosure is None:
        return 25.0, 25.0
    return (
        _scalar(getattr(enclosure, left_name), 25.0),
        _scalar(getattr(enclosure, right_name), 25.0),
    )


def _append_spacing_reason(
    reasons: dict[str, list[str]],
    plane: str,
    labels: tuple[str, str],
    values: tuple[float | None, float | None],
    tolerance_mm: float,
) -> None:
    first, second = values
    if first is None or second is None:
        reasons[plane].append(
            f"enclosure {labels[0]}/{labels[1]} spacing is not a finite scalar"
        )
    elif abs(first - second) > tolerance_mm:
        reasons[plane].append(
            f"enclosure {labels[0]}={first:g} mm differs from {labels[1]}={second:g} mm"
        )


def _failed_resolution(message: str) -> SymmetryResolution:
    return SymmetryResolution(
        quadrants=1234,
        xz=False,
        yz=False,
        reasons={"xz": [message], "yz": [message]},
        tolerance_mm=SYMMETRY_ABSOLUTE_TOLERANCE_MM,
    )


def resolve_symmetry(design: DesignConfig | Mapping[str, Any]) -> SymmetryResolution:
    """Resolve the smallest safe ATH domain from sampled mesher geometry.

    Sampling failures deliberately resolve to the full domain. A false negative
    costs solve time; a false positive silently changes the physical problem.
    """

    try:
        validated = (
            design if isinstance(design, DesignConfig) else DesignConfig.model_validate(design)
        )
        config = design_to_mesher_config(validated)
        mesh = dict(config.get("mesh") or {})
        mesh.update(
            {
                "quadrants": 1234,
                "angularSegments": SYMMETRY_ANGULAR_SEGMENTS,
                "lengthSegments": SYMMETRY_LENGTH_SEGMENTS,
            }
        )
        config["mesh"] = mesh
        from hornlab_mesher.config_builder import build_geometry_params, build_point_grid

        params, _formula, mode = build_geometry_params(config)
        params["quadrants"] = "1234"
        params["angularSegments"] = SYMMETRY_ANGULAR_SEGMENTS
        params["lengthSegments"] = SYMMETRY_LENGTH_SEGMENTS
        grid = build_point_grid(params)
        n_phi = int(grid["grid_n_phi"])
        n_length = int(grid["grid_n_length"])
        surfaces = [
            surface
            for surface in (
                _reshape_surface(grid.get("inner_points"), n_phi, n_length),
                _reshape_surface(grid.get("outer_points"), n_phi, n_length),
            )
            if surface is not None
        ]
        if not surfaces:
            return _failed_resolution("mesher point grid returned no surface points")
    except Exception as exc:
        return _failed_resolution(f"mesher symmetry sampling failed: {exc}")

    all_points = np.concatenate([surface.reshape(-1, 3) for surface in surfaces])
    diagonal = float(np.linalg.norm(np.ptp(all_points, axis=0)))
    tolerance_mm = max(
        SYMMETRY_ABSOLUTE_TOLERANCE_MM,
        SYMMETRY_RELATIVE_TOLERANCE * max(diagonal, SYMMETRY_ABSOLUTE_TOLERANCE_MM),
    )
    reasons: dict[str, list[str]] = {"xz": [], "yz": []}
    for plane, coordinate in (("xz", 1), ("yz", 0)):
        deviation = max(
            _surface_mirror_deviation(surface, coordinate) for surface in surfaces
        )
        if deviation > tolerance_mm:
            reasons[plane].append(
                f"sampled horn surface misses the {plane} mirror by up to "
                f"{deviation:.6g} mm (tolerance {tolerance_mm:.6g} mm)"
            )

    # Mesh.VerticalOffset is a rigid +y placement translation the mesher applies
    # after every cut plane has run at the origin, and ``_solver_mesher_config``
    # drops it outright for the y-cut domains (quadrants 1 and 12) that would
    # otherwise reconstruct about the wrong plane.  A rigid translation cannot
    # destroy a mirror plane, so it must not veto the xz reduction: doing so
    # forced every vertically offset design onto a half or full domain for no
    # physical reason.  Only a value the mesher cannot place is disqualifying.
    vertical_offset = _scalar(validated.root.mesh.vertical_offset, 0.0)
    if vertical_offset is None:
        reasons["xz"].append("mesh.vertical_offset is not a finite scalar")

    if mode == "enclosure":
        _append_spacing_reason(
            reasons,
            "yz",
            ("space_l", "space_r"),
            _margin_pair(validated, "space_l", "space_r"),
            tolerance_mm,
        )
        _append_spacing_reason(
            reasons,
            "xz",
            ("space_t", "space_b"),
            _margin_pair(validated, "space_t", "space_b"),
            tolerance_mm,
        )

    xz = not reasons["xz"]
    yz = not reasons["yz"]
    quadrants = 1 if xz and yz else 12 if xz else 14 if yz else 1234
    return SymmetryResolution(
        quadrants=quadrants,
        xz=xz,
        yz=yz,
        reasons=reasons,
        tolerance_mm=tolerance_mm,
    )


def validate_symmetry_mode(mode: str, resolution: SymmetryResolution) -> int:
    """Return the explicit mask, rejecting any requested absent plane."""

    normalized = str(mode).strip().lower()
    if normalized not in SYMMETRY_MODE_QUADRANTS:
        raise ValueError(
            "symmetry must be one of auto, full, half_xz, half_yz, or quarter"
        )
    if normalized == "auto":
        return resolution.quadrants
    required = {
        "half_xz": ("xz",),
        "half_yz": ("yz",),
        "quarter": ("xz", "yz"),
    }.get(normalized, ())
    missing = [plane for plane in required if not getattr(resolution, plane)]
    if missing:
        details = "; ".join(
            f"{plane} plane: " + "; ".join(resolution.reasons[plane])
            for plane in missing
        )
        raise ValueError(f"Forced symmetry mode '{normalized}' is invalid: {details}")
    return SYMMETRY_MODE_QUADRANTS[normalized]


__all__ = [
    "SYMMETRY_ANGULAR_SEGMENTS",
    "SYMMETRY_LENGTH_SEGMENTS",
    "SYMMETRY_MODE_QUADRANTS",
    "SYMMETRY_RELATIVE_TOLERANCE",
    "SymmetryResolution",
    "resolve_symmetry",
    "validate_symmetry_mode",
]
