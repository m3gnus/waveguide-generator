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
    ResolutionExpr,
)


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _expr(value: Expr | None) -> str | float | None:
    if value is None:
        return None
    return value.execution_text()


def _structural_number(value: Expr | None, field: str, fallback: float = 0.0) -> float:
    if value is None:
        return fallback
    number = value.constant_value()
    if number is None:
        raise ValueError(f"{field} must be a scalar expression")
    return float(number)


def _scale_factor(value: Expr | None) -> float:
    scale = _structural_number(value, "scale", 1.0)
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    return scale


def _scaled_expr(value: Expr | None, scale: float) -> str | float | None:
    """Apply v1's waveguide-only Scale transform to one physical length."""

    translated = _expr(value)
    if translated is None or scale == 1.0:
        return translated
    if value is not None and value.raw is not None:
        execution = value.execution_text()
        assert isinstance(execution, str)
        try:
            numeric = float(execution.strip())
        except (OverflowError, ValueError):
            return f"({execution}) * {scale:.15g}"
        if math.isfinite(numeric):
            return numeric * scale
        raise ValueError("scaled length must be finite")
    assert isinstance(translated, (int, float))
    result = float(translated) * scale
    if not math.isfinite(result):
        raise ValueError("scaled length must be finite")
    return result


def _first_number(value: ResolutionExpr | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        value = value[0]
    number = value.constant_value()
    if number is not None:
        return float(number)
    for item in (value.raw or "").split(","):
        try:
            number = float(item.strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _source_shape(value: Expr | None) -> str | float | None:
    numeric = value.constant_value() if value is not None else None
    if value is not None and numeric is None:
        raise ValueError("source.shape must be a scalar expression")
    if numeric is not None and numeric.is_integer() and int(numeric) == 2:
        # V1: 2 is a flat disc. The mesher uses 0 for the same shape.
        return 0.0
    return numeric


def _profile_points(
    profile: FreeformProfile, length: Expr, scale: float
) -> list[list[str | float]]:
    length_value = _structural_number(length, "FREEFORM length")
    rows: list[list[str | float]] = []
    for index, point in enumerate(profile.points):
        t = _structural_number(point.t, f"FREEFORM point {index + 1} t")
        row = [t * length_value * scale, _scaled_expr(point.r, scale)]
        if point.angle_deg is not None:
            row.append(_expr(point.angle_deg))
        rows.append([item for item in row if item is not None])
    return rows  # type: ignore[return-value]


def _freeform_profile(
    profile: FreeformProfile, length: Expr, scale: float
) -> dict[str, Any]:
    return _clean(
        {
            "points": _profile_points(profile, length, scale),
            "throatAngleDeg": _expr(profile.throat_angle_deg),
            "mouthAngleDeg": _expr(profile.mouth_angle_deg),
        }
    )


def _profile(
    design: OSSEConfig | ROSSEConfig | ICWConfig | FreeformConfig, scale: float
) -> dict[str, Any]:
    """Port v1 ``mesher_adapter.py:200-310`` from typed v2 fields."""

    if isinstance(design, FreeformConfig):
        if design.corner_grids or any(
            station.corner_grid is not None for station in design.cross_sections
        ):
            raise ValueError("FREEFORM corner_grid data cannot be translated by the HornLab mesher")
        stations = [
            _clean(
                {
                    "t": _expr(station.t),
                    "shape": station.shape,
                    "exponent": _expr(station.exponent),
                    "cornerRadiusMm": _scaled_expr(station.corner_radius_mm, scale),
                }
            )
            for station in design.cross_sections
        ]
        return _clean(
            {
                "formula": "FREEFORM",
                "profileH": _freeform_profile(design.profile_h, design.length, scale),
                "profileV": _freeform_profile(design.profile_v, design.length, scale),
                "crossSections": stations,
                "inflectionPolicy": design.inflection_policy,
            }
        )

    profile = _clean(
        {
            "formula": design.formula,
            "r0": _scaled_expr(design.r0, scale),
            "a": _expr(design.a),
            "a0": _expr(design.a0),
            "k": _expr(design.k),
            "q": _expr(design.q),
            "throatExtLength": _scaled_expr(design.throat_ext_length, scale),
            "throatExtAngle": _expr(design.throat_ext_angle),
            "slotLength": _scaled_expr(design.slot_length, scale),
            "_athLengthMode": design.length_mode,
        }
    )
    if isinstance(design, OSSEConfig):
        profile.update(
            _clean(
                {
                    "L": _scaled_expr(design.L, scale),
                    "n": _expr(design.n),
                    "s": _expr(design.s),
                    "h": _expr(design.h),
                    "rot": _expr(design.rotation),
                    "throatProfile": _expr(design.throat_profile),
                    "circArcRadius": _scaled_expr(design.circ_arc_radius, scale),
                    "circArcTermAngle": _expr(design.circ_arc_term_angle),
                }
            )
        )
    elif isinstance(design, ROSSEConfig):
        profile.update(
            _clean(
                {
                    "R": _scaled_expr(design.R, scale),
                    "r": _expr(design.r),
                    "b": _expr(design.b),
                    "m": _expr(design.m),
                    "tmax": _expr(design.tmax),
                }
            )
        )
    else:
        coverage = _structural_number(design.coverage_angle, "coverage_angle")
        profile.update(
            _clean(
                {
                    "L": _scaled_expr(design.L, scale),
                    "R": _scaled_expr(design.R, scale),
                    "termination": design.termination,
                    "n_coeff": _expr(design.n_coeff),
                    "theta1_deg": _expr(design.theta1_deg),
                    "depth": _scaled_expr(design.depth, scale),
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

    scale = _scale_factor(root.scale)
    enclosure_depth = (
        _structural_number(root.enclosure.depth, "enclosure.depth")
        if root.enclosure is not None
        else 0.0
    )
    # ATH treats an omitted wall thickness as 5 mm for normal simulations and
    # 0 mm for infinite-baffle simulations. Keep the document field nullable
    # for lossless CFG round-trips and apply that default only at translation.
    default_wall_thickness = (
        0.0 if root.simulation.sim_type == "infinite-baffle" else 5.0
    )
    wall_thickness = (
        _structural_number(
            root.mesh.wall_thickness,
            "mesh.wall_thickness",
            default_wall_thickness,
        )
        * scale
    )
    if root.mesh.quadrants is not None:
        _structural_number(root.mesh.quadrants, "mesh.quadrants")
    # Inactive enclosure values remain valid preconfiguration. Simulation mode
    # decides whether they are active, matching v1's editable-at-depth-zero and
    # preconfigured-enclosure behavior.
    if root.simulation.sim_type == "infinite-baffle":
        mode = "infinite-baffle"
    elif enclosure_depth > 0.0:
        mode = "enclosure"
    elif wall_thickness > 0.0:
        mode = "freestanding"
    else:
        mode = "bare"

    mesh = root.mesh
    morph = root.morph
    config: dict[str, Any] = {
        "formula": root.formula,
        "mode": mode,
        "profile": _profile(root, scale),
        "mesh": _clean(
            {
                "angularSegments": _expr(mesh.angular_segments),
                "lengthSegments": _expr(mesh.length_segments),
                "cornerSegments": _expr(mesh.corner_segments),
                "samplingMode": mesh.sampling_mode,
                # The mesher's config parser treats a PRESENT ZMapPoints key as
                # "zmap mode requested" (hornlab_mesher/config_parser.py:289-292)
                # and rejects empty points — so a blank value must OMIT the key.
                "zMapPoints": mesh.z_map_points.strip() or None if mesh.z_map_points is not None else None,
                "quadrants": 1234,
                "wallThickness": (
                    wall_thickness
                    if mesh.wall_thickness is None
                    else _scaled_expr(mesh.wall_thickness, scale)
                ),
                "throatResolution": _scaled_expr(mesh.throat_resolution, scale),
                "mouthResolution": _scaled_expr(mesh.mouth_resolution, scale),
                "rearResolution": _scaled_expr(mesh.rear_resolution, scale),
                "apertureResolutionScale": _expr(mesh.aperture_resolution_scale),
                "maxTriangles": _expr(mesh.max_triangles),
                "allowLargeMesh": _expr(mesh.allow_large_mesh),
                "maxEdge": _scaled_expr(mesh.max_edge, scale),
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
                "verticalOffset": _scaled_expr(mesh.vertical_offset, scale),
                "scaleToMetres": True,
            }
        ),
        "cross_section": {"exponent": 2.0, "aspectRatio": 1.0},
        "morph": _clean(
            {
                "morphTarget": _expr(morph.target_shape),
                # ATH and profile_sampling.py define absent/zero target extents
                # as implicit extents derived from the waveguide mouth.
                "morphWidth": _scaled_expr(morph.target_width, scale),
                "morphHeight": _scaled_expr(morph.target_height, scale),
                "morphCorner": _scaled_expr(morph.corner_radius, scale),
                "morphRate": _expr(morph.rate),
                "morphFixed": _expr(morph.fixed_part),
                "morphAllowShrinkage": _expr(morph.allow_shrinkage),
            }
        ),
        "gcurve": {},
        "source": _clean(
            {
                "sourceShape": _source_shape(root.source.shape),
                "sourceRadius": _scaled_expr(root.source.radius, scale),
                "sourceCurv": _expr(root.source.curvature),
            }
        ),
    }
    if isinstance(root, OSSEConfig):
        curve = root.guiding_curve
        config["gcurve"] = _clean(
            {
                "gcurveType": _expr(curve.curve_type),
                "gcurveWidth": _scaled_expr(curve.width, scale),
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

    if mode == "enclosure" and root.enclosure is not None:
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
