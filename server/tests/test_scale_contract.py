"""Cross-path regression for the single v1 geometry Scale transform."""

from __future__ import annotations

import numpy as np

from hornlab_mesher.preview.api import build_preview_geometry

from server.design.schema import DesignConfig
from server.exports.core import _build_stl_mesh_sync, _inner_grid
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config


def _design(scale: float) -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "scale": scale,
            "L": 40,
            "a": 45,
            "a0": 10,
            "r0": 5,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {"length_segments": 6, "angular_segments": 12},
        }
    )


def _preview_bounds(design: DesignConfig) -> np.ndarray:
    geometry = build_preview_geometry(
        design_to_mesher_config(design), preview_options("coarse")
    )
    points = np.concatenate(
        [np.asarray(surface.positions, dtype=float) for surface in geometry.surfaces]
    )
    return np.ptp(points, axis=0)


def _step_source_bounds(design: DesignConfig) -> np.ndarray:
    # STEP's ruled surface is built directly from this authoritative inner grid.
    points = _inner_grid(design, restore_length=True).reshape(-1, 3)
    return np.ptp(points, axis=0)


def _stl_source_bounds(design: DesignConfig) -> np.ndarray:
    # STL serializes these solver-mesh vertices after its fixed metre-to-mm map.
    mesh = _build_stl_mesh_sync(design.model_dump(mode="json"))
    points = np.asarray(mesh["vertices"], dtype=float).reshape(-1, 3)
    return np.ptp(points, axis=0)


def test_scale_two_doubles_preview_step_and_stl_geometry_bounds_once() -> None:
    unscaled = _design(1)
    scaled = _design(2)
    for bounds in (_preview_bounds, _step_source_bounds, _stl_source_bounds):
        np.testing.assert_allclose(bounds(scaled), bounds(unscaled) * 2, rtol=1e-8, atol=1e-10)
