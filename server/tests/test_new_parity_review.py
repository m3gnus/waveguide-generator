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


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
@pytest.mark.parametrize("scale", [.48, 2])
def test_active_ath_omitted_radius_obeys_mesher_scale(family, scale):
    assert_parity(text(family, top=f"Scale = {scale}"))


@_REAL_C4
@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ROSSE"])
def test_ath_diameter_expression_obeys_mesher_fallback(family):
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
