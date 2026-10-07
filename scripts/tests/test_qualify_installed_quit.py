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
