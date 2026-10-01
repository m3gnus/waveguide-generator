"""ATH importer regressions from the independent C4 review.

Parser/report checks run on either pin. Point-grid parity uses the installed
mesher's importer as the oracle and runs when that install supports C4.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
from hornlab_mesher.config_parser import parse_text_config

from server.design.textcfg import TextConfigError, parse, serialize
from server.design.throat_stretch import mesher_supports_stretch
from server.design_io.api import open_design
from server.preview.translate import design_to_mesher_config


_REAL_C4 = pytest.mark.skipif(not mesher_supports_stretch(), reason="needs real C4 mesher")


def _profile(family, extra="", coefficients="s1 = .5\ns2 = .2"):
    dimension = "L = 160" if family == "OSSE" else "R = 200"
    return f"{family} = {{\n{dimension}\n{coefficients}\n{extra}\n}}"


def _grid(config):
    from hornlab_mesher.config_builder import build_geometry_params, build_point_grid_arrays

    config = {**config, "mesh": {"wallThickness": 0, "angularSegments": 8, "lengthSegments": 8}}
    params, _, _ = build_geometry_params(config)
    return build_point_grid_arrays(params)["inner_grid"]


def _translate_or_pin_refusal(design):
    if mesher_supports_stretch():
        return design_to_mesher_config(design)
    with pytest.raises(ValueError, match="needs mesher 0.2.4"):
        design_to_mesher_config(design)
    return None


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("top", ["", "Coverage.Angle = 50", "Term.n = 6",
                                 "Coverage.Angle = 50\nTerm.n = 6"])
@pytest.mark.parametrize("coefficients", ["s1 = .5", "s2 = .2", "s1 = .5\ns2 = .2"])
def test_rosse_alias_selects_same_family_and_reaches_capability_gate(family, top, coefficients):
    text = _profile(family, coefficients=coefficients) + "\n" + top
    parsed = parse(text)
    assert parsed.design.formula == "R-OSSE"
    assert family not in parsed.extra_blocks
    assert parsed.ignored_settings == []
    for key in ("s1", "s2"):
        assert getattr(parsed.design.root, key) == ({"s1": .5, "s2": .2}[key] if key in coefficients else None)
    got = _translate_or_pin_refusal(parsed.design)
    if got is not None:
        expected = parse_text_config(text)
        assert got["formula"] == expected["formula"]
        assert got["profile"].get("s1", 0) == expected["profile"].get("s1", 0)
        assert got["profile"].get("s2", 0) == expected["profile"].get("s2", 0)
        np.testing.assert_array_equal(_grid(got), _grid(expected))


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("top", ["Length = 180", "Length = 180\nCoverage.Angle = 50\nTerm.n = 6"])
def test_rosse_alias_enforces_r_osse_top_length_refusal(family, top):
    text = _profile(family) + "\n" + top
    with pytest.raises(TextConfigError, match="R-OSSE top-level Length"):
        parse(text)
    if mesher_supports_stretch():
        with pytest.raises(ValueError, match="R-OSSE top-level Length"):
            parse_text_config(text)


def test_canonical_rosse_block_wins_over_alias_and_osse_block():
    text = (_profile("R-OSSE") + "\n" + _profile("ROSSE", coefficients="s1 = 1\ns2 = 2")
            + "\n" + _profile("OSSE", coefficients="s1 = 3\ns2 = 4"))
    parsed = parse(text)
    assert parsed.design.formula == "R-OSSE"
    assert parsed.design.root.s1 == .5 and parsed.design.root.s2 == .2
    assert set(parsed.extra_blocks) == {"ROSSE", "OSSE"}
    assert {item.key for item in parsed.ignored_settings} == {"ROSSE.s1", "ROSSE.s2", "OSSE.s1", "OSSE.s2"}
    _translate_or_pin_refusal(parsed.design)


def test_rosse_alias_ignored_coefficients_use_actual_selected_block_name():
    text = _profile("ROSSE") + "\ns1 = 1\ns2 = 2\n" + _profile("OSSE")
    parsed = parse(text)
    assert "ROSSE" not in parsed.extra_blocks and "OSSE" in parsed.extra_blocks
    ignored = {item.key: item for item in parsed.ignored_settings}
    assert set(ignored) == {"s1", "s2", "OSSE.s1", "OSSE.s2"}
    assert all("ROSSE profile section" in item.note for item in ignored.values())
    assert serialize(parsed) == text
    opened = asyncio.run(open_design(text))
    assert {item["key"] for item in opened["ignoredSettings"]} == set(ignored)


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("placement", ["block", "top", "mixed", "mixed-invalid"])
@pytest.mark.parametrize("scale", [.1, 2])
def test_block_scale_matches_mesher_and_is_reported(family, placement, scale):
    block = (f"Scale = {scale}" if placement == "block" else "Scale = 3" if placement == "mixed"
             else "Scale = invalid" if placement == "mixed-invalid" else "")
    top = f"\nScale = {scale}" if placement != "block" else ""
    # Explicit radius isolates Scale placement from ATH default-radius import.
    text = _profile(family, "r0 = 12.7\n" + block) + top
    parsed = parse(text)
    effective_scale = scale if top else 1
    assert (parsed.design.root.scale.constant_value() if parsed.design.root.scale else 1) == effective_scale
    ignored = {item.key: item.value for item in parsed.ignored_settings}
    assert ignored == ({f"{family}.Scale": block.split(" = ")[1]} if block else {})
    if block:
        assert "top level" in parsed.ignored_settings[0].note
    got = _translate_or_pin_refusal(parsed.design)
    if got is not None:
        expected = parse_text_config(text)
        assert got.get("scale", 1) == expected.get("scale", 1)
        np.testing.assert_array_equal(_grid(got), _grid(expected))
        np.testing.assert_array_equal(_grid(got), _grid(design_to_mesher_config(parse(serialize(parsed.design)).design)))


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("coefficients", ["", "s1 = 0\ns2 = 0"])
def test_legacy_and_wg_native_block_scale_keep_existing_behavior(family, coefficients):
    legacy = parse(_profile(family, "Scale = 2", coefficients=coefficients))
    native = parse("; MWG config\n" + _profile(family, "Scale = 2"))
    assert legacy.design.root.scale.constant_value() == native.design.root.scale.constant_value() == 2
    assert legacy.ignored_settings == native.ignored_settings == []


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("extra", ["Rot = 10", "Length = 180", "Rot = ignored", "Length = ignored",
                                   "L = 180", "n = 6", "Term.n = 6", "s = .9", "h = 2", "Unknown = 9"])
def test_ignored_r_osse_block_keys_are_accepted_and_reported(family, extra):
    text = _profile(family, extra)
    parsed = parse(text)
    key, value = extra.split(" = ")
    assert [(item.key, item.value) for item in parsed.ignored_settings] == [(f"{family}.{key}", value)]
    assert serialize(parsed) == text
    assert parsed.design == parse(_profile(family)).design
    got = _translate_or_pin_refusal(parsed.design)
    if got is not None:
        expected = parse_text_config(text)
        assert expected == parse_text_config(_profile(family))
        np.testing.assert_array_equal(_grid(got), _grid(expected))
    opened = asyncio.run(open_design(text))
    assert opened["ignoredSettings"][0]["key"] == f"{family}.{key}"
    assert opened["ignoredSettings"][0]["value"] == value


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("extra,reason", [("Rot = 10", "R-OSSE Rot"), ("Length = 180", "R-OSSE top-level Length")])
@pytest.mark.parametrize("placement", ["top", "mixed"])
def test_r_osse_top_level_composition_refuses_even_with_ignored_block_copy(family, extra, reason, placement):
    text = _profile(family, extra if placement == "mixed" else "") + "\n" + extra
    with pytest.raises(TextConfigError, match=reason):
        parse(text)
    if mesher_supports_stretch():
        with pytest.raises(ValueError, match=reason):
            parse_text_config(text)


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("extra", ["Rot = 10\nLength = 180", "Rot = ignored\nLength = ignored"])
def test_ignored_block_rot_and_length_with_zero_top_rot(family, extra):
    text = _profile(family, extra) + "\nRot = 0"
    parsed = parse(text)
    assert {item.key for item in parsed.ignored_settings} == {f"{family}.Rot", f"{family}.Length"}
    got = _translate_or_pin_refusal(parsed.design)
    if got is not None:
        np.testing.assert_array_equal(_grid(got), _grid(parse_text_config(text)))


@_REAL_C4
@pytest.mark.parametrize("extra,target", [
    ("r0 = 14", "r0"), ("Throat.Diameter = 28", "r0"),
    ("a = 35", "a"), ("Coverage.Angle = 35", "a"),
    ("a0 = 7", "a0"), ("Throat.Angle = 7", "a0"),
    ("k = 2", "k"), ("OS.k = 2", "k"), ("Term.k = 2", "k"),
    ("q = 3", "q"), ("Term.q = 3", "q"),
    ("R = 180", "R"), ("m = .8", "m"), ("b = .3", "b"),
    ("r = .3", "r"), ("tmax = .8", "tmax"),
])
def test_r_osse_consumed_block_keys_match_real_importer(extra, target):
    text = _profile("ROSSE", extra)
    parsed = parse(text)
    assert parsed.ignored_settings == []
    expected = parse_text_config(text)
    got = design_to_mesher_config(parsed.design)
    assert float(got["profile"][target]) == float(expected["profile"][target])
    np.testing.assert_array_equal(_grid(got), _grid(expected))


@pytest.mark.parametrize("family", ["R-OSSE", "ROSSE"])
@pytest.mark.parametrize("extra,reason", [
    ("Throat.Ext.Length = 0", "must be top-level keys"),
    ("Slot.Length = 0", "must be top-level keys"),
    ("Throat.Profile = 3", "only the OS-SE profile"),
    ("Rollback = 1", "Rollback is not supported"),
    ("Source.Contours = dome", "multi-source ATH configs"),
])
def test_r_osse_refused_block_keys_do_not_become_ignored(family, extra, reason):
    text = _profile(family, extra)
    with pytest.raises(TextConfigError, match=reason):
        parse(text)
    if mesher_supports_stretch():
        with pytest.raises(ValueError, match=reason):
            parse_text_config(text)
