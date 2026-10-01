"""Real mesher-to-FRAME-SPEC preview round-trip coverage.

This validates the in-process integration. The overseer should additionally
perform the live socket smoke test from the final batch handoff.
"""

from __future__ import annotations

import json
from pathlib import Path
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
    # Fine frames carry inspection curvature; coarse interaction frames omit it
    # so parameter scrubbing stays responsive.
    geometry = build_preview_geometry(design_to_mesher_config(design), preview_options("fine"))
    frame = encode_preview_geometry(
        geometry,
        epoch=7,
        seq=11,
        design_revision=19,
        lod="fine",
        eval_ms=4.2,
    )
    header, arrays = decode(frame)
    assert header["epoch"] == 7
    assert header["seq"] == 11
    assert header["designRevision"] == 19
    assert header["lod"] == "fine"
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

    # FRAME-SPEC section 5: without these the viewport's curvature heatmap can
    # never populate, which is exactly how it shipped.
    declared = [surface for surface in header["surfaces"] if "curvatureMean" in surface]
    assert declared, "no surface declared analytic curvature"
    for surface in declared:
        mean = arrays[surface["curvatureMean"]]
        principal = arrays[surface["curvaturePrincipal"]]
        vertices = len(arrays[surface["positions"]])
        assert mean.dtype == np.dtype("<f4")
        assert mean.shape == (vertices,)
        assert principal.shape == (vertices,)
        assert np.isfinite(mean).all() and np.isfinite(principal).all()
    # The horn wall is genuinely curved, so a flat heatmap would mean the values
    # arrived but say nothing.
    horn = next(surface for surface in header["surfaces"] if surface["role"] == "horn.inner")
    assert float(np.ptp(arrays[horn["curvatureMean"]])) > 0.0


def test_curvature_sections_are_row_aligned_or_rejected() -> None:
    surface = SimpleNamespace(
        role="horn.inner",
        positions=np.zeros((3, 3), dtype=np.float64),
        indices=np.asarray([], dtype=np.uint32),
        normals=np.tile((0.0, 0.0, 1.0), (3, 1)),
        shading="smooth",
        normal_method="analytic-parametric",
        closed_phi=False,
        curvature_mean=np.zeros(2, dtype=np.float64),
        curvature_principal=np.zeros(3, dtype=np.float64),
    )
    with pytest.raises(ValueError, match="row-aligned"):
        encode_preview_geometry(
            SimpleNamespace(surfaces=[surface], metadata={}),
            epoch=1,
            seq=1,
            design_revision=1,
            lod="coarse",
            eval_ms=1.0,
        )


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


def test_incomplete_enclosure_fidelity_remains_unmeasured_in_aggregate() -> None:
    metadata = json.loads(
        (Path(__file__).parent / "fixtures" / "incomplete_fidelity_enclosure.json")
        .read_text(encoding="utf-8")
    )
    surface = lambda role: SimpleNamespace(  # noqa: E731 - compact fixture factory
        role=role,
        positions=np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64),
        indices=np.asarray([0, 1, 2], dtype=np.uint32),
        normals=np.asarray([[0, 0, 1]] * 3, dtype=np.float64),
        shading="smooth",
        normal_method="exact-planar" if role.startswith("enclosure") else "analytic-parametric",
        closed_phi=False,
    )
    geometry = SimpleNamespace(
        surfaces=[surface("horn.inner"), surface("enclosure.front")],
        metadata=metadata,
    )
    header, _arrays = decode(
        encode_preview_geometry(
            geometry,
            epoch=1,
            seq=2,
            design_revision=3,
            lod="fine",
            eval_ms=1.0,
        )
    )
    fidelity = header["fidelity"]
    assert fidelity["maxChordErrorMmAchieved"] is None
    assert fidelity["chordMeasurementComplete"] is False
    assert fidelity["unmeasuredChordIntervals"] == 2
    assert fidelity["minSilhouetteSegmentsAchieved"] == 4
    assert set(fidelity["surfaces"]) == {"horn.inner", "enclosure.front"}


def test_real_mesher_dimensions_pass_through_preview_frame_unchanged() -> None:
    """Exercises both statuses automatically after the mesher pin moves."""
    config = {
        "formula": "OSSE", "mode": "enclosure", "scale": 1.037,
        "profile": {"L_mm": 120, "r0_mm": 12.7, "a_deg": 55, "a0_deg": 15.5, "k": 1, "q": 0.995},
        "enclosure": {"depth_mm": 150, "edge_mm": 18},
    }
    for lod, status in [("coarse", "pending"), ("fine", "current")]:
        geometry = build_preview_geometry(config, preview_options(lod))
        if "dimensions_mm" not in geometry.metadata:
            pytest.skip("Pinned mesher 0.2.3 has no C2 dimensions metadata; runs after the mesher pin moves")
        assert geometry.metadata["dimensions_status"] == status
        if status == "pending":
            assert geometry.metadata["dimensions_mm"] is None
            assert "dimensions_error" not in geometry.metadata
        else:
            assert set(geometry.metadata["dimensions_mm"]) == {
                "mouth_opening", "horn_overall", "enclosure_overall",
            }
        header, _ = decode(encode_preview_geometry(
            geometry, epoch=7, seq=11, design_revision=19, lod=lod, eval_ms=4.2,
        ))
        assert header["previewMetadata"] == geometry.metadata


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_real_mesher_throat_stretch_passes_through_preview_frame(formula) -> None:
    from server.design.throat_stretch import mesher_supports_stretch

    if not mesher_supports_stretch():
        pytest.skip("Pinned mesher 0.2.3 has no C4 throat stretch; runs after the mesher pin moves")
    from hornlab_mesher.config_builder import build_geometry_params
    from hornlab_mesher.profile_formulas import calculate_osse_curve, calculate_rosse_curve

    payload = {"formula": formula, "s1": .45, "s2": .2, "scale": 1.037,
               "L" if formula == "OSSE" else "R": 130,
               "a": 40, "a0": 10, "r0": 12.7, "k": 1,
               "mesh": {"wall_thickness": 0}, "source": {"shape": 2}}
    config = design_to_mesher_config(DesignConfig.model_validate(payload))
    zero_config = design_to_mesher_config(DesignConfig.model_validate({**payload, "s1": 0, "s2": 0}))
    # Pin the degree convention independently of WG's translator.
    params, _, _ = build_geometry_params(config)
    stations = np.linspace(0, 130 if formula == "OSSE" else 1, 33)
    evaluator = calculate_osse_curve if formula == "OSSE" else calculate_rosse_curve
    x0, r0 = evaluator(stations, 0, {**params, "s1": 0, "s2": 0})
    x, r = evaluator(stations, 0, params)
    np.testing.assert_array_equal(r, r0)
    np.testing.assert_allclose(x, x0 + .45 * np.degrees(np.arctan(.2 * x0)), rtol=0, atol=1e-12)
    for lod in ("coarse", "fine"):
        geometry = build_preview_geometry(config, preview_options(lod))
        zero = build_preview_geometry(zero_config, preview_options(lod))
        inner = next(s for s in geometry.surfaces if s.role == "horn.inner")
        inner_zero = next(s for s in zero.surfaces if s.role == "horn.inner")
        assert np.max(inner.positions.reshape(-1, 3)[:, 2]) > np.max(inner_zero.positions.reshape(-1, 3)[:, 2])
        header, arrays = decode(encode_preview_geometry(
            geometry, epoch=7, seq=11, design_revision=19, lod=lod, eval_ms=4.2,
        ))
        assert header["designRevision"] == 19
        assert header["previewMetadata"] == geometry.metadata
        horn = next(surface for surface in header["surfaces"] if surface["role"] == "horn.inner")
        np.testing.assert_array_equal(arrays[horn["positions"]], inner.positions.astype("<f4").reshape(-1, 3))


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_real_mesher_zero_stretch_is_byte_identical(formula) -> None:
    payload = {"formula": formula, "L" if formula == "OSSE" else "R": 130,
               "a": 40, "a0": 10, "r0": 12.7, "k": 1, "mesh": {"wall_thickness": 0}}
    frames = []
    for design in (payload, {**payload, "s1": 0, "s2": 0}):
        config = design_to_mesher_config(DesignConfig.model_validate(design))
        geometry = build_preview_geometry(config, preview_options("coarse"))
        frames.append(encode_preview_geometry(geometry, epoch=7, seq=11,
                      design_revision=19, lod="coarse", eval_ms=4.2))
    # Runtime timing metadata is allowed to differ; every geometry buffer must
    # remain byte-identical, including normals, indices and curvature.
    _, absent = decode(frames[0])
    _, zero = decode(frames[1])
    assert absent.keys() == zero.keys()
    for key in absent:
        assert absent[key].tobytes() == zero[key].tobytes()
