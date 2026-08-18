"""Read frontend-owned solve settings from preserved text-config blocks.

The browser's durable settings layer is deliberately absent here: a headless
request starts from the portable UI defaults (or an explicit ``base``), then
applies ``ABEC.Polars:*`` followed by ``WG.Solve``.  Serialization remains
frontend-owned.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from server.jobs.models import SolveOptions


_ATH_POLAR_PREFIX = "ABEC.Polars:"
_DIAGONAL_BLOCK = f"{_ATH_POLAR_PREFIX}SPL_D"
_AXIS_ORDER = ("horizontal", "vertical", "diagonal")
_ENGINE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_FREQUENCY_SPLIT = re.compile(r"[\s,;]+")
_DECIMAL_NUMBER = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$"
)
_MAX_FREQUENCY_POINTS = 401
_MAX_POLAR_POINTS = 721
_INCLINATION_EPSILON = 1.0e-6


def _portable_defaults() -> SolveOptions:
    """Return the defaults used when the browser turns its UI into a request."""

    return SolveOptions.model_validate(
        {
            "polar_config": {
                "angle_range": [0.0, 180.0, 37],
                "angle_step": 5.0,
                "distance": 2.0,
                "norm_angle": 5.0,
                "inclination": 45.0,
                "enabled_axes": list(_AXIS_ORDER),
                "observation_origin": "mouth",
                "spherical_sampling": False,
                "field_plane": True,
            }
        }
    )


def _items(block: Any) -> Mapping[str, Any]:
    if isinstance(block, Mapping):
        value = block.get("items")
    else:
        value = getattr(block, "items", None)
    return value if isinstance(value, Mapping) else {}


def _js_number(value: Any) -> float | None:
    """Cover JavaScript ``Number`` spellings that can occur in block values."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            numeric = 0.0
        elif _DECIMAL_NUMBER.fullmatch(stripped):
            numeric = float(stripped)
        else:
            bases = {"0x": 16, "0X": 16, "0b": 2, "0B": 2, "0o": 8, "0O": 8}
            prefix = stripped[:2]
            if prefix not in bases:
                return None
            try:
                numeric = float(int(stripped[2:], bases[prefix]))
            except (OverflowError, ValueError):
                return None
    else:
        return None
    return numeric if math.isfinite(numeric) else None


def _parse_range(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, str):
        return None
    parts = [_js_number(part.strip()) for part in value.split(",")]
    if len(parts) != 3 or any(part is None for part in parts):
        return None
    start, end, raw_count = (float(part) for part in parts if part is not None)
    count = max(2, math.floor(raw_count))
    if end <= start:
        return None
    return start, end, (end - start) / (count - 1)


def _classify_inclination(value: Any) -> str:
    inclination = _js_number(value)
    normalized = ((inclination if inclination is not None else 0.0) % 360.0 + 360.0) % 360.0
    mod_180 = normalized % 180.0
    if (
        abs(mod_180) < _INCLINATION_EPSILON
        or abs(mod_180 - 180.0) < _INCLINATION_EPSILON
    ):
        return "horizontal"
    if abs(mod_180 - 90.0) < _INCLINATION_EPSILON:
        return "vertical"
    return "diagonal"


def _classify_block(name: str, block_items: Mapping[str, Any]) -> str:
    by_inclination = _classify_inclination(block_items.get("Inclination"))
    if name == _DIAGONAL_BLOCK and by_inclination != "diagonal":
        return "diagonal"
    return by_inclination


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value == "1" or (isinstance(value, (int, float)) and value == 1):
        return True
    if value == "0" or (isinstance(value, (int, float)) and value == 0):
        return False
    return None


def _frequency_list(value: Any) -> list[float] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    tokens = [token for token in _FREQUENCY_SPLIT.split(text) if token]
    if not tokens or len(tokens) > _MAX_FREQUENCY_POINTS:
        return None
    frequencies = [_js_number(token) for token in tokens]
    if any(value is None or value <= 0.0 for value in frequencies):
        return None
    parsed = [float(value) for value in frequencies if value is not None]
    if any(later <= earlier for earlier, later in zip(parsed, parsed[1:])):
        return None
    return parsed


def _polar_count(start: float, end: float, step: float) -> int:
    """Mirror ``polarConfigFromUi`` including its one-ULP tolerance."""

    span = end - start
    intervals = span / step
    nearest = math.floor(intervals + 0.5)
    resolved = (
        nearest
        if abs(intervals - nearest) <= max(1.0e-9, abs(intervals) * 1.0e-12)
        else math.floor(intervals)
    )
    count = max(2, resolved + 1)
    if count > _MAX_POLAR_POINTS:
        raise ValueError(
            f"Directivity sweep supports at most {_MAX_POLAR_POINTS} angle samples "
            f"(got {count})."
        )
    return count


def solve_options_from_blocks(
    extra_blocks: Mapping[str, Any] | None,
    base: SolveOptions | None = None,
) -> SolveOptions:
    """Apply portable file blocks to browser defaults or an explicit base.

    Resolution deliberately omits browser-owned durable settings.  ATH polar
    values apply first and WG's block wins where both formats describe a solve.
    Unknown blocks, keys, and unreadable individual values are ignored.
    """

    blocks = extra_blocks if isinstance(extra_blocks, Mapping) else {}
    options = (base if base is not None else _portable_defaults()).model_copy(deep=True)
    payload = options.model_dump(mode="json")
    polar = payload["polar_config"]
    start, end, count = polar["angle_range"]
    step = polar.get("angle_step")
    if step is None:
        step = (end - start) / (count - 1)
    range_overridden = False

    polar_entries = sorted(
        (
            (str(name), block)
            for name, block in blocks.items()
            if isinstance(name, str) and name.startswith(_ATH_POLAR_PREFIX)
        ),
        key=lambda item: item[0].casefold(),
    )
    if polar_entries:
        first = _items(polar_entries[0][1])
        angle_range = _parse_range(first.get("MapAngleRange"))
        if angle_range is not None:
            start, end, step = angle_range
            range_overridden = True
        distance = _js_number(first.get("Distance"))
        if distance is not None:
            polar["distance"] = distance
        norm_angle = _js_number(first.get("NormAngle"))
        if norm_angle is not None:
            polar["norm_angle"] = norm_angle

        selected: set[str] = set()
        for name, block in polar_entries:
            block_items = _items(block)
            axis = _classify_block(name, block_items)
            selected.add(axis)
            if axis == "diagonal":
                inclination = _js_number(block_items.get("Inclination"))
                if inclination is not None:
                    polar["inclination"] = inclination
        axes = [axis for axis in _AXIS_ORDER if axis in selected]
        if axes:
            polar["enabled_axes"] = axes

    solve_items = _items(blocks.get("WG.Solve"))
    if solve_items:
        engine = solve_items.get("Engine")
        if isinstance(engine, str) and _ENGINE_PATTERN.fullmatch(engine):
            payload["engine"] = engine.lower()

        symmetry = solve_items.get("Symmetry")
        if symmetry in {"auto", "full", "half_xz", "half_yz", "quarter"}:
            payload["symmetry"] = symmetry

        mesh_validation = solve_items.get("MeshValidation")
        if mesh_validation in {"warn", "strict", "off"}:
            payload["mesh_validation_mode"] = mesh_validation

        verbose = _bool(solve_items.get("Verbose"))
        if verbose is not None:
            payload["verbose"] = verbose

        spacing = solve_items.get("SweepSpacing")
        if spacing in {"log", "linear"}:
            payload["frequency_spacing"] = spacing

        origin = solve_items.get("ObservationOrigin")
        if origin in {"mouth", "throat"}:
            polar["observation_origin"] = origin

        spherical = _bool(solve_items.get("SphericalSampling"))
        if spherical is not None:
            polar["spherical_sampling"] = spherical

        field_plane = _bool(solve_items.get("FieldPlane"))
        if field_plane is not None:
            polar["field_plane"] = field_plane

        list_text = solve_items.get("Frequencies")
        frequencies = _frequency_list(list_text)
        mode = solve_items.get("SweepPoints")
        if frequencies is not None:
            if mode == "range":
                payload["frequencies_hz"] = None
            else:
                payload["frequencies_hz"] = frequencies
                payload["frequency_range"] = None
                payload["num_frequencies"] = None
        elif (not isinstance(list_text, str) or not list_text.strip()) and mode == "range":
            payload["frequencies_hz"] = None

    if range_overridden:
        polar["angle_range"] = [start, end, _polar_count(start, end, step)]
        polar["angle_step"] = step
    return SolveOptions.model_validate(payload)


def has_solve_blocks(extra_blocks: Mapping[str, Any] | None) -> bool:
    """Return whether a design states either supported settings block family."""

    if not isinstance(extra_blocks, Mapping):
        return False
    return "WG.Solve" in extra_blocks or any(
        isinstance(name, str) and name.startswith(_ATH_POLAR_PREFIX)
        for name in extra_blocks
    )


__all__ = ["has_solve_blocks", "solve_options_from_blocks"]
