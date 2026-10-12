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
from server.tests.fem_validation import (
    C, RHO, CHAMBER_VOLUME_M3, PORT_LENGTH_M, PORT_RADIUS_M,
    helmholtz_frequency, monopole_pressure, resonance_from_compliance,
    sealed_compliance_pressure, surface_average, tube_input_impedance,
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
            assert envelope["channel_order"] == ["drive-LF"]
            assert envelope["frequencies"] == list(frequencies)
            assert envelope["metadata"]["fem"] == {
                "experimental": True, "engine": "beat-cpu", "precision": "float64"}
            assert envelope["metadata"]["solve_path"] == (
                "coupled-fem-bem" if case in {"open", "ported"} else "interior-fem")
            with np.load(BytesIO(runtime.store.get_fem_field(job_id)), allow_pickle=False) as stored:
                field = {name: stored[name].copy() for name in stored.files}
            assert field["schema"].item() == "wg-fem-field-store-v1"
            assert field["phasor_convention"].item() == "exp(-i omega t)"
            assert field["drive_normalization"].item() == "unit_acceleration_m_per_s2"
            assert field["channel_ids"].tolist() == ["drive-LF"]
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
    channel = envelope["channels"]["drive-LF"]
    # Absolute on-axis SPL anchors the normalized polar plane. Reconstruct
    # both observation amplitudes from the durable envelope (20 uPa reference).
    level = channel["metadata"]["spl_on_axis"]["plane_reference_spl_db"]["horizontal"][0]
    polar = np.asarray(channel["directivity"]["horizontal"][0])
    polar = polar[np.isin(polar[:, 0], [0., 90.])]
    assert polar[:, 0].tolist() == [0., 90.]
    computed = 20e-6 * 10**((level + polar[:, 1]) / 20)
    reference = monopole_pressure(RHO, 100., .040 * .030 * abs(acceleration_scale(100.)), 2.)
    tolerance = .05
    write_evidence("open", frequencies, computed, reference, tolerance, record,
                   quantity="far_pressure_amplitude_pa_per_m_s2", angles_deg=polar[:, 0].tolist(), **details)
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
    tolerance = .03
    try:
        computed = resonance_from_compliance(frequencies, pressure)
    except ValueError as exc:
        write_evidence("ported", frequencies, [], reference, tolerance, record,
                       passed=False, failure=str(exc), chamber_pressure_real=pressure.real.tolist(),
                       chamber_pressure_imag=pressure.imag.tolist(), **details)
        raise
    write_evidence("ported", frequencies, computed, reference, tolerance, record,
                   quantity="helmholtz_frequency_hz", inner_end_correction_m=.85 * PORT_RADIUS_M,
                   outer_end_correction_m=.61 * PORT_RADIUS_M,
                   chamber_pressure_real=pressure.real.tolist(), chamber_pressure_imag=pressure.imag.tolist(), **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Helmholtz: check neck resolution, end geometry and lumped assumptions"


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
    write_evidence("tube", frequencies, computed, reference, tolerance, record,
                   quantity="complex_input_impedance_pa_s_m3", first_cross_mode_hz=C / (2 * .010), **details)
    assert computed == pytest.approx(reference, rel=tolerance), "Tube impedance: check termination kind, phase, orientation and mesh dispersion"
