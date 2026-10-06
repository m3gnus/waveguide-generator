"""Production corpus controls, without Julia or numerical engine solves."""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.beat_conformance import corpus, run_corpus as runner
from scripts.beat_conformance.agreement import ResultSet

# Existing tiny authoritative WG mesh; tests never mesh CAD or start an engine.
MESH = (corpus.ROOT / "server/solver/warmup_mesh.msh").read_bytes()


@pytest.fixture
def frozen():
    case = corpus.CASES["osse-full"]
    return corpus.FrozenCase(case, case.request().model_dump(mode="json"), MESH, None,
                             {"vertex_count": 20, "triangle_count": 36})


def native(frequencies, settings, *, gain=1.):
    f = np.asarray(frequencies)
    points = len(settings["observation_angles_deg"])
    planes = len(settings["observation_planes"])
    pressure = (1 + .0001 * f[:, None, None]) * np.ones((len(f), planes, points), complex) * gain
    theta = np.repeat(np.linspace(0, 180, 9), 16)
    phi = np.tile(np.arange(16) * 360 / 16, 9)
    return {"frequencies_hz": f, "pressure_complex": pressure,
            "impedance": (2 + .001j * f) * gain,
            "sphere_pressure_complex": np.ones((len(f), len(theta)), complex) * gain,
            "sphere_theta_deg": theta, "sphere_phi_deg": phi,
            "observation_angles_deg": np.asarray(settings["observation_angles_deg"]),
            "observation_planes": list(settings["observation_planes"]),
            "solver_log": [{"native_diagnostics": {"backend": settings["backend"], "blas_threads": 1,
                           "precision": settings["precision"]}} for _ in f],
            "surface_pressure_complex": None, "surface_neumann_complex": None}


def evidence(frozen, frequencies, settings, *, official, gain=1.):
    name = "beat-engine" if official else "hornlab-beat-bem"
    return {"native": {"source": native(frequencies, settings, gain=gain)}, "response": {},
            "official": official, "mesh_sha256": frozen.sha256, "frequencies_hz": list(frequencies),
            "identity": {"distributions": {name: {"revision": "official-revision" if official else "hbb-pin"}}}}


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_catalogue_uses_valid_wg_requests(case):
    request = case.request()
    assert request.options.frequencies_hz == list(case.coarse_hz)
    assert request.options.polar_config.spherical_sampling
    assert 3 <= len(case.coarse_hz) <= 25
    assert 0 < case.max_refine_count <= 81
    assert case.coarse_minutes[1] < 10 and case.refine_minutes[1] < 12
    assert case.fixture and case.covers


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_freezes_deterministic_tiny_fixture_once_per_case(case, tmp_path):
    calls = []
    def mesher(design):
        calls.append(design)
        return {"msh_text": MESH.decode(), "stats": {"vertex_count": 20}}
    def cad_builder(case, directory):
        calls.append(case.name)
        return MESH, {"sources": [{"id": "a"}, {"id": "b"}]}, {"vertex_count": 20}
    kwargs = {"mesher": mesher, "cad_builder": cad_builder}
    first = corpus.freeze_case(case, tmp_path, **kwargs)
    second = corpus.freeze_case(case, tmp_path, **kwargs)
    assert first.mesh_bytes == second.mesh_bytes and first.sha256 == second.sha256
    assert len(calls) == 2


def test_corpus_mesh_budget_refuses_before_solves(tmp_path):
    with pytest.raises(ValueError, match="vertex CPU budget"):
        corpus.freeze_case(corpus.CASES["osse-full"], tmp_path,
                          mesher=lambda _: {"msh_text": MESH.decode(), "stats": {"vertex_count": 10000}})


@pytest.mark.parametrize("official", [False, True])
def test_corpus_production_function_fake_receives_frozen_inputs_and_controls(frozen, official):
    calls = []
    def parametric(text, context, **kwargs):
        calls.append((text, context, kwargs))
        kwargs["_native_result_callback"]("source", SimpleNamespace(pressure_complex=np.ones((3, 1, 2))))
        return {"frequencies": list(context.frequencies_hz), "metadata": {"engine": "beat-engine" if official else "hornlab-beat-bem"}}
    result = runner.production_run(frozen, (500., 750., 1000.), official=official, backend="cpu",
                                   precision="float64", julia="julia", routes=(parametric, lambda *a, **k: pytest.fail("wrong route")))
    text, context, kwargs = calls[0]
    assert text.encode() == frozen.mesh_bytes
    assert context.frequencies_hz == (500., 750., 1000.)
    assert kwargs["_official"] is official and kwargs["_hbb_options"]["persistent_worker"] is False
    assert kwargs["_hbb_options"]["julia_threads"] == 1
    assert result["native"]["source"]["pressure_complex"].shape == (3, 1, 2)


@pytest.mark.parametrize("official", [False, True])
def test_corpus_imported_production_fake_independent_channels(frozen, official):
    case = corpus.CASES["imported-two-sources"]
    record = {"sources": [{"id": "a"}, {"id": "b"}]}
    frozen = replace(frozen, case=case, request=case.request(record=record).model_dump(mode="json"), record=record)
    def imported(text, request, found_record, **kwargs):
        assert text.encode() == frozen.mesh_bytes and found_record == record
        for channel in request.geometry.drive_channels:
            kwargs["_native_result_callback"](channel.id, SimpleNamespace(pressure_complex=np.ones((3, 1, 2))))
        assert kwargs["_official"] is official
        return {"result_kind": "multi_channel", "channel_order": [c.id for c in request.geometry.drive_channels]}
    result = runner.production_run(frozen, (500., 750., 1000.), official=official, backend="cpu",
                                   precision="float32", julia="julia", routes=(lambda *a, **k: pytest.fail("wrong route"), imported))
    assert list(result["native"]) == ["drive-a", "drive-b"]


def test_corpus_refine_topographic_rule_dyadic_steps_and_parts():
    f = np.arange(500., 1751., 50.)
    levels = np.zeros(len(f))
    levels[5:10] = [0, 6, 12, 6, 0]
    levels[15:20] = [0, -6, -12, -6, 0]
    pressure = 10 ** (levels / 20)
    result = ResultSet(MESH, f, pressure[:, None], np.ones(len(f)) / f,
                       np.ones(len(f)), np.ones(len(f)), {}, "hbb")
    case = replace(corpus.CASES["osse-quarter"], max_refine_count=11)
    plan = runner.refine_windows({"a": result}, case)
    assert len(plan["windows"]) == 2
    assert len(plan["parts"]) > 1
    assert all(3 <= len(p["frequencies_hz"]) <= 11 for p in plan["parts"])
    for window in plan["windows"]:
        assert all(window["step_hz"] / feat["frequency_hz"] < .005 for feat in window["features"])
        assert np.all(np.diff(window["frequencies_hz"]) == window["step_hz"])
        assert np.array_equal(np.asarray(window["frequencies_hz"]), np.asarray(window["frequencies_hz"], dtype=np.float32))


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_corpus_output_dir_refusal(tmp_path, kind):
    path = tmp_path / "used"
    if kind == "file":
        path.write_text("evidence")
    else:
        path.mkdir()
        (path / "evidence").write_text("retain")
    with pytest.raises(ValueError, match="empty or absent"):
        runner.empty_output(path)


def test_corpus_identity_capture_is_fake_and_includes_load_thread_env(monkeypatch):
    commands = []
    def command(command):
        commands.append(command)
        return {"returncode": 0, "stdout": "fake", "stderr": ""}
    monkeypatch.setattr(runner, "command_evidence", command)
    monkeypatch.setattr(runner, "distribution_identity", lambda name: {"name": name, "direct_url": {"url": "git+https://example.invalid/engine"}})
    monkeypatch.setenv("JULIA_NUM_THREADS", "1")
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", "/temporary/official-runtime")
    identity = runner.capture_identity("julia")
    assert identity["hbb_pin"] == runner.hbb_pin()
    assert identity["thread_env"]["JULIA_NUM_THREADS"] == "1"
    assert identity["runtime_env"]["WG2_BEAT_RUNTIME_DIR"] == "/temporary/official-runtime"
    assert ["sysctl", "-n", "vm.loadavg"] in commands and ["pmset", "-g", "batt"] in commands
    assert ["julia", "--startup-file=no", "--version"] in commands


def test_corpus_complex_json_roundtrip(tmp_path):
    path = tmp_path / "raw.json"
    values = {"pressure": np.array([[1+2j, 3-4j]]), "artifact": b"NPZ", "empty": None}
    runner.write_json(path, values)
    decoded = runner.read_json(path)
    np.testing.assert_array_equal(decoded["pressure"], values["pressure"])
    assert decoded["artifact"] == b"NPZ"
    json.loads(path.read_text())


@pytest.mark.parametrize("gain,passed", [(1., True), (1.1, False)])
def test_corpus_verdict_scoring_passthrough(frozen, gain, passed):
    settings = runner.settings_for(frozen, "cpu", "float32")
    reference = evidence(frozen, frozen.case.coarse_hz, settings, official=False)
    candidate = evidence(frozen, frozen.case.coarse_hz, settings, official=True, gain=gain)
    report = runner.score_pair(reference, candidate, frozen, settings, frequency_step=250)
    assert report["passed"] is passed
    assert "pressure" in report["channels"]["source"]["metrics"]
    assert report["channels"]["source"]["thresholds"]["pressure"]["relative_l2"] == 1e-4
    assert "surface_pressure_complex" in report["unavailable"]["hbb"]["source"]


def test_corpus_missing_sphere_never_fabricated(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    run = evidence(frozen, frozen.case.coarse_hz, settings, official=False)
    run["native"]["source"]["sphere_pressure_complex"] = None
    mapped, unavailable = runner.map_results(run, frozen, settings, official=False)
    assert not mapped and "sphere_pressure_complex" in unavailable["source"]


def test_corpus_unsupported_recording(tmp_path):
    case = replace(corpus.CASES["osse-quarter"], unsupported="exact route refusal")
    report = runner.run_case(case, tmp_path / "run", backend="cpu", precision="float32", julia="julia",
                             freezer=lambda *a, **k: pytest.fail("unsupported case meshed"))
    assert report["status"] == "unsupported" and report["refusal"] == "exact route refusal"
    assert runner.read_json(tmp_path / "run/verdict.json") == report


def test_corpus_run_uses_one_mesh_hbb_then_official_and_final_verdict(frozen, tmp_path):
    settings = runner.settings_for(frozen, "cpu", "float32")
    calls = []
    def pair(frozen, frequencies, directory, **kwargs):
        assert not (directory.parent / "verdict.json").exists()
        calls.append((frozen.sha256, frequencies))
        directory.mkdir()
        runs = [evidence(frozen, frequencies, settings, official=value) for value in (False, True)]
        for name, run in zip(("hbb", "official"), runs):
            runner.write_json(directory / f"{name}.json", run)
        return tuple(runs)
    report = runner.run_case(frozen.case, tmp_path / "run", backend="cpu", precision="float32", julia="julia",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert len(calls) == 1 and calls[0][0] == frozen.sha256
    assert report["passed"] and report["status"] == "complete" and report["qualified"] is False
    assert (tmp_path / "run/verdict.json").exists()


def test_corpus_refine_requires_separate_coarse_directory(tmp_path):
    with pytest.raises(ValueError, match="requires --coarse-dir"):
        runner.run_case(corpus.CASES["osse-full"], tmp_path / "run", backend="cpu", precision="float32",
                        julia="julia", phase="refine")


def test_corpus_merge_uses_real_rows_without_interpolation(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    a = evidence(frozen, (500., 1000.), settings, official=False)
    b = evidence(frozen, (750., 1000.), settings, official=False, gain=2.)
    merged = runner.merge_runs([a, b])
    assert merged["frequencies_hz"] == [500., 750., 1000.]
    assert merged["native"]["source"]["pressure_complex"][2, 0, 0] == b["native"]["source"]["pressure_complex"][1, 0, 0]


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_fast_production_meshing_is_deterministic(case, tmp_path):
    # All nine builders were measured < 2 s here. The separate tiny-fixture tests
    # retain catalogue/runner coverage on machines without the optional mesher.
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    import time
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    start = time.monotonic()
    first = corpus.freeze_case(case, first_dir)
    first_elapsed = time.monotonic() - start
    start = time.monotonic()
    second = corpus.freeze_case(case, second_dir)
    assert first_elapsed < 5 and time.monotonic() - start < 5
    assert first.mesh_bytes == second.mesh_bytes
    assert first.sha256 == second.sha256
    runner.settings_for(first, "cpu", "float32")
    if first.record:
        from server.solver.beat_imported import imported_beat_preflight
        from server.jobs.models import SolveRequest
        request = SolveRequest.model_validate(first.request)
        assert imported_beat_preflight(first.record, first.mesh_bytes.decode(), request.geometry.drive_channels) is None
    if case.name == "imported-tilted-rear":
        from server.solver.beat_adapter.mesh import read_surface
        mesh = read_surface(first.mesh_bytes.decode())
        points = mesh.points_m[mesh.faces[mesh.tags == first.record["source_tags"]["curved-rear"]]]
        vector = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]).sum(axis=0)
        assert vector[2] < 0 and vector[0] > 0


def test_corpus_isolation_launches_sequential_children_and_preserves_runtime_env(frozen, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "official-runtime"))
    monkeypatch.setenv("WG2_BEAT_WORKER_DIR", str(tmp_path / "official-workers"))
    class Process:
        pid = 12345
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            index = command.index("--engine-output")
            selected = bool(int(command[command.index("--official-child") + 1]))
            runner.write_json(Path(command[index + 1]), {"official": selected, "native": {}})
        def wait(self, *, timeout=None):
            assert timeout == 240
            return 0
    from pathlib import Path
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    reference, candidate = runner.isolated_pair(frozen, (500., 750., 1000.), tmp_path / "coarse",
                                               backend="cpu", precision="float32", julia="julia")
    assert len(calls) == 2
    for i, (command, options) in enumerate(calls):
        assert command[2] == "scripts.beat_conformance.run_corpus"
        assert command[command.index("--official-child") + 1] == str(i)
        assert options["start_new_session"]
        assert options["env"]["WG2_BEAT_RUNTIME_DIR"] == str(tmp_path / "official-runtime")
        assert options["env"]["WG2_BEAT_WORKER_DIR"] == str(tmp_path / "official-workers")
        assert all(options["env"][k] == "1" for k in runner.THREAD_ENV)
    assert reference is not candidate


def test_corpus_both_route_refusals_are_recorded(frozen, tmp_path):
    def refuse(*args, **kwargs):
        return ({"error": "BeatUnavailable: exact HBB refusal", "refusal": "exact HBB refusal"},
                {"error": "BeatUnavailable: exact official refusal", "refusal": "exact official refusal"})
    report = runner.run_case(frozen.case, tmp_path / "run", backend="cpu", precision="float32", julia="julia",
                             freezer=lambda *a, **k: frozen, pair_runner=refuse)
    assert report["status"] == "unsupported"
    assert report["refusals"] == {"hbb": "exact HBB refusal", "official": "exact official refusal"}


def test_corpus_refine_parts_remain_incomplete_until_complete_windows(frozen, tmp_path):
    frozen = replace(frozen, case=replace(frozen.case, max_refine_count=201))
    settings = runner.settings_for(frozen, "cpu", "float32")
    def pair(frozen, frequencies, directory, **kwargs):
        directory.mkdir()
        corpus.save_frozen(frozen, directory)
        runs = []
        for official, name in ((False, "hbb"), (True, "official")):
            run = evidence(frozen, frequencies, settings, official=official)
            levels = 1 + 5 * np.maximum(1 - np.abs(np.asarray(frequencies) - 1500) / 250, 0)
            run["native"]["source"]["pressure_complex"] = levels[:, None, None] * np.ones((len(frequencies), 3, 13), complex)
            runner.write_json(directory / f"{name}.json", run)
            runs.append(run)
        return tuple(runs)
    first = tmp_path / "first"
    report = runner.run_case(frozen.case, first, backend="cpu", precision="float32", julia="julia",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert report["status"] == "refine_incomplete" and not report["passed"]
    assert report["required_refine_parts"] == 2 and report["completed_refine_parts"] == [1]
    final = runner.run_case(frozen.case, tmp_path / "last", backend="cpu", precision="float32", julia="julia",
                            phase="refine", coarse_dir=first, refine_part=2, refine_dirs=(first,), pair_runner=pair)
    assert final["status"] == "complete" and final["passed"]
    assert final["completed_refine_parts"] == [1, 2]
    assert len(final["local_windows"]) == 1


def test_corpus_merge_refuses_mixed_engine_revisions(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, (500., 750., 1000.), settings, official=False) for _ in range(2)]
    runs[1]["identity"]["distributions"]["hornlab-beat-bem"]["revision"] = "another pin"
    with pytest.raises(ValueError, match="identity distributions"):
        runner.merge_runs(runs)


def test_corpus_trace_and_driver_missing_fields_cannot_pass(frozen):
    frozen = replace(frozen, case=replace(frozen.case, traces=True))
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, frozen.case.coarse_hz, settings, official=value) for value in (False, True)]
    assert not runner.score_pair(*runs, frozen, settings, frequency_step=250)["passed"]


def test_corpus_resonance_failure_prevents_extra_norm_scoring(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, frozen.case.coarse_hz, settings, official=value) for value in (False, True)]
    runs[0]["native"]["source"]["pressure_complex"][3] *= 10
    report = runner.score_pair(*runs, frozen, settings, frequency_step=250)
    assert not report["passed"]
    assert report["channels"]["source"]["extra_fields"] == {}
    assert report["channels"]["source"]["metrics"] == {}


@pytest.mark.parametrize("imported", [False, True])
def test_corpus_actual_hbb_production_hooks_preserve_controls(frozen, imported, monkeypatch, tmp_path):
    from server.tests import test_imported_beat as cad
    from server.solver import beat, beat_imported
    from server.platform import temp_session
    package = cad._RecordingBeat()
    status = {"cpu": {"available": True, "backend": "cpu", "surface_traces": False, "reason": "fake HBB"}}
    for module in (beat, beat_imported):
        monkeypatch.setattr(module, "_load_api", lambda: package)
        monkeypatch.setattr(module, "beat_backend_statuses", lambda: status)
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    if imported:
        request = cad._request()
        case = corpus.CASES["imported-two-sources"]
        frozen = corpus.FrozenCase(case, request.model_dump(mode="json"), cad.MESH.encode(), cad._record(), {})
    result = runner.production_run(frozen, (500., 750., 1000.), official=False, backend="cpu",
                                   precision="float64", julia="fake-julia")
    assert package.solves and result["native"]
    for solve in package.solves:
        config = solve["config"]
        assert config.solve_precision == "double" and config.julia_threads == 1
        assert config.julia_executable == "fake-julia" and config.persistent_worker is False
    metadata = result["response"]["metadata"]
    if imported:
        assert metadata["solver_engine"]["precision"] == "double"
        assert all(c["metadata"]["beat"]["precision"] == "double" for c in result["response"]["channels"].values())
    else:
        assert metadata["beat"]["precision"] == "double"


@pytest.mark.parametrize("imported", [False, True])
def test_corpus_actual_official_production_overrides(frozen, imported, monkeypatch):
    from server.tests import test_imported_beat as cad
    from server.jobs.models import SolveRequest
    from server.solver.context import SolverContext
    from server.solver import beat, beat_imported, official_beat
    statuses = {"cpu": {"available": True, "backend": "cpu", "surface_traces": False, "reason": "fake official"}}
    monkeypatch.setattr(official_beat, "production_statuses", lambda: statuses)
    marker = object()
    captures, wires = {}, []
    def solve(compiled, **kwargs):
        assert kwargs["worker_manager"] is marker and kwargs["julia_executable"] == "fake-julia"
        assert compiled.wire["solver_options"]["precision"] == "float64"
        wires.append(compiled.wire)
        frequencies = np.asarray(compiled.wire["frequencies_hz"])
        pressure = np.ones((len(frequencies), len(compiled.layout.planes), len(compiled.layout.angles_deg)), complex)
        return SimpleNamespace(frequencies_hz=frequencies, pressure_complex=pressure, impedance=np.ones(len(frequencies), complex),
                               directivity_db=np.zeros(pressure.shape), spl_db=np.zeros(pressure.shape),
                               observation_angles_deg=compiled.layout.angles_deg, observation_planes=compiled.layout.planes,
                               solver_log=[], timings={}, mesh_info=None, cancelled=False)
    monkeypatch.setattr(official_beat, "solve_compiled", solve)
    kwargs = {"backend": "cpu", "_official": True, "_precision": "float64", "_worker_manager": marker,
              "_julia_executable": "fake-julia", "_native_result_callback": lambda ch, raw: captures.update({ch: raw})}
    if imported:
        response = beat_imported.solve_imported_beat_from_msh_text(cad.MESH, cad._request(), cad._record(), **kwargs)
        assert response["metadata"]["solver_engine"]["precision"] == "double"
    else:
        request = SolveRequest.model_validate(frozen.request)
        response = beat.solve_beat_from_msh_text(frozen.mesh_bytes.decode(), SolverContext.from_request(request, solver_mode="full_3d"), **kwargs)
        assert response["metadata"]["beat"]["precision"] == "double"
    assert captures and wires


@pytest.mark.parametrize("electrical,gain,passed", [(False, 1., False), (True, 1., True), (True, 1.1, False)])
def test_corpus_electrical_driver_impedance_budget(frozen, electrical, gain, passed):
    frozen = replace(frozen, case=replace(frozen.case, name="driver-loading"))
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, frozen.case.coarse_hz, settings, official=value) for value in (False, True)]
    if electrical:
        for index, run in enumerate(runs):
            run["native"]["source"]["electrical_impedance_ohm"] = np.full(len(frozen.case.coarse_hz), (5+1j) * (gain if index else 1))
    report = runner.score_pair(*runs, frozen, settings, frequency_step=250)
    assert report["passed"] is passed
    if electrical:
        metric = report["channels"]["source"]["extra_fields"]["electrical_impedance_ohm"]
        assert metric["passed"] is passed
    else:
        assert "electrical_impedance_ohm" in report["unavailable"]["hbb"]["source"]


def test_corpus_captured_parametric_mesh_facts_roundtrip(frozen, tmp_path):
    settings = runner.settings_for(frozen, "cpu", "float32")
    run = evidence(frozen, frozen.case.coarse_hz, settings, official=False)
    run["native"]["source"]["mesh_info"] = {"n_vertices": settings["mesh"]["node_count"],
        "n_triangles": settings["mesh"]["triangle_count"], "physical_tag_areas_m2": settings["mesh"]["tag_areas_m2"]}
    runner.write_json(tmp_path / "raw.json", run)
    mapped, _ = runner.map_results(runner.read_json(tmp_path / "raw.json"), frozen, settings, official=False)
    assert mapped["source"].setting_evidence["mesh.tag_areas_m2"]["status"] == "observed"


def test_corpus_imported_hbb_parsed_tags_are_not_original_tag_observations(tmp_path):
    from server.tests import test_imported_beat as cad
    case = corpus.CASES["imported-two-sources"]
    record = cad._record()
    frozen = corpus.FrozenCase(case, case.request(record=record).model_dump(mode="json"), cad.MESH.encode(), record, {})
    settings = runner.settings_for(frozen, "cpu", "float32")
    run = evidence(frozen, case.coarse_hz, settings, official=False)
    areas = settings["mesh"]["tag_areas_m2"]
    facts = {"n_vertices": 4, "n_triangles": 4,
             "physical_tag_areas_m2": {"1": sum(v for k, v in areas.items() if k != "101"), "2": areas["101"]}}
    run["native"]["source"]["mesh_info"] = facts
    runner.write_json(tmp_path / "raw.json", run)
    mapped, _ = runner.map_results(runner.read_json(tmp_path / "raw.json"), frozen, settings, official=False)
    evidence_fields = mapped["source"].setting_evidence
    assert evidence_fields["mesh.node_count"]["status"] == "observed"
    assert evidence_fields["mesh.tag_areas_m2"]["status"] == "declared"
    assert evidence_fields["hbb_mesh_info_sha256"]["value"] == runner.record_sha256(facts)
