"""Translate v2 design models into the HornLab mesher's public config."""

from __future__ import annotations

import math
from typing import Any, Mapping

from server.design.schema import (
    DesignConfig,
    Expr,
    FreeformConfig,
    FreeformProfile,
    ICWConfig,
    OSSEConfig,
    ROSSEConfig,
)


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _expr(value: Expr | None) -> str | float | None:
    if value is None:
        return None
    if value.raw is not None:
        return value.raw
    return value.value


def _number(value: Expr | None, fallback: float = 0.0) -> float:
    if value is None or value.value is None or not math.isfinite(value.value):
        return fallback
    return float(value.value)


def _first_number(value: Expr | None) -> float | None:
    if value is None:
        return None
    if value.value is not None:
        return float(value.value)
    for item in (value.raw or "").split(","):
        try:
            number = float(item.strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _source_shape(value: Expr | None) -> str | float | None:
    translated = _expr(value)
    numeric = value.value if value is not None else None
    if numeric is not None and numeric.is_integer() and int(numeric) == 2:
        # V1: 2 is a flat disc. The mesher uses 0 for the same shape.
        return 0.0
    return translated


def _profile_points(profile: FreeformProfile) -> list[list[str | float]]:
    rows: list[list[str | float]] = []
    for point in profile.points:
        row = [_expr(point.z), _expr(point.r)]
        if point.angle_deg is not None:
            row.append(_expr(point.angle_deg))
            if point.strength is not None:
                row.append(_expr(point.strength))
        rows.append([item for item in row if item is not None])
    return rows  # type: ignore[return-value]


def _freeform_profile(profile: FreeformProfile) -> dict[str, Any]:
    return _clean(
        {
            "points": _profile_points(profile),
            "throatAngleDeg": _expr(profile.throat_angle_deg),
            "mouthAngleDeg": _expr(profile.mouth_angle_deg),
            "throatTangentScale": _expr(profile.throat_tangent_scale),
            "mouthTangentScale": _expr(profile.mouth_tangent_scale),
        }
    )


def _profile(design: OSSEConfig | ROSSEConfig | ICWConfig | FreeformConfig) -> dict[str, Any]:
    """Port v1 ``mesher_adapter.py:200-310`` from typed v2 fields."""

    if isinstance(design, FreeformConfig):
        stations = [
            _clean(
                {
                    "t": _expr(station.t),
                    "shape": station.shape,
                    "exponent": _expr(station.exponent),
                    "cornerRadiusMm": _expr(station.corner_radius_mm),
                }
            )
            for station in design.cross_sections
        ]
        return _clean(
            {
                "formula": "FREEFORM",
                "profileH": _freeform_profile(design.profile_h),
                "profileV": _freeform_profile(design.profile_v),
                "crossSections": stations,
                "overshootPolicy": design.overshoot_policy,
                "inflectionPolicy": design.inflection_policy,
            }
        )

    profile = _clean(
        {
            "formula": design.formula,
            "r0": _expr(design.r0),
            "a": _expr(design.a),
            "a0": _expr(design.a0),
            "k": _expr(design.k),
            "q": _expr(design.q),
            "throatExtLength": _expr(design.throat_ext_length),
            "throatExtAngle": _expr(design.throat_ext_angle),
            "slotLength": _expr(design.slot_length),
            "_athLengthMode": design.length_mode,
        }
    )
    if isinstance(design, OSSEConfig):
        profile.update(
            _clean(
                {
                    "L": _expr(design.L),
                    "n": _expr(design.n),
                    "s": _expr(design.s),
                    "h": _expr(design.h),
                    "rot": _expr(design.rotation),
                    "throatProfile": _expr(design.throat_profile),
                    "circArcRadius": _expr(design.circ_arc_radius),
                    "circArcTermAngle": _expr(design.circ_arc_term_angle),
                }
            )
        )
    elif isinstance(design, ROSSEConfig):
        profile.update(
            _clean(
                {
                    "R": _expr(design.R),
                    "r": _expr(design.r),
                    "b": _expr(design.b),
                    "m": _expr(design.m),
                    "tmax": _expr(design.tmax),
                }
            )
        )
    else:
        coverage = _number(design.coverage_angle)
        profile.update(
            _clean(
                {
                    "L": _expr(design.L),
                    "R": _expr(design.R),
                    "termination": design.termination,
                    "n_coeff": _expr(design.n_coeff),
                    "theta1_deg": _expr(design.theta1_deg),
                    "depth": _expr(design.depth),
                    "coverage_angle": _expr(design.coverage_angle) if coverage > 0 else None,
                    "hold_start": _expr(design.hold_start) if coverage > 0 else None,
                    "hold_end": _expr(design.hold_end) if coverage > 0 else None,
                }
            )
        )
    return profile


def design_to_mesher_config(design: DesignConfig) -> dict[str, Any]:
    """Translate a v2 design, mirroring v1 ``mesher_adapter.py:190-424``.

    The key families, source conversion, and enclosure/IB/freestanding mode
    precedence deliberately match that adapter. Preview is different in one
    documented respect: it always requests all four quadrants so the viewport
    renders the whole device rather than a solver symmetry domain.
    """

    if not isinstance(design, DesignConfig):
        raise TypeError("design must be a DesignConfig")
    root = design.root
    if root.source.contours is not None and root.source.contours.strip():
        raise ValueError("source.contours is not supported by the HornLab mesher preview")

    enclosure_depth = _number(root.enclosure.depth) if root.enclosure is not None else 0.0
    wall_thickness = _number(root.mesh.wall_thickness)
    if enclosure_depth > 0.0:
        mode = "enclosure"
    elif root.simulation.sim_type == "infinite-baffle":
        mode = "infinite-baffle"
    elif wall_thickness > 0.0:
        mode = "freestanding"
    else:
        mode = "bare"

    mesh = root.mesh
    morph = root.morph
    config: dict[str, Any] = {
        "formula": root.formula,
        "mode": mode,
        "profile": _profile(root),
        "mesh": _clean(
            {
                "angularSegments": _expr(mesh.angular_segments),
                "lengthSegments": _expr(mesh.length_segments),
                "cornerSegments": _expr(mesh.corner_segments),
                "samplingMode": mesh.sampling_mode,
                # The mesher's config parser treats a PRESENT ZMapPoints key as
                # "zmap mode requested" (hornlab_mesher/config_parser.py:289-292)
                # and rejects empty points — so a blank value must OMIT the key.
                "zMapPoints": mesh.z_map_points or None,
                "quadrants": 1234,
                "wallThickness": _expr(mesh.wall_thickness),
                "throatResolution": _expr(mesh.throat_resolution),
                "mouthResolution": _expr(mesh.mouth_resolution),
                "rearResolution": _expr(mesh.rear_resolution),
                "apertureResolutionScale": _expr(mesh.aperture_resolution_scale),
                "maxTriangles": _expr(mesh.max_triangles),
                "allowLargeMesh": _expr(mesh.allow_large_mesh),
                "encFrontResolution": (
                    _first_number(root.enclosure.front_resolution)
                    if root.enclosure is not None
                    else None
                ),
                "encBackResolution": (
                    _first_number(root.enclosure.back_resolution)
                    if root.enclosure is not None
                    else None
                ),
                "verticalOffset": _expr(mesh.vertical_offset),
                "scaleToMetres": True,
            }
        ),
        "cross_section": {"exponent": 2.0, "aspectRatio": 1.0},
        "morph": _clean(
            {
                "morphTarget": _expr(morph.target_shape),
                "morphWidth": _expr(morph.target_width),
                "morphHeight": _expr(morph.target_height),
                "morphCorner": _expr(morph.corner_radius),
                "morphRate": _expr(morph.rate),
                "morphFixed": _expr(morph.fixed_part),
                "morphAllowShrinkage": _expr(morph.allow_shrinkage),
            }
        ),
        "gcurve": {},
        "source": _clean(
            {
                "sourceShape": _source_shape(root.source.shape),
                "sourceRadius": _expr(root.source.radius),
                "sourceCurv": _expr(root.source.curvature),
            }
        ),
    }
    if isinstance(root, OSSEConfig):
        curve = root.guiding_curve
        config["gcurve"] = _clean(
            {
                "gcurveType": _expr(curve.curve_type),
                "gcurveWidth": _expr(curve.width),
                "gcurveAspectRatio": _expr(curve.aspect_ratio),
                "gcurveDist": _expr(curve.distance),
                "gcurveRot": _expr(curve.rotation),
                "gcurveSf": _expr(curve.superformula),
                "gcurveSeN": _expr(curve.superellipse_n),
                "gcurveSfA": _expr(curve.sf_a),
                "gcurveSfB": _expr(curve.sf_b),
                "gcurveSfM1": _expr(curve.sf_m1),
                "gcurveSfM2": _expr(curve.sf_m2),
                "gcurveSfN1": _expr(curve.sf_n1),
                "gcurveSfN2": _expr(curve.sf_n2),
                "gcurveSfN3": _expr(curve.sf_n3),
            }
        )

    if enclosure_depth > 0.0 and root.enclosure is not None:
        enclosure = root.enclosure
        config["enclosure"] = _clean(
            {
                "depth": enclosure_depth,
                "space_l": _expr(enclosure.space_l),
                "space_t": _expr(enclosure.space_t),
                "space_r": _expr(enclosure.space_r),
                "space_b": _expr(enclosure.space_b),
                "edge": _expr(enclosure.edge_radius),
                "edgeType": _expr(enclosure.edge_type),
                "frontMeshSize": _first_number(enclosure.front_resolution),
                "backMeshSize": _first_number(enclosure.back_resolution),
            }
        )
    return config


# Explicit alias for callers that prefer the source/target type names.
design_config_to_mesher_config = design_to_mesher_config


__all__ = ["design_config_to_mesher_config", "design_to_mesher_config"]
