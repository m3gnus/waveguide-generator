"""Performance acquisition/report contracts with fakes; never start Julia."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.beat_conformance import corpus, run_corpus, run_perf as perf, perf_report as report
from scripts.beat_conformance.json_io import read_json, write_json

MESH = (corpus.ROOT / "server/solver/warmup_mesh.msh").read_bytes()
PINS = read_json(corpus.ROOT / "pins.json")["modules"]


def clean_identity(pid=100):
    return {"wg_commit": {"returncode": 0, "stdout": "a" * 40},
            "wg_worktree": {"returncode": 0, "stdout": ""}, "pid": pid,
            "distributions": {name: {"name": name, "version": "1", "revision": PINS[name]["sha"],
                "direct_url": {"vcs_info": {"commit_id": PINS[name]["sha"]}}}
                for name in ("hornlab-beat-bem", "beat-engine")},
            "julia_path": "/provisioned/julia", "julia_version": {"returncode": 0, "stdout": "julia version fake"},
            "environment_sha256": "env", "runtime_env": {},
            "thread_env": {key: None for key in run_corpus.THREAD_ENV}}


def records(case="osse-full", *, first_ratio=1.2, warm_ratio=1.1):
    result = []
    for job in perf.interleaving_plan(cases=(case,)):
        seq = job["sequence"]
        official = job["route"] == "official"
        state = {"logical_cpus": 10, "load_average": [1., 1., 1.],
                 "power": {"returncode": 0, "stdout": "AC Power"}}
        result.append(dict(job, schema=perf.SCHEMA, precision="float32", identity=clean_identity(100 + seq),
            mesh_sha256="mesh", request_sha256="request", comparison_environment_sha256=job["route"],
            environment_sha256="env", environment={},
            started_at_epoch_s=seq * 100., ended_at_epoch_s=seq * 100. + 80,
            machine_before=deepcopy(state), machine_after=deepcopy(state),
            hosts=[{"pid": seq + 1000, "key": {"julia_threads": 8}}],
            memory={"peak_rss_bytes": 1024**3, "interval_s": .1, "samples": [{"rss_bytes": 1024**3}], "errors": []},
            timing={"first_result_s": 10 * (first_ratio if official else 1), "first_sweep_s": 40.,
                    "warm_sweep_s": 10 * (warm_ratio if official else 1), "warm_host_reused": True,
                    "threads": {"julia_threads": 8, "blas_threads": 8}}))
    return result


def aggregate(rows, **kwargs):
    return report.aggregate(rows, expected_cases=("osse-full",), **kwargs)


def test_plan_is_case_backend_route_repetition_not_paired_solves():
    plan = perf.interleaving_plan(3)
    assert len(plan) == 18
    assert [r["sequence"] for r in plan] == list(range(1, 19))
    for case in perf.PERF_FREQUENCIES:
        jobs = [r for r in plan if r["case"] == case]
        assert [(r["route"], r["repetition"]) for r in jobs] == [
            (route, n) for n in (1, 2, 3) for route in ("hbb", "official")]
        axis = perf.PERF_FREQUENCIES[case]
        assert 20 <= len(axis) <= 40 and set(axis) <= set(corpus.CASES[case].coarse_hz)
    with pytest.raises(ValueError, match="N >= 3"):
        perf.interleaving_plan(2)


def test_plan_cli_does_not_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(run_corpus, "launch_child", lambda *a: pytest.fail("plan started child"))
    path = tmp_path / "plan.json"
    assert perf.main(["--plan", "--backend", "metal", "--output", str(path)]) == 0
    assert len(read_json(path)["jobs"]) == 18


@pytest.mark.parametrize("official", [False, True])
def test_environment_keeps_production_thread_policy_and_isolates_hosts(monkeypatch, tmp_path, official):
    monkeypatch.setenv("JULIA_DEPOT_PATH", str(tmp_path / "official-depot"))
    monkeypatch.setenv("JULIA_NUM_THREADS", "auto")
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("HORNLAB_BEAT_PERSISTENT_HOST", raising=False)
    env = perf.perf_environment(route="official" if official else "hbb", julia="/julia",
                               hbb_depot=str(tmp_path / "hbb-depot"), directory=tmp_path / "job")
    assert env["JULIA_NUM_THREADS"] == "auto" and env["OPENBLAS_NUM_THREADS"] == "8"
    assert "OMP_NUM_THREADS" not in env
    assert env["HORNLAB_BEAT_WORKER_DIR"] == str(tmp_path / "job/hbb-workers")
    assert env["WG2_BEAT_WORKER_DIR"] == str(tmp_path / "job/official-workers")
    second = dict(env, HORNLAB_BEAT_WORKER_DIR="/another/job", WG2_BEAT_WORKER_DIR="/another/job")
    assert perf.comparison_environment_hash(env) == perf.comparison_environment_hash(second)
    assert run_corpus.environment_sha256(env) != run_corpus.environment_sha256(second)
    second["OPENBLAS_NUM_THREADS"] = "4"
    assert perf.comparison_environment_hash(env) != perf.comparison_environment_hash(second)


@pytest.mark.parametrize("mutation,match", [
    (lambda i: i["wg_worktree"].update(stdout=" M dirty"), "clean WG"),
    (lambda i: i["distributions"]["beat-engine"]["direct_url"].update(dir_info={"editable": True}), "non-editable"),
    (lambda i: i["distributions"]["beat-engine"].update(revision="f" * 40, direct_url={"vcs_info": {"commit_id": "f" * 40}}), "WG pin"),
    (lambda i: i["julia_version"].update(returncode=1), "Julia path/version"),
])
def test_identity_refuses_dirty_unpinned_and_unobserved(mutation, match):
    identity = clean_identity()
    mutation(identity)
    with pytest.raises(ValueError, match=match):
        perf.require_identity(identity)


def test_dirty_parent_refuses_before_child_or_output(monkeypatch, tmp_path):
    def dirty():
        identity = clean_identity()
        identity["wg_worktree"]["stdout"] = " M dirty"
        run_corpus.require_clean_wg(identity)
    monkeypatch.setattr(run_corpus, "wg_identity", dirty)
    monkeypatch.setattr(run_corpus, "launch_child", lambda *a: pytest.fail("launched"))
    with pytest.raises(ValueError, match="clean WG"):
        perf.run_one(case="osse-full", backend="cpu", route="hbb", repetition=1, sequence=1,
                      frozen=tmp_path / "frozen", output=tmp_path / "out", julia="fake", hbb_depot="fake")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("case_name", tuple(perf.PERF_FREQUENCIES))
def test_two_production_calls_callback_clock_defaults_and_warm_host(case_name, official):
    case = corpus.CASES[case_name]
    record = {"sources": [{"id": "a"}, {"id": "b"}]} if case.geometry.startswith("imported") else None
    frozen = corpus.FrozenCase(case, case.request(record=record).model_dump(mode="json"), MESH, record, {})
    ticks = iter([100., 103., 104., 120., 200., 201., 202., 215.])
    calls = []
    manager = object()
    def solve(text, request_or_context, *args, **kwargs):
        assert text.encode() == MESH
        calls.append(kwargs)
        assert kwargs["_official"] is official and kwargs["_precision"] == "float32"
        assert kwargs["_worker_manager"] is manager
        assert kwargs.get("_hbb_options", {}) == ({} if official else {"julia_executable": "fake"})
        kwargs["result_callback"](1, {"frequency": 500.})
        kwargs["result_callback"](2, {"frequency": 600.})
        frequencies = perf.PERF_FREQUENCIES[case_name]
        for channel in (["drive-a", "drive-b"] if record else ["source"]):
            kwargs["_native_result_callback"](channel, SimpleNamespace(frequencies_hz=frequencies, cancelled=False,
                solver_log=[{"native_diagnostics": {"blas_threads": 8,
                    "engine_provenance": {"runtime": {"julia_threads": 8}}}} for _ in frequencies]))
        return {"fake": True}
    def wrong(*a, **k):
        pytest.fail("wrong production route")
    result = perf.production_sweeps(frozen, official=official, backend="cpu", julia="fake", manager=manager,
        routes=(wrong, solve) if record else (solve, wrong), clock=lambda: next(ticks), launch_threads=8,
        host_observer=lambda: [{"pid": 400, "key": {"julia_threads": 8}}])
    assert len(calls) == 2
    assert result["first_result_s"] == 3. and result["first_sweep_s"] == 20.
    assert result["warm_sweep_s"] == 15. and result["warm_host_reused"]
    assert result["threads"]["julia_threads"] == result["threads"]["blas_threads"] == 8


def test_incomplete_or_missing_native_threads_refuses():
    frequencies = (500., 600.)
    native = SimpleNamespace(frequencies_hz=frequencies, solver_log=[{}, {}])
    with pytest.raises(ValueError, match="BLAS"):
        perf.native_threads({"source": native}, frequencies, official=False, launch_threads=8)
    native.solver_log = [{"native_diagnostics": {"blas_threads": 8}}] * 2
    with pytest.raises(ValueError, match="Julia"):
        perf.native_threads({"source": native}, frequencies, official=True, launch_threads=8)
    native.cancelled = True
    with pytest.raises(ValueError, match="Incomplete"):
        perf.native_threads({"source": native}, frequencies, official=False, launch_threads=8)


def test_sampler_whole_tree_detached_hosts_peak_and_pid_reuse(tmp_path):
    directory = tmp_path / "workers"
    directory.mkdir()
    write_json(directory / "host.json", {"pid": 4, "key": {}})
    rows = {1: {"ppid": 0, "rss": 10, "start": "parent"},
            2: {"ppid": 1, "rss": 20, "start": "child"},
            3: {"ppid": 2, "rss": 30, "start": "grandchild"},
            4: {"ppid": 0, "rss": 40, "start": "detached-host"},
            5: {"ppid": 4, "rss": 50, "start": "julia"},
            6: {"ppid": 0, "rss": 10000, "start": "unrelated"}}
    sampler = perf.RSSSampler(1, (directory,), reader=lambda: deepcopy(rows))
    sampler.sample()
    assert sampler.peak_bytes == 150 and sampler.samples[0]["pids"] == [1, 2, 3, 4, 5]
    # Reparented Julia is still owned, reused child PID is not.
    rows[5]["ppid"] = 0
    rows[2].update(start="reused", rss=9000)
    rows[4]["rss"] = 10
    sampler.sample()
    assert sampler.samples[-1]["rss_bytes"] == 100 and sampler.peak_bytes == 150
    assert sampler.stop()["interval_s"] == .1


def test_sampler_ps_fallback_kib_conversion(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="1 0 256 Wed Oct 7 12:00:00 2026\n2 1 512 Wed Oct 7 12:00:01 2026\n")
    monkeypatch.setattr(perf.subprocess, "run", run)
    rows = perf.ps_processes()
    assert calls == [["ps", "-axo", "pid=,ppid=,rss=,lstart="]]
    assert rows[2]["rss"] == 512 * 1024
    assert rows[2]["ppid"] == 1 and rows[1]["start"].startswith("Wed")


def test_sampler_psutil_reader_handles_exited_process():
    class Gone(Exception):
        pass
    class Process:
        @property
        def info(self):
            raise Gone()
    fake = SimpleNamespace(NoSuchProcess=Gone, AccessDenied=PermissionError,
        process_iter=lambda attrs: [Process(), SimpleNamespace(info={"pid": 1, "ppid": 0,
            "memory_info": SimpleNamespace(rss=200), "create_time": 12.})])
    assert perf.psutil_processes(fake) == {1: {"ppid": 0, "rss": 200, "start": 12.}}


def test_sampler_errors_are_recorded_and_only_owned_birth_identity_is_stopped(monkeypatch):
    sampler = perf.RSSSampler(1, (), reader=lambda: {1: {"ppid": 0, "rss": 1, "start": "parent"},
                                                   2: {"ppid": 1, "rss": 2, "start": "new"}})
    sampler.known = {1: "parent", 2: "old", 3: "owned"}
    killed = []
    monkeypatch.setattr(perf.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(perf.time, "sleep", lambda t: None)
    sampler.terminate_owned()
    assert killed == []
    def broken():
        sampler._stop.set()
        raise OSError("ps failed")
    sampler.reader = broken
    sampler._run()
    assert sampler.stop()["errors"] == ["OSError: ps failed"]


def test_gate_boundary_and_just_over_budget_are_never_widened():
    result = aggregate(records())
    assert result["passed"] and result["status"] == "pass"
    metrics = result["cases"][0]["metrics"]
    assert metrics["first_result_s"]["official_over_hbb"] == 1.2
    assert metrics["warm_sweep_s"]["official_over_hbb"] == 1.1
    for first, warm in ((1.20001, 1.1), (1.2, 1.10001)):
        result = aggregate(records(first_ratio=first, warm_ratio=warm))
        assert result["status"] == "fail" and not result["passed"]


def test_medians_ranges_not_mean_of_ratios_and_noise_flags():
    rows = records(first_ratio=1., warm_ratio=1.)
    for row, first in zip(rows, (10., 10., 20., 20., 30., 39.)):
        row["timing"]["first_result_s"] = first
    rows[-1]["machine_after"]["load_average"][0] = 8.
    result = aggregate(rows)
    metric = result["cases"][0]["metrics"]["first_result_s"]
    assert metric["hbb"] == {"median": 20., "min": 10., "max": 30., "n": 3}
    assert metric["official_over_hbb"] == 1.
    assert result["passed"] and result["noisy"]
    assert any("ranges overlap" in s for s in result["cases"][0]["noise_flags"])
    assert result["cases"][0]["pairs"][-1]["high_load"]


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r.pop(), "N >= 3"),
    (lambda r: r[0].update(error="failed solve"), "failed solve"),
    (lambda r: r[0]["identity"]["wg_worktree"].update(stdout=" M dirty"), "clean WG"),
    (lambda r: r[0]["timing"]["threads"].update(blas_threads=4), "thread counts must match"),
    (lambda r: r[0].update(mesh_sha256="other"), "mesh_sha256"),
    (lambda r: r[0].update(comparison_environment_sha256="other"), "environment identities"),
    (lambda r: r[0]["identity"].update(environment_sha256="other"), "Child environment"),
    (lambda r: r[0]["identity"].update(julia_path="other"), "Julia/thread"),
    (lambda r: r[0]["memory"].update(errors=["sample failed"]), "memory samples"),
    (lambda r: r[0]["timing"].update(first_result_s=float("nan")), "finite and positive"),
    (lambda r: r[0].update(repetition=2), "Duplicate repetition"),
    (lambda r: r[0].update(sequence=2), "Duplicate child PID or interleaving"),
    (lambda r: r[0].update(ended_at_epoch_s=220.), "Overlapping"),
    (lambda r: r[0]["timing"].update(warm_host_reused=False), "reuse"),
])
def test_bad_evidence_cannot_pass(mutate, match):
    rows = records()
    mutate(rows)
    result = aggregate(rows)
    assert not result["passed"] and result["status"] == "invalid"
    assert match in result["errors"][0]


def test_route_batches_refuse_even_with_three_each():
    rows = records()
    rows = sorted(rows, key=lambda r: r["route"])
    for sequence, row in enumerate(rows, 1):
        row.update(sequence=sequence, started_at_epoch_s=sequence * 100., ended_at_epoch_s=sequence * 100. + 80)
    result = aggregate(rows)
    assert not result["passed"] and "interleave" in result["errors"][0]


def test_all_required_cases_cpu_and_metal_are_separate():
    rows = []
    for case in perf.PERF_FREQUENCIES:
        for row in records(case):
            sequence = len(rows) + 1
            row.update(sequence=sequence, started_at_epoch_s=sequence * 100., ended_at_epoch_s=sequence * 100. + 80)
            row["identity"]["pid"] = 100 + sequence
            rows.append(row)
    assert report.aggregate(rows)["passed"]
    assert not report.aggregate(records())["passed"]
    metal = deepcopy(rows)
    for row in metal:
        sequence = row["sequence"] + len(rows)
        row.update(backend="metal", sequence=sequence, started_at_epoch_s=sequence * 100., ended_at_epoch_s=sequence * 100. + 80)
        row["identity"]["pid"] = 100 + sequence
    result = report.aggregate(rows + metal)
    assert result["passed"] and len(result["cases"]) == 6


def test_report_rendering_and_cli_write_json_and_markdown(tmp_path):
    rows = []
    paths = []
    for case in perf.PERF_FREQUENCIES:
        for row in records(case):
            sequence = len(rows) + 1
            row.update(sequence=sequence, started_at_epoch_s=sequence * 100., ended_at_epoch_s=sequence * 100. + 80)
            row["identity"]["pid"] = sequence + 100
            rows.append(row)
            path = tmp_path / f"run-{sequence}.json"
            write_json(path, row)
            paths.append(path)
    assert report.main([*(str(p) for p in reversed(paths)), "--output", str(tmp_path / "report")]) == 0
    verdict = read_json(tmp_path / "report/verdict.json")
    text = (tmp_path / "report/verdict.md").read_text()
    assert verdict["passed"] and "**PASS**" in text and "1.20" in text and "1.10" in text
    assert "peak_rss_bytes" in text and "MiB" in text and "Julia 8, BLAS 8" in text
    assert report.main([str(tmp_path / "absent"), "--output", str(tmp_path / "invalid")]) == 1
    assert "Evidence refused" in (tmp_path / "invalid/verdict.md").read_text()


def test_launch_reuses_fresh_process_isolation_and_recorded_group(monkeypatch, tmp_path):
    calls = []
    class Process:
        pid = 123
        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if len(calls) == 2:
                raise run_corpus.subprocess.TimeoutExpired("fake", timeout)
            return 0
        def poll(self):
            return None
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()
    monkeypatch.setattr(run_corpus.subprocess, "Popen", popen)
    killed = []
    monkeypatch.setattr(run_corpus.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(run_corpus.subprocess.TimeoutExpired):
        run_corpus.launch_child(["fake"], {}, tmp_path / "log", 1.)
    assert calls[0][1]["start_new_session"] is True
    assert killed == [(123, perf.signal.SIGTERM)]


def test_one_invocation_launches_exactly_one_fresh_route_child(monkeypatch, tmp_path):
    case = corpus.CASES["osse-full"]
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    corpus.save_frozen(corpus.FrozenCase(case, case.request().model_dump(mode="json"), MESH, None, {}), frozen_dir)
    monkeypatch.setattr(run_corpus, "wg_identity", lambda: clean_identity())
    monkeypatch.setattr(run_corpus, "require_official_ready", lambda *a: None)
    monkeypatch.setenv("JULIA_DEPOT_PATH", str(tmp_path / "official-depot"))
    monkeypatch.delenv("HORNLAB_BEAT_PERSISTENT_HOST", raising=False)
    monkeypatch.setattr(perf, "machine_state", lambda: {"load_average": [1., 1., 1.], "power": {"stdout": "AC"}})
    launches = []
    def launch(command, env, log, timeout):
        launches.append((command, env, log, timeout))
        job = read_json(tmp_path / "out/job.json")
        assert job["route"] == "official" and job["repetition"] == 2 and job["sequence"] == 4
        assert command[2] == "scripts.beat_conformance.run_perf"
        assert "--engine-child" in command
        write_json(tmp_path / "out/perf.json", dict(job, schema=perf.SCHEMA, timing={"first_result_s": 2.}))
        return 0
    monkeypatch.setattr(run_corpus, "launch_child", launch)
    class Sampler:
        def __init__(self, root, directories):
            assert root == perf.os.getpid() and len(directories) == 2
        def start(self):
            pass
        def stop(self):
            return {"peak_rss_bytes": 50, "samples": [50], "errors": [], "interval_s": .1}
        def terminate_owned(self):
            pytest.fail("successful child required failure cleanup")
    monkeypatch.setattr(perf, "RSSSampler", Sampler)
    result = perf.run_one(case="osse-full", backend="cpu", route="official", repetition=2, sequence=4,
        frozen=frozen_dir, output=tmp_path / "out", julia="fake", hbb_depot=str(tmp_path / "hbb-depot"))
    assert len(launches) == 1 and launches[0][-1] == 180.
    assert "error" not in result and result["memory"]["peak_rss_bytes"] == 50
    assert result["environment"]["WG2_BEAT_WORKER_DIR"] == str(tmp_path / "out/official-workers")
    assert read_json(tmp_path / "out/perf.json")["machine_before"] == result["machine_before"]


def test_child_identity_refusal_writes_error_without_solve(monkeypatch, tmp_path):
    job = {"route": "official", "julia": "fake", "case": "osse-full", "backend": "cpu"}
    write_json(tmp_path / "job.json", job)
    dirty = clean_identity()
    dirty["wg_worktree"]["stdout"] = " M dirty"
    monkeypatch.setattr(run_corpus, "capture_identity", lambda j: dirty)
    monkeypatch.setattr(perf, "production_sweeps", lambda *a, **k: pytest.fail("dirty solve"))
    assert perf.engine_child(tmp_path / "job.json", tmp_path / "perf.json") == 1
    assert "clean WG" in read_json(tmp_path / "perf.json")["error"]


def test_child_retains_host_mode_manager_until_both_sweeps_then_shuts_down(monkeypatch, tmp_path):
    from server.solver.beat_runtime import manager as managers
    case = corpus.CASES["osse-full"]
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    corpus.save_frozen(corpus.FrozenCase(case, case.request().model_dump(mode="json"), MESH, None, {}), frozen_dir)
    write_json(tmp_path / "job.json", {"route": "official", "julia": "fake", "case": case.name,
        "backend": "cpu", "frozen": str(frozen_dir)})
    events = []
    class Manager:
        def __init__(self, *, directory, mode="host"):
            assert mode == "host" and directory == tmp_path / "official-workers"
            events.append("created")
        def shutdown(self):
            events.append("shutdown")
    def sweeps(frozen, **kwargs):
        events.append("two sweeps")
        directory = tmp_path / "official-workers"
        directory.mkdir()
        write_json(directory / "host.json", {"pid": 42, "key": {"julia_threads": 8}})
        assert kwargs["host_observer"]() == [{"pid": 42, "key": {"julia_threads": 8}}]
        return {"threads": {"julia_threads": 8, "blas_threads": 8}, "warm_host_reused": True}
    monkeypatch.setattr(managers, "WorkerManager", Manager)
    monkeypatch.setattr(run_corpus, "capture_identity", lambda j: clean_identity())
    monkeypatch.setattr(perf, "production_sweeps", sweeps)
    assert perf.engine_child(tmp_path / "job.json", tmp_path / "perf.json") == 0
    assert events == ["created", "two sweeps", "shutdown"]
    assert read_json(tmp_path / "perf.json")["hosts"][0]["pid"] == 42


def test_malformed_json_record_is_nonpassing_verdict():
    result = aggregate([[]])
    assert not result["passed"] and result["status"] == "invalid" and result["errors"]


def test_psutil_unavailable_rss_only_refuses_owned_processes():
    class Gone(Exception):
        pass
    fake = SimpleNamespace(NoSuchProcess=Gone, AccessDenied=PermissionError,
        process_iter=lambda attrs: [SimpleNamespace(info={"pid": 1, "ppid": 0,
            "memory_info": SimpleNamespace(rss=200), "create_time": 12.}),
            SimpleNamespace(info={"pid": 2, "ppid": 0, "memory_info": None, "create_time": 13.})])
    rows = perf.psutil_processes(fake)
    sampler = perf.RSSSampler(1, (), reader=lambda: rows)
    sampler.sample()
    assert sampler.peak_bytes == 200
    rows[2]["ppid"] = 1
    with pytest.raises(ValueError, match="owned PID 2"):
        sampler.sample()


@pytest.mark.parametrize("callback", [True, False])
def test_warm_host_replacement_and_missing_first_callback_refuse(callback):
    case = corpus.CASES["osse-full"]
    frozen = corpus.FrozenCase(case, case.request().model_dump(mode="json"), MESH, None, {})
    def solve(text, context, **kwargs):
        if callback:
            kwargs["result_callback"](1, {})
        frequencies = perf.PERF_FREQUENCIES[case.name]
        kwargs["_native_result_callback"]("source", SimpleNamespace(frequencies_hz=frequencies,
            solver_log=[{"native_diagnostics": {"blas_threads": 8}}] * len(frequencies)))
        return {}
    host = iter([[{"pid": 1, "key": {}}], [{"pid": 2, "key": {}}]])
    with pytest.raises(ValueError, match="reuse" if callback else "no frequency result callback"):
        perf.production_sweeps(frozen, official=False, backend="cpu", julia="fake", routes=(solve, solve),
                               launch_threads=8, host_observer=lambda: next(host))
