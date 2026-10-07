"""Shared provisioning; package setup alone never proves readiness."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack, suppress
import json
import os
from pathlib import Path
from typing import Any

from . import assets, discovery, identity, installer, julia_steps, locks, paths, state, threads
from .probe import PROBE_CONTRACT


def _completion_valid(completion: Mapping[str, Any], *, backend: str = "cpu") -> bool:
    # PR 11 owns numerical/terminal validation; reject absent or vacuous evidence here.
    return (
        completion.get("finite") is True and completion.get("nonzero") is True
        and type(completion.get("terminal_count")) is int
        and completion["terminal_count"] == 1
        and (backend == "cpu" or completion.get("bem_backend") == backend)
    )


def _ready(previous: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    return (
        previous is not None and previous["status"] == "ready"
        and all(previous.get(key) == value for key, value in expected.items())
        and all(expected.get(key) for key in (
            "engine_fingerprint", "runtime_fingerprint", "julia_identity",
            "probe_contract", "probe_fixture_identity",
        ))
        and _completion_valid(previous["completion"], backend=previous["backend"])
        and (expected.get("probe_contract") != PROBE_CONTRACT
             or compiled_completion_valid(previous["completion"], previous["backend"]))
    )


def compiled_completion_valid(completion: Mapping[str, Any], backend: str) -> bool:
    """Require PR 11's numerical evidence when reusing the built-in probe."""
    return (
        completion.get("finite_nonzero") is True
        and type(completion.get("result_count")) is int and completion["result_count"] == 1
        and type(completion.get("solved_count")) is int and completion["solved_count"] == 1
        and completion.get("bem_backend") == backend
    )


def _failure(
    record: dict[str, Any], directory: Path, exc: Exception,
    report: julia_steps.StatusCallback,
) -> dict[str, Any]:
    record.update(status="failed", error=str(exc), completion={})
    # A diagnostic write failure must not hide the provisioning/lock error.
    with suppress(Exception):
        if not paths.is_link(directory):
            record = state.write_state(record, directory)
    label = {"cpu": "CPU", "metal": "Metal", "cuda": "CUDA", "rocm": "ROCm"}[record["backend"]]
    report(f"BEAT {label} runtime provisioning failed: {exc}")
    return record


def _provision_backend(
    directory: Path | None = None, *, backend: str, status_cb: julia_steps.StatusCallback = print,
    force: bool = False, retry: bool = False, julia_executable: str | None = None,
    julia_project: Path | None = None, julia_threads: int | str = "auto",
    environ: Mapping[str, str] | None = None, depot: Path | None = None,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    probe_contract: str | None = None, probe_fixture_identity: str | None = None,
    run_step: Callable[..., None] | None = None,
    ensure_julia: Callable[..., str] | None = None,
    setup_steps: tuple[tuple[str, str, str], ...] = (),
    step_cb: julia_steps.StatusCallback | None = None,
) -> dict[str, Any]:
    """Resolve, instantiate, precompile and prove one backend; failures can always retry.

    force bypasses a matching ready record; retry also requests a fresh attempt.
    The injected probe receives backend, julia_executable, julia_project,
    julia_threads, environment and status_cb and returns completion evidence.
    Its contract/fixture identity must be supplied for reuse and a ready record.
    """
    if backend not in {"cpu", "metal", "cuda", "rocm"}:
        raise ValueError(f"Unsupported provisioning backend: {backend!r}")
    label = {"cpu": "CPU", "metal": "Metal", "cuda": "CUDA", "rocm": "ROCm"}[backend]
    report = julia_steps.guarded_status(status_cb)
    transition = julia_steps.guarded_status(step_cb) if step_cb is not None else lambda step: None
    env = dict(os.environ if environ is None else environ)
    directory = (paths.runtime_dir(environ=env) if directory is None else paths.checked_root(directory, environ=env)).expanduser().absolute()
    paths.checked_root(directory)
    if paths.is_link(directory):
        raise RuntimeError(f"Linked runtime directory refused: {directory}")
    cwd = Path.cwd()
    record: dict[str, Any] = dict.fromkeys(state._IDENTITY_FIELDS)
    record.update(backend=backend, status="in_progress", step="lock", error=None,
                  environment={}, completion={})

    def resolve_inputs(previous: dict[str, Any] | None) -> tuple[assets.EngineAssets, Path, int, bool]:
        record["step"] = "resolve_assets"
        engine = assets.engine_assets(backend)
        selected_project = engine.project if julia_project is None else julia_project
        count = threads.resolve_julia_threads(backend, julia_threads)
        effective_depot = str(depot) if depot is not None else env.get("JULIA_DEPOT_PATH") or str(directory / "depot")
        env.update(JULIA_DEPOT_PATH=effective_depot, JULIA_NUM_THREADS=str(count),
                   BLAB_BEAT_ENGINE_GPU_BACKEND=backend)
        project, effective_env = julia_steps.julia_environment(selected_project, env, cwd=cwd)
        env.update(effective_env)
        record.update(
            project=str(project), engine_fingerprint=identity.engine_fingerprint(engine, backend=backend, julia_project=project),
            runtime_fingerprint=identity.runtime_fingerprint(), depot=env["JULIA_DEPOT_PATH"],
            environment={key: value for key, value in env.items() if key.startswith(("JULIA_", "BLAB_"))},
            probe_contract=probe_contract, probe_fixture_identity=probe_fixture_identity,
        )
        record["step"] = "resolve_julia"
        julia = discovery.discover_julia(julia_executable, root=directory, environ=env)
        if julia:
            executable = str(Path(julia).expanduser().absolute())
            julia_record = state.read_julia(directory)
            record.update(julia_executable=executable, julia_identity=discovery.executable_identity(Path(julia)),
                          julia_version=julia_record["version"] if julia_record and julia_record["executable"] == executable else None)
            expected = {key: record[key] for key in (*state._IDENTITY_FIELDS, "environment")}
            resolved = Path(executable).resolve()
            managed_root = (directory / "julia").resolve()
            managed_tree = resolved.is_relative_to(managed_root)
            version_dir = resolved.relative_to(managed_root).parts[0] if managed_tree else ""
            outdated = managed_tree and not (version_dir == installer.JULIA_VERSION or version_dir.startswith(f"{installer.JULIA_VERSION}-"))
            selected = bool((julia_executable or "").strip() or env.get(discovery.JULIA_ENV_VAR, "").strip())
            if not (force or retry or outdated and not selected) and _ready(previous, expected):
                return engine, project, count, True
        return engine, project, count, False

    # Atomic records permit a lock-free ready check while another backend sets up.
    previous = state.read_state(directory, backend=backend)
    if previous and previous["status"] == "ready" and not (force or retry):
        try:
            if resolve_inputs(previous)[3]:
                report(f"BEAT {label} runtime is already provisioned.")
                return previous
        except Exception:
            # Resolve/report failures under exclusion, just like a fresh attempt.
            pass
    record["step"] = "lock"
    held = ExitStack()
    try:
        held.enter_context(locks.provisioning_lock(directory, backend=backend, status_cb=report))
    except Exception as exc:
        held.close()
        return _failure(record, directory, exc, report)
    with held:
        try:
            # Recheck under exclusion: another provisioner may have finished waiting.
            previous = state.read_state(directory, backend=backend)
            engine, project, count, ready = resolve_inputs(previous)
            if ready:
                report(f"BEAT {label} runtime is already provisioned.")
                assert previous is not None
                return previous
            state.write_state(record, directory)
            julia = (ensure_julia or installer.ensure_julia)(
                directory, explicit=julia_executable, environ=env, status_cb=report,
                required_bytes=installer.CPU_REQUIRED_FREE_BYTES if backend == "cpu" else installer.GPU_REQUIRED_FREE_BYTES,
            )
            julia = str(Path(julia).expanduser().absolute())
            julia_record = state.read_julia(directory)
            record.update(julia_executable=julia,
                          julia_identity=discovery.executable_identity(Path(julia)),
                          julia_version=julia_record["version"] if julia_record and julia_record["executable"] == julia else None)
            step = run_step or julia_steps.run_julia_step
            for name, code, step_label in (
                ("instantiate", "using Pkg; Pkg.instantiate()", f"Instantiating the Julia {label} environment"),
                ("precompile", "using Pkg; Pkg.precompile()", f"Precompiling the Julia {label} environment"),
            ) + setup_steps:
                record["step"] = name
                state.write_state(record, directory)
                transition(name)
                project, env = julia_steps.julia_environment(project, env, cwd=cwd)
                step(julia, code, project=project, environment=env, label=step_label, status_cb=report)
            record["step"] = f"{backend}_probe"
            state.write_state(record, directory)
            transition(record["step"])
            # TODO(PR 11): wire the compiled solve probe and its contract/fixture identity.
            if probe is None:
                raise RuntimeError(f"Compiled {label} readiness probe is not configured (PR 11)")
            if not probe_contract or not probe_fixture_identity:
                raise RuntimeError(f"Compiled {label} probe contract and fixture identity are required")
            project, env = julia_steps.julia_environment(project, env, cwd=cwd)
            completion = dict(probe(
                backend=backend, julia_executable=julia, julia_project=project,
                julia_threads=count, environment=env, status_cb=report,
            ))
            if not _completion_valid(completion, backend=backend):
                raise RuntimeError(f"Compiled {label} probe returned incomplete readiness evidence")
            json.dumps(completion, allow_nan=False)
            # Pkg can change manifests; save the identity of the project actually proved.
            record.update(engine_fingerprint=identity.engine_fingerprint(engine, backend=backend, julia_project=project),
                          completion=completion, status="ready", step="done", error=None)
            saved = state.write_state(record, directory)
            report(f"BEAT {label} runtime is ready.")
            return saved
        except Exception as exc:
            return _failure(record, directory, exc, report)


def provision_cpu(
    directory: Path | None = None, *, status_cb: julia_steps.StatusCallback = print,
    force: bool = False, retry: bool = False, julia_executable: str | None = None,
    julia_project: Path | None = None, julia_threads: int | str = "auto",
    environ: Mapping[str, str] | None = None, depot: Path | None = None,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    probe_contract: str | None = None, probe_fixture_identity: str | None = None,
    run_step: Callable[..., None] | None = None,
    ensure_julia: Callable[..., str] | None = None,
    step_cb: julia_steps.StatusCallback | None = None,
) -> dict[str, Any]:
    """Provision CPU with the shared lock, identity and injectable compiled probe."""
    return _provision_backend(
        directory, backend="cpu", status_cb=status_cb, force=force, retry=retry,
        julia_executable=julia_executable, julia_project=julia_project,
        julia_threads=julia_threads, environ=environ, depot=depot, probe=probe,
        probe_contract=probe_contract, probe_fixture_identity=probe_fixture_identity,
        run_step=run_step, ensure_julia=ensure_julia, step_cb=step_cb,
    )
