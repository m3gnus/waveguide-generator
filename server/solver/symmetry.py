"""Mesher-authoritative mirror-symmetry resolution for solve domains."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import threading
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
    number = value.constant_value()
    if number is None or not math.isfinite(number):
        return None
    return float(number)


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


#: Resolutions keyed by the geometry that produced them. Small on purpose: the
#: point is to absorb the repeats -- the same design resolved on submit and
#: again on the next edit that did not touch geometry -- not to remember a
#: session's whole history.
_RESOLUTION_CACHE_SIZE = 32
_resolution_cache: "OrderedDict[str, SymmetryResolution]" = OrderedDict()
_resolution_cache_lock = threading.Lock()


def clear_symmetry_cache() -> None:
    """Forget every memoized resolution. For tests and cache maintenance."""

    with _resolution_cache_lock:
        _resolution_cache.clear()


def _resolution_cache_key(design: DesignConfig | Mapping[str, Any]) -> str | None:
    """Hash the geometry this resolution depends on, or None if it cannot be."""

    try:
        validated = (
            design if isinstance(design, DesignConfig) else DesignConfig.model_validate(design)
        )
        config = design_to_mesher_config(validated)
    except Exception:  # noqa: BLE001 - an unhashable design just skips the cache
        return None
    try:
        serialized = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_symmetry(design: DesignConfig | Mapping[str, Any]) -> SymmetryResolution:
    """Resolve the smallest safe ATH domain from sampled mesher geometry.

    Sampling failures deliberately resolve to the full domain. A false negative
    costs solve time; a false positive silently changes the physical problem.

    Results are memoized on the mesher configuration, which is the complete
    input to the sampling below. Measured at 1152 ms on the first call and
    57-150 ms after, and it runs on every submit *and* on every committed
    design edit through ``POST /api/design/symmetry`` -- so a design that is
    edited in a way that does not move geometry now costs nothing at all.
    """

    cache_key = _resolution_cache_key(design)
    if cache_key is not None:
        with _resolution_cache_lock:
            cached = _resolution_cache.get(cache_key)
            if cached is not None:
                _resolution_cache.move_to_end(cache_key)
                return cached

    resolution = _resolve_symmetry_uncached(design)
    if cache_key is not None:
        with _resolution_cache_lock:
            _resolution_cache[cache_key] = resolution
            _resolution_cache.move_to_end(cache_key)
            while len(_resolution_cache) > _RESOLUTION_CACHE_SIZE:
                _resolution_cache.popitem(last=False)
    return resolution


def _resolve_symmetry_uncached(design: DesignConfig | Mapping[str, Any]) -> SymmetryResolution:
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
    # drops it in every domain: the solve mesh is always built in the recentred
    # origin frame, so no cut plane can reconstruct about the wrong plane and
    # the placement reaches only the CAD exports.  A rigid translation cannot
    # destroy a mirror plane either, so it must not veto the xz reduction:
    # doing so forced every vertically offset design onto a half or full domain
    # for no physical reason.  Only a value the mesher cannot place is
    # disqualifying.
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
    "clear_symmetry_cache",
    "resolve_symmetry",
    "validate_symmetry_mode",
]
