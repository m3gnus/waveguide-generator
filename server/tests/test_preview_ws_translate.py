"""Golden checks for the typed v2 design-to-mesher preview translation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hornlab_mesher.preview.api import build_preview_geometry

from server.design.schema import DesignConfig
from server.design_io.api import open_design
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config


def _translate(payload: dict[str, object]) -> dict[str, object]:
    return design_to_mesher_config(DesignConfig.model_validate(payload))


def test_osse_family_and_guiding_curve_golden() -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": "45 - 2*cos(p)",
            "a0": 10,
            "r0": 12.7,
            "k": 7,
            "s": 0.8,
            "n": 4,
            "q": 0.99,
            "rotation": 6,
            "guiding_curve": {"curve_type": 2, "width": 180, "sf_m1": 4},
        }
    )
    assert config["profile"] == {
        "formula": "OSSE",
        "r0": 12.7,
        "a": "45 - 2*cos(p)",
        "a0": 10.0,
        "k": 7.0,
        "q": 0.99,
        "L": 120.0,
        "n": 4.0,
        "s": 0.8,
        "rot": 6.0,
    }
    assert config["gcurve"] == {"gcurveType": 2.0, "gcurveWidth": 180.0, "gcurveSfM1": 4.0}


def test_rosse_family_golden_excludes_other_family_keys() -> None:
    config = _translate(
        {
            "formula": "R-OSSE",
            "R": 180,
            "r": 100,
            "b": 0.5,
            "m": 0.85,
            "tmax": 1.2,
            "r0": 12.7,
            "a": 50,
            "a0": 12,
            "k": 1,
            "q": 1,
        }
    )
    profile = config["profile"]
    assert profile["formula"] == "R-OSSE"
    assert {key: profile[key] for key in ("R", "r", "b", "m", "tmax")} == {
        "R": 180.0,
        "r": 100.0,
        "b": 0.5,
        "m": 0.85,
        "tmax": 1.2,
    }
    assert not {"L", "n", "s", "rot"}.intersection(profile)


def test_icw_family_golden_threads_positive_coverage_only() -> None:
    config = _translate(
        {
            "formula": "ICW",
            "r0": 12.7,
            "a": 50,
            "a0": 12,
            "k": 1,
            "q": 0.995,
            "L": 120,
            "R": 150,
            "termination": "flat_baffle",
            "n_coeff": 18,
            "coverage_angle": 55,
            "hold_start": 0.25,
            "hold_end": 0.75,
        }
    )
    profile = config["profile"]
    assert profile["termination"] == "flat_baffle"
    assert profile["n_coeff"] == 18.0
    assert profile["coverage_angle"] == 55.0
    assert profile["hold_start"] == 0.25
    assert profile["hold_end"] == 0.75
    assert not {"n", "s", "rot", "m", "r", "b", "tmax"}.intersection(profile)


def test_freeform_family_golden_uses_rows_and_station_camel_case() -> None:
    config = _translate(
        {
            "formula": "FREEFORM",
            "profile_h": {
                "points": [
                    {"z": 0, "r": 12.7, "angle_deg": 10, "strength": 0.8},
                    {"z": 120, "r": 100},
                ],
                "mouth_angle_deg": 0,
                "throat_tangent_scale": 1.2,
                "mouth_tangent_scale": 0.8,
            },
            "profile_v": {
                "points": [{"z": 0, "r": 12.7}, {"z": 120, "r": 80}],
                "throat_tangent_scale": 1.1,
                "mouth_tangent_scale": 0.9,
            },
            "cross_sections": [
                {"t": 0, "shape": "circle"},
                {"t": 0.5, "shape": "superellipse", "exponent": 4},
                {"t": 1, "shape": "rounded_rectangle", "corner_radius_mm": 12},
            ],
            "overshoot_policy": "allow",
        }
    )
    assert config["profile"] == {
        "formula": "FREEFORM",
        "profileH": {"points": [[0.0, 12.7, 10.0], [120.0, 100.0]], "mouthAngleDeg": 0.0},
        "profileV": {"points": [[0.0, 12.7], [120.0, 80.0]]},
        "crossSections": [
            {"t": 0.0, "shape": "ellipse"},
            {"t": 0.5, "shape": "superellipse", "exponent": 4.0},
            {"t": 1.0, "shape": "rounded_rectangle", "cornerRadiusMm": 12.0},
        ],
    }
    serialized = repr(config["profile"])
    for removed in ("strength", "TangentScale", "overshootPolicy"):
        assert removed not in serialized


@pytest.mark.parametrize(
    ("extra", "mode", "wall_thickness"),
    [
        ({"enclosure": {"depth": 80}}, "enclosure", 5.0),
        ({"simulation": {"sim_type": "infinite-baffle"}}, "infinite-baffle", 0.0),
        ({"mesh": {"wall_thickness": 3}}, "freestanding", 3.0),
        ({"mesh": {"wall_thickness": 0}}, "bare", 0.0),
        ({}, "freestanding", 5.0),
    ],
)
def test_mode_precedence_and_full_viewport_symmetry(
    extra: dict[str, object], mode: str, wall_thickness: float
) -> None:
    payload: dict[str, object] = {"formula": "OSSE", **extra}
    if "mesh" in payload:
        payload["mesh"] = {"quadrants": 1, **payload["mesh"]}  # type: ignore[dict-item]
    else:
        payload["mesh"] = {"quadrants": 1}
    config = _translate(payload)
    assert config["mode"] == mode
    assert config["mesh"]["quadrants"] == 1234
    assert config["mesh"]["wallThickness"] == wall_thickness


def test_source_shape_mapping_and_unsupported_contours() -> None:
    flat = _translate({"formula": "OSSE", "source": {"shape": 2, "radius": 20, "curvature": -1}})
    assert flat["source"] == {"sourceShape": 0.0, "sourceRadius": 20.0, "sourceCurv": -1.0}
    with pytest.raises(ValueError, match="contours"):
        _translate({"formula": "OSSE", "source": {"contours": "1, 2"}})


def test_blank_zmap_points_key_is_omitted() -> None:
    """A present-but-empty ZMapPoints key would force zmap mode in the mesher
    (hornlab_mesher/config_parser.py:289-292) and fail on the empty points, so
    the translator must drop blanks entirely."""
    from server.design.schema import DesignConfig
    from server.preview.translate import design_to_mesher_config

    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "r0": 12.7,
            "a0": 10,
            "mesh": {"z_map_points": " \t\n "},
        }
    )
    config = design_to_mesher_config(design)
    assert "zMapPoints" not in config.get("mesh", {})


def test_scale_applies_once_to_v1_waveguide_lengths_but_not_enclosure() -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "scale": 2,
            "L": "100 + 20",
            "r0": 10,
            "circ_arc_radius": 30,
            "mesh": {
                "wall_thickness": 3,
                "throat_resolution": 4,
                "vertical_offset": 5,
            },
            "morph": {"target_width": 80},
            "source": {"radius": 12},
            "enclosure": {"depth": 70, "space_l": 15},
        }
    )
    assert config["profile"]["L"] == "(100 + 20) * 2"
    assert config["profile"]["r0"] == 20
    assert config["profile"]["circArcRadius"] == 60
    assert config["mesh"]["wallThickness"] == 6
    assert config["mesh"]["throatResolution"] == 8
    assert config["mesh"]["verticalOffset"] == 10
    assert config["morph"]["morphWidth"] == 160
    assert config["source"]["sourceRadius"] == 24
    assert config["enclosure"]["depth"] == 70
    assert config["enclosure"]["space_l"] == 15


@pytest.mark.parametrize(
    "payload",
    [
        {"formula": "OSSE", "enclosure": {"depth": "80*p"}},
        {"formula": "OSSE", "mesh": {"quadrants": "1 + p"}},
    ],
)
def test_non_scalar_structural_controls_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="scalar"):
        _translate(payload)


def test_corner_grid_is_explicitly_rejected_when_mesher_cannot_translate_it() -> None:
    payload = {
        "formula": "FREEFORM",
        "profile_h": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 50}]},
        "profile_v": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 40}]},
        "cross_sections": [
            {"t": 0, "shape": "ellipse", "corner_grid": [[0, 1]]},
            {"t": 1, "shape": "ellipse"},
        ],
    }
    with pytest.raises(ValueError, match="corner_grid"):
        _translate(payload)


@pytest.mark.parametrize(
    ("morph", "expected"),
    [
        ({"target_shape": 1}, {"morphTarget": 1.0}),
        (
            {"target_shape": 1, "target_width": 0, "target_height": 0},
            {"morphTarget": 1.0, "morphWidth": 0.0, "morphHeight": 0.0},
        ),
        ({"target_shape": 2}, {"morphTarget": 2.0}),
    ],
)
def test_morph_implicit_target_extents_translate(
    morph: dict[str, object], expected: dict[str, float]
) -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "r0": 12.7,
            "a0": 10,
            "morph": morph,
        }
    )
    assert config["morph"] == expected


def test_morph_real_target_extents_keep_waveguide_scale() -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "scale": 2,
            "morph": {"target_shape": 1, "target_width": 80, "target_height": 40},
        }
    )
    assert config["morph"] == {
        "morphTarget": 1.0,
        "morphWidth": 160.0,
        "morphHeight": 80.0,
    }


def test_tritonia_reference_import_translates_to_mesher_config() -> None:
    path = Path(__file__).with_name("data") / "260308tritonia-q.txt"
    opened = asyncio.run(open_design(path.read_text(encoding="utf-8")))
    design = DesignConfig.model_validate(opened["design"])

    config = design_to_mesher_config(design)
    geometry = build_preview_geometry(config, preview_options("coarse"))
    surface_roles = [surface.role for surface in geometry.surfaces]

    assert config["mode"] == "freestanding"
    assert config["mesh"]["wallThickness"] == pytest.approx(3.51)
    assert surface_roles == [
        "horn.inner",
        "horn.outer",
        "mouth_rim",
        "source_cap",
        "wall.rear_cap",
    ]
    assert config["morph"] == {
        "morphTarget": "1",
        "morphCorner": 12.636,
        "morphRate": "3",
        "morphFixed": "0.0",
    }
