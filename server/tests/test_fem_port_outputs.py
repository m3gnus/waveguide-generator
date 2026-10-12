"""Port output contracts and synthetic V9 identities, without native solves."""
import base64
from dataclasses import replace
import json

import numpy as np
import pytest

from server.jobs.models import DriveChannel
from server.solver.beat_adapter.coupled_request import build_coupled_request
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.results import acceleration_scale, map_coupled_sweep, ResultContractError
from server.solver.coupled_system import ExteriorSurface
from server.solver.result_mapping import (
    coupled_interface_diagnostics, map_coupled_port_response, map_coupled_transducer_response,
)
from server.tests.beat_adapter.conftest import wire
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.test_fem_electrodynamic import driver
from server.tests.test_fem_engine_route import compiled, events, region_input, FRAME


def multi_port(*, precision="float64", layout=None, frequencies=(100., 200.), transducers=None):
    regions = [region_input('one', 'one', interface=True), region_input('two', 'two', interface=True)]
    regions[1] = replace(regions[1], domain=replace(regions[1].domain, points_mm=regions[1].domain.points_mm + 20))
    groups = {name: faces + i * 4 for i, region in enumerate(regions) for name, faces in region.domain.surfaces.items()}
    exterior = ExteriorSurface(np.concatenate([r.domain.points_mm for r in regions]), groups,
                               {n: {'kind': 'interface' if 'kind=interface' in n else 'rigid'} for n in groups})
    return build_coupled_request(exterior, regions, layout=layout or compiled().layout, frame=FRAME,
                                 frequencies_hz=frequencies, precision=precision, backend="metal" if precision == "float32" else "cpu", transducers=transducers)


def phasors(values):
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["content_base64"]), dtype="<c16").reshape(values["shape"])
    return np.array([complex(z['real'], z['imaginary']) for z in np.asarray(values, dtype=object).flat]).reshape(np.shape(values))


@pytest.mark.parametrize('precision', ['float32', 'float64'])
@pytest.mark.parametrize('voltage', [False, True])
def test_port_drive_basis_phase_and_linearity_at_every_observation(voltage, precision):
    request = compiled(transducers={'fem': driver()} if voltage else None, precision=precision, backend="metal" if precision == "float32" else "cpu")
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'fem')
    ports = response['metadata']['fem_ports']
    scale = 1 if voltage else acceleration_scale(response['frequencies'][0])
    assert phasors(ports['interfaces'][0]['average_normal_velocity'])[0] == pytest.approx(np.conj(2 * (2 + 3j) * scale))
    assert ports['drive_normalization'] == ('voltage_rms' if voltage else 'unit_acceleration')
    assert ports['reference_voltage_rms_v'] == (request.wire['solver_options']['transducer_reference_voltage_v'] if voltage else None)
    assert ports['velocity_units'] == 'm/s' and ports['pressure_units'] == 'Pa'
    assert ports['normal_orientation'] == 'bounded_region_outward'
    for name in request.layout.planes:
        port_sum = sum(phasors(p['radiated_pressure'][name]) for p in ports['interfaces'])
        other = phasors(ports['other_exterior_radiated_pressure'][name])
        total = phasors(ports['total_radiated_pressure'][name])
        np.testing.assert_allclose(port_sum + other, total, rtol=1e-6 if precision == "float32" else 1e-15)
        np.testing.assert_allclose(total, np.conj(sweep.channels['fem'].pressure_complex[:, request.layout.planes.index(name)]))
        np.testing.assert_allclose(other, np.conj(sweep.interface_radiation[name][:, 1, 1]))
    json.dumps(response, allow_nan=False)


@pytest.mark.parametrize("precision", ["float32", "float64"])
def test_multiple_ports_and_no_other_exterior_source(precision):
    request = multi_port(precision=precision)
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'one')
    block = response['metadata']['fem_ports']
    assert [p['id'] for p in block['interfaces']] == ['interface:one:port', 'interface:two:port']
    assert sweep.interface_velocity.shape == (2, 2, 2)
    assert sweep.interface_areas_m2 == pytest.approx([.00005, .00005])
    assert 'other_exterior_radiated_pressure' not in block
    assert 'diaphragm_radiated_pressure' not in block
    for name in request.layout.planes:
        np.testing.assert_allclose(sum(phasors(p['radiated_pressure'][name]) for p in block['interfaces']),
                                   phasors(block['total_radiated_pressure'][name]), rtol=1e-6 if precision == 'float32' else 1e-15)


@pytest.mark.parametrize('precision', ['float32', 'float64'])
@pytest.mark.parametrize('exterior_source', [False, True])
def test_reconstruction_residual_is_never_a_radiation_source(precision, exterior_source):
    request = compiled(precision=precision, backend="metal" if precision == "float32" else "cpu") if exterior_source else multi_port(precision=precision)
    sweep = map_coupled_sweep(events(request), request)
    for values in sweep.interface_radiation.values():
        values[:, :, -1] += .001 + .002j
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    channel = 'fem' if exterior_source else 'one'
    map_coupled_port_response(response, sweep, request, channel)
    block = response['metadata']['fem_ports']
    assert 'diaphragm_radiated_pressure' not in block
    if not exterior_source:
        assert 'other_exterior_radiated_pressure' not in block
    else:
        basis = request.wire['excitation_port_ids'].index(request.channel_ports[channel][0])
        source = sweep.interface_radiation_source_ids.index('radiation:other-exterior')
        for plane in request.layout.planes:
            np.testing.assert_allclose(phasors(block['other_exterior_radiated_pressure'][plane]),
                                       np.conj(sweep.interface_radiation[plane][:, basis, source]))


@pytest.mark.parametrize('mutation,message', [
    ('ids', 'Interface identity or amplitude convention changed'),
    ('area', 'Interface averaging or represented area changed'),
    ('orientation', 'Interface averaging or represented area changed'),
    ('axes', 'Coupled quantity identity, unit or precision changed'),
    ('source_ids', 'Interface radiation decomposition changed'),
    ('points', 'Interface radiation decomposition changed'),
    ('decomposition', 'Interface radiation decomposition changed'),
])
@pytest.mark.parametrize('precision', ['float32', 'float64'])
def test_port_contract_mismatch_is_refused(mutation, message, precision):
    request = compiled(precision=precision, backend="metal" if precision == "float32" else "cpu")
    raw = coupled_result(request.wire, request.wire['frequencies_hz'][0])
    terminal = {'type': 'cancelled', 'solved_count': 1}
    assert map_coupled_sweep([{'type': 'result', 'result': raw}, terminal], request).cancelled
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
    with pytest.raises(ResultContractError, match='^' + message + '$'):
        map_coupled_sweep([{'type': 'result', 'result': raw}, terminal], request)


@pytest.mark.parametrize('reconstruction,expected', [([.012, .034], .034), ([], None), ([None, .034], None), (.012, .012)])
def test_diagnostics_are_named_ungated_and_align_to_cancelled_prefix(reconstruction, expected):
    request = multi_port()
    raw = coupled_result(request.wire, 100.)
    raw['diagnostics'].update(interface_ids=['interface:two:port', 'interface:one:port'],
                              interface_pressure_continuity_errors=[.2, .1],
                              interface_flux_conservation_errors=[.4, .3],
                              interface_radiation_boundary_reconstruction_relative_error=reconstruction)
    sweep = map_coupled_sweep([{'type': 'result', 'result': raw}, {'type': 'cancelled', 'solved_count': 1}], request)
    diagnostics = coupled_interface_diagnostics(sweep, request)
    assert diagnostics['frequencies'] == [100.]
    assert diagnostics['interfaces'][0]['pressure_continuity_error'] == [.1]
    assert diagnostics['interfaces'][0]['flux_conservation_error'] == [.3]
    assert diagnostics['interfaces'][1]['flux_conservation_error'] == [.4]
    assert diagnostics['aggregation'] == 'maximum_over_excitation_bases'
    assert diagnostics['interface_radiation_boundary_reconstruction_relative_error'] == [expected]
    sweep.channels['one'].solver_log[0]['native_diagnostics'].pop('interface_radiation_boundary_reconstruction_relative_error')
    assert coupled_interface_diagnostics(sweep, request)['interface_radiation_boundary_reconstruction_relative_error'] == [None]


@pytest.mark.parametrize('precision', ['float32', 'float64'])
@pytest.mark.parametrize('current', [.1 + .2j, .3 - .4j, 0j])
def test_rms_input_power_matches_impedance_identity(current, precision):
    request = compiled(transducers={'fem': driver()}, reference_voltage_rms_v=5.66, precision=precision, backend="metal" if precision == "float32" else "cpu")
    sweep = map_coupled_sweep(events(request), request)
    sweep.current[:, 1, 0] = current
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    spec = DriveChannel(id='fem', source_ids=[], motion='axial', exterior_transducer=driver())
    map_coupled_transducer_response(response, sweep, request, 'fem', spec)
    power = response['metadata']['driver']['input_power_w']
    assert power['values'] == pytest.approx([np.real(5.66 * np.conj(current))] * 2)
    assert power['reference_voltage_rms_v'] == pytest.approx(5.66)
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


@pytest.mark.parametrize('precision', ['float32', 'float64'])
@pytest.mark.parametrize('voltage', [False, True])
def test_distinct_port_attribution_by_basis_and_observation(precision, voltage):
    # Each port, drive basis, plane and observation has an independent phasor.
    # Explicit expected values catch port swaps even if their sum is unchanged.
    request = multi_port(precision=precision, transducers={'one': driver()} if voltage else None)
    expected = {}
    stream = []
    for fi, frequency in enumerate(request.wire['frequencies_hz']):
        raw = coupled_result(request.wire, frequency)
        for pi, plane in enumerate(request.layout.planes):
            contributions = np.array([[
                [(1 + 10 * basis + 3 * port + point + fi) + (2 + pi + port - point) * 1j
                 for point in range(len(request.layout.angles_deg))]
                for port in range(2)] for basis in range(2)])
            total = contributions.sum(axis=1)
            quantity = next(q for q in raw['quantities'] if q['id'] == f'interface:radiation:{plane}')
            quantity['values'] = wire(np.concatenate([contributions, total[:, None]], axis=1), precision)
            next(q for q in raw['quantities'] if q['id'] == f'pressure:{plane}')['values'] = wire(total, precision)
            expected[fi, plane] = np.conj(contributions[0] * (1 if voltage else acceleration_scale(frequency)))
        stream.append({'type': 'result', 'result': raw})
    stream.append({'type': 'completed', 'solved_count': 2})
    sweep = map_coupled_sweep(stream, request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'one')
    for port, mapped in enumerate(response['metadata']['fem_ports']['interfaces']):
        for plane in request.layout.planes:
            np.testing.assert_allclose(phasors(mapped['radiated_pressure'][plane]),
                np.array([expected[fi, plane][port] for fi in range(2)]), rtol=1e-7)


@pytest.mark.parametrize('precision', ['float32', 'float64'])
def test_production_port_block_is_bounded_and_has_no_sphere_replay(precision):
    layout = build_observations(sphere_grid=(37, 72), precision=precision)
    request = multi_port(layout=layout, frequencies=np.linspace(100, 20000, 200), precision=precision)
    outputs = request.wire['outputs']
    assert any(o['id'] == 'pressure:sphere' for o in outputs)
    assert [o['id'] for o in outputs if o['quantity'] == 'interface_radiated_pressure'] == [
        'interface:radiation:horizontal', 'interface:radiation:vertical']
    sweep = map_coupled_sweep(events(request), request)
    response = {'frequencies': request.wire['frequencies_hz'], 'metadata': {}}
    map_coupled_port_response(response, sweep, request, 'one')
    block = response['metadata']['fem_ports']
    assert set(sweep.interface_radiation) == set(layout.planes)
    assert set(block['observation_points_m']) == set(layout.planes)
    assert 'sphere_theta_deg' not in block and 'sphere_phi_deg' not in block
    for port in block['interfaces']:
        assert 'observation_points_m' not in port
        assert set(port['radiated_pressure']) == set(layout.planes)
    assert len(json.dumps(block, allow_nan=False).encode()) < 1_000_000
