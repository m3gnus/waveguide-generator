"""Explicit CPU provisioning; package setup alone never proves readiness."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack, suppress
import json
import os
from pathlib import Path
from typing import Any

from . import assets, discovery, identity, installer, julia_steps, locks, paths, state, threads


def _completion_valid(completion: Mapping[str, Any]) -> bool:
    # PR 11 owns numerical/terminal validation; reject absent or vacuous evidence here.
    return (
        completion.get("finite") is True and completion.get("nonzero") is True
        and type(completion.get("terminal_count")) is int
        and completion["terminal_count"] == 1
    )


def _ready(previous: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    return (
        previous is not None and previous["status"] == "ready"
        and all(previous.get(key) == value for key, value in expected.items())
        and all(expected.get(key) for key in (
            "engine_fingerprint", "runtime_fingerprint", "julia_identity",
            "probe_contract", "probe_fixture_identity",
        ))
        and _completion_valid(previous["completion"])
    )


def _failure(
    record: dict[str, Any], directory: Path, exc: Exception,
    report: julia_steps.StatusCallback,
) -> dict[str, Any]:
    record.update(status="failed", error=str(exc), completion={})
    # A diagnostic write failure must not hide the provisioning/lock error.
    with suppress(Exception):
        record = state.write_state(record, directory)
    report(f"BEAT CPU runtime provisioning failed: {exc}")
    return record


def provision_cpu(
    directory: Path | None = None, *, status_cb: julia_steps.StatusCallback = print,
    force: bool = False, retry: bool = False, julia_executable: str | None = None,
    julia_project: Path | None = None, julia_threads: int | str = "auto",
    environ: Mapping[str, str] | None = None, depot: Path | None = None,
    probe: Callable[..., Mapping[str, Any]] | None = None,
    probe_contract: str | None = None, probe_fixture_identity: str | None = None,
    run_step: Callable[..., None] | None = None,
    ensure_julia: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Resolve, instantiate, precompile and prove CPU; failures can always retry.

    force bypasses a matching ready record; retry also requests a fresh attempt.
    The injected probe receives backend, julia_executable, julia_project,
    julia_threads, environment and status_cb and returns completion evidence.
    Its contract/fixture identity must be supplied for reuse and a ready record.
    """
    report = julia_steps.guarded_status(status_cb)
    env = dict(os.environ if environ is None else environ)
    directory = (paths.runtime_dir(environ=env) if directory is None else Path(directory)).expanduser().resolve()
    record: dict[str, Any] = dict.fromkeys(state._IDENTITY_FIELDS)
    record.update(backend="cpu", status="in_progress", step="lock", error=None,
                  environment={}, completion={})
    held = ExitStack()
    try:
        held.enter_context(locks.provisioning_lock(directory, backend="cpu", status_cb=report))
    except Exception as exc:
        held.close()
        return _failure(record, directory, exc, report)
    with held:
        try:
            # Recheck under exclusion: another provisioner may have finished waiting.
            previous = state.read_state(directory, backend="cpu")
            record["step"] = "resolve_assets"
            engine = assets.engine_assets("cpu")
            project = (engine.project if julia_project is None else julia_project).expanduser().resolve()
            count = threads.resolve_julia_threads("cpu", julia_threads)
            effective_depot = str(Path(depot).expanduser().resolve()) if depot is not None else env.get("JULIA_DEPOT_PATH") or str(directory / "depot")
            env.update(JULIA_DEPOT_PATH=effective_depot, JULIA_NUM_THREADS=str(count),
                       BLAB_BEAT_ENGINE_GPU_BACKEND="cpu")
            record.update(
                project=str(project), engine_fingerprint=identity.engine_fingerprint(engine, julia_project=project),
                runtime_fingerprint=identity.runtime_fingerprint(), depot=str(effective_depot),
                environment={key: value for key, value in env.items() if key.startswith(("JULIA_", "BLAB_"))},
                probe_contract=probe_contract, probe_fixture_identity=probe_fixture_identity,
            )
            record["step"] = "resolve_julia"
            julia = discovery.discover_julia(julia_executable, root=directory, environ=env)
            if julia:
                executable = str(Path(julia).resolve())
                julia_record = state.read_julia(directory)
                record.update(julia_executable=executable, julia_identity=discovery.executable_identity(Path(julia)),
                              julia_version=julia_record["version"] if julia_record and julia_record["executable"] == executable else None)
                expected = {key: record[key] for key in (*state._IDENTITY_FIELDS, "environment")}
                managed_tree = Path(executable).is_relative_to(directory / "julia")
                outdated = managed_tree and not Path(executable).relative_to(directory / "julia").parts[0].startswith(f"{installer.JULIA_VERSION}-")
                selected = bool((julia_executable or "").strip() or env.get(discovery.JULIA_ENV_VAR, "").strip())
                if not (force or retry or outdated and not selected) and _ready(previous, expected):
                    report("BEAT CPU runtime is already provisioned.")
                    assert previous is not None
                    return previous
            state.write_state(record, directory)
            julia = (ensure_julia or installer.ensure_julia)(
                directory, explicit=julia_executable, environ=env, status_cb=report,
                required_bytes=installer.CPU_REQUIRED_FREE_BYTES,
            )
            julia = str(Path(julia).resolve())
            julia_record = state.read_julia(directory)
            record.update(julia_executable=julia,
                          julia_identity=discovery.executable_identity(Path(julia)),
                          julia_version=julia_record["version"] if julia_record and julia_record["executable"] == julia else None)
            step = run_step or julia_steps.run_julia_step
            for name, code, label in (
                ("instantiate", "using Pkg; Pkg.instantiate()", "Instantiating the Julia CPU environment"),
                ("precompile", "using Pkg; Pkg.precompile()", "Precompiling the Julia CPU environment"),
            ):
                record["step"] = name
                state.write_state(record, directory)
                step(julia, code, project=project, environment=env, label=label, status_cb=report)
            record["step"] = "cpu_probe"
            state.write_state(record, directory)
            # TODO(PR 11): wire the compiled solve probe and its contract/fixture identity.
            if probe is None:
                raise RuntimeError("Compiled CPU readiness probe is not configured (PR 11)")
            if not probe_contract or not probe_fixture_identity:
                raise RuntimeError("Compiled CPU probe contract and fixture identity are required")
            completion = dict(probe(
                backend="cpu", julia_executable=julia, julia_project=project,
                julia_threads=count, environment=env, status_cb=report,
            ))
            if not _completion_valid(completion):
                raise RuntimeError("Compiled CPU probe returned incomplete readiness evidence")
            json.dumps(completion, allow_nan=False)
            # Pkg can change manifests; save the identity of the project actually proved.
            record.update(engine_fingerprint=identity.engine_fingerprint(engine, julia_project=project),
                          completion=completion, status="ready", step="done", error=None)
            saved = state.write_state(record, directory)
            report("BEAT CPU runtime is ready.")
            return saved
        except Exception as exc:
            return _failure(record, directory, exc, report)
