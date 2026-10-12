"""Exterior v3 symmetry uses the same completion and orbit inference as FEM."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import os

import numpy as np
import pytest

from server.jobs.models import DriveChannel, ExteriorTransducerSpec
from server.solver import beat_transducer_imported as jobs
from server.solver.beat_adapter import transducers as t
from server.solver.beat_adapter.preflight import UnsupportedPhysics
from server.solver.beat_adapter.results import ResultContractError
from server.solver.component_symmetry import infer_component_symmetry
from server.tests.beat_adapter.test_transducers import FRAME, driver, raw
from server.tests.test_fem_coupled_beat_live import worker as worker_fixture

worker = worker_fixture


def octahedron(make_mesh, axes=(), offset=(0., 0., 0.), *, scale=1.):
    points = np.array([[0, 0, 1], [0, 0, -1], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], float)
    faces = np.array([[0, 2, 3], [0, 3, 4], [0, 4, 5], [0, 5, 2],
                      [1, 3, 2], [1, 4, 3], [1, 5, 4], [1, 2, 5]])
    tags = np.array([2] * 4 + [3] * 4)
    selected = np.ones(len(faces), bool)
    for axis in axes:
        selected &= np.all(points[faces, axis] >= 0, axis=1)
    faces = faces[selected]
    used = np.unique(faces)
    remap = np.zeros(len(points), int)
    remap[used] = np.arange(len(used))
    return make_mesh((points[used] * scale + offset).tolist(), remap[faces].tolist(), tags[selected].tolist())


def symmetry_raw(request):
    result = raw(request, 100.)
    # Derive copy accounting from geometry, independently of component parameters.
    system = request.wire['compiled_system']
    component = system['components'][0]
    tags = [b['group']['tag'] for b in system['boundaries'] if b['id'] in component['boundary_ids']]
    faces = request.mesh.faces[np.isin(request.mesh.tags, tags)]
    axes = {'off': (), 'x': (0,), 'xy': (0, 1)}[request.wire['solver_options']['symmetry']]
    inferred = infer_component_symmetry({'exterior': [(faces, request.mesh.points_m[faces])]},
                                       component['parameters']['motion_axis'], axes)
    completion = inferred['surface_completion_factor']
    orbit = inferred['physical_driver_orbit_count']
    for quantity in result['quantities']:
        if quantity['quantity'] in {'diaphragm_velocity', 'voice_coil_current', 'radiation_impedance_matrix'}:
            quantity['metadata'].update(surface_completion_factors=[completion], physical_driver_orbit_counts=[orbit])
        if quantity['quantity'] == 'radiation_impedance_matrix':
            quantity['metadata']['row_weights'] = [orbit]
    return result


@pytest.mark.parametrize('axes,mode,completion', [((), 'full', 1), ((0,), 'yz', 2), ((0, 1), 'yz+xz', 4)])
def test_exterior_central_driver_wire_preflight_and_fake_phasor_accounting(make_mesh, layout, axes, mode, completion):
    from beat_engine.beat_contract import validate_solve_request
    mesh = octahedron(make_mesh, axes)
    spec = driver(tags=(2,), axis=[0, 0, 1], sd_m2=1.8, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    request = t.build_transducer_request(mesh, drivers=[spec], layout=layout, frame=FRAME,
                                         frequencies_hz=[100.], symmetry=mode)
    validate_solve_request(request.wire)
    params = request.wire['compiled_system']['components'][0]['parameters']
    assert params.get('surface_completion_factor', 1) == completion
    assert params.get('physical_driver_orbit_count', 1) == 1
    assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(1.8)
    assert request.driver_sd[spec.id]['geometric_sd_m2'] == pytest.approx(2.)
    assert request.driver_sd[spec.id]['sd_source'] == 'declared'
    assert request.channel_warnings[spec.id] == []
    channel = DriveChannel(id='driver', source_ids=['cone'], motion='axial', exterior_transducer=
        ExteriorTransducerSpec(version=2, motion_axis=(0, 0, 1), re_ohm=6, le_h=.0005,
                              bl_n_per_a=7, mmd_kg=.02, cms_m_per_n=.001, rms_n_s_per_m=1.5))
    record = {'source_tags': {'cone': 2}, 'symmetry': {'domain_planes': ['xy'[a] + '0' for a in axes]}}
    assert jobs.preflight(record, mesh, [channel], backend='cpu') is None
    row = t.parse_transducer_frequency(symmetry_raw(request), request, 100.)
    assert row.input_impedance_ohm(spec.port_id) == pytest.approx(2.83 / (.4 + .2j))
    assert row.excursion_peak_mm[0, 0] == pytest.approx(np.sqrt(2) * 1000 * abs(1 - 2j) / (2 * np.pi * 100))


@pytest.mark.parametrize('mode,orbit', [('yz', 2), ('yz+xz', 4)])
def test_fake_phasor_orbit_current_accounting(make_mesh, layout, mode, orbit):
    mesh = octahedron(make_mesh, offset=(3., 3., 0.))
    spec = driver(tags=(2,), axis=[0, 0, 1])
    request = t.build_transducer_request(mesh, drivers=[spec], layout=layout, frame=FRAME,
                                         frequencies_hz=[100.], symmetry=mode)
    row = t.parse_transducer_frequency(symmetry_raw(request), request, 100.)
    assert row.input_impedance_ohm(spec.port_id) == pytest.approx(2.83 / (orbit * (.4 + .2j)))
    np.testing.assert_allclose(row.current_rms_a, [[.4 + .2j]])
    np.testing.assert_allclose(row.velocity_rms_m_per_s, [[1 - 2j]])


def test_exterior_engine_refuses_off_plane_driver_axis_outside_active_plane(make_mesh, layout):
    mesh = octahedron(make_mesh, offset=(3., 0., 0.))
    with pytest.raises(UnsupportedPhysics, match='Exterior BEAT.*every active symmetry plane'):
        t.build_transducer_request(mesh, drivers=[driver(tags=(2,), axis=[1, 0, 1])],
                                    layout=layout, frame=FRAME, frequencies_hz=[100.], symmetry='yz')


@pytest.mark.parametrize('key', ['surface_completion_factors', 'physical_driver_orbit_counts', 'row_weights'])
def test_exterior_worker_accounting_is_checked(make_mesh, layout, key):
    request = t.build_transducer_request(octahedron(make_mesh, (0,)), drivers=[driver(tags=(2,), axis=[0, 0, 1])],
                                         layout=layout, frame=FRAME, frequencies_hz=[100.], symmetry='yz')
    result = symmetry_raw(request)
    result['quantities'][-1]['metadata'][key] = [99]
    with pytest.raises(ResultContractError, match='physical-copy accounting'):
        t.parse_transducer_frequency(result, request, 100.)
    altered = deepcopy(request.wire)
    altered['compiled_system']['components'][0]['parameters']['surface_completion_factor'] = 1
    with pytest.raises(UnsupportedPhysics, match='inference'):
        t.validate_transducer_request(replace(request, wire=altered))


def test_exterior_y_only_rotation_preserves_inference(make_mesh, layout):
    mesh = octahedron(make_mesh, (1,))
    request = t.build_transducer_request(mesh, drivers=[driver(tags=(2,), axis=[0, 0, 1])],
                                         layout=layout, frame=FRAME, frequencies_hz=[100.], symmetry='xz')
    assert request.wire['solver_options']['symmetry'] == 'x'
    assert request.wire['compiled_system']['components'][0]['parameters']['fractional_symmetry_axes'] == ['x']


def test_exterior_preflight_refuses_open_edges_and_plane_caps(make_mesh):
    params = ExteriorTransducerSpec(version=2, motion_axis=(0, 0, 1), re_ohm=6, le_h=.0005,
                                    bl_n_per_a=7, mmd_kg=.02, cms_m_per_n=.001, rms_n_s_per_m=1.5)
    channel = SimpleNamespace(id='driver', source_ids=['cone'], exterior_transducer=params)
    record = {'source_tags': {'cone': 2}, 'symmetry': {'domain_planes': ['x0']}}
    # An uncapped single patch cannot close by reflection.
    patch = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [2])
    assert 'open edges away' in jobs.preflight(record, patch, [channel], backend='cpu')
    capped = make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                       [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], [2, 3, 3, 3])
    assert 'without plane caps' in jobs.preflight(record, capped, [channel], backend='cpu')



def test_fake_worker_derives_accounting_independently_of_declared_parameters(make_mesh, layout):
    request = t.build_transducer_request(octahedron(make_mesh, (0,)),
        drivers=[driver(tags=(2,), axis=[0, 0, 1])], layout=layout, frame=FRAME,
        frequencies_hz=[100.], symmetry='yz')
    request.wire['compiled_system']['components'][0]['parameters']['surface_completion_factor'] = 1
    result = symmetry_raw(request)
    assert result['quantities'][-1]['metadata']['surface_completion_factors'] == [2]
    with pytest.raises(ResultContractError, match='physical-copy accounting'):
        t.parse_transducer_frequency(result, request, 100.)


@pytest.mark.parametrize('mode', ['full', 'yz'])
def test_exterior_disconnected_moving_tags_refused_at_compile_and_admission(make_mesh, layout, mode):
    # The octahedron's opposite equatorial triangles meet only at a vertex.
    mesh = octahedron(make_mesh, offset=(3., 3., 0.))
    from server.solver.beat_adapter.mesh import read_surface
    parsed = read_surface(mesh)
    tags = parsed.tags.copy()
    tags[:] = 3
    tags[[0, 2]] = 2
    mesh = make_mesh(parsed.points_m.tolist(), parsed.faces.tolist(), tags.tolist())
    with pytest.raises(UnsupportedPhysics, match='2 separate surfaces in exterior') as caught:
        t.build_transducer_request(mesh, drivers=[driver(tags=(2,), axis=[0, 0, 1])],
                                  layout=layout, frame=FRAME, frequencies_hz=[100.], symmetry=mode)
    assert caught.value.code == 'driver_disconnected_patches'
    params = ExteriorTransducerSpec(version=2, motion_axis=(0, 0, 1), re_ohm=6, le_h=.0005,
                                    bl_n_per_a=7, mmd_kg=.02, cms_m_per_n=.001, rms_n_s_per_m=1.5)
    channel = SimpleNamespace(id='driver', source_ids=['cone'], exterior_transducer=params)
    record = {'source_tags': {'cone': 2}, 'symmetry': {'domain_planes': [] if mode == 'full' else ['x0']}}
    from server.solver.coupled_admission import driver_surface_problem
    assert driver_surface_problem(record, [channel], exterior_only=True, msh_text=mesh)[0] == 'driver_disconnected_patches'
    assert '2 separate surfaces' in jobs.preflight(record, mesh, [channel], backend='cpu')


@pytest.mark.live
@pytest.mark.slow
@pytest.mark.real_runtime
@pytest.mark.skipif(os.environ.get('WG2_RUN_LIVE') != '1', reason='set WG2_RUN_LIVE=1')
def test_live_exterior_only_full_half_zin_and_summed_spl_agree(make_mesh, layout, worker):
    from server.solver.official_beat import solve_transducer_compiled

    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    spec = driver(tags=(2,), axis=[0, 0, 1])
    requests = [t.build_transducer_request(octahedron(make_mesh, axes, scale=.01), drivers=[spec],
        layout=layout, frame=FRAME, frequencies_hz=[100., 600.], symmetry=mode)
        for axes, mode in (((), 'full'), ((0,), 'yz'))]
    full, half = [solve_transducer_compiled(r, worker_manager=manager) for r in requests]
    for complete, reduced in zip(full.rows, half.rows):
        np.testing.assert_allclose(reduced.input_impedance_ohm(spec.port_id),
                                   complete.input_impedance_ohm(spec.port_id), rtol=1e-4, atol=0)
        for plane, pressure in complete.pressure_rms_pa.items():
            np.testing.assert_allclose(reduced.pressure_rms_pa[plane], pressure, rtol=1e-4, atol=0)
            np.testing.assert_allclose(20 * np.log10(np.abs(reduced.pressure_rms_pa[plane]) / 20e-6),
                                       20 * np.log10(np.abs(pressure) / 20e-6), rtol=1e-4, atol=0)
