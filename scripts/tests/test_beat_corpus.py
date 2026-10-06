"""Production corpus controls, without Julia or numerical engine solves."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.beat_conformance import corpus, run_corpus as runner
from scripts.beat_conformance.agreement import ResultSet

# Existing tiny authoritative WG mesh; tests never mesh CAD or start an engine.
MESH = (corpus.ROOT / "server/solver/warmup_mesh.msh").read_bytes()


@pytest.fixture
def frozen():
    case = replace(corpus.CASES["osse-full"], expect_no_features=True, no_features_reason="synthetic monotone fixture")
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
            "solver_log": [{"frequency_hz": float(hz), "native_diagnostics": {"backend": settings["backend"], "blas_threads": 1,
                           "precision": settings["precision"]}} for hz in f],
            "surface_pressure_complex": None, "surface_neumann_complex": None}


def evidence(frozen, frequencies, settings, *, official, gain=1.):
    name = "beat-engine" if official else "hornlab-beat-bem"
    return {"native": {"source": native(frequencies, settings, gain=gain)}, "response": {},
            "official": official, "mesh_sha256": frozen.sha256, "frequencies_hz": list(frequencies),
            "timing": {"source": "fixture_wall", "wall_seconds": len(frequencies),
                       "frequency_count": len(frequencies), "wall_seconds_per_frequency": 1., "per_frequency": []},
            "identity": {"wg_commit": {"returncode": 0, "stdout": "wg-test-commit"},
                         "wg_worktree": {"returncode": 0, "stdout": ""}, "runtime_env": {},
                         "distributions": {name: {"revision": "official-revision" if official else "hbb-pin"}}}}


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_catalogue_uses_valid_wg_requests(case):
    request = case.request()
    assert request.options.frequencies_hz == list(case.coarse_hz)
    assert request.options.polar_config.spherical_sampling
    assert 3 <= len(case.coarse_hz) <= runner.FREQUENCY_LIMIT
    assert case.dense_step_hz <= 25 and not case.expect_no_features
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
    case = corpus.CASES["osse-quarter"]
    timing = {name: {"wall_seconds": 10., "wall_seconds_per_frequency": 20.} for name in ("hbb", "official")}
    plan = runner.refine_windows({"a": result}, case, timing=timing)
    assert len(plan["windows"]) == 2
    assert len(plan["parts"]) > 1
    assert all(3 <= len(p["frequencies_hz"]) <= 11 for p in plan["parts"])
    for window in plan["windows"]:
        assert all(window["step_hz"] / feat["frequency_hz"] < .005 for feat in window["features"])
        assert np.all(np.diff(window["frequencies_hz"]) == window["step_hz"])
        assert np.array_equal(np.asarray(window["frequencies_hz"]), np.asarray(window["frequencies_hz"], dtype=np.float32))


def planning_reference(frequencies, levels):
    f = np.asarray(frequencies, dtype=float)
    pressure = 10 ** (np.asarray(levels, dtype=float) / 20)
    return ResultSet(MESH, f, pressure, np.ones(len(f)) / f,
                     np.ones(len(f)), np.ones(len(f)), {}, "hbb")


def planning_timing(hbb=1., official=1., count=13):
    return {name: {"wall_seconds": cost * count, "wall_seconds_per_frequency": cost}
            for name, cost in (("hbb", hbb), ("official", official))}


def test_corpus_local_windows_clamp_merge_adjacent_and_keep_gaps():
    f = np.arange(500., 3001., 250.)
    levels = np.zeros((len(f), 4))
    for column, index in enumerate((1, 3, 7, 9)):
        levels[index, column] = 3
    ref = planning_reference(f, levels)
    plan = runner.refine_windows({"source": ref}, corpus.CASES["osse-quarter"], timing=planning_timing())
    assert [(w["start_hz"], w["end_hz"]) for w in plan["windows"]] == [(500., 1000.), (1000., 1500.), (2000., 2500.), (2500., 3000.)]
    assert [len(w["features"]) for w in plan["windows"]] == [1, 1, 1, 1]
    assert runner.local_bounds(f, 0) == (500., 750.)
    assert runner.local_bounds(f, len(f) - 1) == (2750., 3000.)
    frequencies = {f for part in plan["parts"] for f in part["frequencies_hz"]}
    assert not any(1500 < f < 2000 for f in frequencies)
    for window in plan["windows"]:
        assert window["step_hz"] <= .0025 * min(feat["frequency_hz"] for feat in window["features"])
        assert all(window["start_hz"] <= f <= window["end_hz"] for f in window["frequencies_hz"])


def test_corpus_local_windows_overlap_across_channels_and_quantities():
    f = np.arange(500., 1751., 250.)
    a = planning_reference(f, [0, 0, 3, 0, 0, 0])
    b = planning_reference(f, [0, 0, 0, 3, 0, 0])
    b = replace(b, impedance_per_acceleration=10 ** (np.array([0, 0, 0, 0, 3, 0]) / 20) / f)
    plan = runner.refine_windows({"a": a, "b": b}, corpus.CASES["osse-quarter"], timing=planning_timing())
    assert len(plan["windows"]) == 1
    window = plan["windows"][0]
    assert (window["start_hz"], window["end_hz"]) == (750., 1750.)
    assert len(window["features"]) == 3 and window["step_hz"] == 2


def test_corpus_local_windows_do_not_round_outside_off_grid_neighbours():
    ref = planning_reference([500, 751, 1001, 1251, 1500], [0, 0, 3, 0, 0])
    plan = runner.refine_windows({"source": ref}, corpus.CASES["osse-quarter"], timing=planning_timing())
    window = plan["windows"][0]
    axis = np.asarray(window["frequencies_hz"])
    assert axis[0] == 751 and axis[-1] == 1251
    assert np.max(np.diff(axis)) <= window["step_hz"] == 2
    np.testing.assert_array_equal(axis, axis.astype(np.float32))


@pytest.mark.parametrize("official_cost,max_minutes", [(1., 8.), (10., 8.), (10., 3.)])
def test_corpus_parts_use_both_measured_costs_and_startup(official_cost, max_minutes):
    ref = planning_reference([500, 750, 1000, 1250, 1500], [0, 0, 3, 0, 0])
    timing = planning_timing(official=official_cost)
    plan = runner.refine_windows({"source": ref}, corpus.CASES["osse-quarter"], timing=timing,
                                 max_part_minutes=max_minutes)
    cost = 1 + official_cost
    startup = 13 * cost
    limit = min(runner.FREQUENCY_LIMIT, int((max_minutes * 60 - startup) // cost))
    assert plan["per_part_limit"] == limit
    assert plan["part_count"] == len(plan["parts"])
    assert plan["estimated_minutes"] == pytest.approx(sum(p["estimated_minutes"] for p in plan["parts"]))
    for part in plan["parts"]:
        count = len(part["frequencies_hz"])
        assert count <= limit
        assert part["estimated_minutes"] == pytest.approx((count * cost + startup) / 60)
        assert part["estimated_minutes"] <= max_minutes
    for left, right in zip(plan["parts"], plan["parts"][1:]):
        assert left["frequencies_hz"][-2:] == right["frequencies_hz"][:2]
    acquired = [f for part in plan["parts"] for f in part["frequencies_hz"]]
    assert plan["count"] == len(set(acquired)) == 251
    assert plan["acquired_frequency_count"] == len(acquired)


@pytest.mark.parametrize("minutes", [0, -1, float("nan"), float("inf"), .01])
def test_corpus_invalid_or_unusable_part_budget_refuses(minutes):
    ref = planning_reference([500, 750, 1000], [0, 3, 0])
    with pytest.raises(ValueError, match="max-part-minutes|Part wall budget"):
        runner.refine_windows({"source": ref}, corpus.CASES["osse-quarter"],
                              timing=planning_timing(), max_part_minutes=minutes)


def test_corpus_invalid_part_budget_refuses_before_meshing(tmp_path):
    with pytest.raises(ValueError, match="max-part-minutes"):
        runner.run_case(corpus.CASES["osse-quarter"], tmp_path / "run", backend="cpu",
                        precision="float32", julia="fake", max_part_minutes=float("nan"),
                        freezer=lambda *a, **k: pytest.fail("meshing started"))


@pytest.mark.parametrize("kind,sign", [("peaks", 1), ("dips", -1)])
@pytest.mark.parametrize("quantity", ["pressure_complex", "impedance_per_acceleration"])
def test_corpus_each_feature_must_resolve_even_when_same_column_has_another(kind, sign, quantity):
    f = np.arange(500., 2001., 250.)
    levels = sign * np.array([0, 3, 0, 0, .5, 0, 0])
    ref = planning_reference(f, levels)
    if quantity == "impedance_per_acceleration":
        ref = replace(ref, impedance_per_acceleration=10 ** (levels / 20) / f)
    features = [{"channel": "source", "quantity": quantity, "column": 0, "kind": kind,
                 "frequency_hz": center, "start_hz": center - 250, "end_hz": center + 250}
                for center in (750., 1500.)]
    resolution = runner.resolve_features(ref, features, 1.)
    assert not resolution["passed"]
    assert [f["status"] for f in resolution["features"]] == ["resolved", "not_resolved"]
    assert resolution["features"][1]["reference_prominence_db"] == pytest.approx(.5)


def test_corpus_two_coarse_features_cannot_share_one_refined_peak():
    ref = planning_reference([500, 750, 1000, 1250, 1500], [0, 0, 3, 0, 0])
    features = [{"channel": "source", "quantity": "pressure_complex", "column": 0, "kind": "peaks",
                 "frequency_hz": f, "start_hz": 500., "end_hz": 1500.} for f in (750., 1250.)]
    resolution = runner.resolve_features(ref, features, 1.)
    assert not resolution["passed"]
    assert sorted(f["status"] for f in resolution["features"]) == ["not_resolved", "resolved"]


def test_corpus_broad_feature_keeps_full_band_prominence_in_refine_window(frozen, tmp_path):
    case = replace(frozen.case, coarse_hz=(500., 750., 1000., 1250., 1500.))
    frozen = replace(frozen, case=case, request=case.request().model_dump(mode="json"))
    settings = runner.settings_for(frozen, "cpu", "float32")
    def pair(frozen, frequencies, directory, **kwargs):
        directory.mkdir()
        runs = [evidence(frozen, frequencies, settings, official=value) for value in (False, True)]
        levels = 2 * (1 - ((np.asarray(frequencies) - 1000) / 500) ** 2)
        for name, run in zip(("hbb", "official"), runs):
            run["native"]["source"]["pressure_complex"][:] = (10 ** (levels / 20))[:, None, None]
            run["timing"].update(wall_seconds=.1 * len(frequencies), wall_seconds_per_frequency=.1)
            runner.write_json(directory / f"{name}.json", run)
        return tuple(runs)
    report = runner.run_case(case, tmp_path / "run", backend="cpu", precision="float32", julia="fake",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert report["status"] == "complete" and report["passed"]
    local = report["local_windows"][0]["channels"]["source"]
    assert local["passed"] and local["metrics"]
    assert all(f["status"] == "resolved" for f in local["feature_resolution"]["features"])



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
        return {"returncode": 0, "stdout": "" if "status" in command else "fake", "stderr": ""}
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


def test_corpus_wall_timing_is_context_only(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, frozen.case.coarse_hz, settings, official=value) for value in (False, True)]
    runs[0]["timing"]["wall_seconds"] = 100000
    runs[1].pop("timing")
    report = runner.score_pair(*runs, frozen, settings, frequency_step=250)
    assert report["passed"]
    assert report["timing"]["hbb"][0]["wall_seconds"] == 100000
    assert report["timing"]["official"][0]["source"] == "unavailable"


def test_corpus_legacy_wall_and_per_frequency_native_timings_are_labelled():
    run = {"frequencies_hz": [500., 750., 1000.],
           "response": {"metadata": {"performance": {"total_time_seconds": 6.}}},
           "native": {"a": {"solver_log": [{"frequency_hz": 1000., "timings": {"solve_s": .1}},
                                           {"frequency_hz": 500., "timings": {"solve_s": .2}}]}}}
    timing = runner.engine_timing(run)
    assert timing["source"] == "legacy_production_route_wall"
    assert timing["wall_seconds_per_frequency"] == 2
    assert timing["per_frequency"] == [
        {"channel": "a", "frequency_hz": 1000., "native_timings": {"solve_s": .1}},
        {"channel": "a", "frequency_hz": 500., "native_timings": {"solve_s": .2}}]
    measured = runner.engine_timing(run, wall_seconds=9.)
    assert measured["source"] == "parent_process_wall" and measured["wall_seconds_per_frequency"] == 3


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
    for field in runner.ARRAY_FIELDS:
        if b["native"]["source"].get(field) is not None:
            b["native"]["source"][field][1] = a["native"]["source"][field][1]
    merged = runner.merge_runs([a, b])
    assert merged["frequencies_hz"] == [500., 750., 1000.]
    assert merged["native"]["source"]["pressure_complex"][1, 0, 0] == b["native"]["source"]["pressure_complex"][0, 0, 0]
    assert merged["native"]["source"]["pressure_complex"][2, 0, 0] == a["native"]["source"]["pressure_complex"][1, 0, 0]


@pytest.mark.parametrize("case", corpus.CASES.values(), ids=lambda c: c.name)
def test_corpus_fast_production_meshing_is_deterministic(case, tmp_path):
    # All nine builders were measured < 2 s here. The separate tiny-fixture tests
    # retain catalogue/runner coverage on machines without the optional mesher.
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    import time
    from pathlib import Path
    # pytest's tmp_path, like WG's other CAD-ingestion tests: on Windows a
    # strict TemporaryDirectory cleanup raises WinError 32 while the gmsh
    # session or ingestion store still holds a handle in the directory.
    first_dir, second_dir = Path(tmp_path) / "first", Path(tmp_path) / "second"
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


def test_corpus_isolation_launches_sequential_children_with_separate_environments(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "require_official_ready", lambda *args: None)
    monkeypatch.setattr(runner, "wg_identity", lambda: {})
    official_depot = tmp_path / "official-depot"
    hbb_chain = os.pathsep.join(str(tmp_path / name) for name in ("hbb-writable", "hbb-precompiled"))
    monkeypatch.setenv("JULIA_DEPOT_PATH", str(official_depot))
    monkeypatch.setenv("JULIA_LOAD_PATH", "/official/load")
    monkeypatch.setenv("JULIA_PROJECT", "/official/project")
    monkeypatch.setenv("BLAB_TEST_OVERRIDE", "unsafe")
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", "/hbb/runtime")
    monkeypatch.setenv("HORNLAB_BEAT_WORKER_DIR", "/hbb/workers")
    monkeypatch.setenv("HORNLAB_BEAT_FORCE_CPU", "1")
    clock = iter((0., 8., 8., 13.))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
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
            assert timeout == frozen.case.coarse_minutes[1] * 60
            return 0
    from pathlib import Path
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    reference, candidate = runner.isolated_pair(frozen, (500., 750., 1000.), tmp_path / "coarse",
                                               backend="cpu", precision="float32", julia="julia", hbb_depot=hbb_chain)
    assert len(calls) == 2
    for i, (command, options) in enumerate(calls):
        assert command[2] == "scripts.beat_conformance.run_corpus"
        assert command[command.index("--official-child") + 1] == str(i)
        assert options["start_new_session"]
        env = options["env"]
        assert "BLAB_TEST_OVERRIDE" not in env
        assert env["HORNLAB_BEAT_RUNTIME_DIR"] == "/hbb/runtime"
        assert env["HORNLAB_BEAT_WORKER_DIR"] == "/hbb/workers"
        assert env["HORNLAB_BEAT_FORCE_CPU"] == "1"
        run = candidate if i else reference
        assert run["environment"] == runner.relevant_environment(env)
        assert run["environment_sha256"] == runner.environment_sha256(env)
        if i:
            assert env["WG2_BEAT_RUNTIME_DIR"] == str(tmp_path / "official-runtime")
            assert env["WG2_BEAT_WORKER_DIR"] == str(tmp_path / "official-workers")
            assert env["JULIA_DEPOT_PATH"] == str(official_depot.resolve())
        else:
            assert env["JULIA_DEPOT_PATH"] == os.pathsep.join(
                str((tmp_path / name).resolve()) for name in ("hbb-writable", "hbb-precompiled"))
            for key in ("JULIA_LOAD_PATH", "JULIA_PROJECT", "WG2_BEAT_RUNTIME_DIR", "WG2_BEAT_WORKER_DIR", "WG2_BEAT_JULIA"):
                assert key not in env
        assert all(options["env"][k] == "1" for k in runner.THREAD_ENV)
    assert reference is not candidate
    for run in (reference, candidate):
        assert run["timing"]["source"] == "parent_process_wall"
        assert run["timing"]["wall_seconds_per_frequency"] == run["timing"]["wall_seconds"] / 3
    assert runner.read_json(tmp_path / "coarse/hbb.json")["timing"] == reference["timing"]
    assert reference["timing"]["wall_seconds"] == 8
    assert candidate["timing"]["wall_seconds"] == 5


def test_corpus_both_route_refusals_are_recorded(frozen, tmp_path):
    def refuse(*args, **kwargs):
        return ({"error": "BeatUnavailable: exact HBB refusal", "refusal": "exact HBB refusal"},
                {"error": "BeatUnavailable: exact official refusal", "refusal": "exact official refusal"})
    report = runner.run_case(frozen.case, tmp_path / "run", backend="cpu", precision="float32", julia="julia",
                             freezer=lambda *a, **k: frozen, pair_runner=refuse)
    assert report["status"] == "unsupported"
    assert report["refusals"] == {"hbb": "exact HBB refusal", "official": "exact official refusal"}


@pytest.mark.parametrize("max_minutes", [7.2, 4.67])
def test_corpus_refine_parts_remain_incomplete_until_complete_windows(frozen, tmp_path, max_minutes):
    case = replace(frozen.case, coarse_hz=tuple(float(f) for f in range(500, 3501, 250)))
    frozen = replace(frozen, case=case, request=case.request().model_dump(mode="json"))
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
                             freezer=lambda *a, **k: frozen, pair_runner=pair, max_part_minutes=max_minutes)
    assert report["status"] == "refine_incomplete" and not report["passed"]
    assert report["required_refine_parts"] == 2 and report["completed_refine_parts"] == [1]
    final = runner.run_case(frozen.case, tmp_path / "last", backend="cpu", precision="float32", julia="julia",
                            phase="refine", coarse_dir=first, refine_part=2, refine_dirs=(first,), pair_runner=pair,
                            max_part_minutes=max_minutes)
    assert final["status"] == "complete" and final["passed"]
    assert final["completed_refine_parts"] == [1, 2]
    assert len(final["local_windows"]) == 1
    assert len(final["agreement"]["timing"]["hbb"]) == 3
    assert final["agreement"]["timing"]["hbb"][0]["frequency_count"] == len(frozen.case.coarse_hz)
    assert runner.read_json(first / "part.json")["timing"]["official"]["wall_seconds_per_frequency"] == 1
    if max_minutes == 4.67:
        plan = runner.read_json(first / "refine-plan.json")
        assert plan["parts"][1]["frequencies_hz"][0] == 1500.  # Peak on an acquisition boundary.


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
    captures, wires = [], []
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
              "_julia_executable": "fake-julia", "_native_result_callback": lambda ch, raw: captures.append(ch)}
    if imported:
        response = beat_imported.solve_imported_beat_from_msh_text(cad.MESH, cad._request(), cad._record(), **kwargs)
        assert response["metadata"]["solver_engine"]["precision"] == "double"
    else:
        request = SolveRequest.model_validate(frozen.request)
        response = beat.solve_beat_from_msh_text(frozen.mesh_bytes.decode(), SolverContext.from_request(request, solver_mode="full_3d"), **kwargs)
        assert response["metadata"]["beat"]["precision"] == "double"
    assert len(captures) == len(wires) and len(captures) == len(set(captures))


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


def test_corpus_dense_union_observes_candidate_only_features():
    f = np.arange(500., 601., 10.)
    reference = planning_reference(f, [0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0])
    candidate = planning_reference(f, [0, 0, 3, 0, 0, 0, 0, 0, 3, 0, 0])
    plan = runner.refine_windows({"source": reference}, corpus.CASES["osse-full"],
                                 candidate={"source": candidate}, timing=planning_timing())
    assert [(w["start_hz"], w["end_hz"]) for w in plan["windows"]] == [(510., 530.), (540., 560.), (570., 590.)]
    assert plan["windows"][-1]["features"][0]["engine"] == "official"
    for window in plan["windows"]:
        assert window["step_hz"] <= .0025 * min(f["frequency_hz"] for f in window["features"])
        np.testing.assert_array_equal(window["frequencies_hz"], np.float32(window["frequencies_hz"]))


def test_corpus_part_limit_comes_from_request_schema():
    reference = planning_reference([10., 20., 30.], [0, 3, 0])
    plan = runner.refine_windows({"source": reference}, corpus.CASES["osse-full"],
                                 timing=planning_timing(hbb=.001, official=.001))
    assert plan["per_part_limit"] == runner.FREQUENCY_LIMIT == 401
    assert plan["count"] > 401
    assert all(len(p["frequencies_hz"]) <= runner.FREQUENCY_LIMIT for p in plan["parts"])
    assert set(plan["windows"][0]["frequencies_hz"]) == {
        f for part in plan["parts"] for f in part["frequencies_hz"]}


@pytest.mark.parametrize("allow,reason,passed", [(False, None, False), (True, None, False),
                                               (True, "synthetic featureless control", True)])
def test_corpus_no_features_is_explicit_failure(frozen, tmp_path, allow, reason, passed):
    frozen = replace(frozen, case=replace(frozen.case, expect_no_features=allow, no_features_reason=reason))
    settings = runner.settings_for(frozen, "cpu", "float32")
    calls = []
    def pair(frozen, frequencies, directory, **kwargs):
        calls.append(frequencies)
        return tuple(evidence(frozen, frequencies, settings, official=value) for value in (False, True))
    report = runner.run_case(frozen.case, tmp_path / "case", backend="cpu", precision="float32", julia="fake",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert report["passed"] is passed
    assert report["status"] == ("complete" if passed else "no_features_observed")
    assert len(calls) == 1
    assert report["coarse"]["reference_feature_count"] == 0
    assert report["coarse"]["frequency_step"] == frozen.case.dense_step_hz


@pytest.mark.parametrize("failed_engine", [0, 1])
def test_corpus_phase_both_single_engine_error_has_failed_verdict(frozen, tmp_path, failed_engine):
    settings = runner.settings_for(frozen, "cpu", "float32")
    calls = []
    def pair(frozen, frequencies, directory, **kwargs):
        calls.append(directory)
        runs = [evidence(frozen, frequencies, settings, official=value) for value in (False, True)]
        runs[failed_engine]["error"] = "one engine failed"
        return tuple(runs)
    report = runner.run_case(frozen.case, tmp_path / "run", backend="cpu", precision="float32", julia="fake",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert report["status"] == "failed" and not report["passed"] and len(calls) == 1
    assert runner.read_json(tmp_path / "run/verdict.json") == report


@pytest.mark.parametrize("width,passed", [(8., True), (80., False)])
def test_corpus_reference_q_uses_absolute_3db_crossings(width, passed):
    f = np.arange(200., 401., .5)
    levels = -10 * np.log10(1 + (2 * (f - 280) / width)**2)
    result = planning_reference(f, levels)
    sentinel = runner.narrowness_sentinel({"source": result}, 1., set(f))
    assert sentinel["passed"] is passed
    assert sentinel["features"][0]["q"] == pytest.approx(280 / width, rel=.01)
    assert sentinel["features"][0]["width_3db_hz"] == pytest.approx(width, rel=.01)
    if not passed:
        assert sentinel["status"] == "resonance_not_narrow"
    assert not runner.narrowness_sentinel({"source": result}, 1., set())["passed"]
    assert not runner.narrowness_sentinel({"source": result}, 1., {280.})["passed"]


def test_corpus_q_failure_is_scoring_failure(frozen):
    case = replace(frozen.case, require_narrow=True)
    frozen = replace(frozen, case=case)
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, case.coarse_hz, settings, official=value) for value in (False, True)]
    report = runner.score_pair(*runs, frozen, settings, frequency_step=case.dense_step_hz,
                               refined_frequencies=set(case.coarse_hz))
    assert report["status"] == "resonance_not_narrow" and not report["passed"]


@pytest.mark.parametrize("horizontal,vertical,passed", [(1., 1., False), (1.1, 1., False),
                                                       (1.1, 1.2, True), (1e-5, 1.2, False)])
def test_corpus_cut_requires_both_masked_controls(horizontal, vertical, passed):
    result = planning_reference([500, 510, 520], [0, 3, 0])
    p = np.ones((3, 3, 2), complex)
    p[:, 0] *= horizontal
    p[:, 1] *= vertical
    result = replace(result, pressure_complex=p)
    sentinel = runner.cut_sentinel({"source": result}, ["horizontal", "vertical", "diagonal"])
    assert sentinel["passed"] is passed
    if not passed:
        assert sentinel["status"] == "cut_not_discriminating"


def test_corpus_cut_failure_is_scoring_failure(frozen):
    frozen = replace(frozen, case=replace(frozen.case, require_cut_sensitivity=True))
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, frozen.case.coarse_hz, settings, official=value) for value in (False, True)]
    report = runner.score_pair(*runs, frozen, settings, frequency_step=frozen.case.dense_step_hz)
    assert report["status"] == "cut_not_discriminating" and not report["passed"]


def test_corpus_merge_and_slice_logs_by_frequency_and_check_overlap(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    a = evidence(frozen, (500., 510., 520.), settings, official=False)
    b = evidence(frozen, (510., 520., 530.), settings, official=False)
    a["native"]["source"]["solver_log"].reverse()
    b["native"]["source"]["solver_log"] = np.roll(b["native"]["source"]["solver_log"], 1).tolist()
    b["native"]["source"]["solver_log"][0]["timings"] = {"solve_s": 123.}
    merged = runner.merge_runs([a, b])
    assert [row["frequency_hz"] for row in merged["native"]["source"]["solver_log"]] == [500, 510, 520, 530]
    sliced = runner.slice_run(a, [500., 520.])
    assert [row["frequency_hz"] for row in sliced["native"]["source"]["solver_log"]] == [500, 520]
    b["native"]["source"]["solver_log"][1]["native_diagnostics"]["precision"] = "float64"
    with pytest.raises(ValueError, match="overlapping solver_log settings"):
        runner.merge_runs([a, b])


@pytest.mark.parametrize("key", ["wg_commit", "wg_worktree", "runtime_env", "environment"])
def test_corpus_merge_refuses_changed_commit_dirty_tree_and_environment(frozen, key):
    settings = runner.settings_for(frozen, "cpu", "float32")
    a, b = [evidence(frozen, (500., 510., 520.), settings, official=False) for _ in range(2)]
    if key == "wg_worktree":
        b["identity"][key]["stdout"] = " M scripts/beat_conformance/corpus.py"
    elif key == "wg_commit":
        b["identity"][key]["stdout"] = "other-commit"
    elif key == "environment":
        b[key] = {"JULIA_DEPOT_PATH": "/other/depot"}
    else:
        b["identity"][key] = {"JULIA_PROJECT": "/other/project"}
    with pytest.raises(ValueError, match="clean WG|identity|environment"):
        runner.merge_runs([a, b])


def test_corpus_dirty_tree_refuses_before_julia_probe(monkeypatch):
    calls = []
    def command(command):
        calls.append(command)
        return {"returncode": 0, "stdout": " M corpus.py" if "status" in command else "commit"}
    monkeypatch.setattr(runner, "command_evidence", command)
    with pytest.raises(ValueError, match="clean WG"):
        runner.capture_identity("forbidden-julia")
    assert all(command[0] == "git" for command in calls)


@pytest.mark.parametrize("value", [None, "", "/hbb:"])
def test_corpus_requires_explicit_hbb_depot(value):
    with pytest.raises(ValueError, match="hbb-depot"):
        runner.child_environment(official=False, julia="fake", hbb_depot=value)


@pytest.mark.parametrize("official", [False, True])
def test_corpus_driver_production_response_supplies_electrical_features(official, monkeypatch, tmp_path):
    from server.tests import test_imported_beat as cad
    from server.solver import beat, beat_imported, official_beat
    from server.platform import temp_session
    from server.solver.driver_lem import hornlab_driver
    from server.contracts import DriverSpec
    case = corpus.CASES["driver-loading"]
    record = cad._record()
    record["sources"][0]["observed"] = {"total_area_mm2": np.pi * 25**2}
    frozen = corpus.FrozenCase(case, case.request(record=record).model_dump(mode="json"), cad.MESH.encode(), record, {})
    settings = runner.settings_for(frozen, "cpu", "float32")
    captures = []
    class Package(cad._RecordingBeat):
        def solve_frequencies(self, path, frequencies, config, **kwargs):
            result = super().solve_frequencies(path, frequencies, config, **kwargs)
            for key, value in native(frequencies, settings).items():
                setattr(result, key, value)
            result.impedance[:] = .01  # mocked small acoustic mass; real driver coupling executes
            captures.append(result)
            return result
    package = Package()
    statuses = {"cpu": {"available": True, "backend": "cpu", "surface_traces": False}}
    for module in (beat, beat_imported):
        monkeypatch.setattr(module, "_load_api", lambda: package)
        monkeypatch.setattr(module, "beat_backend_statuses", lambda: statuses)
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    monkeypatch.setattr(official_beat, "production_statuses", lambda: statuses)
    def solve(compiled, **kwargs):
        values = native(compiled.wire["frequencies_hz"], settings)
        values.update(impedance=np.full(len(values["frequencies_hz"]), .01, complex),
                      spl_db=np.zeros(values["pressure_complex"].shape),
                      directivity_db=np.zeros(values["pressure_complex"].shape), timings={}, cancelled=False)
        result = SimpleNamespace(**values)
        captures.append(result)
        return result
    monkeypatch.setattr(official_beat, "solve_compiled", solve)
    run = runner.production_run(frozen, case.coarse_hz, official=official, backend="cpu", precision="float32", julia="fake")
    assert len(captures) == len(run["native"]) == 1
    channel = next(iter(run["native"]))
    response = run["response"]["channels"][channel]
    assert response["metadata"]["impedance_quantity"] == "electrical_input_impedance"
    assert "electrical_impedance_ohm" not in response["metadata"]["driver"]
    electrical = run["native"][channel]["electrical_impedance_ohm"]
    np.testing.assert_array_equal(electrical, np.array(response["impedance"]["real"]) + 1j * np.array(response["impedance"]["imaginary"]))
    run["identity"] = evidence(frozen, case.coarse_hz, settings, official=official)["identity"]
    mapped, _ = runner.map_results(run, frozen, settings, official=official)
    features = runner.detect_features(mapped, case)
    assert any(f["quantity"] == "electrical_impedance_ohm" for f in features)
    fs = hornlab_driver(DriverSpec.model_validate(corpus.DRIVER)).derive().Fs
    assert case.coarse_hz[0] < fs < case.coarse_hz[-1]
    assert corpus.DRIVER["sd_cm2"] == pytest.approx(np.pi * 2.5**2)


def test_corpus_electrical_resonance_gate_precedes_norms(frozen):
    settings = runner.settings_for(frozen, "cpu", "float32")
    f = frozen.case.coarse_hz
    runs = [evidence(frozen, f, settings, official=value) for value in (False, True)]
    for run in runs:
        run["native"]["source"]["electrical_impedance_ohm"] = np.ones(len(f), complex)
    runs[0]["native"]["source"]["electrical_impedance_ohm"][5] = 3
    report = runner.score_pair(*runs, frozen, settings, frequency_step=frozen.case.dense_step_hz)
    channel = report["channels"]["source"]
    assert not channel["resonances"]["electrical_impedance_ohm"]["passed"]
    assert not channel["metrics"] and not channel["extra_fields"]


@pytest.mark.parametrize("imported", [False, True])
def test_corpus_hbb_hooks_default_metadata_precision_and_callback_counts(frozen, imported, monkeypatch, tmp_path):
    from server.tests import test_imported_beat as cad
    from server.solver import beat, beat_imported
    from server.solver.context import SolverContext
    from server.jobs.models import SolveRequest
    from server.platform import temp_session
    package = cad._RecordingBeat()
    status = {"cpu": {"available": True, "backend": "cpu", "surface_traces": False}}
    for module in (beat, beat_imported):
        monkeypatch.setattr(module, "_load_api", lambda: package)
        monkeypatch.setattr(module, "beat_backend_statuses", lambda: status)
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    captures = []
    kwargs = {"backend": "cpu", "_official": False,
              "_native_result_callback": lambda channel, result: captures.append(channel)}
    if imported:
        request = cad._request()
        response = beat_imported.solve_imported_beat_from_msh_text(cad.MESH, request, cad._record(), **kwargs)
        assert response["metadata"]["solver_engine"]["precision"] == "single"
        assert all(c["metadata"]["beat"]["precision"] == "single" for c in response["channels"].values())
        assert sorted(captures) == sorted(c.id for c in request.geometry.drive_channels)
    else:
        request = SolveRequest.model_validate(frozen.request)
        response = beat.solve_beat_from_msh_text(frozen.mesh_bytes.decode(), SolverContext.from_request(request, solver_mode="full_3d"), **kwargs)
        assert response["metadata"]["beat"]["precision"] == "single"
        assert captures == ["source"]


def test_corpus_final_report_uses_dense_step_and_local_step(frozen, tmp_path):
    case = replace(frozen.case, coarse_hz=tuple(float(f) for f in range(500, 601, 10)))
    frozen = replace(frozen, case=case, request=case.request().model_dump(mode="json"))
    settings = runner.settings_for(frozen, "cpu", "float32")
    def pair(frozen, frequencies, directory, **kwargs):
        runs = [evidence(frozen, frequencies, settings, official=value) for value in (False, True)]
        for run in runs:
            levels = 8 * np.maximum(1 - np.abs(np.asarray(frequencies) - 550) / 30, 0)
            run["native"]["source"]["pressure_complex"][:] = 10**(levels[:, None, None] / 20)
            run["timing"].update(wall_seconds=.1 * len(frequencies), wall_seconds_per_frequency=.1)
        return tuple(runs)
    report = runner.run_case(case, tmp_path / "run", backend="cpu", precision="float32", julia="fake",
                             freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert report["passed"] and report["agreement"]["frequency_step"] == 10
    assert report["local_windows"][0]["frequency_step"] == 1
    assert all(f["status"] == "resolved" for f in
               report["local_windows"][0]["channels"]["source"]["feature_resolution"]["features"])


def test_corpus_refine_refuses_legacy_coarse_declaration(frozen, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    corpus.save_frozen(frozen, old)
    settings = runner.settings_for(frozen, "cpu", "float32")
    runner.write_json(old / "inputs.json", {"case": frozen.case, "backend": "cpu", "precision": "float32",
        "frequencies_hz": list(range(500, 3501, 250)), "frequency_step_hz": 250, "settings": settings})
    with pytest.raises(ValueError, match="catalogue/dense declaration"):
        runner.run_case(frozen.case, tmp_path / "new", phase="refine", coarse_dir=old,
                        backend="cpu", precision="float32", julia="fake",
                        pair_runner=lambda *a, **k: pytest.fail("engine started"))


@pytest.mark.parametrize("q", [10, 30, 100, 300])
@pytest.mark.parametrize("center", [280., 280.125])
@pytest.mark.parametrize("quantity", ["pressure_complex", "impedance_per_acceleration"])
def test_corpus_q_real_refine_windows_resolve_lorentzians(q, center, quantity):
    case = corpus.CASES["narrow-resonance"]
    def response(axis):
        levels = -10 * np.log10(1 + (2 * q * (axis - center) / center)**2)
        result = planning_reference(axis, levels if quantity == "pressure_complex" else np.zeros(len(axis)))
        if quantity == "impedance_per_acceleration":
            result = replace(result, impedance_per_acceleration=10**(levels / 20) / axis)
        return result
    dense = np.asarray(case.coarse_hz)
    plan = runner.refine_windows({"source": response(dense)}, case, timing=planning_timing())
    refined = {f for part in plan["parts"] for f in part["frequencies_hz"]}
    axis = np.asarray(sorted(set(dense) | refined))
    sentinel = runner.narrowness_sentinel({"source": response(axis)}, 1., refined,
                                          band_hz=case.narrow_band_hz, columns=case.narrow_columns)
    assert sentinel["passed"], sentinel
    assert {f["quantity"] for f in sentinel["features"]} == {quantity}
    if q <= 100:
        # Wider shoulders lie outside the real neighbour refinement window.
        feature = sentinel["features"][0]
        assert feature["crossings_hz"][0] < min(refined)
        assert feature["crossings_hz"][1] > max(refined)


@pytest.mark.parametrize("mode", ["single_sample", "outside_band", "wrong_column", "electrical_only"])
def test_corpus_q_requires_supported_declared_mode(mode):
    case = corpus.CASES["narrow-resonance"]
    dense = np.asarray(case.coarse_hz)
    center = 330.25 if mode == "outside_band" else 280.
    def response(axis):
        levels = -10 * np.log10(1 + (60 * (axis - center) / center)**2)
        if mode == "single_sample":
            levels = np.where(axis == center, 30., 0.)
        result = planning_reference(axis, levels)
        if mode == "wrong_column":
            result = replace(result, pressure_complex=np.column_stack((np.ones(len(axis)), result.pressure_complex)))
        elif mode == "electrical_only":
            result = replace(result, pressure_complex=np.ones(len(axis)), electrical_impedance_ohm=result.pressure_complex)
        return result
    plan = runner.refine_windows({"source": response(dense)}, case, timing=planning_timing())
    refined = {f for part in plan["parts"] for f in part["frequencies_hz"]}
    axis = np.asarray(sorted(set(dense) | refined))
    assert not runner.narrowness_sentinel({"source": response(axis)}, 1., refined)["passed"]


@pytest.mark.parametrize("option", ["output", "coarse", "refine", "depot"])
def test_corpus_paths_inside_repository_refuse_up_front(monkeypatch, tmp_path, option):
    root = tmp_path / "repo"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(runner, "ROOT", root)
    kwargs = {"coarse_dir": alias / "old"} if option == "coarse" else (
        {"refine_dirs": (alias / "part",)} if option == "refine" else (
            {"hbb_depot": str(tmp_path / "hbb") + ":" + str(alias / "depot")} if option == "depot" else {}))
    output = alias / "out" if option == "output" else tmp_path / "external"
    with pytest.raises(ValueError, match="outside the WG repository root"):
        runner.run_case(corpus.CASES["osse-full"], output, backend="cpu", precision="float32", julia="forbidden",
                        freezer=lambda *a, **k: pytest.fail("meshing started"), **kwargs)
    assert not output.exists()


@pytest.mark.parametrize("default_depot", [False, True])
def test_corpus_depot_chains_refuse_shared_entries_even_read_only(monkeypatch, tmp_path, default_depot):
    from server.solver.beat_runtime import paths
    shared = tmp_path / "runtime" / "wg-beat-engine" / "depot"
    shared.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(shared, target_is_directory=True)
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    if default_depot:
        monkeypatch.delenv("JULIA_DEPOT_PATH", raising=False)
        assert paths.runtime_dir() / "depot" == shared
    else:
        monkeypatch.setenv("JULIA_DEPOT_PATH", str(tmp_path / "official") + ":" + str(shared))
    for official in (False, True):
        with pytest.raises(ValueError, match="distinct chains.*read-only"):
            runner.child_environment(official=official, julia="forbidden", hbb_depot=str(tmp_path / "hbb") + ":" + str(alias))


@pytest.mark.parametrize("key", ["JULIA_CPU_TARGET", "WG2_BEAT_OTHER", "HORNLAB_BEAT_RUNTIME_DIR",
                                 "HORNLAB_BEAT_WORKER_DIR", "HORNLAB_BEAT_FORCE_CPU", "BLAB_TEST",
                                 "OPENBLAS_CORETYPE", "OMP_PROC_BIND", "MKL_DYNAMIC"])
def test_corpus_environment_allowlist_records_and_merge_compares_every_prefix(frozen, key):
    env = {"PATH": "/bin", key: "one"}
    dump = runner.relevant_environment(env)
    assert dump == {key: "one"}
    settings = runner.settings_for(frozen, "cpu", "float32")
    a, b = [evidence(frozen, (500., 510., 520.), settings, official=False) for _ in range(2)]
    a["environment"] = dump
    b["environment"] = runner.relevant_environment(dict(env, **{key: "two"}))
    with pytest.raises(ValueError, match="environment"):
        runner.merge_runs([a, b])


def test_corpus_full_environment_hash_is_sorted_and_excludes_only_documented_volatile_keys():
    env = {"PATH": "/bin", "SECRET_SETTING": "value", "HORNLAB_BEAT_FORCE_CPU": "1"}
    digest = runner.environment_sha256(env)
    assert len(digest) == 64
    assert runner.environment_sha256(dict(reversed(list(env.items())))) == digest
    for key in runner.VOLATILE_ENV_KEYS:
        assert runner.environment_sha256(dict(env, **{key: "volatile"})) == digest
    assert runner.environment_sha256(dict(env, SECRET_SETTING="changed")) != digest


@pytest.mark.parametrize("direct", [{"dir_info": {"editable": True}, "vcs_info": {"commit_id": "a" * 40}},
                                   {"dir_info": {}}, {}, {"vcs_info": {"commit_id": "abc"}}])
def test_corpus_official_identity_refuses_editable_or_non_vcs_distribution(direct):
    with pytest.raises(ValueError, match="non-editable.*exact VCS commit"):
        runner.require_distribution_identity({"revision": "a" * 40, "direct_url": direct}, "beat-engine")


def test_corpus_official_identity_accepts_exact_noneditable_vcs_distribution():
    runner.require_distribution_identity({"revision": "a" * 40, "direct_url": {"vcs_info": {"commit_id": "a" * 40}}}, "beat-engine")


def test_corpus_driver_declares_electrical_structure_even_with_pressure_features(frozen):
    assert corpus.CASES["driver-loading"].expected_electrical_columns == (0,)
    case = replace(frozen.case, expected_electrical_columns=(0,))
    frozen = replace(frozen, case=case)
    settings = runner.settings_for(frozen, "cpu", "float32")
    runs = [evidence(frozen, case.coarse_hz, settings, official=value) for value in (False, True)]
    for run in runs:
        run["native"]["source"]["pressure_complex"][5] *= 3
        run["native"]["source"]["electrical_impedance_ohm"] = np.ones(len(case.coarse_hz), complex)
    report = runner.score_pair(*runs, frozen, settings, expected=True, frequency_step=case.dense_step_hz)
    assert not report["passed"]
    assert "reference has no expected resonances" in str(report["channels"]["source"]["failures"])


def test_corpus_small_budget_after_dense_writes_verdict_and_plan(frozen, tmp_path):
    settings = runner.settings_for(frozen, "cpu", "float32")
    calls = []
    def pair(frozen, frequencies, directory, **kwargs):
        calls.append(directory)
        runs = [evidence(frozen, frequencies, settings, official=value) for value in (False, True)]
        for run in runs:
            run["native"]["source"]["pressure_complex"][5] *= 3
        return tuple(runs)
    output = tmp_path / "run"
    verdict = runner.run_case(frozen.case, output, backend="cpu", precision="float32", julia="forbidden",
                              max_part_minutes=.01, freezer=lambda *a, **k: frozen, pair_runner=pair)
    assert verdict["status"] == "refine_budget_too_small" and not verdict["passed"]
    assert runner.read_json(output / "verdict.json") == verdict
    assert runner.read_json(output / "refine-plan.json") == verdict["plan"]
    assert verdict["plan"]["windows"] and verdict["required_minutes"] > .01
    assert len(calls) == 1


@pytest.mark.parametrize("precision,tolerance", [("float32", 1e-6), ("float64", 1e-12)])
@pytest.mark.parametrize("field", runner.ARRAY_FIELDS)
def test_corpus_merge_checks_overlapping_numeric_rows(frozen, precision, tolerance, field):
    settings = runner.settings_for(frozen, "cpu", precision)
    a = evidence(frozen, (500., 510., 520.), settings, official=False)
    b = evidence(frozen, (510., 520., 530.), settings, official=False)
    for run in (a, b):
        run["native"]["source"][field] = np.ones((3, 2), complex)
    b["native"]["source"][field][0, 1] += .5j * tolerance
    runner.merge_runs([a, b])
    b["native"]["source"][field][0, 1] += 2j * tolerance
    with pytest.raises(ValueError, match="overlapping numeric"):
        runner.merge_runs([a, b])


@pytest.mark.parametrize("wrong", ["part", "hbb_axis", "official_axis", "native_axis"])
def test_corpus_refine_part_uses_actual_planned_axes(frozen, wrong):
    settings = runner.settings_for(frozen, "cpu", "float32")
    a, b = [evidence(frozen, (500., 510., 520.), settings, official=value) for value in (False, True)]
    plan = {"parts": [{"frequencies_hz": [500., 510., 520.]}, {"frequencies_hz": [510., 520., 530.]}]}
    assert runner.validate_part_runs(plan, 1, a, b) == {500., 510., 520.}
    if wrong == "hbb_axis":
        a["frequencies_hz"] = [500., 520.]
    elif wrong == "official_axis":
        b["frequencies_hz"] = [500., 520., 530.]
    elif wrong == "native_axis":
        b["native"]["source"]["frequencies_hz"] = [500., 520., 530.]
    with pytest.raises(ValueError, match="planned frequencies"):
        runner.validate_part_runs(plan, 2 if wrong == "part" else 1, a, b)


def test_corpus_explicit_frequency_limit_is_shared_with_validator():
    from server.jobs.models import MAX_EXPLICIT_FREQUENCIES, SolveOptions
    assert runner.FREQUENCY_LIMIT == MAX_EXPLICIT_FREQUENCIES == 401
    assert len(SolveOptions(frequencies_hz=list(range(1, MAX_EXPLICIT_FREQUENCIES + 1))).frequencies_hz) == 401
    with pytest.raises(ValueError, match="at most 401"):
        SolveOptions(frequencies_hz=list(range(1, MAX_EXPLICIT_FREQUENCIES + 2)))
    assert SolveOptions(num_frequencies=401).num_frequencies == 401
    with pytest.raises(ValueError):
        SolveOptions(num_frequencies=402)
