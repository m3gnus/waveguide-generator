"""Real mesher-to-FRAME-SPEC preview round-trip coverage.

This validates the in-process integration. The overseer should additionally
perform the live socket smoke test from the final batch handoff.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from hornlab_mesher.preview.api import build_preview_geometry

from server.design.schema import DesignConfig
from server.preview.core import encode_preview_geometry, preview_options
from server.preview.translate import design_to_mesher_config
from server.protocol.frame import decode


def test_real_preview_geometry_round_trips_with_normals_for_every_surface() -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "a0": 10,
            "r0": 12.7,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {"wall_thickness": 3},
        }
    )
    geometry = build_preview_geometry(design_to_mesher_config(design), preview_options("coarse"))
    frame = encode_preview_geometry(
        geometry,
        epoch=7,
        seq=11,
        design_revision=19,
        lod="coarse",
        eval_ms=4.2,
    )
    header, arrays = decode(frame)
    assert header["epoch"] == 7
    assert header["seq"] == 11
    assert header["designRevision"] == 19
    assert header["lod"] == "coarse"
    assert header["previewMetadata"]["api_version"] == "hornlab.preview/1"
    assert header["fidelity"]["surfaces"] == geometry.metadata["fidelity"]
    assert len(header["surfaces"]) == len(geometry.surfaces)
    for surface in header["surfaces"]:
        positions = arrays[surface["positions"]]
        normals = arrays[surface["normals"]]
        indices = arrays[surface["indices"]]
        assert positions.dtype == np.dtype("<f4")
        assert normals.dtype == np.dtype("<f4")
        assert indices.dtype == np.dtype("<u4")
        assert normals.shape == positions.shape
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-3)


def test_frame_boundary_refuses_non_finite_geometry() -> None:
    surface = SimpleNamespace(
        role="horn.inner",
        positions=np.asarray([[np.nan, 0, 0]], dtype=np.float64),
        indices=np.asarray([], dtype=np.uint32),
        normals=np.asarray([[0, 0, 1]], dtype=np.float64),
        shading="smooth",
        normal_method="analytic-parametric",
        closed_phi=False,
    )
    geometry = SimpleNamespace(surfaces=[surface], metadata={})
    with pytest.raises(ValueError, match="non-finite"):
        encode_preview_geometry(
            geometry,
            epoch=1,
            seq=1,
            design_revision=1,
            lod="coarse",
            eval_ms=0,
        )
