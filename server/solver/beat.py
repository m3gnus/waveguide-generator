"""Adapter for ``beat-engine`` (the BEAT Engine Julia solver).

BEAT is not one engine. It is one solver with four interchangeable execution
backends -- CUDA, ROCm, Metal and a portable CPU path -- and WG advertises each
of them as its own selectable engine (``beat-cuda``, ``beat-rocm``,
``beat-metal``, ``beat-cpu``), so a host with both a GPU and a CPU path can
choose between them instead of being handed whichever one a probe picked first.
``beat_backend_statuses`` is where that per-backend answer comes from; the bare
``beat`` name that WG advertised while BEAT was a single entry is still accepted
from stored preferences and design files and resolves to an available variant.

Import/load behavior, staged solve, and result mapping mirror
``server/solver/bempp.py``; absence is a normal capability state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from server.contracts.conventions import PHASE_TIME_CONVENTION
from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.mesh.builder import build_solver_mesh
from server.platform.temp_session import temporary_directory_root

from .acoustics import solver_sound_speed_m_per_s
from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    FULL3D_SOLVER_PORT_MARKER,
    ResultCallback,
    StageCallback,
)
from .context import SolverContext
from .field_traces_store import (
    BEAT_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .adaptive_sweep import (
    enabled as adaptive_enabled,
    native_acquisition_frequencies,
    solve_native_adaptively,
)
from .frequency_sweep import (
    live_execution_frequencies,
    sort_native_result_frequencies,
)
from .imported import read_verified_import_mesh, verify_record_mesh_text
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


from .beat_runtime.warm_cache import signature_scope as solve_signature_scope


class BeatUnavailable(RuntimeError):
    """The optional BEAT Engine package cannot run a solve here."""


#: BEAT's execution backends, in the order AUTO should prefer them: the three
#: accelerators, then the portable path every host with a Julia can run.
BEAT_CPU_BACKEND = "cpu"
BEAT_BACKENDS: tuple[str, ...] = ("cuda", "rocm", "metal", BEAT_CPU_BACKEND)

#: Where ``resolve_beat_backend`` lands when a probe reports available without
#: naming an accelerator. The CPU path is the one backend every host has, so a
#: wrong guess here costs a slow solve rather than a failed device init.
BEAT_FALLBACK_BACKEND = BEAT_CPU_BACKEND

#: The engine name WG advertised while BEAT was a single entry. Design files and
#: stored solve options written before the split still carry it, so it stays a
#: valid request and resolves to whichever variant this host can actually run.
LEGACY_BEAT_ENGINE = "beat"

_BEAT_ENGINE_PREFIX = "beat-"

#: What the selector calls each variant. The accelerator is the part a user
#: chooses between, so it leads; the hardware it needs is what makes a greyed-out
#: row legible without reading its reason.
BEAT_BACKEND_LABELS: dict[str, str] = {
    "cuda": "BEAT \u00b7 CUDA \u2014 NVIDIA GPU",
    "rocm": "BEAT \u00b7 ROCm \u2014 AMD GPU",
    "metal": "BEAT \u00b7 Metal \u2014 Apple GPU",
    BEAT_CPU_BACKEND: "BEAT \u00b7 CPU \u2014 no GPU needed",
}

def beat_geometry_sources(backend: str) -> tuple[str, ...]:
    """What one BEAT backend's adapter solves (``EngineInfo.geometry_sources``).

    Fast AUTO keeps the established CPU-only imported declaration. Accurate, or
    an explicitly named beat-metal, may select a ready accelerator through the
    request-specific imported planner.
    """

    return ("parametric", "imported") if backend == BEAT_CPU_BACKEND else ("parametric",)


def beat_engine_name(backend: str) -> str:
    """The engine name that selects ``backend``."""

    return f"{_BEAT_ENGINE_PREFIX}{backend}"


def beat_engine_backend(engine: str) -> str | None:
    """The BEAT backend an engine name selects, or ``None`` for any other engine.

    The bare legacy ``beat`` deliberately returns ``None`` rather than a
    default: it names the family, not a backend, and the caller that can see
    the host's capabilities is the one entitled to pick which variant it means.
    """

    normalized = str(engine or "").strip().lower()
    if not normalized.startswith(_BEAT_ENGINE_PREFIX):
        return None
    backend = normalized[len(_BEAT_ENGINE_PREFIX) :]
    return backend if backend in BEAT_BACKENDS else None


def is_beat_engine(engine: str) -> bool:
    """Whether ``engine`` names BEAT, as a variant or by the legacy family name."""

    normalized = str(engine or "").strip().lower()
    return normalized == LEGACY_BEAT_ENGINE or beat_engine_backend(normalized) is not None


logger = logging.getLogger(__name__)


def reprobe_package_backend_statuses() -> dict[str, Any] | None:
    """The package's per-backend verdicts, asked again from scratch.

    For after runtime preparation, when every cached verdict may describe a
    host without a Julia or without the GPU runtime. The package's own
    ``functional()`` cache is cleared too: a probe that overlapped provisioning
    can have stored its "false" after provisioning cleared it. Clearing and
    re-reading happen under the probe lock, so a probe still running elsewhere
    finishes first and its answer is discarded rather than read.
    """

    from .official_beat import production_statuses

    return production_statuses(force_refresh=True)


def beat_backend_statuses() -> dict[str, dict[str, Any]]:
    """Read the official engine's independent readiness for all four backends."""
    from .official_beat import production_statuses

    return production_statuses()


def beat_status() -> dict[str, Any]:
    """Select the first ready backend, retaining the CPU reason when none is ready."""
    statuses = beat_backend_statuses()
    return dict(next((statuses[name] for name in BEAT_BACKENDS
                      if statuses[name]["available"]), statuses["cpu"]))


def _clear_status_caches() -> None:
    from .beat_runtime import readiness

    readiness.probe_cache_clear(notify=False)


beat_status.cache_clear = _clear_status_caches
beat_backend_statuses.cache_clear = _clear_status_caches


def resolve_beat_backend(status: Mapping[str, Any]) -> str:
    """Resolve the selected backend, with CPU for legacy unnamed family requests."""

    return str(status.get("backend") or BEAT_FALLBACK_BACKEND)


def announce_beat_warmup_wait(stage_callback: StageCallback | None) -> bool:
    """Report the one-off compilation wait through the normal setup stage."""

    if stage_callback is None or not beat_warmup_in_progress():
        return False
    stage_callback("setup", 0.0, BEAT_WARMUP_STAGE_MESSAGE)
    return True


@solve_signature_scope
def solve_beat_from_msh_text(
    msh_text: str,
    context: SolverContext,
    *,
    backend: str | None = None,
    mesh_metadata: dict[str, Any] | None = None,
    mesh_stats: Mapping[str, Any] | None = None,
    field_trace_cap_bytes: int | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
    _worker_manager: Any = None,
    _julia_executable: str | None = None, _precision: str = "float32",
    _mesh_scale_to_m: float = 1.0,
    _native_result_callback: Any = None,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on a named BEAT Engine backend.

    ``backend`` is the caller's explicit choice, which is what an engine name
    like ``beat-metal`` means. Omitting it keeps the pre-split behaviour: the
    package's own probe picks the backend, which is what the legacy ``beat``
    engine name still asks for.

    Internal conformance hooks capture native
    fields before display packaging discards or rounds them; defaults are inert.
    """

    profile = None
    from .beat_runtime.profile import start_profile

    profile = start_profile(logging.getLogger(__name__).info, "wg")
    context.validate()
    del mesh_metadata
    # TODO: qualify official rigid-ground mesh/frame/field evaluation before enabling it.
    if context.ground_plane is not None:
        raise BeatUnavailable(
            "The HornLab BEAT adapter cannot apply a rigid ground plane; "
            "select a ground-plane-capable engine."
        )
    try:
        reject_beat_infinite_baffle(context)
    except ValueError as exc:
        raise BeatUnavailable(str(exc)) from exc
    from .official_beat import production_statuses

    statuses = production_statuses(backend=backend)
    if profile is not None:
        profile.mark("statuses done")
    if backend is None:
        status = (next((statuses[name] for name in BEAT_BACKENDS
                        if statuses[name]["available"]), statuses["cpu"]))
        if not status["available"]:
            raise BeatUnavailable(status["reason"])
        backend = resolve_beat_backend(status)
    else:
        # Availability is asked per backend, not of BEAT as a whole: on a Mac
        # the package probe reports ``metal``, and answering "is BEAT
        # available" there would refuse a CPU solve the user explicitly chose.
        status = (statuses).get(backend)
        if status is None:
            raise BeatUnavailable(
                f"Unknown BEAT backend {backend!r}; expected one of " + ", ".join(BEAT_BACKENDS)
            )
        if not status["available"]:
            raise BeatUnavailable(status["reason"])
    field_plane_enabled = getattr(context, "polar_config", {}).get("field_plane", True) is True
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

    plane = native_symmetry_plane(context)
    if plane in {"xz", "xy"}:
        raise BeatUnavailable(
            "BEAT native symmetry supports 'yz' half and 'yz+xz' "
            f"quarter domains; {plane!r} is not "
            "representable by the BEAT Engine solver"
        )
    from .beat_adapter.request import build_parametric_request
    from .official_beat import batch_request, response_config, solve_compiled, sort_official_result

    try:
        compiled = build_parametric_request(
            msh_text, context, frequencies_hz=live_execution_frequencies(context), backend=backend,
            engine_id=beat_engine_name(backend), surface_traces=retain_traces,
            precision=_precision, mesh_scale_to_m=_mesh_scale_to_m,
        )
    except ValueError as exc:
        raise BeatUnavailable(str(exc)) from exc
    if profile is not None:
        profile.mark("request built")
    config = response_config(compiled, context)
    if _mesh_scale_to_m != 1.0:
        from .beat_adapter.mesh import scale_msh_text

        msh_text = scale_msh_text(msh_text, _mesh_scale_to_m)

    completed = [0]
    adaptive = adaptive_enabled(context)
    acquisition_count = (
        len(native_acquisition_frequencies(context)) if adaptive else context.num_frequencies
    )

    def progress(index: int, total: int, frequency_hz: float) -> None:
        if adaptive:
            completed[0] += 1
            index, total = completed[0] - 1, acquisition_count
        fraction = (index + 1) / max(1, total)
        if progress_callback:
            progress_callback(fraction)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} with BEAT Engine"
                + (" (native acquisition)" if adaptive else ""),
            )

    def on_frequency_result(index: int, frequency_hz: float, entry: dict[str, Any]) -> bool:
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
                    sound_speed_m_per_s=(343.0),
                ),
            )
        return True


    def stage_status(message: str) -> None:
        if stage_callback and message:
            stage_callback(
                "frequency_solve" if adaptive else "setup",
                completed[0] / max(1, acquisition_count) if adaptive else 0.0,
                message,
            )

    def solve_batch(frequencies):
        return solve_compiled(
            batch_request(compiled, frequencies), channel_id="source", worker_manager=_worker_manager,
            _profile=profile,
            julia_executable=_julia_executable, cancellation_callback=cancellation_callback,
            progress_callback=progress,
            on_frequency_result=None if adaptive else on_frequency_result,
            status_callback=stage_status,
        )

    if adaptive:
        revision = [0]

        def publish(native):
            if result_callback:
                snapshot = build_solver_response(
                    result=native, config=config, context=context, start_time=started,
                    metadata={"provisional": {
                        "completed_frequency_count": native.adaptive_sampling["solved_count"],
                        "expected_frequency_count": context.num_frequencies}},
                    sound_speed_m_per_s=343.0,
                )
                result_callback(revision[0], snapshot)
                revision[0] += 1

        result = solve_native_adaptively(
            context, solve_batch, distance_m=config.observation.distance_m,
            sound_speed=343.0, publish=publish, cancel=cancellation_callback,
        )
    else:
        result = solve_batch(live_execution_frequencies(context))
    sort_official_result(result)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging BEAT Engine solver results")
    if _native_result_callback is not None:
        _native_result_callback("source", result)

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
        "engine": "beat-engine",
        "solver_engine": {
            "engine": beat_engine_name(backend),
            "provider": "official",
            "package": "beat-engine",
            "package_version": status.get("version"),
            "device": backend,
        },
        "phase_time_convention": PHASE_TIME_CONVENTION,
        "beat_backend": backend,
        "device_interface": {
            "selected": f"beat-{backend}",
            f"beat-{backend}": status,
        },
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "beat-engine"},
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
            "precision": "double" if _precision == "float64" else "single",
            "solver_log": json_safe_native_value(response_solver_log(solver_log)),
        },
    }
    metadata["cancelled"] = bool(result.cancelled)
    response = build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=(343.0),
    )
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            [("default", result)],
            config,
            backend=BEAT_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=(343.0),
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
    """One BEAT execution backend, as a selectable WG engine.

    Constructed with the backend its engine name encodes, so the adapter that
    was chosen is the adapter that runs. ``backend=None`` is the legacy
    ``beat`` engine: the package probe picks, exactly as it did before the
    backends became separately selectable.
    """

    solver_port_marker = FULL3D_SOLVER_PORT_MARKER

    def __init__(self, backend: str | None = None) -> None:
        if backend is not None and backend not in BEAT_BACKENDS:
            raise ValueError(
                f"Unknown BEAT backend {backend!r}; expected one of " + ", ".join(BEAT_BACKENDS)
            )
        self.backend = backend
        self.name = LEGACY_BEAT_ENGINE if backend is None else beat_engine_name(backend)

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
        result_cb: ResultCallback | None = None,
        imported_record: Mapping[str, Any] | None = None,
    ) -> EngineRunResult:
        if request.options.ground_plane.enabled:
            raise BeatUnavailable(
                "The HornLab BEAT adapter cannot apply a rigid ground plane; "
                "select a ground-plane-capable engine."
            )
        if isinstance(request.geometry, ImportedGeometrySource):
            return await self._run_imported(
                request,
                imported_record,
                cancel_cb=cancel_cb,
                stage_cb=stage_cb,
                artifact_cb=artifact_cb,
                result_cb=result_cb,
            )
        if imported_record is not None:
            raise BeatUnavailable(
                "The HornLab BEAT adapter was handed an ingestion record for a "
                "request without imported geometry; refusing rather than "
                "guessing which of the two to solve."
            )
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
            backend=self.backend,
            mesh_metadata=mesh["metadata"],
            mesh_stats=mesh["stats"],
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
            result_callback=result_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        results.setdefault("metadata", {})["solve_path"] = "full-3d"
        field_traces = results.pop("_field_traces", None)
        field_trace_reason = results.pop("_field_trace_unavailable_reason", None)
        return EngineRunResult(
            results=results,
            msh_text=mesh["msh_text"],
            mesh_stats=mesh["stats"],
            field_traces=field_traces,
            field_trace_unavailable_reason=field_trace_reason,
        )

    def imported_preflight(
        self,
        record: Mapping[str, Any],
        msh_text: str,
        drive_channels: Sequence[Any] | None = None,
    ) -> str | None:
        """Why this engine cannot solve an ingestion record, asked at submission."""

        from .beat_transducer_imported import enabled, preflight

        if drive_channels and enabled(drive_channels):
            return preflight(record, msh_text, drive_channels, backend=self.backend or "cpu")
        from .beat_imported import imported_beat_preflight

        return imported_beat_preflight(record, msh_text, drive_channels)

    def _imported_refusal(self, accuracy: str) -> str | None:
        # BEAT never sees accuracy, so Fast and Accurate are the same solve.
        # Metal is the qualified accelerator; CUDA and ROCm wait for a run on
        # that hardware and stay Accurate-only.
        if self.backend in (BEAT_CPU_BACKEND, "metal") or accuracy == "accurate":
            return None
        label = BEAT_BACKEND_LABELS.get(self.backend or "", self.name)
        return f"{label} solves CAD returns only in Accurate. Choose Accurate or another engine."

    async def _run_imported(
        self,
        request: SolveRequest,
        imported_record: Mapping[str, Any] | None,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None,
        result_cb: ResultCallback | None,
    ) -> EngineRunResult:
        refusal = self._imported_refusal(request.options.accuracy)
        if refusal is not None:
            raise BeatUnavailable(refusal)
        if imported_record is None:
            raise ValueError("imported BEAT solve requires its ingestion record")
        from .beat_imported import solve_imported_beat_from_msh_text

        # The verified record mesh, never a re-mesh: the same bytes Metal is
        # handed for the same record.
        execution_msh = imported_record.get("_execution_msh_text")
        if isinstance(execution_msh, str):
            msh_text = verify_record_mesh_text(imported_record, execution_msh)
        else:
            msh_text = await asyncio.to_thread(read_verified_import_mesh, imported_record)
        mesh_record = imported_record.get("mesh")
        mesh_stats = (
            dict(mesh_record.get("stats") or {}) if isinstance(mesh_record, Mapping) else {}
        )
        if artifact_cb is not None:
            await artifact_cb(msh_text, mesh_stats)
        cancel_cb()
        results = await asyncio.to_thread(
            solve_imported_beat_from_msh_text,
            msh_text,
            request,
            imported_record,
            backend=self.backend or BEAT_CPU_BACKEND,
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
            result_callback=result_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh_stats
        channel_bases = results.pop("_channel_bases_npz", None)
        field_traces = results.pop("_field_traces", None)
        field_trace_reason = results.pop("_field_trace_unavailable_reason", None)
        return EngineRunResult(
            results=results,
            msh_text=msh_text,
            mesh_stats=mesh_stats,
            channel_bases=channel_bases,
            field_traces=field_traces,
            field_trace_unavailable_reason=field_trace_reason,
        )


__all__ = [
    "BEAT_BACKENDS",
    "BEAT_BACKEND_LABELS",
    "BEAT_CPU_BACKEND",
    "BEAT_FALLBACK_BACKEND",
    "LEGACY_BEAT_ENGINE",
    "BeatEngine",
    "BeatUnavailable",
    "announce_beat_warmup_wait",
    "beat_backend_statuses",
    "beat_engine_backend",
    "beat_engine_name",
    "beat_geometry_sources",
    "beat_status",
    "is_beat_engine",
    "reprobe_package_backend_statuses",
    "resolve_beat_backend",
    "solve_beat_from_msh_text",
]
