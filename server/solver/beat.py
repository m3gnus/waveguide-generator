"""Adapter for ``hornlab-beat-bem`` (the BEAT Engine Julia solver).

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

#: Named in an unavailable reason, so a greyed-out row says what it would take.
_BEAT_BACKEND_PREREQUISITES: dict[str, str] = {
    "cuda": "an NVIDIA GPU with a functional CUDA.jl",
    "rocm": "an AMD ROCm runtime with a functional AMDGPU.jl",
    "metal": "an Apple Silicon GPU with a functional Metal.jl",
}

#: How the package's probe names each accelerator family in its own prose.
_BEAT_BACKEND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cuda": ("cuda", "nvidia"),
    "rocm": ("rocm", "amdgpu", "amd"),
    "metal": ("metal", "apple"),
}


def _probe_reason_is_about(backend: str, reason: str) -> bool:
    """Whether the probe's verdict is about this accelerator family alone.

    The probe answers for BEAT as a whole, so its reason lands on one of three
    shapes, and only one of them belongs on a given row. "An NVIDIA GPU is
    present but the CUDA path is not usable: ..." is exactly what a user with a
    broken CUDA install needs to read -- and is noise on the Metal row. The
    verdict for a host with no accelerator at all names every family at once,
    and belongs on none of them: each row's own prerequisite sentence already
    says what is missing, and that generic verdict also carries advice about
    BEAT being GPU-only which stopped being true when the CPU backend became a
    user-facing engine.

    Naming exactly one family is what separates the two, so that is the test.
    It is prose matching and it can only ever lose detail: a reason this does
    not recognise is left off a row that still states its own prerequisite,
    which is the sentence a user acts on.
    """

    lowered = reason.lower()
    mine = _BEAT_BACKEND_KEYWORDS.get(backend, ())
    others = {
        word
        for name, words in _BEAT_BACKEND_KEYWORDS.items()
        if name != backend
        for word in words
    }
    return any(word in lowered for word in mine) and not any(
        word in lowered for word in others
    )


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


def _cpu_backend_status(package: Any) -> tuple[bool, str]:
    """Whether a BEAT CPU solve could start here, without paying a Julia startup.

    This used to answer "is there a Julia executable, and is the bundled project
    on disk", and call that available. Both can be true on a machine where the
    first solve then fails: the project's checked-in Manifest names packages
    that nothing has downloaded, so an offline or never-instantiated host
    discovered the problem inside a user's job instead of on the capability row.

    ``server/solver/beat_cpu_runtime.py`` asks the question that has an honest
    answer -- has ``hornlab_beat_bem.provision`` instantiated this project and
    proved it with a real 1 kHz solve -- and it is still cheap: a small JSON
    read and a content hash rather than the Julia launch the accelerator rows
    pay for. The reason it returns carries the command that fixes whatever it
    found.
    """

    from .beat_cpu_runtime import cpu_runtime_readiness

    readiness = cpu_runtime_readiness(package)
    return readiness.ready, readiness.reason


def beat_backend_statuses() -> dict[str, dict[str, Any]]:
    """One status per BEAT backend, from a single package probe.

    ``hornlab_beat_bem.beat_engine_status`` answers a deliberately different
    question: which *one* backend a solve would use, taking the first
    accelerator family whose hardware is present. That is the right answer for
    AUTO and the wrong one for a selector, which has to say something about
    every backend a user might pick -- including the CPU path, which the probe
    only ever names under ``HORNLAB_BEAT_FORCE_CPU`` and which is in fact
    available wherever a Julia is.

    Nothing here re-implements the package's detection, which is the whole
    point: the probe runs once, the backend it named is available for the
    reason it gave, and every other backend reports its own prerequisite
    alongside what the probe actually found. Two copies of "is there a CUDA
    device" would be one upstream edit away from disagreeing, and the copy that
    drifted would be the one in the dropdown.

    The known gap is a host with two accelerator families -- an NVIDIA card and
    an AMD card in the same box. The probe names the first, so the second reads
    unavailable here even though it would work. That under-declares rather than
    over-promises, which is the safe direction, and closing it needs a
    per-backend probe in ``hornlab-beat-bem`` rather than a second detector in
    this file.
    """

    package = _load_api()
    if package is None:
        reason = (
            "hornlab-beat-bem is not importable (optional BEAT engine not installed)."
        )
        return {
            backend: {
                "available": False,
                "reason": reason,
                "version": None,
                "backend": backend,
                "surface_traces": False,
            }
            for backend in BEAT_BACKENDS
        }

    status = beat_status()
    probe_reason = str(status.get("reason") or "beat capability probe returned no reason")
    selected = str(status.get("backend") or "") if status.get("available") else ""
    statuses: dict[str, dict[str, Any]] = {}
    for backend in BEAT_BACKENDS:
        # CPU has its own provisioning evidence.  The package probe may name
        # CPU when forced, but that selection alone does not prove the runtime
        # can solve.
        if backend == BEAT_CPU_BACKEND:
            available, reason = _cpu_backend_status(package)
        elif backend == selected:
            available, reason = True, probe_reason
        elif selected:
            available = False
            reason = (
                f"Needs {_BEAT_BACKEND_PREREQUISITES[backend]}. The BEAT probe "
                f"selected the {selected} backend on this host instead."
            )
        else:
            available = False
            reason = f"Needs {_BEAT_BACKEND_PREREQUISITES[backend]}."
            if _probe_reason_is_about(backend, probe_reason):
                # Attributed rather than stated as WG's own: it is the evidence
                # a user needs, written by a component that is pinned and
                # re-pinned separately from this sentence.
                reason += f" The BEAT probe reported: {probe_reason}"
        statuses[backend] = {
            "available": available,
            "reason": reason,
            "version": status.get("version"),
            "backend": backend,
            # Detected, not assumed, and identical across backends: surface-trace
            # retention is a property of the installed package, not the device.
            "surface_traces": bool(status.get("surface_traces")),
        }
    return statuses


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
    backend: str | None = None,
    mesh_metadata: dict[str, Any] | None = None,
    mesh_stats: Mapping[str, Any] | None = None,
    field_trace_cap_bytes: int | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on a named BEAT Engine backend.

    ``backend`` is the caller's explicit choice, which is what an engine name
    like ``beat-metal`` means. Omitting it keeps the pre-split behaviour: the
    package's own probe picks the backend, which is what the legacy ``beat``
    engine name still asks for.
    """

    context.validate()
    del mesh_metadata
    if context.solver_mode == "circsym":
        raise ValueError("BEAT cannot run solver_mode='circsym'; use the Axisymmetric runner or full_3d")
    reject_beat_infinite_baffle(context)
    package = _load_api()
    if package is None:
        raise BeatUnavailable("hornlab-beat-bem is not installed.")
    if backend is None:
        status = beat_status()
        if not status["available"]:
            raise BeatUnavailable(status["reason"])
        backend = resolve_beat_backend(status)
    else:
        # Availability is asked per backend, not of BEAT as a whole: on a Mac
        # the package probe reports ``metal``, and answering "is BEAT
        # available" there would refuse a CPU solve the user explicitly chose.
        status = beat_backend_statuses().get(backend)
        if status is None:
            raise BeatUnavailable(
                f"Unknown BEAT backend {backend!r}; expected one of "
                + ", ".join(BEAT_BACKENDS)
            )
        if not status["available"]:
            raise BeatUnavailable(status["reason"])
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
    """One BEAT execution backend, as a selectable WG engine.

    Constructed with the backend its engine name encodes, so the adapter that
    was chosen is the adapter that runs. ``backend=None`` is the legacy
    ``beat`` engine: the package probe picks, exactly as it did before the
    backends became separately selectable.
    """

    def __init__(self, backend: str | None = None) -> None:
        if backend is not None and backend not in BEAT_BACKENDS:
            raise ValueError(
                f"Unknown BEAT backend {backend!r}; expected one of "
                + ", ".join(BEAT_BACKENDS)
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
            backend=self.backend,
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
    "beat_status",
    "is_beat_engine",
    "resolve_beat_backend",
    "solve_beat_from_msh_text",
]
