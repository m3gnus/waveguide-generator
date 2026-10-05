"""The packaged Quit gate, run for real against this checkout.

``.github/workflows/rc-build.yml`` runs ``scripts/qualify_installed_quit.py``
on each platform's installed candidate. A gate that only ever runs on a
release candidate is a gate nobody has seen fail, so this drives the same
``run_gate`` against the checkout with this interpreter: a real server, a
parked mesh build, a real stop and a real restart.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import subprocess
from types import ModuleType

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
