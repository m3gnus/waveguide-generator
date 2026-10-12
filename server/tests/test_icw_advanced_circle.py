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
    before = (Path(__file__).parent / "data/icw-default.json").read_text().strip()
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
    design = DesignConfig.model_validate({"formula": "ICW", key: ADVANCED[key]})
    profile = design_to_mesher_config(design)["profile"]
    assert profile[key] == ADVANCED[key]
    assert set(ICW_ADVANCED_KEYS).intersection(profile) == {key}


@pytest.mark.parametrize("key,value", [
    ("kappa_abs_max", 0), ("dkappa_ds_abs_max", -1), ("icw_S", 0),
    ("theta_max_deg", 90), ("theta_max_deg", 181), ("kappa0", "cos(p)"),
    ("x_aperture", float("inf")), ("pin_mouth_radius", "false"),
])
def test_advanced_scalar_validation_matches_mesher(key: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula": "ICW", key: value})


def test_ath_export_cannot_silently_drop_advanced_inputs() -> None:
    design = DesignConfig.model_validate({"formula": "ICW", "text_import_version": "ath-2026-09a-v2", "kappa0": .001})
    with pytest.raises(TextConfigError, match="native WG/JSON only"):
        serialize(design)


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
