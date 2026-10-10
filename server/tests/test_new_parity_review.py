"""Round-three regressions use the installed ATH importer as the oracle."""
import asyncio
import re

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params, build_point_grid_arrays
from hornlab_mesher.config_parser import parse_text_config
from fastapi import HTTPException

from server.design.textcfg import parse, serialize
from server.design.throat_stretch import mesher_supports_stretch
from server.design_io.api import open_design
from server.preview.translate import design_to_mesher_config

_REAL_C4 = pytest.mark.skipif(not mesher_supports_stretch(), reason="needs real C4 mesher")


def text(family, extra="", top=""):
    dimension = "L = 160" if family == "OSSE" else "R = 200"
    return f"{family} = {{\n{dimension}\ns1 = .5\ns2 = .2\n{extra}\n}}\n{top}"


def params(config):
    config = {**config, "mesh": {"wallThickness": 0, "angularSegments": 8, "lengthSegments": 8}}
    return build_geometry_params(config)[0]


def assert_parity(source):
    expected = params(parse_text_config(source))
    design = parse(source).design
    got = params(design_to_mesher_config(design))
    assert got["r0"] * got["scale"] == expected["r0"] * expected["scale"]
    expected_grid = build_point_grid_arrays(expected)["inner_grid"]
    np.testing.assert_array_equal(build_point_grid_arrays(got)["inner_grid"], expected_grid)
    reopened = params(design_to_mesher_config(parse(serialize(design)).design))
    np.testing.assert_array_equal(build_point_grid_arrays(reopened)["inner_grid"], expected_grid)
    saved_reader = params(parse_text_config(serialize(design)))
    np.testing.assert_array_equal(build_point_grid_arrays(saved_reader)["inner_grid"], expected_grid)


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("scale", [.48, 2])
def test_active_ath_omitted_radius_obeys_mesher_scale(family, scale):
    assert_parity(text(family, top=f"Scale = {scale}"))


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
def test_ath_constant_diameter_expression_matches_mesher(family):
    assert_parity(text(family, extra="Throat.Diameter = 20+2"))


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("scale", [.48, 2])
@pytest.mark.parametrize("extra", ["r0 = 10", "Throat.Diameter = 28", "r0 = 10\nThroat.Diameter = 20+2", "Throat.Diameter = 20+2"])
def test_active_ath_radius_precedence_and_scaled_diameter(family, scale, extra):
    assert_parity(text(family, extra=extra, top=f"Scale = {scale}"))


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("top", ["Rollback = 1", "Rollback = {\nStartAt = .8\n}", "LFSource.1 = {\nRadius = 5\n}"])
def test_active_ath_top_refusals_match_mesher(family, top):
    source = text(family, top=top)
    with pytest.raises(ValueError) as error:
        parse_text_config(source)
    with pytest.raises(ValueError, match=re.escape(str(error.value))):
        parse(source)
    with pytest.raises(HTTPException) as opened_error:
        asyncio.run(open_design(source))
    assert opened_error.value.status_code == 422
    assert opened_error.value.detail["message"] == str(error.value)


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("marker", ["Rollback", "Rollback.StartAt", "LFSource.1", "Source.Contours", "Source.Velocity.1"])
@pytest.mark.parametrize("placement", ["top", "profile", "block"])
def test_active_ath_refusal_scan_matches_all_mesher_locations(family, marker, placement):
    statement = f"{marker} = 1"
    source = text(family, extra=statement if placement == "profile" else "",
                  top=f"{marker} = {{\nValue = 1\n}}" if placement == "block" else statement if placement == "top" else "")
    with pytest.raises(ValueError) as error:
        parse_text_config(source)
    with pytest.raises(ValueError, match=re.escape(str(error.value))):
        parse(source)


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
def test_combined_refusals_match_mesher_order_and_sorted_reason(family):
    source = text(family, extra="Rollback.StartAt = .8\nLFSource.2 = 1",
                  top="Rollback = 1\nLFSource.1 = {\nRadius = 5\n}")
    with pytest.raises(ValueError) as error:
        parse_text_config(source)
    with pytest.raises(ValueError, match=re.escape(str(error.value))):
        parse(source)


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
def test_native_active_diameter_expression_keeps_expression(family):
    design = parse("; MWG config\n" + text(family, extra="Throat.Diameter = 20+2")).design
    assert design.root.r0.raw == "(20+2) / 2"
    if mesher_supports_stretch():
        got = params(design_to_mesher_config(design))
        explicit = parse("; MWG config\n" + text(family, extra="r0 = 11")).design
        expected = params(design_to_mesher_config(explicit))
        np.testing.assert_array_equal(build_point_grid_arrays(got)["inner_grid"],
                                      build_point_grid_arrays(expected)["inner_grid"])


@_REAL_C4
@pytest.mark.parametrize("scale", [.48, 2])
@pytest.mark.parametrize("diameter", ["", "Throat.Diameter = 28", "Throat.Diameter = 20+2"])
def test_flat_active_ath_radius_matches_mesher(scale, diameter):
    assert_parity(f"Length = 160\ns1 = .5\ns2 = .2\nScale = {scale}\n{diameter}")


def diameter_source(family, placement, active, diameter, explicit_radius, scale):
    dimension = "Length = 160" if family == "OSSE" else "R = 200"
    coefficients = "s1 = .5\ns2 = .2" if active else "s1 = 0\ns2 = .2"
    controls = f"{dimension}\n{coefficients}\nThroat.Diameter = {diameter}"
    if explicit_radius:
        controls += "\nr0 = 10"
    profile = (f"{family} = {{\n{controls}\n}}" if placement == "profile"
               else f"{family} = {{\n}}\n{controls}")
    # Explicit simulation type avoids the unrelated raw ATH default mode.
    return f"{profile}\nScale = {scale}\nABEC.SimType = 2"


def assert_inactive_physical_parity(source):
    direct = params(parse_text_config(source))
    design = parse(source).design
    translated = params(design_to_mesher_config(design))
    expected_grid = build_point_grid_arrays(direct)["inner_grid"]
    translated_grid = build_point_grid_arrays(translated)["inner_grid"]
    # Inactive WG pre-scales lengths; the reader scales the evaluated profile.
    # Budget 64 binary64 rounding steps at the grid's physical extent. This
    # absolute bound also covers coordinates near zero; no relative allowance.
    budget_mm = 64 * np.finfo(np.float64).eps * max(1, float(np.abs(expected_grid).max()))
    np.testing.assert_allclose(translated_grid, expected_grid, rtol=0, atol=budget_mm)
    saved = serialize(design)
    reopened = params(design_to_mesher_config(parse(saved).design))
    np.testing.assert_array_equal(build_point_grid_arrays(reopened)["inner_grid"], translated_grid)
    saved_reader = params(parse_text_config(saved))
    np.testing.assert_array_equal(build_point_grid_arrays(saved_reader)["inner_grid"], expected_grid)


@_REAL_C4
@pytest.mark.parametrize("family,active,placement", [
    (family, active, placement) for family in ("OSSE", "R-OSSE")
    for active in (True, False) for placement in ("profile", "flat")
] + [("ROSSE", True, "profile")])
@pytest.mark.parametrize("scale", [.48, 2])
@pytest.mark.parametrize("diameter", ["28", "20+2", "2*sqrt(121)", "2^4+6"])
@pytest.mark.parametrize("explicit_radius", [False, True])
def test_raw_ath_constant_diameter_grid_and_saved_reader_parity(
    family, active, placement, scale, diameter, explicit_radius,
):
    source = diameter_source(family, placement, active, diameter, explicit_radius, scale)
    design = parse(source).design
    expected_radius = 10 if explicit_radius else 14 if diameter == "28" else 11
    assert design.root.r0.value == expected_radius
    # Inactive translation folds global Scale into profile dimensions; active
    # ATH keeps the raw radius and applies Scale when constructing the grid.
    assert params(design_to_mesher_config(design))["r0"] == expected_radius * (1 if active else scale)
    if family == "R-OSSE":
        assert design.root.R.value == 200
        assert params(design_to_mesher_config(design))["R"] == 200 * (1 if active else scale)
    if active:
        assert_parity(source)
    else:
        assert_inactive_physical_parity(source)


@pytest.mark.parametrize("family,active,placement", [
    (family, active, placement) for family in ("OSSE", "R-OSSE")
    for active in (True, False) for placement in ("profile", "flat")
] + [("ROSSE", True, "profile")])
@pytest.mark.parametrize("explicit_radius", [False, True])
@pytest.mark.parametrize("diameter", ["0", "-2", "nan", "inf", "1e309", "20+2*p", "20+0*p", "unknown_name", "20+"])
def test_raw_ath_invalid_diameter_matches_reader_and_api_even_with_radius(
    family, active, placement, explicit_radius, diameter,
):
    source = diameter_source(family, placement, active, diameter, explicit_radius, .48)
    with pytest.raises(ValueError, match="Throat.Diameter") as error:
        parse_text_config(source)
    with pytest.raises(ValueError, match=re.escape(str(error.value))):
        parse(source)
    with pytest.raises(HTTPException) as opened_error:
        asyncio.run(open_design(source))
    assert opened_error.value.status_code == 422
    assert opened_error.value.detail["message"] == str(error.value)


@pytest.mark.parametrize("marker", ["; MWG config", "; Waveguide Generator geometry-interpretation: native-v1"])
@pytest.mark.parametrize("active", [False, True])
def test_native_diameter_expression_remains_azimuth_dependent(marker, active):
    source = diameter_source("OSSE", "profile", active, "20+2*cos(p)", False, 1)
    design = parse(f"{marker}\n{source}").design
    assert design.root.r0.raw == "(20+2*cos(p)) / 2"


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("extension", ["Throat.Ext.Length = 12",
                                       "Throat.Ext.Length = 12\nThroat.Ext.Angle = 5"])
def test_empty_ath_selector_accepts_top_level_extension_in_reader_and_api(family, active, extension):
    source = diameter_source(family, "flat", active, "28", False, 1) + "\n" + extension
    parsed = parse(source)
    assert not any(item.key in {"s1", "s2"} for item in parsed.ignored_profile)
    opened = asyncio.run(open_design(source))
    assert opened["design"]["throat_ext_length"]["value"] == 12
    if active:
        assert_parity(source)
    else:
        assert_inactive_physical_parity(source)


@pytest.mark.parametrize("family,active", [("OSSE", False), ("OSSE", True),
                                          ("R-OSSE", False), ("R-OSSE", True),
                                          ("ROSSE", True)])
@pytest.mark.parametrize("extension", ["Throat.Ext.Length", "Throat.Ext.Angle", "Slot.Length"])
def test_ath_extension_inside_profile_still_refused_by_reader_wg_and_api(family, active, extension):
    source = diameter_source(family, "profile", active, "28", False, 1)
    source = source.replace("Throat.Diameter = 28", f"Throat.Diameter = 28\n{extension} = 12")
    with pytest.raises(ValueError, match="must be top-level keys") as error:
        parse_text_config(source)
    with pytest.raises(ValueError, match=re.escape(str(error.value))):
        parse(source)
    with pytest.raises(HTTPException) as opened_error:
        asyncio.run(open_design(source))
    assert opened_error.value.status_code == 422
    assert opened_error.value.detail["message"] == str(error.value)


def test_populated_ath_profile_still_reports_ignored_flat_coefficients():
    parsed = parse(text("OSSE", top="s1 = .1\ns2 = .3"))
    assert {item.key for item in parsed.ignored_profile} >= {"s1", "s2"}
