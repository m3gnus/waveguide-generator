"""Pin the published rho*c to the solver packages rather than to a literal.

The reported ``Z/(rho*c)`` was once normalized by a typed ``1.21 * 343``, which
divided pressures by a density 0.49% away from the 1.2041 the solve had used.
These tests fail if any of those numbers goes back to being typed here.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from types import ModuleType

import pytest

from server.solver import acoustics
from server.solver.result_mapping import (
    REFERENCE_AIR_DENSITY_KG_PER_M3,
    REFERENCE_RHO_C,
)


def _constants(package_name: str) -> ModuleType:
    try:
        return importlib.import_module(f"{package_name}._constants")
    except (ImportError, OSError):
        pytest.skip(f"{package_name} is not installed")


def test_reference_constants_are_the_native_packages_own() -> None:
    constants = _constants("hornlab_metal_bem")
    assert REFERENCE_AIR_DENSITY_KG_PER_M3 == constants.AIR_DENSITY
    assert REFERENCE_RHO_C == constants.AIR_DENSITY * constants.SPEED_OF_SOUND
    assert acoustics.solver_air_density_kg_per_m3("hornlab_metal_bem") == constants.AIR_DENSITY


def test_the_no_backend_fallbacks_repeat_what_the_packages_publish() -> None:
    """A stale fallback would be invisible until a host had no backend at all."""

    constants = _constants("hornlab_metal_bem")
    assert acoustics._FALLBACK_AIR_DENSITY_KG_PER_M3 == constants.AIR_DENSITY
    assert acoustics._FALLBACK_SOUND_SPEED_M_PER_S == constants.SPEED_OF_SOUND


def test_bempp_publishes_no_density_but_runs_the_shared_one() -> None:
    """Why the density is shared instead of asked of the engine per result."""

    constants = _constants("hornlab_bempp_bem")
    assert not hasattr(constants, "AIR_DENSITY")
    with pytest.raises(AttributeError):
        acoustics.solver_air_density_kg_per_m3("hornlab_bempp_bem")

    from hornlab_bempp_bem import SolveConfig

    defaults = {field.name: field.default for field in dataclasses.fields(SolveConfig)}
    assert defaults["air_density"] == REFERENCE_AIR_DENSITY_KG_PER_M3


def test_an_invalid_published_constant_is_loud_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent backend is stepped over; a backend publishing garbage is not."""

    package = ModuleType("fake_backend")
    constants = ModuleType("fake_backend._constants")
    constants.SPEED_OF_SOUND = 0.0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_backend", package)
    monkeypatch.setitem(sys.modules, "fake_backend._constants", constants)

    with pytest.raises(ValueError, match="SPEED_OF_SOUND"):
        acoustics.solver_sound_speed_m_per_s("fake_backend")
    monkeypatch.setattr(acoustics, "_CONSTANT_PACKAGES", ("fake_backend",))
    with pytest.raises(ValueError, match="SPEED_OF_SOUND"):
        acoustics.reference_sound_speed_m_per_s()


def test_a_host_with_no_backend_still_gets_a_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acoustics, "_CONSTANT_PACKAGES", ("absent_backend",))
    assert acoustics.reference_air_density_kg_per_m3() == acoustics._FALLBACK_AIR_DENSITY_KG_PER_M3
    assert acoustics.reference_sound_speed_m_per_s() == acoustics._FALLBACK_SOUND_SPEED_M_PER_S
