"""Golden checks for the typed v2 design-to-mesher preview translation."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params, build_point_grid
from hornlab_mesher.preview.api import build_preview_geometry

from server.design.schema import DesignConfig
from server.design.migrate import apply_migrations
from server.design_io.api import open_design
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config


def _translate(payload: dict[str, object]) -> dict[str, object]:
    return design_to_mesher_config(DesignConfig.model_validate(payload))


@pytest.mark.parametrize("morph_target", [0, 2], ids=["none", "circle"])
def test_scalar_round_rosse_has_no_hidden_azimuth_morph(morph_target: int) -> None:
    """None and zero-sized Circle settings must keep every meridian identical."""

    config = _translate(
        {
            "formula": "R-OSSE",
            "R": 140,
            "r0": 12.7,
            "a0": 15.5,
            "a": 25,
            "k": 2,
            "m": 0.85,
            "b": 0.2,
            "r": 0.4,
            "q": 3.4,
            "tmax": 1,
            "morph": {
                "target_shape": morph_target,
                "target_width": 0,
                "target_height": 0,
                "corner_radius": 0,
                "rate": 3,
                "fixed_part": 0,
                "allow_shrinkage": 0,
            },
            "mesh": {
                "angular_segments": 40,
                "length_segments": 20,
                "wall_thickness": 5,
            },
        }
    )
    parameters, _formula, _mode = build_geometry_params(config)
    grid = build_point_grid(parameters)
    phi_count = int(grid["grid_n_phi"])
    axial_count = int(grid["grid_n_length"]) + 1
    points = np.asarray(grid["inner_points"], dtype=float).reshape(
        phi_count, axial_count, 3
    )
    radii = np.hypot(points[:, :, 0], points[:, :, 1])

    np.testing.assert_allclose(
        radii, np.broadcast_to(radii[0], radii.shape), rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        points[:, :, 2],
        np.broadcast_to(points[0, :, 2], points[:, :, 2].shape),
        rtol=0,
        atol=1e-12,
    )


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


def test_legacy_math_and_function_wrappers_are_normalized_only_for_mesher_execution() -> None:
    wrapped = "function anonymous(p\n) {\nreturn 45 - 2*Math.cos(p)**2;\n}"
    design = DesignConfig.model_validate(
        {"formula": "OSSE", "a": {"value": 43, "raw": wrapped}}
    )
    config = design_to_mesher_config(design)

    assert design.root.a is not None
    assert design.root.a.raw == wrapped
    assert design.root.a.text() == wrapped
    assert config["profile"]["a"] == "45 - 2*cos(p)**2"


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
    "fixture",
    json.loads((Path(__file__).with_name("data") / "freeform-axis-payloads.json").read_text()),
    ids=lambda fixture: fixture["name"],
)
def test_legacy_freeform_axis_migration_preserves_exact_mesher_payload(
    fixture: dict[str, object],
) -> None:
    migrated, applied = apply_migrations(fixture["legacy_design"])  # type: ignore[arg-type]
    assert applied[-1].name == "005_freeform_normalized_axis"
    actual = design_to_mesher_config(DesignConfig.model_validate(migrated))
    assert actual == fixture["expected_mesher_payload"]


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
        {
            "formula": "OSSE",
            "enclosure": {"depth": {"value": 80, "raw": "80*p"}},
        },
        {
            "formula": "OSSE",
            "mesh": {"quadrants": {"value": 1, "raw": "1 + p"}},
        },
        {
            "formula": "OSSE",
            "source": {"shape": {"value": 2, "raw": "2 - p"}},
        },
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


def test_morph_exponent_is_dimensionless_while_target_width_is_scaled() -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "scale": 1.5,
            "morph": {"target_shape": 3, "target_exponent": 6, "target_width": 80},
        }
    )

    assert config["morph"]["morphExponent"] == 6.0
    assert config["morph"]["morphWidth"] == 120.0


def test_absent_morph_exponent_is_omitted_from_mesher_config() -> None:
    config = _translate({"formula": "OSSE", "morph": {"target_shape": 3}})

    assert "morphExponent" not in config["morph"]


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
        "wall.rear_return",
        "wall.rear_cap",
    ]
    assert config["morph"] == {
        "morphTarget": "1",
        "morphCorner": 12.636,
        "morphRate": "3",
        "morphFixed": "0.0",
    }


def test_every_expression_capable_field_survives_translation() -> None:
    """No formula field may be flattened on the way to the mesher.

    v1 lost this at its JS-to-Python payload bridge: guiding-curve, morph and
    circular-arc scalars were coerced with a float converter, so a formula
    silently became the field's default and the solved mesh disagreed with the
    viewport. v2 carries Expr end-to-end; this pins that down for the fields
    where v1 got it wrong, including the two the frontend registry marks as
    expression-capable but the v1 contract typed as bare floats.
    """

    expr = "7 + 3*cos(p)"
    config = _translate(
        {
            "formula": "OSSE",
            "L": expr,
            "a": expr,
            "rotation": expr,
            "throat_ext_angle": expr,
            "slot_length": expr,
            "circ_arc_term_angle": expr,
            "circ_arc_radius": expr,
            "guiding_curve": {
                "curve_type": 1,
                "distance": expr,
                "width": expr,
                "aspect_ratio": expr,
                "superellipse_n": expr,
                "rotation": expr,
                "sf_a": expr,
            },
            "morph": {
                "target_shape": 1,
                "target_exponent": expr,
                "target_width": expr,
                "target_height": expr,
                "corner_radius": expr,
                "rate": expr,
                "fixed_part": expr,
            },
        }
    )

    for key in ("gcurveDist", "gcurveWidth", "gcurveAspectRatio", "gcurveSeN", "gcurveRot", "gcurveSfA"):
        assert config["gcurve"][key] == expr, f"{key} was flattened"
    for key in (
        "morphExponent",
        "morphWidth",
        "morphHeight",
        "morphCorner",
        "morphRate",
        "morphFixed",
    ):
        assert config["morph"][key] == expr, f"{key} was flattened"
    for key in (
        "L",
        "a",
        "rot",
        "throatExtAngle",
        "slotLength",
        "circArcTermAngle",
        "circArcRadius",
    ):
        assert config["profile"][key] == expr, f"{key} was flattened"


def test_guiding_curve_expressions_reach_the_mesher_intact() -> None:
    """End-to-end: the translated config drives the mesher's own evaluator."""

    from hornlab_mesher.profile_morph import _guiding_curve_target_radius

    config = _translate(
        {
            "formula": "OSSE",
            "L": 400,
            "guiding_curve": {
                "curve_type": 1,
                "width": "1000 - 200*cos(2*p)^2",
                "superellipse_n": 2,
            },
        }
    )
    params = config["gcurve"]
    radii = [
        _guiding_curve_target_radius(math.radians(deg), params) for deg in (0, 45, 90)
    ]
    assert radii == pytest.approx([400.0, 500.0, 400.0], abs=1e-9)


def _frame_header(blob: bytes) -> dict[str, object]:
    """Pull the JSON header the frontend reads off the front of a frame."""

    text = blob.decode("utf-8", "ignore")
    start = text.index("{")
    depth = 0
    for index, char in enumerate(text[start:], start):
        depth += (char == "{") - (char == "}")
        if depth == 0:
            return json.loads(text[start:index + 1])
    raise AssertionError("no JSON header in frame")


_UNREACHABLE_GUIDE = {
    "formula": "OSSE",
    "L": 900,
    "a": 45,
    "a0": 10,
    "r0": 25.4,
    "k": 7,
    "s": 0.85,
    "n": 4,
    "q": 0.991,
    "guiding_curve": {
        "curve_type": 1,
        "width": 1000,
        "aspect_ratio": 1,
        "superellipse_n": 2,
        "distance": 1,
    },
}


def _build_frame(design: dict[str, object]) -> dict[str, object]:
    from server.preview.core import encode_preview_geometry

    config = design_to_mesher_config(DesignConfig.model_validate(design))
    geometry = build_preview_geometry(config, preview_options("coarse"))
    blob = encode_preview_geometry(
        geometry, epoch=1, seq=1, design_revision=1, lod="coarse", eval_ms=1.0
    )
    return _frame_header(blob)


@pytest.mark.skipif(
    not hasattr(
        __import__("hornlab_mesher.preview.api", fromlist=["api"]),
        "_guiding_curve_warnings",
    ),
    reason="pinned mesher predates the guiding-curve saturation guard",
)
def test_unreachable_guiding_curve_reaches_the_frontend_as_a_frame_warning() -> None:
    """Server-to-frame forwarding, not just the mesher's own warnings list.

    The viewport banner reads header["previewMetadata"]["warnings"]; the DOM
    test injects that field directly, so only this test proves the server
    actually puts it there.
    """

    header = _build_frame(_UNREACHABLE_GUIDE)
    warnings = header["previewMetadata"]["warnings"]
    assert any("guiding curve unreachable" in warning for warning in warnings), warnings
    assert any("the mouth radius is" in warning for warning in warnings), warnings


def test_a_reachable_guiding_curve_leaves_the_frame_warning_free() -> None:
    header = _build_frame({**_UNREACHABLE_GUIDE, "L": 400})
    assert header["previewMetadata"]["warnings"] == []


def test_freeform_morph_reshapes_the_preview_mouth() -> None:
    """The counterpart to the alarm this replaces.

    Until the mesher morph work was installed, a FREEFORM design carrying a
    morph built a preview identical to no morph at all -- the viewport quietly
    contradicted the design while the solve refused it. The predecessor of this
    test asserted that broken behaviour on purpose so the engine change could
    not land unnoticed. It fired; this is the flip it asked for.

    The assertions are deliberately about what the morph *achieves* rather than
    that it changes something. A drawn mouth of 100 x 80 mm is clearly
    anisotropic; a 400 x 400 superellipse target should erase that and land the
    mouth on the typed half-extent of 200 mm. Asserting only inequality would
    stay green if the morph applied the wrong target.
    """

    base: dict[str, object] = {
        "formula": "FREEFORM",
        "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 100}]},
        "profile_v": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 80}]},
        "cross_sections": [
            {"t": 0, "shape": "ellipse"},
            {"t": 1, "shape": "ellipse"},
        ],
    }

    def half_extents(payload: dict[str, object]) -> tuple[float, float]:
        geometry = build_preview_geometry(_translate(payload), preview_options("coarse"))
        points = np.concatenate(
            [np.asarray(surface.positions).reshape(-1, 3) for surface in geometry.surfaces]
        )
        return float(np.max(np.abs(points[:, 0]))), float(np.max(np.abs(points[:, 1])))

    drawn_x, drawn_y = half_extents(base)
    assert drawn_x / drawn_y > 1.15, "the drawn mouth should be clearly anisotropic"

    morphed_x, morphed_y = half_extents(
        {
            **base,
            "morph": {
                "target_shape": 3,
                "target_exponent": 2,
                "target_width": 400,
                "target_height": 400,
            },
        }
    )
    # A square target on an anisotropic mouth: both axes land on the typed
    # half-extent, so the anisotropy is gone rather than merely reduced.
    assert morphed_x == pytest.approx(200.0, abs=3.0)
    assert morphed_y == pytest.approx(200.0, abs=3.0)
    assert morphed_x / morphed_y == pytest.approx(1.0, abs=0.01)

    # A target no engine defines is now refused by name instead of building.
    with pytest.raises(ValueError, match="valid values 0, 1, 2, or 3"):
        half_extents({**base, "morph": {"target_shape": 99}})
