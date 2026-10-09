"""WG's supported exterior physics, separate from BEAT's legacy acceptance.

The engine's structural schema deliberately has extensible parameter objects.
This adapter must not forward physics it cannot represent or interpret. This is
an application policy, not a replacement for the engine's schema/mesh checks.
It never changes, normalizes, or copies request data.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from server.contracts.conventions import SOLVER_TIME_CONVENTION


POLICY_VERSION = 1
SOLVER_OPTIONS = frozenset({
    "precision", "bem_backend", "symmetry", "phasor_convention",
    "regular_quadrature_mode", "quadrature_order", "singular_order",
    "ground_plane_min_clearance_m", "burton_miller_assembly",
})


class UnsupportedPhysics(ValueError):
    """A request asks WG to send or interpret unsupported BEAT physics."""


def _fail(path: str, reason: str) -> None:
    raise UnsupportedPhysics(f"WG BEAT preflight {path}: {reason}")


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _number(value: Any, path: str) -> float:
    try:
        finite = math.isfinite(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else False
    except OverflowError:
        finite = False
    if not finite:
        _fail(path, "must be a finite real number")
    return float(value)


def _choice(value: Any, choices: tuple, path: str) -> None:
    if value not in choices:
        _fail(path, f"supported values are {choices}")


def validate_exterior_physics(wire: Mapping[str, Any]) -> None:
    """Refuse unsupported WG physics before worker acquisition, without edits.

This covers the v1/v2 ideal-source exterior adapter. Existing v3 engine drivers
and other BEAT consumers keep their own contracts; WG must explicitly adopt
their inputs AND result interpretation before this policy accepts them.
Descriptive metadata remains open. Engine schema validation remains mandatory.
"""
    wire = _object(wire, "request")
    system = _object(wire.get("compiled_system"), "compiled_system")
    _choice(system.get("contract_version"), (1, 2), "compiled_system.contract_version")
    if system.get("interfaces"):
        _fail("compiled_system.interfaces", "WG supports exterior BEM without FEM interfaces")
    regions = system.get("regions")
    if not isinstance(regions, list) or len(regions) != 1:
        _fail("compiled_system.regions", "requires exactly one exterior air region")
    region = _object(regions[0], "compiled_system.regions[0]")
    _choice(region.get("kind"), ("unbounded_air",), "compiled_system.regions[0].kind")
    if region.get("loss_model") != {}:
        _fail("compiled_system.regions[0].loss_model", "exterior bulk losses are not supported")
    for index, boundary in enumerate(system.get("boundaries", [])):
        path = f"compiled_system.boundaries[{index}]"
        boundary = _object(boundary, path)
        _choice(boundary.get("kind"), ("rigid", "moving"), f"{path}.kind")
        if boundary.get("parameters") != {}:
            _fail(f"{path}.parameters", "wall impedance and other boundary models are not supported")

    options = _object(wire.get("solver_options"), "solver_options")
    for name in options:
        if name not in SOLVER_OPTIONS:
            _fail(f"solver_options.{name}", "option is not represented by the WG exterior adapter")
    _choice(options.get("precision"), ("float32", "float64"), "solver_options.precision")
    _choice(options.get("bem_backend"), ("cpu", "metal", "cuda", "rocm"), "solver_options.bem_backend")
    if options["bem_backend"] == "metal" and options["precision"] != "float32":
        _fail("solver_options.precision", "Metal BEM requires float32")
    _choice(options.get("symmetry"), ("off", "x", "xy", "ground"), "solver_options.symmetry")
    _choice(options.get("phasor_convention"), (SOLVER_TIME_CONVENTION,), "solver_options.phasor_convention")
    _choice(options.get("regular_quadrature_mode"), ("fixed",), "solver_options.regular_quadrature_mode")
    regular = _number(options.get("quadrature_order"), "solver_options.quadrature_order")
    _choice(regular, (1, 2, 4), "solver_options.quadrature_order")
    singular = _number(options.get("singular_order"), "solver_options.singular_order")
    if singular != int(singular) or not 1 <= singular <= 12:
        _fail("solver_options.singular_order", "requires an integer from 1 to 12")
    if options["precision"] == "float32" and singular > 4:
        _fail("solver_options.singular_order", "WG float32 supports orders 1 to 4")
    if "ground_plane_min_clearance_m" in options:
        clearance = _number(options["ground_plane_min_clearance_m"], "solver_options.ground_plane_min_clearance_m")
        if options["symmetry"] != "ground" or clearance < 0:
            _fail("solver_options.ground_plane_min_clearance_m", "requires ground mode and nonnegative clearance")
    _choice(options.get("burton_miller_assembly", "direct_system"),
            ("direct_system", "operator_matrices"), "solver_options.burton_miller_assembly")

    for index, component in enumerate(system.get("components", [])):
        path = f"compiled_system.components[{index}]"
        component = _object(component, path)
        _choice(component.get("kind"), ("ideal_velocity_source",), f"{path}.kind")
        parameters = _object(component.get("parameters"), f"{path}.parameters")
        for name in parameters:
            if name not in {"motion_profile", "motion_axis"}:
                _fail(f"{path}.parameters.{name}", "source parameter is not represented by WG")
        profile = parameters.get("motion_profile", "uniform_normal")
        _choice(profile, ("uniform_normal", "rigid_translation"), f"{path}.parameters.motion_profile")
        if parameters and system["contract_version"] != 2:
            _fail(f"{path}.parameters", "explicit source motion requires compiled contract v2")
        if profile == "uniform_normal":
            if "motion_axis" in parameters:
                _fail(f"{path}.parameters.motion_axis", "requires rigid_translation")
            continue
        axis = parameters.get("motion_axis")
        if not isinstance(axis, list) or len(axis) != 3:
            _fail(f"{path}.parameters.motion_axis", "requires three finite nonzero direction components")
        values = [_number(value, f"{path}.parameters.motion_axis[{j}]") for j, value in enumerate(axis)]
        scale = max(map(abs, values))
        if scale == 0:
            _fail(f"{path}.parameters.motion_axis", "direction must be nonzero")
        # The builder and engine own numerical normalization and geometry
        # tolerances. Repeating normalization here can move a builder-accepted
        # component across the symmetry cutoff through floating point rounding.
    for index, port in enumerate(system.get("excitation_ports", [])):
        port = _object(port, f"compiled_system.excitation_ports[{index}]")
        _choice(port.get("kind"), ("normal_velocity",), f"compiled_system.excitation_ports[{index}].kind")
