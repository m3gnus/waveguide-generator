"""WG boundary for the mesher throat-stretch contract (mesher 0.2.4).

Keep refusal reasons in sync with hornlab_mesher.throat_stretch. This module
also validates saved designs when the installed mesher predates the feature.
"""
from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping


def coefficient(value: Any, key: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(
            f"per-azimuth throat stretch is not supported yet; {key} must be a plain finite number"
        )
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"throat stretch {key} must be finite and >= 0 and <= 10") from exc
    if math.isfinite(number) and number < 0:
        raise ValueError(
            f"throat stretch {key} must not be negative, got {value!r}: negative values "
            "are not supported (ATH accepts them, but they fold the profile back "
            "through the throat for all but very small magnitudes)"
        )
    if not math.isfinite(number) or not 0 <= number <= 10:
        raise ValueError(f"throat stretch {key} must be finite and >= 0 and <= 10, got {value!r}")
    return number


def text_number(value: str) -> Any:
    # Mirror the mesher ATH importer's _maybe_number, including nonfinite text.
    try:
        number = float(value.strip())
    except ValueError:
        return value.strip()
    if not math.isfinite(number):
        return value.strip()
    return int(number) if number.is_integer() else number


PROFILE_COMPOSITION = {
    "slot_length": "Slot.Length", "rotation": "Rot",
    "throat_ext_length": "Throat.Ext.Length", "throat_ext_angle": "Throat.Ext.Angle",
}
GUIDE_COMPOSITION = {
    "curve_type": "GCurve.Type", "width": "GCurve.Width",
    "aspect_ratio": "GCurve.AspectRatio", "distance": "GCurve.Dist",
    "rotation": "GCurve.Rot", "superformula": "GCurve.SF",
    "superellipse_n": "GCurve.SE.n", "sf_a": "GCurve.SF.a", "sf_b": "GCurve.SF.b",
    "sf_m1": "GCurve.SF.m1", "sf_m2": "GCurve.SF.m2", "sf_n1": "GCurve.SF.n1",
    "sf_n2": "GCurve.SF.n2", "sf_n3": "GCurve.SF.n3",
}


def validate_composition(params: Mapping[str, Any], formula: str, *, length_supplied: bool = False) -> None:
    if params.get("s1", 0) == 0 or params.get("s2", 0) == 0:
        return
    for name in (*PROFILE_COMPOSITION.values(), *GUIDE_COMPOSITION.values()):
        if name not in params:
            continue
        value = params[name]
        if not isinstance(value, Real) or isinstance(value, bool):
            raise ValueError(f"throat stretch does not support per-azimuth {name} yet; must be a plain finite number")
        if not math.isfinite(value):
            raise ValueError(f"throat stretch {name} must be a plain finite number")
    prefix = any(params.get(k, 0) != 0 for k in ("Throat.Ext.Length", "Slot.Length"))
    rotation = params.get("Rot", 0) != 0
    guide = all(params.get(k, 0) != 0 for k in ("GCurve.Type", "GCurve.Width"))
    if rotation and (formula == "R-OSSE" or prefix):
        raise ValueError("throat stretch with Rot and a prefix (or R-OSSE Rot) is unverified and not supported")
    if formula == "OSSE" and params.get("Slot.Length", 0) != 0:
        raise ValueError("throat stretch with OSSE Slot.Length is unverified and not supported")
    if guide and (prefix or rotation):
        raise ValueError("throat stretch with GCurve and a prefix or Rot is unverified and not supported")
    if formula == "R-OSSE" and length_supplied:
        raise ValueError("throat stretch with R-OSSE top-level Length is unverified and not supported")


def mesher_supports_stretch() -> bool:
    # Package versions do not identify SHA pins: main can still declare 0.2.3.
    try:
        from hornlab_mesher.throat_stretch import stretch_coefficients
    except ImportError:
        return False
    return callable(stretch_coefficients)
