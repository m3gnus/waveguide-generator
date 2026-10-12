"""Non-live checks of F11 formulas, measurement, evidence and real ingest fixtures."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from server.mesh.artifact import read_verified_fem_region_mesh
from server.mesh.fem_regions import read_fem_region_domain
from server.tests.fem_validation import (
    C, RHO, CHAMBER_VOLUME_M3, PORT_LENGTH_M, PORT_RADIUS_M,
    comparison_evidence, helmholtz_frequency, monopole_pressure, resonance_from_compliance,
    resonance_from_admittance, sealed_compliance_pressure, stiffness_from_chamber_pressure,
    surface_average, surface_centroid, tube_input_impedance, tube_pressure_ratio,
    validation_bundle, validation_request, ingest_validation_bundle, write_evidence,
)
from server.tests.test_official_beat_bridge import runtime as runtime_fixture

runtime = runtime_fixture


def test_sealed_reference_uses_displacement_and_si_volume():
    assert sealed_compliance_pressure(2., 10., .5, .01, .1) == pytest.approx(10.)
    # Independently: bulk modulus times the fractional volume displacement.
    assert sealed_compliance_pressure(RHO, C, .0012, 1e-6, 24e-6) == pytest.approx(7.1177645)


def test_monopole_reference_has_free_space_spherical_spreading():
    assert monopole_pressure(2., 10., .3, 5.) == pytest.approx(.6)
    assert monopole_pressure(2., 10., -.3j, 10.) == pytest.approx(.3)


def test_helmholtz_reference_includes_both_end_corrections():
    assert helmholtz_frequency(2 * math.pi, 4., 2., 1., .25, .75) == pytest.approx(1.)
    corrected = helmholtz_frequency(C, math.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3,
                                    PORT_LENGTH_M, .85 * PORT_RADIUS_M, .61 * PORT_RADIUS_M)
    bare = helmholtz_frequency(C, math.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3, PORT_LENGTH_M, 0., 0.)
    assert corrected / bare == pytest.approx(math.sqrt(.030 / .03292))
    assert 130 < corrected < 140


@pytest.mark.parametrize("frequency,length", [(100., .1), (500., .1), (1000., .1), (200., .4)])
def test_matched_tube_reference_is_real_characteristic_impedance(frequency, length):
    assert tube_input_impedance(RHO, C, .0001, length, frequency) == pytest.approx(4150300. + 0j)


def test_tube_reference_general_load_and_phase():
    # Zero length gives the load; pressure release at kL=pi/4 gives -i Zc.
    assert tube_input_impedance(2., 10., .5, 0., 1., 12 + 3j) == pytest.approx(12 + 3j)
    assert tube_input_impedance(2., 10., .5, 1.25, 1., 0.) == pytest.approx(-40j)


def test_matched_tube_propagation_sign_follows_input_transfer_matrix():
    # At kL=pi/2: p(0)=-i Zc Q(L), p(L)=Zc Q(L), so p(L)/p(0)=+i.
    assert tube_pressure_ratio(10., 2.5, 1.) == pytest.approx(1j)
    ratio = tube_pressure_ratio(C, .100, 1000.)
    assert abs(ratio) == pytest.approx(1.)
    assert np.angle(ratio) == pytest.approx(1.831832451)


@pytest.mark.parametrize("fraction", [.30, .42, .58, .70, .85, .95, 1.05, 1.15])
def test_rigid_tube_reference_is_infinite_general_load_limit(fraction):
    frequency = C / .2 * fraction
    zc = RHO * C / .010**2
    rigid = tube_input_impedance(RHO, C, .010**2, .100, frequency, math.inf)
    finite = tube_input_impedance(RHO, C, .010**2, .100, frequency, zc * 1e12)
    assert rigid == pytest.approx(finite, rel=1e-10)
    assert rigid == pytest.approx(1j * zc / math.tan(math.pi * fraction))


def test_rigid_tube_reference_zero_and_half_wave_pressure_pole():
    assert tube_input_impedance(2., 10., .5, 2.5, 1., math.inf) == pytest.approx(0j, abs=1e-12)
    # kL=pi/4 independently yields +i Zc for the rigid end.
    assert tube_input_impedance(2., 10., .5, 1.25, 1., math.inf) == pytest.approx(40j)
    before = tube_input_impedance(2., 10., .5, 5., .99, math.inf)
    after = tube_input_impedance(2., 10., .5, 5., 1.01, math.inf)
    assert before.imag < -1000 and after.imag > 1000


def test_helmholtz_crossing_interpolates_inverse_frequency_squared():
    frequencies = np.array([90., 97., 103., 110.])
    # Deliberately place tuning off-grid. Finite damping broadens the pole;
    # inverse compliance's real zero remains at fH.
    f_h = 99.25
    x = -1 / (2 * math.pi * frequencies)**2
    p = 20 * x / (1 - f_h**2 / frequencies**2 + .003j)
    assert resonance_from_compliance(frequencies, p) == pytest.approx(f_h, rel=1e-12)


def test_chamber_stiffness_recovers_absolute_volume_compliance_with_outward_drive():
    frequencies = np.array([94., 106.])
    stiffness, area, tuning = 2.5e9, .0016, 99.25
    # Into-air swept volume under unit OUTWARD acceleration is +Sd/omega².
    pressure = stiffness * area / (2 * math.pi * frequencies)**2 / (
        1 - tuning**2 / frequencies**2 + .003j)
    assert stiffness_from_chamber_pressure(frequencies, pressure, area) == pytest.approx(stiffness, rel=1e-12)
    # Tuning is invariant to a drive/pressure scaling error; stiffness is not.
    assert resonance_from_compliance(frequencies, 1.02 * pressure) == pytest.approx(tuning)
    scaled = stiffness_from_chamber_pressure(frequencies, 1.02 * pressure, area)
    assert scaled == pytest.approx(1.02 * stiffness)
    assert not comparison_evidence(scaled, stiffness, .01)["passed"]
    assert not comparison_evidence(-stiffness, stiffness, .01)["passed"]
    assert comparison_evidence(1.0057 * tuning, tuning, .01)["passed"]
    assert not comparison_evidence(1.019 * tuning, tuning, .01)["passed"]


def test_closed_tube_resonance_interpolates_admittance_not_quarter_wave_pole():
    # Symmetric bracket: tan(kL) is odd about the half-wave pressure pole.
    frequencies = np.array([.95, 1.05]) * C / .2
    impedance = np.array([tube_input_impedance(RHO, C, .0001, .1, f, math.inf) for f in frequencies])
    assert resonance_from_admittance(frequencies, 1 / impedance) == pytest.approx(1715.)
    # Off-grid, non-symmetric bracket of an independent reactive admittance.
    assert resonance_from_admittance([1600., 1800.], [2 + .115j, 3 - .085j]) == pytest.approx(1715.)


@pytest.mark.parametrize("admittance", [[1 + 1j] * 4, [1j, -1j, 1j, -1j]])
def test_closed_tube_crossing_refuses_missing_or_ambiguous_resonance(admittance):
    with pytest.raises(ValueError, match="Expected one closed-tube"):
        resonance_from_admittance([1500., 1600., 1800., 1900.], admittance)


@pytest.mark.parametrize("pressure", [[1, 1, 1, 1], [1, -1, 1, -1]])
def test_helmholtz_crossing_refuses_missing_or_ambiguous_resonance(pressure):
    with pytest.raises(ValueError, match="Expected one"):
        resonance_from_compliance([90., 97., 103., 110.], pressure)


def test_pressure_surface_integral_weights_triangle_area_and_complex_phase():
    field = {"points_mm_0": np.array([[0., 0., 0.]]),
             "points_mm_1": np.array([[0., 0., 0.], [1000., 0., 0.], [0., 1000., 0.],
                                      [0., 0., 0.], [2000., 0., 0.], [0., 1000., 0.]]),
             "surface_names_1": np.array(["wg-fem-v1|region=air|group=cone|kind=moving"]),
             "surface_triangles_1_0": np.array([[0, 1, 2], [3, 4, 5]]),
             "pressure": np.array([[[99., 1 + 1j, 2 + 2j, 3 + 3j, 4 - 1j, 5 - 2j, 6 - 3j]]])}
    assert surface_average(field, 1, "cone")[0, 0] == pytest.approx(4 - 2j / 3)
    np.testing.assert_allclose(surface_centroid(field, 1, "cone"), [5 / 9, 1 / 3, 0.])


def test_open_monopole_uses_distances_from_offset_opening():
    centroid = np.array([.040, .015, .010])
    points = np.array([[0., 0., 2.], [2., 0., 0.]])
    distances = np.linalg.norm(points - centroid, axis=1)
    np.testing.assert_allclose(distances**2, [1.99**2 + .04**2 + .015**2, 1.96**2 + .015**2 + .01**2])
    reference = monopole_pressure(2., 10., .3, distances)
    np.testing.assert_allclose(reference, 3 / distances)
    assert reference[1] / 1.5 > 1.02  # The old origin-based reference consumes the new bound.


def test_evidence_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WG2_FEM_VALIDATION_DIR", raising=False)
    # No record access and no git invocation when the destination is absent.
    write_evidence("sealed", [100.], [1.], [1.], .002, {})


def test_evidence_includes_failed_comparison_and_complex_values(tmp_path, monkeypatch):
    monkeypatch.setenv("WG2_FEM_VALIDATION_DIR", str(tmp_path))
    record = {"fem_regions": [{"region_id": "air", "stats": {"nodes": 4, "tetrahedra": 1}}],
              "coupled_exterior": {"stats": {"open_edges": 0}}}
    write_evidence("tube", [100., 500.], np.array([1 + .01j, 1.1]), [1., 1.], .02, record)
    evidence = json.loads((tmp_path / "tube.json").read_text())
    assert evidence["computed"] == {"real": [1., 1.1], "imag": [.01, 0.]}
    assert evidence["relative_error"] == pytest.approx([.01, .1])
    assert evidence["frequencies_hz"] == [100., 500.]
    assert evidence["tolerance"] == .02 and not evidence["passed"]
    assert evidence["mesh_stats"]["regions"]["air"]["nodes"] == 4
    assert len(evidence["git_sha"]) == 40
    # A bracket failure must leave evidence even without a computed tuning.
    write_evidence("ported", [90., 110.], [], 100., .03, record, passed=False, failure="no crossing")
    assert not json.loads((tmp_path / "ported.json").read_text())["passed"]
    # A passing primary metric must not hide failure of a secondary check.
    for passed in (True, False):
        write_evidence("tube", [100.], 1., 1., .02, record,
                       checks={"propagation_phase": {"passed": passed, "tolerance_rad": .02}})
        assert json.loads((tmp_path / "tube.json").read_text())["passed"] is passed


def _synthetic_tube_field(inlet, outlet):
    return {"points_mm_0": np.array([[0., 0., 0.], [10., 0., 0.], [0., 10., 0.],
                                     [0., 0., 100.], [10., 0., 100.], [0., 10., 100.]]),
            "surface_names_0": np.array(["wg-fem-v1|group=cone|kind=moving",
                                          "wg-fem-v1|group=port|kind=plane-wave-termination"]),
            "surface_triangles_0_0": np.array([[0, 1, 2]]),
            "surface_triangles_0_1": np.array([[3, 4, 5]]),
            "pressure": np.concatenate((np.repeat(inlet[:, None], 3, axis=1),
                                        np.repeat(outlet[:, None], 3, axis=1)), axis=1)[:, None, :],
            "phasor_convention": np.array("exp(-i omega t)")}


@pytest.mark.parametrize("fault", [None, "flat", "conjugate", "magnitude", "phase"])
def test_tube_qualification_rejects_propagation_faults_even_with_matched_input(tmp_path, monkeypatch, fault):
    from server.solver.beat_adapter.results import acceleration_scale
    from server.tests import test_fem_validation_pipeline_live as live

    monkeypatch.setenv("WG2_FEM_VALIDATION_DIR", str(tmp_path))

    def pipeline(root, case, frequencies):
        assert case == "tube"
        flow = -.010**2 * np.array([acceleration_scale(f) for f in frequencies])
        inlet = RHO * C / .010**2 * flow  # Correct matched Zin in EVERY fault case.
        kl = 2 * math.pi * np.array(frequencies) * .100 / C
        ratio = np.cos(kl) + 1j * np.sin(kl)
        if fault == "flat":
            ratio = np.ones_like(ratio)
        elif fault == "conjugate":
            ratio = ratio.conj()
        elif fault == "magnitude":
            ratio *= 1.021
        elif fault == "phase":
            ratio *= np.exp(.021j)
        return {"fem_regions": []}, {}, _synthetic_tube_field(inlet, inlet * ratio), {}

    monkeypatch.setattr(live, "_pipeline", pipeline)
    if fault is None:
        live.test_f11_plane_wave_terminated_tube(None, tmp_path)
    else:
        with pytest.raises(AssertionError):
            live.test_f11_plane_wave_terminated_tube(None, tmp_path)
    evidence = json.loads((tmp_path / "tube.json").read_text())
    assert max(evidence["relative_error"]) < 1e-12
    assert evidence["passed"] is (fault is None)


@pytest.mark.parametrize("pressure_scale", [1., 1.025])
def test_closed_tube_qualification_checks_impedance_admittance_and_resonance(tmp_path, monkeypatch, pressure_scale):
    from server.solver.beat_adapter.results import acceleration_scale
    from server.tests import test_fem_validation_pipeline_live as live

    monkeypatch.setenv("WG2_FEM_VALIDATION_DIR", str(tmp_path))

    def pipeline(root, case, frequencies):
        assert case == "closed-tube" and len(frequencies) == 8
        flow = -.010**2 * np.array([acceleration_scale(f) for f in frequencies])
        kl = 2 * math.pi * np.array(frequencies) * .100 / C
        inlet = pressure_scale * 1j * RHO * C / .010**2 * np.cos(kl) / np.sin(kl) * flow
        return {"fem_regions": []}, {}, _synthetic_tube_field(inlet, inlet), {}

    monkeypatch.setattr(live, "_pipeline", pipeline)
    if pressure_scale == 1.:
        live.test_f11_rigid_end_tube_resonance(None, tmp_path)
    else:
        with pytest.raises(AssertionError):
            live.test_f11_rigid_end_tube_resonance(None, tmp_path)
    evidence = json.loads((tmp_path / "closed-tube.json").read_text())
    assert evidence["passed"] is (pressure_scale == 1.)
    assert evidence["checks"]["pressure_resonance_hz"]["computed"] == pytest.approx(1715.)
    assert evidence["checks"]["admittance_near_pressure_pole"]["passed"] is (pressure_scale == 1.)


@pytest.mark.parametrize("pressure_scale,tuning_scale", [(1., 1.), (1.02, 1.), (1., 1.019)])
def test_ported_qualification_rejects_pressure_scaling_and_pressure_release_tuning(tmp_path, monkeypatch,
                                                                                 pressure_scale, tuning_scale):
    from server.tests import test_fem_validation_pipeline_live as live

    monkeypatch.setenv("WG2_FEM_VALIDATION_DIR", str(tmp_path))

    def pipeline(root, case, frequencies):
        assert case == "ported"
        tuning = tuning_scale * helmholtz_frequency(C, math.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3,
                                                    PORT_LENGTH_M, .85 * PORT_RADIUS_M, .61 * PORT_RADIUS_M)
        frequencies = np.array(frequencies)
        pressure = pressure_scale * RHO * C**2 / CHAMBER_VOLUME_M3 * .040**2 / (
            (2 * math.pi * frequencies)**2 * (1 - tuning**2 / frequencies**2))
        return {"fem_regions": []}, {}, _synthetic_tube_field(pressure, pressure), {}

    monkeypatch.setattr(live, "_pipeline", pipeline)
    if (pressure_scale, tuning_scale) == (1., 1.):
        live.test_f11_ported_box_helmholtz(None, tmp_path)
    else:
        with pytest.raises(AssertionError):
            live.test_f11_ported_box_helmholtz(None, tmp_path)
    evidence = json.loads((tmp_path / "ported.json").read_text())
    assert evidence["passed"] is ((pressure_scale, tuning_scale) == (1., 1.))
    assert evidence["checks"]["bulk_stiffness_pa_per_m3"]["passed"] is (pressure_scale == 1.)


@pytest.mark.parametrize("case", ["sealed", "open", "ported", "tube", "closed-tube"])
def test_synthetic_validation_return_passes_real_ingest(tmp_path, monkeypatch, case):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    bundle, manifest = validation_bundle(tmp_path / "returns", case)
    record = ingest_validation_bundle(bundle, tmp_path, case)
    region, = record["fem_regions"]
    assert region["stats"]["tetrahedra"] > 0
    domain = read_fem_region_domain(read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
    assert len(domain.points_mm) == region["stats"]["nodes"]
    assert len(domain.tetrahedra) == region["stats"]["tetrahedra"]
    assert manifest["sources"] == []
    if case in {"open", "ported"}:
        assert record["coupled_exterior"]["stats"]["open_edges"] == 0
    else:
        assert not record.get("coupled_exterior")
    kinds = {group["kind"] for group in region["surface_groups"]}
    assert "moving" in kinds
    assert ("plane-wave-termination" in kinds) == (case == "tube")
    if case == "closed-tube":
        assert {g["group_id"]: g["kind"] for g in region["surface_groups"]} == {"cone": "moving", "port": "rigid"}
        assert region["mesh_size_mm"] == 2.5
    request = validation_request(record, manifest, [100.])
    assert request.options.engine == "beat-cpu"
    assert request.options.frequencies_hz == [100.]
    assert request.geometry.drive_channels == []
    assert request.geometry.ingest_id == record["ingest_id"]
    assert request.geometry.mesh.model_dump(mode="json") == record["mesh_sizes"]
    assert not request.geometry.exterior_only
    assert not [f for f in record["findings"] if f.get("blocking")]
    # Compile verified artifacts through the exact job builder without starting
    # a native worker. This checks interface pairing and the termination kind.
    from server.solver.beat_adapter.coupled_request import build_imported_coupled_request
    from server.solver.context import SolverContext
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")
    compiled = build_imported_coupled_request(record, request.geometry, context, frequencies_hz=[100.])
    assert compiled.wire["solver_options"]["precision"] == "float64"
    assert compiled.wire["solver_options"]["bem_backend"] == "cpu"
    assert ("plane_wave_tube_termination" in {
        b["kind"] for b in compiled.wire["compiled_system"]["boundaries"]}) == (case == "tube")
    if case in {"open", "ported"}:
        assert set(compiled.wire["compiled_system"]["interfaces"][0]["topology"]["normal_sign"]) == {1}
    if case == "ported":
        # Circular P1 geometry must not consume the acoustic tolerance by itself.
        assert region["stats"]["surface_groups"]["port"]["area_mm2"] == pytest.approx(math.pi * 2**2, rel=.025)


@pytest.mark.parametrize("case", ["sealed", "open", "ported", "tube", "closed-tube"])
def test_pipeline_harness_reads_real_job_envelope_and_sidecar_with_fake_worker(runtime, tmp_path, monkeypatch, case):
    """Check harness plumbing without invoking native acoustic qualification."""
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver.beat_adapter.results import acceleration_scale
    from server.tests.beat_runtime.fake_host_worker import coupled_result
    from server.tests.test_fem_validation_pipeline_live import _pipeline

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    registry = EngineRegistry(detector=lambda: [EngineInfo(
        "beat-cpu", True, "fake worker", "test", geometry_sources=("imported",),
        imported_features=("fem-regions",), symmetry_domains=("full",))])

    def mutate(raw):
        raw.clear()
        raw.update(coupled_result(runtime.requests[-1], 100.))

    runtime.mutate = mutate
    record, envelope, field, details = _pipeline(tmp_path, case, [100.], engine_registry=registry)
    assert record["ingest_id"] == envelope["metadata"]["ingest_id"]
    np.testing.assert_allclose(field["pressure"], (2 + 3j) * acceleration_scale(100.))
    assert details["job_id"] and details["job_elapsed_s"] > 0
    assert len(runtime.requests) == 1
    if case == "open":
        assert envelope["channels"]["drive-LF"]["metadata"]["observation_frame_basis"]["axis"] == [0., 0., 1.]
        np.testing.assert_allclose(surface_centroid(field, 0, "port"), [.040, .015, .010], atol=1e-12)
