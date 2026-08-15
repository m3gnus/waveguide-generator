"""Generate the managed Onshape datum feature from a ``wglink`` manifest.

The bundle remains authoritative.  This module only translates its numeric
datum catalogue into one deterministic FeatureScript feature.  Stable
sub-operation ids keep downstream references intact when the feature is
regenerated on a later send.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any


DATUM_FEATURE_TYPE = "wgManagedDatums"
DATUM_FEATURE_NAME = "WG Datums"
DATUM_FEATURE_PARAMETER = "materializeDatums"
DATUM_STUDIO_NAME = "WG Datums"
STD_VERSION = "3044"

_VARIABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_PLANE_KEYS = (
    "WG_THROAT_PLANE",
    "WG_MOUTH_PLANE",
    "WG_BAFFLE_PLANE",
    "WG_ENC_BACK_PLANE",
    "WG_GEOM_MIDPLANE_Y",
    "WG_SOLVER_CUT_PLANE_Y",
    "WG_SOLVER_CUT_PLANE_X",
)
_POLYLINE_KEYS = (
    "WG_MOUTH_OUTLINE_INNER",
    "WG_MOUTH_OUTLINE_OUTER",
    "WG_BAFFLE_OUTLINE_FACE",
    "WG_BAFFLE_OUTLINE_ENVELOPE",
)


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _vector(value: object, *, label: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError(f"{label} must contain three numbers")
    return tuple(_number(item, label=label) for item in value)  # type: ignore[return-value]


def _length(value: float) -> str:
    return f"{value:.6f} * millimeter"


def _scalar(value: float) -> str:
    return f"{value:.12g}"


def _parameter_table(manifest: Mapping[str, Any]) -> dict[str, tuple[str, float]]:
    entries = manifest.get("parameters") or []
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return {}
    fields = (
        "throat_dia",
        "vertical_offset",
        "enc_z_front",
        "enc_depth",
        "enc_x0",
        "enc_y0",
        "enc_w",
        "enc_h",
        "mouth_w",
        "mouth_h",
        "depth",
    )
    result: dict[str, tuple[str, float]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        raw = entry.get("value")
        if not isinstance(name, str) or _VARIABLE_NAME.fullmatch(name) is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        for field in fields:
            if not name.endswith("_" + field):
                continue
            # ``wg_demo_enc_depth`` also ends in ``_depth``.  Prefer the most
            # specific contract field instead of silently binding the mouth
            # plane to the enclosure depth.
            if field == "depth" and name.endswith("_enc_depth"):
                continue
            result[field] = (name, _number(raw, label=f"parameter {name}"))
            break
    return result


def _fallback(datums: Mapping[str, Any], key: str, axis: int, default: float = 0.0) -> float:
    datum = datums.get(key)
    if not isinstance(datum, Mapping):
        return default
    try:
        return _vector(datum.get("origin_mm"), label=f"{key}.origin_mm")[axis]
    except ValueError:
        return default


def _var(
    parameters: Mapping[str, tuple[str, float]], field: str, fallback: float
) -> tuple[str, float]:
    item = parameters.get(field)
    if item is None:
        return _length(fallback), fallback
    name, value = item
    return f'getVariable(context, "{name}", {_length(fallback)})', value


def _point_expression(
    point: tuple[float, float, float],
    *,
    vertical: str,
    vertical_base: float,
    z_anchor: str | None = None,
    z_base: float = 0.0,
    width: str | None = None,
    width_base: float = 0.0,
    height: str | None = None,
    height_base: float = 0.0,
    x_origin: str | None = None,
    x_origin_base: float = 0.0,
    y_origin: str | None = None,
    y_origin_base: float = 0.0,
) -> str:
    x, y, z = point
    if width and width_base != 0.0:
        if x_origin:
            x_expr = f"({x_origin} + {_scalar((x - x_origin_base) / width_base)} * {width})"
        else:
            x_expr = f"({_scalar(x / width_base)} * {width})"
    else:
        x_expr = _length(x)

    if height and height_base != 0.0:
        if y_origin:
            # ``enc_y0`` is already in the placed link-local frame; unlike an
            # unplaced point-ring coordinate it already includes the vertical
            # offset and must not receive it a second time.
            local_y = y - y_origin_base
            y_expr = f"({y_origin} + {_scalar(local_y / height_base)} * {height})"
        else:
            local_y = y - vertical_base
            y_expr = f"({vertical} + {_scalar(local_y / height_base)} * {height})"
    else:
        y_expr = f"({vertical} + {_length(y - vertical_base)})"

    z_expr = _length(z)
    if z_anchor is not None:
        z_expr = f"({z_anchor} + {_length(z - z_base)})"
    return f"vector({x_expr}, {y_expr}, {z_expr})"


def _named(operation: str, name: str) -> str:
    return f'''        setProperty(context, {{
                "entities" : qCreatedBy(id + "{operation}", EntityType.BODY),
                "propertyType" : PropertyType.NAME,
                "value" : "{name}"
        }});'''


def build_datum_featurescript(manifest: Mapping[str, Any]) -> str:
    """Return deterministic source for the bundle's complete datum catalogue."""

    datums = manifest.get("datums")
    if not isinstance(datums, Mapping):
        raise ValueError("wglink.json has no datum catalogue")
    axis = datums.get("WG_AXIS")
    if not isinstance(axis, Mapping):
        raise ValueError("wglink.json has no WG_AXIS datum")
    axis_origin = _vector(axis.get("origin_mm"), label="WG_AXIS.origin_mm")
    axis_direction = _vector(axis.get("direction"), label="WG_AXIS.direction")
    if math.sqrt(sum(value * value for value in axis_direction)) <= 1.0e-12:
        raise ValueError("WG_AXIS.direction must be non-zero")

    parameters = _parameter_table(manifest)
    vertical_base = parameters.get("vertical_offset", ("", axis_origin[1]))[1]
    vertical, _ = _var(parameters, "vertical_offset", vertical_base)
    depth_base = parameters.get("depth", ("", _fallback(datums, "WG_MOUTH_PLANE", 2, 100.0)))[1]
    depth, _ = _var(parameters, "depth", depth_base)
    z_front_base = parameters.get(
        "enc_z_front", ("", _fallback(datums, "WG_BAFFLE_PLANE", 2, depth_base))
    )[1]
    z_front, _ = _var(parameters, "enc_z_front", z_front_base)
    enc_depth_base = parameters.get("enc_depth", ("", 0.0))[1]
    enc_depth, _ = _var(parameters, "enc_depth", enc_depth_base)
    mouth_w_base = parameters.get("mouth_w", ("", 1.0))[1] or 1.0
    mouth_h_base = parameters.get("mouth_h", ("", 1.0))[1] or 1.0
    mouth_w, _ = _var(parameters, "mouth_w", mouth_w_base)
    mouth_h, _ = _var(parameters, "mouth_h", mouth_h_base)
    enc_x0_base = parameters.get("enc_x0", ("", 0.0))[1]
    enc_y0_base = parameters.get("enc_y0", ("", 0.0))[1]
    enc_w_base = parameters.get("enc_w", ("", 1.0))[1] or 1.0
    enc_h_base = parameters.get("enc_h", ("", 1.0))[1] or 1.0
    enc_x0, _ = _var(parameters, "enc_x0", enc_x0_base)
    enc_y0, _ = _var(parameters, "enc_y0", enc_y0_base)
    enc_w, _ = _var(parameters, "enc_w", enc_w_base)
    enc_h, _ = _var(parameters, "enc_h", enc_h_base)

    required_features = manifest.get("required_features")
    interface = manifest.get("interface")
    interface_sources = (
        interface.get("sources") if isinstance(interface, Mapping) else None
    )
    materialize_source = (
        isinstance(required_features, Sequence)
        and not isinstance(required_features, (str, bytes))
        and "source-interface-v1" in required_features
        and isinstance(interface_sources, Sequence)
        and not isinstance(interface_sources, (str, bytes))
        and len(interface_sources) == 1
    )

    body: list[str] = []
    for key in _PLANE_KEYS:
        datum = datums.get(key)
        if not isinstance(datum, Mapping):
            continue
        origin = _vector(datum.get("origin_mm"), label=f"{key}.origin_mm")
        normal = _vector(datum.get("normal"), label=f"{key}.normal")
        if key == "WG_ENC_BACK_PLANE":
            origin_expr = f"vector({_length(origin[0])}, ({vertical} + {_length(origin[1] - vertical_base)}), ({z_front} - {enc_depth}))"
        elif key == "WG_BAFFLE_PLANE":
            origin_expr = _point_expression(
                origin,
                vertical=vertical,
                vertical_base=vertical_base,
                z_anchor=z_front,
                z_base=z_front_base,
            )
        elif key == "WG_MOUTH_PLANE":
            anchor = z_front if "enc_z_front" in parameters else depth
            base = z_front_base if "enc_z_front" in parameters else depth_base
            origin_expr = _point_expression(
                origin,
                vertical=vertical,
                vertical_base=vertical_base,
                z_anchor=anchor,
                z_base=base,
            )
        elif key == "WG_GEOM_MIDPLANE_Y":
            origin_expr = f"vector({_length(origin[0])}, {vertical}, {_length(origin[2])})"
        elif key in {"WG_SOLVER_CUT_PLANE_Y", "WG_SOLVER_CUT_PLANE_X"}:
            origin_expr = (
                f"vector({_length(origin[0])}, {_length(origin[1])}, {_length(origin[2])})"
            )
        else:
            origin_expr = _point_expression(origin, vertical=vertical, vertical_base=vertical_base)
        operation = key
        body.append(
            f'''        opPlane(context, id + "{operation}", {{
                "plane" : plane({origin_expr}, vector({_scalar(normal[0])}, {_scalar(normal[1])}, {_scalar(normal[2])})),
                "width" : {mouth_w},
                "height" : {mouth_h}
        }});
{_named(operation, key)}'''
        )

    if materialize_source:
        throat = datums.get("WG_THROAT_PLANE")
        if not isinstance(throat, Mapping):
            raise ValueError("source-interface-v1 requires WG_THROAT_PLANE")
        throat_parameter = parameters.get("throat_dia")
        if throat_parameter is None:
            raise ValueError("source-interface-v1 requires a managed throat_dia parameter")
        throat_origin = _vector(
            throat.get("origin_mm"), label="WG_THROAT_PLANE.origin_mm"
        )
        throat_normal = _vector(
            throat.get("normal"), label="WG_THROAT_PLANE.normal"
        )
        throat_origin_expr = _point_expression(
            throat_origin,
            vertical=vertical,
            vertical_base=vertical_base,
        )
        throat_dia, _ = _var(parameters, "throat_dia", throat_parameter[1])
        body.append(
            f'''        // Real sheet body exported with the Part Studio; the
        // construction throat plane alone is intentionally not source evidence.
        var wgThroatSourceSketchId = id + "WG_THROAT_SOURCE_SKETCH";
        var wgThroatSourceSketch = newSketchOnPlane(context, wgThroatSourceSketchId, {{
                "sketchPlane" : plane({throat_origin_expr}, vector({_scalar(throat_normal[0])}, {_scalar(throat_normal[1])}, {_scalar(throat_normal[2])}))
        }});
        skCircle(wgThroatSourceSketch, "boundary", {{
                "center" : vector(0 * millimeter, 0 * millimeter),
                "radius" : {throat_dia} / 2
        }});
        skSolve(wgThroatSourceSketch);
        opFillSurface(context, id + "WG_THROAT_SOURCE", {{
                // Select the sketch curve semantically. The closed sketch also
                // creates a coincident sheet-region edge, which is not a wire.
                "edgesG0" : qBodyType(
                        qCreatedBy(wgThroatSourceSketchId, EntityType.EDGE),
                        BodyType.WIRE
                ),
                "edgesG1" : qNothing(),
                "edgesG2" : qNothing(),
                "guideVertices" : qNothing()
        }});
{_named("WG_THROAT_SOURCE", "WG_THROAT_SOURCE")}'''
        )

    axis_origin_expr = _point_expression(
        axis_origin, vertical=vertical, vertical_base=vertical_base
    )
    direction_expr = ", ".join(_scalar(value) for value in axis_direction)
    body.append(
        f"""        var wgAxisOrigin = {axis_origin_expr};
        var wgAxisDirection = normalize(vector({direction_expr}));
        var wgAxisReference = abs(wgAxisDirection[2]) < 0.9 ? vector(0, 0, 1) : vector(1, 0, 0);
        var wgAxisPlane = plane(wgAxisOrigin, cross(wgAxisDirection, wgAxisReference), wgAxisDirection);
        var wgAxisSketch = newSketchOnPlane(context, id + "WG_AXIS", {{ "sketchPlane" : wgAxisPlane }});
        skLineSegment(wgAxisSketch, "axis", {{
                "start" : vector(-25 * millimeter, 0 * millimeter),
                "end" : vector({depth} + 25 * millimeter, 0 * millimeter),
                "construction" : true
        }});
        skSolve(wgAxisSketch);
{_named("WG_AXIS", "WG_AXIS")}"""
    )

    for key in _POLYLINE_KEYS:
        datum = datums.get(key)
        if not isinstance(datum, Mapping):
            continue
        raw_points = datum.get("points_mm")
        if (
            not isinstance(raw_points, Sequence)
            or isinstance(raw_points, (str, bytes))
            or len(raw_points) < 2
        ):
            raise ValueError(f"{key}.points_mm must contain at least two points")
        points = [_vector(point, label=f"{key}.points_mm") for point in raw_points]
        if points[0] != points[-1]:
            points.append(points[0])
        is_baffle = key.startswith("WG_BAFFLE_") or (
            key == "WG_MOUTH_OUTLINE_OUTER" and "enc_w" in parameters
        )
        anchor = z_front if "enc_z_front" in parameters else depth
        z_base = z_front_base if "enc_z_front" in parameters else depth_base
        expressions = []
        for point in points:
            expressions.append(
                _point_expression(
                    point,
                    vertical=vertical,
                    vertical_base=vertical_base,
                    z_anchor=anchor,
                    z_base=z_base,
                    width=enc_w if is_baffle else mouth_w,
                    width_base=enc_w_base if is_baffle else mouth_w_base,
                    height=enc_h if is_baffle else mouth_h,
                    height_base=enc_h_base if is_baffle else mouth_h_base,
                    x_origin=enc_x0 if is_baffle else None,
                    x_origin_base=enc_x0_base if is_baffle else 0.0,
                    y_origin=enc_y0 if is_baffle else None,
                    y_origin_base=enc_y0_base if is_baffle else 0.0,
                )
            )
        rendered = ",\n                ".join(expressions)
        body.append(
            f'''        opPolyline(context, id + "{key}", {{
                "points" : [
                {rendered}
                ]
        }});
{_named(key, key)}'''
        )

    return f'''FeatureScript {STD_VERSION};
import(path : "onshape/std/geometry.fs", version : "{STD_VERSION}.0");

// Generated from wglink datum_schema=1.  The numeric point samples are the
// contract evidence; interface dimensions and placement come from the linked
// WG Variable Studio so user references regenerate instead of being replaced.
annotation {{ "Feature Type Name" : "WG Managed Datums" }}
export const {DATUM_FEATURE_TYPE} = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
        annotation {{ "Name" : "Materialize WG datums" }}
        definition.{DATUM_FEATURE_PARAMETER} is boolean;
    }}
    {{
        if (!definition.{DATUM_FEATURE_PARAMETER})
        {{
            return;
        }}

{chr(10).join(body)}
    }});
'''


__all__ = [
    "DATUM_FEATURE_NAME",
    "DATUM_FEATURE_PARAMETER",
    "DATUM_FEATURE_TYPE",
    "DATUM_STUDIO_NAME",
    "STD_VERSION",
    "build_datum_featurescript",
]
