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


def test_the_gate_passes_against_this_checkout(tmp_path: Path) -> None:
    gate = _gate()
    # What the suite's own WG2_* settings say describes this process, not the
    # server the gate starts; the gate says exactly what the server gets.
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("WG2_")
    }
    report: dict[str, object] = {}

    gate.run_gate(
        REPO_ROOT, Path(sys.executable), environment, tmp_path / "work", tmp_path / "out", report
    )

    grace = report["launcher_grace_seconds"]
    assert isinstance(grace, float)
    quit_ = report["quit"]
    assert isinstance(quit_, dict)
    assert quit_["exit_code"] == 0
    assert quit_["seconds"] < grace
    assert report["next_start_job"]["stage_message"] == "Interrupted by Quit"  # type: ignore[index]
    assert report["left_behind_after_restart"] == []
    memory = report["memory_ceiling"]
    assert isinstance(memory, dict)
    assert memory["physical"]["known"] is True
    assert (tmp_path / "out" / "server.log").is_file()
