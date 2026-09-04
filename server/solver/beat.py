"""GPU-gated adapter for ``hornlab-beat-bem`` (the BEAT Engine Julia solver).

User-facing availability is GPU-only (CUDA today, ROCm when hardware lands):
``beat_status`` reports available solely when the package's own probe finds a
functional accelerator, so BEMPP remains the CPU engine everywhere. The BEAT
CPU backend still exists behind ``HORNLAB_BEAT_FORCE_CPU=1`` as the internal
scaffolding this adapter was validated on and as the CI/regression path; it is
never advertised to users.

Import/load behavior, staged solve, and result mapping mirror
``server/solver/bempp.py``; absence is a normal capability state.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import importlib
import inspect
import logging
import threading
import time
from pathlib import Path
import tempfile
from typing import Any, Mapping

from server.jobs.models import SolveRequest
from server.mesh.builder import build_solver_mesh

from .acoustics import solver_sound_speed_m_per_s
from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    ResultCallback,
    StageCallback,
)
from .context import SolverContext
from .field_traces_store import (
    BEAT_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .frequency_sweep import (
    live_execution_frequencies,
    sort_native_result_frequencies,
)
from .infinite_baffle import reject_beat_infinite_baffle
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    native_observation_frame,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
)
from .warmup import BEAT_WARMUP_STAGE_MESSAGE, beat_warmup_in_progress


try:
    import hornlab_beat_bem as _beat
except (ImportError, OSError):
    _beat = None  # type: ignore[assignment]


class BeatUnavailable(RuntimeError):
    """The optional BEAT Engine package cannot run a solve here."""


#: Where ``resolve_beat_backend`` lands when a probe reports available without
#: naming an accelerator. The CPU path is the one backend every host has, so a
#: wrong guess here costs a slow solve rather than a failed device init.
BEAT_FALLBACK_BACKEND = "cpu"

logger = logging.getLogger(__name__)


def _load_api() -> Any | None:
    global _beat
    if _beat is not None:
        return _beat
    try:
        _beat = importlib.import_module("hornlab_beat_bem")
    except (ImportError, OSError):
        return None
    return _beat


class _BeatProbeUnavailable(RuntimeError):
    """Carries an unavailable probe result past the cache, which must not keep it."""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(status.get("reason", "beat is unavailable"))
        self.status = status


def _package_retains_surface_traces(package: Any) -> bool:
    """Whether the installed package can return the boundary Cauchy datum."""

    solve_config = getattr(package, "SolveConfig", None)
    if solve_config is None:
        return False
    try:
        return "surface_traces" in inspect.signature(solve_config).parameters
    except (TypeError, ValueError):
        return False


def _probe_beat_status() -> dict[str, Any]:
    package = _load_api()
    if package is None:
        return {
            "available": False,
            "reason": "hornlab-beat-bem is not importable (optional GPU engine not installed).",
            "version": None,
            "backend": None,
        }
    status = package.beat_engine_status()
    return {
        "available": bool(status.get("available")),
        "reason": str(status.get("reason") or "beat capability probe returned no reason"),
        "version": status.get("version"),
        "backend": status.get("backend"),
        # Detected, not assumed: surface-trace retention landed after the first
        # pinned build, and the registry advertises whatever the pin can do.
        "surface_traces": _package_retains_surface_traces(package),
    }


#: Serialises a *miss*, which ``lru_cache`` does not. Two threads probing at
#: once is now the normal case at boot, not a corner: the capability snapshot
#: runs on the registry's thread while the prewarm's head start (see
#: ``server/app.py``) warms the saved engine on another, and both ask for this
#: status. Without the lock each one pays the package's own probe.
_status_probe_lock = threading.Lock()


@lru_cache(maxsize=1)
def _cached_successful_beat_status() -> dict[str, Any]:
    status = _probe_beat_status()
    if not status["available"]:
        # functools does not cache exceptions, so an unavailable result is
        # re-probed next time -- plugging in a GPU driver or setting the
        # force-CPU variable must take effect without restarting the server.
        raise _BeatProbeUnavailable(status)
    return status


def beat_status() -> dict[str, Any]:
    """Report whether a BEAT solve can run, and on which accelerator.

    The expensive part (a Julia startup asking CUDA/AMDGPU whether a device is
    functional) is cached inside the package for the process lifetime; only
    successful probes are cached here, following ``bempp_status``.
    """

    with _status_probe_lock:
        try:
            status = _cached_successful_beat_status()
        except _BeatProbeUnavailable as exc:
            status = exc.status
    return dict(status)


beat_status.cache_clear = _cached_successful_beat_status.cache_clear  # type: ignore[attr-defined]


def resolve_beat_backend(status: Mapping[str, Any]) -> str:
    """The accelerator a BEAT solve will run on, given a probe result.

    One function because there are two callers and they must not disagree: the
    solve below, and the boot warmup in ``server/solver/warmup.py``. They used
    to spell the same fallback differently -- the solve said ``"cpu"`` and the
    warmup said ``"cuda"`` -- so a probe that reported available without naming
    a backend would have warmed CUDA and then solved on the CPU, paying a
    device initialisation this host may not even have and leaving the path the
    user actually waits on cold.

    No pin has ever produced that state: at ``hornlab-beat-bem`` ``88487d8``
    every ``available: True`` branch of ``beat_engine_status`` names a concrete
    backend, and every branch that leaves it ``None`` reports unavailable. That
    invariant lives in another repository, though, and nothing here pinned it,
    so the divergence was one upstream edit away from mattering rather than
    impossible. Sharing the resolution makes the two sides wrong together at
    worst, which is recoverable, instead of wrong differently, which is silent.
    """

    return str(status.get("backend") or BEAT_FALLBACK_BACKEND)


def announce_beat_warmup_wait(stage_callback: StageCallback | None) -> bool:
    """Say why a solve that arrived during the boot warmup is standing still.

    ``hornlab_beat_bem`` keeps one Julia worker per process and the boot
    prewarm is inside it, so a solve started before the warmup finishes waits
    for that lock -- 10-16 s on the packaged 0.3.1 app, all of it under
    "Configuring BEAT Engine BEM solve (metal)". Pre-empting the warmup was
    measured and is not the fix (the solves that won the race still took 16-19
    s: the compile is the cost, not the queueing), so what is left is telling
    the user the truth -- that this wait is the one-off compilation and their
    next solve will not pay it.

    Emitted through the ordinary ``setup`` stage callback, so it reaches the
    job's stage events with no new mechanism, and is replaced by the first
    real progress message the moment the solve gets the worker.

    Returns whether a message was emitted, for tests and diagnostics.
    """

    if stage_callback is None or not beat_warmup_in_progress():
        return False
    stage_callback("setup", 0.0, BEAT_WARMUP_STAGE_MESSAGE)
    return True


def solve_beat_from_msh_text(
    msh_text: str,
    context: SolverContext,
    *,
    mesh_metadata: dict[str, Any] | None = None,
    mesh_stats: Mapping[str, Any] | None = None,
    field_trace_cap_bytes: int | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on the BEAT Engine backend."""

    context.validate()
    del mesh_metadata
    if context.solver_mode == "circsym":
        raise ValueError("BEAT cannot run solver_mode='circsym'; use the Axisymmetric runner or full_3d")
    reject_beat_infinite_baffle(context)
    package = _load_api()
    if package is None:
        raise BeatUnavailable("hornlab-beat-bem is not installed.")
    status = beat_status()
    if not status["available"]:
        raise BeatUnavailable(status["reason"])
    backend = resolve_beat_backend(status)
    field_plane_enabled = (
        getattr(context, "polar_config", {}).get("field_plane", True) is True
    )
    retain_traces, trace_reason, trace_estimated_bytes, trace_cap_bytes = (
        field_trace_retention_plan(
            msh_text,
            mesh_stats=mesh_stats,
            frequency_count=len(live_execution_frequencies(context)),
            channel_count=1,
            enabled=field_plane_enabled,
            supported=bool(status.get("surface_traces")),
            cap_bytes=field_trace_cap_bytes,
            unsupported_reason="unsupported_solver_version",
        )
    )

    started = time.time()
    if stage_callback:
        stage_callback("setup", 0.0, f"Configuring BEAT Engine BEM solve ({backend})")
    announce_beat_warmup_wait(stage_callback)

    observation = observation_config(
        context,
        package.ObservationConfig,
        BeatUnavailable,
        "hornlab-beat-bem",
        msh_text=msh_text,
    )
    frame = native_observation_frame(context, msh_text, package.ObservationFrame)
    if frame is None:
        raise BeatUnavailable(
            "hornlab-beat-bem requires the authoritative observation frame "
            "(source-tagged Gmsh 2.2 artifact)."
        )

    def progress(index: int, total: int, frequency_hz: float) -> None:
        if cancellation_callback:
            cancellation_callback()
        fraction = (index + 1) / max(1, total)
        if progress_callback:
            progress_callback(fraction)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} with BEAT Engine",
            )

    def on_frequency_result(index: int, frequency_hz: float, entry: dict[str, Any]) -> bool:
        if cancellation_callback:
            cancellation_callback()
        if result_callback is not None:
            result_callback(
                index,
                build_provisional_frequency_response(
                    index=index,
                    frequency_hz=frequency_hz,
                    entry={
                        "observation_angles_deg": entry.get("observation_angles_deg"),
                        "observation_planes": entry.get("observation_planes"),
                        "observation_spl_db": entry.get("observation_spl_db"),
                        "observation_pressure_complex": entry.get("observation_pressure_complex"),
                        "impedance": entry.get("impedance"),
                    },
                    config=config,
                    context=context,
                    backend="beat",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
                ),
            )
        return True

    try:
        config = package.SolveConfig(
            freq_min_hz=context.frequency_range[0],
            freq_max_hz=context.frequency_range[1],
            freq_count=context.num_frequencies,
            freq_spacing=context.frequency_spacing,
            observation=observation,
            frame_override=frame,
            native_symmetry_plane=native_symmetry_plane(context),
            mesh_scale=1.0,
            beat_backend=backend,
            source_motion=context.source_motion,
            **({"surface_traces": True} if retain_traces else {}),
            progress_callback=progress,
            on_frequency_result=(
                on_frequency_result if result_callback is not None else None
            ),
        )
        package.reject_unsupported_native_symmetry(config)
    except NotImplementedError as exc:
        raise BeatUnavailable(str(exc)) from exc

    def stage_status(message: str) -> None:
        if stage_callback and message:
            stage_callback("setup", 0.0, message)

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)
        try:
            result = package.solve_frequencies(
                str(path),
                live_execution_frequencies(context).tolist(),
                config,
                status_callback=stage_status,
            )
        except NotImplementedError as exc:
            raise BeatUnavailable(str(exc)) from exc
        sort_native_result_frequencies(result)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temporary BEAT mesh %s: %s", path, exc)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging BEAT Engine solver results")

    solver_log = [
        {
            key: value
            for key, value in entry.items()
            # The full per-frequency pressure rows already live in the result
            # arrays; the persisted log keeps diagnostics and timings only.
            if key not in {"observation_pressure_complex", "observation_spl_db"}
        }
        for entry in getattr(result, "solver_log", [])
    ]
    metadata = {
        "solver_backend": "beat",
        "solver_mode": "full_3d",
        "engine": "hornlab-beat-bem",
        "phase_time_convention": "exp(+ikr)",
        "beat_backend": backend,
        "device_interface": {
            "selected": f"beat-{backend}",
            f"beat-{backend}": status,
        },
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "hornlab-beat-bem"},
        "verbose": context.verbose,
        "performance": {
            "total_time_seconds": time.time() - started,
            "native_timings": json_safe_native_value(dict(getattr(result, "timings", {}) or {})),
        },
        "field_trace_retention": {
            "estimated_bytes": trace_estimated_bytes,
            "cap_bytes": trace_cap_bytes,
        },
        "beat": {
            "native_symmetry_plane": getattr(config, "native_symmetry_plane", None),
            "formulation": "burton_miller",
            "backend": backend,
            "drive_convention": (
                "q=i*rho*omega*v_n on a 1 m/s velocity basis, rescaled to "
                "unit normal acceleration by the package"
            ),
            "precision": "single",
            "solver_log": json_safe_native_value(response_solver_log(solver_log)),
        },
    }
    response = build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
    )
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            [("default", result)],
            config,
            backend=BEAT_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_beat_bem"),
        )
        if retain_traces
        else None
    )
    if retain_traces and field_traces is None:
        trace_reason = "trace_output_missing"
    response["_field_traces"] = field_traces
    response["_field_trace_unavailable_reason"] = trace_reason
    return response


class BeatEngine:
    name = "beat"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
    ) -> EngineRunResult:
        if (request.options.solver_mode or "").strip().lower() == "circsym":
            raise ValueError("BEAT cannot run solver_mode='circsym'; select Axisymmetric or use full_3d")
        context = SolverContext.from_request(request, solver_mode="full_3d")
        reject_beat_infinite_baffle(context)
        mesh = await build_solver_mesh(
            request.design,
            request.options,
            cancel_cb,
            lambda stage, progress, message: stage_cb(stage, progress, message),
        )
        if artifact_cb is not None:
            await artifact_cb(mesh["msh_text"], mesh["stats"])
        cancel_cb()
        results = await asyncio.to_thread(
            solve_beat_from_msh_text,
            mesh["msh_text"],
            context,
            mesh_metadata=mesh["metadata"],
            mesh_stats=mesh["stats"],
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
            result_callback=result_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        results.setdefault("metadata", {})["solve_path"] = "full-3d"
        results.setdefault("metadata", {})["axisymmetric_eligibility_reasons"] = [
            "the solve planner selected the full-3D BEAT formulation"
        ]
        field_traces = results.pop("_field_traces", None)
        field_trace_reason = results.pop("_field_trace_unavailable_reason", None)
        return EngineRunResult(
            results=results,
            msh_text=mesh["msh_text"],
            mesh_stats=mesh["stats"],
            field_traces=field_traces,
            field_trace_unavailable_reason=field_trace_reason,
        )


__all__ = [
    "BEAT_FALLBACK_BACKEND",
    "BeatEngine",
    "BeatUnavailable",
    "announce_beat_warmup_wait",
    "beat_status",
    "resolve_beat_backend",
    "solve_beat_from_msh_text",
]
