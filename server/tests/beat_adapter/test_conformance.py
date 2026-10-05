"""Fake wire solves exercise recording, phase gates and anti-vacuity checks."""

from __future__ import annotations

import base64
from dataclasses import replace
import json

import numpy as np
import pytest

from scripts.beat_conformance.__main__ import main
from scripts.beat_conformance.analytic import (AIR_DENSITY, CONTROL_MIN_PHASE_ERROR_DEG,
                                              SPHERE_RADIUS_M, icosphere, score_sphere,
                                              sphere_reference_pressure)
from scripts.beat_conformance.cases import all_cases
from scripts.beat_conformance.recorder import RuntimeEvidence, SolveEvidence, run_case, run_cases
from server.solver.beat_adapter.results import acceleration_scale, map_sweep


@pytest.fixture
def fake_solve(compiled_result):
    def solve(request):
        options = request.wire["solver_options"]
        frequencies = request.wire["frequencies_hz"]
        events = []
        for frequency in frequencies:
            raw = compiled_result(request, frequency=frequency)
            if options["precision"] == "float64":
                # Exact unit-acceleration analytic field encoded on the
                # official velocity basis; exercises WG's decoder and scale.
                pressure = sphere_reference_pressure() / acceleration_scale(frequency)
                for quantity in raw["quantities"]:
                    if quantity["quantity"] == "exterior_pressure":
                        shape = quantity["values"]["shape"]
                        array = np.full(shape, pressure, dtype="<c16")
                        quantity["values"]["content_base64"] = base64.b64encode(array.tobytes()).decode()
            events.append({"type": "result", "result": raw})
        events.append({"type": "completed", "solved_count": len(frequencies)})
        result = map_sweep(events, frequencies, layout=request.layout,
                           source_area_m2=request.channel_loading["source"].area_m2,
                           excitation_port_id="source", boundary_loading=request.channel_loading["source"],
                           precision=options["precision"], backend=options["bem_backend"])
        runtime = RuntimeEvidence(options["bem_backend"], options["precision"],
                                  "/synthetic/julia", "synthetic Julia 1.12.7", "/synthetic/beat_engine",
                                  "synthetic-revision", device_name="synthetic CPU", real_solves=True)
        return SolveEvidence(result, runtime)
    return solve


def test_mesh_and_closed_form_independent_controls():
    vertices, faces = icosphere(SPHERE_RADIUS_M, 3)
    assert faces.shape == (1280, 3)
    np.testing.assert_allclose(np.linalg.norm(vertices, axis=1), SPHERE_RADIUS_M)
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    assert np.all(np.sum(np.cross(b - a, c - a) * (a + b + c), axis=1) > 0)
    edges = {(int(a), int(b)) for face in faces for a, b in zip(face, np.roll(face, -1))}
    assert all((b, a) in edges for a, b in edges)
    area = .5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    assert .99 < area / (4 * np.pi * SPHERE_RADIUS_M**2) < 1
    limit = sphere_reference_pressure(1e-6)
    assert limit.real == pytest.approx(AIR_DENSITY * SPHERE_RADIUS_M**2 / 3., rel=1e-6)
    assert abs(limit.imag) < 1e-9
    assert np.angle(sphere_reference_pressure(distance_m=3.01) / sphere_reference_pressure()) > 0


@pytest.mark.parametrize("transform,passes", [(lambda p: p, True), (np.conj, False), (lambda p: -p, False)])
def test_analytic_gate_catches_level_invisible_conjugation_and_sign(transform, passes):
    exact = np.full((1, 2, 5), sphere_reference_pressure())
    scored = score_sphere(transform(exact))
    assert scored["passed"] is passes
    assert scored["against_closed_form"]["level_db"] == pytest.approx(0, abs=1e-12)
    if passes:
        assert all(control["phase_deg"] > CONTROL_MIN_PHASE_ERROR_DEG for control in scored["controls"].values())
    else:
        assert scored["against_closed_form"]["phase_deg"] > CONTROL_MIN_PHASE_ERROR_DEG


def test_real_evidence_contract_completes_all_cases_and_records_results(tmp_path, fake_solve):
    # Synthetic attestations test the mechanism; this is not installed evidence.
    summary = run_cases(all_cases(), output_dir=tmp_path, solve=fake_solve, evidence_mode="real")
    assert summary["passed"]
    assert summary["real_solved_count"] == 3
    assert not summary["installed_qualified"]
    sphere = json.loads((tmp_path / "analytic_pulsating_sphere_phase.json").read_text())
    assert sphere["mesh_sha256"] and sphere["request_sha256"]
    assert sphere["provenance"]["wg_policy_sha256"]
    assert sphere["provenance"]["captured_at_utc"]
    assert sphere["request"]["solver_options"]["precision"] == "float64"
    assert sphere["comparison"]["ran"] is False and sphere["comparison"]["limitation"]
    assert sphere["result"]["pressure_complex"][0][0][0] == pytest.approx(
        [sphere_reference_pressure().real, sphere_reference_pressure().imag])


def test_synthetic_and_static_only_runs_cannot_qualify(tmp_path, fake_solve):
    synthetic = run_cases(all_cases(), output_dir=tmp_path / "synthetic", solve=fake_solve)
    assert not synthetic["passed"] and synthetic["real_solved_count"] == 0
    assert all(record["status"] == "passed" for record in synthetic["records"])
    static = run_cases(all_cases()[:1], output_dir=tmp_path / "static", evidence_mode="real")
    assert not static["passed"]
    with pytest.raises(ValueError, match="nonempty"):
        run_cases([], output_dir=tmp_path)


@pytest.mark.parametrize("missing", ["Julia", "beat-engine"])
def test_missing_runtime_or_engine_is_recorded_failure_never_skip(tmp_path, missing):
    def fail(request):
        raise ModuleNotFoundError(f"Missing required {missing}")
    summary = run_cases(all_cases(), output_dir=tmp_path, solve=fail, evidence_mode="real")
    assert not summary["passed"]
    assert summary["real_solved_count"] == 0
    for record in summary["records"][1:]:
        assert record["status"] == "failed" and missing in record["error"]


@pytest.mark.parametrize("field,value", [
    ("real_solves", False), ("real_solves", "false"), ("backend", "metal"), ("precision", "float64"),
    ("julia_executable", ""), ("julia_version", ""), ("engine_path", ""),
    ("engine_revision", ""), ("engine_distribution", "hornlab-beat-bem"),
    ("device_class", "gpu"), ("device_name", "")])
def test_each_required_runtime_assertion_bites(tmp_path, fake_solve, field, value):
    def wrong(request):
        evidence = fake_solve(request)
        return replace(evidence, runtime=replace(evidence.runtime, **{field: value}))
    with pytest.raises(ValueError):
        run_case(all_cases()[1], output_dir=tmp_path, solve=wrong, evidence_mode="real")
    record = json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())
    assert record["status"] == "failed" and record["qualification_failures"]


@pytest.mark.parametrize("mutation", ["zero_solves", "cancelled", "reordered", "missing_diagnostics", "wrong_diagnostics", "unconverged"])
def test_solve_counts_completion_and_per_row_assertions(tmp_path, fake_solve, mutation):
    def wrong(request):
        evidence = fake_solve(request)
        result = evidence.result
        if mutation == "zero_solves":
            result.frequencies_hz = np.array([])
        elif mutation == "cancelled":
            result.cancelled = True
        elif mutation == "reordered":
            result.frequencies_hz = result.frequencies_hz[::-1]
        elif mutation == "missing_diagnostics":
            result.solver_log = []
        elif mutation == "unconverged":
            result.solver_log[0]["converged"] = False
        else:
            result.solver_log[0]["native_diagnostics"]["precision"] = "float64"
        return evidence
    with pytest.raises(ValueError):
        run_case(all_cases()[1], output_dir=tmp_path, solve=wrong, evidence_mode="real")
    assert json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())["status"] == "failed"


def test_metal_requires_real_kernel_evidence(tmp_path, fake_solve):
    case = replace(all_cases()[1], backend="metal", make_request=lambda: replace_request_metal())
    original = all_cases()[1].make_request

    def replace_request_metal():
        request = original()
        request.wire["solver_options"]["bem_backend"] = "metal"
        return request

    def metal(request):
        evidence = fake_solve(request)
        return replace(evidence, runtime=replace(evidence.runtime, device_class="gpu", device_name="Synthetic Metal"))
    with pytest.raises(ValueError, match="verified dispatched"):
        run_case(case, output_dir=tmp_path, solve=metal, evidence_mode="real")


def test_failed_acceptance_and_comparison_keep_scored_metrics(tmp_path, fake_solve):
    def comparison(result, entry):
        entry["metrics"] = {"phase_deg": 80.}
        raise AssertionError("comparator disagreement")
    with pytest.raises(AssertionError, match="comparator"):
        run_case(all_cases()[2], output_dir=tmp_path, solve=fake_solve, comparator=comparison)
    written = json.loads((tmp_path / "analytic_pulsating_sphere_phase.json").read_text())
    assert written["metrics"]["against_closed_form"]["phase_deg"] < 1e-9
    assert written["comparison"]["metrics"]["phase_deg"] == 80
    assert written["status"] == "failed"
    case = replace(all_cases()[2], accept=lambda result: score_sphere(np.conj(result.pressure_complex)))
    with pytest.raises(AssertionError, match="phase"):
        run_case(case, output_dir=tmp_path, solve=fake_solve)
    written = json.loads((tmp_path / "analytic_pulsating_sphere_phase.json").read_text())
    assert written["metrics"]["against_closed_form"]["phase_deg"] > 30


def test_skip_style_exception_is_saved_as_failure(tmp_path):
    def fail(request):
        pytest.skip("runtime gone")
    with pytest.raises(RuntimeError, match="runtime gone"):
        run_case(all_cases()[1], output_dir=tmp_path, solve=fail)
    assert json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())["status"] == "failed"
    summary = run_cases(all_cases(), output_dir=tmp_path, solve=fail, evidence_mode="real")
    assert not summary["passed"]
    assert all(record["status"] == "failed" for record in summary["records"][1:])


def test_cli_no_runner_or_unimportable_runner_fails_with_records(tmp_path):
    assert main(["--output-dir", str(tmp_path / "none"), "--evidence-mode", "real"]) == 1
    assert main(["--output-dir", str(tmp_path / "missing"), "--solve", "missing_engine_package:solve",
                 "--evidence-mode", "real"]) == 1
    written = json.loads((tmp_path / "missing" / "summary.json").read_text())
    assert written["records"][1]["status"] == "failed"
    assert "ModuleNotFoundError" in written["records"][1]["error"]


def test_case_list_cannot_lower_floors_or_duplicate_names(tmp_path):
    with pytest.raises(ValueError, match="floor"):
        replace(all_cases()[1], min_solved_count=0)
    with pytest.raises(ValueError, match="unique"):
        run_cases([all_cases()[0], all_cases()[0]], output_dir=tmp_path)
