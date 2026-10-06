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
from scripts.beat_conformance.cases import all_cases, score_exterior
from scripts.beat_conformance import recorder
from scripts.beat_conformance.recorder import EngineRun, RuntimeEvidence, SolveEvidence, run_case, run_cases
from server.solver.beat_adapter.results import ResultContractError, acceleration_scale, map_sweep


@pytest.fixture
def fake_solve(compiled_result):
    def solve(request):
        options = request.wire["solver_options"]
        frequencies = request.wire["frequencies_hz"]
        events = []
        for frequency in frequencies:
            raw = compiled_result(request, frequency=frequency)
            raw["diagnostics"]["engine_provenance"] = {
                "execution": {"backend": options["bem_backend"],
                              "device": "Independent test device"}}
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
        if options["bem_backend"] == "metal":
            runtime = replace(runtime, device_class="gpu", device_name="synthetic Metal", device_kernel_verified=True)
        solve.events = events
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


def test_real_evidence_contract_completes_all_cases_and_records_results(tmp_path, observed_solve):
    # Independent observations are faked at the recorder boundary; no Julia runs.
    summary = run_cases(all_cases(), output_dir=tmp_path, solve=observed_solve, evidence_mode="real", backend="metal")
    assert summary["passed"]
    assert summary["real_solved_count"] == 5
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
        return replace(evidence, runtime=replace(evidence.runtime, device_class="gpu", device_name="Synthetic Metal",
                                                          device_kernel_verified=False))
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


@pytest.fixture
def observed_solve(monkeypatch, fake_solve):
    state = {"terminated": 0}
    def verify(executable, backend):
        assert executable == "/synthetic/julia"
        return {"backend": backend, "julia_executable": executable, "julia_version": "julia version 1.12.7",
                "engine_path": "/synthetic/beat_engine", "engine_revision": "a" * 40,
                "engine_distribution": "beat-engine", "engine_fingerprint": "fake-hash",
                "artifact_kind": "source", "device_class": "cpu" if backend == "cpu" else "gpu",
                "device_name": "Independent test device", "device_kernel_verified": backend == "metal",
                "project": "/synthetic/project", "solver_script": "/synthetic/solver.jl"}
    class Worker:
        def submit(self, wire):
            return iter(state["events"])
        def terminate(self):
            state["terminated"] += 1
    monkeypatch.setattr(recorder, "verify_runtime", verify)
    monkeypatch.setattr(recorder, "_engine_worker", lambda **options: Worker())
    def select(request):
        fake_solve(request)
        state["events"] = fake_solve.events
        return EngineRun("/synthetic/julia", 1)
    select.state = state
    return select


def test_subset_selection_cannot_qualify_and_names_missing_cases(tmp_path, observed_solve, monkeypatch, capsys):
    case = all_cases()[1]
    # All selected numerical checks succeed; the old summary qualified this subset.
    summary = run_cases([case], output_dir=tmp_path / "api", solve=observed_solve, evidence_mode="real")
    assert summary["records"][0]["qualified"]
    assert summary["real_solved_count"] > 0 and all(record["status"] == "passed" for record in summary["records"])
    # Those were the old summary's complete qualification conditions.
    assert not summary["qualified"] and not summary["passed"]
    assert summary["missing_required_cases"] == ["analytic_pulsating_sphere_phase", "capability_refusals"]
    import types
    module = types.SimpleNamespace(solve=observed_solve)
    monkeypatch.setattr("scripts.beat_conformance.__main__.importlib.import_module", lambda name: module)
    assert main(["--case", case.name, "--evidence-mode", "real", "--output-dir", str(tmp_path / "cli"),
                 "--solve", "fake:solve"]) == 1
    output = capsys.readouterr().out
    assert "qualified=False" in output and "capability_refusals" in output


def test_directivity_reference_normalization_rejects_constant_offset(fake_solve):
    result = fake_solve(all_cases()[1].make_request()).result
    assert score_exterior(result)["passed"]
    # Offset spread is unchanged, so the old criterion passed a +3 dB defect.
    from unittest.mock import patch
    with patch.object(type(result), "directivity_db", new=property(lambda self: self.spl_db - self.spl_db[:, :, :1] + 3)):
        assert np.max(np.ptp(result.spl_db - result.directivity_db, axis=2)) < 1e-9
        assert not score_exterior(result)["passed"]


def test_self_attested_runtime_cannot_qualify_even_when_all_claims_are_present(tmp_path, fake_solve):
    with pytest.raises(ValueError, match="attested"):
        run_case(all_cases()[1], output_dir=tmp_path, solve=fake_solve, evidence_mode="real")
    record = json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())
    assert record["real_solved_count"] == 0
    assert record["runtime"]["real_solves"] is True
    assert record["observations"]["device_name"]["status"] == "attested"


@pytest.mark.parametrize("mutation", ["missing_terminal", "zero_terminal_count", "false_terminal_count"])
def test_terminal_events_count_real_solves_instead_of_result_length(tmp_path, observed_solve, mutation):
    def select(request):
        selection = observed_solve(request)
        events = observed_solve.state["events"]
        if mutation == "missing_terminal":
            events.pop()
        else:
            events[-1]["solved_count"] = 0 if mutation == "zero_terminal_count" else 900
        return selection
    with pytest.raises(ResultContractError):
        run_case(all_cases()[1], output_dir=tmp_path, solve=select, evidence_mode="real")
    record = json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())
    assert not record["qualified"] and record["real_solved_count"] == 0
    assert observed_solve.state["terminated"] == 1


def test_metal_case_is_required_for_declared_metal_backend(tmp_path, observed_solve):
    cpu_cases = all_cases()[:3]
    assert run_cases(cpu_cases, output_dir=tmp_path / "cpu", solve=observed_solve, evidence_mode="real")["passed"]
    metal = run_cases(cpu_cases, output_dir=tmp_path / "metal", solve=observed_solve,
                      evidence_mode="real", backend="metal")
    assert not metal["qualified"] and metal["missing_required_cases"] == ["metal_exterior_float32"]
    case = all_cases()[3]
    assert case.backend == "metal" and case.precision == "float32"
    assert case.make_request().wire["solver_options"]["bem_backend"] == "metal"
    record = run_case(case, output_dir=tmp_path / "case", solve=observed_solve, evidence_mode="real")
    assert record["qualified"] and record["real_solved_count"] == 2


def test_static_capability_refusals_fail_when_declared_refusal_becomes_supported(tmp_path, monkeypatch):
    from server.solver.beat_adapter import capabilities
    original = capabilities.request.build_request
    def changed(*args, **kwargs):
        if kwargs.get("symmetry") == "xy":
            kwargs["symmetry"] = "full"
        return original(*args, **kwargs)
    monkeypatch.setattr(capabilities.request, "build_request", changed)
    with pytest.raises(AssertionError, match="Static"):
        run_case(all_cases()[0], output_dir=tmp_path)
    record = json.loads((tmp_path / "capability_refusals.json").read_text())
    assert record["metrics"]["scenarios"]["symmetry.xy"]["supported"]
    assert not record["metrics"]["passed"]


@pytest.mark.parametrize("shape", [(1,), (1, 2, 4), (10,), (2, 1, 5)])
def test_sphere_sample_shape_prevents_single_sample_qualification(shape):
    with pytest.raises(ValueError, match="shape"):
        score_sphere(np.full(shape, sphere_reference_pressure()))


def test_provenance_hashes_environment_values_without_paths_or_secrets(monkeypatch):
    monkeypatch.setenv("BLAB_API_TOKEN", "secret-token-unique")
    monkeypatch.setenv("JULIA_DEPOT_PATH", "/private/user/depot")
    facts = recorder.provenance()
    encoded = json.dumps(facts)
    assert "secret-token-unique" not in encoded and "/private/user/depot" not in encoded
    assert "BLAB_API_TOKEN" in facts["environment_overrides"]
    import hashlib
    assert facts["environment_overrides"]["JULIA_DEPOT_PATH"]["value_sha256"] == hashlib.sha256(
        b"/private/user/depot").hexdigest()


def test_required_case_declaration_cannot_be_replaced_with_smaller_request(tmp_path, observed_solve):
    from server.solver.beat_adapter.capabilities import probe_request
    cases = list(all_cases()[:3])
    cases[1] = replace(cases[1], frequencies_hz=(300.,), min_solved_count=1,
                       make_request=lambda: probe_request(frequencies_hz=[300.]),
                       accept=lambda result: {"passed": True, "failures": []})
    summary = run_cases(cases, output_dir=tmp_path, solve=observed_solve, evidence_mode="real")
    assert all(record["status"] == "passed" for record in summary["records"])
    assert not summary["qualified"] and summary["qualification_failures"]


def test_canonical_record_hash_survives_json_roundtrip_and_detects_mutation(tmp_path, fake_solve):
    record = run_case(all_cases()[1], output_dir=tmp_path, solve=fake_solve)
    saved = json.loads((tmp_path / "exterior_contract_two_frequencies.json").read_text())
    assert record["record_sha256"] == recorder.record_sha256(record) == recorder.record_sha256(saved)
    saved["mesh_sha256"] = "changed"
    assert recorder.record_sha256(saved) != record["record_sha256"]


def test_installed_revision_metadata_is_explicitly_attested_while_solves_are_observed(tmp_path, observed_solve, monkeypatch):
    original = recorder.verify_runtime
    def installed(executable, backend):
        return {**original(executable, backend), "artifact_kind": "installed", "engine_revision_status": "attested"}
    monkeypatch.setattr(recorder, "verify_runtime", installed)
    summary = run_cases(all_cases()[:3], output_dir=tmp_path, solve=observed_solve, evidence_mode="real")
    assert summary["installed_qualified"]
    observation = summary["records"][1]["observations"]
    assert observation["engine_revision"]["status"] == "attested"
    for key in ("backend", "device_name", "solve_count"):
        assert observation[key]["status"] == "observed"
