"""Port output contracts and synthetic V9 identities, without native solves."""
from dataclasses import replace
import json

import numpy as np
import pytest

from server.jobs.models import DriveChannel
from server.solver.beat_adapter.coupled_request import build_coupled_request
from server.solver.beat_adapter.results import acceleration_scale, map_coupled_sweep, ResultContractError
from server.solver.coupled_system import ExteriorSurface
from server.solver.result_mapping import (
    coupled_interface_diagnostics, map_coupled_port_response, map_coupled_transducer_response,
)
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.test_fem_electrodynamic import driver
from server.tests.test_fem_engine_route import compiled, events, region_input, FRAME


def multi_port():
    regions = [region_input('one', 'one', interface=True), region_input('two', 'two', interface=True)]
    regions[1] = replace(regions[1], domain=replace(regions[1].domain, points_mm=regions[1].domain.points_mm + 20))
    groups = {name: faces + i * 4 for i, region in enumerate(regions) for name, faces in region.domain.surfaces.items()}
    exterior = ExteriorSurface(np.concatenate([r.domain.points_mm for r in regions]), groups,
                               {n: {'kind': 'interface' if 'kind=interface' in n else 'rigid'} for n in groups})
    return build_coupled_request(exterior, regions, layout=compiled().layout, frame=FRAME, frequencies_hz=[100., 200.])


def phasors(values):
    return np.array([complex(z['real'], z['imaginary']) for z in np.asarray(values, dtype=object).flat]).reshape(np.shape(values))


@pytest.mark.parametrize('voltage', [False, True])
def test_port_drive_basis_phase_and_linearity_at_every_observation(voltage):
    request = compiled(transducers={'fem': driver()} if voltage else None)
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'fem')
    ports = response['metadata']['fem_ports']
    scale = 1 if voltage else acceleration_scale(response['frequencies'][0])
    assert phasors(ports['interfaces'][0]['average_normal_velocity'])[0] == pytest.approx(np.conj(2 * (2 + 3j) * scale))
    assert ports['drive_normalization'] == ('voltage_rms' if voltage else 'unit_acceleration')
    assert ports['reference_voltage_rms_v'] == (2.83 if voltage else None)
    assert ports['velocity_units'] == 'm/s' and ports['pressure_units'] == 'Pa'
    assert ports['normal_orientation'] == 'bounded_region_outward'
    for name in request.layout.points_m:
        port_sum = sum(phasors(p['radiated_pressure'][name]) for p in ports['interfaces'])
        diaphragm = phasors(ports['diaphragm_radiated_pressure'][name])
        total = phasors(ports['total_radiated_pressure'][name])
        np.testing.assert_allclose(port_sum + diaphragm, total, rtol=1e-15)
        if name != 'sphere':
            np.testing.assert_allclose(total, np.conj(sweep.channels['fem'].pressure_complex[:, request.layout.planes.index(name)]))
        else:
            np.testing.assert_allclose(total, np.conj(sweep.channels['fem'].sphere_pressure_complex))
    json.dumps(response, allow_nan=False)


def test_multiple_ports_and_no_other_exterior_source():
    request = multi_port()
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'one')
    block = response['metadata']['fem_ports']
    assert [p['id'] for p in block['interfaces']] == ['interface:one:port', 'interface:two:port']
    assert sweep.interface_velocity.shape == (2, 2, 2)
    assert sweep.interface_areas_m2 == pytest.approx([.00005, .00005])
    for name in request.layout.points_m:
        np.testing.assert_allclose(phasors(block['diaphragm_radiated_pressure'][name]), 0, atol=1e-17)
        np.testing.assert_allclose(sum(phasors(p['radiated_pressure'][name]) for p in block['interfaces']),
                                   phasors(block['total_radiated_pressure'][name]))


@pytest.mark.parametrize('mutation', ['ids', 'area', 'orientation', 'axes', 'source_ids', 'points', 'decomposition'])
def test_port_contract_mismatch_is_refused(mutation):
    request = compiled()
    raw = coupled_result(request.wire, request.wire['frequencies_hz'][0])
    velocity = next(q for q in raw['quantities'] if q['quantity'] == 'interface_average_normal_velocity')
    radiation = next(q for q in raw['quantities'] if q['quantity'] == 'interface_radiated_pressure')
    if mutation == 'ids':
        velocity['metadata']['interface_ids'] = ['wrong']
    elif mutation == 'area':
        velocity['metadata']['represented_area_m2'] = [0]
    elif mutation == 'orientation':
        velocity['metadata']['normal_orientation'] = 'inward'
    elif mutation == 'axes':
        radiation['axes'] = ['excitation', 'observation', 'radiation_source']
    elif mutation == 'source_ids':
        radiation['metadata']['radiation_source_ids'].reverse()
    elif mutation == 'points':
        radiation['metadata']['points_m'] = []
    else:
        radiation['metadata']['decomposition'] = 'wrong'
    with pytest.raises(ResultContractError):
        map_coupled_sweep([{'type': 'result', 'result': raw}], request)


def test_diagnostics_are_named_ungated_and_align_to_cancelled_prefix():
    request = multi_port()
    raw = coupled_result(request.wire, 100.)
    raw['diagnostics'].update(interface_ids=['interface:two:port', 'interface:one:port'],
                              interface_pressure_continuity_errors=[.2, .1],
                              interface_flux_conservation_errors=[.4, .3])
    sweep = map_coupled_sweep([{'type': 'result', 'result': raw}, {'type': 'cancelled', 'solved_count': 1}], request)
    diagnostics = coupled_interface_diagnostics(sweep, request)
    assert diagnostics['frequencies'] == [100.]
    assert diagnostics['interfaces'][0]['pressure_continuity_error'] == [.1]
    assert diagnostics['interfaces'][0]['flux_conservation_error'] == [.3]
    assert diagnostics['interfaces'][1]['flux_conservation_error'] == [.4]
    assert diagnostics['aggregation'] == 'maximum_over_excitation_bases'


@pytest.mark.parametrize('current', [.1 + .2j, .3 - .4j, 0j])
def test_rms_input_power_matches_impedance_identity(current):
    request = compiled(transducers={'fem': driver()}, reference_voltage_rms_v=5.66)
    sweep = map_coupled_sweep(events(request), request)
    sweep.current[:, 1, 0] = current
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    spec = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    map_coupled_transducer_response(response, sweep, request, 'fem', spec)
    power = response['metadata']['driver']['input_power_w']
    assert power['values'] == pytest.approx([np.real(5.66 * np.conj(current))] * 2)
    assert power['reference_voltage_rms_v'] == 5.66
    assert power['amplitude_convention'] == 'rms'
    assert phasors(power['current_rms_a'])[0] == np.conj(current)
    assert power['phase_convention'] == 'engineering_exp_plus_jwt'
    if current:
        assert power['values'][0] >= 0
        assert power['values'][0] == pytest.approx(response['impedance']['real'][0] * abs(current)**2, abs=1e-15)
    json.dumps(response, allow_nan=False)


def test_no_interfaces_revert_guard_and_empty_cancelled_arrays():
    request = compiled(coupled=False)
    assert not any(q['quantity'].startswith('interface_') for q in request.wire['outputs'])
    sweep = map_coupled_sweep(events(request), request)
    response = {'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'fem')
    assert not response['metadata']
    request = compiled()
    sweep = map_coupled_sweep([{'type': 'cancelled', 'solved_count': 0}], request)
    assert sweep.interface_velocity.shape == (0, 2, 1)
    assert sweep.interface_radiation['horizontal'].shape == (0, 2, 3, 3)
