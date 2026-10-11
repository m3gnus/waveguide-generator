"""ATH CORE transport and saved geometry contracts measured against ATH V2026-09a output."""
from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params, resolve_geometry
from hornlab_mesher.config_parser import parse_text_config
from hornlab_mesher.profile_common import _EVAL_FUNCTIONS, eval_param
from hornlab_mesher.profile_sampling import build_point_grid_arrays

from server.design.schema import DesignConfig, Expr, _SAFE_FUNCTIONS
from server.design.textcfg import TextConfigError, parse, serialize
from server.exports.api import ExportRequest, export_step
from server.exports.core import _build_stl_mesh_sync, binary_stl
from server.mesh.builder import _solver_mesher_config
from server.preview.core import PreviewComputeService, PreviewProtocol, _Request, _cache_relevant_config
from server.preview.translate import design_to_mesher_config
from server.protocol.frame import decode

FIXTURES = Path(__file__).parent / 'fixtures/ath_core'
CORE = (FIXTURES / 'core.cfg').read_text()
BASE = 'OSSE = {\nL=80\nr0=8\na=35\na0=5\ns=.5\n}\nABEC.SimType=2\nMesh.AngularSegments=16\nMesh.LengthSegments=8\nMesh.WallThickness=3\n'


def test_core_json_and_text_transport_match_direct_mesher_import_exactly():
    parsed = parse(CORE)
    original = parsed.design
    assert parse(CORE.replace('Segments = 6', 'Segments = 6.0')).design.root.mesh.ath_roundover == original.root.mesh.ath_roundover
    assert original.root.text_import_version == 'ath-2026-09a-v2'
    assert original.root.ath_axial_sampling_rule == 'ath-v2025-12+'
    assert original.root.ath_nac_present is True
    assert original.root.ath_nac == 10
    assert 'NAC' in {item.key for item in parsed.ignored_settings}
    copies = [original, DesignConfig.model_validate_json(original.model_dump_json()),
              parse(serialize(original)).design]
    for d in copies:
        for name in ('text_import_version', 'ath_axial_sampling_rule', 'ath_nac_present', 'ath_nac'):
            assert getattr(d.root, name) == getattr(original.root, name)
        assert d.root.mesh.ath_roundover == original.root.mesh.ath_roundover
        assert d.root.a.raw == original.root.a.raw
    expected = parse_text_config(CORE)
    # WG preview expands the authored quarter to a full horn; compare the same
    # domain, retaining every other authored geometry control in the oracle.
    expected['mesh']['quadrants'] = 1234
    reference = resolve_geometry(expected).geometry
    for design in copies:
        config = design_to_mesher_config(design)
        assert config['_athNacPresent'] is True
        assert config['mesh']['athRoundover'] == {'radius': 42, 'segments': 6}
        assert config['mesh']['athRearShape'] == 3
        assert config['mesh']['athRearFrontOffset'] == 40
        assert config['mesh']['athRearStep'] == 50
        actual = resolve_geometry(config).geometry
        for name in ('inner_points', 'outer_points', 'ath_rear_points', 'ath_rear_apex'):
            np.testing.assert_array_equal(getattr(actual, name), getattr(reference, name))
        assert actual.inner_points.shape == (100, 27, 3)
    solve = _solver_mesher_config(original)
    for key in ('_textImportVersion', '_athAxialSamplingRule', '_athNacPresent'):
        assert solve[key] == design_to_mesher_config(original)[key]
    # The solve's domain resolver may select symmetry, but cannot alter the lip.
    for key in ('athRoundover', 'athRearShape', 'athRearFrontOffset', 'athRearStep'):
        assert solve['mesh'][key] == expected['mesh'][key]
    expected['mesh']['quadrants'] = solve['mesh']['quadrants']
    solve_reference = resolve_geometry(expected).geometry
    solved = resolve_geometry(solve).geometry
    for name in ('inner_points', 'outer_points', 'ath_rear_points', 'ath_rear_apex'):
        np.testing.assert_array_equal(getattr(solved, name), getattr(solve_reference, name))


def test_saved_v1_dictionaries_and_arrays_are_bit_identical_to_pre_v2_wg():
    # Captured with WG 7ae5c2b1 before changing its parser or translator. These
    # fixed witnesses include omitted coefficients, asymmetric margins, block
    # Rot, extension rotation, an r0 expression and an R-OSSE morph.
    records = json.loads((FIXTURES / 'saved-v1.json').read_text())
    with np.load(FIXTURES / 'saved-v1.npz') as baseline:
        for i, record in enumerate(records):
            saved = parse(record['text']).design
            for design in (saved, DesignConfig.model_validate_json(saved.model_dump_json()),
                           parse(serialize(saved)).design):
                config = design_to_mesher_config(design)
                assert config == record['config']
                assert config['_textImportVersion'] == 'ath-2026-08c-v1'
                params, _, _ = build_geometry_params(config)
                arrays = build_point_grid_arrays(params)
                for name, value in arrays.items():
                    if isinstance(value, np.ndarray):
                        np.testing.assert_array_equal(value, baseline[f'{i}_{name}'])
                if f'{i}_inner_points' in baseline:
                    geometry = resolve_geometry(config).geometry
                    for name in ('inner_points', 'outer_points'):
                        np.testing.assert_array_equal(getattr(geometry, name), baseline[f'{i}_{name}'])


@pytest.mark.parametrize('echo,rule', [('', 'ath-v2025-12+'),
    ('; Ath version V2025-06\n', 'ath-v2025-06'), ('; Ath version V2026-09a\n', 'ath-v2025-12+')])
@pytest.mark.parametrize('nac', ['', 'NAC=10\n'])
def test_sampling_rule_and_nac_survive_json_text_and_mesher_transport(echo, rule, nac):
    design = parse(echo + BASE + nac + 'Mesh.CornerSegments=4\n').design
    assert design.root.ath_axial_sampling_rule == rule
    assert design.root.ath_nac_present == bool(nac)
    for copy in (design, parse(serialize(design)).design, DesignConfig.model_validate_json(design.model_dump_json())):
        config = design_to_mesher_config(copy)
        assert config['_athAxialSamplingRule'] == rule
        assert config['_athNacPresent'] == bool(nac)
        got = build_point_grid_arrays(build_geometry_params(config)[0])['inner_grid']
        expected = parse_text_config(echo + BASE + nac + 'Mesh.CornerSegments=4\n')
        expected['mesh']['quadrants'] = 1234
        np.testing.assert_array_equal(got, build_point_grid_arrays(build_geometry_params(expected)[0])['inner_grid'])


@pytest.mark.parametrize('text', [
    'OSSE = {\nL=80\n}\n', 'R-OSSE = {\n}\n',
    BASE.replace('a=35\n', '').replace('a0=5\n', '').replace('s=.5\n', ''),
    BASE.replace('s=.5', 's=.5\nRot=invalid') + 'Rot=7\n',
    BASE + 'Rot=7\nThroat.Ext.Length=4\nThroat.Ext.Angle=8\n',
    BASE + 'Mesh.Enclosure = {\nDepth=30\nSpacing=11,12,13,14\n}\n',
    BASE.replace('s=.5', 's=.5 + .05*cos(p)^2') + 'Mesh.LengthSegments=16\n',
])
def test_v2_defaults_margins_rotation_extension_and_warped_wall_match_mesher(text):
    design = parse(text).design
    for copy in (design, parse(serialize(design)).design):
        actual = design_to_mesher_config(copy)
        expected = parse_text_config(text)
        expected['mesh']['quadrants'] = 1234
        # Defaults in this witness request a full grid at the same resolution.
        if 'Mesh.AngularSegments' not in text:
            for c in (actual, expected):
                c['mesh'].update(angularSegments=8, lengthSegments=8)
        a = build_point_grid_arrays(build_geometry_params(actual)[0])
        b = build_point_grid_arrays(build_geometry_params(expected)[0])
        for name in ('inner_grid', 'outer_grid'):
            if b.get(name) is not None:
                np.testing.assert_array_equal(a[name], b[name])
        if design.root.enclosure:
            assert design.root.enclosure.space_l.constant_value() == 13
            assert design.root.enclosure.space_r.constant_value() == 11
            for key in ('space_l', 'space_t', 'space_r', 'space_b'):
                assert float(actual['enclosure'][key]) == expected['enclosure'][key + '_mm']
        if 'Rot=invalid' in text:
            assert 'OSSE.Rot' in {item.key for item in parse(text).ignored_settings}


@pytest.mark.parametrize('extra,key', [
    ('Mesh.RearShape=2\n', 'Mesh.RearShape'), ('Mesh.RearShape=5\n', 'Mesh.RearShape'),
    ('Mesh.Roundover=5\n', 'Mesh.Roundover'),
    ('Mesh.Roundover.Radius=5\n', 'Mesh.Roundover'),
    ('Mesh.RearShape.Step=10\n', 'Mesh.RearShape'),
    ('Mesh.Roundover = {\nRadius=0\n}\n', 'Mesh.Roundover'),
    ('Mesh.Roundover = {\nRadius=5\nSegments=1.5\n}\n', 'Mesh.Roundover'),
    ('Mesh.Roundover = {\nRadius=5\nUnknown=1\n}\n', 'Mesh.Roundover'),
    ('NAC=1.5\n', 'NAC'), ('NAC=nan\n', 'NAC'),
])
def test_unsupported_or_invalid_new_keys_are_refused_by_name(extra, key):
    with pytest.raises(TextConfigError, match=key):
        parse(BASE + extra)


@pytest.mark.parametrize('extra,key', [('Rot=1\n','Rot'), ('Scale=2\n','Scale'),
    ('Throat.Ext.Length=1\n','Throat.Ext.Length'), ('Throat.Ext.Angle=1\n','Throat.Ext.Angle'),
    ('Mesh.VerticalOffset=1\n','Mesh.VerticalOffset'), ('Source.Shape=2\n','Source.Shape'),
    ('ABEC.SimType=1\n','baffle'), ('Mesh.Enclosure = {\nDepth=30\n}\n','Mesh.Enclosure')])
def test_lip_compositions_keep_mesher_named_refusals(extra, key):
    with pytest.raises(TextConfigError, match=key):
        parse(BASE + 'Mesh.Roundover = {\nRadius=10\n}\n' + extra)


def test_r_osse_morph_and_nonliteral_osse_radius_are_refused_only_for_v2():
    for source, name in [(BASE.replace('r0=8','r0=6+2'), 'OSSE.r0'),
            ('R-OSSE = {\nR=80\n}\nMorph.TargetShape=1\n', 'R-OSSE')]:
        with pytest.raises(TextConfigError, match=name):
            parse(source)
        old = parse('; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n'+source).design
        assert design_to_mesher_config(old)['_textImportVersion'] == 'ath-2026-08c-v1'


def test_nac_dictionary_presence_and_v1_optional_transport_do_not_upgrade_geometry():
    saved = parse('; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n' + BASE).design
    expected = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(saved))[0])['inner_grid']
    saved.root.ath_axial_sampling_rule = 'ath-v2025-06'
    saved.root.ath_nac_present = True
    reopened = parse(serialize(saved)).design
    assert reopened.root.ath_axial_sampling_rule == 'ath-v2025-06'
    assert reopened.root.ath_nac_present is True
    np.testing.assert_array_equal(build_point_grid_arrays(build_geometry_params(design_to_mesher_config(reopened))[0])['inner_grid'], expected)
    v2 = DesignConfig.model_validate({'formula': 'OSSE', 'text_import_version': 'ath-2026-09a-v2', 'ath_nac': 10})
    assert design_to_mesher_config(v2)['_athNacPresent'] is True
    with pytest.raises(ValueError, match='NAC conflicts'):
        DesignConfig.model_validate({'formula': 'OSSE', 'ath_nac': 10, 'ath_nac_present': False})


def test_ath_whitelist_and_scalar_results_share_mesher_implementations():
    assert _SAFE_FUNCTIONS == _EVAL_FUNCTIONS
    expressions = ['sf(4,12,15,16,1,.618,.2)', 'exp(1)', 'log(10)', 'log10(10)',
        'round(-2.5)', 'round(2.5)', 'ldexp(2,1.9)', 'scalbn(2,-1.9)', 'logb(8)',
        'fma(2,3,4)', 'fmod(5,2)', 'remainder(5,2)', 'nearbyint(2.5)',
        'hypot(3,4)', 'erf(.5)', 'erfc(.5)', 'gamma(3)', 'lgamma(3)', 'nextafter(1,2)']
    for raw in expressions:
        expression = Expr.model_validate(raw)
        assert expression.constant_value() == eval_param(raw)
        assert eval_param(expression.execution_text()) == eval_param(raw)
    ath = parse(BASE.replace('a=35', 'a=35+log(10)')).design
    native = parse('; Parameter config\n'+BASE.replace('a=35','a=35+log(10)+ln(e)')).design
    assert ath.root.a.constant_value() == 35 + np.log(10)
    assert native.root.a.constant_value() == 37
    assert native.root.a.raw == '35+log(10)+ln(e)'
    for copy in (native, parse(serialize(native)).design, DesignConfig.model_validate_json(native.model_dump_json())):
        assert copy.root.a.execution_text() == '35+log10(10)+log(e)'
        assert eval_param(design_to_mesher_config(copy)['profile']['a']) == 37
    for raw in ('frexp(1)', 'modf(1)'):
        with pytest.raises(ValueError, match='unknown expression function'):
            Expr.model_validate(raw)


def test_authored_segment_counts_remain_in_lip_and_opus_cache_keys():
    config = design_to_mesher_config(parse(CORE).design)
    for feature in ('athRoundover', 'athRearShape'):
        active = {**config, 'mesh': dict(config['mesh'])}
        for key in ('athRoundover', 'athRearShape'):
            if key != feature:
                active['mesh'].pop(key)
        assert 'angularSegments' in _cache_relevant_config(active)['mesh']
        assert 'lengthSegments' in _cache_relevant_config(active)['mesh']
        for key in ('angularSegments', 'lengthSegments'):
            changed = {**active, 'mesh': {**active['mesh'], key: 64}}
            assert _cache_relevant_config(active) != _cache_relevant_config(changed)
    plain = design_to_mesher_config(parse(BASE).design)
    assert 'angularSegments' not in _cache_relevant_config(plain)['mesh']


def test_core_builds_coarse_preview_through_wg_options_and_recentering():
    service = PreviewComputeService()
    protocol = PreviewProtocol(epoch=1, preview_service=service)
    try:
        frame = protocol._compute_frame(_Request(kind='preview', seq=1, design_revision=1,
            lod='coarse', design=parse(CORE).design))
        header, arrays = decode(frame)
        assert header['lod'] == 'coarse'
        assert header['designRevision'] == 1
        assert arrays
        assert any('positions' in name for name in arrays)
        for array in arrays.values():
            assert array.size > 0
            assert np.isfinite(array).all()
    finally:
        asyncio.run(service.shutdown())


@pytest.mark.parametrize('body', ['solid', 'surface'])
def test_step_api_returns_named_refusal_without_attempting_a_cad_build(body):
    from fastapi import HTTPException
    request = ExportRequest(design=parse(CORE).design, designRevision=1)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(export_step(request, body=body))
    assert caught.value.status_code == 422
    assert 'STEP export of Mesh.Roundover / Mesh.RearShape = 3' in caught.value.detail


def test_core_stl_retains_authored_patches_and_existing_axis_mapping(monkeypatch):
    from hornlab_mesher.preview import api
    actual_builder = api.build_preview_geometry
    captured = {}

    def measured(config, options):
        captured['config'] = config
        captured['options'] = options
        captured['geometry'] = actual_builder(config, options)
        return captured['geometry']

    monkeypatch.setattr(api, 'build_preview_geometry', measured)
    design = parse(CORE).design
    mesh = _build_stl_mesh_sync(design.model_dump(mode='json'))
    config = captured['config']
    assert int(config['mesh']['angularSegments']) == 100
    assert int(config['mesh']['lengthSegments']) == 20
    assert int(config['mesh']['cornerSegments']) == 4
    assert config['mesh']['athRearShape'] == 3
    assert float(config['mesh']['wallThickness']) == 15
    assert captured['options'].max_chord_error_mm == .1
    surface = next(s for s in captured['geometry'].surfaces if s.role == 'horn.inner')
    assert surface.metadata['fidelity']['measurement_complete'] is True
    assert surface.metadata['fidelity']['max_chord_error_mm'] <= .1
    np.testing.assert_array_equal(np.asarray(mesh['vertices']).reshape(-1,3), surface.positions * .001)
    data = binary_stl(mesh['vertices'], mesh['indices'], mesh['surfaceTags'])
    assert struct.unpack_from('<I', data, 80)[0] == len(mesh['indices']) // 3
    # The first facet's first vertex retains +x and axial +z, and flips vertical
    # +y, consistently with ordinary STL and the corrected enclosure margins.
    vertex = np.asarray(mesh['vertices']).reshape(-1,3)[mesh['indices'][0]]
    np.testing.assert_array_equal(struct.unpack_from('<3f', data, 96),
        (vertex * np.array([1000,-1000,1000])).astype(np.float32))
