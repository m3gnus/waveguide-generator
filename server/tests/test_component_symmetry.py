"""Driver completion, physical orbits and voltage mapping on reduced meshes."""
from copy import deepcopy
from dataclasses import replace
import os
from types import SimpleNamespace

import numpy as np
import pytest

from server.jobs.models import DriveChannel
from server.solver.beat_adapter.coupled_request import build_coupled_request
from server.solver.beat_adapter.results import map_coupled_sweep, ResultContractError
from server.solver.component_symmetry import infer_component_symmetry
from server.solver.coupled_system import ExteriorSurface
from server.solver.result_mapping import map_coupled_transducer_response
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.test_fem_coupled_beat_live import worker as worker_fixture
from server.tests.test_fem_electrodynamic import driver
from server.tests.test_fem_engine_route import FRAME, compiled, events, region_input
from server.tests.test_official_beat_bridge import runtime as runtime_fixture

worker = worker_fixture
runtime = runtime_fixture


def skin(points, faces):
    points, faces = np.asarray(points, float), np.asarray(faces, int)
    return (faces, points[faces])


@pytest.mark.parametrize("axes,x,y,cut,completion,orbit", [
    ((), 0., 0., [], 1, 1),
    ((0,), 0., 0., ['x'], 2, 1),
    ((0, 1), 0., 0., ['x', 'y'], 4, 1),
    ((0,), .1, .1, [], 1, 2),
    ((0, 1), 0., .1, ['x'], 2, 2),
    ((0, 1), .1, .1, [], 1, 4),
])
def test_central_fractional_and_off_plane_representatives(axes, x, y, cut, completion, orbit):
    patch = skin([[x, y, 0], [x + .02, y, 0], [x, y + .02, 0]], [[0, 1, 2]])
    params = infer_component_symmetry({'mesh': [patch]}, [0, 0, 1], axes)
    assert params == {'symmetry_role': 'fractional_driver' if cut else 'complete_representative',
                      'fractional_symmetry_axes': cut, 'surface_completion_factor': completion,
                      'physical_driver_orbit_count': orbit}
    assert completion * orbit == 2 ** len(axes)


def test_group_union_ignores_internal_plane_seams():
    # The x0 edge separates two selected groups and disappears in their union.
    points = [[0, 0, 0], [0, .02, 0], [.02, 0, 0], [.02, .02, 0]]
    groups = [skin(points, [[0, 1, 2]]), skin(points, [[1, 0, 3]])]
    params = infer_component_symmetry({'mesh': groups}, [1, 0, 1], (0,))
    assert params['fractional_symmetry_axes'] == []
    assert params['physical_driver_orbit_count'] == 2


def test_front_and_rear_meshes_do_not_cancel_cut_perimeters():
    points = [[0, 0, 0], [.02, 0, 0], [0, .02, 0]]
    params = infer_component_symmetry({'front': [skin(points, [[0, 1, 2]])],
                                       'rear': [skin(points, [[0, 2, 1]])]}, [0, 0, 1], (0, 1))
    assert params['surface_completion_factor'] == 4


def test_disconnected_patches_with_different_cuts_are_refused_by_channel():
    groups = [skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]]),
              skin([[.1, 0, 0], [.12, 0, 0], [.1, .02, 0]], [[0, 1, 2]])]
    with pytest.raises(ValueError, match="Channel 'woofer'.*different symmetry cuts"):
        infer_component_symmetry({'mesh': groups}, [0, 0, 1], (0,), channel_id='woofer')


@pytest.mark.parametrize('axes,axis,plane', [((0,), [1, 0, 1], 'x0'), ((0, 1), [0, 1, 1], 'y0')])
def test_axis_outside_cutting_plane_refused(axes, axis, plane):
    patch = skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]])
    with pytest.raises(ValueError, match=f"Channel 'woofer'.*motion_axis.*{plane}"):
        infer_component_symmetry({'mesh': [patch]}, axis, axes, channel_id='woofer')


def test_compilation_refuses_axis_outside_cut_before_worker_acquisition(runtime):
    raw = driver().model_dump(mode='json')
    raw['motion_axis'] = [1., 0., 1.]
    with pytest.raises(ValueError, match="Channel 'fem'.*cutting plane x0"):
        build_coupled_request(None, [region_input()], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry='yz', transducers={'fem': raw})
    assert runtime.calls == []


@pytest.mark.parametrize('mode', ['full', 'yz', 'yz+xz'])
def test_missing_chamber_sd_precedes_driver_symmetry_geometry(mode):
    raw = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}).model_dump(mode='json')
    raw['motion_axis'] = [1., 0., 1.]
    with pytest.raises(ValueError, match="driver_sd_required: Enter Sd.*channel 'fem'"):
        build_coupled_request(None, [region_input()], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry=mode, transducers={'fem': raw})


@pytest.mark.parametrize('completion', [1, 2, 4])
def test_completed_weighted_geometry_never_changes_declared_chamber_sd(completion):
    from server.solver.beat_adapter.coupled_transducers import mechanical_impedance, parameters

    patch = skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]])
    # Weight and completion cancel exactly: geometric Sd is the full declared Sd.
    weights = {'mesh': [np.array([1. / completion])]}
    spec = driver(sd_m2=.0002, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    metadata, notes = {}, []
    params, area = parameters(spec, {'mesh': [patch]}, face_weights=weights,
                              surface_completion_factor=completion, sd_metadata=metadata,
                              collected_warnings=notes)
    assert area == pytest.approx(.0002)
    assert metadata['geometric_sd_m2'] == pytest.approx(.0002)
    assert metadata['sd_source'] == 'declared'
    assert notes == []
    dry = driver().model_dump()
    added_load = mechanical_impedance(params, 100., density=1.21, sound_speed=343.) - mechanical_impedance(
        dry, 100., density=1.21, sound_speed=343.)
    assert added_load == pytest.approx(1.21 * 343**2 * .0002**2 / .002 / (-1j * 2 * np.pi * 100))


def reflect_mesh(points, groups, axis, tetrahedra=None):
    """Reflect the same elements, joining only vertices exactly on the plane."""
    off_plane = points[:, axis] != 0
    mirrored = points[off_plane].copy()
    mirrored[:, axis] *= -1
    mapping = np.arange(len(points))
    mapping[off_plane] = len(points) + np.arange(len(mirrored))
    reflected_groups = {}
    for name, faces in groups.items():
        faces = faces[~np.all(points[faces, axis] == 0, axis=1)]
        if len(faces):
            reflected_groups[name] = np.concatenate([faces, mapping[faces[:, [0, 2, 1]]]])
    tets = None if tetrahedra is None else np.concatenate([tetrahedra, mapping[tetrahedra[:, [1, 0, 2, 3]]]])
    return np.concatenate([points, mirrored]), reflected_groups, tets


def reflect_inputs(exterior, item, axis):
    p, g, t = reflect_mesh(item.domain.points_mm, item.domain.surfaces, axis, item.domain.tetrahedra)
    item = replace(item, domain=replace(item.domain, points_mm=p, surfaces=g, tetrahedra=t))
    if exterior is not None:
        p, g, _ = reflect_mesh(exterior.points_mm, exterior.groups, axis)
        exterior = replace(exterior, points_mm=p, groups=g)
    return exterior, item


def synthetic_request(mode, *, coupled=True, offset=0., chamber=False):
    item = region_input()
    exterior = None
    if coupled:
        groups = {name: faces[~np.any(np.all(item.domain.points_mm[faces, :2] == 0, axis=1), axis=1)]
                  for name, faces in item.domain.surfaces.items()}
        groups = {name: faces for name, faces in groups.items() if len(faces)}
        exterior = ExteriorSurface(item.domain.points_mm, groups, {
            name: {'kind': 'moving' if 'moving' in name else 'rigid', 'drive_channel_id': 'fem'}
            for name in groups})
    if mode in ('full', 'yz'):
        exterior, item = reflect_inputs(exterior, item, 1)
    if mode == 'full':
        exterior, item = reflect_inputs(exterior, item, 0)
    if offset:
        shift = np.array([offset, 0., 0.])
        item = replace(item, domain=replace(item.domain, points_mm=item.domain.points_mm + shift))
        if exterior is not None:
            exterior = replace(exterior, points_mm=exterior.points_mm + shift)
    spec = driver(sd_m2=.00018 if chamber else None,
                  lumped_sealed_rear_chamber={'enabled': chamber, 'volume_m3': .002})
    return build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
                                 frequencies_hz=[100.], symmetry=mode, transducers={'fem': spec})


@pytest.mark.parametrize('coupled', [False, True])
@pytest.mark.parametrize('mode,completion', [('full', 1), ('yz', 2), ('yz+xz', 4)])
def test_compiled_driver_contract_and_full_area(coupled, mode, completion):
    from beat_engine.beat_contract import validate_solve_request
    request = synthetic_request(mode, coupled=coupled, chamber=not coupled)
    validate_solve_request(request.wire)
    system = request.wire['compiled_system']
    params = system['components'][0]['parameters']
    assert params['surface_completion_factor'] == completion
    assert params['physical_driver_orbit_count'] == 1
    assert system['metadata']['projected_areas_m2']['fem'] == pytest.approx(.0002 if coupled else .00018)
    assert system['metadata']['driver_sd']['fem']['geometric_sd_m2'] == pytest.approx(.0002)
    assert system['metadata'].get('warnings', []) == []
    if not coupled:
        assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(.00018)
    map_coupled_sweep(events(request), request)


def mapped(request, sweep=None):
    if sweep is None:
        sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': [100.], 'metadata': {}}
    map_coupled_transducer_response(response, sweep, request, 'fem',
                                    DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver()))
    return response


@pytest.mark.parametrize('coupled', [False, True])
def test_fake_worker_full_half_quarter_zin_excursion_and_acoustic_mapping(coupled):
    responses = [mapped(synthetic_request(mode, coupled=coupled)) for mode in ('full', 'yz', 'yz+xz')]
    full = responses[0]
    for reduced in responses[1:]:
        assert reduced['impedance'] == full['impedance']
        assert reduced['acoustic_impedance'] == full['acoustic_impedance']
        assert reduced['metadata']['driver'] == full['metadata']['driver']
        assert reduced['metadata']['acoustic_impedance'] == full['metadata']['acoustic_impedance']


def test_off_plane_orbit_aggregates_current_and_power_without_scaling_excursion():
    # One complete half-domain driver represents two parallel physical drivers.
    full = mapped(synthetic_request('full', coupled=False))
    request = synthetic_request('yz', coupled=False, offset=20.)
    reduced = mapped(request)
    meta = reduced['metadata']
    assert meta['beat_transducer']['physical_driver_orbit_counts'] == [2]
    assert reduced['impedance']['real'][0] == pytest.approx(full['impedance']['real'][0] / 2)
    assert meta['driver']['input_power_w']['values'][0] == pytest.approx(
        2 * full['metadata']['driver']['input_power_w']['values'][0])
    assert meta['driver']['cone_excursion_mm'] == full['metadata']['driver']['cone_excursion_mm']
    assert meta['beat_transducer']['current_rms_a'] == full['metadata']['beat_transducer']['current_rms_a']


@pytest.mark.parametrize('key', ['surface_completion_factors', 'physical_driver_orbit_counts'])
def test_worker_copy_accounting_must_match_inference(key):
    request = synthetic_request('yz+xz')
    raw = coupled_result(request.wire, 100.)
    next(q for q in raw['quantities'] if q['quantity'] == 'voice_coil_current')['metadata'][key] = [99]
    with pytest.raises(ResultContractError, match='physical-copy accounting'):
        map_coupled_sweep(iter([{'type': 'result', 'result': raw}]), request)


def test_fem_moving_groups_share_a_driver_across_regions():
    from server.solver.coupled_admission import fem_drive_problem
    record = deepcopy(region_input().record)
    record['surface_groups'].append({'group_id': 'surround', 'kind': 'moving', 'drive_channel_id': 'fem'})
    assert fem_drive_problem([record], {}, painted_sources=set(), electrodynamic_channels={'fem'}) is None
    assert fem_drive_problem([record], {}, painted_sources=set()) is None
    other = deepcopy(record)
    other['region_id'] = 'other'
    assert fem_drive_problem([record, other], {}, painted_sources=set(), electrodynamic_channels={'fem'}) is None
    assert fem_drive_problem([record, other], {}, painted_sources=set())[0] == 'fem_multiple_diaphragms_unsupported'


def test_split_fem_groups_compile_as_union_and_replace_stale_symmetry():
    _, item = reflect_inputs(None, region_input(), 1)
    groups = dict(item.domain.surfaces)
    cone = next(name for name in groups if 'moving' in name)
    groups[cone], surround = groups[cone][:1], groups[cone][1:]
    groups[cone.replace('group=cone', 'group=surround')] = surround
    record = deepcopy(item.record)
    record['surface_groups'].append({'group_id': 'surround', 'kind': 'moving', 'drive_channel_id': 'fem'})
    item = replace(item, record=record, domain=replace(item.domain, surfaces=groups))
    raw = driver().model_dump(mode='json')
    raw.update(symmetry_role='complete_representative', surface_completion_factor=1,
               physical_driver_orbit_count=4, fractional_symmetry_axes=['y'])
    request = build_coupled_request(None, [item], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], symmetry='yz', transducers={'fem': raw})
    params = request.wire['compiled_system']['components'][0]['parameters']
    assert params['fractional_symmetry_axes'] == ['x']
    assert params['surface_completion_factor'] == 2
    assert params['physical_driver_orbit_count'] == 1
    assert request.wire['compiled_system']['metadata']['projected_areas_m2']['fem'] == pytest.approx(.0002)


def test_coupled_axis_rotates_with_y_only_image_frame():
    raw = driver().model_dump(mode='json')
    raw['motion_axis'] = [1., 0., 1.]
    request = build_coupled_request(None, [region_input()], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], symmetry='xz', transducers={'fem': raw})
    params = request.wire['compiled_system']['components'][0]['parameters']
    assert params['fractional_symmetry_axes'] == ['x']
    np.testing.assert_allclose(params['motion_axis'], [0, -1 / np.sqrt(2), 1 / np.sqrt(2)])
    assert raw['motion_axis'] == [1., 0., 1.]


def test_managed_fake_worker_full_reduced_mapping_and_session_reuse(runtime):
    from server.solver.official_beat import solve_coupled_compiled

    def mutate(raw):
        raw.clear()
        raw.update(coupled_result(runtime.requests[-1], 100.))
    runtime.mutate = mutate
    results = []
    for mode in ('full', 'yz', 'yz+xz'):
        request = synthetic_request(mode)
        results.append(mapped(request, solve_coupled_compiled(request)))
    for reduced in results[1:]:
        assert reduced['impedance'] == results[0]['impedance']
        assert reduced['metadata']['driver'] == results[0]['metadata']['driver']
    assert len(runtime.workers) == 1
    assert len(runtime.requests) == 3


def v7_requests(root):
    from server.tests.test_fem_symmetry import mesh_pair, inputs

    built, region = mesh_pair(root / 'quarter', ('x0', 'y0'))
    exterior, item = inputs(built, region)
    quarter = exterior, item
    half = reflect_inputs(*quarter, 1)
    full = reflect_inputs(*half, 0)
    requests = []
    for (exterior, item), mode in ((full, 'full'), (half, 'yz'), (quarter, 'yz+xz')):
        requests.append(build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
                                             frequencies_hz=[100., 600.], symmetry=mode,
                                             transducers={'drive-lf': driver()}))
    return requests


def test_v7_mirrored_mesh_requests_match_pinned_engine_contract(tmp_path):
    from beat_engine.beat_contract import validate_solve_request
    requests = v7_requests(tmp_path)
    areas = []
    for request in requests:
        validate_solve_request(request.wire)
        areas.append(request.wire['compiled_system']['metadata']['projected_areas_m2']['drive-lf'])
    np.testing.assert_allclose(areas, areas[0], rtol=1e-12, atol=0)


@pytest.mark.live
@pytest.mark.slow
@pytest.mark.real_runtime
@pytest.mark.skipif(os.environ.get('WG2_RUN_LIVE') != '1', reason='set WG2_RUN_LIVE=1')
def test_live_v7_full_x_xy_driver_impedance_excursion_and_field_agree(tmp_path, worker):
    from server.solver.official_beat import solve_coupled_compiled

    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    results = [solve_coupled_compiled(request, worker_manager=manager) for request in v7_requests(tmp_path)]
    for reduced in results[1:]:
        assert np.all(np.abs(results[0].current) > 0)
        assert np.all(np.abs(results[0].velocity) > 0)
        np.testing.assert_allclose(reduced.current, results[0].current, rtol=1e-4, atol=0)
        np.testing.assert_allclose(reduced.velocity, results[0].velocity, rtol=1e-4, atol=0)
        np.testing.assert_allclose(2.83 / reduced.current, 2.83 / results[0].current, rtol=1e-4, atol=0)
        frequencies = np.array([100., 600.])[:, None, None]
        excursion = lambda result: np.sqrt(2) * 1000 * np.abs(result.velocity) / (2 * np.pi * frequencies)
        np.testing.assert_allclose(excursion(reduced), excursion(results[0]), rtol=1e-4, atol=0)
        np.testing.assert_allclose(reduced.channels['drive-lf'].pressure_complex,
                                   results[0].channels['drive-lf'].pressure_complex, rtol=1e-4, atol=0)
