"""Backend-local compiled-system proof, matched against the current launch identity."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from . import assets, discovery, gpu, hardware, identity, julia_steps, locks, paths, probe, provision, state, threads, warm_cache

BACKENDS = ("cpu", "metal", "cuda", "rocm")
log = logging.getLogger(__name__)
_listener_lock = threading.Lock()
_listeners: list[Callable[[], None]] = []
_verdict_lock = threading.RLock()
_verdicts: dict[tuple, BackendReadiness] = {}
_negative_verdicts: dict[tuple, tuple[BackendReadiness, float]] = {}
_NEGATIVE_TTL = 30.0
_TRANSIENT_NEGATIVES = {"no-device", "detection-failed", "unsupported"}


@dataclass(frozen=True)
class BackendReadiness:
    ready: bool
    state: str
    reason: str


def add_readiness_listener(listener: Callable[[], None]) -> None:
    with _listener_lock:
        if listener not in _listeners:
            _listeners.append(listener)


def remove_readiness_listener(listener: Callable[[], None]) -> None:
    with _listener_lock:
        if listener in _listeners:
            _listeners.remove(listener)


def probe_cache_clear(
    *, notify: bool = True, directory: Path | None = None, persist: bool = False,
    refresh_hardware: bool = True,
) -> None:
    """Invalidate warm verdicts and host keys, then notify presentation owners.

    Warm sweeps reuse full proofs only while the metadata signature is unchanged.
    Hardware refresh is explicit; provisioning can invalidate without detecting
    hardware again. Listeners run outside locks and cannot break provisioning.
    """
    if refresh_hardware:
        hardware.clear_hardware_cache()
    warm_cache.invalidate()
    with _verdict_lock:
        _verdicts.clear()
        _negative_verdicts.clear()
    if persist:
        root = paths.runtime_dir() if directory is None else paths.checked_root(directory)
        for name in ("state-cpu.json", "state-metal.json", "state-cuda.json", "state-rocm.json", "julia.json"):
            target = root / name
            if paths.is_link(root) or paths.is_link(target):
                raise ValueError("Linked readiness state refused")
            try:
                os.utime(target, None)
            except FileNotFoundError:
                pass
    if not notify:
        return
    notify_readiness_listeners()


def notify_readiness_listeners() -> None:
    """Publish a cleared verdict after the caller has released ownership locks."""
    with _listener_lock:
        listeners = tuple(_listeners)
    for listener in listeners:
        try:
            listener()
        except Exception:
            log.debug("BEAT readiness listener failed", exc_info=True)


def expected_identity(
    backend: str, directory: Path, *, environ: Mapping[str, str] | None = None,
    julia_executable: str | None = None, julia_project: Path | None = None,
    julia_threads: int | str = "auto", depot: Path | None = None,
) -> dict[str, Any]:
    """Resolve the same defaults as provisioning, without starting Julia."""
    env = dict(os.environ if environ is None else environ)
    engine = assets.engine_assets(backend)
    count = threads.resolve_julia_threads(backend, julia_threads)
    env.update(JULIA_DEPOT_PATH=str(depot) if depot is not None else env.get("JULIA_DEPOT_PATH") or str(directory / "depot"),
               JULIA_NUM_THREADS=str(count), BLAB_BEAT_ENGINE_GPU_BACKEND=backend)
    project, env = julia_steps.julia_environment(
        engine.project if julia_project is None else julia_project, env, cwd=Path.cwd(),
    )
    expected = dict.fromkeys(state._IDENTITY_FIELDS)
    expected.update(
        project=str(project), engine_fingerprint=identity.engine_fingerprint(engine, backend=backend, julia_project=project),
        runtime_fingerprint=identity.runtime_fingerprint(), depot=env["JULIA_DEPOT_PATH"],
        environment={key: value for key, value in env.items() if key.startswith(("JULIA_", "BLAB_"))},
        probe_contract=probe.PROBE_CONTRACT, probe_fixture_identity=probe.fixture_identity(),
    )
    julia = discovery.discover_julia(julia_executable, root=directory, environ=env)
    if julia:
        recorded = state.read_julia(directory)
        expected.update(julia_executable=julia, julia_identity=discovery.executable_identity(Path(julia)),
                        julia_version=recorded["version"] if recorded and recorded["executable"] == julia else None)
    return expected


def _prove_backend_readiness(
    backend: str, directory: Path | None = None, *,
    hardware_facts: Mapping[str, bool | str] | None = None, **options: Any,
) -> BackendReadiness:
    """Static engine catalogs and hardware eligibility never establish ready."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown BEAT backend: {backend!r}")
    if backend in hardware.GPU_BACKENDS:
        try:
            facts = (hardware.gpu_hardware(only=backend, environ=options.get("environ"))[backend]
                     if hardware_facts is None else hardware_facts)
        except Exception as exc:
            return BackendReadiness(False, "detection-failed", f"BEAT {backend} hardware detection failed: {exc}")
        if not facts["available"]:
            return BackendReadiness(False, "no-device", str(facts["reason"]))
    try:
        root = paths.runtime_dir(environ=options.get("environ")) if directory is None else paths.checked_root(directory)
        record = state.read_state(root, backend=backend)
        if record and record["status"] == "in_progress" and locks.provisioning_active(root):
            return BackendReadiness(False, "provisioning", f"BEAT {backend} provisioning is active (step: {record['step']}).")
        expected = expected_identity(backend, root, **options)
    except assets.AssetsUnavailable as exc:
        return BackendReadiness(False, "package-unusable", str(exc))
    except Exception as exc:
        return BackendReadiness(False, "identity-unavailable", f"BEAT {backend} identity unavailable: {exc}")
    matches = record is not None and all(record.get(key) == value for key, value in expected.items())
    if matches and record["status"] == "failed":
        return BackendReadiness(False, "failed", f"BEAT {backend} provisioning failed: {record['error'] or 'unknown error'}. Retry with --force.")
    if matches and record["status"] == "in_progress":
        return BackendReadiness(False, "interrupted", f"BEAT {backend} provisioning stopped at {record['step']}; provision again.")
    if not expected["julia_executable"]:
        return BackendReadiness(False, "no-julia", f"No verified Julia executable; provision the BEAT {backend} runtime.")
    executable = Path(expected["julia_executable"]).resolve()
    managed = (root / "julia").resolve()
    if executable.is_relative_to(managed):
        version = executable.relative_to(managed).parts[0]
        env = options.get("environ")
        selected = options.get("julia_executable") or (os.environ if env is None else env).get(discovery.JULIA_ENV_VAR)
        if not selected and not (version == provision.installer.JULIA_VERSION or version.startswith(f"{provision.installer.JULIA_VERSION}-")):
            return BackendReadiness(False, "stale", "WG's portable Julia needs an upgrade; provision again.")
    if provision._ready(record, expected):
        return BackendReadiness(True, "ready", f"BEAT {backend} runtime proved by a matching compiled-system 1 kHz solve.")
    reason = "Stored identity or compiled proof is stale; provision again." if record else "Runtime has not been provisioned and proved."
    return BackendReadiness(False, "stale" if record else "unprovisioned", f"BEAT {backend}: {reason}")


def backend_readiness(
    backend: str, directory: Path | None = None, *, force_refresh: bool = False,
    hardware_facts: Mapping[str, bool | str] | None = None, **options: Any,
) -> BackendReadiness:
    """Reuse a full proof under the cheap launch signature; refresh is explicit."""
    if force_refresh:
        probe_cache_clear(notify=False)
    if hardware_facts is not None:
        return _prove_backend_readiness(backend, directory, hardware_facts=hardware_facts, **options)
    # Hardware-only negatives do not depend on a source/depot proof, and may
    # precede asset discovery entirely. Keep their cache independent of it.
    negative_key = (warm_cache.generation(), hardware.cache_generation(), backend, str(directory), str(Path.cwd()),
                    warm_cache._freeze(dict(os.environ)), warm_cache._freeze(options))
    with _verdict_lock:
        cached = _negative_verdicts.get(negative_key)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
    try:
        signature = warm_cache.runtime_signature(backend, directory, **options)
    except Exception:
        # Let the full proof retain its precise unavailable/error semantics.
        signature = None
    with _verdict_lock:
        if signature is not None and signature in _verdicts:
            return _verdicts[signature]
        verdict = _prove_backend_readiness(backend, directory, **options)
        if verdict.state in _TRANSIENT_NEGATIVES:
            if len(_negative_verdicts) >= 32:
                _negative_verdicts.clear()
            _negative_verdicts[negative_key] = verdict, time.monotonic() + _NEGATIVE_TTL
        elif signature is not None and verdict.state not in {"provisioning", "interrupted"}:
            try:
                unchanged = signature == warm_cache.runtime_signature(backend, directory, _fresh=True, **options)
            except Exception:
                unchanged = False
            if unchanged:
                if len(_verdicts) >= 32:
                    _verdicts.clear()
                _verdicts[signature] = verdict
        return verdict


def backend_status(backend: str, directory: Path | None = None, **options: Any) -> dict[str, Any]:
    verdict = backend_readiness(backend, directory, **options)
    return dict(available=verdict.ready, state=verdict.state, reason=verdict.reason,
                backend=backend, version=None, surface_traces=False)


def beat_backend_statuses(directory: Path | None = None, **options: Any) -> dict[str, dict[str, Any]]:
    if options.pop("force_refresh", False):
        probe_cache_clear(notify=False)
    return {backend: backend_status(backend, directory, **options) for backend in BACKENDS}


def beat_engine_status(directory: Path | None = None, **options: Any) -> dict[str, Any]:
    statuses = beat_backend_statuses(directory, **options)
    return next((statuses[name] for name in (*hardware.GPU_BACKENDS, "cpu") if statuses[name]["available"]), statuses["cpu"])


def _engine_worker(**launch: Any) -> Any:
    from beat_engine import EngineWorker

    return EngineWorker(**launch)


def provision_cpu(
    directory: Path | None = None, *, worker_factory: Callable[..., Any] | None = None,
    status_cb: Callable[[str], None] = print, **options: Any,
) -> dict[str, Any]:
    """Wire the official compiled CPU probe into the shared provisioner."""
    root = paths.runtime_dir(environ=options.get("environ")) if directory is None else paths.checked_root(directory)

    def solve_probe(**launch: Any) -> Mapping[str, Any]:
        worker = (worker_factory or _engine_worker)(
            julia_executable=launch["julia_executable"], solver_script=assets.engine_assets("cpu").system_solver,
            julia_project=launch["julia_project"], julia_threads=launch["julia_threads"],
            environment=launch["environment"], backend_label="CPU",
        )
        try:
            verdict = probe.compiled_probe(worker, directory=root, backend="cpu")
            if not verdict.ready:
                raise RuntimeError(verdict.reason)
            if verdict.fixture_identity != options["probe_fixture_identity"] or options["probe_contract"] != probe.PROBE_CONTRACT:
                raise RuntimeError("Compiled CPU probe identity changed during provisioning")
            return dict(verdict.completion, finite=True, nonzero=True, terminal_count=1)
        finally:
            worker.terminate()

    if options.get("probe") is None:
        options.update(probe=solve_probe)
        options.setdefault("probe_contract", probe.PROBE_CONTRACT)
        options.setdefault("probe_fixture_identity", probe.fixture_identity())
    else:
        options["probe_contract"] = f"custom:{options.get('probe_contract') or ''}"
    try:
        return provision.provision_cpu(directory, status_cb=status_cb, **options)
    finally:
        probe_cache_clear(refresh_hardware=False)


def provision_metal(directory: Path | None = None, **options: Any) -> dict[str, Any]:
    try:
        return gpu.provision_gpu(directory, backend="metal", **options)
    finally:
        probe_cache_clear(refresh_hardware=False)


def provision_cuda(directory: Path | None = None, **options: Any) -> dict[str, Any]:
    try:
        return gpu.provision_gpu(directory, backend="cuda", **options)
    finally:
        probe_cache_clear(refresh_hardware=False)


def provision_rocm(directory: Path | None = None, **options: Any) -> dict[str, Any]:
    try:
        return gpu.provision_gpu(directory, backend="rocm", **options)
    finally:
        probe_cache_clear(refresh_hardware=False)
