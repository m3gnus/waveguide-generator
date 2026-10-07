"""The packaged Quit gate, run for real against this checkout.

``.github/workflows/rc-build.yml`` runs ``scripts/qualify_installed_quit.py``
on each platform's installed candidate. A gate that only ever runs on a
release candidate is a gate nobody has seen fail, so this drives the same
``run_gate`` against the checkout with this interpreter: a real server, a
parked mesh build, a real stop and a real restart.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _gate() -> ModuleType:
    name = "qualify_installed_quit_under_test"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    path = REPO_ROOT / "scripts" / "qualify_installed_quit.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve their annotations
    # through ``sys.modules``, as they would for an ordinary import.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_launcher_grace_is_read_from_the_launcher() -> None:
    gate = _gate()
    from launchers.statusapp.controller import StatusController
    from inspect import signature

    expected = signature(StatusController.__init__).parameters["shutdown_timeout"].default
    assert gate.launcher_grace(REPO_ROOT) == float(expected)


def test_an_unreadable_launcher_grace_is_a_failure_not_a_default(tmp_path: Path) -> None:
    gate = _gate()
    controller = tmp_path / "launchers" / "statusapp" / "controller.py"
    controller.parent.mkdir(parents=True)
    controller.write_text("class StatusController: pass\n", encoding="utf-8")

    with pytest.raises(gate.QualificationError):
        gate.launcher_grace(tmp_path)


def test_the_process_table_sees_this_process_and_its_parent() -> None:
    gate = _gate()
    table = gate.process_table()

    assert os.getpid() in table
    assert table[os.getpid()][1] is True
    assert os.getpid() in gate.descendants(os.getppid(), table)


def test_a_hung_process_table_fails_the_gate_within_its_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = _gate()
    timeout = 0.1
    monkeypatch.setattr(gate, "PROCESS_COMMAND_TIMEOUT_S", timeout)
    # Exercise the subprocess path on every platform without changing os globally.
    monkeypatch.setattr(gate, "os", SimpleNamespace(name="posix"))
    original_run = subprocess.run
    calls = []

    def hung_process_table(command, **kwargs):
        assert command[0] == "ps"
        assert kwargs.get("timeout") == timeout  # Fail promptly if the bound is removed.
        calls.append(command)
        return original_run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    monkeypatch.setattr(subprocess, "run", hung_process_table)
    monkeypatch.setattr(gate, "resolve_payload", lambda payload: (tmp_path, REPO_ROOT, Path(sys.executable)))
    monkeypatch.setattr(gate, "isolated_environment", lambda app, work: {})
    monkeypatch.setattr(gate, "launcher_grace", lambda app: 10.0)
    monkeypatch.setattr(gate, "memory_ceiling", lambda *args: {})
    monkeypatch.setattr(gate, "http", lambda *args: {"job_id": "parked-job"})
    work = tmp_path / "work"
    work.mkdir()
    (work / "gmsh-parked").write_text("_build_sync", encoding="utf-8")
    output = tmp_path / "server.out"
    output.write_bytes(b"")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run = gate.Run(process, work / "stop", output)
    monkeypatch.setattr(gate.Run, "start", lambda *args: run)

    started = time.monotonic()
    try:
        result = gate.main([
            "--payload", str(tmp_path), "--work", str(work), "--output", str(tmp_path / "out")
        ])
        elapsed = time.monotonic() - started
        assert result == 1
        # Two bounded listings: the gate's check, then its cleanup. Allow scheduling overhead.
        assert elapsed < 2 * timeout + 2.0
        assert len(calls) == 2
        assert process.poll() is not None
        report = json.loads((tmp_path / "out" / "quit-qualification.json").read_text())
        assert report["qualified"] is False
        assert report["error"] == "the process-table command (ps) timed out after 0.1 s"
        assert "Quit qualification FAILED: " + report["error"] in capsys.readouterr().err
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_the_windows_cleanup_command_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()
    monkeypatch.setattr(gate, "os", SimpleNamespace(name="nt"))
    calls = []

    def hung_taskkill(command, **kwargs):
        assert command == ["taskkill", "/F", "/PID", "4242"]
        assert kwargs["timeout"] == gate.PROCESS_COMMAND_TIMEOUT_S
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hung_taskkill)
    with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
        gate._kill(4242)
    assert len(calls) == 1


def test_the_servers_own_session_is_named_by_its_lock_not_the_launched_process(tmp_path: Path) -> None:
    """On Windows a virtual environment's ``python.exe`` runs the server as its
    child, so the process the gate starts is not the server, and the server's
    live session carries the child's pid. Judged by the launched pid, that
    session read as a stale one the next start had left behind."""

    gate = _gate()
    data = tmp_path / "data"
    (data / "locks").mkdir(parents=True)
    (data / "locks" / "server.pid").write_text('{"pid": 4242, "port": 3100}\n', encoding="utf-8")
    temporary = tmp_path / "tmp"
    for name in ("wg2-run-4242-live", "wg2-run-17-stale", "tmpunrelated"):
        (temporary / name).mkdir(parents=True)

    assert gate.server_pid(data) == 4242
    assert gate._temporary_leftovers(temporary, except_pid=gate.server_pid(data)) == ["wg2-run-17-stale"]


@pytest.mark.parametrize("content", [None, "", "{}", '{"pid": 0}', '{"pid": true}', '{"pid": "12"}'])
def test_a_server_pid_that_cannot_be_read_is_a_failure(tmp_path: Path, content: str | None) -> None:
    gate = _gate()
    (tmp_path / "locks").mkdir()
    if content is not None:
        (tmp_path / "locks" / "server.pid").write_text(content, encoding="utf-8")

    with pytest.raises(gate.QualificationError):
        gate.server_pid(tmp_path)


@pytest.mark.slow
def test_the_gate_passes_against_this_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()
    # Each start takes seconds here; bound it well below the suite's 300 s
    # faulthandler so a hung start fails with the gate's own error and log tail.
    monkeypatch.setattr(gate, "START_TIMEOUT_S", 120.0)
    # What the suite's own WG2_* settings say describes this process, not the
    # server the gate starts; the gate says exactly what the server gets.
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("WG2_")
    }
    environment["WG2_WGLINK_REFRESH"] = "0"
    report: dict[str, object] = {}

    launches = []
    original_popen = subprocess.Popen

    def checked_popen(command, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("serve.py"):
            child_environment = kwargs["env"]
            data = Path(command[command.index("--data-dir") + 1])
            addins = Path(child_environment["WG2_FUSION_ADDINS_DIR"])
            assert data == Path(child_environment["WG2_DATA_DIR"])
            assert data.is_dir() and addins.is_dir()
            assert data.is_relative_to(tmp_path / "work")
            assert addins.is_relative_to(tmp_path / "work")
            from server.platform.paths import documents_root

            for name in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"):
                assert Path(child_environment[name]).is_relative_to(tmp_path / "work")
            assert documents_root(
                environ=child_environment, home=child_environment["HOME"]
            ).is_relative_to(tmp_path / "work")
            launches.append(data)
        return original_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", checked_popen)
    gate.run_gate(
        REPO_ROOT, Path(sys.executable), environment, tmp_path / "work", tmp_path / "out", report
    )

    assert launches == [tmp_path / "work" / "data"] * 2
    grace = report["launcher_grace_seconds"]
    assert isinstance(grace, float)
    quit_ = report["quit"]
    assert isinstance(quit_, dict)
    assert quit_["exit_code"] == 0
    assert quit_["seconds"] < grace
    assert report["next_start_job"]["stage_message"] == "Interrupted by Quit"  # type: ignore[index]
    assert report["left_behind_after_restart"] == []
    # However the clean stop ended, nothing of either run is left once swept.
    assert [
        path.name for path in (tmp_path / "work" / "tmp").iterdir() if path.name.startswith("wg2-")
    ] == []
    memory = report["memory_ceiling"]
    assert isinstance(memory, dict)
    assert memory["physical"]["known"] is True
    assert (tmp_path / "out" / "server.log").is_file()


def test_the_sweep_the_gate_runs_removes_a_dead_session(tmp_path: Path) -> None:
    """The branch the gate takes when a clean stop leaves its own session behind."""

    gate = _gate()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    holder = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from server.platform.temp_session import TemporarySession\n"
        # Flushed: os._exit discards whatever stdout still buffers.
        f"print(TemporarySession.create(Path({str(temporary)!r})).path.name, flush=True)\n"
        "os._exit(0)\n"
    )
    left = subprocess.run(
        [sys.executable, "-c", holder], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert left.startswith("wg2-run-"), left
    assert (temporary / left).is_dir()
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("WG2_")
    }

    removed = gate.sweep_in_runtime(Path(sys.executable), REPO_ROOT, environment, temporary)

    assert removed == [left]
    assert list(temporary.iterdir()) == []


@pytest.fixture
def fake_quit_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Installed launches, registry authentication and process trees without Julia."""
    gate = _gate()
    state = SimpleNamespace(
        damage=None, cleanup_damage=None, prewarm_log=None,
        runs=[], launches=[], inspections=[], cleaned=False, solves=[],
        # What the official host logs on Windows (``host.main``); [] logs nothing.
        breakaway=["granted"],
    )
    work = tmp_path / "work"
    monkeypatch.setattr(gate, "launcher_grace", lambda app: 8.0)
    monkeypatch.setattr(gate, "memory_ceiling", lambda *args: {"physical": {"known": True}})
    monkeypatch.setattr(gate, "CHILD_REAP_S", 0)
    monkeypatch.setattr(gate, "HOST_TREE_POLL_S", 0.001)

    class FakeRun:
        def __init__(self, environment, root):
            self.pid = 101 + len(state.runs)
            self.server_pid = self.pid
            self.base = f"fake-{self.pid}"
            self.children = set()
            self.process = SimpleNamespace(returncode=None)
            self.stopped = False
            root.mkdir(parents=True)
            self.output = root / "server.out"
            self.output.write_text(state.prewarm_log or
                                   ("BEAT worker prewarm failed after 0.1 s: failed\n"
                                   if state.damage == "prewarm-failed" else
                                   "BEAT worker prewarm finished in 0.1 s\n"))
            if environment.get("WG2_BEAT_PROVIDER") == "official" and not state.runs:
                registry = Path(environment["WG2_BEAT_WORKER_DIR"]) / "official"
                registry.mkdir(parents=True, exist_ok=True)
                for index, outcome in enumerate(state.breakaway):
                    (registry / f"host{index}.log").write_bytes(
                        f"2026-10-07 10:00:00 job breakaway: {outcome}\r\n"
                        "2026-10-07 10:00:01 serving host (idle 1800s)\r\n".encode())
            state.runs.append(self)
            state.launches.append(environment)
        def capabilities(self):
            return {"engines": [{"name": "beat-cpu", "available": True}]}
        def fail_if_exited(self, doing):
            assert not self.stopped
        def stop_and_time(self, grace):
            assert grace == 8.0
            self.stopped = True
            self.process.returncode = 0
            return 0.25
        def kill_if_alive(self):
            self.stopped = True

    monkeypatch.setattr(gate.Run, "start", lambda interpreter, app, env, data, root: FakeRun(env, root))

    def http(base, path, body=None, **kwargs):
        if path == "/api/solve":
            state.solves.append(body)
            (work / "gmsh-parked").write_text("_build_sync\n")
            return {"job_id": "parked-job"}
        assert path == "/api/status/parked-job"
        return {"status": "cancelled", "stage_message": "Interrupted by Quit"}

    monkeypatch.setattr(gate, "http", http)

    def table():
        answer = {}
        for run in state.runs:
            if not run.stopped:
                answer[run.pid] = (1, True)
                answer[run.pid + 10] = (run.pid, True)  # owned mesher
            elif state.damage == "stray-child":
                answer[run.pid + 10] = (1, True)
        if state.runs and not state.cleaned and state.launches[0].get("WG2_BEAT_PROVIDER") == "official":
            if state.damage != "no-host":
                parent = (state.runs[0].pid if not state.runs[0].stopped
                          and state.damage != "unowned-host" else 1)
                host_pid = 202 if state.runs[0].stopped and state.damage == "changed-host" else 201
                if state.damage in {"owned-launcher", "launcher-no-worker"}:
                    answer[191] = (parent, True)
                    parent = 191
                answer[host_pid] = (parent, True)
                if state.damage not in {"no-worker", "launcher-no-worker"}:
                    answer[301] = (host_pid, True)  # official API exposes no engine_pid
                if state.runs[0].stopped and state.damage == "lost-julia":
                    answer.pop(301, None)
        if state.cleaned and "cleanup-live-julia" in {state.damage, state.cleanup_damage}:
            answer[301] = (1, True)
        return answer

    monkeypatch.setattr(gate, "process_table", table)

    def probe(command, **kwargs):
        env = kwargs["env"]
        assert env["WG2_BEAT_PROVIDER"] == "official"
        assert env["WG2_BEAT_WORKER_DIR"] == str(work / "official-beat-registry")
        assert "WG2_SKIP_BEAT_CPU_PROVISION" not in env
        if command[2] == gate._INSPECT_BEAT_HOSTS:
            assert kwargs["cwd"] == str(REPO_ROOT)
            state.inspections.append(len(state.runs))
            present = bool(state.runs) and not state.cleaned and state.damage != "no-host"
            changed = bool(state.runs) and state.runs[0].stopped
            if changed and state.damage == "lost-host":
                present = False
            verified = [{"host_pid": 202 if changed and state.damage == "changed-host" else 201,
                         "engine_pid": None,
                         "worker_instance": "changed" if changed and state.damage == "changed-worker" else "warm"}] if present else []
            if present and state.damage in {"owned-launcher", "launcher-no-worker"}:
                verified[0]["launcher_pid"] = 191
            records = ["host.json"] if present else []
            if changed and state.damage == "stale-record":
                records.append("stale.json")
            if state.cleaned and state.damage == "cleanup-record":
                records.append("stale.json")
                # An authenticated host remains: get past inspection's record
                # count check and exercise run_gate's cleaned["records"] check.
                verified.append({"host_pid": 401, "engine_pid": None, "worker_instance": "left"})
            answer = {"contained": state.damage != "uncontained", "refused": [],
                      "records": records, "verified": verified}
            if changed and state.damage == "refused-record":
                answer["refused"] = [{"reason": "bad HMAC"}]
        else:
            assert command[-1] == "stop"
            state.cleaned = True
            answer = {"contained": True, "refused": [], "verified": []}
            if state.damage == "cleanup-failure":
                answer["refused"] = [{"reason": "cannot authenticate"}]
        return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")

    monkeypatch.setattr(subprocess, "run", probe)
    state.work = work
    state.gate = gate
    return state


@pytest.mark.parametrize("inherited_warmup", [None, "0", "1"])
def test_beat_gate_retains_only_its_authenticated_warm_host_and_cleans_it(fake_quit_gate, inherited_warmup):
    state = fake_quit_gate
    gate = state.gate
    report = {}
    environment = gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official")
    environment["WG2_SKIP_BEAT_CPU_PROVISION"] = "1"  # inherited skip must be removed
    environment.pop("WG2_SOLVER_WARMUP", None)
    if inherited_warmup is not None:
        environment["WG2_SOLVER_WARMUP"] = inherited_warmup
    gate.run_gate(REPO_ROOT, Path(sys.executable), environment, state.work,
                  state.work / "out", report, engine="beat")

    assert state.cleaned and len(state.runs) == 2
    for env in state.launches:
        assert env["WG2_BEAT_PROVIDER"] == "official"
        assert "WG2_SKIP_BEAT_CPU_PROVISION" not in env
        assert "WG2_SOLVER_WARMUP" not in env
    settings = json.loads((state.work / "data" / "ui_settings.json").read_text())
    assert settings["namespaces"]["solveOptions"]["state"]["engine"] == "beat-cpu"
    assert state.solves[0]["options"] == {"engine": "beat-cpu"}
    for phase in ("after_quit", "after_restart", "after_clean_stop"):
        assert report[f"beat_host_{phase}"] == report["beat_host_before_quit"]
    assert report["beat_processes"] == [201, 301]
    assert report["beat_registry_after_cleanup"]["records"] == []
    assert report["next_start_job"]["stage_message"] == "Interrupted by Quit"
    assert report["left_behind_after_restart"] == report["left_by_clean_stop"] == []
    assert report["quit"] == {"seconds": 0.25, "exit_code": 0}


@pytest.mark.parametrize(("damage", "message"), [
    ("no-host", "never started"),
    ("no-worker", "no live Julia/worker"),
    ("launcher-no-worker", "no live Julia/worker"),
    ("prewarm-failed", "CPU prewarm failed"),
    ("unowned-host", "not started by this server"),
    ("lost-host", "retain the same BEAT host"),
    ("changed-worker", "retain the same BEAT host"),
    ("changed-host", "retain the same BEAT host"),
    ("lost-julia", "process tree"),
    ("stale-record", "stale BEAT host registry"),
    ("refused-record", "uncontained or refused"),
    ("stray-child", "outlived the server"),
    ("cleanup-record", "cleanup left host registry records"),
    ("cleanup-live-julia", "survived authenticated cleanup"),
    ("cleanup-failure", "refused records"),
])
def test_beat_gate_rejects_missing_hosts_and_wrong_detach_or_cleanup(fake_quit_gate, damage, message):
    state = fake_quit_gate
    state.damage = damage
    gate = state.gate
    environment = gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official")
    report = {}
    with pytest.raises(gate.QualificationError, match=message):
        gate.run_gate(REPO_ROOT, Path(sys.executable), environment, state.work,
                      state.work / "out", report, engine="beat")
    assert state.cleaned
    if damage == "cleanup-record":
        cleaned = report["beat_registry_after_cleanup"]
        assert len(cleaned["records"]) == len(cleaned["verified"]) == 1


@pytest.mark.parametrize("record_names_launcher", [True, False])
def test_beat_gate_retains_an_owned_launcher_parent(fake_quit_gate, monkeypatch, record_names_launcher):
    state = fake_quit_gate
    state.damage = "owned-launcher"
    gate = state.gate
    if not record_names_launcher:
        original_inspect = gate.inspect_beat_hosts
        def inspect(*args):
            answer = original_inspect(*args)
            for host in answer["verified"]:
                host.pop("launcher_pid", None)
            return answer
        monkeypatch.setattr(gate, "inspect_beat_hosts", inspect)
    report = {}
    gate.run_gate(REPO_ROOT, Path(sys.executable),
                  gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official"),
                  state.work, state.work / "out", report, engine="beat")
    assert report["beat_launcher_pid"] == 191
    assert report["beat_processes"] == [191, 201, 301]
    assert state.cleaned and len(state.runs) == 2


@pytest.mark.parametrize("outcome", ["granted", "refused"])
def test_windows_beat_report_names_the_recorded_breakaway_case(fake_quit_gate, monkeypatch, outcome):
    state = fake_quit_gate
    gate = state.gate
    monkeypatch.setattr(gate.platform, "system", lambda: "Windows")
    state.breakaway = [outcome]
    environment = gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official")
    report = {}
    gate.run_gate(REPO_ROOT, Path(sys.executable), environment,
                  state.work, state.work / "out", report, engine="beat")
    assert report["beat_host_policy"] == gate.WINDOWS_BEAT_HOST_POLICY[outcome]
    assert report["beat_host_policy"].startswith(f"job_breakaway: {outcome} ")
    assert ("allowed" if outcome == "granted" else "forbids breakaway") in report["beat_host_policy"]


@pytest.mark.parametrize("lines", [[], ["granted", "refused"]])
def test_windows_beat_report_refuses_a_missing_or_ambiguous_breakaway_record(
    fake_quit_gate, monkeypatch, lines,
):
    state = fake_quit_gate
    gate = state.gate
    monkeypatch.setattr(gate.platform, "system", lambda: "Windows")
    state.breakaway = lines
    environment = gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official")
    with pytest.raises(gate.QualificationError, match="no single job_breakaway"):
        gate.run_gate(REPO_ROOT, Path(sys.executable), environment,
                      state.work, state.work / "out", {}, engine="beat")
    assert state.cleaned


@pytest.mark.parametrize("log", [
    "BEAT worker prewarm skipped: the first solve uses bempp",
    "BEAT worker prewarm skipped: unavailable runtime",
    "BEAT worker prewarm disabled by WG2_SOLVER_WARMUP=0",
    "beat worker prewarm could not resolve an engine: unavailable",
])
def test_beat_prewarm_fails_on_the_first_poll_when_unavailable(fake_quit_gate, monkeypatch, log):
    state = fake_quit_gate
    state.prewarm_log = log
    gate = state.gate
    def no_sleep(seconds):
        pytest.fail("unavailable prewarm should fail without polling again")
    monkeypatch.setattr(gate.time, "sleep", no_sleep)
    with pytest.raises(gate.QualificationError, match="CPU prewarm unavailable"):
        gate.run_gate(REPO_ROOT, Path(sys.executable),
                      gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official"),
                      state.work, state.work / "out", {}, engine="beat")
    assert state.cleaned


def test_beat_host_tree_waits_out_transient_children_at_capture_and_comparison(monkeypatch):
    gate = _gate()
    host = {"verified": [{"host_pid": 201, "worker_instance": "warm"}], "records": ["host.json"]}
    stable = {201: (1, True), 301: (201, True)}
    tables = iter([
        {**stable, 401: (301, True)}, stable, stable,  # initial capture
        {**stable, 402: (301, True)}, stable, stable,  # comparison
        stable,  # liveness
    ])
    sleeps = []
    monkeypatch.setattr(gate, "process_table", lambda: next(tables))
    monkeypatch.setattr(gate.time, "sleep", sleeps.append)
    monkeypatch.setattr(gate, "inspect_beat_hosts", lambda *args: host)
    processes = gate.stable_beat_tree(201)
    assert processes == {201, 301}
    assert gate.require_detached_host(Path(sys.executable), REPO_ROOT, {}, host, processes) == host
    assert sleeps == [gate.HOST_TREE_POLL_S] * 4


def test_beat_host_tree_settling_is_bounded(monkeypatch):
    gate = _gate()
    calls = []
    clock = SimpleNamespace(now=0.0)
    def changing_table():
        calls.append(None)
        return {201: (1, True), 300 + len(calls): (201, True)}
    def advance(seconds):
        clock.now += seconds
    monkeypatch.setattr(gate, "process_table", changing_table)
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(gate.time, "sleep", advance)
    monkeypatch.setattr(gate, "HOST_TREE_SETTLE_S", 1.0)
    monkeypatch.setattr(gate, "HOST_TREE_POLL_S", 0.25)
    with pytest.raises(gate.QualificationError, match="stable BEAT host process tree"):
        gate.stable_beat_tree(201)
    assert len(calls) == 4
    assert clock.now == 1.0


def test_early_host_ownership_failure_still_checks_for_surviving_julia(fake_quit_gate):
    state = fake_quit_gate
    state.damage = "unowned-host"
    state.cleanup_damage = "cleanup-live-julia"
    gate = state.gate
    report = {}
    with pytest.raises(gate.QualificationError, match="not started by this server") as caught:
        gate.run_gate(REPO_ROOT, Path(sys.executable),
                      gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official"),
                      state.work, state.work / "out", report, engine="beat")
    assert state.cleaned
    assert "survived authenticated cleanup" in report["beat_cleanup_error"]
    assert "survived authenticated cleanup" in caught.value.__notes__[0]


def test_default_gate_still_prefers_bempp_and_skips_beat_without_inspecting_hosts(fake_quit_gate):
    state = fake_quit_gate
    gate = state.gate
    gate.run_gate(REPO_ROOT, Path(sys.executable), {}, state.work, state.work / "out", {})
    assert not state.inspections and not state.cleaned
    assert all(env["WG2_SKIP_BEAT_CPU_PROVISION"] == "1" for env in state.launches)
    assert state.solves[0]["options"] == {}
    settings = json.loads((state.work / "data" / "ui_settings.json").read_text())
    assert settings["namespaces"]["solveOptions"]["state"]["engine"] == "bempp"


def test_beat_main_selects_official_and_reuses_cpu_work_with_a_private_registry(
    tmp_path, monkeypatch,
):
    gate = _gate()
    work, cpu_work, output = (tmp_path / name for name in ("quit", "cpu", "out"))
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "hbb")
    monkeypatch.setattr(gate, "resolve_payload", lambda payload: (REPO_ROOT, REPO_ROOT, Path(sys.executable)))
    seen = []
    def run(app, interpreter, env, scratch, out, report, *, engine):
        seen.append(env)
        assert engine == "beat" and scratch == work
        assert env["WG2_BEAT_PROVIDER"] == "official"
        assert env["WG2_BEAT_RUNTIME_DIR"] == str(cpu_work / "official-beat-runtime")
        assert env["JULIA_DEPOT_PATH"] == str(cpu_work / "julia-depot")
        assert env["WG2_BEAT_WORKER_DIR"] == str(work / "official-beat-registry")
        assert env["HOME"].startswith(str(work))
    monkeypatch.setattr(gate, "run_gate", run)
    assert gate.main(["--payload", str(REPO_ROOT), "--work", str(work), "--output", str(output),
                      "--engine", "beat", "--official-runtime-work", str(cpu_work)]) == 0
    assert len(seen) == 1 and os.environ["WG2_BEAT_PROVIDER"] == "hbb"
    assert not (cpu_work / "official-beat-registry").exists()


def test_parser_defaults_to_bempp_and_rejects_runtime_reuse_without_beat(tmp_path):
    gate = _gate()
    args = ["--payload", str(tmp_path), "--work", str(tmp_path), "--output", str(tmp_path)]
    assert gate.build_parser().parse_args(args).engine == "bempp"
    with pytest.raises(SystemExit) as exc:
        gate.main([*args, "--official-runtime-work", str(tmp_path)])
    assert exc.value.code == 2


def test_beat_main_reports_a_host_that_never_started_as_unqualified(fake_quit_gate, monkeypatch):
    state = fake_quit_gate
    state.damage = "no-host"
    gate = state.gate
    monkeypatch.setattr(gate, "resolve_payload", lambda payload: (REPO_ROOT, REPO_ROOT, Path(sys.executable)))
    output = state.work / "out"
    assert gate.main(["--payload", str(REPO_ROOT), "--work", str(state.work),
                      "--output", str(output), "--engine", "beat"]) == 1
    report = json.loads((output / "quit-qualification.json").read_text())
    assert report["qualified"] is False
    assert "never started" in report["error"]
    assert state.cleaned


def test_cleanup_failure_preserves_the_primary_beat_gate_failure(fake_quit_gate, monkeypatch):
    state = fake_quit_gate
    state.damage = "no-host"
    gate = state.gate
    def failed_cleanup(*args):
        raise gate.QualificationError("cleanup failed")
    monkeypatch.setattr(gate, "stop_our_workers", failed_cleanup)
    report = {}
    with pytest.raises(gate.QualificationError, match="never started") as caught:
        gate.run_gate(REPO_ROOT, Path(sys.executable),
                      gate.isolated_environment(REPO_ROOT, state.work, beat_provider="official"),
                      state.work, state.work / "out", report, engine="beat")
    assert report["beat_cleanup_error"] == "cleanup failed"
    assert caught.value.__notes__ == ["BEAT cleanup also failed: cleanup failed"]


def test_main_includes_cleanup_failure_note_in_the_report_error(fake_quit_gate, monkeypatch):
    state = fake_quit_gate
    state.damage = "no-host"
    gate = state.gate
    monkeypatch.setattr(gate, "resolve_payload", lambda payload: (REPO_ROOT, REPO_ROOT, Path(sys.executable)))
    def failed_cleanup(*args):
        raise gate.QualificationError("cleanup failed")
    monkeypatch.setattr(gate, "stop_our_workers", failed_cleanup)
    output = state.work / "out"
    assert gate.main(["--payload", str(REPO_ROOT), "--work", str(state.work),
                      "--output", str(output), "--engine", "beat"]) == 1
    report = json.loads((output / "quit-qualification.json").read_text())
    assert "never started" in report["error"]
    assert report["error"].endswith("\nBEAT cleanup also failed: cleanup failed")
    assert report["beat_cleanup_error"] == "cleanup failed"


@pytest.mark.parametrize(("answer", "code", "message"), [
    ("not json", 0, "unreadable BEAT host inspection"),
    ("{}", 1, "BEAT host inspection failed"),
    ('{"contained": false}', 0, "uncontained or refused"),
    ('{"contained": true, "refused": [{"reason": "HMAC"}]}', 0, "uncontained or refused"),
    ('{"contained": true, "records": ["dead.json"], "verified": []}', 0, "stale BEAT host registry"),
])
def test_beat_host_inspection_refuses_failed_or_unverifiable_probes(monkeypatch, answer, code, message):
    gate = _gate()
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, code, answer, "failed"))
    with pytest.raises(gate.QualificationError, match=message):
        gate.inspect_beat_hosts(Path(sys.executable), REPO_ROOT, {})


def test_beat_probe_reads_only_its_empty_registry_without_starting_julia(tmp_path):
    gate = _gate()
    environment = gate.isolated_environment(REPO_ROOT, tmp_path, beat_provider="official")
    answer = gate.inspect_beat_hosts(Path(sys.executable), REPO_ROOT, environment)
    assert answer["contained"] and answer["records"] == answer["verified"] == []
    assert not Path(environment["WG2_BEAT_WORKER_DIR"]).exists()


def test_rc_build_runs_the_beat_quit_gate_on_every_installed_candidate():
    import yaml

    workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/rc-build.yml").read_text())
    for job in ("macos-bundle", "windows-bundle", "linux-bundle"):
        steps = workflow["jobs"][job]["steps"]
        names = [step.get("name", "") for step in steps]
        default = next(i for i, name in enumerate(names) if name.startswith("Qualify Quit during a blocked mesh build"))
        beat = next(i for i, name in enumerate(names) if name.startswith("Qualify Quit with a live official BEAT host"))
        assert beat > default
        step, default_step = steps[beat], steps[default]
        assert step["if"] == "${{ !cancelled() }}"
        assert "env" not in step or "WG2_BEAT_PROVIDER" not in step["env"]  # the gate selects official itself
        run = step["run"]

        def argument(text, flag):
            line = next(line for line in text.splitlines() if line.strip().startswith(flag + " "))
            return line.strip()[len(flag):].strip().rstrip("\\`").strip()

        assert "--engine beat" in run
        official = next(s for s in steps if s.get("name", "").startswith("Qualify the official BEAT engine"))
        # The reused runtime is exactly the official CPU gate's work directory.
        assert argument(run, "--official-runtime-work") == argument(official["run"], "--work")
        assert argument(run, "--payload") == argument(default_step["run"], "--payload")
        assert argument(run, "--payload-kind") == argument(default_step["run"], "--payload-kind")
        assert argument(run, "--work") != argument(default_step["run"], "--work")
        assert argument(run, "--output") != argument(default_step["run"], "--output")
        assert "beat-quit-work" in argument(run, "--work")
        assert "beat-quit-qualification" in argument(run, "--output")
        log = steps[beat + 1]
        assert names[beat + 1] == "Preserve the BEAT Quit qualification logs"
        assert log["if"] == "always()" and "beat-quit-qualification" in log["run"]
        if job == "windows-bundle":
            assert step["shell"] == "pwsh" and log["shell"] == "pwsh"
            assert '$ErrorActionPreference = "Stop"' in run and "$LASTEXITCODE" in run
        else:
            assert "shell" not in step and "set -euo pipefail" in run


def test_a_start_that_never_reserves_a_port_fails_with_the_server_log_tail(tmp_path, monkeypatch):
    gate = _gate()

    class Hung:
        pid = 4242

        def __init__(self, command, **kwargs):
            kwargs["stdout"].write(b"loading engines...\nstill waiting on a lock\n")

        def poll(self):
            return None  # alive, never writes ready.json, never exits

    monkeypatch.setattr(gate.subprocess, "Popen", Hung)
    monkeypatch.setattr(gate, "START_TIMEOUT_S", 0.3)
    with pytest.raises(gate.QualificationError) as failure:
        gate.Run.start(Path("python"), tmp_path / "app", {}, tmp_path / "data", tmp_path / "run")
    message = str(failure.value)
    assert "reserve a port" in message
    assert "server log tail" in message and "still waiting on a lock" in message
