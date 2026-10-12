"""Opt-in V1: meshed sealed rear chamber against the lumped closed-box model."""
from __future__ import annotations

import math
import os

import numpy as np
import pytest

from server.mesh.fem_regions import read_fem_region_domain
from server.solver.beat_adapter.coupled_request import build_coupled_request
from server.solver.beat_adapter.coupled_transducers import mechanical_impedance
from server.solver.beat_adapter.observations import build_observations
from server.solver.coupled_system import ExteriorSurface, FemRegionInput
from server.tests.test_fem_coupled_beat_live import worker as worker_fixture
from server.tests.test_fem_electrodynamic import driver
from server.tests.test_fem_engine_route import FRAME
from server.tests.test_fem_regions import _mesh, _region, _write_step

worker = worker_fixture
pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get('WG2_RUN_LIVE') != '1', reason='opt-in sealed-box qualification')]


def test_sealed_electrodynamic_box_impedance_peak_and_spl(worker, tmp_path):
    # Small dimensions keep the first cavity mode well above the resonance.
    region = _region(mesh_size_mm=6.)
    region['surface_groups'] = region['surface_groups'][:1]
    region['surface_groups'][0]['mesh_size_mm'] = 6.
    domain = read_fem_region_domain(_mesh(_write_step(tmp_path / 'box.step'), region)['msh_text'], 'rear-chamber')
    exterior = ExteriorSurface(domain.points_mm, domain.surfaces, {
        name: {'kind': 'moving', 'drive_channel_id': 'drive-lf'} if 'kind=moving' in name else {'kind': 'rigid'}
        for name in domain.surfaces})
    spec = driver()
    rho, c, sd, vb = 1.21, 343., .04 * .03, .04 * .03 * .02
    fs = 1 / (2 * math.pi * math.sqrt(spec.mmd_kg * spec.cms_m_per_n))
    vas = rho * c**2 * sd**2 * spec.cms_m_per_n
    fc = fs * math.sqrt(1 + vas / vb)
    frequencies = np.unique(np.concatenate([np.linspace(.8 * fc, 1.2 * fc, 25), [fc / 4, fc / 2]]))
    layout = build_observations(angle_range=(0., 90., 3), distance_m=1., sphere_grid=None, precision='float64')
    request = build_coupled_request(exterior, [FemRegionInput(region, domain)], layout=layout,
                                    frequencies_hz=frequencies, frame=FRAME, transducers={'drive-lf': spec})
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    # Through the managed bridge, as production runs it (the managed worker has no submit).
    from types import SimpleNamespace
    from server.solver.official_beat import solve_coupled_compiled
    sweep = solve_coupled_compiled(request, worker_manager=SimpleNamespace(get_worker=lambda *a, **k: worker))
    current, velocity = sweep.current[:, 0, 0], sweep.velocity[:, 0, 0]
    zin = 2.83 / current
    params = request.wire['compiled_system']['components'][0]['parameters']
    zm = np.asarray([mechanical_impedance(params, f, density=rho, sound_speed=c) for f in frequencies])
    omega = 2 * np.pi * frequencies
    # Low-ka circular-piston radiation at equivalent projected area, plus
    # the explicit rear cavity compliance. No added mass is hidden in Mmd.
    ka = omega * math.sqrt(sd / math.pi) / c
    zrad = rho * c * sd * (ka**2 / 2 - 1j * 8 * ka / (3 * math.pi))
    zbox = rho * c**2 * sd**2 / vb / (-1j * omega)
    expected = spec.re_ohm - 1j * omega * spec.le_h + spec.bl_n_per_a**2 / (zm + zbox + zrad)
    peak = int(np.argmax(np.abs(zin)))
    assert frequencies[peak] == pytest.approx(fc, rel=.03)
    assert abs(zin[peak]) == pytest.approx(np.max(np.abs(expected)), rel=.05)
    np.testing.assert_allclose(zin[frequencies < fc / 2], expected[frequencies < fc / 2], rtol=.02)
    # The compact body radiates as a monopole at low frequency; compare the
    # absolute voltage-basis SPL, including electromechanical velocity.
    expected_velocity = 2.83 * spec.bl_n_per_a / ((spec.re_ohm - 1j * omega * spec.le_h) * (zm + zbox + zrad) + spec.bl_n_per_a**2)
    np.testing.assert_allclose(np.abs(velocity[:2]), np.abs(expected_velocity[:2]), rtol=.03)
    expected_pressure = rho * omega * sd * np.abs(expected_velocity) / (4 * math.pi)
    pressure = np.abs(sweep.channels['drive-lf'].pressure_complex[:, 0, 0])
    assert 20 * np.log10(pressure[0] / 20e-6) == pytest.approx(20 * np.log10(expected_pressure[0] / 20e-6), abs=.5)
