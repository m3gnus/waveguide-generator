"""Native ICW advanced inputs and FREEFORM circle station contracts."""

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from hornlab_mesher.preview.api import build_preview_geometry

from server.design.migrate import apply_migrations
from server.design.schema import DesignConfig, ICW_ADVANCED_KEYS
from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config


ADVANCED = {
    "r_aperture": 110,
    "x_aperture": 120,
    "x_setback": 10,
    "kappa0": -0.001,
    "kappa_abs_max": 0.05,
    "dkappa_ds_abs_max": 0.1,
    "theta_max_deg": 170,
    "pin_mouth_radius": False,
    "icw_seed": {"type": "OSSE", "L": 120, "r0": 12.7, "a": 45, "metadata": {"label": "seed; preserved"}},
    "icw_coeffs": [0, "0.001", 0.002, 0],
    "icw_S": 180,
}


def test_absent_advanced_icw_keeps_exact_existing_json_and_text() -> None:
    design = DesignConfig.model_validate({"formula": "ICW"})
    before = (Path(__file__).parent / "data/icw-default.json").read_text(encoding="utf-8").strip()
    assert design.model_dump_json() == before
    assert DesignConfig.model_validate_json(before).model_dump_json() == before
    assert not set(ICW_ADVANCED_KEYS).intersection(design_to_mesher_config(design)["profile"])
    source = "; Parameter config\nICW = {\nL = 120\nR = 110\n}\n"
    assert serialize(parse(source)) == source
    assert "WG.Geometry" not in serialize(design)


def test_present_advanced_icw_round_trips_native_file_and_json() -> None:
    design = DesignConfig.model_validate({"formula": "ICW", **ADVANCED})
    assert design.root.icw_seed == ADVANCED["icw_seed"]
    assert type(design.root.icw_coeffs[0]) is int
    assert type(design.root.icw_coeffs[2]) is float
    wire = design.model_dump_json()
    assert DesignConfig.model_validate_json(wire).model_dump_json() == wire
    text = serialize(design)
    assert "WG.Geometry" in text
    assert "\\u003b" in text
    reopened = parse(text).design
    assert reopened.model_dump_json() == wire
    assert serialize(parse(text)) == text


@pytest.mark.parametrize("key", ICW_ADVANCED_KEYS)
def test_each_advanced_icw_key_translates_only_when_set(key: str) -> None:
    # Direct coefficients are only valid with their arc length.
    companions = {"icw_S": ADVANCED["icw_S"]} if key == "icw_coeffs" else {}
    design = DesignConfig.model_validate({"formula": "ICW", key: ADVANCED[key], **companions})
    profile = design_to_mesher_config(design)["profile"]
    assert profile[key] == ADVANCED[key]
    assert set(ICW_ADVANCED_KEYS).intersection(profile) == {key, *companions}


@pytest.mark.parametrize("key,value", [
    ("kappa_abs_max", 0), ("dkappa_ds_abs_max", -1), ("icw_S", 0),
    ("theta_max_deg", 90), ("theta_max_deg", 181), ("kappa0", "cos(p)"),
    ("x_aperture", float("inf")), ("pin_mouth_radius", "false"),
])
def test_advanced_scalar_validation_matches_mesher(key: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula": "ICW", key: value})


@pytest.mark.parametrize("stamp", ["ath-2026-08c-v1", "ath-2026-09a-v2"])
def test_ath_stamped_icw_design_with_advanced_keys_saves_and_reopens(stamp: str) -> None:
    # WG.Geometry is a WG-only block (ATH ignores it), so an ATH-stamped ICW design
    # reached through a family switch must still save, reopen and hash.
    design = DesignConfig.model_validate(
        {"formula": "ICW", "text_import_version": stamp, "pin_mouth_radius": False, "kappa0": .001}
    )
    text = serialize(design)
    assert "WG.Geometry" in text
    reopened = parse(text).design
    assert reopened.root.text_import_version == stamp
    assert reopened.root.pin_mouth_radius is False
    assert reopened.root.kappa0.constant_value() == .001
    assert serialize(parse(text)) == text


def test_advanced_icw_controls_are_refused_for_other_families() -> None:
    text = serialize(DesignConfig.model_validate({"formula": "ICW", "kappa0": .001}))
    with pytest.raises(TextConfigError, match="require an ICW design"):
        parse(text.replace("formula = ICW", "formula = OSSE").replace("ICW = {", "OSSE = {"))


def test_nondefault_throat_curvature_changes_real_preview() -> None:
    payload = {"formula": "ICW", "r0": 12.7, "a0": 18, "L": 120, "R": 110, "mesh": {"wall_thickness": 0}}
    def wall(extra: dict[str, object]) -> np.ndarray:
        config = design_to_mesher_config(DesignConfig.model_validate({**payload, **extra}))
        geometry = build_preview_geometry(config, preview_options("coarse"))
        return next(surface.positions for surface in geometry.surfaces if surface.role == "horn.inner")
    default = wall({})
    changed = wall({"kappa0": .001})
    assert default.shape == changed.shape
    assert np.max(np.abs(default - changed)) > .01


def circle_payload() -> dict[str, object]:
    return {
        "formula": "FREEFORM", "length": 120,
        "profile_h": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 100}]},
        "profile_v": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 80}]},
        "cross_sections": [{"t": 0, "shape": "circle"}, {"t": .5, "shape": "circle"}, {"t": 1, "shape": "circle"}],
        "mesh": {"wall_thickness": 0},
    }


def test_circle_schema_translation_file_round_trip_and_real_preview() -> None:
    payload = circle_payload()
    migrated, applications = apply_migrations(payload)
    assert applications == []
    design = DesignConfig.model_validate(migrated)
    assert all(station.shape == "circle" for station in design.root.cross_sections)
    assert DesignConfig.model_validate_json(design.model_dump_json()) == design
    text = serialize(design)
    assert "0 circle" in text
    reopened = parse(text).design
    assert [station.shape for station in reopened.root.cross_sections] == ["circle"] * 3
    assert [station.t.constant_value() for station in reopened.root.cross_sections] == [0, .5, 1]
    config = design_to_mesher_config(design)
    assert config["profile"]["crossSections"] == [{"t": t, "shape": "circle"} for t in (0, .5, 1)]
    geometry = build_preview_geometry(config, preview_options("coarse"))
    wall = next(surface.positions for surface in geometry.surfaces if surface.role == "horn.inner")
    assert wall.size > 0 and np.isfinite(wall).all()
    # Circle aliases ellipse, retaining both different meridian mouth radii.
    ellipse = circle_payload()
    ellipse["cross_sections"] = [{"t": t, "shape": "ellipse"} for t in (0, .5, 1)]
    other = build_preview_geometry(design_to_mesher_config(DesignConfig.model_validate(ellipse)), preview_options("coarse"))
    np.testing.assert_allclose(wall, next(surface.positions for surface in other.surfaces if surface.role == "horn.inner"))


@pytest.mark.parametrize("extra", [{"exponent": 4}, {"corner_radius_mm": 5}])
def test_circle_rejects_irrelevant_station_fields(extra: dict[str, object]) -> None:
    payload = circle_payload()
    payload["cross_sections"][1].update(extra)
    with pytest.raises(ValidationError, match="circle stations take no"):
        DesignConfig.model_validate(payload)


SCALE_AXES = {
    "r0": 12.7, "L": 120, "R": 110, "a0": 18, "mesh": {"wall_thickness": 0},
}


def _scaled_pair(common: dict[str, object], lengths: dict[str, float], curvatures: dict[str, float], coeffs: list[float] | None = None):
    """A scale-2 design and the same device written by hand at scale 1."""
    base = {**SCALE_AXES, **common}
    at_two = {**base, "scale": 2, **lengths, **curvatures}
    by_hand = {**base, "scale": 1}
    for key in ("r0", "L", "R", *lengths):
        by_hand[key] = 2 * float({**base, **lengths}[key])
    for key, value in curvatures.items():
        by_hand[key] = value / 2
    if coeffs is not None:
        at_two["icw_coeffs"] = list(coeffs)
        by_hand["icw_coeffs"] = [c / 2 for c in coeffs]
    return at_two, by_hand


def _translated(payload: dict[str, object]) -> dict[str, object]:
    return design_to_mesher_config(DesignConfig.model_validate({"formula": "ICW", **payload}))["profile"]


def test_scale_divides_curvatures_in_target_mode() -> None:
    at_two, by_hand = _scaled_pair(
        {"termination": "rollback", "theta1_deg": 160},
        {"r_aperture": 55, "x_aperture": 60, "x_setback": 5},
        {"kappa0": -.004, "kappa_abs_max": .05, "dkappa_ds_abs_max": .2},
    )
    at_two.pop("R")
    by_hand.pop("R")
    assert _translated(at_two) == _translated(by_hand)
    assert _translated(at_two)["kappa0"] == -.002


def test_scale_divides_curvature_expressions_and_coefficients_in_direct_mode() -> None:
    at_two, by_hand = _scaled_pair({}, {"icw_S": 90}, {"kappa0": .002}, coeffs=[.004, .008, .012, .016])
    assert _translated(at_two) == _translated(by_hand)
    assert _translated(at_two)["icw_coeffs"] == [.002, .004, .006, .008]
    # A constant string entry folds like a number; an expression divides symbolically.
    folded = _translated({**at_two, "icw_coeffs": [.004, "0.008", "2*0.006", .016]})["icw_coeffs"]
    assert folded[:2] == [.002, .004]
    assert folded[2] == "(2*0.006) / 2"
    assert _translated({**at_two, "kappa0": "0.002"})["kappa0"] == .001


def test_string_coefficients_reach_the_mesher_in_its_syntax() -> None:
    at_two, by_hand = _scaled_pair({}, {"icw_S": 90}, {}, coeffs=[.004, .008, .012, .016])
    # The schema accepts WG expression syntax, so the mesher must get its own spelling.
    unscaled = _translated({**by_hand, "icw_coeffs": [.004, "Math.PI*0.001", .012, .016]})["icw_coeffs"]
    assert unscaled[1] == "pi*0.001"
    scaled = _translated({**at_two, "icw_coeffs": [.004, "Math.PI*0.001", .012, .016]})["icw_coeffs"]
    assert scaled[1] == "(pi*0.001) / 2"


def test_scale_with_icw_seed_is_refused_but_scale_one_passes() -> None:
    seed = {"type": "OSSE", "L": 120, "r0": 12.7, "a": 45}
    with pytest.raises(ValueError, match="icw_seed holds unscaled"):
        _translated({**SCALE_AXES, "scale": 2, "icw_seed": seed})
    assert _translated({**SCALE_AXES, "scale": 1, "icw_seed": seed})["icw_seed"] == seed


def test_scaled_direct_curve_builds_the_hand_scaled_geometry() -> None:
    at_two, by_hand = _scaled_pair({}, {"icw_S": 90}, {}, coeffs=[.004, .008, .012, .016])
    at_two["mesh"] = by_hand["mesh"] = {"wall_thickness": 0}
    def wall(payload: dict[str, object]) -> np.ndarray:
        geometry = build_preview_geometry(
            design_to_mesher_config(DesignConfig.model_validate({"formula": "ICW", **payload})), preview_options("coarse")
        )
        return next(s.positions for s in geometry.surfaces if s.role == "horn.inner")
    np.testing.assert_allclose(wall(at_two), wall(by_hand), atol=1e-9)


@pytest.mark.parametrize("seed", [
    {"type": "OSSE", "L": float("nan")},
    {"type": "R-OSSE", "nested": {"R": [1, float("inf")]}},
    {"type": "LOOKUP"},
    {"type": "ICW"},
])
def test_seed_schema_refuses_what_the_mesher_refuses(seed: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula": "ICW", "icw_seed": seed})


def test_seed_type_follows_mesher_normalisation_and_default() -> None:
    for kind in ("osse", "r_osse", "ROSSE", None):
        seed = {"L": 100} if kind is None else {"type": kind, "L": 100}
        assert DesignConfig.model_validate({"formula": "ICW", "icw_seed": seed}).root.icw_seed == seed


@pytest.mark.parametrize("coeffs", [
    [], [True, 0.1], [0.1, float("nan")], [0.1, "nope("], [0.1, "cos(p)"], [0.1, "1/0"], [0.1, "1e999"],
])
def test_coeff_schema_refuses_what_the_mesher_refuses(coeffs: list[object]) -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula": "ICW", "icw_coeffs": coeffs, "icw_S": 100})


def test_coeffs_need_an_arc_length_unless_a_seed_takes_precedence() -> None:
    with pytest.raises(ValidationError, match="requires icw_S"):
        DesignConfig.model_validate({"formula": "ICW", "icw_coeffs": [.1, .2]})
    DesignConfig.model_validate({"formula": "ICW", "icw_coeffs": [.1, .2], "icw_S": 100})
    DesignConfig.model_validate({"formula": "ICW", "icw_coeffs": [.1, .2], "icw_seed": {"type": "OSSE"}})


def test_aperture_radius_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula": "ICW", "r_aperture": 0})


def test_coverage_is_sent_only_for_flat_baffle() -> None:
    held = {"coverage_angle": 30, "hold_start": .2, "hold_end": .6}
    flat = _translated({**SCALE_AXES, **held})
    assert flat["coverage_angle"] == 30 and flat["hold_start"] == .2
    rollback = _translated({**SCALE_AXES, **held, "termination": "rollback", "r_aperture": 50})
    assert not {"coverage_angle", "hold_start", "hold_end"} & set(rollback)
    # The mesher accepts the gated config where it would otherwise raise.
    config = design_to_mesher_config(DesignConfig.model_validate({
        "formula": "ICW", "r0": 12.7, "a0": 18, "R": 110, "termination": "rollback",
        "theta1_deg": 160, "depth": 60, "mesh": {"wall_thickness": 0}, **held,
    }))
    build_preview_geometry(config, preview_options("coarse"))


def test_catalog_pin_option_values_are_valid_design_values() -> None:
    import json

    catalog = json.loads((Path(__file__).parents[1] / "integration/parameter-catalog.v1.json").read_text(encoding="utf-8"))
    pin = next(item for item in catalog["parameters"] if item["id"] == "icw.pin_mouth_radius")
    assert [option["value"] for option in pin["options"]] == [None, False, True]
    for option in pin["options"]:
        DesignConfig.model_validate({"formula": "ICW", "pin_mouth_radius": option["value"]})
    theta = next(item for item in catalog["parameters"] if item["id"] == "icw.theta_max_deg")
    assert theta["editor_bounds"]["exclusive_minimum"] is True
