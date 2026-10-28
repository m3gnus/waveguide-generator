"""Offset-fold diagnostics stay actionable across preview, export and solve."""

import asyncio
import json
from pathlib import Path

import pytest

from server.design.schema import DesignConfig
from server.exports import api as exports_api
from server.mesh import builder
from server.preview.core import PreviewProtocol, _Request, _compute_value_error_fields
from server.preview.errors import mesher_error_message
from server.preview import wall_check
from server.preview.translate import design_to_mesher_config


DETAIL = "FREEFORM generated outer offset grid has a normal flip near azimuth row 17, axial interval 382"
DESIGN = json.loads((Path(__file__).parent / "fixtures/wall-fold/freeform.json").read_text())
DEGENERATE_DIAGNOSTICS = [
    ("has a degenerate inner normal", "The inner surface collapses"),
    ("has a degenerate outer normal", "The outer surface collapses"),
    ("has degenerate inner and outer normals", "The inner and outer surfaces collapse"),
]
REFUSAL_CASES = [(DETAIL, "The 5 mm wall is thicker"), *[
    (f"FREEFORM generated outer offset grid {detail} near azimuth row 2, axial interval 3", expected)
    for detail, expected in DEGENERATE_DIAGNOSTICS
]]


def design():
    return DesignConfig.model_validate(DESIGN)


@pytest.mark.parametrize("family", ["FREEFORM", "R-OSSE", "OSSE", "ICW"])
@pytest.mark.parametrize("diagnostic", [
    "has a normal flip near azimuth row 17, axial interval 382",
    "self-intersects near azimuth row 2",
    "self-intersects on axial ring 12",
])
def test_all_offset_folds_share_message_and_preserve_diagnostic(family, diagnostic, caplog):
    config = design_to_mesher_config(design())
    config["mesh"]["wallThickness"] = 2.75
    detail = f"{family} generated outer offset grid {diagnostic}"
    message = mesher_error_message(config, detail)
    assert message.startswith("The 2.75 mm wall is thicker than the tightest bend")
    assert "Reduce Wall thickness" in message
    assert "throat or mouth angle" in message
    assert "Bare shell / Infinite baffle" in message
    assert "azimuth" not in message
    assert detail in caplog.text


@pytest.mark.parametrize("detail", [
    "FREEFORM outer offset grid contains non-finite coordinates",
    "FREEFORM crossSections span 0..1 produces a non-convex outline",
    "other error",
])
def test_unrelated_errors_are_not_relabelled(detail):
    assert mesher_error_message(design_to_mesher_config(design()), detail) == detail


@pytest.mark.parametrize("family", ["FREEFORM", "R-OSSE", "OSSE", "ICW"])
@pytest.mark.parametrize("diagnostic,expected", DEGENERATE_DIAGNOSTICS)
def test_degenerate_offset_normals_name_the_collapsed_surface(family, diagnostic, expected, caplog):
    detail = f"{family} generated outer offset grid {diagnostic} near azimuth row 2, axial interval 3"

    def unused_config():
        pytest.fail("Degenerate normals do not establish a wall-thickness cause")

    message = mesher_error_message(unused_config, detail)
    assert message.startswith(expected)
    assert "Adjust the local profile" in message
    assert "thicker" not in message and "fold" not in message
    assert "azimuth" not in message
    assert detail in caplog.text


@pytest.mark.parametrize("diagnostic,expected", DEGENERATE_DIAGNOSTICS)
def test_degenerate_offset_normals_keep_the_existing_fine_wall_refusal(diagnostic, expected, monkeypatch):
    import hornlab_mesher.preview.api

    config = design_to_mesher_config(design())
    detail = f"FREEFORM generated outer offset grid {diagnostic} near azimuth row 2, axial interval 3"

    def fail(*args, **kwargs):
        raise ValueError(detail)

    monkeypatch.setattr(hornlab_mesher.preview.api, "build_preview_geometry", fail)
    wall_check.clear()
    try:
        assert wall_check.check_fine_wall(config) == (True, detail)
        wall_check.remember(config, detail)
        assert wall_check.lookup(config) == (True, detail)
        assert mesher_error_message(config, detail).startswith(expected)
    finally:
        wall_check.clear()


@pytest.mark.parametrize("diagnostic,expected", DEGENERATE_DIAGNOSTICS)
def test_overlap_warning_does_not_turn_degeneracy_into_a_thickness_claim(diagnostic, expected):
    detail = f"OSSE generated outer offset grid {diagnostic} near azimuth row 2, axial interval 3"
    warnings = builder._self_intersection_warnings(
        {"checked": True, "proper_crossing_count": 2, "coplanar_overlap_count": 1}, fold=detail,
    )
    assert len(warnings) == 1
    assert "2 triangle pairs cross and 1 overlap in-plane" in warnings[0]
    assert expected in warnings[0]
    assert "thicker" not in warnings[0] and "thin wall" not in warnings[0]


def test_real_preview_failure_maps_to_plain_message(caplog):
    protocol = PreviewProtocol()
    request = _Request(kind="preview", seq=1, design_revision=1, lod="fine", design=design())
    try:
        with pytest.raises(ValueError, match="normal flip") as error:
            protocol._compute_frame(request)
        fields = _compute_value_error_fields(request, error.value)
        assert fields["design"].startswith("The 5 mm wall is thicker")
        assert str(error.value) in caplog.text
    finally:
        asyncio.run(protocol._preview_service.shutdown())


@pytest.mark.parametrize("diagnostic,expected", REFUSAL_CASES)
@pytest.mark.parametrize("route", ["export_step", "export_stl", "export_profiles"])
def test_export_failures_use_shared_message(route, diagnostic, expected, monkeypatch, caplog):
    async def fail(*args, **kwargs):
        raise ValueError(diagnostic)

    def fail_sync(*args, **kwargs):
        raise ValueError(diagnostic)

    monkeypatch.setattr(exports_api, "build_step_solid", fail)
    monkeypatch.setattr(exports_api, "build_stl", fail)
    monkeypatch.setattr(exports_api, "build_profiles", fail_sync)
    request = exports_api.ExportRequest(design=design(), designRevision=1)
    call = getattr(exports_api, route)
    kwargs = {"body": "solid"} if route == "export_step" else {}
    with pytest.raises(exports_api.HTTPException) as error:
        asyncio.run(call(request, **kwargs))
    assert error.value.status_code == 422
    assert error.value.detail.startswith(expected)
    assert diagnostic in caplog.text


@pytest.mark.parametrize("diagnostic,expected", REFUSAL_CASES)
def test_solver_build_refusal_uses_shared_message(diagnostic, expected, monkeypatch, caplog):
    import hornlab_mesher.config_builder

    def fail(*args, **kwargs):
        raise ValueError(diagnostic)

    monkeypatch.setattr(hornlab_mesher.config_builder, "build_from_config", fail)
    with pytest.raises(RuntimeError, match=expected) as error:
        builder._build_sync(design().model_dump(mode="json"))
    assert str(error.value.__cause__) == diagnostic
    assert diagnostic in caplog.text


def test_wall_expression_uses_effective_scaled_thickness():
    data = {**DESIGN, "scale": 2, "mesh": {**DESIGN["mesh"], "wall_thickness": {"raw": "2+3"}}}
    config = design_to_mesher_config(DesignConfig.model_validate(data))
    assert mesher_error_message(config, DETAIL).startswith("The 10 mm wall is thicker")
