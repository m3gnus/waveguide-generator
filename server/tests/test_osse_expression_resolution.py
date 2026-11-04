"""Native switches may freeze constants, never an angular editor sample."""
from copy import deepcopy
import json
import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.profile_common import eval_param
from hornlab_mesher.profile_sampling import build_point_grid_arrays
from hornlab_mesher.preview.api import build_preview_geometry

from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig, Expr
from server.design.textcfg import parse
from server.design_io.api import create_design_io_router
from server.preview.core import _recentred_preview_config, preview_options
from server.preview.translate import design_to_mesher_config


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(create_design_io_router(CadLinkStore(':memory:')))
    with TestClient(app) as client:
        yield client


def resolve(client, expressions, design=None):
    if design is None:
        design = {'formula': 'OSSE'}
        for path, expression in expressions.items():
            target = design
            parts = path.split('.')
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = expression
    return client.post('/api/design/resolve-expressions', json={'design': design, 'paths': list(expressions)})


@pytest.mark.parametrize('raw,dialect', [
    ('120', 'ath'), ('  +1.20e2  ', 'ath'), ('2 * 12.7', 'ath'),
    ('15 + 2*sin(pi/2)', 'ath'), ('20 + log(100)', 'ath'),
    ('20 + log(100)', 'wg-v1'), ('20 + ln(100)', 'wg-v1'),
])
def test_resolves_constants_with_the_geometry_owner_not_the_cached_sample(client, raw, dialect):
    expression = Expr.model_validate({'raw': raw, 'dialect': dialect})
    payload = {'L': expression.model_dump()}
    before = deepcopy(payload)
    # ATH expressions belong to a versioned import. Unversioned WG documents
    # use the dialect migration owned by DesignConfig, even for explicit Exprs.
    design = {'formula': 'OSSE', **payload}
    if dialect == 'ath':
        design['text_import_version'] = 'ath-2026-09a-v3'
    response = resolve(client, payload, design)
    assert response.status_code == 200
    assert response.json() == {'L': eval_param(expression.execution_text())}
    assert payload == before


@pytest.mark.parametrize('version', [None, 'ath-2026-08c-v1', 'ath-2026-09a-v2', 'ath-2026-09a-v3'])
@pytest.mark.parametrize('field', ['L', 'scale'])
@pytest.mark.parametrize('encoding', ['string', 'raw-object', 'cached-value'])
def test_wire_dialect_resolution_preserves_normal_preview_numbers_and_geometry(client, version, field, encoding):
    raw = '1 + log(100)' if field == 'scale' else '100 + log(100)'
    expected = (1 if field == 'scale' else 100) + (math.log(100) if version else 2)
    expression = raw if encoding == 'string' else {'raw': raw}
    if encoding == 'cached-value':
        expression['value'] = expected
    wire = {'formula': 'OSSE', 'L': 120, 's': 0,
            'mesh': {'angular_segments': 16, 'length_segments': 8, 'wall_thickness': 0}}
    if version is not None:
        wire['text_import_version'] = version
    wire[field] = expression
    original_wire = deepcopy(wire)
    original = DesignConfig.model_validate(wire)
    before_config = _recentred_preview_config(original)

    response = resolve(client, {field: None}, wire)
    assert response.status_code == 200
    assert response.json() == {field: expected}

    numeric = deepcopy(wire)
    numeric[field] = response.json()[field]
    after_config = _recentred_preview_config(DesignConfig.model_validate(numeric))
    if field == 'L':
        assert eval_param(build_geometry_params(before_config)[0]['L']) == expected
        assert build_geometry_params(after_config)[0]['L'] == expected
    before = build_preview_geometry(before_config, preview_options('coarse'))
    after = build_preview_geometry(after_config, preview_options('coarse'))
    assert len(before.surfaces) == len(after.surfaces) > 0
    for old, new in zip(before.surfaces, after.surfaces, strict=True):
        assert old.role == new.role
        for name in ('positions', 'normals', 'indices'):
            old_array, new_array = getattr(old, name), getattr(new, name)
            assert old_array.dtype == new_array.dtype and old_array.shape == new_array.shape
            assert old_array.tobytes() == new_array.tobytes(), name
    assert wire == original_wire


@pytest.mark.parametrize('formula', ['R-OSSE', 'ICW', 'OSSE-AXIAL', 'SOURCE-DISK'])
def test_resolution_requires_an_osse_root(client, formula):
    design = {'formula': formula}
    if formula == 'OSSE-AXIAL':
        design['axial_scale'] = 1
    response = resolve(client, {'scale': None}, design)
    assert response.status_code == 422
    assert response.json()['detail'] == 'Expression resolution requires an OSSE design. Choose OSSE under Family and try again.'


@pytest.mark.parametrize('path', ['L', 'a0', 'a', 'mesh.throat_resolution'])
def test_never_freezes_angular_expressions_even_with_an_editor_sample(client, path):
    response = resolve(client, {path: {'raw': '12 + sin(p)', 'value': 12}})
    assert response.status_code == 200
    assert response.json() == {path: None}


def test_does_not_treat_other_field_names_as_constant_variables(client):
    response = resolve(client, {'L': {'raw': '2*r0', 'value': 25.4}})
    assert response.status_code == 200
    assert response.json() == {'L': None}


def test_uses_the_executed_value_even_if_the_sidecar_differs_in_the_last_bit(client):
    response = resolve(client, {'L': {'raw': '120', 'value': 120.00000000000001}})
    assert response.json() == {'L': 120}


def test_angular_scale_is_reported_before_trying_to_build_missing_defaults(client):
    response = resolve(client, {'L': None, 'scale': {'raw': '1 + .1*sin(p)', 'value': 1}})
    assert response.status_code == 200
    assert response.json() == {'L': None, 'scale': None}


def test_scale_keeps_its_existing_structural_arithmetic(client):
    raw = '9007199254740993 - 9007199254740992 + 1'
    # The bounded structural evaluator rounds each operation; profile arithmetic
    # retains integer operands. Scale must retain its own existing result.
    expression = Expr.model_validate(raw)
    assert expression.constant_value() == 1 and eval_param(expression.execution_text()) == 2
    assert resolve(client, {'scale': expression.model_dump()}).json() == {'scale': 1}


@pytest.mark.parametrize('path', ['L', 'mesh.throat_resolution'])
def test_never_freezes_an_expression_if_legacy_scale_rounding_changes_its_number(client, path):
    original = {'formula': 'OSSE', 'scale': 1.2345678901234567}
    if '.' in path:
        original['mesh'] = {'throat_resolution': {'raw': '6 * 2'}}
    else:
        original[path] = {'raw': '60 * 2'}
    response = resolve(client, {path: None}, original)
    assert response.status_code == 200
    assert response.json() == {path: None}


@pytest.mark.parametrize('raw', ['unknown(1)', '1e999', '__import__("os")'])
def test_invalid_or_nonfinite_expressions_are_not_resolved(client, raw):
    assert resolve(client, {'L': {'raw': raw}}).status_code == 422


def test_ath_numeric_import_resolves_and_existing_geometry_and_save_stay_identical(client):
    text = 'OSSE = {\nL=60*2\nr0=12.7\na=40\na0=5*2\nk=1\ns=0\n}\nScale=1\nABEC.SimType=2\nMesh.AngularSegments=16\nMesh.LengthSegments=8\n'
    original = parse(text).design
    saved = original.model_dump_json()
    payload = {path: getattr(original.root, path).model_dump() for path in ['L', 'r0', 'a', 'a0', 'k', 'scale']}
    response = resolve(client, payload)
    assert response.status_code == 200
    resolved = response.json()
    assert resolved == {'L': 120, 'r0': 12.7, 'a': 40, 'a0': 10, 'k': 1, 'scale': 1}
    numeric = original.model_dump(mode='json')
    numeric.update(resolved)
    numeric = DesignConfig.model_validate(numeric)
    before = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(original))[0])
    after = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(numeric))[0])
    for name, value in before.items():
        if isinstance(value, np.ndarray):
            assert value.dtype == after[name].dtype and value.shape == after[name].shape
            assert value.tobytes() == after[name].tobytes(), name
    assert original.model_dump_json() == saved
    assert json.loads(saved)['L']['raw'] == '60*2'


@pytest.mark.parametrize('version', [None, 'ath-2026-08c-v1', 'ath-2026-09a-v3'])
@pytest.mark.parametrize('scale', [None, 2])
def test_absent_body_defaults_preserve_geometry_at_each_import_scale(client, version, scale):
    payload = {'formula': 'OSSE', 's': 0, 'scale': scale, 'mesh': {'angular_segments': 16, 'length_segments': 8}}
    if version:
        payload['text_import_version'] = version
    original = DesignConfig.model_validate(payload)
    paths = ['L', 'r0', 'a', 'a0', 'k'] + (['scale'] if scale is None else [])
    wire = original.model_dump(mode='json')
    response = resolve(client, dict.fromkeys(paths), wire)
    assert response.status_code == 200
    values = response.json()
    if scale == 2 and version in (None, 'ath-2026-08c-v1'):
        assert values['L'] is None and values['r0'] is None
    else:
        assert all(value is not None for value in values.values())
    numeric = DesignConfig.model_validate({**wire, **{key: value for key, value in values.items() if value is not None}})
    before = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(original))[0])
    after = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(numeric))[0])
    for name, value in before.items():
        if isinstance(value, np.ndarray):
            assert value.dtype == after[name].dtype and value.shape == after[name].shape
            assert value.tobytes() == after[name].tobytes(), name
    assert original.model_dump(mode='json') == wire


def test_core_import_reports_the_angular_coverage_instead_of_freezing_it(client):
    original = parse((Path(__file__).parent / 'fixtures/ath_core/core.cfg').read_text()).design
    wire = original.model_dump(mode='json')
    response = resolve(client, dict.fromkeys(['L', 'a', 'a0', 'r0', 'k', 'scale']), wire)
    assert response.status_code == 200
    assert response.json() == {'L': 260, 'a': None, 'a0': 1.2, 'r0': 25, 'k': None, 'scale': None}
    assert original.model_dump(mode='json') == wire
