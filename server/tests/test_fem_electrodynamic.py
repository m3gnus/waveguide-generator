"""F10a contract, mixed-basis mapping and load recovery without native solves."""
from __future__ import annotations

from types import SimpleNamespace
import time

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
    spec = driver(semi_inductance={'enabled': True, 're_prime_ohm': 3., 'leb_h': .001,
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
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
    with pytest.raises(UnsupportedPhysics, match="both sides"):
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
        frequencies_hz=[100.], transducers={'front': driver(
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
    spec = driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002})
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
    with pytest.raises(ValueError, match='both sides'):
        parameters(driver(lumped_sealed_rear_chamber={'enabled': True, 'volume_m3': .002}), meshes)


def test_projected_area_mismatch_reaches_compiled_and_result_warnings():
    from server.tests.test_imported_jobs import _request
    region = region_input()
    points = region.domain.points_mm.copy()
    points[:, 0] *= 2
    exterior = ExteriorSurface(points, region.domain.surfaces, {
        name: {'kind': 'moving' if 'moving' in name else 'rigid', 'drive_channel_id': 'fem'}
        for name in region.domain.surfaces})
    with pytest.warns(UserWarning, match='more than 10%'):
        request = build_coupled_request(exterior, [region], layout=compiled().layout, frame=FRAME,
                                        frequencies_hz=[100.], transducers={'fem': driver()})
    assert request.wire['compiled_system']['metadata']['projected_areas_m2']['fem'] == pytest.approx(7.5e-5)
    assert len(request.warnings) == 1
    geometry = _request('wgi_' + '0' * 26).geometry
    geometry.drive_channels = [DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())]
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    response = coupled_envelope(map_coupled_sweep(events(request), request), request, context=context,
        geometry=geometry, record={}, config=official_beat.response_config(request, context), started=time.time())
    assert response['metadata']['warnings'] == list(request.warnings)
    assert response['channels']['fem']['metadata']['warnings'] == list(request.warnings)


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
