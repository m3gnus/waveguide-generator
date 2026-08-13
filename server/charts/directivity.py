"""Directivity heatmaps with an explicit ten-degree angular graticule."""

from __future__ import annotations

import base64
import importlib
import inspect
import io
import math
from typing import Any, Callable

import numpy as np


ANGLE_GUIDE_STEP_DEG = 10.0


def _constant_horizontal_lines(axis: Any) -> set[float]:
    values: set[float] = set()
    for line in axis.lines:
        try:
            y_values = np.asarray(line.get_ydata(), dtype=float).reshape(-1)
        except (TypeError, ValueError):
            continue
        if y_values.size >= 2 and np.isfinite(y_values).all() and np.allclose(
            y_values, y_values[0]
        ):
            values.add(round(float(y_values[0]), 9))
    return values


def add_ten_degree_angle_guides(
    figure: Any,
    theme: Any,
    *,
    grid_kwargs: Callable[..., dict[str, Any]] | None = None,
) -> None:
    """Fill missing 10-degree guides without duplicating HornLab's own lines."""

    style = (
        grid_kwargs(theme, linewidth=0.5)
        if grid_kwargs is not None
        else {"linewidth": 0.5}
    )
    for axis in figure.axes:
        if axis.get_ylabel() != "Angle [deg]":
            continue
        lower, upper = sorted(float(value) for value in axis.get_ylim())
        first = math.ceil(lower / ANGLE_GUIDE_STEP_DEG) * ANGLE_GUIDE_STEP_DEG
        existing = _constant_horizontal_lines(axis)
        for angle in np.arange(
            first, upper + ANGLE_GUIDE_STEP_DEG * 0.5, ANGLE_GUIDE_STEP_DEG
        ):
            value = float(angle)
            if value <= lower or value >= upper or round(value, 9) in existing:
                continue
            axis.axhline(
                value,
                color=theme.grid_color,
                alpha=theme.secondary_grid_alpha,
                **style,
            )


def render_directivity_heatmap_b64(
    plots: Any,
    frequencies: list[float],
    directivity: dict[str, Any],
    *,
    reference_level: float,
    theme: str,
    reference_frequencies: list[float] | None = None,
    reference_directivity: dict[str, Any] | None = None,
    reference_label: str | None = None,
    dpi: int = 150,
) -> str | None:
    """Render through hornlab-plots, retaining overlays and adding 10° guides."""

    renderer = plots.directivity_heatmap_from_legacy_dict
    try:
        parameters = inspect.signature(renderer).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {
        "reference_level": reference_level,
        "theme": theme,
    }
    if reference_directivity is not None:
        kwargs.update(
            reference_frequencies=reference_frequencies,
            reference_directivity=reference_directivity,
            reference_label=reference_label,
        )
    # Prefer the public API as soon as hornlab-plots exposes this control.
    if "angle_grid_step" in parameters:
        return renderer(
            frequencies,
            directivity,
            dpi=dpi,
            angle_grid_step=ANGLE_GUIDE_STEP_DEG,
            **kwargs,
        )

    module = importlib.import_module(renderer.__module__)
    planes = module._build_planes_from_legacy(frequencies, directivity, smooth=False)  # noqa: SLF001
    if not planes:
        return None
    reference_planes = None
    if reference_frequencies is not None and reference_directivity:
        reference_planes = module._build_planes_from_legacy(  # noqa: SLF001
            reference_frequencies,
            reference_directivity,
            smooth=False,
        )
    theme_object = module.apply_theme_overrides(theme)
    figure = module._build_figure_from_planes(  # noqa: SLF001
        planes,
        reference_level=reference_level,
        theme=theme_object,
        reference_planes=reference_planes,
        reference_label=reference_label,
    )
    add_ten_degree_angle_guides(
        figure,
        theme_object,
        grid_kwargs=getattr(module, "theme_grid_kwargs", None),
    )
    output = io.BytesIO()
    try:
        figure.savefig(
            output,
            format="png",
            dpi=dpi,
            facecolor=figure.get_facecolor(),
            edgecolor="none",
            bbox_inches="tight",
        )
    finally:
        module.plt.close(figure)
    return base64.b64encode(output.getvalue()).decode("ascii")


__all__ = [
    "ANGLE_GUIDE_STEP_DEG",
    "add_ten_degree_angle_guides",
    "render_directivity_heatmap_b64",
]
