"""BEAT CPU runtime: truthful readiness, and who provisions it.

Every case here is a machine somebody actually has. A workstation with no Julia
at all; a Julia that exists because something else put it there, in front of a
project nothing ever instantiated (the case the old "is there a Julia" answer
called *available*, and which then failed inside the user's first solve); a host
that has been provisioned and probed; one where provisioning failed; and one
whose pinned ``hornlab-beat-bem`` predates the provisioning API entirely.

The package is stubbed rather than driven. Its real ``provision_cpu`` downloads
a portable Julia and precompiles a Julia bundle, which is precisely the thing a
test must not do, and the contract this consumer depends on is small enough to
state: ``read_state``/``provisioned_julia``/``default_project``/
``package_fingerprint`` in, a state dict out.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from server.solver import beat, beat_cpu_runtime


CPU_PROJECT_FILES = ("Project.toml", "Manifest.toml")


@pytest.fixture(autouse=True)
def _no_leaked_provisioning():
    """No test may leave the module thinking a provisioning is running."""

    # Each test supplies its own package; a successful real-package probe from
    # an earlier test must not answer for that replacement.
    beat.beat_backend_statuses.cache_clear()
    yield
    beat.beat_backend_statuses.cache_clear()
    beat_cpu_runtime._provision_thread = None
    beat_cpu_runtime._provision_step = None
    beat_cpu_runtime._preparation_in_flight = False


def _cpu_project(tmp_path: Path) -> Path:
    project = tmp_path / "package" / "julia"
    project.mkdir(parents=True)
    for name in CPU_PROJECT_FILES:
        (project / name).write_text("# bundled\n", encoding="utf-8")
    return project


def _julia(tmp_path: Path) -> Path:
    executable = tmp_path / "runtime" / "julia-1.12.6" / "bin" / "julia"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def _ready_julia(state: dict[str, object] | None) -> str | None:
    if (
        state is not None
        and state.get("status") == "ready"
        and state.get("julia_executable")
        and Path(str(state["julia_executable"])).exists()
    ):
        return str(state["julia_executable"])
    return None


def _install_stub_package(
    monkeypatch,
    *,
    project: Path,
    state: dict[str, object] | None,
    fingerprint: str = "abc123",
    julia_on_path: str | None = None,
    provision_cpu: object | None = object(),
    detect_gpu_backend: object | None = None,
    backend_states: dict[str, dict[str, object]] | None = None,
    provision_gpu: object | None = None,
    backend_ready: object | None = None,
) -> SimpleNamespace:
    """Stand in for an installed ``hornlab-beat-bem`` of a chosen vintage.

    ``provision_cpu=None`` is the older pinned package: the attribute simply is
    not there, which is exactly how the real one differs.

    ``backend_states`` is the newer one: readiness recorded per backend, which
    the real package signals by exposing ``read_backend_states`` and by
    accepting ``backend=`` on ``read_state``/``provisioned_julia``. Passing it
    also sets ``state`` as the legacy mirror, so a test can have the mirror
    describe one backend while the CPU record describes another -- which is the
    situation on any host that provisions both.
    """

    def read_state(runtime_dir=None, *, backend=None):
        if backend is None:
            return state
        if backend_states is None:
            raise TypeError("read_state() got an unexpected keyword argument 'backend'")
        return backend_states.get(backend)

    def provisioned_julia(runtime_dir=None, *, backend=None):
        if backend is None:
            return _ready_julia(state)
        if backend_states is None:
            raise TypeError(
                "provisioned_julia() got an unexpected keyword argument 'backend'"
            )
        return _ready_julia(backend_states.get(backend))

    provision = SimpleNamespace(
        read_state=read_state,
        provisioned_julia=provisioned_julia,
    )
    if backend_states is not None:
        provision.read_backend_states = lambda runtime_dir=None: dict(backend_states)
    if provision_cpu is not None:
        provision.provision_cpu = provision_cpu
    if detect_gpu_backend is not None:
        provision.detect_gpu_backend = detect_gpu_backend
    if provision_gpu is not None:
        provision.provision_gpu = provision_gpu
    if backend_ready is not None:
        provision.backend_ready = backend_ready
    runtime = SimpleNamespace(
        default_project=lambda backend: project,
        package_fingerprint=lambda selected=None: fingerprint,
    )
    package = SimpleNamespace(discover_julia=lambda: julia_on_path)
    modules = {
        "hornlab_beat_bem": package,
        "hornlab_beat_bem.provision": provision,
        "hornlab_beat_bem.runtime": runtime,
    }
    monkeypatch.setattr(beat_cpu_runtime, "_import", lambda name: modules.get(name))
    return package


def _ready_state(project: Path, julia: Path, fingerprint: str = "abc123") -> dict:
    return {
        "status": "ready",
        "backend": "cpu",
        "project": str(project),
        "package_fingerprint": fingerprint,
        "julia_executable": str(julia),
        "step": "done",
    }


def test_no_julia_at_all_is_reported_with_the_command_that_fixes_it(
    tmp_path, monkeypatch
) -> None:
    package = _install_stub_package(
        monkeypatch, project=_cpu_project(tmp_path), state=None, julia_on_path=None
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "no-julia"
    assert "No Julia executable was found" in readiness.reason
    assert "hornlab_beat_bem.provision --backend cpu" in readiness.reason


def test_a_discovered_julia_is_not_by_itself_a_usable_cpu_backend(
    tmp_path, monkeypatch
) -> None:
    """The regression this module exists for.

    A Julia executable and the bundled project on disk were treated as
    readiness. On an offline host, or any host that never instantiated the
    project, that is a promise the first solve breaks: the checked-in Manifest
    names packages nothing has downloaded. Nothing about this machine changed --
    only the honesty of the answer.
    """

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch, project=project, state=None, julia_on_path=str(julia)
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "unprovisioned"
    assert str(julia) in readiness.reason
    assert "not been instantiated and probed" in readiness.reason
    assert "hornlab_beat_bem.provision --backend cpu" in readiness.reason


def test_a_provisioned_and_probed_runtime_is_available(tmp_path, monkeypatch) -> None:
    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state=_ready_state(project, julia),
        julia_on_path=None,  # discovery is not consulted once the record is good
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert "1 kHz solve" in readiness.reason
    assert str(julia) in readiness.reason


@pytest.mark.parametrize(
    "field, value, expectation",
    [
        ("backend", "cuda", "a GPU runtime does not satisfy a CPU request"),
        ("package_fingerprint", "different", "an updated package must be re-probed"),
    ],
)
def test_a_ready_record_for_something_else_is_not_readiness(
    tmp_path, monkeypatch, field: str, value: str, expectation: str
) -> None:
    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    state = _ready_state(project, julia)
    state[field] = value
    package = _install_stub_package(
        monkeypatch, project=project, state=state, julia_on_path=str(julia)
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False, expectation
    assert readiness.state == "unprovisioned"


def test_a_ready_record_from_an_identical_copy_elsewhere_is_readiness(
    tmp_path, monkeypatch
) -> None:
    """The regression that kept BEAT · CPU grey in a second install for good.

    A source checkout's virtual environment beside the installed application
    holds the same package, byte for byte, at another path. The per-user record
    names the application's copy. Comparing paths made that record proof of
    nothing here, and the second install never provisions
    (``WG2_SKIP_BEAT_CPU_PROVISION``), so nothing could ever turn the row on.
    Same content fingerprint, same Julia, same depot: the probe the
    application's copy passed is this copy's probe too.
    """

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    elsewhere = tmp_path / "installed-app" / "hornlab_beat_bem" / "julia"
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state=_ready_state(elsewhere, julia),
        julia_on_path=None,
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert "identical copy" in readiness.reason
    assert str(elsewhere) in readiness.reason
    assert str(julia) in readiness.reason
    assert "--backend cpu" not in readiness.reason


def test_a_copy_elsewhere_with_other_content_is_not_readiness(
    tmp_path, monkeypatch
) -> None:
    """Content, not location, is the identity -- so other content still fails it."""

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state=_ready_state(tmp_path / "older-app" / "julia", julia, fingerprint="older"),
        julia_on_path=str(julia),
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "unprovisioned"


def test_another_installs_failure_is_not_reported_as_this_ones(
    tmp_path, monkeypatch
) -> None:
    """Only a ready record is shared. A failure stays with the install that saw it."""

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state={
            "status": "failed",
            "backend": "cpu",
            "project": str(tmp_path / "other-install" / "julia"),
            "package_fingerprint": "abc123",
            "error": "Not enough free disk space for the CPU runtime",
        },
        julia_on_path=str(julia),
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "unprovisioned"
    assert "disk space" not in readiness.reason


def test_a_recorded_failure_is_reported_verbatim_with_a_retry(
    tmp_path, monkeypatch
) -> None:
    project = _cpu_project(tmp_path)
    state = {
        "status": "failed",
        "backend": "cpu",
        "project": str(project),
        "package_fingerprint": "abc123",
        "error": "SHA-256 mismatch for julia-1.12.6-win64.zip",
    }
    package = _install_stub_package(monkeypatch, project=project, state=state)

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "failed"
    assert "SHA-256 mismatch" in readiness.reason
    assert "--force" in readiness.reason


def test_a_fingerprint_exception_fails_closed_for_a_ready_record(tmp_path, monkeypatch):
    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    state = _ready_state(project, julia)
    package = _install_stub_package(monkeypatch, project=project, state=state)
    package_module = beat_cpu_runtime._import
    runtime = SimpleNamespace(
        default_project=lambda backend: project,
        package_fingerprint=lambda selected=None: (_ for _ in ()).throw(
            RuntimeError("identity unavailable")
        ),
    )
    monkeypatch.setattr(
        beat_cpu_runtime,
        "_import",
        lambda name: runtime if name == "hornlab_beat_bem.runtime" else package_module(name),
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "fingerprint-unavailable"


def test_a_missing_fingerprint_fails_closed_for_a_ready_record(tmp_path, monkeypatch):
    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    state = _ready_state(project, julia)
    package = _install_stub_package(monkeypatch, project=project, state=state)
    runtime = SimpleNamespace(default_project=lambda backend: project)
    monkeypatch.setattr(
        beat_cpu_runtime,
        "_import",
        lambda name: runtime if name == "hornlab_beat_bem.runtime" else (
            SimpleNamespace(read_state=lambda runtime_dir=None: state,
                            provision_cpu=object(),
                            provisioned_julia=lambda runtime_dir=None: str(julia))
            if name == "hornlab_beat_bem.provision" else package
        ),
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "fingerprint-unavailable"


def test_an_older_package_degrades_without_claiming_anything(
    tmp_path, monkeypatch
) -> None:
    """The currently pinned build has no ``provision_cpu``.

    It must not be treated as ready -- nothing in it can prove a CPU solve would
    run -- and it must not be treated as broken either. The reason names the
    commit that changes the answer, because moving the pin is a separate,
    deliberate act.
    """

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state=None,
        julia_on_path=str(julia),
        provision_cpu=None,
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "package-too-old"
    assert beat_cpu_runtime.REQUIRED_PACKAGE_COMMIT in readiness.reason


def test_a_provisioning_running_now_says_when_it_will_count(
    tmp_path, monkeypatch
) -> None:
    """The reason promises the live refresh rather than requiring a restart."""

    project = _cpu_project(tmp_path)
    package = _install_stub_package(
        monkeypatch, project=project, state={"status": "in_progress", "backend": "cpu"}
    )
    monkeypatch.setattr(
        beat_cpu_runtime, "cpu_provisioning_step", lambda: "Unpacking julia-1.12.6"
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is False
    assert readiness.state == "provisioning"
    assert "Unpacking julia-1.12.6" in readiness.reason
    assert "becomes selectable here when ready" in readiness.reason
    assert "next time" not in readiness.reason


def test_the_adapter_reports_exactly_what_the_readiness_check_found(
    tmp_path, monkeypatch
) -> None:
    """``beat_backend_statuses`` must carry this verdict, not re-derive one."""

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch, project=project, state=_ready_state(project, julia)
    )
    monkeypatch.setattr(beat, "_load_api", lambda: package)
    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: {
            "available": False,
            "reason": "No supported GPU was detected",
            "version": "0.1.0",
            "backend": None,
            "surface_traces": False,
        },
    )

    statuses = beat.beat_backend_statuses()

    assert statuses["cpu"]["available"] is True
    assert "1 kHz solve" in statuses["cpu"]["reason"]
    # The accelerator rows are untouched by any of this.
    assert [name for name, item in statuses.items() if item["available"]] == ["cpu"]


def test_an_older_package_leaves_the_other_backends_alone(tmp_path, monkeypatch) -> None:
    project = _cpu_project(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state=None,
        julia_on_path=str(_julia(tmp_path)),
        provision_cpu=None,
    )
    monkeypatch.setattr(beat, "_load_api", lambda: package)
    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: {
            "available": True,
            "reason": "Apple Silicon GPU detected and Metal.functional() confirmed",
            "version": "0.1.0",
            "backend": "metal",
            "surface_traces": True,
        },
    )

    statuses = beat.beat_backend_statuses()

    assert statuses["metal"]["available"] is True
    assert statuses["cpu"]["available"] is False
    assert beat_cpu_runtime.REQUIRED_PACKAGE_COMMIT in statuses["cpu"]["reason"]


# --------------------------------------------------------------------------
# Who provisions, and when.
# --------------------------------------------------------------------------


def _provisioning_host(tmp_path, monkeypatch, **kwargs):
    """A Windows/Linux-shaped host with an unprovisioned, provisionable package."""

    project = _cpu_project(tmp_path)
    return _install_stub_package(
        monkeypatch,
        project=project,
        state=kwargs.pop("state", None),
        julia_on_path=kwargs.pop("julia_on_path", None),
        **kwargs,
    )


def test_provisioning_starts_off_the_calling_thread_on_a_gpu_less_host(
    tmp_path, monkeypatch
) -> None:
    started: list[str] = []
    _provisioning_host(
        tmp_path, monkeypatch, detect_gpu_backend=lambda: None
    )
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: started.append("ran")
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux")

    assert thread is not None and thread.daemon
    thread.join(timeout=5.0)
    assert started == ["ran"]


@pytest.mark.parametrize(
    "found, worker_line",
    [
        (None, "BEAT GPU check: no supported GPU found; no GPU runtime prepared"),
        ("cuda", "BEAT GPU check: cuda hardware found"),
    ],
)
def test_the_log_reports_a_gpu_only_after_the_inventory_found_one(
    tmp_path, monkeypatch, caplog, found, worker_line
) -> None:
    """The start line cannot know about hardware; the worker says what it found.

    The 0.3.4 Windows rehearsal (a VM with a virtual display adapter and no
    GPU) logged "detected GPU: yes" at start, which only meant the GPU stage
    would run its check.
    """

    import logging

    project = _cpu_project(tmp_path)
    _install_stub_package(
        monkeypatch,
        project=project,
        state=None,
        backend_states={},
        detect_gpu_backend=lambda: found,
        provision_cpu=lambda runtime_dir=None, *, status_cb=print, force=False: {"status": "ready"},
        provision_gpu=lambda *_args, **_kwargs: {"status": "ready"},
        backend_ready=lambda *_args, **_kwargs: False,
    )
    caplog.set_level(logging.INFO)

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Windows")

    assert thread is not None
    thread.join(timeout=5.0)
    messages = [record.getMessage() for record in caplog.records]
    assert "Preparing BEAT runtimes in the background (CPU: yes, GPU check: yes)" in messages
    assert worker_line in messages
    assert not any("detected GPU" in message for message in messages)


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
def test_every_supported_platform_prepares_the_cpu_runtime(
    tmp_path, monkeypatch, system: str
) -> None:
    """Every supported computer has a CPU, so every one of them offers this.

    macOS used to be excluded, on the reasoning that AUTO prefers Metal there
    so a CPU runtime would never be *selected*. ``BEAT · CPU`` is an engine a
    user chooses by name, and on a Mac it was a row that could never light up,
    offering as its remedy a shell command a packaged application gives nobody
    a shell for.
    """

    started: list[str] = []
    _provisioning_host(tmp_path, monkeypatch, detect_gpu_backend=lambda: None)
    monkeypatch.setattr(beat_cpu_runtime, "_provision_worker", lambda: started.append(system))

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system=system)

    assert thread is not None, f"{system} must prepare the CPU runtime"
    thread.join(timeout=5.0)
    assert started == [system]


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
def test_a_gpu_host_prepares_it_too_on_every_platform(
    tmp_path, monkeypatch, system: str
) -> None:
    """Having a GPU is not a reason to withhold the CPU engine.

    The requirement is explicit that a usable Metal, CUDA or other accelerator
    does not satisfy it -- and on a Mac the accelerator is the *normal* case, so
    a rule that skipped GPU hosts would have skipped nearly every Mac.
    """

    project = _cpu_project(tmp_path)
    calls: list[str] = []

    def provision_cpu(runtime_dir=None, *, status_cb=print, force=False):
        calls.append("provisioned")
        return {"status": "ready"}

    _install_stub_package(
        monkeypatch,
        project=project,
        state={"status": "ready", "backend": "metal", "project": str(project)},
        backend_states={"metal": {"status": "ready", "backend": "metal", "project": str(project)}},
        detect_gpu_backend=lambda: "metal",
        provision_cpu=provision_cpu,
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system=system)

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["provisioned"]


def test_an_unsupported_platform_still_declines(tmp_path, monkeypatch) -> None:
    """The set is the platforms this application ships for, not "all of them"."""

    _provisioning_host(tmp_path, monkeypatch, detect_gpu_backend=lambda: None)
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="FreeBSD") is None


@pytest.mark.parametrize("state_name, expected", [
    ("ready", "beat-cpu"), ("provisioning", "bempp"), ("failed", "bempp"),
])
def test_auto_uses_readiness_without_waiting_for_cpu_provisioning(
    tmp_path, monkeypatch, state_name, expected
) -> None:
    import asyncio

    from server.engines.registry import EngineInfo, EngineRegistry
    from server.jobs.runtime import resolve_submission
    from server.tests.test_engines_registry import _planner_request

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    state = {**_ready_state(project, julia), "status": state_name, "error": "offline"}
    package = _install_stub_package(monkeypatch, project=project, state=state)
    monkeypatch.setattr(beat_cpu_runtime, "cpu_provisioning_step",
                        lambda: "instantiating" if state_name == "provisioning" else None)
    ready, reason = beat._cpu_backend_status(package)
    registry = EngineRegistry(
        cpu_refresh=False,
        detector=lambda: [
            EngineInfo("beat-cpu", ready, reason, "test"),
            EngineInfo("bempp", True, "ready", "test"),
        ],
        factory=lambda _name: object(),
    )

    async def exercise():
        try:
            resolution = await asyncio.wait_for(resolve_submission(_planner_request(), registry), 5)
            assert resolution.engine_name == expected
            assert await registry.unavailable_reason("beat-cpu") == reason
            if state_name != "ready":
                assert state_name in reason.lower()
        finally:
            await registry.shutdown_prewarm()

    asyncio.run(exercise())


def test_ready_cpu_preference_is_platform_independent() -> None:
    """Background preparation enables the preferred CPU route on every platform."""

    from server.engines.registry import full3d_engine_order

    for system in ("Darwin", "Windows", "Linux"):
        order = full3d_engine_order(system)
        assert order.index("beat-cpu") < order.index("bempp")
        assert order[0] == "metal"


def test_a_single_slot_package_leaves_a_gpu_host_to_its_gpu_runtime(
    tmp_path, monkeypatch
) -> None:
    """Decided on the worker thread, because ``nvidia-smi`` is a subprocess.

    The launcher must not wait on a hardware inventory the package is willing
    to give 15 s to, so the thread starts first and the decision is inside it.

    On a package that records readiness in one slot the decision is still "no":
    provisioning the CPU there would overwrite the record saying the CUDA
    runtime is ready, and the next GPU hook would re-resolve multi-gigabyte
    artifacts to get back to where it already was. Nothing is downloaded, and
    nothing ever reports itself as provisioning.
    """

    def provision_cpu(*_args, **_kwargs):
        pytest.fail("a single-slot package must not have its GPU record overwritten")

    _provisioning_host(
        tmp_path,
        monkeypatch,
        detect_gpu_backend=lambda: "cuda",
        provision_cpu=provision_cpu,
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Windows")

    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert beat_cpu_runtime.cpu_provisioning_step() is None


def test_a_gpu_host_prepares_the_cpu_runtime_too_when_records_are_per_backend(
    tmp_path, monkeypatch
) -> None:
    """The reported defect, from this side.

    ``BEAT · CPU -- no GPU needed`` is a row a user can select by name, and on
    every GPU machine it was permanently unavailable because preparation stopped
    as soon as ``nvidia-smi`` found a card. With per-backend records nothing is
    traded for it: the portable Julia is the one the GPU runtime already
    downloaded, so what remains is instantiating the CPU project and the 1 kHz
    probe solve.
    """

    project = _cpu_project(tmp_path)
    calls: list[str] = []

    def provision_cpu(runtime_dir=None, *, status_cb=print, force=False):
        calls.append("provisioned")
        return {"status": "ready"}

    _install_stub_package(
        monkeypatch,
        project=project,
        state={"status": "ready", "backend": "cuda", "project": str(project)},
        backend_states={
            "cuda": {"status": "ready", "backend": "cuda", "project": str(project)}
        },
        detect_gpu_backend=lambda: "cuda",
        provision_cpu=provision_cpu,
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["provisioned"]


def test_the_cpu_record_is_read_even_when_the_mirror_describes_the_gpu(
    tmp_path, monkeypatch
) -> None:
    """Both provisioned is now representable, so read the right record.

    The legacy mirror holds whichever backend was provisioned last. Reading
    that as the CPU answer is what made a CUDA box report ``beat-cpu``
    unprovisioned even after it had been provisioned.
    """

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    package = _install_stub_package(
        monkeypatch,
        project=project,
        state={"status": "ready", "backend": "cuda", "project": str(project)},
        backend_states={
            "cuda": {"status": "ready", "backend": "cuda", "project": str(project)},
            "cpu": _ready_state(project, julia),
        },
    )

    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert str(julia) in readiness.reason


def test_the_provisioning_command_says_it_costs_the_gpu_row_nothing(
    tmp_path, monkeypatch
) -> None:
    """The sentence a user on a GPU box acts on, and the pin it depends on.

    Against a single-slot package the same command really would overwrite the
    GPU runtime's record, so the reassurance is attached to the capability that
    makes it true rather than stated unconditionally.
    """

    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    per_backend = _install_stub_package(
        monkeypatch,
        project=project,
        state=None,
        backend_states={},
        julia_on_path=str(julia),
    )
    assert (
        "does not disturb a provisioned GPU runtime"
        in beat_cpu_runtime.cpu_runtime_readiness(per_backend).reason
    )

    single_slot = _install_stub_package(
        monkeypatch, project=project, state=None, julia_on_path=str(julia)
    )
    assert (
        "does not disturb a provisioned GPU runtime"
        not in beat_cpu_runtime.cpu_runtime_readiness(single_slot).reason
    )


def test_preparation_lifecycle_covers_delayed_gpu_inventory(
    tmp_path, monkeypatch
) -> None:
    inventory_started = threading.Event()
    release_inventory = threading.Event()
    observed: list[bool] = []

    def delayed_inventory():
        inventory_started.set()
        assert release_inventory.wait(5.0)
        return "cuda"

    _provisioning_host(
        tmp_path, monkeypatch, detect_gpu_backend=delayed_inventory
    )
    listener = lambda: observed.append(
        beat_cpu_runtime.cpu_preparation_in_flight()
    )
    beat_cpu_runtime.add_readiness_listener(listener)
    try:
        thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux")
        assert thread is not None
        assert inventory_started.wait(5.0)
        assert beat_cpu_runtime.cpu_preparation_in_flight() is True
        assert observed == [True]

        release_inventory.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert beat_cpu_runtime.cpu_preparation_in_flight() is False
        assert observed == [True, False]
    finally:
        release_inventory.set()
        beat_cpu_runtime.remove_readiness_listener(listener)


def test_a_recorded_failure_is_never_retried_automatically(tmp_path, monkeypatch) -> None:
    """One attempt, then a reason. Not a download on every launch."""

    project = _cpu_project(tmp_path)
    _install_stub_package(
        monkeypatch,
        project=project,
        state={
            "status": "failed",
            "backend": "cpu",
            "project": str(project),
            "package_fingerprint": "abc123",
            "error": "Not enough free disk space for the CPU runtime",
        },
        detect_gpu_backend=lambda: None,
    )
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux") is None


def test_an_already_provisioned_runtime_starts_nothing(tmp_path, monkeypatch) -> None:
    project = _cpu_project(tmp_path)
    _install_stub_package(
        monkeypatch,
        project=project,
        state=_ready_state(project, _julia(tmp_path)),
        detect_gpu_backend=lambda: None,
    )
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Windows") is None


def test_an_identical_copy_elsewhere_starts_nothing_either(tmp_path, monkeypatch) -> None:
    """Otherwise two installs would take the one record from each other on every launch."""

    project = _cpu_project(tmp_path)
    _install_stub_package(
        monkeypatch,
        project=project,
        state=_ready_state(tmp_path / "installed-app" / "julia", _julia(tmp_path)),
        detect_gpu_backend=lambda: None,
    )
    started: list[bool] = []
    monkeypatch.setattr(beat_cpu_runtime, "_provision_worker", lambda: started.append(True))

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin") is None
    assert started == []


def test_an_older_package_provisions_nothing(tmp_path, monkeypatch) -> None:
    _provisioning_host(
        tmp_path, monkeypatch, provision_cpu=None, detect_gpu_backend=lambda: None
    )
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux") is None


def test_the_opt_out_switch_is_honoured(tmp_path, monkeypatch) -> None:
    _provisioning_host(tmp_path, monkeypatch, detect_gpu_backend=lambda: None)
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert (
        beat_cpu_runtime.start_cpu_provisioning(
            environ={beat_cpu_runtime.SKIP_PROVISION_ENV_VAR: "1"}, system="Linux"
        )
        is None
    )


def test_a_failed_provisioning_run_is_recorded_rather_than_raised(
    tmp_path, monkeypatch
) -> None:
    """``provision_cpu`` returns its failures; anything else must still not escape."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("urllib could not resolve julialang-s3.julialang.org")

    _provisioning_host(
        tmp_path, monkeypatch, provision_cpu=explode, detect_gpu_backend=lambda: None
    )

    beat_cpu_runtime._provision_worker()  # must not raise

    assert beat_cpu_runtime.cpu_provisioning_step() is None


def test_the_provisioner_progress_becomes_the_reported_step(tmp_path, monkeypatch) -> None:
    """What the package prints while it works is what the engine row reports."""

    project = _cpu_project(tmp_path)
    seen: list[str] = []

    def provision_cpu(runtime_dir=None, *, status_cb=print, force=False):
        status_cb("Downloading julia-1.12.6-linux-x86_64.tar.gz: 40 / 275 MB")
        seen.append(beat_cpu_runtime._provision_step or "")
        return {"status": "ready"}

    _install_stub_package(
        monkeypatch,
        project=project,
        state=None,
        provision_cpu=provision_cpu,
        detect_gpu_backend=lambda: None,
    )

    beat_cpu_runtime._provision_worker()

    assert seen == ["Downloading julia-1.12.6-linux-x86_64.tar.gz: 40 / 275 MB"]
    # And the step does not outlive the run that reported it.
    assert beat_cpu_runtime._provision_step is None


def test_the_state_file_the_package_writes_is_the_one_this_reads(tmp_path) -> None:
    """A shape check against the real writer, so the field names cannot drift silently.

    ``hornlab_beat_bem.provision`` writes exactly these keys; everything this
    module concludes is a comparison between them and the installed package. If
    a future package renames one, this fails here rather than by reporting a
    provisioned runtime as unprovisioned forever.
    """

    recorded = json.loads(
        json.dumps(
            {
                "status": "ready",
                "backend": "cpu",
                "project": str(tmp_path / "julia"),
                "package_fingerprint": "0123456789abcdef",
                "julia_executable": str(tmp_path / "julia" / "bin" / "julia"),
                "step": "done",
                "julia_version": "1.12.6",
            }
        )
    )
    assert beat_cpu_runtime._matches_cpu_request(
        recorded, tmp_path / "julia", "0123456789abcdef"
    )
    assert not beat_cpu_runtime._matches_cpu_request(
        recorded, tmp_path / "julia", "other"
    )
    assert beat_cpu_runtime._proves_cpu_runtime(recorded, "0123456789abcdef")
    assert not beat_cpu_runtime._proves_cpu_runtime(recorded, "other")


def test_the_real_package_record_proves_an_identical_copy_elsewhere(
    tmp_path, monkeypatch
) -> None:
    """End to end against the installed package: its reader, file layout and hash.

    The stubs above state the contract; this holds the real package to it. A
    real copy of the package goes to another path and its fingerprint is taken
    *there*, which is what proves the one assumption the content identity rests
    on: the hash names files relative to the package, so the same files hash the
    same wherever they are installed. The record goes where the real
    ``read_state`` looks, in the per-backend file, naming that copy -- the shape
    a second install on one machine actually reads. The same record with other
    content must not count, or the first half proves nothing.
    """

    package = pytest.importorskip("hornlab_beat_bem")
    provision = pytest.importorskip("hornlab_beat_bem.provision")
    runtime = pytest.importorskip("hornlab_beat_bem.runtime")
    if not hasattr(provision, "backend_state_path"):
        pytest.skip("the pinned hornlab-beat-bem predates per-backend records")
    runtime_dir = tmp_path / "beat-runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(runtime_dir))
    julia = _julia(tmp_path)
    # The Julia is the fake's, not whatever this host has provisioned or on PATH.
    monkeypatch.setenv("HORNLAB_BEAT_JULIA", str(julia))
    here = runtime.default_project("cpu")
    copy = tmp_path / "installed-app" / "hornlab_beat_bem"
    shutil.copytree(
        runtime.PACKAGE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__")
    )
    elsewhere = copy / here.relative_to(runtime.PACKAGE_DIR)
    assert str(elsewhere) != str(here)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(runtime, "PACKAGE_DIR", copy)
            runtime.package_fingerprint.cache_clear()
            copy_fingerprint = runtime.package_fingerprint(elsewhere)
    finally:
        runtime.package_fingerprint.cache_clear()
    assert copy_fingerprint == runtime.package_fingerprint(here)
    record = _ready_state(elsewhere, julia, fingerprint=copy_fingerprint)
    record_path = provision.backend_state_path(runtime_dir, "cpu")

    record_path.write_text(json.dumps(record), encoding="utf-8")
    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)
    assert readiness.ready is True, readiness.reason
    assert str(elsewhere) in readiness.reason

    record_path.write_text(
        json.dumps({**record, "package_fingerprint": "0000000000000000"}),
        encoding="utf-8",
    )
    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)
    assert readiness.ready is False
    assert readiness.state == "unprovisioned"


# --------------------------------------------------------------------------
# The boot warmup follows the same order AUTO does.
# --------------------------------------------------------------------------


def _warmup_host(monkeypatch, *, system: str, cpu_available: bool) -> list[str]:
    """A host with no Metal and no BEAT accelerator, and a stated CPU answer."""

    import platform as platform_module

    from server.solver import bempp, metal, warmup

    warmed: list[str] = []
    monkeypatch.setattr(platform_module, "system", lambda: system)
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False, "reason": "no Metal"})
    monkeypatch.setattr(
        beat,
        "beat_status",
        lambda: {"available": False, "reason": "No supported GPU was detected"},
    )
    monkeypatch.setattr(
        beat,
        "beat_backend_statuses",
        lambda: {
            backend: {"available": backend == "cpu" and cpu_available, "reason": "test"}
            for backend in beat.BEAT_BACKENDS
        },
    )
    monkeypatch.setattr(
        bempp, "bempp_status", lambda: {"available": True, "assembly_backend": "numba"}
    )
    monkeypatch.setattr(warmup, "_warm_beat", lambda backend: warmed.append(f"beat-{backend}"))
    monkeypatch.setattr(warmup, "_warm_bempp", lambda _status: warmed.append("bempp"))
    monkeypatch.setattr(warmup, "_warm_metal", lambda: warmed.append("metal"))
    return warmed


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
def test_the_boot_warmup_warms_the_engine_auto_would_pick(monkeypatch, system: str) -> None:
    """The warmup and AUTO must agree on the measured CPU preference.

    It is the same failure ``resolve_beat_backend`` was written for, one level
    up: the warmup and the planner must not disagree about which engine the
    first solve reaches, or the user pays the cold start they were meant to be
    spared.
    """

    from server.solver import warmup

    warmed = _warmup_host(monkeypatch, system=system, cpu_available=True)

    warmup._run_warmup()

    assert warmed == ["beat-cpu"]


def test_an_unprovisioned_cpu_runtime_leaves_the_warmup_on_bempp(monkeypatch) -> None:
    from server.solver import warmup

    warmed = _warmup_host(monkeypatch, system="Linux", cpu_available=False)

    warmup._run_warmup()

    assert warmed == ["bempp"]


def test_older_gpu_record_offers_upgrade_instead_of_overwriting_command(tmp_path, monkeypatch):
    project = _cpu_project(tmp_path)
    package = _install_stub_package(
        monkeypatch, project=project, state={"backend": "cuda", "status": "ready"},
    )
    readiness = beat_cpu_runtime.cpu_runtime_readiness(package)
    assert readiness.ready is False
    assert "Update Waveguide Generator" in readiness.reason
    assert "interrupt GPU availability" in readiness.reason
    assert "--backend cpu" not in readiness.reason


# --------------------------------------------------------------------------
# The GPU runtime, prepared after the CPU one.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_preparation_flag():
    """A worker run here must not make the registry re-probe in later tests."""

    beat_cpu_runtime._runtimes_prepared = False
    yield
    beat_cpu_runtime._runtimes_prepared = False


def _gpu_host(tmp_path, monkeypatch, *, cpu_ready=True, gpu_states=None, provision_gpu=None,
              provision_cpu=None, gpu="metal"):
    project = tmp_path / "package" / "julia"
    if not project.exists():
        project = _cpu_project(tmp_path)
    julia = tmp_path / "runtime" / "julia-1.12.6" / "bin" / "julia"
    if not julia.exists():
        julia = _julia(tmp_path)
    states = dict(gpu_states or {})
    if cpu_ready:
        states["cpu"] = _ready_state(project, julia)
    calls: list[str] = []

    def default_provision_gpu(runtime_dir=None, *, backend, status_cb=print, force=False):
        calls.append(backend)
        return {"status": "ready"}

    def default_provision_cpu(runtime_dir=None, *, status_cb=print, force=False):
        calls.append("cpu")
        return {"status": "ready"}

    _install_stub_package(
        monkeypatch,
        project=project,
        state=states.get("cpu"),
        backend_states=states,
        detect_gpu_backend=lambda: gpu,
        provision_cpu=provision_cpu or default_provision_cpu,
        provision_gpu=provision_gpu or default_provision_gpu,
        backend_ready=lambda backend, runtime_dir=None: (
            (states.get(backend) or {}).get("status") == "ready"
        ),
    )
    return calls


@pytest.mark.parametrize("gpu", ["metal", "cuda"])
def test_a_packaged_gpu_host_prepares_its_gpu_runtime(tmp_path, monkeypatch, gpu) -> None:
    """The reported defect: nothing in the packaged app ever prepared BEAT Metal.

    ``scripts/bootstrap.py --if-gpu`` is the only other place that runs, and
    the packaged application never runs it, so BEAT Metal/CUDA stayed
    unavailable for good on an installed copy.
    """

    calls = _gpu_host(tmp_path, monkeypatch, gpu=gpu)

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == [gpu]


def test_the_cpu_runtime_is_prepared_before_the_gpu_one(tmp_path, monkeypatch) -> None:
    calls = _gpu_host(tmp_path, monkeypatch, cpu_ready=False)

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["cpu", "metal"]


def test_a_ready_gpu_runtime_is_not_provisioned_again(tmp_path, monkeypatch) -> None:
    calls = _gpu_host(
        tmp_path, monkeypatch,
        gpu_states={"metal": {"status": "ready", "backend": "metal"}},
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == []
    assert beat_cpu_runtime.runtimes_prepared_this_process() is False


def test_a_gpu_failure_for_this_build_is_never_retried(tmp_path, monkeypatch) -> None:
    project = _cpu_project(tmp_path)
    calls = _gpu_host(
        tmp_path, monkeypatch,
        gpu_states={"cuda": {
            "status": "failed", "backend": "cuda", "project": str(project),
            "package_fingerprint": "abc123", "error": "offline",
        }},
        gpu="cuda",
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Windows")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == []


def test_another_gpu_familys_record_does_not_block_the_detected_one(
    tmp_path, monkeypatch
) -> None:
    """A replaced or second card: a CUDA verdict says nothing about ROCm."""

    project = _cpu_project(tmp_path)
    calls = _gpu_host(
        tmp_path, monkeypatch,
        gpu_states={
            "cuda": {
                "status": "failed", "backend": "cuda", "project": str(project),
                "package_fingerprint": "abc123", "error": "offline",
            },
            "metal": {"status": "ready", "backend": "metal"},
        },
        gpu="rocm",
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["rocm"]
    assert beat_cpu_runtime.runtimes_prepared_this_process() is True


def test_a_gpu_failure_from_another_build_is_retried(tmp_path, monkeypatch) -> None:
    project = _cpu_project(tmp_path)
    calls = _gpu_host(
        tmp_path, monkeypatch,
        gpu_states={"metal": {
            "status": "failed", "backend": "metal", "project": str(project),
            "package_fingerprint": "older-build", "error": "offline",
        }},
    )

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["metal"]


def test_the_gpu_opt_out_leaves_the_cpu_stage_alone(tmp_path, monkeypatch) -> None:
    calls = _gpu_host(tmp_path, monkeypatch, cpu_ready=False)
    env = {beat_cpu_runtime.SKIP_GPU_PROVISION_ENV_VAR: "1"}

    thread = beat_cpu_runtime.start_cpu_provisioning(environ=env, system="Darwin")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == ["cpu"]

    _gpu_host(tmp_path, monkeypatch)
    assert beat_cpu_runtime.start_cpu_provisioning(environ=env, system="Darwin") is None


def test_a_gpu_less_host_runs_no_gpu_stage(tmp_path, monkeypatch) -> None:
    calls = _gpu_host(tmp_path, monkeypatch, gpu=None)

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Linux")

    assert thread is not None
    thread.join(timeout=5.0)
    assert calls == []
    assert beat_cpu_runtime.cpu_preparation_in_flight() is False


def test_the_gpu_stage_reports_itself_and_not_as_cpu_provisioning(tmp_path, monkeypatch) -> None:
    """During the GPU stage the CPU row must not claim it is provisioning."""

    in_stage = threading.Event()
    release = threading.Event()
    seen: dict[str, object] = {}

    def provision_gpu(runtime_dir=None, *, backend, status_cb=print, force=False):
        status_cb("Instantiating the Julia Metal environment")
        seen["gpu_reason"] = beat_cpu_runtime.gpu_preparation_reason("metal")
        seen["cuda_reason"] = beat_cpu_runtime.gpu_preparation_reason("cuda")
        seen["cpu_step"] = beat_cpu_runtime.cpu_provisioning_step()
        in_stage.set()
        assert release.wait(5.0)
        return {"status": "ready"}

    _gpu_host(tmp_path, monkeypatch, cpu_ready=False, provision_gpu=provision_gpu)
    try:
        thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")
        assert thread is not None
        assert in_stage.wait(5.0)
        assert beat_cpu_runtime.cpu_preparation_in_flight() is True
    finally:
        release.set()
    thread.join(timeout=5.0)

    assert "preparing the BEAT metal runtime" in str(seen["gpu_reason"])
    assert "Instantiating the Julia Metal environment" in str(seen["gpu_reason"])
    assert seen["cuda_reason"] is None
    assert seen["cpu_step"] is None
    assert beat_cpu_runtime.gpu_preparation_reason("metal") is None


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda"])
@pytest.mark.parametrize("saved", ["auto", "explicit"])
def test_interrupted_runtime_is_redone_before_background_prewarm(
    tmp_path, monkeypatch, backend, saved
) -> None:
    """Restart with a partial record, then warm after the refreshed row is ready.

    Both the provisioning work and its capability publication are parked
    independently. No Julia is run; a fake first solve only checks whether the
    startup hook has already paid its compile cost.
    """

    from server.app import beat_worker_prewarm
    from server.engines import registry as registry_module
    from server.solver import warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    project = _cpu_project(tmp_path)
    julia = _julia(tmp_path)
    states = {"cpu": _ready_state(project, julia)}
    states[backend] = {
        **_ready_state(project, julia), "backend": backend,
        "status": "in_progress", "step": "cpu_probe" if backend == "cpu" else "instantiate",
    }
    preparing = threading.Event()
    finish_provision = threading.Event()
    publishing = threading.Event()
    finish_publish = threading.Event()
    calls = []
    warmed = []

    def provision(*_args, status_cb=print, **_kwargs):
        calls.append((backend, threading.current_thread().name))
        status_cb("Resuming interrupted preparation")
        preparing.set()
        assert finish_provision.wait(3)
        states[backend] = {**states[backend], "status": "ready", "step": "done"}
        return states[backend]

    package = _install_stub_package(
        monkeypatch, project=project, state=states["cpu"], backend_states=states,
        provision_cpu=provision, provision_gpu=provision,
        detect_gpu_backend=lambda: None if backend == "cpu" else backend,
        backend_ready=lambda selected: states[selected]["status"] == "ready",
    )
    monkeypatch.setattr(beat, "_load_api", lambda: package)

    def refresh(*_args):
        if states[backend]["status"] == "ready":
            publishing.set()
            assert finish_publish.wait(3)
        return {f"beat-{backend}": (states[backend]["status"] == "ready", "fake readiness")}

    monkeypatch.setattr(registry_module, "_beat_row_updates", refresh)
    monkeypatch.setattr(
        warmup, "prewarm_beat_worker_for_engine",
        lambda engine: warmed.append(engine) or True,
    )
    settings = SimpleNamespace(get=lambda _key: {"state": {"engine": (
        "auto" if saved == "auto" else f"beat-{backend}"
    )}})

    async def wait_event(event):
        async with asyncio.timeout(2):
            while not event.is_set():
                await asyncio.sleep(0.01)

    async def scenario():
        registry = registry_module.EngineRegistry(
            detector=lambda: [
                registry_module.EngineInfo(f"beat-{backend}", False, "partial runtime", None),
                registry_module.EngineInfo("bempp", True, "fallback", None),
            ],
            cpu_refresh=True,
        )
        task = None
        try:
            # Cache the partial startup verdict before preparation completes.
            await registry.capabilities()
            task = asyncio.create_task(beat_worker_prewarm(registry, settings))
            await asyncio.sleep(0.02)
            assert warmed == []
            finish_provision.set()
            await wait_event(publishing)
            assert warmed == [], "prewarm must wait for capability publication too"
            finish_publish.set()
            await asyncio.wait_for(task, 2)
            assert warmed == [f"beat-{backend}"]
            # A fake first solve consumes the same warmed worker.
            assert await registry.resolve(f"beat-{backend}", solver_mode=None) in warmed
        finally:
            finish_provision.set()
            finish_publish.set()
            if task is not None and not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await registry.shutdown_prewarm()

    thread = beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin")
    assert thread is not None
    try:
        assert preparing.wait(2)
        asyncio.run(scenario())
    finally:
        finish_provision.set()
        finish_publish.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert calls == [(backend, beat_cpu_runtime.PROVISION_THREAD_NAME)]


@pytest.fixture(autouse=True)
def _hbb_rollback_provider():
    """The fake package/provisioner in this module exercises the rollback route."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("WG2_BEAT_PROVIDER", "hbb")
        yield
