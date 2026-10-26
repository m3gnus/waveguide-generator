"""The application preserves the mesher's native OSSE feature contract."""
from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hornlab_mesher.config_builder import resolve_geometry
import pytest
from pydantic import ValidationError

from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.design_io.api import create_design_io_router
from server.cadlink.store import CadLinkStore
from server.jobs.models import SolveRequest
from server.jobs.runtime.core import SymmetryValidationError, _apply_bempp_wall_default
from server.preview.translate import design_to_mesher_config, has_closed_outer_body
from server.tests.test_native_formulas import ADAPTER, design_payload


def composite_payload(throat: bool, mouth: str, axial: bool) -> dict:
    if mouth == 'arc':
        payload = design_payload('OSSE-ARC')
        payload['scale'] = 1.6
        payload['terminating_arc']['end_tangent_deg'] = 60
        if throat:
            payload['throat_adapter'] = {**ADAPTER, 'join_t': .02}
    else:
        payload = design_payload('OSSE-ROUNDOVER')
        payload.update(L=12, r0=1.27, a=45, a0=10, k=1)
        payload['mesh'].update(wall_thickness=.2, mouth_roundover_radius_mm=1)
        if throat:
            payload['throat_adapter'] = {
                'mode': 'authored', 'contract_revision': 1, 'driver_exit_diameter_mm': 2,
                'exit_half_angle_deg': 5, 'length_mm': 3, 'join_t': .1,
                'driver_handle_mm': .8, 'body_handle_mm': 1.2,
            }
    payload['formula'] = 'OSSE-NATIVE'
    if axial:
        payload['axial_scale'] = .7
    return payload


COMBINATIONS = [(False, 'arc', True), (True, 'arc', False), (True, 'arc', True),
                (False, 'roundover', True), (True, 'roundover', False), (True, 'roundover', True)]


@pytest.mark.parametrize('throat,mouth,axial', COMBINATIONS)
def test_composite_round_trip_and_mesher_translation(throat: bool, mouth: str, axial: bool) -> None:
    design = DesignConfig.model_validate(composite_payload(throat, mouth, axial))
    config = design_to_mesher_config(design)
    assert config['formula'] == 'OSSE-NATIVE'
    assert config['mode'] == ('freestanding' if mouth == 'roundover' else 'bare')
    assert ('throat_adapter' in config) is throat
    assert ('axial_scale' in config) is axial
    assert ('terminating_arc' in config) is (mouth == 'arc')
    assert ('mouth_roundover_radius_mm' in config['mesh']) is (mouth == 'roundover')
    assert has_closed_outer_body(design.root) is (mouth == 'roundover')
    assert resolve_geometry(config).formula == 'OSSE-NATIVE'
    assert parse(serialize(design)).design == design


@pytest.mark.parametrize('throat,mouth,axial', COMBINATIONS)
def test_bempp_accepts_only_closed_composites(throat: bool, mouth: str, axial: bool) -> None:
    request = SolveRequest.model_validate({'design': composite_payload(throat, mouth, axial)})
    original = request.model_dump(mode='json')
    if mouth == 'roundover':
        result, adjustment = _apply_bempp_wall_default(request, 'bempp')
        assert result.model_dump(mode='json') == original
        assert adjustment is None
    else:
        with pytest.raises(SymmetryValidationError, match='BEMPP requires a closed body'):
            _apply_bempp_wall_default(request, 'bempp')
    assert request.model_dump(mode='json') == original


def test_composite_rejects_legacy_spelling_controls_and_conflicting_mouths() -> None:
    baseline = composite_payload(False, 'arc', True)
    old_marker = {**baseline, 'formula': 'OSSE-ARC'}
    with pytest.raises(ValidationError):
        DesignConfig.model_validate(old_marker)
    single = deepcopy(baseline)
    del single['axial_scale']
    with pytest.raises(ValidationError, match='write OSSE-ARC'):
        DesignConfig.model_validate(single)
    controls = deepcopy(baseline)
    controls['throat_adapter'] = design_payload('OSSE-ADAPTER-CONTROLS')['throat_adapter']
    with pytest.raises(ValidationError):
        DesignConfig.model_validate(controls)
    conflicting = deepcopy(baseline)
    conflicting['mesh']['mouth_roundover_radius_mm'] = 25
    with pytest.raises(ValidationError, match='conflict'):
        DesignConfig.model_validate(conflicting)


@pytest.mark.parametrize('field,value', [('a', 80), ('k', 0), ('scale', 11), ('axial_scale', .001)])
def test_invalid_carried_dimensions_are_refused(field: str, value: float) -> None:
    payload = composite_payload(True, 'roundover', True)
    payload[field] = value
    with pytest.raises(ValidationError):
        DesignConfig.model_validate(payload)


def test_serialization_reports_coupled_geometry_refusal_as_json() -> None:
    app = FastAPI()
    app.include_router(create_design_io_router(CadLinkStore(':memory:')))
    payload = composite_payload(True, 'roundover', True)
    payload.update(a=30, a0=40)
    response = TestClient(app).post('/api/design/serialize', json={'design': payload})
    assert response.status_code == 422
    assert 'opening angles' in response.json()['detail'][0]['msg'].lower()
    assert 'ctx' not in response.json()['detail'][0]
