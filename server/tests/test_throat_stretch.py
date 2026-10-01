"""WG's C4 boundary, independent of whether the mesher pin has moved."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from server.cadlink.identity import design_hash
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.design.throat_stretch import mesher_supports_stretch
from server.design_io.api import open_design, serialize_design
from server.integration.api import parameter_catalog
from server.preview.translate import design_to_mesher_config


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("key", ["s1", "s2"])
@pytest.mark.parametrize("value,reason", [
    (-.1, "must not be negative"), (10.01, "must be finite and >= 0 and <= 10"),
    (float("inf"), "must be finite and >= 0 and <= 10"),
    (float("nan"), "must be finite and >= 0 and <= 10"),
    (True, "must be a plain finite number"), (".5", "must be a plain finite number"),
    ("0*p", "per-azimuth throat stretch is not supported yet"),
    ({"raw": "1+1", "value": 2}, "must be a plain finite number"),
])
def test_schema_refuses_unsupported_coefficients(formula, key, value, reason):
    with pytest.raises(ValidationError, match=reason):
        DesignConfig.model_validate({"formula": formula, key: value})


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("value", [0, .001, 10])
def test_schema_accepts_plain_bounded_numbers(formula, value):
    design = DesignConfig.model_validate({"formula": formula, "s1": value, "s2": value})
    assert design.root.s1 == design.root.s2 == value


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE", "ICW"])
def test_absent_and_explicit_zero_keep_translation_identical(formula):
    original = DesignConfig.model_validate({"formula": formula})
    zero = DesignConfig.model_validate({"formula": formula, "s1": 0, "s2": 0})
    assert design_to_mesher_config(original) == design_to_mesher_config(zero)
    assert "s1" not in original.model_dump(mode="json")
    assert "s2" not in original.model_dump(mode="json")
    if formula != "ICW":
        assert "s1" not in serialize(original)
        assert "s2" not in serialize(original)


def test_other_family_refuses_nonzero_stretch():
    with pytest.raises(ValidationError, match="OSSE/R-OSSE shape keys are not valid with formula ICW"):
        DesignConfig.model_validate({"formula": "ICW", "s1": .5})


@pytest.mark.parametrize("coefficients", [{"s1": .5}, {"s2": .2}, {"s1": .5, "s2": .2}])
def test_old_pin_refuses_any_nonzero_coefficient(monkeypatch, coefficients):
    monkeypatch.setattr("server.design.throat_stretch.mesher_supports_stretch", lambda: False)
    design = DesignConfig.model_validate({"formula": "OSSE", **coefficients})
    with pytest.raises(ValueError, match="needs mesher 0.2.4"):
        design_to_mesher_config(design)


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_translation_preserves_coefficients_and_scales_after_profile(monkeypatch, formula):
    monkeypatch.setattr("server.design.throat_stretch.mesher_supports_stretch", lambda: True)
    design = DesignConfig.model_validate({"formula": formula, "s1": 10, "s2": 10, "scale": .1,
                                         "L" if formula == "OSSE" else "R": 130})
    config = design_to_mesher_config(design)
    assert config["profile"]["s1"] == config["profile"]["s2"] == 10
    assert config["scale"] == .1
    assert config["profile"]["L" if formula == "OSSE" else "R"] == 130
    assert config["mesh"]["wallThickness"] == .5


@pytest.mark.parametrize("text,expected", [
    ("Length = 160\ns1 = .5\ns2 = .2", (.5, .2)),
    ("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n}", (.5, .2)),
    ("R-OSSE = {\nR = 200\ns1 = .5\ns2 = .2\n}", (.5, .2)),
    ("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n}\ns1 = 1\ns2 = 2", (.5, .2)),
    ("OSSE = {\nL = 160\n}\ns1 = .5\ns2 = .2", (None, None)),
])
def test_ath_import_matches_mesher_coefficient_precedence(text, expected):
    design = parse(text).design
    assert (design.root.s1, design.root.s2) == expected
    assert not {"s1", "s2"} & design.extra_keys.keys()


@pytest.mark.parametrize("location", ["top", "selected", "ignored"])
@pytest.mark.parametrize("value,reason", [("-.1", "must not be negative"), ("11", "<= 10"),
                                          ("nan", "plain finite number"), ("0*p", "per-azimuth")])
def test_ath_refuses_invalid_coefficients_even_in_ignored_sections(location, value, reason):
    text = "OSSE = {\nL = 160\n" + (f"s1 = {value}\n" if location == "selected" else "") + "}\n"
    text += f"s1 = {value}\n" if location == "top" else ""
    text += f"Report = {{\ns1 = {value}\n}}\n" if location == "ignored" else ""
    with pytest.raises(TextConfigError, match=reason):
        parse(text)


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("extra,reason", [
    ("Rot = 10\nThroat.Ext.Length = 12", "Rot and a prefix"),
    ("GCurve.Type = 1\nGCurve.Width = 250\nThroat.Ext.Length = 12", "GCurve and a prefix"),
    ("Slot.Length = 0*p", "per-azimuth Slot.Length"),
    ("Rot = 1+1", "per-azimuth Rot"),
])
def test_ath_composition_refusals(formula, extra, reason):
    dimension = "L = 160" if formula == "OSSE" else "R = 200"
    with pytest.raises(TextConfigError, match=reason):
        parse(f"{formula} = {{\n{dimension}\ns1 = .5\ns2 = .2\n}}\n{extra}")


def test_ath_rot_and_length_contract():
    inside = parse("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\nRot = 10\n}\nRot = 20").design
    outside = parse("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n}\nRot = 10").design
    assert inside.root.rotation.constant_value() == outside.root.rotation.constant_value() == 10
    with pytest.raises(TextConfigError, match="R-OSSE top-level Length"):
        parse("R-OSSE = {\nR = 200\ns1 = .5\ns2 = .2\n}\nLength = 180")
    with pytest.raises(TextConfigError, match="OSSE Slot.Length"):
        parse("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n}\nSlot.Length = 8")


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_save_open_and_identity_include_stretch(formula):
    payload = {"formula": formula, "s1": .5, "s2": .2, "L" if formula == "OSSE" else "R": 160}
    design = DesignConfig.model_validate(payload)
    saved = asyncio.run(serialize_design({"design": payload}))
    opened = asyncio.run(open_design(saved["text"]))["design"]
    assert opened["s1"] == .5 and opened["s2"] == .2
    assert design_hash(DesignConfig.model_validate(opened)) == design_hash(design)
    assert design_hash(DesignConfig.model_validate({**payload, "s1": .6})) != design_hash(design)
    assert design_hash(DesignConfig.model_validate({**payload, "s2": .3})) != design_hash(design)
    if mesher_supports_stretch():
        from hornlab_mesher.config_parser import parse_text_config
        imported = parse_text_config(saved["text"])
        assert imported["profile"]["s1"] == .5 and imported["profile"]["s2"] == .2


def test_catalog_exposes_plain_coefficients_only_for_osse_families():
    catalog = json.loads(asyncio.run(parameter_catalog()).body)
    for key in ("s1", "s2"):
        field = next(p for p in catalog["parameters"] if p["id"] == f"common.{key}")
        assert field["path"] == field["legacy_key"] == key
        assert field["families"] == ["R-OSSE", "OSSE"]
        assert field["accepts_expression"] is False
        assert field["editor_bounds"] == {"minimum": 0, "maximum": 10}


@pytest.mark.parametrize("path", ["slot_length", "throat_ext_length", "throat_ext_angle", "rotation"])
def test_native_composition_expressions_refuse_before_translation(path):
    with pytest.raises(ValidationError, match="throat stretch does not support per-azimuth"):
        DesignConfig.model_validate({"formula": "OSSE", "s1": .5, "s2": .2, path: "0*p"})


def test_numeric_ath_composition_spelling_reaches_mesher_as_numbers(monkeypatch):
    monkeypatch.setattr("server.design.throat_stretch.mesher_supports_stretch", lambda: True)
    design = parse("OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n}\nRot = 0\nSlot.Length = 0\nGCurve.Type = 1\nGCurve.Width = 250").design
    config = design_to_mesher_config(design)
    assert config["profile"]["rot"] == config["profile"]["slotLength"] == 0
    assert config["gcurve"]["gcurveWidth"] == 250


def test_cli_import_refuses_invalid_stretch_before_meshing(tmp_path, monkeypatch, capsys):
    from server.cli.args import main
    from server.engines.dryrun import DryRunEngine
    from server.engines.registry import EngineInfo, EngineRegistry

    path = tmp_path / "stretch.cfg"
    path.write_text("OSSE = {\nL = 160\ns1 = -1\ns2 = .2\n}")
    def unexpected(*args, **kwargs):
        pytest.fail("invalid ATH stretch reached meshing")
    monkeypatch.setattr("server.cli.validate.build_solver_mesh", unexpected)
    registry = EngineRegistry(detector=lambda: [EngineInfo("dryrun", True, "test", "builtin")],
                              factory=lambda _name: DryRunEngine())
    assert main(["validate", str(path), "--json"], engine_registry=registry) == 1
    assert "must not be negative" in capsys.readouterr().out


@pytest.mark.parametrize("coefficients", ["s1 = 0\ns2 = 0", "s1 = 0\ns2 = .2", "s1 = .5\ns2 = 0"])
def test_inactive_stretch_does_not_change_legacy_ath_parameter_precedence(coefficients):
    old = parse("OSSE = {\nL = 160\nr0 = 12.7\n}\nLength = 180\nThroat.Diameter = 40").design
    zero = parse(f"OSSE = {{\nL = 160\nr0 = 12.7\n{coefficients}\n}}\nLength = 180\nThroat.Diameter = 40").design
    # On the current pin dormant nonzero controls still need 0.2.4, so inspect
    # feature-off profile values here; the all-zero translation is identical.
    assert zero.root.L == old.root.L and zero.root.r0 == old.root.r0


@pytest.mark.parametrize("namespace", ["GCurve", "GCURVE"])
def test_ath_guide_blocks_follow_mesher_precedence_and_refusals(namespace):
    text = f"OSSE = {{\nL = 160\ns1 = .5\ns2 = .2\n}}\nGCurve.Width = 100\n{namespace} = {{\nType = 1\nWidth = 250\n}}"
    guide = parse(text).design.root.guiding_curve
    assert guide.curve_type.constant_value() == 1
    assert guide.width.constant_value() == 250
    with pytest.raises(TextConfigError, match="GCurve and a prefix"):
        parse(text + "\nThroat.Ext.Length = 12")
    with pytest.raises(TextConfigError, match="guiding curves are only supported with formula OSSE"):
        parse(text.replace("OSSE =", "R-OSSE =").replace("L = 160", "R = 200"))


def test_ath_block_diameter_obeys_explicit_radius_precedence():
    text = "OSSE = {\nL = 160\nThroat.Diameter = 40\ns1 = .5\ns2 = .2\n}"
    assert parse(text).design.root.r0.constant_value() == 20
    assert parse(text.replace("L = 160", "L = 160\nr0 = 12.7")).design.root.r0.constant_value() == 12.7


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("scale", [.1, 2])
def test_real_stretch_preserves_omitted_radial_dimensions(formula, scale):
    if not mesher_supports_stretch():
        pytest.skip("C4 radial compatibility runs after the mesher pin moves")
    import numpy as np
    from hornlab_mesher.config_builder import build_geometry_params, build_point_grid_arrays
    payload = {"formula": formula, "scale": scale,
               "mesh": {"wall_thickness": 0, "angular_segments": 4, "length_segments": 8}}
    def points(payload):
        config = design_to_mesher_config(DesignConfig.model_validate(payload))
        params, _, _ = build_geometry_params(config)
        return build_point_grid_arrays(params)["inner_grid"]
    zero = points(payload)
    active = points({**payload, "s1": .001, "s2": .001})
    np.testing.assert_allclose(active[..., :2], zero[..., :2], rtol=0, atol=1e-12)



def test_ignored_top_level_coefficients_are_reported_to_the_user():
    opened = asyncio.run(open_design("OSSE = {\nL = 160\n}\ns1 = .5\ns2 = .2"))
    assert "s1" not in opened["design"] and "s2" not in opened["design"]
    assert [row["key"] for row in opened["ignoredSettings"]] == ["s1", "s2"]
    assert all("ignored" in row["note"] for row in opened["ignoredSettings"])


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_real_geometry_identity_changes_with_each_stretch_coefficient(formula):
    if not mesher_supports_stretch():
        pytest.skip("C4 geometry identity runs after the mesher pin moves")
    from server.exports.geometry_identity import geometry_hash_for_design
    payload = {"formula": formula, "s1": .45, "s2": .2,
               "L" if formula == "OSSE" else "R": 130, "mesh": {"wall_thickness": 0}}
    identity = geometry_hash_for_design(DesignConfig.model_validate(payload))
    assert geometry_hash_for_design(DesignConfig.model_validate({**payload, "s1": .5})) != identity
    assert geometry_hash_for_design(DesignConfig.model_validate({**payload, "s2": .3})) != identity
    absent = {k: v for k, v in payload.items() if k not in {"s1", "s2"}}
    assert geometry_hash_for_design(DesignConfig.model_validate(absent)) == geometry_hash_for_design(DesignConfig.model_validate({**absent, "s1": 0, "s2": 0}))


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE"])
def test_active_ath_alias_precedence_is_independent_of_source_order(reverse, family):
    pairs = [("a", "40"), ("Coverage.Angle", "50"), ("k", "2"), ("OS.k", "3"), ("Term.k", "5")]
    if family == "OSSE":
        pairs += [("L", "160"), ("Length", "180")]
    else:
        pairs += [("R", "200")]
    if reverse:
        pairs.reverse()
    body = "\n".join(f"{key} = {value}" for key, value in pairs)
    text = f"{family} = {{\n{body}\ns1 = .5\ns2 = .2\n}}"
    root = parse(text).design.root
    assert root.a.constant_value() == 50 and root.k.constant_value() == 5
    if family == "OSSE":
        assert root.L.constant_value() == 180
    if mesher_supports_stretch():
        from hornlab_mesher.config_parser import parse_text_config
        profile = parse_text_config(text)["profile"]
        assert profile["a"] == root.a.constant_value()
        assert profile["k"] == root.k.constant_value()
        if family == "OSSE":
            assert profile["L"] == root.L.constant_value()


def test_old_pin_refusal_covers_preview_solve_and_export_helpers(monkeypatch):
    from server.exports.core import _bare_grid_config
    from server.mesh.builder import _solver_mesher_config
    from server.preview.core import _recentred_preview_config

    monkeypatch.setattr("server.design.throat_stretch.mesher_supports_stretch", lambda: False)
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 160, "s1": .5, "s2": .2})
    for consumer in (_recentred_preview_config, _solver_mesher_config, _bare_grid_config):
        with pytest.raises(ValueError, match="needs mesher 0.2.4"):
            consumer(design)



def test_active_flat_ath_diameter_does_not_override_explicit_radius():
    root = parse("Length = 160\nr0 = 12.7\nThroat.Diameter = 40\ns1 = .5\ns2 = .2").design.root
    assert root.r0.constant_value() == 12.7


@pytest.mark.parametrize("location", ["top", "block"])
def test_active_ath_refuses_unsupported_throat_profile(location):
    text = "OSSE = {\nL = 160\ns1 = .5\ns2 = .2\n"
    text += "Throat.Profile = 2\n}\n" if location == "block" else "}\nThroat.Profile = 2"
    with pytest.raises(TextConfigError, match="Throat.Profile = 2 is not supported"):
        parse(text)



def test_active_ath_block_throat_profile_wins_over_top_level():
    text = "OSSE = {\nL = 160\ns1 = .5\ns2 = .2\nThroat.Profile = 1\n}\nThroat.Profile = 2"
    assert parse(text).design.root.throat_profile.constant_value() == 1
