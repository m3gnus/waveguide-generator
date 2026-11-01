"""WG boundary for the mesher throat-stretch contract (mesher 0.2.4).

Keep refusal reasons in sync with hornlab_mesher.throat_stretch. This module
also validates saved designs when the installed mesher predates the feature.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def coefficient(value: Any, key: str) -> float:
    from hornlab_mesher.throat_stretch import stretch_coefficients
    values = stretch_coefficients({key: value})
    return values[0 if key == "s1" else 1]


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
    from hornlab_mesher.throat_stretch import (
        COMPOSITION_PROFILE_KEYS, COMPOSITION_GUIDE_KEYS, validate_stretch_composition,
    )
    if params.get("s1", 0) == 0 or params.get("s2", 0) == 0:
        return
    keys = {name: key for key, (name, _) in {**COMPOSITION_PROFILE_KEYS, **COMPOSITION_GUIDE_KEYS}.items()}
    validate_stretch_composition({keys.get(key, key): value for key, value in params.items()},
                                 formula, length_supplied=length_supplied)


def mesher_supports_stretch() -> bool:
    # Package versions do not identify SHA pins: main can still declare 0.2.3.
    try:
        from hornlab_mesher.throat_stretch import stretch_coefficients
    except (ImportError, OSError):
        return False
    return callable(stretch_coefficients)
