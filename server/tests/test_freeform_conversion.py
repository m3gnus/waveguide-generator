"""S4 conversion: generated geometry is the judge, without browser fallback."""
from copy import deepcopy
import math
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.design.textcfg import parse
from server.jobs.operation_progress import mount_operation_progress
from server.jobs.freeform import mount_freeform
from server.jobs.freeform_conversion import compare_generated_designs, convert_design, conversion_notice, legacy_lookup_source


@pytest.fixture
def client():
    app = FastAPI()
    mount_freeform(app)
    mount_operation_progress(app)
    with TestClient(app) as transport:
        yield transport


def convert(payload):
    return convert_design(legacy_lookup_source(payload) if payload.get('formula') == 'LOOKUP' else DesignConfig.model_validate(payload), 'source-revision')


def assert_honest(result):
    report = result.report
    maximum = max(report.wall.max_deviation_mm, report.mouth.max_deviation_mm, report.axial_extent_deviation_mm)
    assert maximum <= .5
    if result.verdict == 'match':
        assert maximum <= .01
        assert abs(report.source_cap_angle_before_deg-report.source_cap_angle_after_deg) <= .01
        assert not report.features
    assert result.design.formula == 'FREEFORM'
    assert 2 <= len(result.design.root.profile_h.points) <= 64
    assert 2 <= len(result.design.root.profile_v.points) <= 64
    assert 2 <= len(result.design.root.cross_sections) <= 32
    assert report.method.azimuths_deg == np.arange(0,360,2.5).tolist()
    assert {d.azimuth_deg for d in report.off_axis} == set(report.method.azimuths_deg)-{0,90}
    assert report.method.limits
    return maximum


def test_circular_osse_matches_scaled_generated_wall_and_preserves_settings(client):
    source = {'formula': 'OSSE', 'L': 120, 'a0': 15.5, 'a': 60, 'r0': 12.7, 'scale': 2,
              'mesh': {'quadrants': 1, 'wall_thickness': 0},
              'source': {'shape': 2, 'velocity': {'value': 1, 'raw': '2/2'}},
              'simulation': {'f1': 250, 'f2': 16000}, 'output': {'stl': 1},
              'enclosure': {'depth': 300}, 'extra_keys': {'Design.Name': 'keep me'}}
    before = deepcopy(source)
    response = client.post('/api/design/freeform/convert', json={'design': source, 'content_key': 'success-key'},
                           headers={'X-WG-Operation-Id': '00000000-0000-0000-0000-000000000004'})
    assert response.status_code == 200, response.text
    assert response.json()['content_key'] == 'success-key'
    from server.jobs.freeform_conversion import ConvertResponse
    result = ConvertResponse.model_validate(response.json())
    assert result.verdict == 'match'
    assert assert_honest(result) <= .01
    assert result.report.wall.z_mm <= 240
    assert result.design.root.shared_profile is True
    original = DesignConfig.model_validate(source).root
    for field in ('scale','mesh','source','simulation','enclosure','output','extra_keys'):
        assert getattr(result.design.root,field) == getattr(original,field)
    assert source == before
    assert 'sample' in response.headers['X-WG-Operation-Stages']


def test_non_circular_rosse_has_stations_and_honest_off_axis_deviation():
    result = convert({'formula':'R-OSSE', 'tmax':.7, 'cross_section':{'exponent':2.5,'aspect_ratio':1.1}})
    assert result.verdict == 'approximate'
    assert result.design.root.shared_profile is False
    assert len(result.design.root.cross_sections) > 2
    assert max(d.max_deviation_mm for d in result.report.off_axis) > .01
    assert_honest(result)


def test_equal_hv_with_non_circular_section_is_never_shared():
    # Equality at two axes is not evidence of rotational symmetry.
    result = convert({'formula':'OSSE','cross_section':{'exponent':2.1,'aspect_ratio':1.0}})
    assert result.design.root.shared_profile is False
    assert_honest(result)


def test_lookup_rows_are_corners_and_can_match_including_cap():
    rows = [[0,12.7],[13,19.2],[50,40],[120,140]]
    result = convert({'formula':'LOOKUP','lookup_profile':rows, 'a0': math.degrees(math.atan(.5))})
    assert result.verdict == 'approximate'
    assert_honest(result)
    points = result.design.root.profile_h.points
    assert len(points) == len(rows)
    assert all(p.join == 'corner' for p in points)
    assert [p.t.constant_value()*120 for p in points] == pytest.approx([row[0] for row in rows])


def test_lookup_cap_shape_is_preserved_explicitly():
    result = convert({'formula':'LOOKUP','lookup_profile':[[0,12.7],[120,72.7]]})
    assert result.report.wall.max_deviation_mm < .01
    assert result.report.source_cap_angle_before_deg == 0
    assert result.report.source_cap_angle_after_deg == 0
    assert result.verdict == 'approximate'


def test_large_lookup_is_approximate_or_refused_without_truncating(client):
    rows = [[float(i), 20+i*.2+.02*(i%2)] for i in range(90)]
    result = convert({'formula':'LOOKUP','lookup_profile':rows})
    assert result.verdict == 'approximate'
    assert result.design.root.length.constant_value() == 89
    assert_honest(result)
    from hornlab_mesher.errors import UserRefusal
    with pytest.raises(UserRefusal, match='0.5 mm'):
        convert({'formula':'LOOKUP', 'lookup_profile':[[float(i),20+i*.2+2*(i%2)] for i in range(90)]})



@pytest.mark.parametrize('payload, message', [
    ({'formula':'OSSE','L':-10}, 'Enter positive Horn length'),
])
def test_refusal_is_actionable_and_echoes_key(client,payload,message):
    response = client.post('/api/design/freeform/convert',json={'design':payload,'content_key':'refusal-key'})
    assert response.status_code == 422, response.text
    assert response.json()['content_key'] == 'refusal-key'
    assert message in response.json()['detail']


def test_freeform_and_schema_refusals_echo_key(client):
    freeform = convert({'formula':'OSSE'}).design.model_dump(mode='json')
    for design, expected in [(freeform,'already a Custom profile'), ({'formula':'unknown'},'union_tag_invalid')]:
        response = client.post('/api/design/freeform/convert', json={'design':design,'content_key':'error-key'})
        assert response.status_code == 422
        assert response.json()['content_key'] == 'error-key'
        assert expected in str(response.json()['detail'])


@pytest.mark.parametrize('error_name',['RuntimeError', 'ValueError', 'InternalInvariantError', 'chained invariant'])
def test_program_faults_are_not_presented_as_user_refusals(client,monkeypatch,error_name):
    import server.jobs.freeform_conversion as conversion
    from hornlab_mesher.errors import ConfigError, InternalInvariantError
    from server.preview.errors import public_error_message
    error_type = {'RuntimeError': RuntimeError, 'ValueError': ValueError,
                  'InternalInvariantError': InternalInvariantError, 'chained invariant': ConfigError}[error_name]
    def broken(design):
        if error_name == 'chained invariant':
            try:
                raise InternalInvariantError('deliberate internal fault')
            except InternalInvariantError as cause:
                raise ConfigError('deliberate internal fault') from cause
        raise error_type('deliberate internal fault')
    monkeypatch.setattr(conversion,'_sample',broken)
    with pytest.raises(error_type,match='deliberate internal fault') as raised:
        client.post('/api/design/freeform/convert',json={'design':{'formula':'OSSE'},'content_key':'fault'})
    if error_name in ('InternalInvariantError', 'chained invariant'):
        assert public_error_message(raised.value) is None


@pytest.mark.parametrize('name',['osse_scale.cfg','osse_no_simtype.cfg','osse_baffle_polars_unstated.cfg'])
def test_three_existing_ath_corpus_designs_report_maximum(name,record_property):
    source = parse((Path(__file__).parent/'fixtures/ath_import_defaults'/name).read_text()).design
    result = convert_design(source,'ath-corpus')
    maximum = assert_honest(result)
    record_property('max_deviation_mm',maximum)
    print(f'{name}: {result.verdict}, max deviation {maximum:.8f} mm')
    assert result.report.source_cap_angle_before_deg == pytest.approx(10)


def test_comparison_core_reuses_generated_geometry_and_detects_off_axis_changes():
    source = DesignConfig.model_validate({'formula':'OSSE'})
    changed = DesignConfig.model_validate({'formula':'OSSE','cross_section':{'exponent':4}})
    report = compare_generated_designs(source,changed)
    assert report.H.max_deviation_mm < 1e-10
    assert report.V.max_deviation_mm < 1e-10
    assert report.wall.max_deviation_mm > 1
    assert report.mouth.max_deviation_mm > 1


def test_ath_roundover_is_named_in_report_and_removed_lip(client):
    source = parse('OSSE={\nL=80\nr0=8\na=35\na0=5\ns=.5\n}\nABEC.SimType=2\n'
                   'Mesh.AngularSegments=16\nMesh.LengthSegments=16\n'
                   'Mesh.Roundover={\nRadius=3\nSegments=3\n}\n').design
    payload = source.model_dump(mode='json')
    payload['mesh'].pop('ath_roundover')
    after = DesignConfig.model_validate(payload)
    report = compare_generated_designs(source,after)
    assert any(feature.name == 'ATH Mesh.Roundover' for feature in report.features)
    assert report.wall.max_deviation_mm > .01
    response = client.post('/api/design/freeform/convert',json={'design':source.model_dump(mode='json'),'content_key':'lip'})
    assert response.status_code == 200, response.text
    assert response.json()['verdict'] == 'approximate'
    assert any(feature['name'] == 'Rolled-back lip removed' for feature in response.json()['report']['features'])


@pytest.fixture(scope='module')
def default_rosse_conversion():
    return convert({'formula': 'R-OSSE'})


def test_default_rosse_converts_through_fold_and_reports_removed_lip(default_rosse_conversion):
    from server.jobs.freeform_conversion import _sample, _meridians, _end_angle, _kept_wall
    result = default_rosse_conversion
    original = _meridians(_sample(DesignConfig.model_validate({'formula': 'R-OSSE'})))
    kept, _ = _kept_wall(original)
    assert result.verdict == 'approximate'
    assert_honest(result)
    assert result.design.root.length.constant_value() == pytest.approx(original[0, :, 0].max())
    assert result.design.root.profile_h.mouth_angle_deg.constant_value() == pytest.approx(_end_angle(kept[0], mouth=True))
    assert result.design.root.profile_h.mouth_angle_deg.constant_value() > 89
    lip = next(f for f in result.report.features if f.name == 'Rolled-back lip removed')
    assert lip.change == 'removed'
    assert float(re.search(r'length along the horn was ([\d.]+)', lip.message)[1]) == pytest.approx(5.21551, abs=1e-5)
    assert float(re.search(r'radial extent \(distance outwards\) was ([\d.]+)', lip.message)[1]) == pytest.approx(11.2769, abs=1e-4)
    assert float(re.search(r'around the horn: ([\d.]+)', lip.message)[1]) == pytest.approx(12.4332, abs=1e-4)
    assert result.report.wall.max_deviation_mm == pytest.approx(.009511758, abs=1e-5)
    assert result.report.mouth.max_deviation_mm < .01
    assert result.report.axial_extent_deviation_mm == 0
    # Measuring the entire original lip would violate the kept-wall budget.
    assert compare_generated_designs(DesignConfig.model_validate({'formula': 'R-OSSE'}), result.design,
                                     include_construction=False).wall.max_deviation_mm > .5
    notice = conversion_notice(result)
    assert lip.message in notice
    assert 'kept' in notice and 'approximate' in notice
    visible, details = notice.split('\n\n', 1)
    assert visible.startswith('Close to the original — the kept inside of the horn')
    assert 'lip at the mouth is removed (5.2 mm long)' in visible
    assert 'Measurements include Scale' in details
    assert notice.count('Measurements include Scale') == 1
    assert '0.01 mm' in details and '0.5 mm' in details
    assert 'H:' in details and 'z =' in details


def test_fold_cut_retains_each_meridians_own_maximum_and_measures_physical_lip():
    from server.jobs.freeform_conversion import _kept_wall, _lip_change
    original = np.array([[[0, 1], [10, 2], [9, 4], [8, 6]],
                         [[0, 1], [8, 2], [12, 4], [11, 7]]], dtype=float)
    kept, lips = _kept_wall(original)
    assert [len(rows) for rows in kept] == [2, 3]
    np.testing.assert_array_equal([rows[-1] for rows in kept], [[10, 2], [12, 4]])
    assert 'length along the horn was 2 mm. Its radial extent (distance outwards) was 4 mm' in _lip_change(lips, np.array([rows[-1] for rows in kept])).message
    unchanged, empty = _kept_wall(original[:, :2])
    np.testing.assert_array_equal(unchanged, original[:, :2])
    assert _lip_change(empty, original[:, 1]) is None


def test_fold_before_later_maximum_still_refuses():
    from hornlab_mesher.errors import UserRefusal
    from server.jobs.freeform_conversion import _kept_wall
    with pytest.raises(UserRefusal, match='wall doubles back'):
        _kept_wall(np.array([[[0, 1], [2, 3], [1, 4], [3, 5]]]))


@pytest.mark.parametrize('mounting', ['infinite-baffle', 'enclosure'])
def test_lip_dependent_baffle_and_enclosure_changes_are_reported(default_rosse_conversion, mounting):
    from server.jobs.freeform_conversion import _construction_changes
    before = DesignConfig.model_validate({'formula': 'R-OSSE', 'simulation': {'sim_type': 'infinite-baffle' if mounting == 'infinite-baffle' else 'freestanding'},
                                          'enclosure': {'depth': 300 if mounting == 'enclosure' else 0}})
    payload = default_rosse_conversion.design.model_dump(mode='json')
    payload['simulation'] = before.root.simulation.model_dump(mode='json')
    payload['enclosure'] = before.root.enclosure.model_dump(mode='json')
    features = _construction_changes(before, DesignConfig.model_validate(payload), removed_lip=True)
    assert any(f.name == 'Baffle opening' and 'mm' in f.message for f in features)
    if mounting == 'enclosure':
        assert any(f.name == 'Enclosure / baffle bounds' and '→' in f.message for f in features)


def test_morph_is_baked_with_stations_and_reported_as_approximate():
    result = convert({'formula':'OSSE','morph':{'target_shape':3,'target_exponent':2.3,
        'target_width':400,'target_height':350,'fixed_part':.5}})
    assert result.verdict == 'approximate'
    assert result.design.root.morph.target_shape.constant_value() == 0
    assert len(result.design.root.cross_sections) > 2
    assert any(feature.name == 'Mouth morph' for feature in result.report.features)
    assert_honest(result)


def test_icw_refines_generated_wall_before_accepting_conversion(client, record_property):
    response = client.post('/api/design/freeform/convert',json={'design':{'formula':'ICW'},'content_key':'icw'})
    assert response.json()['content_key'] == 'icw'
    assert response.status_code == 200, response.text
    from server.jobs.freeform_conversion import ConvertResponse
    result = ConvertResponse.model_validate(response.json())
    maximum = assert_honest(result)
    assert maximum < .47
    assert 23 < len(result.design.root.profile_h.points) <= 64
    assert result.report.method.axial_segments_before >= 4096
    record_property('max_deviation_mm', maximum)
    print(f'Default ICW: {maximum:.8f} mm, {len(result.design.root.profile_h.points)} anchors')
    print('ICW construction:', [feature.message for feature in result.report.features])


def test_ath_rear_plane_change_is_measured_and_prevents_match():
    source = parse('OSSE={\nL=120\na=40\na0=15.5\n}\nABEC.SimType=2\nMesh.WallThickness=5\n').design
    result = convert_design(source, 'rear')
    assert result.report.wall.max_deviation_mm <= .01
    assert result.verdict == 'approximate'
    rear = next(feature for feature in result.report.features if feature.name == 'Rear construction')
    assert float(re.search(r'length: (-?[\d.]+)', rear.message)[1]) == pytest.approx(-6.33652, abs=1e-4)
    assert '→ -5 mm' in rear.message
    assert float(re.search(r'rear tip: ([\d.]+)', rear.message)[1]) == pytest.approx(1.33652, abs=1e-4)
    visible, details = conversion_notice(result).split('\n\n', 1)
    assert visible.startswith('Close to the original — the inside of the horn changes by at most')
    assert 'rear edge or rear tip moves by up to 1.3 mm' in visible
    assert 'The outside of the horn changes by up to 0.500 mm' in visible
    assert rear.message in details


def test_comparison_measures_outer_wall_and_rear_changes_with_unchanged_bore():
    source = DesignConfig.model_validate({'formula': 'OSSE', 'mesh': {'wall_thickness': 3}})
    changed = DesignConfig.model_validate({'formula': 'OSSE', 'mesh': {'wall_thickness': 5}})
    report = compare_generated_designs(source, changed)
    assert report.wall.max_deviation_mm == 0
    assert {'Outer wall', 'Rear construction'} <= {feature.name for feature in report.features}
    assert any('→ -5 mm' in feature.message for feature in report.features)


@pytest.mark.parametrize('scale', [1, 1.5])
def test_ath_natural_log_expression_keeps_value_after_family_change(scale):
    from server.preview.translate import design_to_mesher_config
    from server.design.textcfg import serialize
    from hornlab_mesher.profile_common import eval_param
    source = parse('OSSE={\nL=120\na=40\na0=15.5\n}\n'
                   f'Scale={scale}\nSource.Radius=10*log(100)\nSource.Shape=1\n'
                   'Mesh.WallThickness=0\n').design
    result = convert_design(source, 'expressions')
    assert_honest(result)
    radius = design_to_mesher_config(result.design)['source']['sourceRadius']
    assert eval_param(radius, 0, 0) == pytest.approx(10*math.log(100))
    assert result.report.source_cap_angle_after_deg == pytest.approx(result.report.source_cap_angle_before_deg)
    for design in (result.design, DesignConfig.model_validate(result.design.model_dump(mode='json')),
                   parse(serialize(result.design)).design):
        assert design.root.source.radius.constant_value()*scale == pytest.approx(10*math.log(100))


@pytest.mark.parametrize('raw', ['log(100+p)', 'Math.log(100+p)', 'log10(100+p)'])
def test_expression_owner_can_preserve_parameterized_log_meaning_in_another_dialect(raw):
    from server.design.schema import Expr
    from hornlab_mesher.profile_common import eval_param
    original = Expr.model_validate(raw)
    converted = original.in_dialect('wg-v1')
    assert converted.constant_value() is None
    for phi in (0, .5, 1):
        assert eval_param(converted.execution_text(), phi, 0) == pytest.approx(eval_param(original.execution_text(), phi, 0))


def test_extensions_and_slots_are_baked_once_with_real_corners():
    source = {'formula':'OSSE','throat_ext_length':10,'throat_ext_angle':0,'slot_length':5}
    result = convert(source)
    assert_honest(result)
    assert result.design.root.throat_ext_length is None
    assert result.design.root.slot_length is None
    assert {'Throat extension','Slot'} <= {feature.name for feature in result.report.features}
    points = result.design.root.profile_h.points
    corners = [p.t.constant_value()*result.design.root.length.constant_value() for p in points if p.join == 'corner']
    assert corners == pytest.approx([10,15])


@pytest.mark.parametrize('payload',[
    {'formula':'OSSE-AXIAL','axial_scale':1.2},
    {'formula':'OSSE-ADAPTER','throat_adapter':{'mode':'authored'}},
])
def test_native_horns_reuse_resolved_wall_and_keep_flat_source(payload):
    result = convert(payload)
    assert_honest(result)
    assert result.design.root.source.shape.constant_value() == 2
    assert result.design.root.mesh.wall_thickness.constant_value() == 0
    assert result.report.source_cap_angle_before_deg == result.report.source_cap_angle_after_deg == 0
    assert result.report.features
    assert result.report.method.angular_samples_before > 0


def test_source_body_has_an_actionable_refusal(client):
    response = client.post('/api/design/freeform/convert',json={'design':{'formula':'SOURCE-DISK'},'content_key':'source-body'})
    assert response.status_code == 422
    assert response.json()['content_key'] == 'source-body'
    assert 'no inner horn wall' in response.json()['detail']
    assert 'choose a horn family' in response.json()['detail']


def test_scaled_ath_keeps_physical_mesh_offset_resolution_guard_and_explicit_source_radius():
    from server.preview.translate import design_to_mesher_config
    source = parse((Path(__file__).parent/'fixtures/ath_import_defaults/osse_scale.cfg').read_text()).design
    payload = source.model_dump(mode='json')
    payload['mesh'].update(vertical_offset=9,throat_resolution=3,mouth_resolution=5,rear_resolution=12,max_edge=13)
    payload['source'].update(shape=1,radius=50)
    source = DesignConfig.model_validate(payload)
    result = convert_design(source,'scaled-settings')
    assert_honest(result)
    before = design_to_mesher_config(source)
    after = design_to_mesher_config(result.design)
    from hornlab_mesher.profile_common import eval_param
    for field in ('wallThickness','throatResolution','mouthResolution','rearResolution','verticalOffset','maxEdge'):
        assert eval_param(after['mesh'][field],0,0) == pytest.approx(eval_param(before['mesh'][field],0,0))
    assert eval_param(after['source']['sourceRadius'],0,0) == pytest.approx(eval_param(before['source']['sourceRadius'],0,0))
    assert result.report.source_cap_angle_before_deg == pytest.approx(result.report.source_cap_angle_after_deg)


def test_small_lookup_origin_change_is_reported_instead_of_silently_shifted():
    result = convert({'formula':'LOOKUP','lookup_profile':[[.1,12.7],[120.1,72.7]]})
    assert result.verdict == 'approximate'
    assert result.report.axial_extent_deviation_mm == pytest.approx(.1)
    assert result.report.mouth.max_deviation_mm == pytest.approx(.1)
    assert any(feature.name == 'Axial origin' for feature in result.report.features)
    assert_honest(result)


def test_lookup_endpoint_angles_follow_end_chords_even_next_to_a_nearby_kink():
    result = convert({'formula':'LOOKUP','lookup_profile':[[0,12.7],[.01,12.71],[120,30]],'a0':45})
    assert_honest(result)
    assert result.design.root.profile_h.throat_angle_deg.constant_value() == pytest.approx(45)
    assert result.design.root.profile_h.mouth_angle_deg.constant_value() == pytest.approx(math.degrees(math.atan2(30-12.71,120-.01)))


def test_conversion_router_exposes_live_progress_and_cancel(monkeypatch):
    import asyncio
    import json
    import threading
    from uuid import uuid4
    import server.jobs.freeform as api
    from server.tests.test_operation_stages import _http

    entered = threading.Event()
    release = threading.Event()

    def fitting(_design, _key, *, check, progress):
        progress('sample')
        progress('fit')
        entered.set()
        while not release.wait(.01):
            check()
        check()
        pytest.fail('Conversion should have stopped at its cancellation check')

    monkeypatch.setattr(api, 'convert_design', fitting)

    async def scenario():
        app = FastAPI()
        mount_freeform(app)
        mount_operation_progress(app)
        key = str(uuid4())
        pending = asyncio.create_task(_http(app, 'POST', '/api/design/freeform/convert',
            {'design': {'formula': 'OSSE'}, 'content_key': 'cancel-revision'}, operation_id=key))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            status, _, body = await _http(app, 'GET', f'/api/operation-progress/{key}')
            assert status == 200
            assert json.loads(body) == {'stages': ['sample', 'fit'], 'total': 4}
            assert (await _http(app, 'DELETE', f'/api/operation-progress/{key}'))[0] == 204
            status, _, body = await asyncio.wait_for(pending, 5)
            assert status == 499
            assert json.loads(body)['content_key'] == 'cancel-revision'
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())
