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
from server.solver.component_symmetry import DriverSymmetryError, infer_component_symmetry
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


@pytest.mark.parametrize('axes', [(), (0,)])
def test_disconnected_patches_in_one_mesh_are_refused_by_channel(axes):
    groups = [skin([[.03, 0, 0], [.05, 0, 0], [.03, .02, 0]], [[0, 1, 2]]),
              skin([[.1, 0, 0], [.12, 0, 0], [.1, .02, 0]], [[0, 1, 2]])]
    with pytest.raises(DriverSymmetryError, match="Channel 'woofer': its moving faces form 2 separate drivers") as caught:
        infer_component_symmetry({'mesh': groups}, [0, 0, 1], axes, channel_id='woofer')
    assert caught.value.code == 'driver_disconnected_patches'
    assert 'give each driver its own drive channel' in str(caught.value)
    assert 'make them overlap along the motion axis / paint the rim as moving' in str(caught.value)


@pytest.mark.parametrize('axes,axis,plane', [((0,), [1, 0, 1], 'x0'), ((0, 1), [0, 1, 1], 'y0')])
def test_axis_outside_cutting_plane_refused(axes, axis, plane):
    patch = skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]])
    with pytest.raises(DriverSymmetryError, match=f"Channel 'woofer'.*motion_axis.*{plane}.*Force full domain"):
        infer_component_symmetry({'mesh': [patch]}, axis, axes, channel_id='woofer')


def test_compilation_refuses_axis_outside_cut_before_worker_acquisition(runtime):
    raw = driver().model_dump(mode='json')
    raw['motion_axis'] = [1., 0., 1.]
    with pytest.raises(DriverSymmetryError, match="Channel 'fem'.*cutting plane x0.*Force full domain"):
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
def test_fake_worker_full_half_quarter_response_accounting(coupled):
    # Constant fake phasors verify mapping only, not acoustic or LEM parity.
    responses = [mapped(synthetic_request(mode, coupled=coupled)) for mode in ('full', 'yz', 'yz+xz')]
    full = responses[0]
    for reduced in responses[1:]:
        assert reduced['impedance'] == full['impedance']
        assert reduced['acoustic_impedance'] == full['acoustic_impedance']
        assert reduced['metadata']['driver'] == full['metadata']['driver']
        assert reduced['metadata']['acoustic_impedance'] == full['metadata']['acoustic_impedance']


def test_off_plane_orbit_aggregates_current_and_power_without_scaling_excursion():
    # One complete half-domain driver represents two parallel physical drivers.
    request = synthetic_request('yz', coupled=False, offset=20.)
    sweep = map_coupled_sweep(events(request), request)
    response = mapped(request, sweep)
    meta = response['metadata']
    current = sweep.current[0, 0, 0]
    assert meta['beat_transducer']['physical_driver_orbit_counts'] == [2]
    assert response['impedance']['real'][0] == pytest.approx((2.83 / (2 * current)).real)
    power = meta['driver']['input_power_w']
    assert power['values'][0] == pytest.approx((2.83 * np.conj(2 * current)).real)
    assert power['channel_current_rms_a'][0] == {'real': (2 * current).real, 'imaginary': -(2 * current).imag}
    assert 'current_rms_a' not in power
    assert meta['beat_transducer']['current_rms_a'][0][0] == {'real': current.real, 'imaginary': current.imag}
    assert meta['driver']['cone_excursion_mm']['values'][0] == pytest.approx(
        np.sqrt(2) * 1000 * abs(sweep.velocity[0, 0, 0]) / (2 * np.pi * 100))


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


def test_adjacent_fem_groups_admit_and_compile_as_union_and_replace_stale_symmetry(monkeypatch):
    _, item = reflect_inputs(None, region_input(), 1)
    groups = dict(item.domain.surfaces)
    cone = next(name for name in groups if 'moving' in name)
    groups[cone], surround = groups[cone][:1], groups[cone][1:]
    assert len(groups[cone]) == len(surround) == 1
    assert len(set(groups[cone][0]) & set(surround[0])) == 2
    groups[cone.replace('group=cone', 'group=surround')] = surround
    record = deepcopy(item.record)
    record['surface_groups'].append({'group_id': 'surround', 'kind': 'moving', 'drive_channel_id': 'fem'})
    item = replace(item, record=record, domain=replace(item.domain, surfaces=groups))
    from server.solver.coupled_admission import fem_drive_problem, fem_request_problem

    assert fem_drive_problem([record], {}, painted_sources=set()) is None
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: item.domain)
    retained = {'fem_regions': [dict(record, mesh_store_path='retained.msh')],
                'symmetry': {'domain_planes': ['x0']}}
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    assert fem_request_problem(retained, [channel]) is None
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


def test_managed_fake_worker_response_accounting_and_session_reuse(runtime):
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
    requests = v7_requests(tmp_path)
    results = [solve_coupled_compiled(request, worker_manager=manager) for request in requests]
    impedances = [mapped_impedance(request, result, 'drive-lf') for request, result in zip(requests, results)]
    for index, reduced in enumerate(results[1:], 1):
        assert np.all(np.abs(results[0].current) > 0)
        assert np.all(np.abs(results[0].velocity) > 0)
        np.testing.assert_allclose(reduced.current, results[0].current, rtol=1e-4, atol=0)
        np.testing.assert_allclose(reduced.velocity, results[0].velocity, rtol=1e-4, atol=0)
        np.testing.assert_allclose(impedances[index], impedances[0], rtol=1e-4, atol=0)
        frequencies = np.array([100., 600.])[:, None, None]
        excursion = lambda result: np.sqrt(2) * 1000 * np.abs(result.velocity) / (2 * np.pi * frequencies)
        np.testing.assert_allclose(excursion(reduced), excursion(results[0]), rtol=1e-4, atol=0)
        np.testing.assert_allclose(reduced.channels['drive-lf'].pressure_complex,
                                   results[0].channels['drive-lf'].pressure_complex, rtol=1e-4, atol=0)



def mapped_impedance(request, sweep, channel):
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_transducer_response(response, sweep, request, channel,
        DriveChannel(id=channel, source_ids=[], motion='axial', exterior_transducer=driver()))
    z = response['impedance']
    return np.asarray(z['real']) + 1j * np.asarray(z['imaginary'])


@pytest.mark.parametrize('failure', ['axis', 'disconnected'])
def test_admission_checks_retained_driver_geometry_before_engine_probe(monkeypatch, failure):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request

    _, item = reflect_inputs(None, region_input(), 1)
    raw = driver().model_dump(mode='json')
    if failure == 'axis':
        raw['motion_axis'] = [1., 0., 1.]
        code = 'driver_symmetry_unsupported'
        remedy = 'Force full domain'
        planes = ['x0']
    else:
        item = replace(item, domain=replace(item.domain, points_mm=item.domain.points_mm + [20, 0, 0]))
        _, item = reflect_inputs(None, item, 0)
        code = 'driver_disconnected_patches'
        remedy = 'give each driver its own drive channel'
        planes = []
    record = deepcopy(item.record)
    record['mesh_store_path'] = 'retained.msh'
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: item.domain)
    request = _request('wgi_' + '0' * 26)
    request.options.engine = 'beat-cpu'
    request.geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=raw)]
    refusal = _imported_request_refusal(request, {'fem_regions': [record], 'symmetry': {'domain_planes': planes}})
    assert refusal[0] == code
    assert remedy in refusal[1]
    if failure == 'disconnected':
        with pytest.raises(DriverSymmetryError, match=remedy):
            build_coupled_request(None, [item], layout=compiled().layout, frame=FRAME,
                                  frequencies_hz=[100.], transducers={'fem': driver()})


@pytest.mark.parametrize('mode,planes', [('full', []), ('yz', ['x0'])])
@pytest.mark.parametrize('declared,code', [(None, 'driver_sd_required'), (.0001, 'driver_disconnected_patches')])
def test_chamber_sd_and_connectivity_refusals_match_admission_and_compile(monkeypatch, mode, planes, declared, code):
    from server.solver.coupled_admission import fem_request_problem

    _, item = reflect_inputs(None, region_input(), 1)
    item = replace(item, domain=replace(item.domain, points_mm=item.domain.points_mm + [20, 0, 0]))
    _, item = reflect_inputs(None, item, 0)
    item = replace(item, domain=replace(item.domain, points_mm=item.domain.points_mm + [50, 0, 0]))
    record = dict(item.record, mesh_store_path='retained.msh')
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: item.domain)
    spec = driver(sd_m2=declared, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=spec)
    assert fem_request_problem({'fem_regions': [record], 'symmetry': {'domain_planes': planes}},
                               [channel], enabled=True)[0] == code
    with pytest.raises(ValueError) as caught:
        build_coupled_request(None, [item], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], symmetry=mode, transducers={'fem': spec})
    assert getattr(caught.value, 'code', getattr(caught.value, 'reason_code', None)) == code


def test_shared_driver_in_different_fem_meshes_is_allowed():
    from server.solver.coupled_admission import fem_drive_problem

    items = [region_input('front'), region_input('rear')]
    assert fem_drive_problem([i.record for i in items], {}, painted_sources=set(),
                             electrodynamic_channels={'fem'}) is None
    request = build_coupled_request(None, items, layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], transducers={'fem': driver()})
    assert len(request.wire['compiled_system']['components']) == 1


def exterior_driven_inputs(root):
    """V7 chamber coupled through its mouth, driven on the outer BEM bottom."""
    from server.tests.test_fem_symmetry import mesh_pair, inputs

    built, region = mesh_pair(root / 'quarter', ('x0', 'y0'))
    exterior, item = inputs(built, region)
    record = deepcopy(item.record)
    for group in record['surface_groups']:
        if group['kind'] == 'moving':
            group['kind'] = 'rigid'
            group.pop('drive_channel_id', None)
    surfaces = {name.replace('kind=moving', 'kind=rigid'): faces
                for name, faces in item.domain.surfaces.items()}
    item = replace(item, record=record, domain=replace(item.domain, surfaces=surfaces))
    groups, kinds, moving = {}, {}, []
    bottom = exterior.points_mm[:, 2].min()
    for name, faces in exterior.groups.items():
        selected = np.all(np.isclose(exterior.points_mm[faces, 2], bottom, atol=1e-8), axis=1)
        moving.extend(faces[selected])
        if np.any(~selected):
            groups[name] = faces[~selected]
            kinds[name] = exterior.kinds[name]
    assert moving
    groups['exterior-driver'] = np.asarray(moving)
    kinds['exterior-driver'] = {'kind': 'moving', 'drive_channel_id': 'drive-lf'}
    quarter = replace(exterior, groups=groups, kinds=kinds), item
    half = reflect_inputs(*quarter, 1)
    full = reflect_inputs(*half, 0)
    return full, half


def exterior_driven_requests(root):
    return [build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
        frequencies_hz=[100., 600.], symmetry=mode, transducers={'drive-lf': driver()})
        for (exterior, item), mode in zip(exterior_driven_inputs(root), ('full', 'yz'))]


def orbit_two_requests(root):
    """One complete off-plane representative versus two independent full drivers."""
    from server.tests.test_fem_symmetry import mesh_pair, inputs

    built, region = mesh_pair(root / 'quarter', ('x0', 'y0'))
    exterior, item = inputs(built, region)
    exterior, item = reflect_inputs(*reflect_inputs(exterior, item, 1), 0)
    shift = np.array([60., 0., 0.])
    item = replace(item, domain=replace(item.domain, points_mm=item.domain.points_mm + shift))
    exterior = replace(exterior, points_mm=exterior.points_mm + shift)
    half = build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
        frequencies_hz=[100., 600.], symmetry='yz', transducers={'drive-lf': driver()})
    exterior, item = reflect_inputs(exterior, item, 0)
    record = deepcopy(item.record)
    declarations = {g['group_id']: g for g in record['surface_groups']}
    groups, new_groups = {}, []
    for name, faces in item.domain.surfaces.items():
        if 'kind=moving' not in name:
            groups[name] = faces
            continue
        group_id = next(part.split('=', 1)[1] for part in name.split('|') if part.startswith('group='))
        positive = item.domain.points_mm[faces, 0].mean(axis=1) > 0
        for side, mask in (('right', positive), ('left', ~positive)):
            groups[name.replace(f'group={group_id}', f'group={group_id}-{side}')] = faces[mask]
            new_groups.append(dict(declarations[group_id], group_id=f'{group_id}-{side}', drive_channel_id=side))
    record['surface_groups'] = [g for g in record['surface_groups'] if g['kind'] != 'moving'] + new_groups
    item = replace(item, record=record, domain=replace(item.domain, surfaces=groups))
    full = build_coupled_request(exterior, [item], layout=compiled().layout, frame=FRAME,
        frequencies_hz=[100., 600.], transducers={'left': driver(), 'right': driver()})
    return full, half


@pytest.mark.parametrize('case', ['orbit-two', 'exterior-side'])
def test_live_comparison_requests_validate_without_running_worker(tmp_path, case):
    from beat_engine.beat_contract import validate_solve_request

    requests = orbit_two_requests(tmp_path) if case == 'orbit-two' else exterior_driven_requests(tmp_path)
    for request in requests:
        validate_solve_request(request.wire)
    counts = [c['parameters']['physical_driver_orbit_count']
              for c in requests[1].wire['compiled_system']['components']]
    assert counts == ([2] if case == 'orbit-two' else [1])
    if case == 'orbit-two':
        assert set(requests[0].channel_ports) == {'left', 'right'}
    else:
        system = requests[1].wire['compiled_system']
        ids = set(system['components'][0]['boundary_ids'])
        assert all(b['region_id'] == 'region:exterior-air' for b in system['boundaries'] if b['id'] in ids)


@pytest.mark.live
@pytest.mark.slow
@pytest.mark.real_runtime
@pytest.mark.skipif(os.environ.get('WG2_RUN_LIVE') != '1', reason='set WG2_RUN_LIVE=1')
def test_live_orbit_two_per_driver_zin_and_summed_spl_agree(tmp_path, worker):
    from server.solver.official_beat import solve_coupled_compiled

    full_request, half_request = orbit_two_requests(tmp_path)
    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    full = solve_coupled_compiled(full_request, worker_manager=manager)
    half = solve_coupled_compiled(half_request, worker_manager=manager)
    # Simultaneously drive both independent full-model voltage bases. Include
    # induced currents from the other basis before mapping each driver's Zin.
    combined = replace(full,
        current=np.repeat(full.current.sum(axis=1, keepdims=True), full.current.shape[1], axis=1),
        velocity=np.repeat(full.velocity.sum(axis=1, keepdims=True), full.velocity.shape[1], axis=1))
    per_driver_half_zin = 2 * mapped_impedance(half_request, half, 'drive-lf')
    for channel in ('left', 'right'):
        np.testing.assert_allclose(mapped_impedance(full_request, combined, channel), per_driver_half_zin,
                                   rtol=1e-4, atol=0)
    pressure = sum(full.channels[c].pressure_complex for c in ('left', 'right'))
    np.testing.assert_allclose(half.channels['drive-lf'].pressure_complex, pressure, rtol=1e-4, atol=0)
    np.testing.assert_allclose(half.channels['drive-lf'].spl_db,
        20 * np.log10(np.abs(pressure) / 20e-6), rtol=1e-4, atol=0)


@pytest.mark.live
@pytest.mark.slow
@pytest.mark.real_runtime
@pytest.mark.skipif(os.environ.get('WG2_RUN_LIVE') != '1', reason='set WG2_RUN_LIVE=1')
def test_live_coupled_exterior_side_driver_full_half_zin_and_spl_agree(tmp_path, worker):
    from server.solver.official_beat import solve_coupled_compiled

    full_request, half_request = exterior_driven_requests(tmp_path)
    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    full = solve_coupled_compiled(full_request, worker_manager=manager)
    half = solve_coupled_compiled(half_request, worker_manager=manager)
    np.testing.assert_allclose(mapped_impedance(half_request, half, 'drive-lf'),
                               mapped_impedance(full_request, full, 'drive-lf'), rtol=1e-4, atol=0)
    np.testing.assert_allclose(half.channels['drive-lf'].pressure_complex,
                               full.channels['drive-lf'].pressure_complex, rtol=1e-4, atol=0)
    np.testing.assert_allclose(half.channels['drive-lf'].spl_db, full.channels['drive-lf'].spl_db,
                               rtol=1e-4, atol=0)



@pytest.mark.parametrize('failure', ['empty', 'zero-axis', 'invalid-plane'])
def test_inference_refusals_are_unsupported_physics(failure):
    from server.solver.beat_adapter.preflight import UnsupportedPhysics

    selected = {'mesh': [skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]])]}
    axis, planes = [0, 0, 1], ()
    if failure == 'empty':
        selected = {}
    elif failure == 'zero-axis':
        axis = [0, 0, 0]
    else:
        planes = (2,)
    with pytest.raises(UnsupportedPhysics):
        infer_component_symmetry(selected, axis, planes)


def test_admission_plane_tolerance_uses_the_whole_mesh(monkeypatch):
    from server.solver.coupled_admission import driver_surface_problem

    item = region_input()
    points = item.domain.points_mm.copy()
    # Moving-edge vertices are close enough to snap using the full mesh extent,
    # while the moving patch alone would yield a smaller tolerance.
    points[[0, 2], 0] = 5e-5
    points[3, 2] = 1000.
    domain = replace(item.domain, points_mm=points)
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: domain)
    record = {'fem_regions': [dict(item.record, mesh_store_path='retained.msh')],
              'symmetry': {'domain_planes': ['x0']}}
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver().model_copy(update={'motion_axis': (1., 0., 1.)}))
    problem = driver_surface_problem(record, [channel])
    assert problem[0] == 'driver_symmetry_unsupported'
    assert 'Force full domain' in problem[1]


@pytest.mark.parametrize('two_meshes', [False, True])
@pytest.mark.parametrize('axes', [(), (0,)])
def test_front_rear_identity_uses_motion_footprints_in_every_mesh_layout(two_meshes, axes):
    front = skin([[0, 0, 0], [.02, 0, 0], [0, .02, 0]], [[0, 1, 2]])
    rear = (front[0][:, ::-1], front[1][:, ::-1] + [0, 0, -.002])
    meshes = {'front': [front], 'rear': [rear]} if two_meshes else {'mesh': [front, rear]}
    result = infer_component_symmetry(meshes, [0, 0, 1], axes)
    assert result['surface_completion_factor'] == 2 ** len(axes)


@pytest.mark.parametrize('fraction,allowed', [(.49, False), (.5, True), (.75, True)])
def test_driver_footprint_overlap_threshold(fraction, allowed):
    points = np.array([[0, 0, 0], [.02, 0, 0], [.02, .02, 0], [0, .02, 0]])
    front = skin(points, [[0, 1, 2], [0, 2, 3]])
    rear = skin(points + [.02 * (1 - fraction), 0, -.002], [[0, 2, 1], [0, 3, 2]])
    if allowed:
        assert infer_component_symmetry({'front': [front], 'rear': [rear]}, [0, 0, 1])['physical_driver_orbit_count'] == 1
    else:
        with pytest.raises(DriverSymmetryError) as caught:
            infer_component_symmetry({'front': [front], 'rear': [rear]}, [0, 0, 1])
        assert caught.value.code == 'driver_disconnected_patches'


def test_coax_footprint_preserves_annular_hole():
    from server.tests.test_fem_electrodynamic import _revolved_moving
    disc = _revolved_moving([([(0, 0), (.01, 0)], False)])
    annulus = _revolved_moving([([(.02, 0), (.03, 0)], False)])
    with pytest.raises(DriverSymmetryError) as caught:
        infer_component_symmetry({'mesh': disc + annulus}, [0, 0, 1])
    assert caught.value.code == 'driver_disconnected_patches'


def test_dome_surround_cap_connect_across_separately_indexed_noisy_tags():
    from server.tests.test_fem_electrodynamic import _revolved_moving
    blocks = _revolved_moving([([(0, .002), (.01, 0)], False),
                              ([(.01, 0), (.02, .01)], False),
                              ([(.02, .01), (.03, .01)], False)])
    blocks = [(np.arange(len(t) * 3).reshape(-1, 3), t + i * 1e-13) for i, (_, t) in enumerate(blocks)]
    assert infer_component_symmetry({'mesh': blocks}, [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('same_mesh', [False, True])
def test_full_mirrored_drivers_refused_at_admission_and_compile(monkeypatch, same_mesh):
    from server.solver.coupled_admission import driver_surface_problem
    right = region_input('right')
    right = replace(right, domain=replace(right.domain, points_mm=right.domain.points_mm + [20, 0, 0]))
    if same_mesh:
        _, item = reflect_inputs(None, right, 0)
        items = [item]
    else:
        left = region_input('left')
        xyz = left.domain.points_mm + [20, 0, 0]
        xyz[:, 0] *= -1
        left = replace(left, domain=replace(left.domain, points_mm=xyz,
            surfaces={k: v[:, ::-1] for k, v in left.domain.surfaces.items()},
            tetrahedra=left.domain.tetrahedra[:, [0, 2, 1, 3]]))
        items = [right, left]
    domains = {i.record['region_id']: i.domain for i in items}
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda record, identity: identity)
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda text, identity: domains[identity])
    records = [dict(i.record, mesh_store_path='retained.msh') for i in items]
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    problem = driver_surface_problem({'fem_regions': records}, [channel])
    assert problem[0] == 'driver_disconnected_patches'
    assert 'own drive channel' in problem[1] and 'paint the rim as moving' in problem[1]
    with pytest.raises(DriverSymmetryError) as caught:
        build_coupled_request(None, items, layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], transducers={'fem': driver()})
    assert caught.value.code == problem[0]
    # A reduced representative still creates its physical driver orbit.
    reduced = build_coupled_request(None, [right], layout=compiled().layout, frame=FRAME,
                                   frequencies_hz=[100.], symmetry='yz', transducers={'fem': driver()})
    assert reduced.wire['compiled_system']['components'][0]['parameters']['physical_driver_orbit_count'] == 2



def test_projected_footprint_union_counts_folds_once_and_skips_degenerate_faces():
    from server.solver.component_symmetry import _footprint, _footprints_overlap, _coverage
    square = np.array([[[0., 0.], [1., 0.], [1., 1.]], [[0., 0.], [1., 1.], [0., 1.]]])
    collapsed = np.array([[[0., 0.], [.5, .5], [1., 1.]]])
    for offset in (0., 1e6):
        folded = _footprint(np.concatenate([square, collapsed, square[:, ::-1]]) + offset)
        assert folded[1] == pytest.approx(1.)
        assert len(folded[0]) == 4
        assert _coverage(folded[0], np.array([offset, offset]), 1 / 256, (256, 256)).sum() / 256**2 == pytest.approx(1.)
        assert _footprints_overlap(folded, _footprint(square + [.5, 0] + offset))
        assert not _footprints_overlap(folded, _footprint(square + [.51, 0] + offset))


def revolved_patch(radius, z=0., *, reverse=False, cone=False, rings=10, sectors=64):
    """Connected disc/cone with no degenerate pole triangles."""
    radii = np.linspace(0, radius, rings + 1)
    theta = np.arange(sectors) * 2 * np.pi / sectors
    points = np.zeros((1 + rings * sectors, 3))
    points[1:, 0] = (radii[1:, None] * np.cos(theta)).ravel()
    points[1:, 1] = (radii[1:, None] * np.sin(theta)).ravel()
    points[:, 2] = z + (.3 * np.linalg.norm(points[:, :2], axis=1) if cone else 0)
    j = np.arange(sectors)
    faces = [np.column_stack([np.zeros(sectors, int), 1 + j, 1 + (j + 1) % sectors])]
    for ring in range(1, rings):
        a, b = 1 + (ring - 1) * sectors + j, 1 + (ring - 1) * sectors + (j + 1) % sectors
        c, d = a + sectors, b + sectors
        faces.extend([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    faces = np.concatenate(faces)
    if reverse:
        faces = faces[:, ::-1]
    return faces, points[faces]


@pytest.mark.parametrize('two_meshes', [False, True])
@pytest.mark.parametrize('case', ['coax', 'dome-over-cone', 'isobaric', 'opposed-isobaric',
                                'clamshell', 'opposed-flat', 'force-cancelling'])
def test_overlapping_independent_drivers_are_refused(case, two_meshes):
    front = revolved_patch(.1, cone=True)
    axis = [0, 0, 1]
    if case in ('coax', 'dome-over-cone'):
        rear = revolved_patch(.015 if case == 'coax' else .03, .04, cone=case == 'dome-over-cone')
    elif case in ('isobaric', 'opposed-isobaric'):
        rear = revolved_patch(.1, -.08, cone=True, reverse=case == 'opposed-isobaric')
    elif case == 'clamshell':
        # 200 mm cones opening toward each other, about 40 mm apart along
        # the motion axis (area-weighted centres), with a 20 mm rim gap.
        rear = revolved_patch(.1, cone=True)
        rear = rear[0], rear[1] * [1, 1, -1] + [0, 0, .08]
        front = front[0][:, ::-1], front[1][:, ::-1]
    elif case == 'opposed-flat':
        front = revolved_patch(.1)
        rear = revolved_patch(.1, .03, reverse=True)
    else:
        front = revolved_patch(.1)
        rear = revolved_patch(.1, reverse=True)
        front = front[0], front[1][..., [2, 0, 1]] + [.2, 0, 0]
        rear = rear[0], rear[1][..., [2, 0, 1]] - [.2, 0, 0]
        axis = [1, 0, 0]
    meshes = {'exterior': [front], 'fem:rear': [rear]} if two_meshes else {'mesh': [front, rear]}
    with pytest.raises(DriverSymmetryError) as caught:
        infer_component_symmetry(meshes, axis, channel_id=case)
    assert caught.value.code == 'driver_disconnected_patches'


@pytest.mark.parametrize('layout', ['two-chambers', 'compression-dome', 'fem-exterior', 'dipole-rigid-rim'])
def test_close_opposed_diaphragm_faces_remain_one_driver(layout):
    front = revolved_patch(.025, cone=layout == 'two-chambers')
    if layout == 'compression-dome':
        triangles = front[1].copy()
        triangles[:, :, 2] = .02 * np.sqrt(np.maximum(0., 1 - (np.linalg.norm(triangles[:, :, :2], axis=2) / .025)**2))
        front = front[0], triangles
    rear = front[0][:, ::-1], front[1][:, ::-1] - [0, 0, .0005]
    meshes = ({'front-chamber': [front], 'rear-chamber': [rear]} if layout in ('two-chambers', 'compression-dome')
              else {'exterior': [front], 'fem:rear': [rear]} if layout == 'fem-exterior'
              else {'exterior': [front, rear]})
    assert infer_component_symmetry(meshes, [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('two_meshes', [False, True])
def test_200mm_cone_skins_one_mm_apart_remain_one_driver(two_meshes):
    front = revolved_patch(.1, cone=True)
    rear = front[0][:, ::-1], front[1][:, ::-1] - [0, 0, .001]
    meshes = {'front': [front], 'rear': [rear]} if two_meshes else {'mesh': [front, rear]}
    assert infer_component_symmetry(meshes, [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('radius', [.025, .1])
@pytest.mark.parametrize('extra_gap,allowed', [(0., True), (1e-6, False)])
def test_diaphragm_skin_axial_bound(radius, extra_gap, allowed):
    front = revolved_patch(radius)
    triangles = front[1]
    area = np.abs(np.cross(triangles[:, 1] - triangles[:, 0],
                           triangles[:, 2] - triangles[:, 0])[:, 2]).sum() / 2
    gap = max(.005, .05 * 2 * np.sqrt(area / np.pi)) + extra_gap
    rear = front[0][:, ::-1], front[1][:, ::-1] - [0, 0, gap]
    if allowed:
        assert infer_component_symmetry({'front': [front], 'rear': [rear]}, [0, 0, 1])['physical_driver_orbit_count'] == 1
    else:
        with pytest.raises(DriverSymmetryError) as caught:
            infer_component_symmetry({'front': [front], 'rear': [rear]}, [0, 0, 1])
        assert caught.value.code == 'driver_disconnected_patches'


def profile_skin(profile, *, reverse=False, sectors=48):
    """Revolve the reviewer probe's radial/axial profile, welding the pole."""
    profile = np.asarray(profile, float)
    theta = np.arange(sectors) * 2 * np.pi / sectors
    points, indices, faces = [], {}, []
    def vertex(ring, sector):
        sector = 0 if profile[ring, 0] == 0 else sector % sectors
        key = ring, sector
        if key not in indices:
            radius, height = profile[ring]
            indices[key] = len(points)
            points.append((radius * np.cos(theta[sector]), radius * np.sin(theta[sector]), height))
        return indices[key]
    for ring in range(len(profile) - 1):
        for sector in range(sectors):
            a, b = vertex(ring, sector), vertex(ring, sector + 1)
            c, d = vertex(ring + 1, sector), vertex(ring + 1, sector + 1)
            faces.extend([(a, d, c)] if profile[ring, 0] == 0 else [(a, b, d), (a, d, c)])
    points, faces = np.asarray(points), np.asarray(faces)
    triangles = points[faces]
    if np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2].sum() < 0:
        faces = faces[:, ::-1]
    return skin(points, faces[:, ::-1] if reverse else faces)


def normal_offset_profile(profile, thickness):
    tangent = np.gradient(profile, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    normal[normal[:, 1] < 0] *= -1
    rear = profile - thickness * normal
    rear[:, 0] = np.maximum(rear[:, 0], 0)
    if profile[0, 0] == 0:
        rear[0, 0] = 0
    return rear


@pytest.mark.parametrize('radius,depth,inner,surround', [(.065, .045, .015, .015), (.05, .03, .012, .012)])
@pytest.mark.parametrize('motion_axis', [2, 0])
def test_front_cone_surround_and_rear_cone_share_one_diaphragm(radius, depth, inner, surround, motion_axis):
    radii = np.linspace(inner, radius, 30)
    cone = np.column_stack([radii, depth * (radii - inner) / (radius - inner)])
    rim = np.linspace(radius, radius + surround, 10)
    roll = np.column_stack([rim, depth + surround / 2 * np.sin(np.pi * (rim - radius) / surround)])
    front = profile_skin(np.vstack([cone, roll[1:]]))
    rear = profile_skin(normal_offset_profile(cone, .001), reverse=True)
    if motion_axis == 0:
        front = front[0], front[1][..., [2, 0, 1]]
        rear = rear[0], rear[1][..., [2, 0, 1]]
    axis = np.eye(3)[motion_axis]
    assert infer_component_symmetry({'exterior': [front], 'fem:rear': [rear]}, axis)['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('depth', [.06, .09])
@pytest.mark.parametrize('equal_extent', [False, True])
def test_deep_200mm_cone_one_mm_skins_remain_one_driver(depth, equal_extent):
    radii = np.linspace(.02, .1, 30)
    cone = np.column_stack([radii, depth * (radii - .02) / .08])
    rear = normal_offset_profile(cone, .001)
    if equal_extent:
        rear = cone - [0, .001 * np.sqrt(1 + (depth / .08)**2)]
    assert infer_component_symmetry({'front': [profile_skin(cone)],
                                     'rear': [profile_skin(rear, reverse=True)]}, [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('diameter,height', [(.025, .008), (.025, .0125), (.044, .012), (.075, .02)])
@pytest.mark.parametrize('surround', [False, True])
def test_dome_skins_across_fem_regions_with_optional_front_surround(diameter, height, surround):
    radii = np.linspace(0, diameter / 2, 16)
    sphere_radius = ((diameter / 2)**2 + height**2) / (2 * height)
    dome = np.column_stack([radii, np.sqrt(sphere_radius**2 - radii**2) - (sphere_radius - height)])
    front = dome
    if surround:
        rim = np.linspace(diameter / 2, diameter / 2 + .004, 6)
        roll = np.column_stack([rim, -.001 * np.sin(np.pi * (rim - diameter / 2) / .004)])
        front = np.vstack([dome, roll[1:]])
    assert infer_component_symmetry({'fem:front': [profile_skin(front)],
                                     'fem:rear': [profile_skin(normal_offset_profile(dome, .0001), reverse=True)]},
                                    [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('depth', [.015, .02, .03, .04])
@pytest.mark.parametrize('rim_gap', [.001, .002])
@pytest.mark.parametrize('two_meshes', [False, True])
def test_clamshell_200mm_cones_with_close_rims_are_separate_drivers(depth, rim_gap, two_meshes):
    radii = np.linspace(0, .1, 30)
    cone = np.column_stack([radii, depth * radii / .1])
    opposed = cone.copy()
    opposed[:, 1] = 2 * depth + rim_gap - cone[:, 1]
    front, rear = profile_skin(cone), profile_skin(opposed, reverse=True)
    meshes = {'front': [front], 'rear': [rear]} if two_meshes else {'fem:trap': [front, rear]}
    with pytest.raises(DriverSymmetryError) as caught:
        infer_component_symmetry(meshes, [0, 0, 1])
    assert caught.value.code == 'driver_disconnected_patches'


def test_very_shallow_face_to_face_cones_within_skin_gap_merge():
    radii = np.linspace(0, .1, 30)
    cone = np.column_stack([radii, .005 * radii / .1])
    opposed = cone.copy()
    opposed[:, 1] = .011 - cone[:, 1]
    assert infer_component_symmetry({'fem:trap': [profile_skin(cone), profile_skin(opposed, reverse=True)]},
                                    [0, 0, 1])['physical_driver_orbit_count'] == 1


def test_geometry_verdict_cache_uses_content_and_grouping_not_artifact_path(monkeypatch):
    from server.solver import component_symmetry as module
    patch = revolved_patch(.023456)
    module._verdicts.clear()
    infer_component_symmetry({'old-artifact.msh': [patch]}, [0, 0, 1], channel_id='cache-test')
    original = module._infer_component_symmetry
    calls = []
    def inspect(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_infer_component_symmetry', inspect)
    infer_component_symmetry({'new-artifact.msh': [(patch[0] + 10, patch[1].copy())]}, [0, 0, 1], channel_id='cache-test')
    assert not calls
    infer_component_symmetry({'new-artifact.msh': [(patch[0], patch[1] + [0, 0, .001])]}, [0, 0, 1], channel_id='cache-test')
    assert len(calls) == 1
    infer_component_symmetry({'new-artifact.msh': [patch]}, [0, 0, -1], channel_id='cache-test')
    assert len(calls) == 2
    infer_component_symmetry({'new-artifact.msh': [patch]}, [0, 0, 1], channel_id='another-channel')
    assert len(calls) == 3
    split = [(patch[0][:30], patch[1][:30]), (patch[0][30:], patch[1][30:])]
    infer_component_symmetry({'new-artifact.msh': split}, [0, 0, 1], channel_id='cache-test')
    assert len(calls) == 4


def test_20k_triangle_faces_have_bounded_vectorised_overlap_work(monkeypatch):
    import time
    from server.solver import component_symmetry as module
    front = revolved_patch(.1, cone=True, rings=40, sectors=256)
    rear = front[0][:, ::-1], front[1][:, ::-1] - [0, 0, .003]
    module._verdicts.clear()
    checks = []
    original = module.check_geometry
    def check():
        checks.append(True)
        original()
    monkeypatch.setattr(module, 'check_geometry', check)
    start = time.perf_counter()
    infer_component_symmetry({'exterior': [front], 'rear': [rear]}, [0, 0, 1])
    elapsed = time.perf_counter() - start
    assert len(front[0]) == 20224
    assert len(checks) < 1000  # Bounded raster chunks, not triangle pairs.
    assert elapsed < 2.0  # Generous CI bound; local target is below 0.2 s.


def test_compile_reuses_admission_geometry_verdict(monkeypatch):
    from server.solver import component_symmetry as module
    from server.solver.coupled_admission import driver_surface_problem
    item = region_input()
    record = {'fem_regions': [dict(item.record, mesh_store_path='first.msh')]}
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: item.domain)
    module._verdicts.clear()
    assert driver_surface_problem(record, [channel]) is None
    monkeypatch.setattr(module, '_infer_component_symmetry', lambda *a, **kw: pytest.fail('compile repeated geometry verdict'))
    build_coupled_request(None, [item], layout=compiled().layout, frame=FRAME,
                          frequencies_hz=[100.], transducers={'fem': driver()})


def test_compile_reuses_admission_geometry_verdict_under_x_symmetry(monkeypatch):
    from server.solver import component_symmetry as module
    from server.solver.coupled_admission import driver_surface_problem
    item = region_input()
    points = item.domain.points_mm.copy()
    # Retained meshes carry signed zeros and sub-tolerance plane offsets.
    # Compilation's image transform and snapping normalise both; admission
    # must hash the same geometry.
    points[[0, 1, 2], 2] = -0.
    points[2, 0] = 3e-9
    item = replace(item, domain=replace(item.domain, points_mm=points))
    record = {'fem_regions': [dict(item.record, mesh_store_path='first.msh')],
              'symmetry': {'domain_planes': ['x0']}}
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *a: 'verified')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *a: item.domain)
    module._verdicts.clear()
    assert driver_surface_problem(record, [channel]) is None
    assert len(module._verdicts) == 1
    monkeypatch.setattr(module, '_infer_component_symmetry', lambda *a, **kw: pytest.fail('compile repeated geometry verdict'))
    request = build_coupled_request(None, [item], layout=compiled().layout, frame=FRAME, symmetry='yz',
                                    frequencies_hz=[100.], transducers={'fem': driver()})
    assert request.wire['compiled_system']['components'][0]['parameters']['surface_completion_factor'] == 2


def test_cancelled_raster_work_stops_before_completion_and_is_not_cached():
    from server.solver import component_symmetry as module
    front = revolved_patch(.1, cone=True, rings=40, sectors=256)
    rear = front[0][:, ::-1], front[1][:, ::-1] - [0, 0, .003]
    module._verdicts.clear()
    checks = 0
    def stop():
        nonlocal checks
        checks += 1
        # Topology, then only the first few bounded raster chunks.
        if checks == 12:
            raise module.GeometryCheckCancelled()
    with module.geometry_cancellation(stop), pytest.raises(module.GeometryCheckCancelled):
        module.infer_component_symmetry({'exterior': [front], 'rear': [rear]}, [0, 0, 1])
    assert checks == 12
    assert not module._verdicts


def test_fem_normal_preparation_observes_admission_cancel_token():
    from server.solver.component_symmetry import geometry_cancellation, GeometryCheckCancelled
    from server.solver.coupled_system import _fem_driver_triangles
    def stop():
        raise GeometryCheckCancelled()
    with geometry_cancellation(stop), pytest.raises(GeometryCheckCancelled):
        _fem_driver_triangles(region_input().domain)


def _strip(aspect, *, angle=0., shift=0., z=0., reverse=False, length=.1):
    width = length / aspect
    corners = np.array([[0, shift], [length, shift], [length, shift + width], [0, shift + width]], float)
    c, s = np.cos(angle), np.sin(angle)
    xy = corners @ np.array([[c, s], [-s, c]])
    points = np.column_stack([xy, np.full(4, z)])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    return skin(points, faces[:, ::-1] if reverse else faces)


@pytest.mark.parametrize('angle', [0., np.radians(30)], ids=['aligned', 'diagonal'])
@pytest.mark.parametrize('aspect', [500, 1000])
def test_thin_strip_front_and_rear_faces_merge_with_true_area(aspect, angle):
    from server.solver import component_symmetry as module
    module._verdicts.clear()
    front = _strip(aspect, angle=angle)
    rear = _strip(aspect, angle=angle, z=-.001, reverse=True)
    footprint = module._footprint(front[1][:, :, :2])
    assert footprint.area == pytest.approx(.1 * .1 / aspect, rel=.02)
    assert infer_component_symmetry({'front': [front], 'rear': [rear]}, [0, 0, 1])['physical_driver_orbit_count'] == 1


@pytest.mark.parametrize('angle', [0., np.radians(30)], ids=['aligned', 'diagonal'])
@pytest.mark.parametrize('aspect', [500, 1000])
def test_thin_strip_beside_another_strip_does_not_merge(aspect, angle):
    from server.solver import component_symmetry as module
    module._verdicts.clear()
    width = .1 / aspect
    front = _strip(aspect, angle=angle)
    neighbour = _strip(aspect, angle=angle, shift=.6 * width, z=-.001, reverse=True)
    with pytest.raises(DriverSymmetryError) as caught:
        infer_component_symmetry({'front': [front], 'rear': [neighbour]}, [0, 0, 1])
    assert caught.value.code == 'driver_disconnected_patches'
