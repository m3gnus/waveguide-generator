"""BEAT CPU is offered on every supported computer, and never overstated.

The reported defect: ``BEAT · CPU`` did not appear as available on machines
that have a CPU -- which is all of them. Two separate things had to hold, and
one of them did not:

* **It must be offered.** The capability list carries a row for every BEAT
  backend whatever its state, and the interface renders each as an option that
  is disabled and carries its reason rather than disappearing. That half was
  already true and is pinned here so it stays true.
* **It must be preparable.** Availability came from a provisioning record, and
  the provisioning that writes it ran on Windows and Linux only, so on a Mac
  the row could never light up. Its remedy named a shell command a packaged
  application gives nobody a shell for.

And the thing that must *not* change: offering the row is not the same as
preferring it. AUTO's order is settled by measurement and is checked separately
in ``test_beat_cpu_runtime``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.solver import beat_cpu_runtime
from server.solver.beat_cpu_runtime import CPU_BACKEND, cpu_runtime_readiness


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "julia"
    project.mkdir(parents=True, exist_ok=True)
    for name in ("Project.toml", "Manifest.toml"):
        (project / name).write_text("", encoding="utf-8")
    return project


def _package(monkeypatch, tmp_path: Path, *, state: dict[str, Any] | None,
             julia: str | None = None) -> Any:
    """An installed package of the current vintage, in a chosen state."""

    project = _project(tmp_path)
    provision = SimpleNamespace(
        read_state=lambda runtime_dir=None, *, backend=None: state,
        provisioned_julia=lambda runtime_dir=None, *, backend=None: (
            str(state.get("julia_executable"))
            if state and state.get("status") == "ready" and state.get("julia_executable")
            and Path(str(state["julia_executable"])).exists()
            else None
        ),
        read_backend_states=lambda runtime_dir=None: ({} if state is None else {CPU_BACKEND: state}),
        provision_cpu=object(),
    )
    package = SimpleNamespace(
        provision=provision,
        discover_julia=lambda: julia,
        runtime=SimpleNamespace(default_project=lambda backend: project, package_fingerprint=lambda p: "fp"),
    )
    monkeypatch.setattr(beat_cpu_runtime, "_import", lambda name: {
        "hornlab_beat_bem": package, "hornlab_beat_bem.provision": provision,
        "hornlab_beat_bem.runtime": package.runtime,
    }.get(name))
    return package


# ---------------------------------------------------------------------------
# Offered on every supported computer
# ---------------------------------------------------------------------------


def test_the_capability_list_always_carries_a_cpu_row(monkeypatch) -> None:
    """Unavailable is a state to show, not a reason to drop the row.

    A user who cannot see ``BEAT · CPU`` at all has no way to learn why it is
    missing, and no way to ask for it once it is ready.
    """

    from server.solver import beat as beat_module

    monkeypatch.setattr(
        beat_module, "_load_api", lambda: None
    )
    statuses = beat_module.beat_backend_statuses()

    assert CPU_BACKEND in statuses, statuses
    assert statuses[CPU_BACKEND]["reason"], "an unavailable row must say why"


def test_every_backend_including_cpu_reaches_the_engine_capabilities(monkeypatch) -> None:
    """The registry advertises four BEAT engines, not one.

    And it advertises the unavailable ones too: the interface renders each as a
    disabled option carrying its reason, which is only possible if the row is
    published in the first place.
    """

    from server.engines import registry
    from server.solver import beat as beat_module
    from server.solver.beat import BEAT_BACKENDS

    # detect_engines imports it from server.solver.beat at call time, so that
    # is where the double has to go.
    monkeypatch.setattr(
        beat_module,
        "beat_backend_statuses",
        lambda: {
            backend: {
                "available": backend == "metal",
                "reason": f"{backend} probe said so",
                "version": "0.1.0",
                "surface_traces": False,
            }
            for backend in BEAT_BACKENDS
        },
    )

    engines = {info.name: info for info in registry.detect_engines(environ={})}

    assert "beat-cpu" in engines, sorted(engines)
    cpu_row = engines["beat-cpu"]
    assert cpu_row.available is False
    assert cpu_row.reason == "cpu probe said so", "the row must carry its own reason"
    assert engines["beat-metal"].available is True, "the other rows are unaffected"


# ---------------------------------------------------------------------------
# Preparable, and honest about which of those states it is in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected", "must_say"),
    [
        (None, "unprovisioned", "provision"),
        ({"status": "in_progress", "backend": CPU_BACKEND, "step": "instantiate"},
         "interrupted", "again"),
        ({"status": "failed", "backend": CPU_BACKEND, "error": "no space left on device"},
         "failed", "no space left on device"),
    ],
)
def test_a_runtime_that_is_not_ready_says_which_kind_of_not_ready(
    tmp_path, monkeypatch, state, expected, must_say
) -> None:
    """"Not ready" is four different situations with four different remedies.

    A user waiting on a download, a user whose disk filled up, and a user who
    has never asked for the runtime need different sentences -- and none of them
    may be told the engine is ready.
    """

    package = _package(monkeypatch, tmp_path, state=state, julia="/usr/bin/julia")
    if state is not None:
        state.setdefault("project", str(_project(tmp_path)))
        state.setdefault("package_fingerprint", "fp")

    readiness = cpu_runtime_readiness(package)

    assert readiness.ready is False, "provisioning that has not succeeded is never ready"
    assert readiness.state == expected, readiness
    assert must_say in readiness.reason.casefold(), readiness.reason


def test_a_provisioning_in_flight_in_this_process_is_reported_as_such(
    tmp_path, monkeypatch
) -> None:
    """The distinction the record alone cannot make.

    A runtime being downloaded right now and one nobody has asked for both have
    no ready record. Only this process knows which, and telling them apart is
    the difference between "wait" and "do something".
    """

    import threading

    package = _package(monkeypatch, tmp_path, state=None, julia="/usr/bin/julia")
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    monkeypatch.setattr(beat_cpu_runtime, "_provision_thread", worker)
    monkeypatch.setattr(beat_cpu_runtime, "_provision_step", "instantiate")
    try:
        readiness = cpu_runtime_readiness(package)
    finally:
        release.set()
        worker.join(timeout=5.0)

    assert readiness.ready is False
    assert readiness.state == "provisioning", readiness
    assert "instantiate" in readiness.reason


def test_a_missing_julia_is_its_own_state_not_a_failure(tmp_path, monkeypatch) -> None:
    """No Julia yet is the ordinary first-run state, and says so distinctly.

    This is the "provisionable, but not installed" case, and it is deliberately
    *not* the same state as "provisioning failed": one is a machine that has not
    been asked yet, the other is a machine that tried and could not. Neither may
    be reported as ready on the strength of a package being installed.
    """

    package = _package(monkeypatch, tmp_path, state=None, julia=None)

    readiness = cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "no-julia", readiness
    assert readiness.state != "failed", "nothing has failed; nothing has been tried"
    assert "portable julia" in readiness.reason.casefold(), readiness.reason


def test_a_ready_record_is_only_believed_with_a_julia_that_exists(
    tmp_path, monkeypatch
) -> None:
    """A record is a claim about a file, and the file can be gone.

    Believing it would put a selectable engine in front of a user that fails at
    the first solve, which is the failure mode the readiness rewrite exists to
    remove.
    """

    project = _project(tmp_path)
    package = _package(
        monkeypatch, tmp_path,
        state={
            "status": "ready", "backend": CPU_BACKEND, "project": str(project),
            "package_fingerprint": "fp",
            "julia_executable": str(tmp_path / "deleted" / "julia"),
        },
    )

    readiness = cpu_runtime_readiness(package)

    assert readiness.ready is False, "a record naming a Julia that is gone is not ready"
