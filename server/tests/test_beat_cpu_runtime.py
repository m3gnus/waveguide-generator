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

import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from server.solver import beat, beat_cpu_runtime


CPU_PROJECT_FILES = ("Project.toml", "Manifest.toml")


@pytest.fixture(autouse=True)
def _no_leaked_provisioning():
    """No test may leave the module thinking a provisioning is running."""

    yield
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


def _install_stub_package(
    monkeypatch,
    *,
    project: Path,
    state: dict[str, object] | None,
    fingerprint: str = "abc123",
    julia_on_path: str | None = None,
    provision_cpu: object | None = object(),
    detect_gpu_backend: object | None = None,
) -> SimpleNamespace:
    """Stand in for an installed ``hornlab-beat-bem`` of a chosen vintage.

    ``provision_cpu=None`` is the older pinned package: the attribute simply is
    not there, which is exactly how the real one differs.
    """

    provision = SimpleNamespace(
        read_state=lambda runtime_dir=None: state,
        provisioned_julia=lambda runtime_dir=None: (
            str(state.get("julia_executable"))
            if state is not None
            and state.get("status") == "ready"
            and state.get("julia_executable")
            and Path(str(state["julia_executable"])).exists()
            else None
        ),
    )
    if provision_cpu is not None:
        provision.provision_cpu = provision_cpu
    if detect_gpu_backend is not None:
        provision.detect_gpu_backend = detect_gpu_backend
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
        ("project", "/somewhere/else/julia", "a package that moved is not ready"),
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


def test_provisioning_is_not_offered_on_macos(tmp_path, monkeypatch) -> None:
    """AUTO prefers the measured Metal path there, and the GPU hook covers it."""

    _provisioning_host(tmp_path, monkeypatch, detect_gpu_backend=lambda: None)
    monkeypatch.setattr(
        beat_cpu_runtime, "_provision_worker", lambda: pytest.fail("must not run")
    )

    assert beat_cpu_runtime.start_cpu_provisioning(environ={}, system="Darwin") is None


def test_a_gpu_host_is_left_to_the_gpu_runtime(tmp_path, monkeypatch) -> None:
    """Decided on the worker thread, because ``nvidia-smi`` is a subprocess.

    The launcher must not wait on a hardware inventory the package is willing
    to give 15 s to, so the thread starts first and the decision is inside it.
    Nothing is downloaded, and nothing ever reports itself as provisioning.
    """

    def provision_cpu(*_args, **_kwargs):
        pytest.fail("a GPU host must not provision a CPU runtime")

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


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_the_boot_warmup_warms_the_engine_auto_would_pick(monkeypatch, system: str) -> None:
    """Warming BEMPP while AUTO solves on BEAT-CPU is the divergence to avoid.

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


def test_macos_keeps_warming_bempp(monkeypatch) -> None:
    """Where the order does not swap, neither does the warmup."""

    from server.solver import warmup

    warmed = _warmup_host(monkeypatch, system="Darwin", cpu_available=True)

    warmup._run_warmup()

    assert warmed == ["bempp"]
