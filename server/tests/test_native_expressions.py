"""Native body expressions survive file, preview and solve-mesh boundaries."""
from copy import deepcopy

import numpy as np
import pytest

from hornlab_mesher.config_builder import resolve_geometry
from hornlab_mesher.preview import build_preview_geometry, PreviewOptionsV1
from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.jobs.models import SolveRequest
from server.mesh.builder import _solver_mesher_config
from server.preview.translate import design_to_mesher_config
from server.tests.test_native_formulas import design_payload
from server.tests.test_osse_native_composition import COMBINATIONS, composite_payload


@pytest.mark.parametrize("formula", ["OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL",
                                      "OSSE-AXIAL", "OSSE-ARC", "OSSE-ROUNDOVER", "OSSE-NATIVE"])
def test_expression_body_round_trip_and_geometry(formula):
    payload = design_payload(formula)
    payload["a"] = {"raw": "40 + 2*cos(2*p)", "value": 42, "dialect": "ath"}
    before = deepcopy(payload)
    design = DesignConfig.model_validate(payload)
    config = design_to_mesher_config(design)
    assert config["profile"]["a_deg"] == "40 + 2*cos(2*p)"
    assert config["scale"] == payload["scale"]
    assert parse(serialize(design)).design == design
    request = SolveRequest.model_validate({"design": design.model_dump(mode="json")})
    assert design_to_mesher_config(request.design) == config
    assert _solver_mesher_config(design)["profile"] == config["profile"]
    geometry = resolve_geometry(config).geometry
    radii = np.hypot(geometry.inner_points[:, -1, 0], geometry.inner_points[:, -1, 1])
    assert np.ptp(radii) > .01
    preview = build_preview_geometry(config, PreviewOptionsV1(lod="coarse", include_curvature=False))
    assert "horn.inner" in {surface.role for surface in preview.surfaces}
    assert payload == before


@pytest.mark.parametrize("throat,mouth,axial", COMBINATIONS)
def test_composite_expression_body(throat, mouth, axial):
    payload = composite_payload(throat, mouth, axial)
    payload["L"] = f"{payload['L']} + .1*cos(p)"
    design = DesignConfig.model_validate(payload)
    assert parse(serialize(design)).design == design
    geometry = resolve_geometry(design_to_mesher_config(design)).geometry
    assert geometry.expression_surface is not None


@pytest.mark.parametrize("field", ["L", "r0", "a", "a0", "k"])
def test_invalid_boolean_body_is_still_refused(field):
    payload = design_payload("OSSE-ADAPTER")
    payload[field] = True
    with pytest.raises(ValueError):
        DesignConfig.model_validate(payload)


def test_expression_failure_explains_remedy():
    payload = design_payload("OSSE-AXIAL")
    payload["a"] = "40 + 50*sin(p)"
    with pytest.raises(ValueError, match="azimuth.*Edit.*expression"):
        DesignConfig.model_validate(payload)


@pytest.mark.parametrize("field", ["L", "r0", "a", "a0", "k"])
def test_expression_dialect_is_preserved_and_executed_by_the_existing_owner(field):
    payload = design_payload("OSSE-ADAPTER")
    base = payload[field]
    payload[field] = {"raw": f"{base} + .01*log(10) + .01*sin(p)", "value": None, "dialect": "wg-v1"}
    design = DesignConfig.model_validate(payload)
    config = design_to_mesher_config(design)
    key = {"L": "L_mm", "r0": "r0_mm", "a": "a_deg", "a0": "a0_deg"}.get(field, field)
    assert "log10(10)" in config["profile"][key]
    assert parse(serialize(design)).design == design


def test_native_ath_expression_keeps_natural_log_after_losing_the_import_stamp():
    payload = design_payload("OSSE-ADAPTER")
    raw = "40 + .01*log(10) + .01*sin(p)"
    payload["a"] = {"raw": raw, "value": None, "dialect": "ath"}
    design = DesignConfig.model_validate(payload)
    assert design_to_mesher_config(design)["profile"]["a_deg"] == raw
    reopened = parse(serialize(design)).design
    assert design_to_mesher_config(reopened)["profile"]["a_deg"] == raw


def test_native_expression_stl_and_step_exports(tmp_path):
    from server.exports.core import _build_step_solid_sync, _build_stl_mesh_sync

    payload = design_payload("OSSE-AXIAL")
    payload.update(L="12 + cos(2*p)", r0="1.27 + .05*cos(2*p)",
                   a="40 + cos(2*p)", a0=10, scale=1, axial_scale=1.2)
    payload["mesh"].update(throat_resolution=1, mouth_resolution=2, rear_resolution=2)
    design = DesignConfig.model_validate(payload)
    step = _build_step_solid_sync(design.model_dump(mode="json"), work_dir=str(tmp_path))
    assert "native-osse-meridian-grid-v1" in step.step_text
    stl = _build_stl_mesh_sync(design.model_dump(mode="json"), work_dir=str(tmp_path))
    assert stl["vertices"] and stl["indices"]
    assert set(stl["surfaceTags"]) == {1, 2}
