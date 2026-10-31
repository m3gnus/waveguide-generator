"""Custom profile contracts, legacy bytes, and mesh-free server operations."""

from copy import deepcopy
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from pydantic import ValidationError
from scipy.optimize import brentq

from server.design.schema import DesignConfig, Expr
from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.freeform import mount_freeform
from server.preview.translate import design_to_mesher_config


LEGACY = json.loads((Path(__file__).parent / 'data/custom-profile-legacy.json').read_text())


@pytest.fixture
def design():
    return json.loads(LEGACY['freeform']['json'])


@pytest.fixture
def client():
    app = FastAPI()
    mount_freeform(app)
    with TestClient(app) as transport:
        yield transport


def post(client, operation, **payload):
    response = client.post('/api/design/freeform/' + operation, json={'content_key': 'revision-7', **payload})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['content_key'] == 'revision-7'
    return result


@pytest.mark.parametrize('family', ['freeform', 'lookup'])
def test_legacy_json_translation_and_canonical_text_keep_original_bytes(family):
    golden = LEGACY[family]
    design = DesignConfig.model_validate_json(golden['json'])
    assert design.model_dump_json(indent=2) + '\n' == golden['json']
    assert json.dumps(design_to_mesher_config(design), separators=(',', ':')) == golden['config']
    assert serialize(design) == golden['text']
    if family == 'lookup':
        assert serialize(parse(golden['json'])) == golden['json']
    assert serialize(parse(golden['text'])) == golden['text']


def test_explicit_default_join_keeps_legacy_translation(design):
    before = design_to_mesher_config(DesignConfig.model_validate(design))
    for profile in ('profile_h', 'profile_v'):
        for point in design[profile]['points']:
            point['join'] = 'smooth'
    assert design_to_mesher_config(DesignConfig.model_validate(design)) == before


@pytest.mark.parametrize(('index', 'fields', 'message'), [
    (0, {'handle_in': [-1, -1]}, 'first point'),
    (-1, {'handle_out': [1, 1]}, 'last point'),
    (0, {'join': 'corner', 'angle_out_deg': 20}, 'interior corner'),
    (-1, {'join': 'corner', 'angle_out_deg': 20}, 'interior corner'),
    (1, {'angle_out_deg': 20}, 'interior corner'),
    (1, {'join': 'crease'}, 'Input should be'),
    (1, {'handle_in': ['-1', 2]}, 'valid number'),
    (1, {'handle_in': [True, 2]}, 'valid number'),
    (1, {'handle_in': [float('nan'), 2]}, 'finite number'),
    (1, {'handle_out': [1, 2, 3]}, '2 items'),
])
def test_schema_refuses_invalid_positions_and_handle_types(design, index, fields, message):
    design['profile_h']['points'][index].update(fields)
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(design)


def test_schema_accepts_corners_handles_and_keeps_inactive_v(design):
    design['profile_h']['points'][1].update(join='corner', angle_out_deg=20, handle_in=[-2, -3], handle_out=[4, 5])
    design['shared_profile'] = True
    design['profile_v']['points'][0]['r'] = 99
    root = DesignConfig.model_validate(design).root
    assert root.profile_v.points[0].r.value == 99
    root.shared_profile = True
    design['shared_profile'] = False
    with pytest.raises(ValidationError, match='throat radii must match'):
        DesignConfig.model_validate(design)


def test_object_translation_scaling_and_shared_profile(design):
    design['scale'] = 2
    design['shared_profile'] = True
    design['profile_v']['points'][0]['r'] = 99
    design['profile_h']['points'][1].update(join='corner', angle_deg=20, angle_out_deg=50, handle_in=[-8, -3], handle_out=[10, 12])
    config = design_to_mesher_config(DesignConfig.model_validate(design))['profile']
    assert config['profileH'] == config['profileV']
    assert config['profileH']['points'][1] == {
        'z': 80, 'r': 60, 'join': 'corner', 'angleDeg': 20, 'angleOutDeg': 50,
        'handleIn': [-16, -6], 'handleOut': [20, 24],
    }
    assert config['profileH']['points'][0] == [0, 25.4]
    assert 'a0' not in config  # Existing build default stays 15.5.


def test_corner_text_round_trip_all_rows_endpoints_and_inactive_v(design):
    design['shared_profile'] = True
    for name in ('profile_h', 'profile_v'):
        profile = design[name]
        profile['points'][0].update(join='corner', handle_out=[5.25, 1.75], angle_deg=16)
        profile['points'][1].update(join='corner', angle_deg=25, angle_out_deg=45, handle_in=[-4, -3], handle_out=[7, 6])
        profile['points'][-1].update(join='corner', handle_in=[-10, -15], angle_deg=55)
    design['profile_v']['points'][0]['r'] = 13
    original = DesignConfig.model_validate(design)
    emitted = serialize(original)
    for token in ('Freeform.Shared = 1', 'Freeform.V.Inactive = 1', 'ThroatJoin = corner',
                  'MouthJoin = corner', 'ThroatHandle = 5.25,1.75', 'MouthHandle = -10.0,-15.0',
                  'a=25 aout=45 corner hin=-4.0,-3.0 hout=7.0,6.0'):
        assert token in emitted
    reopened = parse(emitted).design
    def numeric_semantics(value):
        if isinstance(value, dict):
            return {key: numeric_semantics(item) for key, item in value.items()
                    if key != 'raw' or value.get('value') is None}
        if isinstance(value, list):
            return [numeric_semantics(item) for item in value]
        return value
    assert numeric_semantics(reopened.model_dump(mode='json')) == numeric_semantics(original.model_dump(mode='json'))
    # The dotted key spellings are also read outside the profile blocks.
    dotted = emitted.replace('ThroatJoin = corner\n', '').replace('MouthJoin = corner\n', '')
    dotted += '\nFreeform.H.ThroatJoin = corner\nFreeform.H.MouthJoin = corner\nFreeform.V.ThroatJoin = corner\nFreeform.V.MouthJoin = corner\n'
    assert parse(dotted).design == reopened


@pytest.mark.parametrize('shared', [True, False])
def test_shared_text_flag_and_smooth_endpoint_keys(design, shared):
    design['shared_profile'] = shared
    text = serialize(DesignConfig.model_validate(design))
    assert f'Freeform.Shared = {int(shared)}' in text
    assert ('Freeform.V.Inactive = 1' in text) == shared
    assert parse(text).design.root.shared_profile is shared
    text += '\nFreeform.H.ThroatJoin = smooth\nFreeform.H.MouthJoin = smooth\n'
    assert parse(text).design.root.profile_h.points[0].join == 'smooth'


@pytest.mark.parametrize('row', ['40 30 corner', '40 30 25', '40 30 a=25', '40 30 corner aout=40'])
def test_old_positional_and_new_named_point_rows(design, row):
    text = serialize(DesignConfig.model_validate(design))
    text = text.replace('40 30', row)
    point = parse(text).design.root.profile_h.points[1]
    assert point.t.value == pytest.approx(1/3)
    if 'corner' in row:
        assert point.join == 'corner'


@pytest.mark.parametrize('row', ['40 30 bogus', '40 30 a=30 a=35', '40 30 corner corner',
                              '40 30 hin=1', '40 30 corner aout=90', '40 30 aout=40',
                              'corner 30 a=20', 'z=40 30 corner'])
def test_bad_text_rows_are_refused(design, row):
    text = serialize(DesignConfig.model_validate(design)).replace('40 30', row)
    with pytest.raises((TextConfigError, ValidationError)):
        parse(text)


@pytest.mark.parametrize('key', ['Freeform.Shared = 2', 'Freeform.V.Inactive = 0',
                               'Freeform.Shared = 0\nFreeform.V.Inactive = 1'])
def test_bad_shared_text_flags_are_refused(design, key):
    with pytest.raises(TextConfigError):
        parse(serialize(DesignConfig.model_validate(design)) + '\n' + key + '\n')


def independent_radii(curve, samples):
    result = []
    for z in samples:
        controls = next(np.array(s['bezier']) for s in curve['spans'] if s['bezier'][0][0] - 1e-12 <= z <= s['bezier'][-1][0] + 1e-12)
        def value(t):
            return (1-t)**3*controls[0] + 3*(1-t)**2*t*controls[1] + 3*(1-t)*t*t*controls[2] + t**3*controls[3]
        t = brentq(lambda t: value(t)[0] - z, 0, 1, xtol=1e-15) if controls[0][0] < z < controls[-1][0] else (0 if z <= controls[0][0] else 1)
        result.append(value(t)[1])
    return np.array(result)


def test_curves_shape_design_units_and_no_mesh(client, design, monkeypatch):
    import hornlab_mesher.config_builder as builder
    def forbidden(*args, **kwargs):
        pytest.fail('Curve APIs must not build a mesh or grid')
    monkeypatch.setattr(builder, 'build_point_grid', forbidden)
    monkeypatch.setattr(builder, 'build_geometry_params', forbidden)
    original = post(client, 'curves', design=design)
    design['scale'] = 3
    assert post(client, 'curves', design=design) == original
    assert set(original) == {'content_key', 'H', 'V', 'warnings'}
    assert original['H']['anchors'][1]['t'] == pytest.approx(1/3)
    assert original['H']['anchors'][1]['z'] == 40
    assert len(original['H']['spans']) == 2
    assert len(original['H']['samples']) == 127
    assert original['H']['anchors'][0]['handleIn'] is None
    exact = post(client, 'insert', design=design, plane='H', z_mm=21.407)
    post(client, 'compare', before=design, after=exact['design'])


def test_exact_insert_whole_curve_survives_json_and_text_reload(client, design):
    before = post(client, 'curves', design=design)
    result = post(client, 'insert', design=design, plane='H', z_mm=21.407, mode='exact')
    assert result['deviation']['curve_deviation_mm'] < 1e-9
    updated = DesignConfig.model_validate(result['design'])
    assert len(updated.root.profile_h.points) == 4
    assert result['design']['profile_v'] == design['profile_v']
    assert updated.root.profile_h.points[1].t.value * 120 == pytest.approx(21.407, abs=1e-12)
    for saved in (updated.model_dump_json(), serialize(updated)):
        reloaded = (DesignConfig.model_validate_json(saved).model_dump(mode='json')
                    if saved.startswith('{') else parse(saved).semantic_data())
        after = post(client, 'curves', design=reloaded)
        z = np.linspace(0, 120, 401)
        np.testing.assert_allclose(independent_radii(before['H'], z), independent_radii(after['H'], z), atol=1e-9, rtol=0)
        report = post(client, 'compare', before=design, after=reloaded)
        assert report['H']['curve_deviation_mm'] < 1e-9
        assert report['V']['curve_deviation_mm'] == 0


@pytest.mark.parametrize('shared', [None, True])
@pytest.mark.parametrize('length_raw', ['60 * 2', '60*log(100)', '60*ln(e**2)'])
def test_length_expression_keeps_curves_handles_and_omissions_after_edit(client, design, shared, length_raw):
    design['length'] = length_raw
    design['shared_profile'] = shared
    for profile in ('profile_h', 'profile_v'):
        design[profile]['throat_angle_deg'] = None
        design[profile]['mouth_angle_deg'] = None
    if shared:
        design['profile_v']['points'][0]['r'] = 13
    authored = DesignConfig.model_validate(design)
    first = parse(serialize(authored)).design
    assert first.root.length.raw == length_raw
    assert first.root.shared_profile is shared
    for before, after in zip((authored.root.profile_h, authored.root.profile_v),
                             (first.root.profile_h, first.root.profile_v)):
        assert after.points[0].r.constant_value() == before.points[0].r.constant_value()
        assert after.points[-1].r.constant_value() == before.points[-1].r.constant_value()
        assert after.throat_angle_deg is before.throat_angle_deg is None
        assert after.mouth_angle_deg is before.mouth_angle_deg is None
    before_curve = post(client, 'curves', design=design)
    inserted = post(client, 'insert', design=first.model_dump(mode='json'), plane='H', z_mm=21.407)
    edited = DesignConfig.model_validate(inserted['design'])
    # A further authored expression edit must remain executable source on disk.
    edited.root.length = Expr.model_validate('40 * 3')
    restored = parse(serialize(edited)).design
    assert restored.root.length.raw == '40 * 3'
    assert restored.root.shared_profile is shared
    for source, saved in zip(edited.root.profile_h.points, restored.root.profile_h.points):
        assert saved.handle_in == source.handle_in
        assert saved.handle_out == source.handle_out
        assert (saved.join or 'smooth') == (source.join or 'smooth')
    after_curve = post(client, 'curves', design=restored.model_dump(mode='json'))
    for axis in ('H', 'V'):
        np.testing.assert_allclose(
            independent_radii(before_curve[axis], np.linspace(0, 120, 401)),
            independent_radii(after_curve[axis], np.linspace(0, 120, 401)), atol=1e-9, rtol=0)


@pytest.mark.parametrize(('index', 'raw'), [(0, '25.4 / 2'), (-1, '70 * 2')])
def test_radius_expression_mesher_refusal_is_unchanged_by_cfg_round_trip(client, design, index, raw):
    # The mesher currently requires literal radii. Saving must neither erase the
    # formula to make it executable nor introduce a different geometry request.
    authored = parse(serialize(DesignConfig.model_validate(design))).design
    for profile in (authored.root.profile_h, authored.root.profile_v):
        profile.points[index].r = Expr.model_validate(raw)
    reopened = parse(serialize(authored)).design
    assert design_to_mesher_config(reopened) == design_to_mesher_config(authored)
    refusals = [client.post('/api/design/freeform/curves', json={
        'content_key': 'revision-7', 'design': item.model_dump(mode='json'),
    }) for item in (authored, reopened)]
    for response in refusals:
        assert response.status_code == 422
        assert raw in response.json()['detail']
    assert refusals[0].json() == refusals[1].json()


def test_repeated_precise_inserts_survive_text_round_trip(client, design):
    edited = design
    for z in (21.40731234567891, 75.25398765432109):
        edited = post(client, 'insert', design=edited, plane='H', z_mm=z)['design']
    restored = parse(serialize(DesignConfig.model_validate(edited))).semantic_data()
    original_points = DesignConfig.model_validate(edited).root.profile_h.points
    saved_points = DesignConfig.model_validate(restored).root.profile_h.points
    assert len(saved_points) == len(original_points)
    for original, saved in zip(original_points, saved_points):
        assert saved.t.constant_value() == pytest.approx(original.t.constant_value(), abs=1e-14)
        assert saved.r.constant_value() == pytest.approx(original.r.constant_value(), abs=1e-11)
        assert saved.handle_in == original.handle_in
        assert saved.handle_out == original.handle_out
    comparison = post(client, 'compare', before=design, after=restored)
    assert comparison['H']['curve_deviation_mm'] < 1e-9
    assert comparison['H']['evaluator_deviation_mm'] is not None


@pytest.mark.parametrize('positions', [
    (1e-8, 39.9999999, 119.9999999),
    (21.40731234567891, 21.40732234567891, 75.25398765432109),
])
@pytest.mark.parametrize('corners', [False, True])
def test_exact_insert_near_anchors_survives_repeated_text_saves(client, design, positions, corners):
    if corners:
        for point in design['profile_h']['points']:
            point['join'] = 'corner'
    before = post(client, 'curves', design=design)['H']
    edited = design
    for z in positions:
        edited = post(client, 'insert', design=edited, plane='H', z_mm=z)['design']
        edited = parse(serialize(DesignConfig.model_validate(edited))).semantic_data()
        after = post(client, 'curves', design=edited)['H']
        samples = np.unique(np.r_[np.linspace(0, 120, 401), positions])
        np.testing.assert_allclose(
            independent_radii(before, samples), independent_radii(after, samples), atol=1e-9, rtol=0)
    report = post(client, 'compare', before=design, after=edited)
    assert report['H']['curve_deviation_mm'] < 1e-9


def test_refit_and_compare_report_reviewers_deviation(client, design):
    result = post(client, 'insert', design=design, plane='H', z_mm=21.407, mode='refit')
    assert result['deviation']['curve_deviation_mm'] == pytest.approx(1.20667, abs=1e-5)
    assert result['deviation']['curve_at_z_mm'] == pytest.approx(70.4941, abs=0.01)
    inserted = result['design']['profile_h']['points'][1]
    assert 'handle_in' not in inserted and 'handle_out' not in inserted
    report = post(client, 'compare', before=design, after=result['design'])
    assert report['H']['curve_deviation_mm'] == result['deviation']['curve_deviation_mm']
    assert report['V']['curve_deviation_mm'] == 0


def test_refit_after_exact_insert_releases_all_handles_and_uses_production_radius(client, design):
    from hornlab_mesher import freeform_evaluate_profile

    exact = post(client, 'insert', design=design, plane='H', z_mm=21.407)['design']
    assert any('handle_in' in point or 'handle_out' in point for point in exact['profile_h']['points'])
    params = design_to_mesher_config(DesignConfig.model_validate(exact))['profile']
    radius = float(freeform_evaluate_profile(params, 'H', 75.25))
    refit = post(client, 'insert', design=exact, plane='H', z_mm=75.25, mode='refit')
    points = refit['design']['profile_h']['points']
    assert all('handle_in' not in point and 'handle_out' not in point for point in points)
    assert points[3]['r']['value'] == pytest.approx(radius, abs=1e-12)
    assert refit['design']['profile_v'] == exact['profile_v']
    assert refit['deviation']['curve_deviation_mm'] > 0
    assert refit['deviation']['evaluator_deviation_mm'] > 0


def test_exact_insert_preserves_authored_point_values(client, design):
    design['profile_h']['points'][1]['r'] = {'value': 30.0, 'raw': '30.000'}
    design['profile_h']['points'][1]['t'] = {'value': 1 / 3, 'raw': '1 / 3'}
    result = post(client, 'insert', design=design, plane='H', z_mm=21.407)['design']
    retained = result['profile_h']['points'][2]
    assert retained['r'] == design['profile_h']['points'][1]['r']
    assert retained['t'] == design['profile_h']['points'][1]['t']


def test_exact_insert_reports_curve_and_production_evaluator_separately(client, design):
    result = post(client, 'insert', design=design, plane='H', z_mm=21.407)
    summary = result['deviation']
    assert summary['curve_deviation_mm'] < 1e-9
    # Exact subdivision switches the legacy sampled inverse to the extended
    # evaluator; preserving the cubic does not promise identical radii.
    assert summary['evaluator_deviation_mm'] > summary['curve_deviation_mm']
    compared = post(client, 'compare', before=design, after=result['design'])
    assert compared['H'] == summary


@pytest.mark.parametrize('shared', [True, False])
def test_vertical_insert_and_shared_redirect_keep_stored_v(client, design, shared):
    design['shared_profile'] = shared
    if shared:
        design['profile_v']['points'][0]['r'] = 99
    result = post(client, 'insert', design=design, plane='V', z_mm=21.407)
    field = 'profile_h' if shared else 'profile_v'
    retained = 'profile_v' if shared else 'profile_h'
    assert len(result['design'][field]['points']) == 4
    assert result['design'][retained] == DesignConfig.model_validate(design).model_dump(mode='json')[retained]
    assert result['deviation']['curve_deviation_mm'] < 1e-9
    resolved = post(client, 'curves', design=result['design'])
    if shared:
        assert resolved['H'] == resolved['V']


@pytest.mark.parametrize('plane', ['H', 'V'])
def test_mesher_error_names_plane_span_and_z(client, design, plane):
    design['profile_' + plane.lower()]['points'][0]['handle_out'] = [-20, 5]
    response = client.post('/api/design/freeform/curves', json={'design': design, 'content_key': 'bad'})
    assert response.status_code == 422
    assert 'profile' + plane in response.json()['detail']
    assert 'span' in response.json()['detail'] and 'z=' in response.json()['detail']


def test_smooth_handle_collinearity_error_is_reported_by_mesher(client, design):
    design['profile_h']['points'][1].update(handle_in=[-3, -1], handle_out=[3, 2])
    response = client.post('/api/design/freeform/curves', json={'design': design, 'content_key': 'bad'})
    assert response.status_code == 422
    assert 'profileH' in response.json()['detail']
    assert 'collinear' in response.json()['detail']
    assert response.json()['content_key'] == 'bad'


@pytest.mark.parametrize('z', [-1, 0, 40, 120, 130])
@pytest.mark.parametrize('mode', ['exact', 'refit'])
def test_insert_bad_positions_are_422(client, design, z, mode):
    response = client.post('/api/design/freeform/insert', json={'design': design, 'plane': 'V', 'z_mm': z, 'mode': mode, 'content_key': 'bad'})
    assert response.status_code == 422
    assert 'profileV' in response.json()['detail']
    assert response.json()['content_key'] == 'bad'


def test_wrong_family_and_bad_insert_are_422(client, design):
    response = client.post('/api/design/freeform/curves', json={'design': {'formula': 'OSSE'}, 'content_key': 'bad'})
    assert response.status_code == 422
    response = client.post('/api/design/freeform/insert', json={'design': design, 'plane': 'X', 'z_mm': 20, 'mode': 'invalid', 'content_key': 'bad'})
    assert response.status_code == 422


def test_comparison_summarizes_overlapping_extents_without_sample_grid(client, design):
    changed = deepcopy(design)
    changed['length'] = 121
    response = client.post('/api/design/freeform/compare', json={'before': design, 'after': changed, 'content_key': 'revision-7'})
    assert response.status_code == 200, response.text
    assert len(response.content) < 4000
    for plane in ('H', 'V'):
        summary = response.json()[plane]
        assert 'samples' not in summary
        assert summary['sample_count'] >= 16385
        assert summary['extents'] == {'before': [0, 120], 'after': [0, 121],
                                      'extents_equal': False, 'overlap': [0, 120],
                                      'comparison_domain': 'overlap_only'}
        assert summary['curve_deviation_mm'] > 0
        assert summary['evaluator_deviation_mm'] > 0
        assert 'not a certified maximum' in summary['method']['estimate']


def test_request_validation_errors_echo_content_key(client, design):
    response = client.post('/api/design/freeform/insert', json={
        'design': design, 'plane': 'invalid', 'z_mm': 20, 'content_key': 'stale-key'})
    assert response.status_code == 422
    assert response.json()['content_key'] == 'stale-key'
    assert isinstance(response.json()['detail'], list)


@pytest.mark.parametrize('fields', [
    {'join': 'corner'}, {'handle_out': [4, 2]},
    {'join': 'corner', 'angle_out_deg': 35},
])
def test_each_extended_feature_selects_object_point_form(design, fields):
    design['profile_h']['points'][1].update(fields)
    translated = design_to_mesher_config(DesignConfig.model_validate(design))['profile']['profileH']['points']
    assert isinstance(translated[1], dict)
    assert isinstance(translated[0], list) and isinstance(translated[-1], list)


def test_absent_throat_angle_uses_same_default_for_exact_insert(client, design):
    for plane in ('profile_h', 'profile_v'):
        design[plane]['throat_angle_deg'] = None
    result = post(client, 'insert', design=design, plane='H', z_mm=21.407)
    assert result['deviation']['curve_deviation_mm'] < 1e-9
    deviation = post(client, 'compare', before=design, after=result['design'])
    assert deviation['H']['curve_deviation_mm'] < 1e-9


def test_text_inactive_marker_alone_and_qualified_endpoint_handles(design):
    text = serialize(DesignConfig.model_validate(design)) + '\nFreeform.V.Inactive = 1\n'
    text += 'Freeform.H.ThroatHandle = 2,1\nFreeform.H.MouthHandle = -2,-1\n'
    text += 'Freeform.V.ThroatHandle = 3,1\nFreeform.V.MouthHandle = -3,-1\n'
    parsed = parse(text).design.root
    assert parsed.shared_profile is True
    assert parsed.profile_h.points[0].handle_out == (2, 1)
    assert parsed.profile_h.points[-1].handle_in == (-2, -1)
    assert parsed.profile_v.points[0].handle_out == (3, 1)
    assert parsed.profile_v.points[-1].handle_in == (-3, -1)


def test_insert_limit_is_422(client, design):
    points = [{'t': i / 63, 'r': 12.7 + i * 2} for i in range(64)]
    for plane in ('profile_h', 'profile_v'):
        design[plane]['points'] = points
        design[plane]['throat_angle_deg'] = None
        design[plane]['mouth_angle_deg'] = None
    response = client.post('/api/design/freeform/insert', json={
        'design': design, 'plane': 'V', 'z_mm': 21.407, 'content_key': 'too-many'})
    assert response.status_code == 422
    assert 'profileV' in response.json()['detail'] and '64' in response.json()['detail']
    assert response.json()['content_key'] == 'too-many'


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_insert_position_is_422(client, design, value):
    payload = json.dumps({'design': design, 'plane': 'H', 'z_mm': 'replace-me', 'content_key': 'nonfinite'})
    payload = payload.replace('"replace-me"', value)
    response = client.post('/api/design/freeform/insert', content=payload, headers={'content-type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['content_key'] == 'nonfinite'


def test_boolean_insert_position_is_refused(client, design):
    response = client.post('/api/design/freeform/insert', json={
        'design': design, 'plane': 'H', 'z_mm': True, 'content_key': 'boolean'})
    assert response.status_code == 422
    assert response.json()['content_key'] == 'boolean'


def test_corner_query_straight_spans_and_join_change_comparison(client, design):
    changed = deepcopy(design)
    for plane in ('profile_h', 'profile_v'):
        for point in changed[plane]['points']:
            point['join'] = 'corner'
    curve = post(client, 'curves', design=changed)
    assert all(span['straight'] for span in curve['H']['spans'])
    assert all(anchor['join'] == 'corner' for anchor in curve['H']['anchors'])
    result = post(client, 'compare', before=design, after=changed)
    assert result['H']['curve_deviation_mm'] > 0
    assert result['H'] == result['V']
