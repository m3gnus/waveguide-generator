"""S4 conversion: generated geometry is the judge, without browser fallback."""
from copy import deepcopy
import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.design.textcfg import parse
from server.jobs.operation_progress import mount_operation_progress
from server.preview.freeform import mount_freeform
from server.preview.freeform_conversion import compare_generated_designs, convert_design


@pytest.fixture
def client():
    app = FastAPI()
    mount_freeform(app)
    mount_operation_progress(app)
    with TestClient(app) as transport:
        yield transport


def convert(payload):
    return convert_design(DesignConfig.model_validate(payload), 'source-revision')


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
    from server.preview.freeform_conversion import ConvertResponse
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
    assert result.verdict == 'match'
    assert_honest(result)
    points = result.design.root.profile_h.points
    assert len(points) == len(rows)
    assert all(p.join == 'corner' for p in points)
    assert [p.t.constant_value()*120 for p in points] == pytest.approx([row[0] for row in rows])


def test_lookup_cap_change_prevents_match_even_when_wall_matches():
    result = convert({'formula':'LOOKUP','lookup_profile':[[0,12.7],[120,72.7]]})
    assert result.report.wall.max_deviation_mm < .01
    assert result.report.source_cap_angle_before_deg == 0
    assert result.report.source_cap_angle_after_deg > 20
    assert result.verdict == 'approximate'


def test_large_lookup_is_approximate_or_refused_without_truncating(client):
    rows = [[float(i), 20+i*.2+.02*(i%2)] for i in range(90)]
    result = convert({'formula':'LOOKUP','lookup_profile':rows})
    assert result.verdict == 'approximate'
    assert result.design.root.length.constant_value() == 89
    assert_honest(result)
    response = client.post('/api/design/freeform/convert',json={'design':{'formula':'LOOKUP',
        'lookup_profile':[[float(i),20+i*.2+2*(i%2)] for i in range(90)]},'content_key':'too-many'})
    assert response.status_code == 422
    assert response.json()['content_key'] == 'too-many'
    assert '0.5 mm' in response.json()['detail']
    assert 'Keep the original' in response.json()['detail']


@pytest.mark.parametrize('payload, message', [
    ({'formula':'R-OSSE'}, 'rolls back'),
    ({'formula':'LOOKUP','lookup_profile':[[10,8],[100,50]]}, 'first z is zero'),
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


@pytest.mark.parametrize('error_type',[RuntimeError,ValueError])
def test_program_faults_are_not_presented_as_user_refusals(client,monkeypatch,error_type):
    import server.preview.freeform_conversion as conversion
    def broken(design):
        raise error_type('deliberate internal fault')
    monkeypatch.setattr(conversion,'_sample',broken)
    with pytest.raises(error_type,match='deliberate internal fault'):
        client.post('/api/design/freeform/convert',json={'design':{'formula':'OSSE'},'content_key':'fault'})


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


def test_ath_roundover_is_named_in_report_and_foldback_refusal(client):
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
    assert response.status_code == 422
    assert 'ATH Mesh.Roundover' in response.json()['detail']
    assert 'Keep this horn' in response.json()['detail']


def test_morph_is_baked_with_stations_and_reported_as_approximate():
    result = convert({'formula':'OSSE','morph':{'target_shape':3,'target_exponent':2.3,
        'target_width':400,'target_height':350,'fixed_part':.5}})
    assert result.verdict == 'approximate'
    assert result.design.root.morph.target_shape.constant_value() == 0
    assert len(result.design.root.cross_sections) > 2
    assert any(feature.name == 'Mouth morph' for feature in result.report.features)
    assert_honest(result)


def test_icw_converts_or_explains_a_fidelity_refusal(client):
    # The default flat-baffle ICW approaches a vertical mouth; normalized-t
    # anchor spacing can make the last fractions of a millimetre unfit for
    # FREEFORM. It must be measured or refused, never reduced to endpoints.
    response = client.post('/api/design/freeform/convert',json={'design':{'formula':'ICW'},'content_key':'icw'})
    assert response.json()['content_key'] == 'icw'
    if response.status_code == 200:
        from server.preview.freeform_conversion import ConvertResponse
        assert_honest(ConvertResponse.model_validate(response.json()))
    else:
        assert response.status_code == 422
        assert '0.5 mm' in response.json()['detail']
        assert 'Keep the original' in response.json()['detail']


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
