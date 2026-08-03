"""Ordered legacy design migrations.

The FREEFORM transforms port the state normalization in v1
``src/config/freeformModel.js:90-143,231-238``.  Migrations operate on the
plain API-shaped payload immediately before Pydantic validation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping


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
        return float(value)
    except (TypeError, ValueError):
        return fallback


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
        return dict(wire)
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
        t = min(1.0, max(0.0, _number(station.get("t"))))
        radius_h = _radius_at(horizontal, t)
        radius_v = _radius_at(vertical, t)
        # V1 rounds the absolute millimetre radius to one decimal place.
        millimetres = _number(legacy) * min(radius_h, radius_v)
        station["corner_radius_mm"] = math.floor(millimetres * 10 + 0.5) / 10


def _has_inflection_allow(payload: Payload) -> bool:
    value = payload.get("inflection_policy", payload.get("inflectionPolicy"))
    return str(value).strip().lower() == "allow"


def _migrate_inflection_allow(payload: Payload) -> None:
    if "inflection_policy" in payload:
        payload["inflection_policy"] = "warn"
    if "inflectionPolicy" in payload:
        payload["inflectionPolicy"] = "warn"


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
