"""Hardware-gated Metal setup with a real compiled-system readiness probe."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import assets, hardware, julia_steps, paths, probe as compiled, provision
from .hardware import detect_gpu_backend  # noqa: F401 - provisioning entry point

_METAL_STEPS = ((
    "metal_device",
    "import Metal; Metal.versioninfo(); exit(Metal.functional() ? 0 : 1)",
    "Resolving Metal artifacts and checking the device",
),)


def _engine_worker(**kwargs: Any) -> Any:
    from beat_engine import EngineWorker

    return EngineWorker(**kwargs)


def provision_gpu(
    directory: Path | None = None, *, backend: str | None = "metal",
    status_cb: julia_steps.StatusCallback = print,
    worker_factory: Callable[..., Any] | None = None,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Provision Metal; absent/unsupported hardware causes no filesystem writes.

    None selects eligible hardware automatically. CUDA/ROCm return an explicit
    unsupported skip. Other options are provision_cpu's setup/retry/identity
    injections. The default probe launches the public EngineWorker against the
    selected system solver, then closes its stream and retires only that worker.
    Custom probes require their own contract/fixture identity and Metal evidence;
    their stored contract is namespaced so default probes always re-prove it.
    """
    if backend not in {None, "metal", "cuda", "rocm"}:
        raise ValueError(f"Unknown GPU backend: {backend!r}")
    report = julia_steps.guarded_status(status_cb)
    selected = backend or "metal"
    facts = ({"available": False, "reason": hardware.UNSUPPORTED}
             if selected in {"cuda", "rocm"} else hardware.gpu_hardware()[selected])
    if not facts["available"]:
        report(f"Skipping BEAT {selected} runtime provisioning: {facts['reason']}.")
        return {"backend": selected, "status": "skipped", "reason": facts["reason"]}

    root = (paths.runtime_dir(environ=options.get("environ"))
            if directory is None else Path(directory)).expanduser().absolute()

    def solve_probe(**launch: Any) -> Mapping[str, Any]:
        report("Proving a tiny compiled Metal solve")
        worker = (worker_factory or _engine_worker)(
            julia_executable=launch["julia_executable"],
            solver_script=assets.engine_assets("metal").system_solver,
            julia_project=launch["julia_project"], julia_threads=launch["julia_threads"],
            environment=launch["environment"], backend_label="Metal",
        )
        try:
            if options["probe_contract"] != compiled.PROBE_CONTRACT:
                raise RuntimeError("Compiled Metal probe contract does not match probe.py")
            verdict = compiled.compiled_probe(worker, directory=root, backend="metal")
            if not verdict.ready:
                raise RuntimeError(verdict.reason)
            if verdict.fixture_identity != options["probe_fixture_identity"]:
                raise RuntimeError("Compiled Metal probe fixture changed during provisioning")
            # Adapt PR 11's numerical evidence to the shared PR 10 completion API.
            return dict(verdict.completion, finite=True, nonzero=True, terminal_count=1)
        finally:
            worker.terminate()

    if probe is None:
        options.setdefault("probe_contract", compiled.PROBE_CONTRACT)
        options.setdefault("probe_fixture_identity", compiled.fixture_identity())
    elif options.get("probe_contract"):
        options["probe_contract"] = f"custom:{options['probe_contract']}"
    return provision._provision_backend(
        directory, backend="metal", status_cb=report, probe=solve_probe if probe is None else probe,
        setup_steps=_METAL_STEPS, **options,
    )
