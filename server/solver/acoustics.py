"""Acoustic constants sourced from the pinned native solver packages.

WG never passes a sound speed or an air density into a solve, so whatever the
native package defaults to is what the physics actually ran with.  Typing the
numbers here instead of asking is how the reported impedance ended up
normalized by rho=1.21 while every pressure had been produced with 1.2041.
"""

from __future__ import annotations

import importlib
import math


# ``hornlab_metal_bem`` is first because it is the only package that publishes
# an ``AIR_DENSITY``, and it speaks for both: ``hornlab_bempp_bem`` runs the
# identical 1.2041 as the default of ``SolveConfig.air_density``.
_CONSTANT_PACKAGES = ("hornlab_metal_bem", "hornlab_bempp_bem")

# Reached only on a host where neither native package imports.  Result mapping
# resolves its reference constants at import time, long before any engine is
# picked, so a missing backend must not be the thing that makes it unimportable
# -- absence of a backend is a normal capability state here.  These repeat the
# packages' own published values; ``server/tests/test_solver_acoustics.py``
# fails if they ever drift apart.
_FALLBACK_SOUND_SPEED_M_PER_S = 343.0
_FALLBACK_AIR_DENSITY_KG_PER_M3 = 1.2041


def _solver_constant(package_name: str, attribute: str) -> float:
    constants = importlib.import_module(f"{package_name}._constants")
    value = float(getattr(constants, attribute))
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{package_name} exposes an invalid {attribute}")
    return value


def _first_published(attribute: str, fallback: float) -> float:
    """Take the constant from the first installed package that publishes it.

    A package that publishes a nonsense value still raises: a wrong number is
    a bug to see, not something to quietly step over like an absent backend.
    """

    for package_name in _CONSTANT_PACKAGES:
        try:
            return _solver_constant(package_name, attribute)
        except (ImportError, OSError, AttributeError):
            continue
    return fallback


def solver_sound_speed_m_per_s(package_name: str) -> float:
    """Return the sound speed the named native solver uses for its wavenumber."""

    return _solver_constant(package_name, "SPEED_OF_SOUND")


def solver_air_density_kg_per_m3(package_name: str) -> float:
    """Return the air density the named native solver's physics runs at.

    Only ``hornlab_metal_bem`` publishes ``AIR_DENSITY``; asking
    ``hornlab_bempp_bem`` raises ``AttributeError``, which is why anything
    needing a density for a non-Metal result goes through
    ``reference_air_density_kg_per_m3`` rather than naming a package.
    """

    return _solver_constant(package_name, "AIR_DENSITY")


def reference_air_density_kg_per_m3() -> float:
    """Return the density every backend's solve ran at.

    Not per-engine, because it cannot be: the density lives in one package's
    ``_constants`` and in another's dataclass default, and both are 1.2041.
    Should they ever diverge, this has to become a per-engine value plumbed
    beside the sound speed.
    """

    return _first_published("AIR_DENSITY", _FALLBACK_AIR_DENSITY_KG_PER_M3)


def reference_sound_speed_m_per_s() -> float:
    """Return a sound speed for callers with no engine in hand.

    Anything that knows which backend produced a result should ask
    ``solver_sound_speed_m_per_s`` for that backend instead.
    """

    return _first_published("SPEED_OF_SOUND", _FALLBACK_SOUND_SPEED_M_PER_S)


__all__ = [
    "reference_air_density_kg_per_m3",
    "reference_sound_speed_m_per_s",
    "solver_air_density_kg_per_m3",
    "solver_sound_speed_m_per_s",
]
