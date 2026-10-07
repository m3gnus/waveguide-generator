"""Hardware-gated GPU setup with a real compiled-system readiness probe."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import assets, hardware, julia_steps, paths, probe as compiled, provision
from .hardware import detect_gpu_backend  # noqa: F401 - provisioning entry point

_GPU_MODULES = {"metal": "Metal", "cuda": "CUDA", "rocm": "AMDGPU"}
_GPU_LABELS = {"metal": "Metal", "cuda": "CUDA", "rocm": "ROCm"}


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
    """Provision the selected GPU only when matching hardware is present.

    None selects CUDA, then ROCm, then Metal. The built-in probe proves the
    compiled-system request contract, including CUDA/ROCm's source driver.
    Injected probes keep their own namespaced contract and fixture identity.
    """
    if backend not in {None, "metal", "cuda", "rocm"}:
        raise ValueError(f"Unknown GPU backend: {backend!r}")
    report = julia_steps.guarded_status(status_cb)
    inventory = (hardware.gpu_hardware() if options.get("environ") is None
                 else hardware.gpu_hardware(environ=options["environ"]))
    selected = backend or next((name for name in hardware.GPU_BACKENDS
                                if inventory[name]["available"]), None)
    facts = inventory[selected] if selected else {"available": False, "reason": "no device"}
    if not facts["available"]:
        report(f"Skipping BEAT {selected} runtime provisioning: {facts['reason']}.")
        return {"backend": selected, "status": "skipped", "reason": facts["reason"]}

    label, module = _GPU_LABELS[selected], _GPU_MODULES[selected]
    root = (paths.runtime_dir(environ=options.get("environ"))
            if directory is None else Path(directory)).expanduser().absolute()

    def solve_probe(**launch: Any) -> Mapping[str, Any]:
        report(f"Proving a tiny {label} compiled-system solve")
        worker = (worker_factory or _engine_worker)(
            julia_executable=launch["julia_executable"],
            solver_script=assets.engine_assets(selected).system_solver,
            julia_project=launch["julia_project"], julia_threads=launch["julia_threads"],
            environment=launch["environment"], backend_label=label,
        )
        try:
            if options["probe_contract"] != compiled.PROBE_CONTRACT:
                raise RuntimeError(f"Compiled {label} probe contract does not match probe.py")
            verdict = compiled.compiled_probe(worker, directory=root, backend=selected)
            if not verdict.ready:
                raise RuntimeError(verdict.reason)
            if verdict.fixture_identity != options["probe_fixture_identity"]:
                raise RuntimeError(f"Compiled {label} probe fixture changed during provisioning")
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
        directory, backend=selected, status_cb=report, probe=solve_probe if probe is None else probe,
        setup_steps=((f"{selected}_device",
                      f"import {module}; {module}.versioninfo(); exit({module}.functional() ? 0 : 1)",
                      f"Resolving {label} artifacts and checking the device"),), **options,
    )
