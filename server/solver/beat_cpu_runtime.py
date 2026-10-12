"""Readiness and background preparation for the official BEAT CPU/GPU runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
import os
from pathlib import Path
import platform
import sys
import shlex
import subprocess
import threading
from typing import Any, Mapping

log = logging.getLogger("wg.solver.beat")

#: The BEAT backend name this module is about. Spelled here rather than
#: imported from ``server.solver.beat`` so that module can import this one.
CPU_BACKEND = "cpu"

#: Opt out of the background provisioning described above.
SKIP_PROVISION_ENV_VAR = "WG2_SKIP_BEAT_CPU_PROVISION"

#: Opt out of the GPU stage only. Same name ``scripts/bootstrap.py`` honours.
SKIP_GPU_PROVISION_ENV_VAR = "WG2_SKIP_GPU_PROVISION"

#: The accelerator backends ``provision_gpu`` accepts.
GPU_BACKENDS: tuple[str, ...] = ("cuda", "rocm", "metal")

#: Where the CPU runtime is provisioned automatically: every desktop platform
#: this application ships for. See the module docstring for why macOS is in the
#: set even though AUTO never prefers a CPU solve there.
PROVISION_SYSTEMS: frozenset[str] = frozenset({"Windows", "Linux", "Darwin"})

#: Thread name, so a stack dump from ``faulthandler`` names this work.
PROVISION_THREAD_NAME = "wg2-beat-cpu-provision"

_provision_lock = threading.Lock()
_provision_thread: threading.Thread | None = None
_provision_step: str | None = None
_preparation_in_flight = False
_official_cpu_stage_pending = False
_prepare_cpu = True
_prepare_gpu = False
_gpu_stage_backend: str | None = None
_gpu_stage_step: str | None = None
_runtimes_prepared = False
_requested_backend: str | None = None
_preparation_errors: dict[str, str] = {}
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


def provision_command(*, production: bool = False, backend: str = "cpu") -> str:
    """The exact command that provisions a BEAT runtime (CPU by default) on this install.

    The interpreter is named rather than assumed: the packaged application's
    Python is not on PATH, so a bare ``python -m ...`` in a capability reason
    would send a user to whichever interpreter their shell finds, which is not
    the one that has ``beat-engine`` installed.
    """

    executable = sys.executable or "python"
    from server.platform.paths import app_root

    code = (f"import sys, runpy; sys.path.insert(0, {str(app_root())!r}); "
            "runpy.run_module('server.solver.beat_runtime.cli', run_name='__main__')")
    command = [executable, "-c", code, "--backend", backend]
    return subprocess.list2cmdline(command) if platform.system() == "Windows" else shlex.join(command)


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


def gpu_preparation_reason(backend: str) -> str | None:
    """The sentence for a GPU row whose runtime this process is preparing now.

    ``None`` unless the worker has reached the GPU stage for exactly this
    backend: before that it may still decide there is nothing to prepare.
    """

    with _provision_lock:
        if _gpu_stage_backend != backend:
            return None
        step = _gpu_stage_step
    detail = f" (step: {step})" if step else ""
    return (
        f"Waveguide Generator is preparing the BEAT {backend} runtime now"
        f"{detail}. It becomes selectable here when ready."
    )


def runtimes_prepared_this_process() -> bool:
    """Whether this process has run a CPU or GPU provisioning step.

    The registry's cue that the GPU rows it detected at startup may describe a
    host that no longer exists -- one without a Julia, or without the GPU
    runtime -- and must be probed again rather than kept.
    """

    with _provision_lock:
        return _runtimes_prepared


def _mark_runtimes_prepared() -> None:
    global _runtimes_prepared
    with _provision_lock:
        _runtimes_prepared = True


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
        changed = _provision_step != step
        _provision_step = step
    if changed:
        _notify_readiness_listeners()


def add_readiness_listener(listener: Any) -> None:
    from .beat_runtime import readiness

    readiness.add_readiness_listener(_official_readiness_changed)
    with _provision_lock:
        if listener not in _readiness_listeners:
            _readiness_listeners.append(listener)


def remove_readiness_listener(listener: Any) -> None:
    with _provision_lock:
        if listener in _readiness_listeners:
            _readiness_listeners.remove(listener)
        empty = not _readiness_listeners
    if (empty):
        from .beat_runtime import readiness

        readiness.remove_readiness_listener(_official_readiness_changed)


def _official_readiness_changed() -> None:
    _notify_readiness_listeners()


def _notify_readiness_listeners() -> None:
    with _provision_lock:
        listeners = tuple(_readiness_listeners)
    for listener in listeners:
        try:
            listener()
        except Exception:  # noqa: BLE001
            log.debug("CPU readiness listener failed", exc_info=True)


def cpu_runtime_readiness(package: Any, *, production: bool = False) -> CpuRuntimeReadiness:
    """Whether a BEAT CPU solve can start here, without paying a Julia startup.

    Cheap on purpose, and cheap in the same way the accelerator rows are not:
    they pay a Julia launch to ask ``CUDA``/``Metal``.``functional()`` because a
    device can be present and broken. The CPU path has no device -- what it has
    is a runtime that may never have been instantiated -- so the evidence is the
    provisioning record, which is one small JSON read plus a content hash of the
    package's own files, rather than a second Julia launch on every boot.
    """

    with _provision_lock:
        preparing_cpu = _official_cpu_stage_pending
    if preparing_cpu:
        step = cpu_provisioning_step() or "starting"
        return CpuRuntimeReadiness(False, "provisioning", f"WG is preparing the BEAT CPU runtime (step: {step}).")
    from .beat_runtime.readiness import backend_readiness

    verdict = backend_readiness(CPU_BACKEND)
    return CpuRuntimeReadiness(verdict.ready, verdict.state, verdict.reason)


def _provision_status(message: str) -> None:
    """Forward the provisioner's own progress into the application log.

    Its status callback defaults to ``print``, which in the packaged
    application writes to a pipe the status window drains; the log is where a
    user is already sent for "why is this not available", so send it there.
    """

    global _provision_step
    with _provision_lock:
        _provision_step = message
    log.info("BEAT CPU runtime provisioning: %s", message)


def _start_official_provisioning(env: Mapping[str, str], *, backend: str | None = None) -> threading.Thread | None:
    """Prepare WG-owned CPU/GPU records using the existing launcher lifecycle."""
    from .beat_runtime import hardware, readiness

    global _preparation_in_flight, _provision_thread, _official_cpu_stage_pending, _requested_backend
    verdict = readiness.backend_readiness(CPU_BACKEND, environ=env)
    if verdict.state == "package-unusable":
        return None
    prepare_cpu = backend == CPU_BACKEND if backend is not None else not verdict.ready and verdict.state != "failed"
    prepare_gpu = False
    if str(env.get(SKIP_GPU_PROVISION_ENV_VAR, "")).strip() != "1":
        try:
            # Only PATH/directory hints here: nvidia-smi belongs in the worker.
            prepare_gpu = any(row["available"] for row in
                              hardware.gpu_hardware(environ=env, probe_nvidia=False).values())
        except Exception:
            log.info("BEAT GPU hardware inventory failed; skipping GPU preparation", exc_info=True)
    if backend is not None:
        prepare_gpu = backend in GPU_BACKENDS
    if not prepare_cpu and not prepare_gpu:
        return None
    snapshot = dict(env)

    def worker() -> None:
        global _preparation_in_flight, _provision_step, _gpu_stage_backend, _gpu_stage_step
        global _official_cpu_stage_pending
        try:
            if prepare_cpu:
                _mark_runtimes_prepared()
                _record_step("starting")
                try:
                    result = readiness.provision_cpu(environ=snapshot, status_cb=_provision_status, step_cb=_record_step,
                                                     **({"retry": True} if backend is not None else {}))
                    _remember_preparation_result(CPU_BACKEND, result)
                except Exception as exc:
                    _remember_preparation_failure(CPU_BACKEND, str(exc))
                    log.warning("WG-owned BEAT CPU preparation could not run", exc_info=True)
                finally:
                    with _provision_lock:
                        _official_cpu_stage_pending = False
                    _record_step(None)
            try:
                inventory = hardware.gpu_hardware(environ=snapshot) if prepare_gpu else {}
                gpu_backend = (backend if backend in GPU_BACKENDS else hardware.detect_gpu_backend(inventory=inventory)) if prepare_gpu else None
            except Exception as exc:
                if backend is not None:
                    _remember_preparation_failure(backend, str(exc))
                log.info("BEAT GPU hardware inventory failed; assuming none", exc_info=True)
                gpu_backend = None
            if gpu_backend is not None:
                facts = inventory[gpu_backend]
                verdict = readiness.backend_readiness(gpu_backend, environ=snapshot, hardware_facts=facts)
                if backend is None and (verdict.ready or verdict.state in {"failed", "no-device", "package-unusable"}):
                    return
                if not facts["available"]:
                    _remember_preparation_failure(gpu_backend, str(facts["reason"]))
                    return
                _mark_runtimes_prepared()
                with _provision_lock:
                    _gpu_stage_backend, _gpu_stage_step = gpu_backend, "starting"
                _notify_readiness_listeners()

                def status(message: str) -> None:
                    global _gpu_stage_step
                    with _provision_lock:
                        _gpu_stage_step = message
                    log.info("BEAT %s runtime provisioning: %s", gpu_backend, message)

                def step_changed(step: str) -> None:
                    global _gpu_stage_step
                    with _provision_lock:
                        changed = _gpu_stage_step != step
                        _gpu_stage_step = step
                    if changed:
                        _notify_readiness_listeners()

                result = getattr(readiness, f"provision_{gpu_backend}")(
                    environ=snapshot, status_cb=status, step_cb=step_changed, hardware_facts=facts,
                    **({"retry": True} if backend is not None else {}))
                _remember_preparation_result(gpu_backend, result)
        except Exception as exc:
            if backend is not None:
                _remember_preparation_failure(backend, str(exc))
            log.warning("WG-owned BEAT preparation could not run", exc_info=True)
        finally:
            with _provision_lock:
                _preparation_in_flight = _official_cpu_stage_pending = False
                _provision_step = _gpu_stage_backend = _gpu_stage_step = None
            _notify_readiness_listeners()

    with _provision_lock:
        if _provision_thread is not None and _provision_thread.is_alive():
            if backend is not None:
                raise RuntimePreparationConflict("A BEAT runtime preparation is already running.")
            return _provision_thread
        if backend is not None:
            _begin_requested_preparation(backend)
        else:
            _requested_backend = None
        _preparation_in_flight = True
        _official_cpu_stage_pending = prepare_cpu
        _provision_thread = threading.Thread(target=worker, name=PROVISION_THREAD_NAME, daemon=True)
        _provision_thread.start()
        started = _provision_thread
    _notify_readiness_listeners()
    return started


class RuntimePreparationConflict(RuntimeError):
    """Another preparation owns the shared runtime lifecycle."""


def _remember_preparation_failure(backend: str, reason: str) -> None:
    with _provision_lock:
        _preparation_errors[backend] = reason


def _remember_preparation_result(backend: str, result: Mapping[str, Any]) -> None:
    if result.get("status") != "ready":
        _remember_preparation_failure(backend, str(result.get("error") or result.get("reason") or "Preparation did not complete."))
    else:
        with _provision_lock:
            _preparation_errors.pop(backend, None)


def _begin_requested_preparation(backend: str) -> None:
    # Caller owns _provision_lock; the worker cannot publish before admission.
    global _requested_backend
    _requested_backend = backend
    _preparation_errors.pop(backend, None)


def preparation_enabled(backend: str, *, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (platform.system() in PROVISION_SYSTEMS
            and str(env.get(SKIP_PROVISION_ENV_VAR, "")).strip() != "1"
            and (backend == CPU_BACKEND or str(env.get(SKIP_GPU_PROVISION_ENV_VAR, "")).strip() != "1"))


def runtime_preparation_status(backend: str) -> tuple[str | None, str | None]:
    """Overlay current work and unexpected failures on persisted readiness."""
    with _provision_lock:
        active = _preparation_in_flight
        requested = _requested_backend
        error = _preparation_errors.get(backend)
        cpu = _official_cpu_stage_pending or _provision_step is not None
        gpu = _gpu_stage_backend
        step = _provision_step if backend == CPU_BACKEND else _gpu_stage_step
    if active and (requested == backend or backend == CPU_BACKEND and cpu or gpu == backend):
        return "preparing", f"Preparing BEAT {backend} runtime (step: {step or 'starting'})."
    if error:
        return "failed", f"BEAT {backend} preparation failed: {error}"
    return None, None


def start_runtime_preparation(backend: str) -> threading.Thread:
    """Explicit user request; use the launch worker, including its safety gates."""
    global _provision_thread, _preparation_in_flight, _prepare_cpu, _prepare_gpu
    if backend not in (CPU_BACKEND, *GPU_BACKENDS):
        raise ValueError(f"Unknown BEAT runtime: {backend}")
    with _provision_lock:
        if _preparation_in_flight or _provision_thread is not None and _provision_thread.is_alive():
            raise RuntimePreparationConflict("A BEAT runtime preparation is already running.")
    if not preparation_enabled(backend):
        raise ValueError("BEAT preparation is disabled by this installation's platform or provisioning settings.")
    from .beat_runtime import hardware, locks, readiness

    if locks.provisioning_active():
        raise RuntimePreparationConflict("Another process is preparing a BEAT runtime.")
    options = {} if backend == CPU_BACKEND else {
        "hardware_facts": hardware.gpu_hardware(only=backend, probe_nvidia=False)[backend]}
    verdict = readiness.backend_readiness(backend, **options)
    if verdict.state == "provisioning":
        raise RuntimePreparationConflict(verdict.reason)
    if verdict.ready or verdict.state in {"no-device", "package-unusable", "unsupported", "identity-unavailable", "detection-failed"}:
        raise ValueError(verdict.reason)
    started = _start_official_provisioning(os.environ, backend=backend)
    if started is None:
        raise ValueError("The BEAT package cannot prepare this runtime.")
    return started


def start_cpu_provisioning(
    *, environ: Mapping[str, str] | None = None, system: str | None = None
) -> threading.Thread | None:
    """Provision the BEAT CPU runtime in the background when this host needs one.

    Returns the live thread, or ``None`` when nothing was started -- which is
    the common case and never an error. Every gate is logged, because "why did
    my machine not download that" is a question the log has to be able to answer.
    """

    global _preparation_in_flight, _provision_thread, _provision_step
    global _prepare_cpu, _prepare_gpu, _requested_backend

    env = os.environ if environ is None else environ
    if str(env.get(SKIP_PROVISION_ENV_VAR, "")).strip() == "1":
        log.info("BEAT CPU runtime provisioning disabled by %s=1", SKIP_PROVISION_ENV_VAR)
        return None
    host = system or platform.system()
    if host not in PROVISION_SYSTEMS:
        log.info(
            "BEAT CPU runtime provisioning is not offered on %s, which is not a "
            "platform this application ships for",
            host,
        )
        return None

    return _start_official_provisioning(env)


__all__ = [
    "CPU_BACKEND",
    "CpuRuntimeReadiness",
    "GPU_BACKENDS",
    "PROVISION_SYSTEMS",
    "PROVISION_THREAD_NAME",
    "SKIP_GPU_PROVISION_ENV_VAR",
    "SKIP_PROVISION_ENV_VAR",
    "cpu_provisioning_step",
    "cpu_preparation_in_flight",
    "cpu_runtime_readiness",
    "gpu_preparation_reason",
    "provision_command",
    "runtimes_prepared_this_process",
    "start_cpu_provisioning",
    "start_runtime_preparation",
    "runtime_preparation_status",
    "preparation_enabled",
    "RuntimePreparationConflict",
]
