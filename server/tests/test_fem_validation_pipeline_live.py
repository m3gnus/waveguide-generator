"""F11: synthetic wgreturn -> real intake -> managed CPU job -> durable results.

Opt in through the compute broker with WG2_RUN_LIVE=1, WG2_FEM_REGIONS=1,
WG2_BEAT_PROVIDER=official and a provisioned WG2_BEAT_RUNTIME_DIR. Each case
uses float64 and a small mesh. WG2_FEM_VALIDATION_DIR optionally receives JSON
evidence, including failed acoustic comparisons; no evidence is kept in-tree.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import math
import os
import time

import numpy as np
import pytest

from server.cadlink.store import CadLinkStore
from server.cadlink.intake.readings import confirm_frame
from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.solver.beat_adapter.results import acceleration_scale
from server.solver.beat_adapter.observations import build_observations
from server.tests.fem_validation import (
    C, RHO, CHAMBER_VOLUME_M3, PORT_LENGTH_M, PORT_RADIUS_M,
    comparison_evidence, helmholtz_frequency, monopole_pressure, resonance_from_compliance,
    resonance_from_admittance, sealed_compliance_pressure, stiffness_from_chamber_pressure,
    surface_average, surface_centroid, tube_input_impedance, tube_pressure_ratio,
    validation_bundle, validation_request, ingest_validation_bundle, write_evidence,
)

_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1; run F11 through the compute broker")]


@pytest.fixture
def managed_cpu(monkeypatch):
    """Own only this test's managed child; never use a direct engine fallback."""
    from server.solver import official_beat
    from server.solver.beat_runtime import readiness
    from server.solver.beat_runtime.manager import ManagedWorker, WorkerManager

    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    status = readiness.backend_status("cpu")
    assert status["available"], status["reason"]
    manager = WorkerManager(mode="child")
    monkeypatch.setattr(official_beat, "get_manager", lambda: manager)
    try:
        assert isinstance(manager.get_worker("cpu"), ManagedWorker)
        yield
    finally:
        manager.shutdown()


def _pipeline(tmp_path, case, frequencies, *, engine_registry=None):
    bundle, manifest = validation_bundle(tmp_path / "returns", case)
    record = ingest_validation_bundle(bundle, tmp_path, case)
    assert record["fem_regions"][0]["stats"]["tetrahedra"] > 0
    if case in {"open", "ported"}:
        assert record["coupled_exterior"]["stats"]["open_edges"] == 0
    request = validation_request(record, manifest, frequencies)

    async def run():
        cad_store = CadLinkStore(tmp_path / "cadlink.db")
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"), cadlink_store=cad_store,
                             engine_registry=engine_registry)
        try:
            # Synthetic CAD-authored returns have no painted source from which
            # WG can infer an axis. Confirm their known +z frame through the
            # production intake API, preserving the real admission gate.
            confirm_frame(cad_store, record, "+z")
            started = time.monotonic()
            job_id = await runtime.submit(request)
            await runtime.wait_idle(timeout=120.)
            elapsed = time.monotonic() - started
            row = runtime.store.get_job_row(job_id)
            assert row["status"] == "complete", row["error_message"]
            assert row["task_metadata"]["has_fem_field_artifact"]
            envelope = runtime.store.get_results(job_id)
            RESULT_ENVELOPE_ADAPTER.validate_python(envelope)
            assert envelope["channel_order"] == ["drive-lf"]
            assert envelope["frequencies"] == list(frequencies)
            assert envelope["metadata"]["fem"] == {
                "engine": "beat-cpu", "precision": "float64"}
            assert envelope["metadata"]["solve_path"] == (
                "coupled-fem-bem" if case in {"open", "ported"} else "interior-fem")
            with np.load(BytesIO(runtime.store.get_fem_field(job_id)), allow_pickle=False) as stored:
                field = {name: stored[name].copy() for name in stored.files}
            assert field["schema"].item() == "wg-fem-field-store-v1"
            assert field["phasor_convention"].item() == "exp(-i omega t)"
            assert field["drive_normalization"].item() == "unit_acceleration_m_per_s2"
            assert field["channel_ids"].tolist() == ["drive-lf"]
            assert field["region_ids"].tolist() == ["rear-chamber"]
            np.testing.assert_array_equal(field["frequencies_hz"], frequencies)
            assert field["pressure"].dtype == np.complex128
            assert field["pressure"].shape == (len(frequencies), 1, len(field["points_mm_0"]))
            assert np.isfinite(field["pressure"]).all()
            return record, envelope, field, {"job_id": job_id, "job_elapsed_s": elapsed}
        finally:
            await runtime.shutdown()
            await runtime.engine_registry.shutdown_prewarm()
            cad_store.close()

    return asyncio.run(run())


def test_f11_sealed_box_compliance(managed_cpu, tmp_path):
    frequencies = [100.]
    record, _, field, details = _pipeline(tmp_path, "sealed", frequencies)
    # Per unit acceleration, |x| = 1/omega². Check EVERY node, not just a mean.
    reference = sealed_compliance_pressure(RHO, C, .040 * .030,
                                            1 / (2 * math.pi * 100)**2, .040 * .030 * .020)
    computed = np.abs(field["pressure"][0, 0])
    tolerance = .002
    write_evidence("sealed", frequencies, computed, reference, tolerance, record,
                   quantity="nodal_pressure_amplitude_pa_per_m_s2", **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Sealed compliance: check compactness, mesh and drive scaling"


def test_f11_open_box_monopole(managed_cpu, tmp_path):
    frequencies = [100.]
    record, envelope, field, details = _pipeline(tmp_path, "open", frequencies)
    channel = envelope["channels"]["drive-lf"]
    # Absolute on-axis SPL anchors the normalized polar plane. Reconstruct
    # both observation amplitudes from the durable envelope (20 uPa reference).
    level = channel["metadata"]["spl_on_axis"]["plane_reference_spl_db"]["horizontal"][0]
    polar = np.asarray(channel["directivity"]["horizontal"][0])
    polar = polar[np.isin(polar[:, 0], [0., 90.])]
    assert polar[:, 0].tolist() == [0., 90.]
    computed = 20e-6 * 10**((level + polar[:, 1]) / 20)
    # The polar origin is the throat, not the opening at (40, 15, 10) mm.
    # Reconstruct the actual observation points in the published solver frame.
    basis = channel["metadata"]["observation_frame_basis"]
    layout = build_observations(angle_range=(-180., 180., 5), planes=("horizontal",),
                                distance_m=2., origin_m=basis["origin_m"], axis=basis["axis"],
                                u=basis["u"], v=basis["v"], sphere_grid=None, precision="float64")
    points = layout.points_m["horizontal"][np.isin(layout.angles_deg, polar[:, 0])]
    centroid = surface_centroid(field, 0, "port")
    distances = np.linalg.norm(points - centroid, axis=1)
    reference = monopole_pressure(RHO, 100., .040 * .030 * abs(acceleration_scale(100.)), distances)
    tolerance = .02
    write_evidence("open", frequencies, computed, reference, tolerance, record,
                   quantity="far_pressure_amplitude_pa_per_m_s2", angles_deg=polar[:, 0].tolist(),
                   opening_centroid_m=centroid.tolist(), observation_points_m=points.tolist(),
                   opening_distances_m=distances.tolist(), **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Monopole: check interface winding and volume velocity"
    inside = np.abs(field["pressure"] / acceleration_scale(100.))
    assert 1. < inside.max() < 100.


def test_f11_ported_box_helmholtz(managed_cpu, tmp_path):
    reference = helmholtz_frequency(C, math.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3,
                                    PORT_LENGTH_M, .85 * PORT_RADIUS_M, .61 * PORT_RADIUS_M)
    # A two-point bracket suffices for affine inverse compliance and bounds
    # CPU cost. Both points are compact: k * chamber width < 0.11.
    frequencies = (reference * np.array([.94, 1.06])).tolist()
    record, _, field, details = _pipeline(tmp_path, "ported", frequencies)
    pressure = surface_average(field, 0, "cone")[:, 0]
    # The resolved circular neck gave 0.57% tuning error. A 1% bound admits
    # that discretization but rejects the +1.9% pressure-release outlet result.
    # Tuning alone cancels pressure scaling: also recover rho*c²/V to 1%.
    tolerance = .01
    stiffness = stiffness_from_chamber_pressure(frequencies, pressure, .040**2)
    stiffness_reference = RHO * C**2 / CHAMBER_VOLUME_M3
    checks = {"bulk_stiffness_pa_per_m3": comparison_evidence(stiffness, stiffness_reference, .01)}
    try:
        computed = resonance_from_compliance(frequencies, pressure)
    except ValueError as exc:
        write_evidence("ported", frequencies, [], reference, tolerance, record,
                       passed=False, failure=str(exc), chamber_pressure_real=pressure.real.tolist(),
                       chamber_pressure_imag=pressure.imag.tolist(), checks=checks, **details)
        raise
    write_evidence("ported", frequencies, computed, reference, tolerance, record,
                   quantity="helmholtz_frequency_hz", inner_end_correction_m=.85 * PORT_RADIUS_M,
                   outer_end_correction_m=.61 * PORT_RADIUS_M,
                   chamber_pressure_real=pressure.real.tolist(), chamber_pressure_imag=pressure.imag.tolist(),
                   checks=checks, **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Helmholtz: check neck resolution, end geometry and lumped assumptions"
    assert stiffness == pytest.approx(stiffness_reference, rel=.01), "Bulk stiffness: check chamber volume and absolute drive/pressure scaling"


def test_f11_plane_wave_terminated_tube(managed_cpu, tmp_path):
    frequencies = [100., 500., 1000.]
    # Rectangular rigid tube: first transverse cutoff c/(2 max(w,h)) = 17150 Hz.
    assert max(frequencies) < C / (2 * .010)
    record, _, field, details = _pipeline(tmp_path, "tube", frequencies)
    pressure = surface_average(field, 0, "cone")[:, 0]
    # BEAT's FEM drive is positive OUT of the air region. At the inlet its
    # outward normal is -z, so Q into the tube = -S * prescribed velocity.
    computed = pressure / (-np.array([acceleration_scale(f) for f in frequencies]) * .010**2)
    reference = np.array([tube_input_impedance(RHO, C, .010**2, .100, f) for f in frequencies])
    tolerance = .02
    outlet = surface_average(field, 0, "port")[:, 0]
    ratio = outlet / pressure
    ratio_reference = np.array([tube_pressure_ratio(C, .100, f) for f in frequencies])
    phase_error = np.angle(ratio / ratio_reference)
    # h=5 mm gives kh=0.092 at 1 kHz (kL=1.83). P1 phase dispersion
    # is O((kh)²), about 0.001 rad in the 1-D estimate kL*(kh)²/24;
    # 0.02 rad and 2% magnitude allow tetrahedral error without hiding
    # missing propagation, conjugation, or a pressure-release termination.
    checks = {
        "propagation_magnitude": comparison_evidence(np.abs(ratio), np.abs(ratio_reference), .02),
        "propagation_phase": {"computed_rad": np.angle(ratio).tolist(),
                              "reference_rad": np.angle(ratio_reference).tolist(),
                              "absolute_error_rad": np.abs(phase_error).tolist(), "tolerance_rad": .02,
                              "passed": bool(np.all(np.isfinite(phase_error)) and np.all(np.abs(phase_error) <= .02))},
    }
    write_evidence("tube", frequencies, computed, reference, tolerance, record,
                   quantity="complex_input_impedance_pa_s_m3", first_cross_mode_hz=C / (2 * .010),
                   inlet_pressure_real=pressure.real.tolist(), inlet_pressure_imag=pressure.imag.tolist(),
                   outlet_pressure_real=outlet.real.tolist(), outlet_pressure_imag=outlet.imag.tolist(),
                   propagation_ratio_real=ratio.real.tolist(), propagation_ratio_imag=ratio.imag.tolist(),
                   propagation_reference_real=ratio_reference.real.tolist(), propagation_reference_imag=ratio_reference.imag.tolist(),
                   mesh_size_mm=5., wavenumber_length_rad=(2 * math.pi * np.array(frequencies) * .100 / C).tolist(),
                   phasor_convention=field["phasor_convention"].item(), checks=checks, **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Tube impedance: check termination kind, phase, orientation and mesh dispersion"
    np.testing.assert_allclose(np.abs(ratio), 1., rtol=.02, atol=0)
    assert np.abs(phase_error) == pytest.approx(np.zeros(len(frequencies)), abs=.02)


def test_f11_rigid_end_tube_resonance(managed_cpu, tmp_path):
    length, area = .100, .010**2
    resonance_reference = C / (2 * length)
    # Eight small interior-only solves, no exterior BEM, h=2.5 mm. Stay below
    # the 17150 Hz cross mode; avoid the exact 857.5 Hz impedance zero and
    # 1715 Hz pressure pole. The last local bracket straddles the latter.
    frequencies = resonance_reference * np.array([.30, .42, .58, .70, .85, .95, 1.05, 1.15])
    assert max(frequencies) < C / (2 * .010)
    record, _, field, details = _pipeline(tmp_path, "closed-tube", frequencies.tolist())
    pressure = surface_average(field, 0, "cone")[:, 0]
    flow = -area * np.array([acceleration_scale(f) for f in frequencies])
    impedance = pressure / flow
    reference = np.array([tube_input_impedance(RHO, C, area, length, f, math.inf) for f in frequencies])
    # ZL -> infinity in the existing exp(-i wt) transfer matrix yields
    # +i Zc cot(kL); pressure resonances are half-wave, not quarter-wave.
    # Compare Zin to 2% away from poles and Y to 2% in their local bracket.
    near_pole = np.array([False, False, False, False, False, True, True, False])
    admittance = 1 / impedance
    checks = {"admittance_near_pressure_pole": {
        **comparison_evidence(admittance[near_pole], 1 / reference[near_pole], .02),
        "frequencies_hz": frequencies[near_pole].tolist()}}
    try:
        resonance = resonance_from_admittance(frequencies[near_pole], admittance[near_pole])
    except ValueError as exc:
        write_evidence("closed-tube", frequencies.tolist(), impedance[~near_pole], reference[~near_pole], .02, record,
                       passed=False, failure=str(exc), checks=checks,
                       impedance_frequencies_hz=frequencies[~near_pole].tolist(),
                       inlet_pressure_real=pressure.real.tolist(), inlet_pressure_imag=pressure.imag.tolist(), **details)
        raise
    checks["pressure_resonance_hz"] = comparison_evidence(resonance, resonance_reference, .01)
    write_evidence("closed-tube", frequencies.tolist(), impedance[~near_pole], reference[~near_pole], .02, record,
                   quantity="complex_input_impedance_pa_s_m3", checks=checks,
                   impedance_frequencies_hz=frequencies[~near_pole].tolist(),
                   inlet_pressure_real=pressure.real.tolist(), inlet_pressure_imag=pressure.imag.tolist(),
                   first_cross_mode_hz=C / (2 * .010), antiresonance_reference_hz=C / (4 * length),
                   mesh_size_mm=2.5, phasor_convention=field["phasor_convention"].item(), **details)
    np.testing.assert_allclose(impedance[~near_pole], reference[~near_pole], rtol=.02, atol=0)
    np.testing.assert_allclose(admittance[near_pole], 1 / reference[near_pole], rtol=.02, atol=0)
    assert resonance == pytest.approx(resonance_reference, rel=.01)
