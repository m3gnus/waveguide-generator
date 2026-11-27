"""Mesher-authoritative mirror-symmetry resolution for solve domains."""

from __future__ import annotations

from collections import OrderedDict
import copy
from dataclasses import dataclass
import hashlib
import math
import threading
from typing import Any, Mapping

import numpy as np

from server.contracts.canonical import canonical_ascii_string_json

from server.contracts.geometry import (
    PLANE_BY_QUADRANTS,
    SYMMETRY_MODE_QUADRANTS as SYMMETRY_MODE_QUADRANTS,
    quadrants_for_symmetry_planes,
)
from server.design.schema import DesignConfig, Expr
from server.preview.construction import retain_mesher_construction
from server.preview.translate import design_to_mesher_config


#: The mesher's ``exact`` tolerance class (``hornlab_mesher.symmetry.TOLERANCES``),
#: which the check below uses; kept here for the wire and for
#: :func:`resolution_was_sampled` without importing the mesher.
SYMMETRY_RELATIVE_TOLERANCE = 2.0e-4
SYMMETRY_ABSOLUTE_TOLERANCE_MM = 1.0e-7
SYMMETRY_ANGULAR_SEGMENTS = 128
SYMMETRY_LENGTH_SEGMENTS = 16


@dataclass(frozen=True, slots=True)
class SymmetryResolution:
    quadrants: int
    xz: bool
    yz: bool
    reasons: dict[str, list[str]]
    tolerance_mm: float
    relative_tolerance: float = SYMMETRY_RELATIVE_TOLERANCE
    #: The sampled surface's mirror verdict per cut plane (``x0``, ``y0``), as
    #: ``PlaneSymmetryVerdict.to_dict()``; None when it was not sampled.
    #: Evidence for the solve record, not part of the wire answer.
    plane_verdicts: Mapping[str, Mapping[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The wire answer (``POST /api/design/symmetry``); no verdict evidence."""
        return {
            "quadrants": self.quadrants,
            "xz": self.xz,
            "yz": self.yz,
            "reasons": {plane: list(items) for plane, items in self.reasons.items()},
            "tolerance_mm": self.tolerance_mm,
            "relative_tolerance": self.relative_tolerance,
        }


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


def _failed_resolution(message: str) -> SymmetryResolution:
    return SymmetryResolution(
        quadrants=1234,
        xz=False,
        yz=False,
        reasons={"xz": [message], "yz": [message]},
        tolerance_mm=SYMMETRY_ABSOLUTE_TOLERANCE_MM,
    )


def resolution_was_sampled(resolution: SymmetryResolution) -> bool:
    """Whether the geometry was actually sampled, rather than defaulted to full.

    A native formula and a sampling failure resolve to the full domain through
    :func:`_failed_resolution`, which carries the absolute tolerance floor; a
    sampled design's tolerance is relative to its size and always above it.
    "Full" from those means "not evaluated", not "has no mirror planes".
    """

    return resolution.tolerance_mm > SYMMETRY_ABSOLUTE_TOLERANCE_MM


#: Resolutions keyed by the geometry that produced them. Small on purpose: the
#: point is to absorb the repeats -- the same design resolved on submit and
#: again on the next edit that did not touch geometry -- not to remember a
#: session's whole history.
_RESOLUTION_CACHE_SIZE = 32
_resolution_cache: "OrderedDict[str, SymmetryResolution]" = OrderedDict()
_resolution_cache_lock = threading.Lock()


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
        serialized = canonical_ascii_string_json(config)
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
                # A copy: a caller that edits its answer cannot change the cache.
                return copy.deepcopy(cached)

    resolution = _resolve_symmetry_uncached(design)
    if cache_key is not None:
        with _resolution_cache_lock:
            _resolution_cache[cache_key] = copy.deepcopy(resolution)
            _resolution_cache.move_to_end(cache_key)
            while len(_resolution_cache) > _RESOLUTION_CACHE_SIZE:
                _resolution_cache.popitem(last=False)
    return resolution


def _resolve_symmetry_uncached(design: DesignConfig | Mapping[str, Any]) -> SymmetryResolution:
    try:
        validated = (
            design if isinstance(design, DesignConfig) else DesignConfig.model_validate(design)
        )
        from server.design.native import NATIVE_FORMULAS

        if validated.formula in NATIVE_FORMULAS:
            return _failed_resolution(f"{validated.formula} requires the full circular mesh domain")
        config = retain_mesher_construction(design_to_mesher_config(validated))
        mesh = dict(config.get("mesh") or {})
        mesh.update(
            {
                "quadrants": 1234,
                "angularSegments": SYMMETRY_ANGULAR_SEGMENTS,
                "lengthSegments": SYMMETRY_LENGTH_SEGMENTS,
            }
        )
        config["mesh"] = mesh
        from hornlab_mesher.config_builder import (
            build_geometry_params,
            build_point_grid,
            enclosure_quadrant_resolutions,
        )

        params, _formula, mode = build_geometry_params(config)
        enclosure_resolutions: dict[str, list[float]] = {}
        if mode == "enclosure":
            enclosure_resolutions = enclosure_quadrant_resolutions(config)
        params["quadrants"] = "1234"
        params["angularSegments"] = SYMMETRY_ANGULAR_SEGMENTS
        params["lengthSegments"] = SYMMETRY_LENGTH_SEGMENTS
        grid = build_point_grid(params)
        n_phi = int(grid["grid_n_phi"])
        n_length = int(grid["grid_n_length"])
        grids = {
            name: surface
            for name, surface in (
                ("inner", _reshape_surface(grid.get("inner_points"), n_phi, n_length)),
                ("outer", _reshape_surface(grid.get("outer_points"), n_phi, n_length)),
            )
            if surface is not None
        }
        if not grids:
            return _failed_resolution("mesher point grid returned no surface points")
    except Exception as exc:
        return _failed_resolution(f"mesher symmetry sampling failed: {exc}")

    from hornlab_mesher.symmetry import (
        evaluate_enclosure_symmetry,
        evaluate_point_grid_symmetry,
    )

    # The mesher's one mirror check, exact class: each sampled ring against
    # its own mirror image, within 2e-4 of the grids' diagonal.
    verdicts = evaluate_point_grid_symmetry(grids, tolerance_class="exact")
    tolerance_mm = verdicts["y0"].tolerance
    reasons: dict[str, list[str]] = {"xz": [], "yz": []}
    for plane, cut_plane in (("xz", "y0"), ("yz", "x0")):
        verdict = verdicts[cut_plane]
        if not verdict.accepted:
            reasons[plane].append(
                f"sampled horn surface misses the {plane} mirror by up to "
                f"{verdict.worst_off_model_distance:.6g} mm (tolerance {tolerance_mm:.6g} mm)"
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
        # The same expanded quadrant sizes the mesher applies, the box's Dim
        # (the mesher refuses a reduced build whose Dim differs across a cut
        # plane) and the margins.
        enclosure = validated.root.enclosure
        box = evaluate_enclosure_symmetry(
            resolutions=enclosure_resolutions,
            dim_mm=enclosure.dim_mm if enclosure is not None else None,
            spacing_lr=_margin_pair(validated, "space_l", "space_r"),
            spacing_tb=_margin_pair(validated, "space_t", "space_b"),
            spacing_tolerance_mm=tolerance_mm,
        )
        reasons["xz"].extend(box["y0"])
        reasons["yz"].extend(box["x0"])

    xz = not reasons["xz"]
    yz = not reasons["yz"]
    quadrants = quadrants_for_symmetry_planes(xz=xz, yz=yz)
    return SymmetryResolution(
        quadrants=quadrants,
        xz=xz,
        yz=yz,
        reasons=reasons,
        tolerance_mm=tolerance_mm,
        plane_verdicts={plane: verdict.to_dict() for plane, verdict in verdicts.items()},
    )


def restrict_for_ground_plane(
    resolution: SymmetryResolution, ground_axis: str | None
) -> SymmetryResolution:
    """Drop the mirror plane a rigid ground plane makes unavailable.

    A reduced mesh is cut *on* its mirror plane and must touch it; a model
    standing above a ground plane must not reach it. The two cannot name the
    same plane, and lifting the model off the origin is what destroys the cut,
    so the restriction does not depend on the height.

    Applied here rather than inside ``resolve_symmetry`` because that result is
    memoized on the design alone and is also served to the design-symmetry
    endpoint, which knows nothing about solve options. Restricting afterwards
    keeps one geometry answer and makes the ground plane a separate, visible
    subtraction from it -- so ``auto`` degrades quarter to half_yz on a floor
    instead of the package rejecting the mesh after it has been built, and a
    forced conflicting mode fails naming the ground plane as the reason.
    """

    if ground_axis is None:
        return resolution
    from .ground_plane import GroundPlane

    blocked = GroundPlane(axis=ground_axis, height_m=0.0).blocked_symmetry_plane
    if blocked not in {"xz", "yz"}:
        # A z-axis ground plane blocks only the legacy "xy" bi-symmetry, which
        # WG never resolves and the package refuses anyway. Nothing to subtract.
        return resolution
    if not getattr(resolution, blocked):
        return resolution
    reason = (
        f"a rigid ground plane bounding {ground_axis} stands the model off "
        f"{ground_axis} = 0, so the {blocked} mirror plane is no longer a cut "
        "through the model"
    )
    reasons = {plane: list(items) for plane, items in resolution.reasons.items()}
    reasons.setdefault(blocked, []).append(reason)
    xz = resolution.xz and blocked != "xz"
    yz = resolution.yz and blocked != "yz"
    return SymmetryResolution(
        quadrants=quadrants_for_symmetry_planes(xz=xz, yz=yz),
        xz=xz,
        yz=yz,
        reasons=reasons,
        tolerance_mm=resolution.tolerance_mm,
        relative_tolerance=resolution.relative_tolerance,
        plane_verdicts=resolution.plane_verdicts,
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
    native_plane = PLANE_BY_QUADRANTS[SYMMETRY_MODE_QUADRANTS[normalized]]
    # Preserve the xz-before-yz ordering in forced-quarter refusal messages.
    required = tuple(sorted(native_plane.split("+"))) if native_plane is not None else ()
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
    "resolution_was_sampled",
    "resolve_symmetry",
    "restrict_for_ground_plane",
    "validate_symmetry_mode",
]
