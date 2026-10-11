"""Cross-section and enclosure controls against the pinned mesher contract."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
from hornlab_mesher.config_builder import build_from_config, build_geometry_params, build_point_grid, resolve_geometry
from hornlab_mesher.density import _parse_quadrant_resolutions
from pydantic import ValidationError

from server.design.schema import DesignConfig
from server.design_io.api import open_design, serialize_design
from server.preview.translate import design_to_mesher_config
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/section-default-translation.json').read_text())


def design(**updates):
    return DesignConfig.model_validate({**FIXTURE['design'], **updates})


def test_existing_design_translation_is_byte_identical():
    config = design_to_mesher_config(design())
    assert json.dumps(config, sort_keys=True) == json.dumps(FIXTURE['mesher_config'], sort_keys=True)
    assert design_to_mesher_config(design(cross_section={'exponent': 2, 'aspect_ratio': 1})) == config
    assert 'cross_section' not in design().model_dump(mode='json')
    assert 'depth_margin_mm' not in design().root.enclosure.model_dump(mode='json')


@pytest.mark.parametrize('sizes,expected', [(8, [8]*4), ({'value':None,'raw':'8.00,12.0,16,20'}, [8,12,16,20]), ('8,12', [8,12,12,12]), ([8,12,16], [8,12,16,16]), ([8,12,16,20], [8,12,16,20]), ([0,12], [20,12,12,12])])
def test_documented_quadrant_resolution_examples(sizes, expected):
    root = design(enclosure={'depth':90, 'front_resolution':sizes, 'back_resolution':sizes})
    config = design_to_mesher_config(root)
    density = resolve_geometry(config).density
    assert _parse_quadrant_resolutions(density.enc_front_res_mm, density.mouth_res_mm) == expected
    assert _parse_quadrant_resolutions(density.enc_back_res_mm, density.mouth_res_mm) == expected
    assert config['enclosure']['frontMeshSize'] == config['mesh']['encFrontResolution']


@pytest.mark.parametrize('field,value', [('exponent',0), ('exponent',-2), ('aspect_ratio',0), ('aspect_ratio',float('inf')), ('exponent','4')])
def test_section_requires_positive_finite_numbers(field, value):
    with pytest.raises(ValidationError):
        design(cross_section={field:value})


def test_freeform_and_native_sections_refuse_nondefault():
    freeform = {'formula':'FREEFORM', 'length':60, 'profile_h':{'points':[[0,12.7],[1,40]]}, 'profile_v':{'points':[[0,12.7],[1,40]]}, 'cross_sections':[{'t':0,'shape':'ellipse'},{'t':1,'shape':'ellipse'}], 'cross_section':{'exponent':4}}
    with pytest.raises(ValidationError, match='stations own the outline'):
        DesignConfig.model_validate(freeform)
    with pytest.raises(ValidationError, match='cross_section.aspect_ratio'):
        DesignConfig.model_validate({'formula':'SOURCE-DISK','source_body':{'radius_mm':12.7,'outer_radius_mm':30,'depth_mm':5},'cross_section':{'aspect_ratio':2}})


def test_active_guiding_curve_refuses_nondefault_section():
    with pytest.raises(ValidationError, match='Active guiding curves require'):
        design(cross_section={'aspect_ratio':2}, guiding_curve={'curve_type':1,'width':100})


@pytest.mark.parametrize('formula,profile', [('OSSE',{'L':60}), ('R-OSSE',{'R':60}), ('ICW',{'L':60,'R':40})])
def test_sections_change_point_grid_for_legacy_families(formula, profile):
    def points(section):
        root = DesignConfig.model_validate({'formula':formula, **profile, 'cross_section':section,'mesh':{'wall_thickness':0,'angular_segments':16,'length_segments':8}})
        params, _, _ = build_geometry_params(design_to_mesher_config(root))
        return np.asarray(build_point_grid(params)['inner_points'])
    assert not np.allclose(points({'exponent':4,'aspect_ratio':1.5}), points({}))


def test_section_precedes_morph():
    params, _, _ = build_geometry_params(design_to_mesher_config(design(cross_section={'exponent':4,'aspect_ratio':1.5}, morph={'target_shape':3,'target_exponent':4,'target_width':180,'target_height':100})))
    assert params['profileSystem']['crossSection'] == {'exponent':4,'aspectRatio':1.5}
    assert np.isfinite(build_point_grid(params)['inner_points']).all()


@pytest.mark.parametrize('sizes,xz,yz', [(20,True,True), ([20,24,24,20],True,False), ([20,20,24,24],False,True), ([20,24,28,32],False,False)])
@pytest.mark.parametrize('panel', ['front_resolution','back_resolution'])
def test_resolution_symmetry_controls_reduction(sizes,xz,yz,panel):
    result = resolve_symmetry(design(enclosure={'depth':90,panel:sizes}))
    assert (result.xz,result.yz) == (xz,yz)
    if not (xz and yz):
        with pytest.raises(ValueError, match='quadrant sizes differ'):
            validate_symmetry_mode('quarter', result)


def test_ellipse_keeps_mirrors_and_inactive_enclosure_does_not_veto():
    result = resolve_symmetry(design(cross_section={'aspect_ratio':1.5}, enclosure={'depth':0,'front_resolution':[20,24,28,32]}))
    assert (result.xz,result.yz) == (True,True)


def test_wg_file_round_trip():
    root = design(cross_section={'exponent':4,'aspect_ratio':1.5}, enclosure={'depth':90,'depth_margin_mm':7,'front_resolution':['8.00','12.0','16','20'],'back_resolution':[20,24]})
    saved = asyncio.run(serialize_design({'design':root.model_dump(mode='json')}))
    opened = asyncio.run(open_design(saved['text']))
    restored = DesignConfig.model_validate(opened['design'])
    assert restored.root.cross_section == root.root.cross_section
    assert restored.root.enclosure.depth_margin_mm == 7
    assert restored.root.enclosure.front_resolution == root.root.enclosure.front_resolution
    assert design_to_mesher_config(restored)['mesh']['encBackResolution'] == '20,24'
    from server.design.textcfg import parse, serialize
    assert serialize(parse(saved['text'])) == saved['text']


def test_depth_margin_honoured_for_rectangle():
    from hornlab_mesher.builders.enclosure import enclosure_box_bounds
    config = design_to_mesher_config(design(enclosure={'depth':1,'depth_margin_mm':7}))
    resolved = resolve_geometry(config)
    bounds = enclosure_box_bounds(resolved.geometry.inner_points, resolved.geometry.enclosure, closed=True, symmetry_planes=())
    assert bounds['enc_depth'] >= 67


@pytest.mark.parametrize('updates', [{'cross_section':{'exponent':4}}, {'cross_section':{'aspect_ratio':1.5}}, {'enclosure':{'depth':1,'edge_radius':0,'depth_margin_mm':7,'front_resolution':[20,24,28,32],'back_resolution':[24,28,32,36]}}])
def test_real_mesh_nondefault_controls(tmp_path, updates):
    result = build_from_config(design_to_mesher_config(design(**updates)), tmp_path/'section.msh')
    assert (tmp_path/'section.msh').stat().st_size > 0
    assert result.n_triangles > 0
    assert result.n_vertices > 0


@pytest.mark.parametrize('sizes', [[], [8,12,16,20,24], '8,p', float('nan')])
def test_invalid_resolution_lists_refuse_at_schema(sizes):
    with pytest.raises(ValidationError):
        design(enclosure={'front_resolution':sizes})


@pytest.mark.parametrize('margin', [-1, float('inf'), '7'])
def test_depth_margin_requires_nonnegative_finite_number(margin):
    with pytest.raises(ValidationError):
        design(enclosure={'depth_margin_mm':margin})


@pytest.mark.parametrize('payload', ['[]', '{"plan_type":2}', '{"cross_section":{"exponent":0}}'])
def test_wg_geometry_block_refuses_invalid_payload(payload):
    from server.design.textcfg import TextConfigError, parse
    with pytest.raises(TextConfigError):
        parse('; Parameter config\n; Waveguide Generator geometry-interpretation: native-v1\nOSSE = {\n}\nWG.Geometry = {\nPayload = '+payload+'\n}\n')



def test_preview_solver_and_profile_export_retain_new_controls():
    from hornlab_mesher.preview.api import build_preview_geometry
    from server.preview.core import preview_options
    from server.mesh.builder import _solver_mesher_config
    from server.exports.core import _bare_grid_config

    root = design(cross_section={'exponent':4,'aspect_ratio':1.5}, enclosure={'depth':90,'depth_margin_mm':7,'front_resolution':[20,24,28,32],'back_resolution':[24,28]})
    preview = build_preview_geometry(design_to_mesher_config(root), preview_options('coarse'))
    assert preview.surfaces
    assert all(np.isfinite(surface.positions).all() for surface in preview.surfaces)
    config = _solver_mesher_config(root)
    assert config['mesh']['quadrants'] == 1234
    assert config['mesh']['encFrontResolution'] == '20,24,28,32'
    assert config['enclosure']['depth_margin_mm'] == 7
    assert config['cross_section'] == {'exponent':4,'aspectRatio':1.5}
    assert _bare_grid_config(root)['cross_section'] == config['cross_section']


def test_cad_datums_honour_rectangle_clearance_with_elliptical_section():
    from hornlab_mesher.datums import derive_datums
    from hornlab_mesher.geometry import BuiltGeometry
    from hornlab_mesher.builders.enclosure import enclosure_box_bounds

    root = design(cross_section={'aspect_ratio':1.5}, enclosure={'depth':1,'depth_margin_mm':7,'edge_radius':0})
    geometry = resolve_geometry(design_to_mesher_config(root)).geometry
    bounds = enclosure_box_bounds(geometry.inner_points, geometry.enclosure, closed=True, symmetry_planes=())
    built = BuiltGeometry(surface_groups={}, axial_bounds_mm=(0,60), enclosure_bounds=bounds)
    datums = derive_datums(geometry, built)
    assert datums['WG_ENC_BACK_PLANE']['origin_mm'][2] == pytest.approx(-7)
    assert datums['rim_planar'] is True



def test_symmetry_density_failure_returns_full_domain_with_reason():
    result = resolve_symmetry(design(mesh={'mouth_resolution':'p'}))
    assert result.quadrants == 1234
    assert any('mesher symmetry sampling failed' in reason for reason in result.reasons['xz'])
