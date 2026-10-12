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
    helmholtz_frequency, monopole_pressure, resonance_from_compliance,
    sealed_compliance_pressure, surface_average, tube_input_impedance,
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


def test_helmholtz_crossing_interpolates_inverse_frequency_squared():
    frequencies = np.array([90., 97., 103., 110.])
    # Deliberately place tuning off-grid. Finite damping broadens the pole;
    # inverse compliance's real zero remains at fH.
    f_h = 99.25
    x = -1 / (2 * math.pi * frequencies)**2
    p = 20 * x / (1 - f_h**2 / frequencies**2 + .003j)
    assert resonance_from_compliance(frequencies, p) == pytest.approx(f_h, rel=1e-12)


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


@pytest.mark.parametrize("case", ["sealed", "open", "ported", "tube"])
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


@pytest.mark.parametrize("case", ["sealed", "open", "ported", "tube"])
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
