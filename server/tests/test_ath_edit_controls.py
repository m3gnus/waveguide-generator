"""Editable ATH import controls retain transport and geometry contracts."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.preview.api import build_preview_geometry
from hornlab_mesher.profile_sampling import build_point_grid_arrays
from pydantic import ValidationError

from server.design.schema import AthRoundoverConfig, DesignConfig, Expr
from server.design.textcfg import parse, serialize
from server.preview.core import preview_options
from server.preview.translate import design_to_mesher_config, effective_wall_thickness

CORE = Path(__file__).parent / 'fixtures' / 'ath_core' / 'core.cfg'
SMALL = 'OSSE = {\nL=80\nr0=8\na=35\na0=5\ns=.5\n}\nABEC.SimType=2\nMesh.AngularSegments=16\nMesh.LengthSegments=8\nMesh.WallThickness=3\n'


def test_unedited_core_export_is_byte_identical_to_pre_edit_controls_export():
    # Digest captured from the existing serializer before the UI changes.
    text = serialize(DesignConfig.model_validate_json((CORE.parent.parent / 'ath_v3_controls/saved-core-v2.json').read_text()))
    assert sha256(text.encode()).hexdigest() == '3ed4df4ec04c7a57961921a720fcf8d7d441b8f482ddfd958640356aba400006'


def test_edited_lip_and_opus_values_translate_export_and_reimport():
    design = parse(CORE.read_text()).design
    mesh = design.root.mesh
    mesh.ath_roundover = AthRoundoverConfig(radius=48, segments=7)
    mesh.ath_rear_front_offset = 35
    mesh.ath_rear_step = 30
    written = serialize(design)
    assert 'Mesh.Roundover = {\nRadius = 48.0\nSegments = 7\n}' in written
    assert 'Mesh.RearShape = 3' in written
    assert 'Mesh.RearShape.FrontOffset = 35' in written
    assert 'Mesh.RearShape.Step = 30' in written
    for variant in (design, parse(written).design):
        cfg = design_to_mesher_config(variant)
        assert cfg['mesh']['athRoundover'] == {'radius': 48, 'segments': 7}
        assert cfg['mesh']['athRearShape'] == 3
        assert cfg['mesh']['athRearFrontOffset'] == 35
        assert cfg['mesh']['athRearStep'] == 30


def test_removing_lip_and_selecting_full_rear_round_trips():
    design = parse(CORE.read_text()).design
    mesh = design.root.mesh
    mesh.ath_roundover = None
    mesh.ath_rear_shape = 1
    mesh.ath_rear_front_offset = None
    mesh.ath_rear_step = None
    text = serialize(design)
    assert 'Mesh.Roundover' not in text
    assert 'Mesh.RearShape.FrontOffset' not in text
    assert 'Mesh.RearShape.Step' not in text
    reopened = parse(text).design
    assert reopened.root.mesh.ath_roundover is None
    assert reopened.root.mesh.ath_rear_shape == 1


# WG always sends ATH's 5 mm for an omitted wall on imports (ATH's parser default), so a lip
# or an Opus rear never changes an imported design's wall.
@pytest.mark.parametrize('controls,wall', [({}, 5), ({'ath_roundover': {'radius': 10}}, 5), ({'ath_rear_shape': 3}, 5)])
def test_unset_import_wall_is_ath_default(controls, wall):
    design = DesignConfig.model_validate({'formula': 'OSSE', 'text_import_version': 'ath-2026-09a-v2', 'mesh': controls})
    assert effective_wall_thickness(design.root) == wall
    cfg = design_to_mesher_config(design)
    assert cfg['mesh']['wallThickness'] == wall
    params, _, _ = build_geometry_params(cfg)
    assert params['wallThickness'] == wall
    assert design.root.mesh.wall_thickness is None


@pytest.mark.parametrize('wall,effective', [(.05, .1), (1200, 1000)])
def test_opus_clamps_geometry_without_overwriting_saved_wall(wall, effective):
    design = parse(SMALL + 'Mesh.RearShape=3\nMesh.RearShape.FrontOffset=10\n').design
    design.root.mesh.wall_thickness = Expr(value=wall)
    cfg = design_to_mesher_config(design)
    params, _, _ = build_geometry_params(cfg)
    assert params['wallThickness'] == effective
    assert design.root.mesh.wall_thickness.value == wall
    assert f'Mesh.WallThickness = {wall}' in serialize(design)


@pytest.mark.parametrize('mesh', [
    {'ath_roundover': {'radius': 0}}, {'ath_roundover': {'radius': float('inf')}},
    {'ath_roundover': {'radius': 10, 'segments': 0}}, {'ath_roundover': {'radius': 10, 'segments': 1.5}},
    {'ath_rear_shape': 3, 'ath_rear_front_offset': float('inf')},
    {'ath_rear_shape': 3, 'ath_rear_front_offset': -1, 'ath_roundover': {'radius': 10}},
    {'ath_rear_shape': 3, 'ath_rear_step': 0}, {'ath_rear_shape': 3, 'ath_rear_step': float('inf')},
    {'ath_rear_shape': 1, 'ath_rear_step': 25}, {'ath_rear_shape': 1, 'ath_rear_front_offset': 40},
])
def test_edited_wire_values_obey_mesher_bounds(mesh):
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({'formula': 'OSSE', 'text_import_version': 'ath-2026-09a-v2', 'mesh': mesh})


def test_small_preview_edited_lip_radius_changes_mouth_and_keeps_body():
    design = parse(SMALL + 'Mesh.Roundover = {\nRadius=10\nSegments=5\n}\n').design
    cfg = design_to_mesher_config(design)
    before = build_preview_geometry(cfg, preview_options('coarse'))
    original_grid = build_point_grid_arrays(build_geometry_params(cfg)[0])['inner_grid']
    design.root.mesh.ath_roundover.radius = 16
    edited = design_to_mesher_config(design)
    after = build_preview_geometry(edited, preview_options('coarse'))
    edited_grid = build_point_grid_arrays(build_geometry_params(edited)[0])['inner_grid']
    np.testing.assert_array_equal(original_grid[:, :-5], edited_grid[:, :-5])
    assert not np.array_equal(original_grid[:, -5:], edited_grid[:, -5:])
    first = next(s for s in before.surfaces if s.role == 'horn.inner')
    second = next(s for s in after.surfaces if s.role == 'horn.inner')
    assert np.isfinite(second.positions).all()
    assert first.positions.shape == second.positions.shape
    assert not np.array_equal(first.positions, second.positions)
    assert np.max(second.positions[:, 0]) > np.max(first.positions[:, 0])


def test_negative_opus_offset_survives_save_export_reimport_and_preview():
    from hornlab_mesher.config_builder import resolve_geometry

    design = parse(SMALL + 'Mesh.RearShape=3\nMesh.RearShape.FrontOffset=-50\nMesh.WallThickness=100\n').design
    restored = DesignConfig.model_validate_json(design.model_dump_json())
    text = serialize(restored)
    assert 'Mesh.RearShape.FrontOffset = -50' in text
    before = resolve_geometry(design_to_mesher_config(design)).geometry
    for variant in (restored, parse(text).design):
        cfg = design_to_mesher_config(variant)
        assert cfg['mesh']['athRearFrontOffset'] == -50
        geometry = resolve_geometry(cfg).geometry
        np.testing.assert_array_equal(geometry.inner_points, before.inner_points)
        np.testing.assert_array_equal(geometry.ath_rear_points, before.ath_rear_points)
        np.testing.assert_array_equal(geometry.ath_rear_apex, before.ath_rear_apex)
    preview = build_preview_geometry(cfg, preview_options('coarse'))
    assert {'horn.inner', 'horn.outer', 'mouth_rim', 'wall.rear_cap'} <= {s.role for s in preview.surfaces}
    assert all(np.isfinite(s.positions).all() for s in preview.surfaces)


@pytest.mark.parametrize('stamp', ['ath-2026-09a-v2', 'ath-2026-09a-v3'])
@pytest.mark.parametrize('controls', [
    'Mesh.Roundover = {\nRadius=10\nSegments=5\n}\n',
    'Mesh.RearShape=3\nMesh.RearShape.FrontOffset=10\n',
    'Mesh.Roundover = {\nRadius=10\nSegments=5\n}\nMesh.RearShape=3\nMesh.RearShape.FrontOffset=10\n',
])
@pytest.mark.parametrize('composition', [
    'Scale=.7\n', 'Throat.Ext.Length=8\n', 'Throat.Ext.Length=8\nThroat.Ext.Angle=12\n',
    'Rot=5\n', 'Mesh.VerticalOffset=17\n', 'Source.Shape=2\n',
])
def test_measured_surface_compositions_survive_saved_design_and_both_mesh_transports(stamp, controls, composition):
    from hornlab_mesher.config_builder import resolve_geometry
    from hornlab_mesher.config_parser import parse_text_config
    from server.mesh.builder import _solver_mesher_config

    text = f'; Waveguide Generator geometry-interpretation: {stamp}\n' + SMALL + controls + composition
    design = parse(text).design
    original = resolve_geometry(parse_text_config(text)).geometry
    saved = DesignConfig.model_validate_json(design.model_dump_json())
    for copy in (design, saved, parse(serialize(saved)).design):
        for cfg in (design_to_mesher_config(copy), _solver_mesher_config(copy)):
            actual = resolve_geometry(cfg).geometry
            np.testing.assert_array_equal(actual.inner_points, original.inner_points)
            np.testing.assert_array_equal(actual.ath_rear_points, original.ath_rear_points)
            np.testing.assert_array_equal(actual.ath_rear_apex, original.ath_rear_apex)
            assert actual.source_shape == original.source_shape


def test_axial_scaled_lip_survives_saved_design_and_export():
    from hornlab_mesher.config_builder import resolve_geometry

    design = parse(SMALL + 'Scale=.7\nScale.Z=1.4\nMesh.Roundover = {\nRadius=10\nSegments=5\n}\n').design
    before = resolve_geometry(design_to_mesher_config(design)).geometry
    for copy in (DesignConfig.model_validate_json(design.model_dump_json()), parse(serialize(design)).design):
        after = resolve_geometry(design_to_mesher_config(copy)).geometry
        np.testing.assert_array_equal(after.inner_points, before.inner_points)
        np.testing.assert_array_equal(after.ath_rear_points, before.ath_rear_points)
