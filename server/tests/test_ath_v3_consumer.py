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
    ('; Ath version V2025-06\nOSSE = {\nL=80\n}\nMesh.ThroatExtSegments=4\n', 'Remove Mesh.ThroatExtSegments'),
    ('OSSE = {\nL=80\n}\nABEC.SimType=2\nScale.Z=2\nMesh.Roundover = {\nRadius=10\n}\n', 'Remove Scale.Z'),
])
def test_unqualified_new_control_combinations_explain_the_fix(text, remedy):
    with pytest.raises(TextConfigError, match=remedy):
        parse(text)


@pytest.mark.parametrize('rotation', ['foo', '1/0'])
def test_invalid_rotation_expressions_still_fail_when_evaluated(rotation):
    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', f'Rot = {rotation}')
    design = parse(text).design
    with pytest.raises(ValueError, match='invalid parameter expression'):
        build_point_grid_arrays(build_geometry_params(design_to_mesher_config(design))[0])


@pytest.mark.parametrize('rotation', ['1e309', 'nan', 'inf'])
def test_nonfinite_rotation_explains_the_fix(rotation):
    text = (ROOT / 'r-rot10.cfg').read_text().replace('Rot = 10', f'Rot = {rotation}')
    with pytest.raises(TextConfigError, match='Set Rot to a finite number'):
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
