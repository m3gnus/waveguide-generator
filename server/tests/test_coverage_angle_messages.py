"""The shared mesher owns coverage validation; WG presents its refusal unchanged."""

import asyncio

import pytest

from server.design.schema import DesignConfig
from server.exports import api as exports_api
from server.mesh import builder
from server.preview.core import _Request, _compute_value_error_fields
from server.preview.errors import mesher_error_message


MOUTH = "Mouth coverage angle (a) reaches 94.8° near p = 54.0°; OSSE needs it below 90° everywhere. Change the expression so it stays below 90°."
THROAT = "Throat angle (a0) reaches 90.0° near p = 0.0°; OSSE needs it above -90° and below 90° everywhere. Change the expression so it stays above -90° and below 90°."


def design():
    return DesignConfig.model_validate({
        "formula": "OSSE", "L": 140, "r0": 12.7, "a": 45, "a0": 20,
    })


@pytest.mark.parametrize("field,detail", [
    ("a", MOUTH), ("a0", THROAT),
    ("a", "Mouth coverage angle (a): division by zero. Change the expression."),
    ("a0", "Throat angle (a0): math domain error. Change the expression."),
])
def test_preview_targets_the_offending_expression(field, detail):
    request = _Request(kind="preview", seq=1, design_revision=1, lod="fine", design=design())
    assert _compute_value_error_fields(request, ValueError(detail)) == {field: detail}


def test_unrelated_preview_failure_stays_global():
    detail = "the acoustic control grid needed for the requested mm resolution is too large"
    request = _Request(kind="preview", seq=1, design_revision=1, lod="fine", design=design())
    assert _compute_value_error_fields(request, ValueError(detail)) == {"design": detail}


def test_point_grid_failure_does_not_target_a_design_field():
    request = _Request(kind="points", seq=1, design_revision=1, lod="fine")
    assert _compute_value_error_fields(request, ValueError(MOUTH)) == {"points": MOUTH}


@pytest.mark.parametrize("detail", [MOUTH, THROAT])
def test_shared_refusal_is_preserved_without_re_evaluating_design(detail):
    def config():
        raise AssertionError("WG must not repeat the mesher's validation")

    assert mesher_error_message(config, detail) == detail


@pytest.mark.parametrize("route", ["export_step", "export_stl", "export_profiles"])
def test_export_refusals_preserve_the_actionable_message(route, monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError(MOUTH)

    def fail_sync(*args, **kwargs):
        raise ValueError(MOUTH)

    monkeypatch.setattr(exports_api, "build_step_solid", fail)
    monkeypatch.setattr(exports_api, "build_stl", fail)
    monkeypatch.setattr(exports_api, "build_profiles", fail_sync)
    request = exports_api.ExportRequest(design=design(), designRevision=1)
    kwargs = {"body": "solid"} if route == "export_step" else {}
    with pytest.raises(exports_api.HTTPException) as error:
        asyncio.run(getattr(exports_api, route)(request, **kwargs))
    assert error.value.status_code == 422
    assert error.value.detail == MOUTH


def test_solver_build_preserves_shared_refusal(monkeypatch):
    import hornlab_mesher.config_builder

    def fail(*args, **kwargs):
        raise ValueError(MOUTH)

    monkeypatch.setattr(hornlab_mesher.config_builder, "build_from_config", fail)
    with pytest.raises(ValueError, match="Mouth coverage angle") as error:
        builder._build_sync(design().model_dump(mode="json"))
    assert str(error.value) == MOUTH
