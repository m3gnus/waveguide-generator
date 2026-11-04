"""Shared inner-wall sampling and construction preparation for exports and imports."""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping

import numpy as np

from server.design.schema import DesignConfig, Expr
from server.design.native import NATIVE_FORMULAS
from server.mesh.builder import _solver_mesher_config
from server.preview.construction import retain_mesher_construction
from server.preview.translate import design_to_mesher_config

# Finest historical corner count for bare inner-wall sampling.
EXPORT_CORNER_SEGMENTS = 12
MAX_EXPORT_SEGMENT_INPUT = 1_000_000.0


def _number(value: Expr | None, fallback: float) -> float:
    if value is not None:
        number = value.constant_value()
        if number is not None and math.isfinite(number):
            return float(number)
        from hornlab_mesher.errors import UserRefusal
        raise UserRefusal("Export mesh controls", "Export mesh controls need finite numbers.", "Enter finite numbers in Surface angular samples, Surface length samples and Surface corner samples.", diagnostic="export mesh controls must be finite scalar expressions")
    return fallback


def _segment_number(value: Expr | None, fallback: float, field: str) -> float:
    number = _number(value, fallback)
    if abs(number) > MAX_EXPORT_SEGMENT_INPUT:
        from hornlab_mesher.errors import UserRefusal
        from hornlab_mesher.controls import control_label
        label = control_label(field)
        raise UserRefusal(label, f"{label} is outside the supported export range of ±{int(MAX_EXPORT_SEGMENT_INPUT)}.", f"Reduce {label} before exporting.", diagnostic=f"{field} is outside the supported export range of ±{int(MAX_EXPORT_SEGMENT_INPUT)}")
    return number


def validate_export_segments(design: DesignConfig) -> None:
    """Reject segment controls an export cannot make sense of.

    The geometry exports no longer *read* these fields -- they size themselves
    from a deviation tolerance (``server/exports/sizing.py``). They are still
    validated here because the design carries them for the solver and for the
    profile CSVs, and a non-finite or absurd value should be a 422 rather than
    something the export silently ignores.
    """

    mesh = design.root.mesh
    _segment_number(mesh.length_segments, 0, "mesh.length_segments")
    _segment_number(mesh.angular_segments, 0, "mesh.angular_segments")
    _segment_number(mesh.corner_segments, 0, "mesh.corner_segments")


def _profile_angular(value: float) -> int:
    count = max(4, int(math.floor(value + 0.5)))
    if count % 4 == 0:
        return count
    return max(8, int(math.ceil(count / 8.0) * 8))


def _prepared_design(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> DesignConfig:
    """Reopen the full domain and strip everything a part does not have.

    ``grid`` pins the export's own sampling, chosen by the planners in
    ``server/exports/sizing.py``. Without it this is the probe form: the
    design's own counts stand, which is what a planner measures from before it
    decides. Profile CSVs are the exception -- their rows *are* the artifact,
    so they keep the design's counts verbatim (v1 compatibility).
    """

    validate_export_segments(design)
    payload = copy.deepcopy(design.model_dump(mode="json"))
    root = payload
    if profile_sampling:
        original_length = _segment_number(
            design.root.mesh.length_segments, 40, "mesh.length_segments"
        )
        root["mesh"]["length_segments"] = max(1, int(math.floor(original_length + 0.5)))
        root["mesh"]["angular_segments"] = _profile_angular(
            _number(design.root.mesh.angular_segments, 40)
        )
    else:
        if grid is not None:
            root["mesh"]["angular_segments"] = int(grid[0])
            root["mesh"]["length_segments"] = int(grid[1])
        # Corner arcs get the finest count the export ever used rather than a
        # multiple of the solve setting; only morph and guiding-curve designs
        # have corner arcs at all.
        root["mesh"]["corner_segments"] = EXPORT_CORNER_SEGMENTS
    root["mesh"]["quadrants"] = 1234
    root["mesh"]["wall_thickness"] = 0
    # Bore exports retain the authored lip rows, but have no outer rear body.
    for key in ("ath_rear_shape", "ath_rear_front_offset", "ath_rear_step"):
        root["mesh"][key] = None
    root["mesh"]["vertical_offset"] = 0
    # A bore has no enclosure. Keeping a zero-depth block is invalid for ATH
    # imports, whose translator preserves authored blocks even in bare mode.
    # Remove it before translation/native construction validation, just as the
    # bare grid config does, rather than inventing an invalid enclosure.
    root["enclosure"] = None
    root.setdefault("simulation", {})["sim_type"] = "freestanding"
    return DesignConfig.model_validate(root)


def _prepared_mesher_config(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
    solver: bool = False,
) -> dict[str, Any]:
    """Freeze authored construction before preparing an export's density."""

    validate_export_segments(design)
    authored = design_to_mesher_config(design)
    retained = retain_mesher_construction(authored)
    prepared = _prepared_design(
        design,
        grid=grid,
        profile_sampling=profile_sampling,
    )
    config = _solver_mesher_config(prepared) if solver else design_to_mesher_config(prepared)
    # Carry only the native owner's added transient data. Restoring authored
    # fields here would also restore deliberately removed enclosures/rears.
    config.update({key: value for key, value in retained.items() if key not in authored})
    return config


def _bare_grid_config(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> dict[str, Any]:
    config = _prepared_mesher_config(design, grid=grid, profile_sampling=profile_sampling)
    config["mode"] = "bare"
    config.pop("enclosure", None)
    mesh = config.setdefault("mesh", {})
    mesh.update(
        {
            "quadrants": 1234,
            "wallThickness": 0.0,
            "verticalOffset": 0.0,
            "scaleToMetres": False,
        }
    )
    return config


def _inner_grid(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> np.ndarray:
    try:
        from hornlab_mesher.viewport import build_viewport_geometry_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher viewport geometry is unavailable; install the pinned server requirements"
        ) from exc
    if design.formula in NATIVE_FORMULAS:
        from hornlab_mesher.config_builder import resolve_geometry
        from hornlab_mesher.errors import UserRefusal

        # Native constructions bypass the formula-only viewport grid API.
        # Their own resolved wall is the same owner used by native exports.
        # Resolve the complete authored construction: stripping its wall can
        # invalidate a native roundover before its inner wall is sampled.
        config = design_to_mesher_config(design)
        config["mesh"].update({"vertical_offset_mm": 0.0, "scale_to_metres": False})
        native = resolve_geometry(config).geometry
        points = np.asarray(getattr(native, "inner_points", None), dtype=float)
        if points.ndim != 3 or points.shape[2] != 3:
            raise UserRefusal("Convert to Custom profile", "This construction has no complete inner-wall ring grid.",
                              "Keep its original family, or remove closed-body features before converting.")
        grid = {"inner_points": points, "grid_n_phi": points.shape[0], "grid_n_length": points.shape[1]-1}
    else:
        geometry = build_viewport_geometry_from_config(
            _bare_grid_config(design, grid=grid, profile_sampling=profile_sampling)
        )
        grid = geometry.get("grid") if isinstance(geometry, Mapping) else None
    if not isinstance(grid, Mapping):
        raise RuntimeError("HornLab mesher returned no inner point grid")
    n_phi = int(grid.get("grid_n_phi") or 0)
    n_length = int(grid.get("grid_n_length") or 0)
    points = np.asarray(grid.get("inner_points"), dtype=float)
    expected = n_phi * (n_length + 1) * 3
    if n_phi < 4 or n_length < 1 or points.size != expected:
        raise RuntimeError(
            f"HornLab inner point grid has {points.size} values; expected {expected}"
        )
    points = points.reshape(n_phi, n_length + 1, 3)
    if not np.isfinite(points).all():
        raise RuntimeError("HornLab inner point grid contains non-finite coordinates")
    return points

