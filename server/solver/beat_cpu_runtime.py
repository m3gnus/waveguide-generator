"""Whether BEAT's CPU backend can really solve here, and getting it there.

Two things live in this module, and they are one subject: what a *provisioned*
BEAT CPU runtime is, and who provisions it.

**Readiness.** The question the picker asks about ``beat-cpu`` used to be "is
there a Julia executable, and is the bundled project on disk". Both can be true
on a machine where the first solve then fails: the Julia project has a
checked-in Manifest, but nothing has downloaded the packages that Manifest
names, and an offline host discovers that inside the user's job rather than in
a capability row. ``hornlab_beat_bem.provision`` answers the real question --
its CPU path instantiates the project and then *solves a 1 kHz probe* through
the precompiled engine bundle before it records ``ready`` -- so readiness here
is that record, matched against the backend, project and package fingerprint it
was recorded for. Anything else is reported unavailable with the reason and the
command that fixes it.

**Provisioning.** ``scripts/bootstrap.py`` covers the source install, where an
installer run is the natural place to spend the download. The packaged
application has no such step: it ships a prebuilt runtime layer with
``hornlab-beat-bem`` already in it and never runs the bootstrap, so on a
GPU-less Windows or Linux box nothing would ever fetch Julia and the CPU engine
would stay permanently unavailable. ``start_cpu_provisioning`` is that missing
step, and its constraints come from where it runs:

* It never blocks startup. A daemon thread does the work -- including the GPU
  hardware inventory that decides whether to do any of it, because that one
  shells out to ``nvidia-smi`` -- and the server serves while it runs.
* It never starts GPU work. ``provision_cpu`` instantiates the CPU project,
  which depends on no accelerator package, so this cannot turn into the
  multi-gigabyte CUDA/ROCm artifact pull that ``--if-gpu`` is gated on.
* It runs on Windows and Linux only. macOS is not a host that needs it: Apple
  Silicon provisions Metal through the GPU hook and AUTO prefers Metal there on
  measured evidence, so downloading a Julia for a backend that would not be
  selected is cost without a user.
* It does not retry a failure. A recorded failure for this same build is
  reported, not re-run, so a metered or offline machine pays the attempt once
  and reads why instead of re-downloading on every launch.
* ``WG2_SKIP_BEAT_CPU_PROVISION=1`` switches it off entirely.

Readiness changes are published to ``EngineRegistry`` while the process is
running. The interface polls only while this background preparation thread is
alive, so a completed CPU runtime becomes selectable without restarting the
application and a failed or skipped preparation stops polling.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
import os
from pathlib import Path
import platform
import sys
import threading
from typing import Any, Mapping


log = logging.getLogger("wg.solver.beat")

#: The BEAT backend name this module is about. Spelled here rather than
#: imported from ``server.solver.beat`` so that module can import this one.
CPU_BACKEND = "cpu"

#: Opt out of the background provisioning described above.
SKIP_PROVISION_ENV_VAR = "WG2_SKIP_BEAT_CPU_PROVISION"

#: Where the CPU runtime is provisioned automatically. See the module docstring.
PROVISION_SYSTEMS: frozenset[str] = frozenset({"Windows", "Linux"})

#: The ``hornlab-beat-bem`` commit that introduced ``provision.provision_cpu``
#: and its ``--backend cpu`` CLI. Named in the unavailable reason because the
#: pin in ``server/requirements-pins.txt`` is older than this and is updated by
#: the landing role, not here: until it moves, this path degrades to an honest
#: "cannot prove it" rather than to a wrong "ready".
REQUIRED_PACKAGE_COMMIT = "ac48d90"

#: Thread name, so a stack dump from ``faulthandler`` names this work.
PROVISION_THREAD_NAME = "wg2-beat-cpu-provision"

_provision_lock = threading.Lock()
_provision_thread: threading.Thread | None = None
_provision_step: str | None = None
_preparation_in_flight = False
_readiness_listeners: list[Any] = []


@dataclass(frozen=True, slots=True)
class CpuRuntimeReadiness:
    """A verdict about the BEAT CPU runtime and the sentence explaining it.

    ``state`` is the machine-readable half -- tests and the provisioning gate
    switch on it -- while ``reason`` is what a user reads on a greyed-out row.
    """

    ready: bool
    state: str
    reason: str


def _import(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except (ImportError, OSError):
        return None


def provision_command() -> str:
    """The exact command that provisions the CPU runtime on this install.

    The interpreter is named rather than assumed: the packaged application's
    Python is not on PATH, so a bare ``python -m ...`` in a capability reason
    would send a user to whichever interpreter their shell finds, which is not
    the one that has ``hornlab-beat-bem`` installed.
    """

    executable = sys.executable or "python"
    if " " in executable:
        executable = f'"{executable}"'
    return f"{executable} -m hornlab_beat_bem.provision --backend cpu"


def cpu_provisioning_step() -> str | None:
    """The step a provisioning running *in this process* has reached, if any.

    Only set once the work has actually begun, which is not the same instant
    the thread starts: the thread's first act is a hardware inventory that may
    decide not to provision at all, and reporting "provisioning" during it
    would be a claim about a download that never happens.
    """

    with _provision_lock:
        if _provision_thread is not None and _provision_thread.is_alive():
            return _provision_step
    return None


def cpu_preparation_in_flight() -> bool:
    """Whether CPU preparation is still deciding or provisioning.

    This covers the whole worker lifetime, including the potentially slow GPU
    hardware inventory before a provisioning step exists. It is the explicit
    lifecycle signal consumed by the capabilities endpoint and browser.
    """

    with _provision_lock:
        return _preparation_in_flight


def _record_step(step: str | None) -> None:
    global _provision_step
    with _provision_lock:
        _provision_step = step


def add_readiness_listener(listener: Any) -> None:
    with _provision_lock:
        if listener not in _readiness_listeners:
            _readiness_listeners.append(listener)


def remove_readiness_listener(listener: Any) -> None:
    with _provision_lock:
        if listener in _readiness_listeners:
            _readiness_listeners.remove(listener)


def _notify_readiness_listeners() -> None:
    with _provision_lock:
        listeners = tuple(_readiness_listeners)
    for listener in listeners:
        try:
            listener()
        except Exception:  # noqa: BLE001
            log.debug("CPU readiness listener failed", exc_info=True)


def _matches_cpu_request(
    state: Mapping[str, Any], project: Path, fingerprint: str | None
) -> bool:
    """Whether a recorded state describes *this* package's CPU runtime.

    The same three fields ``provision._ready_for`` compares, for the same
    reasons: a CUDA-ready runtime does not satisfy a CPU request, a package
    reinstalled into a different prefix leaves the recorded project behind, and
    a content fingerprint is what notices an in-place update of the solver
    underneath an unchanged version string.
    """

    if state.get("backend") != CPU_BACKEND:
        return False
    if state.get("project") != str(project):
        return False
    if fingerprint is not None and state.get("package_fingerprint") != fingerprint:
        return False
    return True


def _cpu_project_and_fingerprint(runtime: Any) -> tuple[Path | None, str | None]:
    """The bundled CPU project and its content fingerprint, best effort.

    Both come from the optional package, so both are allowed to be missing: a
    build that cannot say which project it would instantiate cannot be shown to
    be ready either, and that is exactly what the callers do with ``None``.
    """

    try:
        project = Path(runtime.default_project(CPU_BACKEND))
    except Exception:  # noqa: BLE001 - an unlocatable project is not a crash
        return None, None
    try:
        return project, str(runtime.package_fingerprint(project))
    except Exception:  # noqa: BLE001 - an older signature is not a crash
        return project, None


def cpu_runtime_readiness(package: Any) -> CpuRuntimeReadiness:
    """Whether a BEAT CPU solve can start here, without paying a Julia startup.

    Cheap on purpose, and cheap in the same way the accelerator rows are not:
    they pay a Julia launch to ask ``CUDA``/``Metal``.``functional()`` because a
    device can be present and broken. The CPU path has no device -- what it has
    is a runtime that may never have been instantiated -- so the evidence is the
    provisioning record, which is one small JSON read plus a content hash of the
    package's own files, rather than a second Julia launch on every boot.
    """

    provision = _import("hornlab_beat_bem.provision")
    runtime = _import("hornlab_beat_bem.runtime")
    if provision is None or runtime is None:
        return CpuRuntimeReadiness(
            False,
            "package-unusable",
            "hornlab-beat-bem is installed but its provisioning module could not "
            "be imported, so nothing here can say whether a BEAT CPU solve would run.",
        )
    if not hasattr(provision, "provision_cpu"):
        return CpuRuntimeReadiness(
            False,
            "package-too-old",
            "The installed hornlab-beat-bem cannot provision a BEAT CPU runtime: "
            f"provision_cpu arrived in {REQUIRED_PACKAGE_COMMIT} and this build "
            "predates it. A Julia executable and the bundled project on disk are "
            "not evidence that a solve would run, so this backend stays "
            "unavailable until the pinned package is updated. Every other BEAT "
            "backend is unaffected.",
        )

    project, fingerprint = _cpu_project_and_fingerprint(runtime)
    if project is None:
        return CpuRuntimeReadiness(
            False,
            "no-project",
            "The bundled BEAT CPU Julia project could not be located in the "
            "installed hornlab-beat-bem.",
        )
    if not (project / "Project.toml").exists():
        return CpuRuntimeReadiness(
            False,
            "no-project",
            f"The bundled BEAT CPU Julia project is missing: {project}",
        )
    if fingerprint is None:
        return CpuRuntimeReadiness(
            False,
            "fingerprint-unavailable",
            "The BEAT CPU package identity could not be verified, so a recorded "
            "provisioning result is not trusted. Run: "
            f"{provision_command()} --force",
        )

    step = cpu_provisioning_step()
    if step is not None:
        return CpuRuntimeReadiness(
            False,
            "provisioning",
            "Waveguide Generator is provisioning the BEAT CPU runtime now "
            f"(step: {step}); it downloads a portable Julia and proves the "
            "engine with a 1 kHz solve. It becomes selectable here when ready.",
        )

    state = provision.read_state() or {}
    status = state.get("status")
    if status == "ready" and _matches_cpu_request(state, project, fingerprint):
        julia = provision.provisioned_julia()
        if julia is not None:
            return CpuRuntimeReadiness(
                True,
                "ready",
                "The BEAT CPU runtime is provisioned: its Julia project was "
                "instantiated and proved with a 1 kHz solve through the "
                f"precompiled engine bundle ({julia}). No accelerator needed.",
            )
        return CpuRuntimeReadiness(
            False,
            "julia-gone",
            "The BEAT CPU runtime was provisioned, but the Julia it recorded is "
            f"no longer on disk. Provision it again: {provision_command()}",
        )
    if status == "failed" and _matches_cpu_request(state, project, fingerprint):
        return CpuRuntimeReadiness(
            False,
            "failed",
            "BEAT CPU runtime provisioning failed here: "
            f"{state.get('error') or 'no reason was recorded'}. It is not "
            f"retried automatically. Retry with: {provision_command()} --force",
        )
    if status == "in_progress" and _matches_cpu_request(state, project, fingerprint):
        return CpuRuntimeReadiness(
            False,
            "interrupted",
            "An earlier BEAT CPU runtime provisioning did not finish (it stopped "
            f"at step: {state.get('step', 'unknown')}). Run it again: "
            f"{provision_command()}",
        )

    try:
        julia = package.discover_julia()
    except Exception as exc:  # noqa: BLE001 - a broken optional stack is unavailable
        return CpuRuntimeReadiness(
            False, "detection-failed", f"BEAT CPU detection failed: {exc}"
        )
    if julia is None:
        return CpuRuntimeReadiness(
            False,
            "no-julia",
            "No Julia executable was found and the BEAT CPU runtime has not been "
            f"provisioned here. Run: {provision_command()} -- it downloads a "
            "portable Julia, instantiates the CPU project, and proves it with a "
            "1 kHz solve.",
        )
    return CpuRuntimeReadiness(
        False,
        "unprovisioned",
        f"Julia is present ({julia}) but the BEAT CPU runtime has not been "
        "instantiated and probed here, and a Julia executable alone is not "
        "evidence that a solve would run -- an uninstantiated or offline depot "
        f"fails at the first solve instead. Run: {provision_command()}",
    )


def _provision_worker() -> None:
    """Decide on the hardware, then run ``provision_cpu``, reporting to the log.

    The hardware inventory is *here* rather than in the caller because
    ``nvidia-smi`` is a subprocess the package is willing to wait 15 s for, and
    the caller is a launcher that has a server to start. Nothing about it is
    urgent: it only decides whether this host wants a CPU runtime at all.
    """

    try:
        provision = _import("hornlab_beat_bem.provision")
        if provision is None:  # pragma: no cover - caller already imported it
            return
        gpu = _gpu_backend_present(provision)
        if gpu is not None:
            log.info("BEAT %s hardware found; CPU preparation skipped", gpu)
            return
        _record_step("starting")
        try:
            state = provision.provision_cpu(status_cb=_provision_status)
        except Exception as exc:  # noqa: BLE001 - provisioning is never fatal here
            # provision_cpu records and returns its own failures, so reaching
            # this means something outside its contract broke. The final
            # notification still lets the registry publish the terminal state.
            log.warning("BEAT CPU runtime provisioning could not run: %s", exc)
            return
        status = str(state.get("status") or "unknown")
        if status == "ready":
            log.info("BEAT CPU runtime is ready")
        else:
            log.warning(
                "BEAT CPU runtime provisioning finished as %s: %s",
                status,
                state.get("error") or "no reason was recorded",
            )
    finally:
        global _preparation_in_flight, _provision_step
        with _provision_lock:
            _provision_step = None
            _preparation_in_flight = False
        _notify_readiness_listeners()


def _provision_status(message: str) -> None:
    """Forward the provisioner's own progress into the application log.

    Its status callback defaults to ``print``, which in the packaged
    application writes to a pipe the status window drains; the log is where a
    user is already sent for "why is this not available", so send it there.
    """

    _record_step(message)
    _notify_readiness_listeners()
    log.info("BEAT CPU runtime provisioning: %s", message)


def _gpu_backend_present(provision: Any) -> str | None:
    try:
        return provision.detect_gpu_backend()
    except Exception as exc:  # noqa: BLE001 - an inventory failure is not a crash
        log.info("BEAT GPU hardware inventory failed (%s); assuming none", exc)
        return None


def start_cpu_provisioning(
    *, environ: Mapping[str, str] | None = None, system: str | None = None
) -> threading.Thread | None:
    """Provision the BEAT CPU runtime in the background when this host needs one.

    Returns the live thread, or ``None`` when nothing was started -- which is
    the common case and never an error. Every gate is logged, because "why did
    my machine not download that" is a question the log has to be able to answer.
    """

    global _preparation_in_flight, _provision_thread, _provision_step

    env = os.environ if environ is None else environ
    if str(env.get(SKIP_PROVISION_ENV_VAR, "")).strip() == "1":
        log.info("BEAT CPU runtime provisioning disabled by %s=1", SKIP_PROVISION_ENV_VAR)
        return None
    host = system or platform.system()
    if host not in PROVISION_SYSTEMS:
        log.debug(
            "BEAT CPU runtime provisioning is not offered on %s; AUTO prefers the "
            "measured Metal path there and the GPU hook provisions it",
            host,
        )
        return None

    package = _import("hornlab_beat_bem")
    provision = _import("hornlab_beat_bem.provision")
    if package is None or provision is None:
        log.info("BEAT is not installed here; no CPU runtime to provision")
        return None
    if not hasattr(provision, "provision_cpu"):
        log.info(
            "The installed hornlab-beat-bem predates CPU runtime provisioning "
            "(needs %s); leaving the BEAT CPU engine unavailable",
            REQUIRED_PACKAGE_COMMIT,
        )
        return None

    readiness = cpu_runtime_readiness(package)
    if readiness.ready:
        log.debug("BEAT CPU runtime is already provisioned")
        return None
    if readiness.state in {"provisioning", "failed", "no-project", "package-unusable"}:
        # A failure is reported, not repeated: re-downloading a portable Julia
        # on every launch of a machine that is offline or out of disk is worse
        # than one honest unavailable row carrying the retry command.
        log.info("Not provisioning the BEAT CPU runtime: %s", readiness.reason)
        return None

    started: threading.Thread
    with _provision_lock:
        if _provision_thread is not None and _provision_thread.is_alive():
            return _provision_thread
        _provision_step = None
        _preparation_in_flight = True
        _provision_thread = threading.Thread(
            target=_provision_worker, name=PROVISION_THREAD_NAME, daemon=True
        )
        _provision_thread.start()
        started = _provision_thread
    # Listener notification takes the same lock to snapshot its callbacks.
    # Publish START after releasing it, while the just-started thread is live.
    _notify_readiness_listeners()
    log.info("Preparing the BEAT CPU runtime in the background")
    return started


__all__ = [
    "CPU_BACKEND",
    "CpuRuntimeReadiness",
    "PROVISION_SYSTEMS",
    "PROVISION_THREAD_NAME",
    "REQUIRED_PACKAGE_COMMIT",
    "SKIP_PROVISION_ENV_VAR",
    "cpu_provisioning_step",
    "cpu_preparation_in_flight",
    "cpu_runtime_readiness",
    "provision_command",
    "start_cpu_provisioning",
]
