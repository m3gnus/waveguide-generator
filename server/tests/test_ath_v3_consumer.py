"""Fresh ATH controls reach the same captured wall after every WG transport."""
from pathlib import Path
import asyncio

import numpy as np
import pytest

from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.profile_sampling import build_point_grid_arrays
from server.cadlink.store import CadLinkStore
from server.design_io.api import open_design, save_design
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.translate import design_to_mesher_config

ROOT = Path(__file__).parent / 'fixtures/ath_v3_controls'
V3 = 'ath-2026-09a-v3'


@pytest.mark.parametrize('name', ['o-z2-s3', 'r-rot10', 'r-ext-seg4'])
def test_fresh_control_matches_ath_after_save_reopen_and_cfg(name, tmp_path):
    text = (ROOT / f'{name}.cfg').read_text()
    design = parse(text).design
    assert design.root.text_import_version == V3
    expected = np.array([[[float(v) for v in row.split(';')] for row in block.splitlines()]
                         for block in (ROOT / f'{name}.csv').read_text().strip().split('\n\n')])
    store = CadLinkStore(tmp_path / 'cadlink.db')
    opened = asyncio.run(open_design(text, store=store))
    saved = asyncio.run(save_design(opened['design'], store=store))
    reopened = asyncio.run(open_design(saved['text'], store=store))
    variants = [design, DesignConfig.model_validate_json(design.model_dump_json()),
                parse(serialize(design)).design, DesignConfig.model_validate(reopened['design'])]
    for variant in variants:
        config = design_to_mesher_config(variant)
        assert config['_textImportVersion'] == V3
        params = build_geometry_params(config)[0]
        actual = build_point_grid_arrays(params)['inner_grid']
        assert actual.shape == expected.shape
        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-4)
    assert not {'Scale.Z', 'Rot', 'Mesh.ThroatExtSegments'} & design.root.extra_keys.keys()


@pytest.mark.parametrize('version', ['ath-2026-08c-v1', 'ath-2026-09a-v2'])
def test_saved_import_keeps_its_stamp_without_new_controls(version):
    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', 'Rot = 0')
    design = parse(f'; Waveguide Generator geometry-interpretation: {version}\n' + text).design
    reopened = parse(serialize(design)).design
    assert reopened.root.text_import_version == version
    assert reopened.model_dump(mode='json') == design.model_dump(mode='json')


@pytest.mark.parametrize('line', ['Scale.Z = 0', 'Scale.Z = 1+sin(p)',
                                  'Mesh.ThroatExtSegments = 4.5'])
def test_invalid_new_control_names_the_remedy(line):
    text = (ROOT / 'r-rot10.cfg').read_text() + '\n' + line + '\n'
    with pytest.raises(TextConfigError, match='[Ss]et|[Ee]nter|[Rr]emove|[Rr]e-import'):
        parse(text)


@pytest.mark.parametrize('name', ['o-z2-s3', 'r-rot10', 'r-ext-seg4'])
def test_solve_preparation_keeps_new_controls_and_authored_wall(name):
    from server.mesh.builder import _solver_mesher_config

    design = parse((ROOT / f'{name}.cfg').read_text()).design
    preview = design_to_mesher_config(design)
    solve = _solver_mesher_config(design)
    for key in ('_textImportVersion', 'athAxialScale'):
        assert solve.get(key) == preview.get(key)
    assert solve['profile'] == preview['profile']
    assert solve['mesh'].get('athThroatExtSegments') == preview['mesh'].get('athThroatExtSegments')
    expected = build_point_grid_arrays(build_geometry_params(preview)[0])['inner_grid']
    actual = build_point_grid_arrays(build_geometry_params(solve)[0])['inner_grid']
    np.testing.assert_array_equal(actual, expected)


def test_unit_axial_override_replaces_global_scale_and_survives_cfg():
    design = parse((ROOT / 'o-z2-s3.cfg').read_text().replace('Scale.Z = 2', 'Scale.Z = 1')).design
    for copy in (design, parse(serialize(design)).design):
        config = design_to_mesher_config(copy)
        assert config['scale'] == 3
        assert config['athAxialScale'] == 1


@pytest.mark.parametrize('stamp', ['ath-2026-08c-v1', 'ath-2026-09a-v2'])
@pytest.mark.parametrize('control', [{'ath_axial_scale': 2}, {'rotation': 10},
                                   {'mesh': {'ath_throat_ext_segments': 4}}])
def test_saved_stamps_cannot_silently_acquire_new_geometry(stamp, control):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match='Re-import a copy'):
        DesignConfig.model_validate({'formula': 'R-OSSE', 'text_import_version': stamp, **control})


@pytest.mark.parametrize('text, remedy', [
    ('; Ath version V2025-06\nOSSE = {\nL=80\n}\nMesh.ThroatExtSegments=4\n', 'Change or remove'),
    ('OSSE = {\nL=80\n}\nABEC.SimType=2\nScale.Z=2\nMesh.Roundover = {\nRadius=10\n}\n', 'Change or remove'),
])
def test_unqualified_new_control_combinations_explain_the_fix(text, remedy):
    with pytest.raises(TextConfigError, match=remedy):
        parse(text)


@pytest.mark.parametrize('rotation', ['foo', '1/0'])
def test_invalid_rotation_expressions_are_refused_at_import(rotation):
    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', f'Rot = {rotation}')
    with pytest.raises(TextConfigError, match='Rot:.*Change or remove'):
        parse(text)


@pytest.mark.parametrize('rotation', ['1e309', 'nan', 'inf'])
def test_nonfinite_rotation_explains_the_fix(rotation):
    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', f'Rot = {rotation}')
    with pytest.raises(TextConfigError, match='Rot must be finite.*Change or remove'):
        parse(text)


@pytest.mark.parametrize('rotation', [0, 10])
def test_rosse_block_rotation_stays_ignored_through_cfg_reopen(rotation):
    from hornlab_mesher.config_parser import parse_text_config

    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', f'Rot = {rotation}')
    text = text.replace(' q = 3.4', ' q = 3.4\n Rot = 37')
    parsed = parse(text)
    assert 'R-OSSE.Rot' in {item.key for item in parsed.ignored_settings}
    expected = build_point_grid_arrays(build_geometry_params(parse_text_config(text))[0])['inner_grid']
    for design in (parsed.design, parse(serialize(parsed.design)).design):
        config = design_to_mesher_config(design)
        actual = build_point_grid_arrays(build_geometry_params(config)[0])['inner_grid']
        np.testing.assert_array_equal(actual, expected)


def test_extension_block_precedence_matches_mesher_through_reopen():
    from hornlab_mesher.config_parser import parse_text_config

    text = (ROOT / 'r-ext-seg4.cfg').read_text() + '\nMesh = {\n ThroatExtSegments = 7\n}\n'
    expected_config = parse_text_config(text)
    expected = build_point_grid_arrays(build_geometry_params(expected_config)[0])['inner_grid']
    design = parse(text).design
    for copy in (design, parse(serialize(design)).design):
        config = design_to_mesher_config(copy)
        assert config['mesh']['athThroatExtSegments'] == 7
        actual = build_point_grid_arrays(build_geometry_params(config)[0])['inner_grid']
        assert actual.shape == (32, 28, 3)
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('control', [
    {'ath_axial_scale': 2, 'mesh': {'ath_roundover': {'radius': 10, 'segments': 5}}},
    {'ath_axial_sampling_rule': 'ath-v2025-06', 'mesh': {'ath_throat_ext_segments': 4}},
])
def test_dictionary_composition_decisions_belong_to_mesher(monkeypatch, control):
    import hornlab_mesher.config_builder as native

    seen = []

    def accepted(config):
        seen.append(config)
        return {}, 'OSSE', 'bare'

    monkeypatch.setattr(native, 'build_geometry_params', accepted)
    design = DesignConfig.model_validate({
        'formula': 'OSSE', 'text_import_version': V3, 'simulation': {'sim_type': 'freestanding'},
        **control,
    })
    config = design_to_mesher_config(design)
    assert seen == [config]


def test_axial_scale_text_interpretation_belongs_to_mesher(monkeypatch):
    import hornlab_mesher.config_parser as native

    original = native.parse_text_config

    def supported(text):
        # Simulate a new constant spelling accepted by the native owner.
        return original(text.replace('native_axis_unit', '2'))

    monkeypatch.setattr(native, 'parse_text_config', supported)
    text = (ROOT / 'o-z2-s3.cfg').read_text().replace('Scale.Z = 2', 'Scale.Z = native_axis_unit')
    assert parse(text).design.root.ath_axial_scale == 2


@pytest.mark.parametrize('block', ['Scale = {\n Z = 7\n}\n', 'profile'])
def test_axial_scale_has_no_supported_block_override(block):
    from hornlab_mesher.config_parser import ConfigError, parse_text_config

    text = (ROOT / 'o-z2-s3.cfg').read_text()
    text = text.replace('OSSE = {', 'OSSE = {\n Scale.Z = 7') if block == 'profile' else text + '\n' + block
    with pytest.raises(ConfigError):
        parse_text_config(text)
    with pytest.raises(TextConfigError):
        parse(text)


def test_mesher_refusal_wording_is_preserved_with_a_remedy(monkeypatch):
    import hornlab_mesher.config_builder as native

    def refuse(config):
        raise ValueError('A newly worded native refusal')

    monkeypatch.setattr(native, 'build_geometry_params', refuse)
    design = DesignConfig.model_validate({'formula': 'OSSE', 'text_import_version': V3, 'ath_axial_scale': 2})
    with pytest.raises(ValueError, match='A newly worded native refusal.*Change or remove'):
        design_to_mesher_config(design)


@pytest.mark.parametrize('stamp', ['ath-2026-08c-v1', 'ath-2026-09a-v2'])
def test_hand_edited_old_cfg_controls_remain_inert(stamp):
    original = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10\n', '')
    prefix = f'; Waveguide Generator geometry-interpretation: {stamp}\n'
    plain = parse(prefix + original).design
    edited = parse(prefix + original + 'Scale.Z=2\nMesh.ThroatExtSegments=7\nRot=10\n').design
    expected = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(plain))[0])['inner_grid']
    for design in (edited, parse(serialize(edited)).design):
        assert design.root.text_import_version == stamp
        assert {'Scale.Z', 'Mesh.ThroatExtSegments', 'Rot'} <= design.root.extra_keys.keys()
        config = design_to_mesher_config(design)
        assert 'athAxialScale' not in config
        assert 'athThroatExtSegments' not in config['mesh']
        assert 'rot' not in config['profile']
        np.testing.assert_array_equal(build_point_grid_arrays(build_geometry_params(config)[0])['inner_grid'], expected)


def test_unknown_import_version_has_the_update_remedy():
    with pytest.raises(TextConfigError) as error:
        parse('; Waveguide Generator geometry-interpretation: future-v4\nOSSE = {\nL=120\n}\n')
    assert str(error.value) == 'This design was saved by a newer version of WG. Update WG to open it.'


@pytest.mark.parametrize('kind', ['key', 'block'])
def test_saved_v3_keeps_inert_wg_metadata(kind):
    from server.design.schema import ConfigBlock

    design = parse((ROOT / 'r-ext-seg4.cfg').read_text()).design
    expected = build_point_grid_arrays(build_geometry_params(design_to_mesher_config(design))[0])['inner_grid']
    if kind == 'key':
        design.root.extra_keys['ArchiveNote'] = 'unchanged'
    else:
        design.root.extra_blocks['ArchiveNote'] = ConfigBlock(items={'Text': 'unchanged'}, entries=['Text = unchanged'])
    reopened = parse(serialize(design)).design
    assert reopened.root.extra_keys == design.root.extra_keys
    assert reopened.root.extra_blocks == design.root.extra_blocks
    np.testing.assert_array_equal(build_point_grid_arrays(build_geometry_params(design_to_mesher_config(reopened))[0])['inner_grid'], expected)


def test_saved_sampling_metadata_reaches_the_native_composition_check():
    text = ('; Parameter config\n; Waveguide Generator geometry-interpretation: ' + V3 + '\n'
            'OSSE = {\nL=80\n}\n_athAxialSamplingRule=ath-v2025-06\nMesh.ThroatExtSegments=4\n')
    with pytest.raises(TextConfigError, match='Mesh.ThroatExtSegments requires the current.*Change or remove'):
        parse(text)


def test_explicit_unit_axial_scale_survives_before_a_transverse_scale_edit():
    from hornlab_mesher.config_parser import parse_text_config
    from server.design.schema import Expr

    text = (ROOT / 'o-z2-s3.cfg').read_text().replace('Scale = 3', 'Scale = 1').replace('Scale.Z = 2', 'Scale.Z = 1')
    design = parse(text).design
    for copy in (design, parse(serialize(design)).design):
        assert copy.root.ath_axial_scale == 1
        copy.root.scale = Expr(value=3)
        config = design_to_mesher_config(copy)
        assert config['athAxialScale'] == 1
        expected = parse_text_config(text.replace('Scale = 1', 'Scale = 3'))
        actual_grid = build_point_grid_arrays(build_geometry_params(config)[0])['inner_grid']
        expected_grid = build_point_grid_arrays(build_geometry_params(expected)[0])['inner_grid']
        np.testing.assert_array_equal(actual_grid, expected_grid)
