"""Ordered legacy design migrations.

The FREEFORM transforms port the state normalization in v1
``src/config/freeformModel.js:90-143,231-238``.  Migrations operate on the
plain API-shaped payload immediately before Pydantic validation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from types import UnionType
from typing import Annotated, Any, Callable, Mapping, Union, get_args, get_origin

from pydantic import BaseModel

from .schema import (
    Expr,
    FreeformConfig,
    ICWConfig,
    OSSEConfig,
    ROSSEConfig,
)


Payload = dict[str, Any]


@dataclass(frozen=True)
class MigrationApplication:
    """One applied migration and the human-readable report note."""

    name: str
    note: str


@dataclass(frozen=True)
class Migration:
    """A versioned, ordered, independently testable state transform."""

    name: str
    applies_if: Callable[[Payload], bool]
    transform: Callable[[Payload], None]
    note: str


def _number(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("raw"))
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _finite_number(value: Any, location: str) -> float:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("raw"))
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{location} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{location} must be a finite number")
    return number


def _point_row(point: Any) -> list[float] | None:
    if isinstance(point, Mapping):
        raw = [point.get("z"), point.get("r")]
        angle = point.get("angle_deg", point.get("angleDeg"))
        strength = point.get("strength")
        if angle is not None:
            raw.append(angle)
        if strength is not None and angle is not None:
            raw.append(strength)
        return [_number(value) for value in raw]
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return [_number(value) for value in point[:4]]
    return None


def _profile_spec(payload: Payload, axis: str) -> dict[str, Any]:
    wire = payload.get(f"profile_{axis.lower()}")
    if isinstance(wire, Mapping):
        profile = dict(wire)
        points = profile.get("points")
        if isinstance(points, (list, tuple)):
            length = _number(payload.get("length"), 120.0)
            # Migration 001 samples physical meridians. Text-config imports now
            # reach it already normalized, so reconstruct z only in this
            # private curve view; the persisted points remain on the t axis.
            profile["points"] = [
                {**point, "z": _number(point.get("t")) * length}
                if isinstance(point, Mapping) and "z" not in point and "t" in point
                else point
                for point in points
            ]
        return profile
    legacy = payload.get(f"profile{axis}")
    length = _number(payload.get("length"), 120.0)
    throat = _number(payload.get("throatRadius"), 12.7)
    if isinstance(legacy, list) and len(legacy) >= 2:
        points = legacy
    else:
        interior = payload.get(f"interior{axis}")
        points = [
            [0.0, throat],
            *(interior if isinstance(interior, list) else []),
            [length, _number(payload.get(f"mouthRadius{axis}"), throat)],
        ]
    return {
        "points": points,
        "throat_angle_deg": payload.get("throatAngle", 15.5),
        "mouth_angle_deg": payload.get(f"mouthAngle{axis}", 60.0),
        "throat_tangent_scale": payload.get(f"throatTangentScale{axis}", 1.0),
        "mouth_tangent_scale": payload.get(f"mouthTangentScale{axis}", 1.0),
    }


def _endpoint_slope(h0: float, h1: float, delta0: float, delta1: float) -> float:
    def sign(value: float) -> int:
        return 1 if value > 0 else -1 if value < 0 else 0

    slope = ((2 * h0 + h1) * delta0 - h0 * delta1) / (h0 + h1)
    if sign(slope) != sign(delta0):
        return 0.0
    if sign(delta0) != sign(delta1) and abs(slope) > abs(3 * delta0):
        return 3 * delta0
    return slope


def _pchip_slopes(parameters: list[float], values: list[float]) -> list[float]:
    if len(values) == 2:
        slope = (values[1] - values[0]) / (parameters[1] - parameters[0])
        return [slope, slope]
    h = [right - left for left, right in zip(parameters, parameters[1:])]
    delta = [(right - left) / span for left, right, span in zip(values, values[1:], h)]
    slopes = [0.0] * len(values)
    slopes[0] = _endpoint_slope(h[0], h[1], delta[0], delta[1])
    slopes[-1] = _endpoint_slope(h[-1], h[-2], delta[-1], delta[-2])
    for index in range(1, len(values) - 1):
        before, after = delta[index - 1], delta[index]
        if before == 0 or after == 0 or (before > 0) != (after > 0):
            continue
        before_weight = 2 * h[index] + h[index - 1]
        after_weight = h[index] + 2 * h[index - 1]
        slopes[index] = (before_weight + after_weight) / (
            before_weight / before + after_weight / after
        )
    return slopes


def _hermite(value0: float, value1: float, slope0: float, slope1: float, span: float, u: float) -> float:
    u2, u3 = u * u, u * u * u
    return (
        (2 * u3 - 3 * u2 + 1) * value0
        + (u3 - 2 * u2 + u) * span * slope0
        + (-2 * u3 + 3 * u2) * value1
        + (u3 - u2) * span * slope1
    )


def _display_curve(profile: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Port ``buildFreeformDisplayCurve`` from v1 freeformCurve.js:97-183."""

    anchors = [row for value in profile.get("points", []) if (row := _point_row(value)) is not None]
    unique: list[list[float]] = []
    for anchor in anchors:
        if not unique or math.hypot(anchor[0] - unique[-1][0], anchor[1] - unique[-1][1]) > 1e-12:
            unique.append(anchor)
    if len(unique) < 2:
        return [(row[0], row[1]) for row in unique]
    parameters = [0.0]
    for left, right in zip(unique, unique[1:]):
        parameters.append(parameters[-1] + max(1e-12, math.hypot(right[0] - left[0], right[1] - left[1])))
    z_slopes = _pchip_slopes(parameters, [row[0] for row in unique])
    r_slopes = _pchip_slopes(parameters, [row[1] for row in unique])

    def override(index: int, angle: Any, scale: Any) -> None:
        speed = math.hypot(z_slopes[index], r_slopes[index]) * _number(scale, 1.0)
        radians = math.radians(_number(angle))
        z_slopes[index] = speed * math.cos(radians)
        r_slopes[index] = speed * math.sin(radians)

    if len(unique[0]) < 3:
        override(0, profile.get("throat_angle_deg", 0), profile.get("throat_tangent_scale", 1))
    if len(unique[-1]) < 3:
        override(-1, profile.get("mouth_angle_deg", 0), profile.get("mouth_tangent_scale", 1))
    for index, anchor in enumerate(unique):
        if len(anchor) < 3:
            continue
        speed = math.hypot(z_slopes[index], r_slopes[index]) * (anchor[3] if len(anchor) >= 4 else 1.0)
        radians = math.radians(anchor[2])
        z_slopes[index] = speed * math.cos(radians)
        r_slopes[index] = speed * math.sin(radians)

    samples: list[tuple[float, float]] = []
    total = parameters[-1]
    target_samples = max(len(unique), 256)
    for segment in range(len(unique) - 1):
        span = parameters[segment + 1] - parameters[segment]
        segment_samples = max(1, math.floor(((target_samples - 1) * span) / total + 0.5))
        for step in range(segment_samples):
            fraction = step / segment_samples
            samples.append(
                (
                    _hermite(unique[segment][0], unique[segment + 1][0], z_slopes[segment], z_slopes[segment + 1], span, fraction),
                    _hermite(unique[segment][1], unique[segment + 1][1], r_slopes[segment], r_slopes[segment + 1], span, fraction),
                )
            )
    samples.append((unique[-1][0], unique[-1][1]))
    return samples


def _radius_at(profile: Mapping[str, Any], t: float) -> float:
    """Interpolate the sampled chord-parameterized PCHIP/Hermite meridian."""

    curve = _display_curve(profile)
    if not curve:
        return 0.0
    z = curve[0][0] + min(1.0, max(0.0, t)) * (curve[-1][0] - curve[0][0])
    for left, right in zip(curve, curve[1:]):
        if min(left[0], right[0]) <= z <= max(left[0], right[0]):
            if right[0] == left[0]:
                return left[1]
            amount = (z - left[0]) / (right[0] - left[0])
            return left[1] + amount * (right[1] - left[1])
    return curve[-1][1]


def _legacy_corner_stations(payload: Payload) -> list[dict[str, Any]]:
    stations = payload.get("cross_sections", payload.get("crossSections", []))
    if not isinstance(stations, list):
        return []
    return [station for station in stations if isinstance(station, dict)]


def _has_corner_ratio(payload: Payload) -> bool:
    return any("cornerRatio" in item or "corner_ratio" in item for item in _legacy_corner_stations(payload))


def _migrate_corner_ratio(payload: Payload) -> None:
    horizontal = _profile_spec(payload, "H")
    vertical = _profile_spec(payload, "V")
    for station in _legacy_corner_stations(payload):
        legacy = station.pop("cornerRatio", station.pop("corner_ratio", None))
        if legacy is None or "corner_radius_mm" in station or "cornerRadiusMm" in station:
            continue
        ratio = _finite_number(legacy, "FREEFORM corner ratio")
        t = min(1.0, max(0.0, _number(station.get("t"))))
        radius_h = _radius_at(horizontal, t)
        radius_v = _radius_at(vertical, t)
        # V1 rounds the absolute millimetre radius to one decimal place.
        millimetres = ratio * min(radius_h, radius_v)
        if not math.isfinite(millimetres):
            raise ValueError("FREEFORM corner ratio produces a non-finite radius")
        tenths = millimetres * 10
        if not math.isfinite(tenths):
            raise ValueError("FREEFORM corner ratio is too large to round safely")
        station["corner_radius_mm"] = math.floor(tenths + 0.5) / 10


def _has_inflection_allow(payload: Payload) -> bool:
    value = payload.get("inflection_policy", payload.get("inflectionPolicy"))
    return str(value).strip().lower() == "allow"


def _migrate_inflection_allow(payload: Payload) -> None:
    if "inflection_policy" in payload:
        payload["inflection_policy"] = "warn"
    if "inflectionPolicy" in payload:
        payload["inflectionPolicy"] = "warn"


# "null" belongs here with the other two: v1's writer interpolates a JSON-null
# parameter as ``key = null`` (``${null}``), which ``Number()`` reads as NaN just
# like the other two tokens.  Without it a null would validate as an opaque
# raw expression with no value and reach the mesher as a non-finite coordinate.
_JS_ARTIFACT_TOKENS = frozenset({"undefined", "NaN", "null"})


def _numeric_paths_for_annotation(
    annotation: Any, prefix: tuple[str, ...]
) -> set[tuple[str, ...]]:
    if annotation is Expr:
        return {prefix}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        result: set[tuple[str, ...]] = set()
        for name, field_info in annotation.model_fields.items():
            result.update(
                _numeric_paths_for_annotation(field_info.annotation, (*prefix, name))
            )
        return result
    origin = get_origin(annotation)
    if origin is Annotated:
        return _numeric_paths_for_annotation(get_args(annotation)[0], prefix)
    if origin is list:
        return _numeric_paths_for_annotation(get_args(annotation)[0], (*prefix, "*"))
    if origin in {Union, UnionType}:
        result: set[tuple[str, ...]] = set()
        for choice in get_args(annotation):
            result.update(_numeric_paths_for_annotation(choice, prefix))
        return result
    return set()


# Migration 003 is intentionally derived from the typed schema. Unknown keys,
# passthrough mappings, and string-valued known fields are outside this set.
_KNOWN_NUMERIC_PATHS = frozenset(
    path
    for root_model in (OSSEConfig, ROSSEConfig, ICWConfig, FreeformConfig)
    for path in _numeric_paths_for_annotation(root_model, ())
)


def _is_js_undefined(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() in _JS_ARTIFACT_TOKENS
    if isinstance(value, Mapping):
        raw = value.get("raw")
        return isinstance(raw, str) and raw.strip() in _JS_ARTIFACT_TOKENS
    return False


def _is_known_numeric_path(path: tuple[str, ...]) -> bool:
    return any(
        len(candidate) == len(path)
        and all(expected == "*" or expected == actual for expected, actual in zip(candidate, path))
        for candidate in _KNOWN_NUMERIC_PATHS
    )


def _walk_has_js_undefined(node: Any, path: tuple[str, ...] = ()) -> bool:
    if isinstance(node, Mapping):
        return any(
            (_is_known_numeric_path((*path, str(key))) and _is_js_undefined(value))
            or _walk_has_js_undefined(value, (*path, str(key)))
            for key, value in node.items()
        )
    if isinstance(node, (list, tuple)):
        return any(_walk_has_js_undefined(item, (*path, "*")) for item in node)
    return False


def _has_js_undefined(payload: Payload) -> bool:
    return _walk_has_js_undefined(payload)


def _strip_js_undefined(node: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(node, dict):
        for key in [
            key
            for key, value in node.items()
            if _is_known_numeric_path((*path, str(key))) and _is_js_undefined(value)
        ]:
            del node[key]
        for key, value in node.items():
            _strip_js_undefined(value, (*path, str(key)))
    elif isinstance(node, list):
        for item in node:
            _strip_js_undefined(item, (*path, "*"))


def _migrate_js_undefined(payload: Payload) -> None:
    _strip_js_undefined(payload)


_REMOVED_FREEFORM_PROFILE_KEYS = frozenset(
    {"throat_tangent_scale", "mouth_tangent_scale"}
)


def _has_removed_freeform_controls(payload: Payload) -> bool:
    if "overshoot_policy" in payload:
        return True
    for profile_name in ("profile_h", "profile_v"):
        profile = payload.get(profile_name)
        if not isinstance(profile, Mapping):
            continue
        if any(key in profile for key in _REMOVED_FREEFORM_PROFILE_KEYS):
            return True
        points = profile.get("points")
        if isinstance(points, (list, tuple)) and any(
            (isinstance(point, Mapping) and "strength" in point)
            or (isinstance(point, (list, tuple)) and len(point) >= 4)
            for point in points
        ):
            return True
    stations = payload.get("cross_sections")
    return isinstance(stations, (list, tuple)) and any(
        isinstance(station, Mapping) and station.get("shape") == "circle"
        for station in stations
    )


def _migrate_removed_freeform_controls(payload: Payload) -> None:
    payload.pop("overshoot_policy", None)
    for profile_name in ("profile_h", "profile_v"):
        profile = payload.get(profile_name)
        if not isinstance(profile, dict):
            continue
        for key in _REMOVED_FREEFORM_PROFILE_KEYS:
            profile.pop(key, None)
        points = profile.get("points")
        if not isinstance(points, list):
            continue
        for index, point in enumerate(points):
            if isinstance(point, dict):
                point.pop("strength", None)
            elif isinstance(point, list) and len(point) >= 4:
                del point[3]
            elif isinstance(point, tuple) and len(point) >= 4:
                points[index] = [*point[:3], *point[4:]]
    stations = payload.get("cross_sections")
    if isinstance(stations, list):
        for station in stations:
            if isinstance(station, dict) and station.get("shape") == "circle":
                station["shape"] = "ellipse"


def _has_freeform_millimetre_axis(payload: Payload) -> bool:
    """Recognize the two persisted pre-005 FREEFORM point representations."""

    if payload.get("formula") != "FREEFORM":
        return False
    for profile_name in ("profile_h", "profile_v"):
        profile = payload.get(profile_name)
        if not isinstance(profile, Mapping):
            continue
        points = profile.get("points")
        if not isinstance(points, (list, tuple)):
            continue
        if any(isinstance(point, Mapping) and "z" in point for point in points):
            return True
        if "length" not in payload and any(
            isinstance(point, (list, tuple)) and len(point) >= 2 for point in points
        ):
            return True
    return False


def _scalar_number(value: Any) -> float | None:
    """Return a finite scalar without manufacturing a fallback geometry value."""

    try:
        number = Expr.model_validate(value).value
    except (OverflowError, TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _legacy_axis_point(point: Any) -> tuple[float, dict[str, Any]] | None:
    if isinstance(point, Mapping):
        z = _scalar_number(point.get("z"))
        if z is None:
            return None
        rewritten = dict(point)
        rewritten.pop("z", None)
        return z, rewritten
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        z = _scalar_number(point[0])
        if z is None:
            return None
        rewritten = {"r": point[1]}
        if len(point) >= 3:
            rewritten["angle_deg"] = point[2]
        return z, rewritten
    return None


def _migrate_freeform_normalized_axis(payload: Payload) -> None:
    """Store anchors in normalized design space while preserving every anchor.

    Both meridians deliberately use the horizontal profile's physical length.
    A mismatched vertical endpoint therefore remains visible to schema validation
    instead of being silently repaired as a separate coordinate system.
    """

    horizontal = payload.get("profile_h")
    if not isinstance(horizontal, Mapping):
        return
    horizontal_points = horizontal.get("points")
    if not isinstance(horizontal_points, (list, tuple)) or not horizontal_points:
        return
    last = _legacy_axis_point(horizontal_points[-1])
    if last is None or last[0] <= 0:
        return
    length = last[0]

    rewritten_profiles: dict[str, list[dict[str, Any]]] = {}
    for profile_name in ("profile_h", "profile_v"):
        profile = payload.get(profile_name)
        if not isinstance(profile, Mapping):
            return
        points = profile.get("points")
        if not isinstance(points, (list, tuple)):
            return
        rewritten: list[dict[str, Any]] = []
        for point in points:
            parsed = _legacy_axis_point(point)
            if parsed is None:
                return
            z, values = parsed
            rewritten.append({"t": z / length, **values})
        rewritten_profiles[profile_name] = rewritten

    payload["length"] = length
    for profile_name, points in rewritten_profiles.items():
        profile = payload[profile_name]
        assert isinstance(profile, dict)
        profile["points"] = points


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        name="001_corner_ratio_to_corner_grid",
        applies_if=_has_corner_ratio,
        transform=_migrate_corner_ratio,
        note=(
            "Converted FREEFORM cornerRatio/corner_ratio fractions to absolute "
            "corner_radius_mm values using the local H/V profile radius."
        ),
    ),
    Migration(
        name="002_inflection_allow_to_warn",
        applies_if=_has_inflection_allow,
        transform=_migrate_inflection_allow,
        note="Renamed the removed FREEFORM inflection policy alias 'allow' to 'warn'.",
    ),
    Migration(
        name="003_js_undefined_lines_dropped",
        applies_if=_has_js_undefined,
        transform=_migrate_js_undefined,
        note=(
            "Dropped 'key = undefined', 'key = NaN', and 'key = null' assignments (v1 JS exporter "
            "artifacts in old job snapshots); absent parameters fall back to family "
            "defaults, matching v1's absent-key behavior. Found live in real corpus "
            "snapshots (e.g. 260311_simulation_2: Throat.Diameter = NaN broke mesh "
            "builds with non-finite coordinates)."
        ),
    ),
    Migration(
        name="004_freeform_solved_tangent_contract",
        applies_if=_has_removed_freeform_controls,
        transform=_migrate_removed_freeform_controls,
        note=(
            "Removed obsolete FREEFORM tangent scales, per-anchor strengths, and "
            "overshoot policy, and normalized circle cross-section stations to ellipse."
        ),
    ),
    Migration(
        name="005_freeform_normalized_axis",
        applies_if=_has_freeform_millimetre_axis,
        transform=_migrate_freeform_normalized_axis,
        note=(
            "Normalized FREEFORM meridian anchors from absolute z millimetres to "
            "the shared t axis and moved length to the top-level design."
        ),
    ),
)


def apply_migrations(payload: Mapping[str, Any]) -> tuple[Payload, list[MigrationApplication]]:
    """Apply every matching migration in numeric order to a defensive copy."""

    migrated = deepcopy(dict(payload))
    applied: list[MigrationApplication] = []
    for migration in MIGRATIONS:
        if migration.applies_if(migrated):
            migration.transform(migrated)
            applied.append(MigrationApplication(migration.name, migration.note))
    return migrated, applied


__all__ = ["MIGRATIONS", "Migration", "MigrationApplication", "apply_migrations"]
