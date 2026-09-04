"""Regression coverage for accepted Luna export findings."""

from __future__ import annotations

import asyncio
import importlib.util

from fastapi import HTTPException
import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.exports import api
from server.exports.api import ExportRequest
from server.exports.core import (
    _build_stl_mesh_sync,
    _inner_grid,
    _prepared_design,
    binary_stl,
    validate_export_segments,
)


def _design(*, scale: float = 1.0, length_segments: float = 12) -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 30,
            "a0": 10,
            "r0": 10,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "scale": scale,
            "mesh": {
                "length_segments": length_segments,
                "angular_segments": 12,
                "corner_segments": 4,
                "quadrants": 1234,
            },
        }
    )


def test_geometry_exports_pin_their_own_grid_but_profiles_keep_the_design_s() -> None:
    """Profile CSV rows are the artifact; a mesh grid is the export's to choose."""

    design = _design(length_segments=1)
    pinned = _prepared_design(design, grid=(120, 80))
    profile = _prepared_design(design, profile_sampling=True)
    assert pinned.root.mesh.angular_segments.value == 120
    assert pinned.root.mesh.length_segments.value == 80
    assert profile.root.mesh.length_segments.value == 1


def test_extreme_segment_input_becomes_422_not_overflow(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    request = ExportRequest.model_validate(
        {
            "design": _design(length_segments=1.0e308).model_dump(mode="json"),
            "designRevision": 1,
        }
    )

    async def validate(design: DesignConfig) -> str:
        validate_export_segments(design)
        return "unreachable"

    monkeypatch.setattr(api, "build_step", validate)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.export_step(request))
    assert caught.value.status_code == 422
    assert "supported export range" in caught.value.detail


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_binary_stl_rejects_nonfinite_coordinates(coordinate: float) -> None:
    vertices = [coordinate, 0, 0, 1, 0, 0, 0, 1, 0]
    with pytest.raises(ValueError, match="finite"):
        binary_stl(vertices, [0, 1, 2], [1])


def test_binary_stl_rejects_values_outside_float32_range() -> None:
    vertices = [1.0e100, 0, 0, 1, 0, 0, 0, 1, 0]
    with pytest.raises(ValueError, match="binary STL range"):
        binary_stl(vertices, [0, 1, 2], [1])


def test_base_name_only_strips_known_design_extensions() -> None:
    assert api._base_name("horn.cfg") == "horn"
    assert api._base_name("horn.TXT") == "horn"
    assert api._base_name("horn.mwg") == "horn"
    assert api._base_name("horn.v1") == "horn.v1"


@pytest.mark.skipif(
    importlib.util.find_spec("hornlab_mesher") is None,
    reason="hornlab-waveguide-mesher is not installed",
)
def test_scale_two_doubles_step_grid_and_stl_mesh_bounds() -> None:
    unit = _design(scale=1.0)
    doubled = _design(scale=2.0)

    step_unit = np.ptp(_inner_grid(unit), axis=(0, 1))
    step_doubled = np.ptp(_inner_grid(doubled), axis=(0, 1))
    assert step_doubled == pytest.approx(step_unit * 2.0, rel=1.0e-6, abs=1.0e-8)

    stl_unit = _build_stl_mesh_sync(
        _prepared_design(unit).model_dump(mode="json")
    )
    stl_doubled = _build_stl_mesh_sync(
        _prepared_design(doubled).model_dump(mode="json")
    )
    bounds_unit = np.ptp(np.asarray(stl_unit["vertices"]).reshape(-1, 3), axis=0)
    bounds_doubled = np.ptp(np.asarray(stl_doubled["vertices"]).reshape(-1, 3), axis=0)
    assert bounds_doubled == pytest.approx(bounds_unit * 2.0, rel=1.0e-5, abs=1.0e-9)
