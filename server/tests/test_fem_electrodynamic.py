"""F10a contract, mixed-basis mapping and load recovery without native solves."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import time
import warnings

import numpy as np
import pytest

from server.jobs.models import DriveChannel, ExteriorTransducerSpec
from server.solver.beat_adapter.coupled_request import build_coupled_request, build_imported_coupled_request
from server.solver.beat_adapter.coupled_transducers import mechanical_impedance, recover_load
from server.solver.beat_adapter.results import acceleration_scale, coupled_envelope, map_coupled_sweep, ResultContractError
from server.solver.context import SolverContext
from server.solver.coupled_system import ExteriorSurface
from server.solver import official_beat
from server.tests.test_fem_engine_route import compiled, events, region_input, FRAME
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.test_official_beat_bridge import runtime as runtime_fixture

runtime = runtime_fixture


def driver(**options):
    return ExteriorTransducerSpec(version=2, re_ohm=3.3, le_h=0.00029, bl_n_per_a=7.9,
                                  mmd_kg=0.0397, cms_m_per_n=0.00086, rms_n_s_per_m=1.25,
                                  motion_axis=(0., 0., 1.), **options)


def _shared_regions(*, two_regions, split=True, rear_scale=1.):
    from server.mesh.fem_regions import FemRegionDomain, physical_name
    from server.solver.coupled_system import FemRegionInput

    front = region_input('front')
    if split:
        points = np.vstack([front.domain.points_mm, [5., 0., 0.]])
        surfaces = {
            physical_name('front', 'cone', 'moving'): np.array([[0, 2, 4]]),
            physical_name('front', 'surround', 'moving'): np.array([[4, 2, 1]]),
            physical_name('front', 'walls', 'rigid'): np.array([[0, 4, 3], [4, 1, 3], [1, 2, 3], [2, 0, 3]]),
        }
        front.record['surface_groups'].append({'group_id': 'surround', 'kind': 'moving', 'drive_channel_id': 'fem'})
        front = FemRegionInput(front.record, FemRegionDomain('front', points,
                               np.array([[0, 4, 2, 3], [4, 1, 2, 3]]), surfaces))
    if not two_regions:
        return [front]
    rear = region_input('rear')
    points = rear.domain.points_mm * [rear_scale, 1., -1.]
    return [front, FemRegionInput(rear.record, FemRegionDomain('rear', points,
                                 rear.domain.tetrahedra, rear.domain.surfaces))]


@pytest.mark.parametrize('two_regions', [False, True])
@pytest.mark.parametrize('split', [False, True])
@pytest.mark.parametrize('electrodynamic', [False, True])
def test_shared_diaphragm_admission_and_compilation_matrix(monkeypatch, two_regions, split, electrodynamic):
    from beat_engine.beat_contract import validate_compiled_system
    from server.solver.coupled_admission import fem_request_problem
    from server.solver.coupled_system import build_coupled_system

    monkeypatch.delenv('WG2_FEM_REGIONS', raising=False)
    regions = _shared_regions(two_regions=two_regions, split=split)
    channels = [DriveChannel(id='fem', source_ids=['painted-front'], motion='axial' if electrodynamic else 'normal',
                             exterior_transducer=driver() if electrodynamic else None)]
    record = {'fem_regions': [r.record for r in regions]}
    specs = {'fem': driver()} if electrodynamic else {}
    problem = fem_request_problem(record, channels)
    if two_regions and not electrodynamic:
        assert problem[0] == 'fem_multiple_diaphragms_unsupported'
        assert 'front/cone' in problem[1] and 'rear/cone' in problem[1]
        assert 'give this channel an electrodynamic driver to share a diaphragm between regions' in problem[1]
        with pytest.raises(ValueError, match='give this channel an electrodynamic driver'):
            build_coupled_system(None, regions, transducers=specs)
        return
    assert problem is None
    system = build_coupled_system(None, regions, transducers=specs)
    validate_compiled_system(system)
    component, = system['components']
    moving = {b['id'] for b in system['boundaries'] if b['kind'] == 'moving'}
    assert set(component['boundary_ids']) == moving
    assert len(moving) == 1 + split + two_regions
    port, = system['excitation_ports']
    assert port['kind'] == ('voltage' if electrodynamic else 'normal_velocity')
    assert component['kind'] == ('electrodynamic_transducer' if electrodynamic else 'ideal_velocity_source')
    if electrodynamic:
        assert system['metadata']['projected_areas_m2']['fem'] == pytest.approx(.00005)
    # Reverting the driver spec restores the multi-region ideal refusal.
    channels[0].exterior_transducer = None
    channels[0].motion = 'normal'
    reverted = fem_request_problem(record, channels)
    assert bool(reverted) is two_regions


@pytest.mark.parametrize('rear_scale', [1., .6, .1])
def test_two_fem_sides_use_declared_sd_or_unambiguous_geometry_and_refuse_chamber(monkeypatch, rear_scale):
    from server.solver.coupled_admission import fem_request_problem
    from server.solver.coupled_system import build_coupled_system

    monkeypatch.delenv('WG2_FEM_REGIONS', raising=False)
    regions = _shared_regions(two_regions=True, rear_scale=rear_scale)
    if rear_scale != 1.:
        with pytest.warns(UserWarning, match="Channel 'fem'.*geometry is ambiguous"):
            system = build_coupled_system(None, regions, transducers={'fem': driver()})
    else:
        system = build_coupled_system(None, regions, transducers={'fem': driver()})
    metadata = system['metadata']['driver_sd']['fem']
    assert metadata['geometric_sd_m2'] == pytest.approx(.00005 * (1 + rear_scale) / 2)
    assert metadata['geometry_ambiguous'] is (rear_scale != 1.)
    assert metadata['sd_source'] == ('geometry' if rear_scale == 1. else None)
    assert system['metadata']['projected_areas_m2']['fem'] == (pytest.approx(.00005) if rear_scale == 1. else None)
    # A declared area stays authoritative even when the two mesh nets disagree.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        declared = build_coupled_system(None, regions, transducers={'fem': driver(sd_m2=.00007)})
    assert declared['metadata']['driver_sd']['fem']['sd_source'] == 'declared'
    assert declared['metadata']['projected_areas_m2']['fem'] == .00007
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=spec)
    record = {'fem_regions': [r.record for r in regions]}
    problem = fem_request_problem(record, [channel])
    assert problem[0] == 'driver_sd_required'
    with pytest.raises(ValueError) as raised:
        build_coupled_system(None, regions, transducers={'fem': spec})
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'
    spec = driver(sd_m2=.00007, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel.exterior_transducer = spec
    # Feed admission the same oriented triangles that compilation will use.
    from server.solver.coupled_system import _fem_driver_triangles
    moving_meshes = {'fem': {r.record['region_id']: [
        (faces, np.asarray([_fem_driver_triangles(r.domain)[tuple(sorted(face))] for face in faces]))
        for name, faces in r.domain.surfaces.items() if 'kind=moving' in name
    ] for r in regions}}
    problem = fem_request_problem(record, [channel], moving_meshes=moving_meshes)
    assert problem[0] == 'lumped_chamber_double_rear_load'
    with pytest.raises(ValueError) as raised:
        build_coupled_system(None, regions, transducers={'fem': spec})
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


@pytest.mark.parametrize('coupled', [True, False])
def test_voltage_request_passes_engine_contract_and_owns_all_boundaries(coupled):
    from beat_engine.beat_contract import validate_solve_request
    region = region_input(interface=coupled)
    exterior = None
    if coupled:
        exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
            name: {'kind': 'interface' if 'interface' in name else 'moving' if 'moving' in name else 'rigid',
                   'drive_channel_id': 'fem'} for name in region.domain.surfaces})
    # The exterior skin is the diaphragm's second side; a lumped chamber would load the rear twice.
    spec = driver(sd_m2=.00005, semi_inductance={'enabled': True, 're_prime_ohm': 3., 'leb_h': .001,
                                  'le_h': .002, 'ke_semi_h': .003, 'rss_ohm': 4.},
                  lumped_sealed_rear_chamber={'enabled': not coupled, 'volume_m3': .002})
    request = build_coupled_request(exterior, [region], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], transducers={'fem': spec})
    validate_solve_request(request.wire)
    component, = request.wire['compiled_system']['components']
    assert component['kind'] == 'electrodynamic_transducer'
    assert len(component['boundary_ids']) == (2 if coupled else 1)
    params = component['parameters']
    assert params['motion_profile'] == 'rigid_translation'
    assert params['semi_inductance'] == spec.semi_inductance.model_dump()
    assert request.wire['compiled_system']['metadata']['projected_areas_m2']['fem'] == pytest.approx(0.00005)
    if not coupled:
        assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(0.00005)
    assert request.wire['solver_options']['transducer_reference_voltage_v'] == 2.83
    assert request.wire['compiled_system']['excitation_ports'][0]['kind'] == 'voltage'
    assert request.drive_normalization == {'fem': 'voltage_rms'}
    sweep = map_coupled_sweep(events(request), request)
    np.testing.assert_allclose(sweep.nodal_pressure[0, 0], 2 + 3j)
    assert np.isfinite(sweep.generalized_load).all()


def test_two_sided_diaphragm_refuses_lumped_rear_chamber():
    from server.solver.beat_adapter.preflight import UnsupportedPhysics
    region = region_input(interface=True)
    exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
        name: {'kind': 'interface' if 'interface' in name else 'moving' if 'moving' in name else 'rigid',
               'drive_channel_id': 'fem'} for name in region.domain.surfaces})
    spec = driver(sd_m2=.00005, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    with pytest.raises(UnsupportedPhysics, match="lumped_chamber_double_rear_load.*rear already radiates"):
        build_coupled_request(exterior, [region], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], transducers={'fem': spec})


def test_mixed_bases_scale_fields_and_pressure_independently():
    request = compiled(transducers={'fem': driver()})
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    sweep = map_coupled_sweep(events(request), request)
    np.testing.assert_allclose(sweep.nodal_pressure[0, 0], (2 + 3j) * acceleration_scale(1000.123456))
    np.testing.assert_allclose(sweep.nodal_pressure[0, 1], 2 * (2 + 3j))
    np.testing.assert_allclose(sweep.channels['fem'].pressure_complex[0], 2 * (2 + 3j))
    assert request.drive_normalization == {'exterior': 'unit_acceleration', 'fem': 'voltage_rms'}


def test_load_recovery_uses_all_cross_coupled_bases_and_gaps_singular_motion():
    request = build_coupled_request(None, [region_input('one', 'one'), region_input('two', 'two')],
                                    layout=compiled().layout, frame=FRAME, frequencies_hz=[100.],
                                    transducers={'one': driver(), 'two': driver()})
    system = request.wire['compiled_system']
    velocity = np.array([[1 + 2j, .3j], [.2, 2 - 1j]])
    load = np.array([[4 + 8j, 2 - 1j], [2 - 1j, 6 + 3j]])
    zm = mechanical_impedance(system['components'][0]['parameters'], 100., density=1.21, sound_speed=343.)
    current = (load @ velocity.T + zm * velocity.T).T / 7.9
    recovered = recover_load(system['components'], system['excitation_ports'], velocity, current,
                             100., density=1.21, sound_speed=343.)
    np.testing.assert_allclose(recovered, load, atol=1e-12)
    # Dividing only the self basis would incorrectly include the cross load.
    assert abs((7.9 * current[0, 0] - zm * velocity[0, 0]) / velocity[0, 0] - load[0, 0]) > .1
    for bad in (np.ones((2, 2)), np.diag([1., 1e-12])):
        assert np.isnan(recover_load(system['components'], system['excitation_ports'], bad, current,
                                    100., density=1.21, sound_speed=343.)).all()


def test_voltage_envelope_electrical_excursion_acoustic_and_spl():
    from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
    from server.tests.test_imported_jobs import _request
    request = compiled(transducers={'fem': driver()})
    job_request = _request('wgi_' + '0' * 26)
    geometry = job_request.geometry
    geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())]
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    context.polar_config.update(distance=1., observation_origin='mouth')
    sweep = map_coupled_sweep(events(request), request)
    response = coupled_envelope(sweep, request, context=context, geometry=geometry, record={},
                                config=official_beat.response_config(request, context), started=time.time())
    from server.jobs.runtime import JobRuntime
    RESULT_ENVELOPE_ADAPTER.validate_python(JobRuntime._with_request_metadata(response, job_request))
    channel = response['channels']['fem']
    zin = 2.83 / (2 * (2 + 3j))
    assert channel['impedance']['real'][0] == pytest.approx(zin.real)
    assert channel['impedance']['imaginary'][0] == pytest.approx(-zin.imag)
    assert channel['spl_on_axis']['spl'][0] == pytest.approx(20 * np.log10(abs(2 * (2 + 3j)) / 20e-6), abs=.01)
    metadata = channel['metadata']
    assert metadata['drive']['voltage_v'] == 2.83
    assert metadata['driver']['cone_excursion_mm']['values'][0] == pytest.approx(
        np.sqrt(2) * 1000 * abs(2 * (2 + 3j)) / (2 * np.pi * 1000.123456))
    assert np.isfinite(channel['acoustic_impedance']['real']).all()
    assert 'impedance' not in response['channels']['exterior']
    assert response['metadata']['pressure_normalization'] == 'mixed'


@pytest.mark.parametrize('mutation', ['ids', 'orbit', 'voltage', 'unit'])
def test_transducer_contract_mutations_are_refused(mutation):
    request = compiled(transducers={'fem': driver()})
    raw = coupled_result(request.wire, request.wire['frequencies_hz'][0])
    quantity = raw['quantities'][-1]
    if mutation == 'ids':
        quantity['metadata']['component_ids'] = ['wrong']
    elif mutation == 'orbit':
        quantity['metadata']['physical_driver_orbit_counts'] = [2]
    elif mutation == 'voltage':
        raw['diagnostics']['transducer_reference_voltage_v'] = 1.
    else:
        quantity['unit'] = 'Pa'
    with pytest.raises(ResultContractError):
        map_coupled_sweep([{'type': 'result', 'result': raw}], request)


def test_interior_imported_driver_needs_no_painted_surface(monkeypatch):
    region = region_input()
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.read_verified_fem_region_mesh', lambda *a: '')
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.read_fem_region_domain', lambda *a: region.domain)
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.imported_anchor_frame', lambda *a: FRAME)
    geometry = SimpleNamespace(drive_channels=[DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())], combine=None)
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    request = build_imported_coupled_request({'fem_regions': [region.record]}, geometry, context, frequencies_hz=[100.])
    assert [r['kind'] for r in request.wire['compiled_system']['regions']] == ['bounded_air']
    assert request.drive_normalization == {'fem': 'voltage_rms'}


def test_managed_voltage_bridge_with_fake_worker(runtime):
    request = compiled(transducers={'fem': driver()})
    def mutate(raw):
        raw.clear()
        raw.update(coupled_result(runtime.requests[-1], request.wire['frequencies_hz'][runtime.result_count % 2]))
    runtime.mutate = mutate
    sweep = official_beat.solve_coupled_compiled(request)
    assert sweep.current.shape == (2, 2, 1)
    assert all(not path.exists() for path in runtime.paths)


def test_mixed_job_contract_and_exterior_only_revert_guard():
    from server.jobs.models import SolveRequest
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    payload = _request('wgi_' + '0' * 26).model_dump(mode='json')
    payload['options']['engine'] = 'beat-cpu'
    payload['geometry']['drive_channels'][1].update(motion='axial', exterior_transducer=driver().model_dump(mode='json'))
    request = SolveRequest.model_validate(payload)
    assert request.geometry.drive_channels[0].exterior_transducer is None
    assert request.geometry.drive_channels[1].exterior_transducer is not None
    assert _imported_request_refusal(request, {})[0] == 'exterior_transducer_mixed_channels'
    payload['geometry']['exterior_only'] = True
    with pytest.raises(ValueError, match='every drive channel'):
        SolveRequest.model_validate(payload)


def test_fem_only_job_contract_and_non_fem_revert_guard():
    from server.jobs.models import SolveRequest
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    payload = _request('wgi_' + '0' * 26).model_dump(mode='json')
    payload['options']['engine'] = 'beat-cpu'
    payload['geometry']['drive_channels'] = [dict(id='fem', source_ids=[], motion='axial',
                                                exterior_transducer=driver().model_dump(mode='json'))]
    payload['geometry']['mesh']['source_size_mm'] = {}
    request = SolveRequest.model_validate(payload)
    assert request.geometry.drive_channels[0].source_ids == []
    assert _imported_request_refusal(request, {})[0] == 'exterior_transducer_missing_source'
    with pytest.raises(ValueError, match='requires an electrodynamic transducer'):
        DriveChannel(id='ideal', source_ids=[])


@pytest.mark.parametrize('voltage', [0., -1., float('nan'), True])
def test_invalid_reference_voltage_is_refused(voltage):
    with pytest.raises(ValueError, match='transducer_reference_voltage_v'):
        compiled(transducers={'fem': driver()}, reference_voltage_rms_v=voltage)


def test_reference_voltage_is_preserved_in_wire_and_field_mapping():
    request = compiled(transducers={'fem': driver()}, reference_voltage_rms_v=5.66)
    assert request.wire['solver_options']['transducer_reference_voltage_v'] == 5.66
    sweep = map_coupled_sweep(events(request), request)
    # Pressure supplied by the worker is already at this RMS voltage.
    np.testing.assert_allclose(sweep.nodal_pressure[0, 1], 2 * (2 + 3j))


def test_interior_termination_stays_supported_with_a_voltage_driver():
    region = region_input()
    name, faces = next((n, f) for n, f in region.domain.surfaces.items() if 'kind=rigid' in n)
    region.record['surface_groups'].append({'group_id': 'walls', 'kind': 'plane-wave-termination'})
    region.domain.surfaces[name.replace('kind=rigid', 'kind=plane-wave-termination')] = faces
    del region.domain.surfaces[name]
    request = build_coupled_request(None, [region], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], transducers={'fem': driver()})
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    assert request.wire['compiled_system']['components'][0]['kind'] == 'electrodynamic_transducer'


@pytest.mark.parametrize('unsupported', ['driver', 'weights', 'axial'])
def test_direct_builder_keeps_unsupported_drive_refusals(unsupported):
    channel = SimpleNamespace(id='one', source_ids=[], motion='normal', driver=None,
                              patch_weights=None, exterior_transducer=None)
    if unsupported == 'driver':
        channel.driver = object()
    elif unsupported == 'weights':
        channel.patch_weights = {'patch': 1.}
    else:
        channel.motion = 'axial'
    with pytest.raises(ValueError, match='fem_drive_unsupported'):
        build_imported_coupled_request({}, SimpleNamespace(drive_channels=[channel]), None, frequencies_hz=[100.])


def test_cancelled_voltage_sweep_before_first_row_retains_typed_empty_arrays():
    request = compiled(transducers={'fem': driver()})
    sweep = map_coupled_sweep([{'type': 'cancelled', 'solved_count': 0}], request)
    assert sweep.cancelled
    assert sweep.current.shape == sweep.velocity.shape == (0, 2, 1)
    assert sweep.generalized_load.shape == (0, 1, 2)
    assert sweep.nodal_pressure.shape == (0, 2, 4)


def test_projected_fem_area_uses_tetrahedral_normals_even_with_reversed_facets():
    region = region_input()
    for name, block in region.domain.surfaces.items():
        region.domain.surfaces[name] = block[:, [0, 2, 1]]
    request = build_coupled_request(None, [region], layout=compiled().layout, frame=FRAME,
                                    frequencies_hz=[100.], transducers={'fem': driver()})
    assert request.wire['compiled_system']['metadata']['projected_areas_m2']['fem'] == pytest.approx(.00005)


def test_ill_conditioned_load_and_zero_current_map_to_contract_gaps():
    from server.solver.result_mapping import map_coupled_transducer_response
    request = compiled(transducers={'fem': driver()})
    sweep = map_coupled_sweep(events(request), request)
    sweep.generalized_load[:] = complex(np.nan, np.nan)
    sweep.current[:] = 0
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    response = {'frequencies': sweep.channels['fem'].frequencies_hz.tolist(), 'metadata': {}}
    map_coupled_transducer_response(response, sweep, request, 'fem', channel)
    assert response['impedance']['real'] == response['impedance']['imaginary'] == [None, None]
    assert response['acoustic_impedance']['real'] == [None, None]
    assert response['metadata']['beat_transducer']['generalized_load']['unavailable_frequency_indices'] == [0, 1]


@pytest.mark.parametrize('split_tags', [False, True])
def test_connected_fold_keeps_sd_and_accepts_lumped_chamber_across_tags(split_tags):
    points = np.array([[0., 0., 0.], [20., 0., 0.], [0., 10., 0.], [0., 2.5, 5.]])
    moving = np.array([[0, 1, 2], [1, 0, 3]])
    groups = {'moving': moving, 'walls': np.array([[0, 2, 3], [1, 3, 2]])}
    if split_tags:
        groups['fold'] = groups['moving'][1:]
        groups['moving'] = groups['moving'][:1]
    exterior = ExteriorSurface(points, groups, {
        name: {'kind': 'rigid' if name == 'walls' else 'moving', 'drive_channel_id': 'front'}
        for name in groups})
    request = build_coupled_request(exterior, [region_input()], layout=compiled().layout, frame=FRAME,
        frequencies_hz=[100.], transducers={'front': driver(sd_m2=7.5e-5,
            lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})})
    system = request.wire['compiled_system']
    assert system['metadata']['projected_areas_m2']['front'] == pytest.approx(7.5e-5)
    component = next(c for c in system['components'] if c['name'] == 'front')
    assert component['parameters']['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(7.5e-5)


def test_numerically_neutral_disconnected_rim_joins_populated_side():
    from server.solver.beat_adapter.coupled_transducers import parameters
    front = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .01, 0.]]])
    # A separate rim group with opposing projections and a tiny negative net.
    rim = np.array([[[1., 0., 0.], [1.02, 0., 0.], [1., .01, .01]],
                    [[1.02, 0., 0.], [1., 0., 0.], [1., .01 + 1e-16, -.01]]])
    spec = driver(sd_m2=1e-4, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    params, area = parameters(spec, {'exterior': [(np.array([[0, 1, 2]]), front),
                                                (np.array([[3, 4, 5], [4, 3, 6]]), rim)]})
    assert area == pytest.approx(1e-4)
    assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(area)


def test_coincident_faces_in_distinct_meshes_are_two_sides_and_average():
    from server.solver.beat_adapter.coupled_transducers import parameters
    front = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .01, 0.]]])
    rear = front[:, [0, 2, 1]]
    meshes = {'exterior': [(np.array([[0, 1, 2]]), front)],
              'fem': [(np.array([[0, 2, 1]]), rear)]}
    _, area = parameters(driver(), meshes)
    assert area == pytest.approx(1e-4)
    with pytest.raises(ValueError, match='lumped_chamber_double_rear_load.*rear already radiates'):
        parameters(driver(sd_m2=.0002, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}), meshes)


def test_projected_area_mismatch_reaches_compiled_and_result_warnings():
    from server.tests.test_imported_jobs import _request
    region = region_input()
    points = region.domain.points_mm.copy()
    points[:, 0] *= 2
    exterior = ExteriorSurface(points, region.domain.surfaces, {
        name: {'kind': 'moving' if 'moving' in name else 'rigid', 'drive_channel_id': 'fem'}
        for name in region.domain.surfaces})
    with pytest.warns(UserWarning, match='more than 10 %'):
        request = build_coupled_request(exterior, [region], layout=compiled().layout, frame=FRAME,
                                        frequencies_hz=[100.], transducers={'fem': driver()})
    assert request.wire['compiled_system']['metadata']['projected_areas_m2']['fem'] is None
    assert len(request.warnings) == 2
    geometry = _request('wgi_' + '0' * 26).geometry
    geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())]
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    response = coupled_envelope(map_coupled_sweep(events(request), request), request, context=context,
        geometry=geometry, record={}, config=official_beat.response_config(request, context), started=time.time())
    assert response['metadata']['warnings'] == list(request.warnings)
    assert response['channels']['fem']['metadata']['warnings'] == list(request.warnings)
    assert 'acoustic_impedance' not in response['channels']['fem']
    assert response['channels']['fem']['metadata']['acoustic_impedance_note'] == 'Sd not determined; enter Sd to get acoustic impedance'


def test_direct_builder_prioritizes_termination_like_admission(monkeypatch):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv('WG2_FEM_REGIONS', '1')
    region = region_input(interface=True).record
    region['surface_groups'].append({'group_id': 'tube', 'kind': 'plane-wave-termination'})
    record = {'fem_regions': [region]}
    request = _request('wgi_' + '0' * 26)
    request.geometry.drive_channels[0].patch_weights = {'patch': 1.}
    problem = _imported_request_refusal(request, record)
    assert problem[0] == 'fem_termination_with_exterior'
    with pytest.raises(ValueError, match=problem[0]):
        build_imported_coupled_request(record, request.geometry, None, frequencies_hz=[100.])


def test_driver_symmetry_refusal_names_actual_preparation_control(monkeypatch):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv('WG2_FEM_REGIONS', '1')
    request = _request('wgi_' + '0' * 26)
    request.geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())]
    problem = _imported_request_refusal(request, {'fem_regions': [region_input().record],
                                               'symmetry': {'domain_planes': ['x0']}})
    assert problem[0] == 'fem_domain_unsupported'
    assert 'until driver symmetry is supported' in problem[1]
    assert 'Force full domain' in problem[1]
    assert 'CAD author cut it' in problem[1]
    with pytest.raises(ValueError, match='Force full domain') as raised:
        build_coupled_request(None, [region_input()], layout=compiled().layout, frame=FRAME,
                             frequencies_hz=[100.], symmetry='yz', transducers={'fem': driver()})
    assert str(raised.value) == problem[1]


def _closed_moving_skin(*, dome=False, rear_fraction=1.):
    """Two 20 mm square sides, a connecting rim, and optional raised cap centres."""
    points = np.array([[x, y, z] for z in (0., .002) for x, y in
                       ((0., 0.), (.02, 0.), (.02, .02), (0., .02))])
    if dome:
        points = np.vstack([points, [.01, .01, -.004], [.01, .01, .006]])
        front = np.array([[i, 8, (i + 1) % 4] for i in range(4)])
        rear = np.array([[i + 4, (i + 1) % 4 + 4, 9] for i in range(4)])
    else:
        front = np.array([[0, 2, 1], [0, 3, 2]])
        rear = np.array([[4, 5, 6], [4, 6, 7]])
    if rear_fraction != 1.:
        # Both tags touch the rim; only 60% of the rear is moving.
        points = np.vstack([points, [.02 * rear_fraction, 0., .002],
                            [.02 * rear_fraction, .02, .002]])
        rear = np.array([[4, 8, 9], [4, 9, 7]])
    rim = np.array([face for i in range(4) for face in
                    ([i, (i + 1) % 4, (i + 1) % 4 + 4], [i, (i + 1) % 4 + 4, i + 4])])
    return {'fem': [(front, points[front]), (rim, points[rim]), (rear, points[rear])]}


@pytest.mark.parametrize('dome,rear_fraction', [(False, 1.), (True, 1.), (False, .2), (False, .6)])
def test_connected_front_rim_rear_skin_requires_declared_sd(dome, rear_fraction):
    from server.solver.beat_adapter.coupled_transducers import parameters
    meshes = _closed_moving_skin(dome=dome, rear_fraction=rear_fraction)
    with pytest.warns(UserWarning, match="Sd not determined; enter Sd to get acoustic impedance"):
        assert parameters(driver(), meshes, channel_id='skin')[1] is None
    with pytest.raises(ValueError, match="driver_sd_required: Enter Sd.*channel 'skin'"):
        parameters(driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}),
                   meshes, channel_id='skin')
    metadata = {}
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        _, sd = parameters(driver(sd_m2=.0004), meshes, channel_id='skin', sd_metadata=metadata)
    assert emitted == []
    assert sd == .0004
    spec = driver(sd_m2=.0004, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    from server.solver.coupled_admission import driver_sd_problem
    channel = DriveChannel(id='skin', source_ids=[], motion='axial', exterior_transducer=spec)
    problem = driver_sd_problem({}, [channel], moving_meshes={'skin': meshes})
    assert problem[0] == 'lumped_chamber_double_rear_load'
    with pytest.raises(ValueError) as raised:
        parameters(spec, meshes, channel_id='skin')
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'
    # Disabling the chamber restores the declared-area response without removing faces.
    disabled = driver(sd_m2=.0004, lumped_sealed_rear_chamber={'enabled': False, 'volume_m3': .002})
    channel.exterior_transducer = disabled
    assert driver_sd_problem({}, [channel], moving_meshes={'skin': meshes}) is None
    assert parameters(disabled, meshes)[1] == .0004
    assert metadata['sd_source'] == 'declared'
    assert metadata['geometry_ambiguous'] is True


def test_separately_tagged_cone_surround_roll_and_cap_keep_fold_subtraction():
    from server.solver.beat_adapter.coupled_transducers import parameters
    # Cone and cap project forward; a separately meshed surround roll is a
    # small reversed fold, not a rear radiating side.
    cone = np.array([[[0., 0., 0.], [.1, 0., 0.], [0., .1, .05]]])
    cap = np.array([[[.03, .03, .06], [.07, .03, .06], [.03, .07, .06]]])
    roll = np.array([[[.1, 0., 0.], [0., 0., 0.], [0., .01, .02]]])
    meshes = {'fem': [(np.array([[0, 1, 2]]), cone),
                      (np.array([[3, 4, 5]]), cap), (np.array([[1, 0, 6]]), roll)]}
    _, area = parameters(driver(), meshes)
    assert area == pytest.approx(.005 + .0008 - .0005)


@pytest.mark.parametrize('mesh_names', [('fem-front', 'fem-rear'), ('fem-front', 'exterior')])
def test_two_sides_keep_separate_mesh_connectivity(mesh_names):
    from server.solver.beat_adapter.coupled_transducers import parameters
    triangles = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .02, 0.]]])
    faces = np.array([[0, 1, 2]])
    meshes = {mesh_names[0]: [(faces, triangles)], mesh_names[1]: [(faces, triangles[:, [0, 2, 1]])]}
    assert parameters(driver(), meshes)[1] == pytest.approx(.0002)
    with pytest.raises(ValueError, match='lumped_chamber_double_rear_load.*rear already radiates'):
        parameters(driver(sd_m2=.0002, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}), meshes)


def test_cancelling_skin_without_separable_sides_names_channel():
    from server.solver.beat_adapter.coupled_transducers import parameters
    triangles = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .01, 0.]],
                          [[.02, 0., 0.], [0., 0., 0.], [0., .01, .01]]])
    with pytest.raises(ValueError, match="driver_sd_required: Enter Sd.*channel 'cancel'"):
        parameters(driver(lumped_sealed_rear_chamber={"enabled": True, "volume_m3": .002}), {'fem': [(np.array([[0, 1, 2], [1, 0, 3]]), triangles)]}, channel_id='cancel')


def test_area_warning_is_channel_specific_in_mixed_results():
    from server.tests.test_imported_jobs import _request
    region = region_input()
    points = region.domain.points_mm.copy()
    points[:, 0] *= 2
    exterior = ExteriorSurface(points, region.domain.surfaces, {
        name: {'kind': 'moving' if 'moving' in name else 'rigid', 'drive_channel_id': 'fem'}
        for name in region.domain.surfaces})
    with pytest.warns(UserWarning, match="Channel 'fem'.*more than 10 %; geometry is ambiguous"):
        request = build_coupled_request(exterior, [region, region_input('other', 'other')],
            layout=compiled().layout, frame=FRAME, frequencies_hz=[100.], transducers={'fem': driver()})
    geometry = _request('wgi_' + '0' * 26).geometry
    geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver()),
                               DriveChannel(id='other', source_ids=['painted-other'])]
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    # A derived result must not inherit another channel's warning either.
    from server.jobs.models import ChannelCombineSpec
    geometry.combine = ChannelCombineSpec(id='combined', members=['fem', 'other'], crossovers_hz=[500.], level_match=False, align=False)
    response = coupled_envelope(map_coupled_sweep(events(request), request), request, context=context,
        geometry=geometry, record={}, config=official_beat.response_config(request, context), started=time.time())
    assert response['channels']['fem']['metadata']['warnings'] == list(request.warnings)
    assert response['channels']['other']['metadata']['warnings'] == []
    assert response['channels']['combined']['metadata']['warnings'] == []
    assert 'sealed rear chamber' not in request.warnings[0]


@pytest.mark.parametrize('baffle,planes,transducer,weights,passive', [
    (True, [], False, True, False),
    (False, ['x0'], True, True, False),
    (True, ['x0'], True, True, False),
    (False, ['z0'], False, True, False),
    (False, [], False, True, True),
    (True, [], False, False, True),
])
def test_admission_and_compilation_share_first_refusal(monkeypatch, baffle, planes, transducer, weights, passive):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv('WG2_FEM_REGIONS', '1')
    request = _request('wgi_' + '0' * 26)
    channel = request.geometry.drive_channels[0]
    if transducer:
        channel.exterior_transducer = driver()
    if weights:
        channel.patch_weights = {'patch': 1.}
    request.geometry.passive_cardioid_rear_volume_l = 10. if passive else None
    record = {'fem_regions': [region_input(channel=channel.id).record],
              'infinite_baffle': baffle, 'symmetry': {'domain_planes': planes}}
    problem = _imported_request_refusal(request, record)
    assert problem is not None
    with pytest.raises(ValueError) as raised:
        build_imported_coupled_request(record, request.geometry, None, frequencies_hz=[100.])
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


def test_disabled_fem_has_same_first_refusal_in_admission_and_compilation(monkeypatch):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv('WG2_FEM_REGIONS', '0')
    request = _request('wgi_' + '0' * 26)
    request.geometry.drive_channels[0].patch_weights = {'patch': 1.}
    record = {'fem_regions': [region_input().record], 'infinite_baffle': True}
    problem = _imported_request_refusal(request, record)
    assert problem[0] == 'fem_required'
    with pytest.raises(ValueError) as raised:
        build_imported_coupled_request(record, request.geometry, None, frequencies_hz=[100.])
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


def test_thick_spherical_cap_skin_has_two_radiating_sides():
    from server.solver.beat_adapter.coupled_transducers import parameters
    sectors, rings = 16, 4
    theta_max = 1.2
    radius = np.sqrt(.0004 / (sectors / 2 * np.sin(2 * np.pi / sectors))) / np.sin(theta_max)
    cap = [[0., 0., radius]]
    for theta in np.linspace(theta_max / rings, theta_max, rings):
        cap.extend([[radius * np.sin(theta) * np.cos(phi), radius * np.sin(theta) * np.sin(phi),
                     radius * np.cos(theta)] for phi in np.arange(sectors) * 2 * np.pi / sectors])
    cap = np.array(cap)
    points = np.vstack([cap, cap + [0., 0., .002]])
    front = [[0, 1 + i, 1 + (i + 1) % sectors] for i in range(sectors)]
    for ring in range(rings - 1):
        start = 1 + ring * sectors
        front.extend([face for i in range(sectors) for face in
                      ([start + i, start + sectors + i, start + sectors + (i + 1) % sectors],
                       [start + i, start + sectors + (i + 1) % sectors, start + (i + 1) % sectors])])
    front = np.array(front)
    rear = front[:, [0, 2, 1]] + len(cap)
    edge = np.arange(1 + (rings - 1) * sectors, len(cap))
    rim = np.array([face for a, b in zip(edge, np.roll(edge, -1)) for face in
                    ([a, b, b + len(cap)], [a, b + len(cap), a + len(cap)])])
    meshes = {'fem': [(front, points[front]), (rear, points[rear]), (rim, points[rim])]}
    with pytest.warns(UserWarning, match='Sd not determined'):
        assert parameters(driver(), meshes, channel_id='dome')[1] is None
    with pytest.raises(ValueError, match="driver_sd_required: Enter Sd.*channel 'dome'"):
        parameters(driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}),
                   meshes, channel_id='dome')


def _revolved_moving(profiles, sectors=48):
    """Weld axisymmetric profiles into tagged moving boundaries in metres."""
    points, identities, boundaries = [], {}, []

    def vertex(point):
        key = tuple(np.round(point, 12))
        if key not in identities:
            identities[key] = len(points)
            points.append(point)
        return identities[key]

    for profile, reverse in profiles:
        rings = [[vertex([r * np.cos(phi), r * np.sin(phi), z])
                  for phi in np.arange(sectors) * 2 * np.pi / sectors] for r, z in profile]
        faces = []
        for a, b in zip(rings[:-1], rings[1:]):
            for i in range(sectors):
                j = (i + 1) % sectors
                for face in ([a[i], b[i], b[j]], [a[i], b[j], a[j]]):
                    if len(set(face)) == 3:
                        faces.append(face[::-1] if reverse else face)
        boundaries.append(np.asarray(faces))
    points = np.asarray(points)
    return [(faces, points[faces]) for faces in boundaries]


@pytest.mark.parametrize('depth,roll_end', [(.06, 0.), (.06, -np.pi / 2), (.4, 0.)])
def test_cone_half_roll_270_roll_and_deep_cap_have_exact_net_sd(depth, roll_end):
    from server.solver.beat_adapter.coupled_transducers import parameters
    cap = [(.02 * np.sin(t), .015 * np.cos(t)) for t in np.linspace(0, np.pi / 2, 10)]
    cone = [(.02 + .08 * s, depth * s) for s in np.linspace(0, 1, 12)]
    roll = [(.11 + .01 * np.cos(t), depth + .01 * np.sin(t))
            for t in np.linspace(np.pi, roll_end, 36)]
    boundaries = _revolved_moving([(cap, False), (cone, False), (roll, False)])
    # Projected polygon area at the outer endpoint, including signed folds.
    radius = roll[-1][0]
    expected = 48 / 2 * np.sin(2 * np.pi / 48) * radius**2
    params, sd = parameters(driver(sd_m2=expected, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}),
                            {'fem': boundaries})
    assert parameters(driver(), {'fem': boundaries})[1] == pytest.approx(expected, rel=1e-12)
    assert sd == pytest.approx(expected, rel=1e-12)
    assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == sd


@pytest.mark.parametrize('depth', [.04, .06, .08])
@pytest.mark.parametrize('inner_rim', [False, True])
def test_one_mm_thick_cone_with_both_faces_never_silently_uses_small_net(depth, inner_rim):
    from server.solver.beat_adapter.coupled_transducers import parameters
    tangent = np.array([.08, depth])
    tangent /= np.linalg.norm(tangent)
    normal = np.array([-tangent[1], tangent[0]])
    a, b = np.array([.02, 0.]), np.array([.1, depth])
    c, d = a - .001 * normal, b - .001 * normal
    profiles = [([tuple(a + (b - a) * s) for s in np.linspace(0, 1, 12)], False),
                ([tuple(c + (d - c) * s) for s in np.linspace(0, 1, 12)], True),
                ([tuple(b), tuple(d)], False)]
    if inner_rim:
        profiles.append(([tuple(c), tuple(a)], False))
    meshes = {'fem': _revolved_moving(profiles)}
    with pytest.warns(UserWarning, match='Sd not determined'):
        assert parameters(driver(), meshes, channel_id='cone')[1] is None
    with pytest.raises(ValueError, match="driver_sd_required: Enter Sd.*channel 'cone'"):
        parameters(driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}),
                   meshes, channel_id='cone')
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert parameters(driver(sd_m2=.03016), meshes, channel_id='cone')[1] == .03016
    assert emitted == []


@pytest.mark.parametrize('declared', [None, .0002])
def test_ambiguous_channel_omits_acoustic_impedance_or_uses_declared_sd(declared):
    from server.solver.result_mapping import map_coupled_transducer_response
    from server.solver.beat_adapter.coupled_transducers import parameters
    spec = driver(**({'sd_m2': declared} if declared is not None else {}))
    request = compiled(transducers={'fem': spec})
    system = request.wire['compiled_system']
    metadata = {}
    notes = []
    with (pytest.warns(UserWarning) if declared is None else nullcontext()):
        _, area = parameters(spec, _closed_moving_skin(), channel_id='fem',
                             sd_metadata=metadata, collected_warnings=notes)
    system['metadata']['projected_areas_m2']['fem'] = area
    system['metadata']['driver_sd']['fem'] = metadata
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': sweep.channels['fem'].frequencies_hz.tolist(), 'metadata': {}}
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=spec)
    map_coupled_transducer_response(response, sweep, request, 'fem', channel)
    assert response['metadata']['sd_m2'] == declared
    if declared is None:
        assert 'acoustic_impedance' not in response
        assert response['metadata']['acoustic_impedance_note'] == 'Sd not determined; enter Sd to get acoustic impedance'
    else:
        medium = request.regions[0].record['medium']
        expected = sweep.generalized_load[:, 0, 1] / (medium['density_kg_per_m3'] * medium['sound_speed_m_per_s'] * declared)
        np.testing.assert_allclose(response['acoustic_impedance']['real'], expected.real)
        assert response['metadata']['sd_source'] == 'declared'


def test_declared_sd_controls_kbox_exactly_and_requires_v2():
    from server.solver.beat_adapter.coupled_transducers import parameters
    spec = driver(sd_m2=.0004, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        params, area = parameters(spec, {'fem': [_closed_moving_skin()['fem'][0]]})
    assert emitted == []
    dry = driver().model_dump()
    difference = mechanical_impedance(params, 100., density=1.21, sound_speed=343.) - mechanical_impedance(
        dry, 100., density=1.21, sound_speed=343.)
    assert difference == pytest.approx(1.21 * 343**2 * area**2 / .002 / (-1j * 2 * np.pi * 100))
    with pytest.raises(ValueError, match='version 2'):
        ExteriorTransducerSpec.model_validate({**spec.model_dump(), 'version': 1})
    for invalid in (0., -1., float('nan'), True):
        with pytest.raises(ValueError):
            driver(sd_m2=invalid)
    v1 = ExteriorTransducerSpec.model_validate({**driver().model_dump(), 'version': 1}).model_dump()
    assert 'sd_m2' not in v1


@pytest.mark.parametrize('minority,ambiguous', [(.049, False), (.05, True), (.2, True), (.6, True)])
def test_opposite_disconnected_patch_threshold(minority, ambiguous):
    from server.solver.beat_adapter.transducers import geometric_driver_area
    front = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    rear = np.array([[[2., 0., 0.], [2., minority, 0.], [3., 0., 0.]]])
    meshes = {'fem': [(np.array([[0, 1, 2], [3, 4, 5]]), np.concatenate([front, rear]))]}
    area, actual, _ = geometric_driver_area(meshes, (0., 0., 1.))
    assert actual == ambiguous
    assert area == pytest.approx(.5 * (1 - minority))


def test_shared_sd_admission_and_compile_return_identical_channel_refusal(monkeypatch):
    from server.solver.coupled_admission import fem_request_problem
    from server.solver.beat_adapter.coupled_transducers import parameters
    from server.solver.beat_adapter.transducers import DriverSdRequired
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='skin', source_ids=[], motion='axial', exterior_transducer=spec)
    region = region_input(channel='skin').record
    meshes = _closed_moving_skin(rear_fraction=.2)
    problem = fem_request_problem({'fem_regions': [region]}, [channel], enabled=True,
                                  moving_meshes={'skin': meshes})
    assert problem == ('driver_sd_required', "Enter Sd in the driver options for channel 'skin'; a lumped rear chamber needs the driver's Sd")
    with pytest.raises(DriverSdRequired) as raised:
        parameters(spec, meshes, channel_id='skin')
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'
    # Domain conflicts retain priority; turning off the chamber permits the solve.
    assert fem_request_problem({'fem_regions': [region], 'infinite_baffle': True}, [channel], enabled=True,
                               moving_meshes={'skin': meshes})[0] == 'fem_domain_unsupported'
    channel.exterior_transducer = driver()
    assert fem_request_problem({'fem_regions': [region]}, [channel], enabled=True,
                               moving_meshes={'skin': meshes}) is None


def test_two_fem_regions_share_one_voltage_coordinate_and_omit_disagreeing_sd(monkeypatch):
    from server.solver.coupled_admission import fem_request_problem
    regions = [region_input('front', 'speaker'), region_input('rear', 'speaker')]
    regions[1].domain.points_mm[:, 0] *= 2
    regions[1].domain.points_mm[:, 2] *= -1
    spec = driver()
    channel = DriveChannel(id='speaker', source_ids=[], motion='axial', exterior_transducer=spec)
    assert fem_request_problem({'fem_regions': [r.record for r in regions]}, [channel], enabled=True) is None
    with pytest.warns(UserWarning, match='more than 10 %'):
        request = build_coupled_request(None, regions, layout=compiled().layout, frame=FRAME,
                                       frequencies_hz=[100.], transducers={'speaker': spec})
    system = request.wire['compiled_system']
    assert len(system['components']) == 1
    assert len(system['components'][0]['boundary_ids']) == 2
    assert system['metadata']['driver_sd']['speaker']['sd_source'] is None
    assert system['metadata']['driver_sd']['speaker']['geometry_ambiguous']
    assert system['metadata']['projected_areas_m2']['speaker'] is None
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)



def test_fem_sd_admission_refuses_before_geometry_and_matches_compile(monkeypatch):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    region = region_input(channel='skin')
    region.record['surface_groups'].append({'group_id': 'walls', 'kind': 'moving', 'drive_channel_id': 'skin'})
    name = next(name for name in region.domain.surfaces if 'kind=rigid' in name)
    region.domain.surfaces[name.replace('kind=rigid', 'kind=moving')] = region.domain.surfaces.pop(name)[:, [0, 2, 1]]
    calls = []
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh',
                        lambda record, identity: calls.append(identity) or 'verified mesh')
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *args: region.domain)
    job = _request('wgi_' + '0' * 26)
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    job.geometry.drive_channels = [DriveChannel(id='skin', source_ids=[], motion='axial', exterior_transducer=spec)]
    problem = _imported_request_refusal(job, {'fem_regions': [region.record]})
    assert problem[0] == 'driver_sd_required'
    assert calls == []
    with pytest.raises(ValueError) as raised:
        build_coupled_request(None, [region], layout=compiled().layout, frame=FRAME,
                              frequencies_hz=[100.], transducers={'skin': spec})
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


@pytest.mark.parametrize('rear_fraction', [.2, .6])
@pytest.mark.parametrize('kind', ['twisted-puck', 'slanted-cone'])
def test_non_neutral_rear_connection_always_requires_chamber_sd(rear_fraction, kind):
    # Regression geometries from the review probes: a zigzag rim or an outer
    # slanted end face connects a partly tagged rear to the moving front.
    from server.solver.beat_adapter.coupled_transducers import parameters
    from server.solver.coupled_admission import fem_request_problem
    if kind == 'twisted-puck':
        meshes = _closed_moving_skin(rear_fraction=rear_fraction)
        boundaries = meshes['fem']
        # Twist the rear-ring coordinates, preserving shared node identities.
        for faces, corners in boundaries:
            angle = np.where(corners[:, :, 2] == 0, np.pi / 48, 0.)
            x, y = corners[:, :, 0].copy(), corners[:, :, 1].copy()
            corners[:, :, 0] = x * np.cos(angle) - y * np.sin(angle)
            corners[:, :, 1] = x * np.sin(angle) + y * np.cos(angle)
    else:
        a, b = np.array([.02, 0.]), np.array([.1, .06])
        c, d = a - [-.0006, .0008], b - [-.0006, .0008]
        meshes = {'fem': _revolved_moving([
            ([tuple(a + (b - a) * x) for x in np.linspace(0, 1, 12)], False),
            ([tuple(b), tuple(d)], False),
            ([tuple(c + (d - c) * x) for x in np.linspace(1 - rear_fraction, 1, 6)], True)])}
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='probe', source_ids=[], motion='axial', exterior_transducer=spec)
    problem = fem_request_problem({'fem_regions': [region_input(channel='probe').record]},
                                  [channel], enabled=True, moving_meshes={'probe': meshes})
    assert problem[0] == 'driver_sd_required'
    with pytest.raises(ValueError) as raised:
        parameters(spec, meshes, channel_id='probe')
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


@pytest.mark.parametrize('second', ['fem-rear', 'exterior'])
def test_declared_chamber_double_load_refused_at_admission_and_compile(second):
    from server.solver.beat_adapter.coupled_transducers import parameters
    from server.solver.coupled_admission import fem_request_problem
    faces = np.array([[0, 1, 2]])
    front = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .01, 0.]]])
    meshes = {'fem-front': [(faces, front)], second: [(faces, front[:, [0, 2, 1]])]}
    spec = driver(sd_m2=.0001, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='speaker', source_ids=[], motion='axial', exterior_transducer=spec)
    problem = fem_request_problem({'fem_regions': [region_input(channel='speaker').record]},
                                  [channel], enabled=True, moving_meshes={'speaker': meshes})
    assert problem[0] == 'lumped_chamber_double_rear_load'
    assert 'rear already radiates' in problem[1]
    with pytest.raises(ValueError) as raised:
        parameters(spec, meshes, channel_id='speaker')
    assert str(raised.value) == f'{problem[0]}: {problem[1]}'


def test_same_side_split_meshes_sum_and_opposite_sides_average():
    from server.solver.beat_adapter.transducers import geometric_driver_area
    faces = np.array([[0, 1, 2]])
    triangle = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    meshes = {'inner': [(faces, triangle)], 'outer': [(faces, triangle)]}
    assert geometric_driver_area(meshes, [0, 0, 1]) == (1., False, [])
    meshes['rear'] = [(faces, triangle[:, [0, 2, 1]] * np.sqrt(2))]
    area, ambiguous, notes = geometric_driver_area(meshes, [0, 0, 1])
    assert area == pytest.approx(1.)
    assert not ambiguous and notes == []
    meshes['rear'] = [(faces, triangle[:, [0, 2, 1]])]
    assert 'more than 10 %' in geometric_driver_area(meshes, [0, 0, 1])[2][0]


@pytest.mark.parametrize('ratio,ambiguous,warning', [(.79, False, True), (.8, False, False), (1.2, False, False),
                                                   (1.4, False, False), (1.6, False, False),
                                                   (1.61, False, True), (2., True, False)])
def test_declared_sd_cross_check_requires_unambiguous_geometry(monkeypatch, ratio, ambiguous, warning):
    from server.solver.beat_adapter import transducers as t
    monkeypatch.setattr(t, 'geometric_driver_area', lambda *args: (ratio, ambiguous, []))
    _, _, notes = t.resolve_driver_sd({'sd_m2': 1.}, {}, [0, 0, 1], 'speaker')
    assert bool(notes) == warning
    if warning:
        assert 'geometric projected area' in notes[0] and 'cm²' in notes[0]
        assert 'includes everything that moves such as the surround' in notes[0]
        assert 'differs from the declared Sd (10000 cm²)' in notes[0]
        assert 'normal' not in notes[0]


def test_coupled_axis_is_normalized_again_before_engine_use():
    from server.solver.beat_adapter.coupled_transducers import parameters
    raw = driver().model_dump()
    raw['motion_axis'] = [0., 0., 30.]
    faces = np.array([[0, 1, 2]])
    triangle = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    params, area = parameters(raw, {'fem': [(faces, triangle)]})
    assert params['motion_axis'] == [0., 0., 1.]
    assert area == .5


def test_admission_without_chamber_never_reads_fem_sd_geometry(monkeypatch):
    from server.solver.coupled_admission import fem_request_problem
    def unexpected(*args, **kwargs):
        pytest.fail('admission read a tetrahedral mesh for Sd without a chamber')
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', unexpected)
    channel = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    assert fem_request_problem({'fem_regions': [region_input().record]}, [channel], enabled=True) is None


def test_coupled_exterior_admission_and_compile_share_oriented_projection(monkeypatch):
    from server.solver.coupled_admission import _channel_moving_meshes, driver_sd_problem
    from server.solver.beat_adapter.transducers import geometric_driver_area
    points = np.array([[0., 0., 0.], [20., 0., 0.], [0., 10., 0.], [0., 2.5, 5.]])
    groups = {'source_id=front': np.array([[0, 1, 2]]),
              'source_id=fold': np.array([[0, 1, 3]]),  # OCC face wound backwards
              'walls': np.array([[0, 2, 3], [1, 3, 2]])}
    record = {'coupled_exterior': {'physical_names': list(groups)},
              'fem_regions': [region_input().record]}
    monkeypatch.setattr('server.mesh.artifact.read_verified_coupled_exterior_mesh', lambda *args: 'verified')
    monkeypatch.setattr('server.mesh.fem_coupling.read_coupled_exterior', lambda *args: (points, groups))
    spec = driver(sd_m2=7.5e-5, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='front', source_ids=['front', 'fold'], motion='axial', exterior_transducer=spec)
    meshes = _channel_moving_meshes(record, channel)
    area, ambiguous, _ = geometric_driver_area(meshes, spec.motion_axis)
    assert area == pytest.approx(7.5e-5) and not ambiguous
    assert driver_sd_problem(record, [channel]) is None
    exterior = ExteriorSurface(points, groups, {
        name: {'kind': 'rigid' if name == 'walls' else 'moving', 'drive_channel_id': 'front'} for name in groups})
    request = build_coupled_request(exterior, [region_input()], layout=compiled().layout, frame=FRAME,
                                   frequencies_hz=[100.], transducers={'front': spec})
    metadata = request.wire['compiled_system']['metadata']['driver_sd']['front']
    assert metadata['geometric_sd_m2'] == pytest.approx(area)
    assert metadata['geometry_ambiguous'] == ambiguous


@pytest.mark.parametrize('exterior_side', [False, True])
def test_declared_chamber_admission_checks_verified_multi_mesh_return(monkeypatch, exterior_side):
    from server.solver.coupled_admission import driver_sd_problem
    regions = [region_input('front', 'speaker'), region_input('rear', 'speaker')]
    regions[1].domain.points_mm[:, 2] *= -1
    domains = {r.record['region_id']: r.domain for r in regions}
    calls = []
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh',
                        lambda record, identity: calls.append(identity) or identity)
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain',
                        lambda text, identity: domains[identity])
    record = {'fem_regions': [r.record for r in regions]}
    if exterior_side:
        record['fem_regions'] = [regions[0].record]
        record['coupled_exterior'] = {'physical_names': ['source_id=front']}
        domain = regions[0].domain
        groups = {('source_id=front' if 'moving' in name else name): faces
                  for name, faces in domain.surfaces.items()}
        monkeypatch.setattr('server.mesh.artifact.read_verified_coupled_exterior_mesh', lambda *args: 'verified')
        monkeypatch.setattr('server.mesh.fem_coupling.read_coupled_exterior',
                            lambda *args: (domain.points_mm, groups))
    spec = driver(sd_m2=.00005, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='speaker', source_ids=['front'] if exterior_side else [],
                           motion='axial', exterior_transducer=spec)
    problem = driver_sd_problem(record, [channel])
    assert problem[0] == 'lumped_chamber_double_rear_load'
    assert calls == (['front'] if exterior_side else ['front', 'rear'])


@pytest.mark.parametrize('split', [False, True])
def test_declared_chamber_same_side_split_is_allowed_at_admission_and_compile(split):
    from server.solver.coupled_admission import fem_request_problem
    from server.solver.beat_adapter.coupled_transducers import parameters
    faces = np.array([[0, 1, 2]])
    triangle = np.array([[[0., 0., 0.], [.02, 0., 0.], [0., .01, 0.]]])
    meshes = {'front': [(faces, triangle)]}
    if split:
        meshes['outer-front'] = [(faces, triangle)]
    spec = driver(sd_m2=.0001 * len(meshes), lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='speaker', source_ids=[], motion='axial', exterior_transducer=spec)
    assert fem_request_problem({'fem_regions': [region_input(channel='speaker').record]}, [channel],
                               enabled=True, moving_meshes={'speaker': meshes}) is None
    params, area = parameters(spec, meshes)
    assert area == pytest.approx(.0001 * len(meshes))
    assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == area


def test_front_split_and_small_cap_rear_do_not_publish_averaged_sd():
    from server.solver.beat_adapter.transducers import resolve_driver_sd
    faces = np.array([[0, 1, 2]])
    triangle = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    meshes = {'inner': [(faces, triangle)], 'outer': [(faces, triangle)],
              'cap-cavity': [(faces, triangle[:, [0, 2, 1]] * .4)]}
    area, metadata, notes = resolve_driver_sd({}, meshes, [0, 0, 1], 'speaker')
    assert metadata['geometric_sd_m2'] == pytest.approx(.54)
    assert area is None and metadata['geometry_ambiguous']
    assert metadata['sd_source'] is None
    assert any('more than 10 %' in note for note in notes)
    assert any('Sd not determined; enter Sd to get acoustic impedance' in note for note in notes)


def test_admission_counts_moving_meshes_without_geometry_and_skips_exterior_only(monkeypatch):
    from server.solver import coupled_admission as admission
    def unexpected(*args, **kwargs):
        pytest.fail('mesh counting or exterior-only admission read geometry')
    monkeypatch.setattr(admission, '_channel_moving_meshes', unexpected)
    spec = driver(sd_m2=.0001, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='speaker', source_ids=['front'], motion='axial', exterior_transducer=spec)
    record = {'fem_regions': [region_input('front', 'speaker').record,
                              region_input('rear', 'speaker').record]}
    assert admission._channel_moving_mesh_ids(record, channel) == {'fem:front', 'fem:rear', 'exterior'}
    assert admission.driver_sd_problem({}, [channel], exterior_msh_text='malformed') is None
    assert admission.fem_request_problem(record, [channel], enabled=True, inspect_sd_geometry=False) is None


@pytest.mark.parametrize('failure', ['malformed', 'nested', 'tet-boundary'])
def test_sd_admission_geometry_failures_have_named_refusals(monkeypatch, failure):
    from server.solver.coupled_admission import driver_sd_problem
    region = region_input(channel='speaker')
    record = {'fem_regions': [region.record]}
    monkeypatch.setattr('server.mesh.artifact.read_verified_fem_region_mesh', lambda *args: 'mesh')
    domain = region.domain
    monkeypatch.setattr('server.mesh.fem_regions.read_fem_region_domain', lambda *args: domain)
    spec = driver(sd_m2=.0001, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    if failure == 'tet-boundary':
        # Remove the tetrahedron, leaving its tagged moving face off the boundary.
        domain = replace(domain, tetrahedra=np.empty((0, 4), dtype=int))
        sources = []
    else:
        sources = ['front']
        record['coupled_exterior'] = {'physical_names': ['source_id=front']}
        monkeypatch.setattr('server.mesh.artifact.read_verified_coupled_exterior_mesh', lambda *args: 'mesh')
        if failure == 'malformed':
            def invalid(*args):
                raise ValueError('Malformed exterior mesh')
            monkeypatch.setattr('server.mesh.fem_coupling.read_coupled_exterior', invalid)
        else:
            faces = np.concatenate(list(domain.surfaces.values()))
            inner = domain.points_mm * .1 + [1., 1., 1.]
            points = np.concatenate([domain.points_mm, inner])
            groups = {'source_id=front': faces, 'sealed-chamber': faces + len(inner)}
            monkeypatch.setattr('server.mesh.fem_coupling.read_coupled_exterior', lambda *args: (points, groups))
    channel = DriveChannel(id='speaker', source_ids=sources, motion='axial', exterior_transducer=spec)
    problem = driver_sd_problem(record, [channel])
    assert problem[0] == 'driver_sd_geometry_invalid'
    assert "Channel 'speaker'" in problem[1]
    assert {'tet-boundary': 'tetrahedral boundary', 'nested': 'Nested',
            'malformed': 'Malformed exterior mesh'}[failure] in problem[1]


def test_imported_chamber_compile_reads_moving_mesh_only_once(monkeypatch):
    region = region_input()
    calls = []
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.read_verified_fem_region_mesh',
                        lambda record, identity: calls.append(identity) or 'mesh')
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.read_fem_region_domain', lambda *args: region.domain)
    monkeypatch.setattr('server.solver.beat_adapter.coupled_request.imported_anchor_frame', lambda *args: FRAME)
    def unexpected(*args, **kwargs):
        pytest.fail('compilation reread and oriented moving geometry during admission')
    monkeypatch.setattr('server.solver.coupled_admission._channel_moving_meshes', unexpected)
    spec = driver(sd_m2=.00005, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    geometry = SimpleNamespace(drive_channels=[DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=spec)], combine=None)
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    request = build_imported_coupled_request({'fem_regions': [region.record]}, geometry, context, frequencies_hz=[100.])
    assert calls == [region.record['region_id']]
    params = request.wire['compiled_system']['components'][0]['parameters']
    assert params['lumped_sealed_rear_chamber']['projected_area_m2'] == pytest.approx(.00005)


@pytest.mark.parametrize('rear_fraction', [.01, .049])
def test_chamber_refuses_even_small_disconnected_rear_patch(rear_fraction):
    from server.solver.beat_adapter.coupled_transducers import parameters
    from server.solver.coupled_admission import driver_sd_problem
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    front = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    rear = np.array([[[2., 0., 0.], [2., rear_fraction, 0.], [3., 0., 0.]]])
    meshes = {'front-and-rear': [(faces, np.concatenate([front, rear]))]}
    spec = driver(sd_m2=.5, lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    channel = DriveChannel(id='speaker', source_ids=[], motion='axial', exterior_transducer=spec)
    assert driver_sd_problem({}, [channel], moving_meshes={'speaker': meshes})[0] == 'lumped_chamber_double_rear_load'
    with pytest.raises(ValueError, match='lumped_chamber_double_rear_load'):
        parameters(spec, meshes)
