"""Finished-model Scale parity measured against ATH V2026-09a output."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params, resolve_geometry
from hornlab_mesher.config_parser import parse_text_config
from hornlab_mesher.profile_sampling import build_point_grid_arrays

from server.design.schema import DesignConfig, Expr
from server.design.textcfg import parse, serialize
from server.exports.core import _bare_grid_config
from server.exports.geometry_identity import geometry_hash, geometry_hash_for_design, mesher_version
from server.mesh.builder import _solver_mesher_config
from server.mesh import builder as mesh_builder
from server.preview.core import _cache_relevant_config, _recentred_preview_config
from server.preview.translate import design_to_mesher_config

FIXTURES = Path(__file__).parent / 'fixtures' / 'ath_scale'
TRITONIA = Path(__file__).parent / 'data' / '260308tritonia-q.txt'


def _arrays(config):
    return build_point_grid_arrays(build_geometry_params(config)[0])


def _assert_arrays(actual, expected):
    for name, reference in expected.items():
        if isinstance(reference, np.ndarray):
            np.testing.assert_allclose(actual[name], reference, rtol=0, atol=1e-9, err_msg=name)


@pytest.mark.parametrize('source', [FIXTURES / 'sc_1.cfg', FIXTURES / 'sc_0p7.cfg', TRITONIA],
                         ids=['scale-1', 'scale-0p7', 'scaled-tritonia'])
def test_scaled_implicit_morph_matches_direct_import_through_geometry_paths(source):
    text = source.read_text()
    original = parse(text).design
    direct = parse_text_config(text)
    direct['mesh']['quadrants'] = 1234
    expected = _arrays(direct)
    for design in (original, parse(serialize(original)).design,
                   DesignConfig.model_validate_json(original.model_dump_json())):
        translated = design_to_mesher_config(design)
        assert translated['scale'] == direct.get('scale', 1)
        assert float(translated['profile']['L']) == float(direct['profile']['L'])
        assert float(translated['morph']['morphCorner']) == float(direct['morph']['morphCorner'])
        assert 'morphWidth' not in translated['morph']
        assert 'morphHeight' not in translated['morph']
        _assert_arrays(_arrays(translated), expected)

        preview = _recentred_preview_config(design)
        preview_reference = {**direct, 'mesh': {**direct['mesh'], 'verticalOffset': 0}}
        _assert_arrays(_arrays(preview), _arrays(preview_reference))
        assert _cache_relevant_config(preview)['scale'] == translated['scale']

        solve = _solver_mesher_config(design)
        solve_reference = {**preview_reference, 'mesh': {
            **preview_reference['mesh'], 'quadrants': solve['mesh']['quadrants']}}
        _assert_arrays(_arrays(solve), _arrays(solve_reference))
        placed = _solver_mesher_config(design, keep_placement=True)
        assert float(placed['mesh'].get('verticalOffset', 0)) == float(direct['mesh'].get('verticalOffset', 0))

        exported = _bare_grid_config(design, profile_sampling=True)
        export_reference = {**preview_reference, 'mode': 'bare', 'mesh': {
            **preview_reference['mesh'], 'wallThickness': 0, 'scaleToMetres': False}}
        _assert_arrays(_arrays(exported), _arrays(export_reference))

    # WGLink freshness uses the same placed geometry as the outbound export.
    # Zero-wall probe files request inner-only geometry at the WG boundary.
    if float(direct['mesh'].get('wallThickness', 5)) == 0:
        direct['mode'] = 'bare'
    reference = resolve_geometry(direct).geometry
    assert geometry_hash_for_design(original) == geometry_hash(reference, mesher_version())


@pytest.mark.parametrize('scale', [.7, 1, 1.5])
@pytest.mark.parametrize('controls', [
    '', 'Morph.TargetShape=1\n',
    'Morph.TargetShape=1\nMorph.TargetWidth=400\nMorph.TargetHeight=300\n',
    'Morph.TargetShape=1\nMorph.TargetWidth=0\nMorph.TargetHeight=0\nMorph.CornerRadius=18\n',
    'GCurve.Type=1\nGCurve.Width=300\nGCurve.AspectRatio=1.2\n',
    'Throat.Ext.Length=4\nThroat.Ext.Angle=8\nRot=7\nSlot.Length=3\n',
])
def test_v2_authored_dimensions_and_defaults_match_direct_mesher(scale, controls):
    text = (
        'OSSE = {\nL=120\na=50-15*sin(p)^2\na0=10\ns=.5\n}\n'
        f'Scale={scale}\n{controls}'
        'Mesh.AngularSegments=16\nMesh.LengthSegments=20\n'
        'Mesh.WallThickness=3\nMesh.ThroatResolution=4\n'
        'Mesh.MouthResolution=8\nMesh.RearResolution=9\n'
        'Source.Radius=6\nMesh.VerticalOffset=11\nABEC.SimType=2\n'
    )
    direct = parse_text_config(text)
    translated = design_to_mesher_config(parse(text).design)
    assert translated['scale'] == scale
    assert 'r0' not in translated['profile']
    for key, value in [('wallThickness', 3), ('throatResolution', 4),
                       ('mouthResolution', 8), ('rearResolution', 9), ('verticalOffset', 11)]:
        assert float(translated['mesh'][key]) == value
    assert float(translated['source']['sourceRadius']) == 6
    _assert_arrays(_arrays(translated), _arrays(direct))


def _ath_profiles(name):
    # GridExport CSVs are blank-line separated x;y;z profiles in millimetres.
    text = (FIXTURES / f'{name}_profiles_g.csv').read_text().strip()
    return np.array([[[float(value) for value in line.split(';')]
                      for line in block.splitlines()] for block in text.split('\n\n')])


@pytest.mark.parametrize('name', ['sc_1', 'sc_0p7'])
def test_built_grid_matches_ath_v2026_09a_at_exported_profiles(monkeypatch, name):
    expected = _ath_profiles(name)
    # ATH and the mesher allocate different angular stations around corners.
    # Match profiles by their exported throat angle, retaining WG's authored
    # shape, implicit target derivation, Scale and axial station sampling.
    angles = np.mod(np.arctan2(expected[:, 0, 1], expected[:, 0, 0]), 2 * np.pi)
    monkeypatch.setattr('hornlab_mesher.profile_sampling._angle_list',
                        lambda params, **kwargs: (angles, True))
    design = parse((FIXTURES / f'{name}.cfg').read_text()).design
    actual = _arrays(design_to_mesher_config(design))['inner_grid']
    assert actual.shape == expected.shape == (32, 21, 3)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-4)


def test_ath_v2026_09a_scale_multiplies_finished_export():
    # Compare decimal export precision exactly, without binary64 roundoff at
    # the 8e-7 mm boundary of these six-decimal-place measurements.
    values = []
    for name in ('sc_1', 'sc_0p7'):
        text = (FIXTURES / f'{name}_profiles_g.csv').read_text()
        values.append([Decimal(value) for line in text.splitlines() if line.strip()
                       for value in line.split(';')])
    assert len(values[0]) == len(values[1]) == 32 * 21 * 3
    assert max(abs(scaled - Decimal('.7') * base)
               for base, scaled in zip(*values)) <= Decimal('8e-7')


@pytest.mark.parametrize('version', [None, 'ath-2026-08c-v1', 'ath-2026-09a-v2'])
@pytest.mark.parametrize('scale,edge_mm', [(.7, 6), (2, 12)])
def test_realized_edge_guard_keeps_v2_millimetres_and_legacy_scaling(monkeypatch, version, scale, edge_mm):
    # A fixed artifact isolates guard units from mesher sizing. Its longest
    # edge falls between the authored and legacy-scaled 8 mm thresholds.
    points = np.array([[0, 0, 0], [edge_mm * .001, 0, 0], [edge_mm * .0005, .001, 0]])
    parsed = SimpleNamespace(points=points,
        cells=[SimpleNamespace(type='triangle', data=np.array([[0, 1, 2]]))],
        cell_data={'gmsh:physical': [np.array([2])]})

    def build(config, path, **kwargs):
        expected_guard = 8 if version == 'ath-2026-09a-v2' else 8 * scale
        assert float(config['mesh']['maxEdge']) == expected_guard
        path.write_text('$MeshFormat\n')
        return SimpleNamespace(metadata={}, units='m')

    monkeypatch.setattr('hornlab_mesher.config_builder.build_from_config', build)
    monkeypatch.setattr('meshio.read', lambda path: parsed)
    design = parse((FIXTURES / 'sc_0p7.cfg').read_text()).design
    design.root.text_import_version = version
    design.root.scale = Expr(value=scale)
    design.root.mesh.max_edge = Expr(value=8)
    guard = 8 if version == 'ath-2026-09a-v2' else 8 * scale
    if edge_mm > guard:
        with pytest.raises(RuntimeError, match='exceeded mesh.max_edge guard'):
            mesh_builder._build_sync(design.model_dump(mode='json'))
    else:
        result = mesh_builder._build_sync(design.model_dump(mode='json'))
        assert result['stats']['max_edge_guard_mm'] == guard
        assert result['stats']['max_edge_mm'] == pytest.approx(edge_mm, abs=1e-12)


def test_v2_source_radius_and_vertical_offset_stay_authored_as_in_ath() -> None:
    # ATH V2026-09a, sv_0p7.cfg (Scale 0.7, Source.Radius 40, Mesh.VerticalOffset 30):
    # its bem_mesh.geo points on the throat axis (sv_0p7-axis.csv) put the
    # spherical cap's apex at z = 40 - sqrt(40^2 - (0.7 * 12.7)^2) and its centre
    # 40 mm below that, on the axis at y = 30. ATH keeps both values as written.
    axis = np.loadtxt(FIXTURES / 'sv_0p7-axis.csv', delimiter=';')
    assert np.all(axis[:, 1] == 30)
    sagitta = 40 - np.sqrt(40 ** 2 - (0.7 * 12.7) ** 2)
    assert np.any(np.abs(axis[:, 2] - sagitta) < 2e-3)
    assert np.any(np.abs(axis[:, 2] - (sagitta - 40)) < 2e-3)
    parsed = parse((FIXTURES / 'sv_0p7.cfg').read_text())
    config = design_to_mesher_config(parsed.design)
    assert config['scale'] == 0.7
    assert float(config['source']['sourceRadius']) == 40
    assert float(config['mesh']['verticalOffset']) == 30
    assert float(config['mesh']['wallThickness']) == 5
