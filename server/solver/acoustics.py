"""Acoustic constants sourced from the pinned native solver packages."""

from __future__ import annotations

import importlib
import math


def solver_sound_speed_m_per_s(package_name: str) -> float:
    """Return the sound speed the named native solver uses for its wavenumber."""

    constants = importlib.import_module(f"{package_name}._constants")
    value = float(getattr(constants, "SPEED_OF_SOUND"))
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{package_name} exposes an invalid SPEED_OF_SOUND")
    return value


__all__ = ["solver_sound_speed_m_per_s"]
