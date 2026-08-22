"""Guarded CPU fallback adapter for ``hornlab-bempp-bem``.

The import/load behavior, symmetry, source-motion feature detection, staged
frequency solve, and result mapping port v1
``server/solver/bempp_solver.py:21-67,153-188,229-356``.  Absence is a normal
capability state and never gets faked.
"""

from __future__ import annotations

from functools import lru_cache
import importlib
import importlib.metadata
import logging
import os
import platform
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from server.jobs.models import SolveRequest
from server.mesh.builder import build_solver_mesh
from server.preview.translate import has_closed_outer_body

from .acoustics import solver_sound_speed_m_per_s
from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    ResultCallback,
    StageCallback,
)
from .context import SolverContext
from .frequency_sweep import (
    live_execution_frequencies,
    sort_native_result_frequencies,
)
from .field_traces_store import (
    BEMPP_FIELD_TRACE_BACKEND,
    build_field_trace_artifact,
    field_trace_retention_plan,
)
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .infinite_baffle import require_coupled_aperture_tag
from .result_mapping import (
    build_provisional_frequency_response,
    build_solver_response,
    json_safe_native_value,
    native_observation_frame,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_bempp_bem import (
        BIEFormulation,
        ObservationConfig,
        ObservationFrame,
        SolveConfig,
        solve as bempp_solve,
    )
    from hornlab_bempp_bem import solve_frequencies as bempp_solve_frequencies
except (ImportError, OSError):
    BIEFormulation = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    ObservationFrame = None  # type: ignore[assignment]
    SolveConfig = None  # type: ignore[assignment]
    bempp_solve = None  # type: ignore[assignment]
    bempp_solve_frequencies = None  # type: ignore[assignment]


class BemppUnavailable(RuntimeError):
    """The optional BEMPP fallback package is not importable."""


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _version() -> str | None:
    # Resolving a distribution version walks sys.path for *.dist-info; the
    # answer cannot change inside a running process.
    try:
        return importlib.metadata.version("hornlab-bempp-bem")
    except importlib.metadata.PackageNotFoundError:
        return None


#: The sweep runs in one cancellable process unless the operator asks for more.
#:
#: ``WG2_SOLVE_WORKERS=0`` selects the engine's own auto mode, which splits a
#: sweep across processes once each worker would get at least 40 frequencies;
#: any positive integer forces that count. Auto was the default until it was
#: measured on an M1 Max (10 cores, macOS 15.5), 766-triangle quarter-domain
#: OSSE mesh, numba assembly backend, wall clock as mean over 3 repeats:
#:
#:   frequencies   workers=1          auto              workers=4
#:   79            64.1 s (sd 4.4)    66.3 s (1 proc)   59.7 s (sd 3.3)
#:   80            65.6 s (sd 5.2)    67.4 s (2 proc)   59.2 s (sd 4.5)
#:   200          166.7 s (sd 8.0)   111.4 s (5 proc)  116.0 s (sd 5.4)
#:
#: One process already draws 5.1 CPU-seconds per wall-second of the ten
#: available, so splitting the sweep buys the remaining headroom rather than
#: idle cores: 200 frequencies across five workers spent 24% more total CPU
#: (954 against 771 CPU-seconds) to finish 1.33x sooner in that run, and at 80
#: -- where auto first splits -- the difference was inside the run-to-run
#: spread.
#:
#: What it costs is Stop. The parent can only raise between progress events and
#: must then join every sibling chunk, so a cancelled 200-frequency sweep
#: returned after 88 s instead of 0.3 s, having solved the whole sweep anyway;
#: and because each spawned worker re-JITs bempp-cl's kernels, the first
#: cancellable moment moves from 0.6 s to 21 s, re-creating the cold-start
#: window ``server/solver/warmup.py`` exists to keep off the user's solve.
#: Charging 88 s to the user who just said the remaining time was not worth it,
#: to save 55 s for the user who did not, is the wrong way round.
#:
#: Results themselves are not at stake: serial and parallel payloads for the
#: same design and sweep were byte-identical in compact JSON at both 80 and 200
#: frequencies, once per-frequency wall-clock timings were excluded.
DEFAULT_SOLVE_WORKERS = 1


def _resolved_workers() -> int:
    """How many processes the frequency sweep may use.

    Zero means the engine's own auto mode; see ``DEFAULT_SOLVE_WORKERS`` for
    what it was measured to cost and why it is not the default.
    """

    raw = os.environ.get("WG2_SOLVE_WORKERS", "").strip()
    if not raw:
        return DEFAULT_SOLVE_WORKERS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Ignoring WG2_SOLVE_WORKERS=%r: expected a non-negative integer", raw
        )
        return DEFAULT_SOLVE_WORKERS


#: ``hornlab_bempp_bem`` splits an auto sweep only when every worker would get
#: at least this many frequencies, because a spawned worker re-imports bempp-cl
#: and re-JITs its kernels before it can solve anything.
_ENGINE_MIN_FREQUENCIES_PER_WORKER = 40


def _sweep_will_split(workers: int, num_frequencies: int) -> bool:
    """Will this sweep really run in more than one process?

    An explicit count above one always splits -- the engine honours the caller
    over its own arithmetic -- while auto splits only once the sweep is long
    enough. Both cases cost the same thing at Stop, so both have to be said out
    loud, and neither may be claimed when the solve is going to stay in one
    process after all.
    """

    if workers != 0:
        return workers > 1
    try:
        package = importlib.import_module("hornlab_bempp_bem")
        resolve_worker_count = getattr(package, "_resolve_worker_count")
    except (ImportError, OSError, AttributeError):
        return num_frequencies >= 2 * _ENGINE_MIN_FREQUENCIES_PER_WORKER
    return resolve_worker_count(0, num_frequencies) > 1


def _load_api() -> bool:
    global BIEFormulation, ObservationConfig, ObservationFrame, SolveConfig, bempp_solve
    global bempp_solve_frequencies
    if (
        ObservationConfig is not None
        and ObservationFrame is not None
        and SolveConfig is not None
        and bempp_solve is not None
    ):
        return True
    try:
        package = importlib.import_module("hornlab_bempp_bem")
        BIEFormulation = getattr(package, "BIEFormulation")
        ObservationConfig = getattr(package, "ObservationConfig")
        ObservationFrame = getattr(package, "ObservationFrame")
        SolveConfig = getattr(package, "SolveConfig")
        bempp_solve = getattr(package, "solve")
        # Optional: an older pin without it stays usable for generated grids and
        # only refuses explicit lists, at the adapter guard below.
        bempp_solve_frequencies = getattr(package, "solve_frequencies", None)
    except (ImportError, OSError, AttributeError):
        return False
    return True


# Compiled-extension runtime DLLs that Windows does not ship by default. numba
# and llvmlite link against these; the diagnosis and remedy are v1's
# ``server/scripts/check_solver_engine.py``.
_WINDOWS_RUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")

_VCREDIST_GUIDANCE = (
    "Install the Microsoft Visual C++ Redistributable (x64), then start again: "
    "winget install --id Microsoft.VCRedist.2015+.x64 --scope user "
    "(or https://aka.ms/vs/17/release/vc_redist.x64.exe)"
)


def _missing_windows_runtime_dlls() -> list[str]:
    """Return the VC++ runtime DLLs that will not load. Windows only."""

    if platform.system() != "Windows":
        return []
    import ctypes

    missing = []
    for name in _WINDOWS_RUNTIME_DLLS:
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


PREFERRED_ASSEMBLY_BACKEND = "opencl"
FALLBACK_ASSEMBLY_BACKEND = "numba"

#: The device type the solve really asks bempp-cl for, below. The probe has to
#: look for the same one: bempp-cl's dense assembly does not run on a GPU-only
#: inventory, and reporting a GPU as proof that OpenCL works is how Apple
#: Silicon got a READY capability report and then an ``OpenCL cpu device could
#: not be initialized`` in the middle of every solve.
OPENCL_DEVICE_TYPE = "cpu"

_OPENCL_GUIDANCE = (
    "Install an OpenCL CPU runtime and start again: on Windows the Intel CPU "
    "Runtime for OpenCL registers an ICD under "
    "HKLM\\SOFTWARE\\Khronos\\OpenCL\\Vendors; on Linux install pocl or your "
    "vendor's ICD. Apple Silicon has no CPU OpenCL device at all, so BEMPP "
    "assembles on numba there and Metal is the engine to prefer."
)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"


def _bempp_default_cpu_device() -> Any:
    """Return the concrete device bempp-cl will use for ``opencl_device='cpu'``."""

    from bempp_cl.core.opencl_kernels import default_cpu_device

    return default_cpu_device()


def _opencl_status() -> tuple[bool, str]:
    """Is there a device to assemble on, not merely a pyopencl import?

    pyopencl imports cleanly with no ICD installed and only fails when something
    asks for a platform, which would be the solve. Enumerating here keeps a
    missing runtime a capability answer instead of a mid-solve crash.

    The device has to be of the type the solve will ask for -- see
    ``OPENCL_DEVICE_TYPE``. Accepting any device made this probe answer a
    different question from the one the solve asks, which on Apple Silicon,
    whose ICD exposes the GPU and no CPU, meant a READY report followed by a
    failure inside every solve.
    """

    try:
        import pyopencl
    except BaseException as exc:  # noqa: BLE001 - a broken build raises more than ImportError
        return False, f"pyopencl cannot load ({_describe(exc)}). {_OPENCL_GUIDANCE}"

    try:
        platforms = pyopencl.get_platforms()
    except Exception as exc:  # noqa: BLE001 - pyopencl raises its own LogicError
        return False, f"no OpenCL platform is usable ({_describe(exc)}). {_OPENCL_GUIDANCE}"
    try:
        wanted = getattr(pyopencl.device_type, OPENCL_DEVICE_TYPE.upper())
    except AttributeError as exc:  # a partial build is unusable, not fatal
        return False, f"pyopencl exposes no device types ({_describe(exc)}). {_OPENCL_GUIDANCE}"
    seen: list[str] = []
    for platform_entry in platforms:
        try:
            devices = platform_entry.get_devices(device_type=wanted)
        except Exception:  # noqa: BLE001 - DEVICE_NOT_FOUND, and broken ICDs, are both "no"
            devices = []
        if devices:
            try:
                selected = _bempp_default_cpu_device()
            except BaseException as exc:  # noqa: BLE001 - native loaders raise broad failures
                return False, (
                    "bempp-cl cannot initialize its default OpenCL cpu device "
                    f"({_describe(exc)}). {_OPENCL_GUIDANCE}"
                )
            selected_type = getattr(selected, "type", None)
            try:
                selected_is_wanted = bool(int(selected_type) & int(wanted))
            except (TypeError, ValueError):
                selected_is_wanted = selected_type == wanted
            selected_name = str(getattr(selected, "name", "unknown device")).strip()
            selected_platform = str(
                getattr(getattr(selected, "platform", None), "name", platform_entry.name)
            ).strip()
            if not selected_is_wanted:
                return False, (
                    f"bempp-cl selected OpenCL device {selected_name} ({selected_platform}), "
                    f"which is not a {OPENCL_DEVICE_TYPE} device. {_OPENCL_GUIDANCE}"
                )
            return True, (
                f"bempp-cl assembles on OpenCL device {selected_name} "
                f"({selected_platform})"
            )
        try:
            seen.extend(device.name.strip() for device in platform_entry.get_devices())
        except Exception:  # noqa: BLE001 - naming what is there is a courtesy, not a contract
            continue
    inventory = f" Devices found: {', '.join(seen)}." if seen else ""
    return False, (
        f"an OpenCL runtime is present but exposes no {OPENCL_DEVICE_TYPE} device, "
        f"which is the one bempp-cl assembles on.{inventory} {_OPENCL_GUIDANCE}"
    )


def numba_fallback_warning(opencl_reason: str) -> str:
    """Say which backend is really running and exactly what to fix."""

    return (
        "Falling back to the numba assembly backend because OpenCL is unusable: "
        f"{opencl_reason} Until that is fixed, solves assemble on numba, which is "
        "slower, and the first solve after each start spends roughly a minute "
        "compiling kernels. Stop remains prompt because WG runs native BEMPP in "
        "an isolated worker; cancelling during compilation discards that worker "
        "and the replacement must compile again on the next solve."
    )


def _assembly_backend_status() -> tuple[bool, str, str | None, str | None]:
    """Resolve the backend a solve would really use: (usable, reason, backend, warning).

    ``hornlab_bempp_bem`` is a thin pure-Python wrapper, so it imports happily
    on a host where bempp-cl's engine cannot load at all. v1 hit exactly this on
    clean Windows: the installer said "Bempp ready", the preflight said READY,
    and every solve then died on ``ImportError: Numba could not be imported``
    because the compiled extensions need a redistributable Windows does not
    install by default. Reporting importability as availability reproduces that
    bug, so probe the backend the solve path really uses.

    OpenCL is the production backend and is preferred. numba remains a working
    fallback rather than a hard failure, but it is never chosen silently: the
    reason it was chosen, and the remedy, travel with the capability report.

    This still stops short of assembling an operator: a kernel that only fails
    once it is built would get past it.
    """

    try:
        importlib.import_module("bempp_cl.api")
    except BaseException as exc:  # noqa: BLE001 - these raise bare ImportError chains
        detail = _describe(exc)
        missing = _missing_windows_runtime_dlls()
        if missing:
            return False, f"bempp_cl cannot load ({detail}). Missing {', '.join(missing)}. {_VCREDIST_GUIDANCE}", None, None
        return False, f"bempp_cl cannot load, so no assembly backend can run a solve ({detail}).", None, None

    opencl_usable, opencl_reason = _opencl_status()
    if opencl_usable:
        return True, opencl_reason, PREFERRED_ASSEMBLY_BACKEND, None

    try:
        importlib.import_module("numba")
    except BaseException as exc:  # noqa: BLE001 - numba raises bare ImportError chains
        detail = _describe(exc)
        missing = _missing_windows_runtime_dlls()
        remedy = f" Missing {', '.join(missing)}. {_VCREDIST_GUIDANCE}" if missing else ""
        return (
            False,
            f"no assembly backend can run a solve. OpenCL: {opencl_reason} numba also "
            f"failed ({detail}).{remedy}",
            None,
            None,
        )

    warning = numba_fallback_warning(opencl_reason)
    return True, warning, FALLBACK_ASSEMBLY_BACKEND, warning


class _BemppProbeUnavailable(RuntimeError):
    """Carries an unavailable probe result past the cache, which must not keep it."""

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(status.get("reason", "bempp is unavailable"))
        self.status = status


def _probe_bempp_status() -> dict[str, Any]:
    if not _load_api():
        return {
            "available": False,
            "reason": "hornlab-bempp-bem is not importable (optional CPU fallback not installed).",
            "version": _version(),
            "assembly_backend": None,
            "warning": None,
        }
    usable, reason, backend, warning = _assembly_backend_status()
    coupled_infinite_baffle = False
    if SolveConfig is not None:
        try:
            SolveConfig(aperture_tag=1)
        except TypeError:
            pass
        else:
            coupled_infinite_baffle = True
    return {
        "available": usable,
        "reason": reason,
        "version": _version(),
        "assembly_backend": backend,
        "warning": warning,
        "coupled_infinite_baffle": coupled_infinite_baffle,
    }


@lru_cache(maxsize=1)
def _cached_successful_bempp_status() -> dict[str, Any]:
    status = _probe_bempp_status()
    if not status["available"]:
        # functools does not cache exceptions, so an unavailable result is
        # re-probed next time -- installing an OpenCL runtime must take effect
        # without restarting the server.
        raise _BemppProbeUnavailable(status)
    return status


def bempp_status() -> dict[str, Any]:
    """Report whether a solve can run, not merely whether the wrapper imports.

    Successful probes are cached, following ``server/solver/metal.py``. The
    probe imports ``bempp_cl.api``, enumerates every OpenCL platform and its
    devices -- which loads ICD DLLs -- and reads distribution metadata, and it
    was being run again at the start of every single solve.
    """

    try:
        status = _cached_successful_bempp_status()
    except _BemppProbeUnavailable as exc:
        status = exc.status
    return dict(status)


# The public cache-maintenance hook, matching metal_status.
bempp_status.cache_clear = _cached_successful_bempp_status.cache_clear  # type: ignore[attr-defined]


def _closed_mode(context: SolverContext) -> bool:
    """Whether the mesh under validation is a closed body.

    Resolved through the translation layer that built the mesh, so an omitted
    wall thickness -- which the mesher turns into ATH's 5 mm shell -- is judged
    closed rather than mistaken for a bare shell and let past the check.
    """

    return has_closed_outer_body(context.design.root)


def solve_bempp_from_msh_text(
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
    force_serial: bool = False,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on the guarded CPU backend."""

    context.validate()
    if context.solver_mode == "circsym":
        raise ValueError(
            "BEMPP full 3D cannot execute the axisymmetric formulation; "
            "the solve planner must route it to the Axisymmetric runner"
        )
    if not _load_api() or SolveConfig is None or bempp_solve is None:
        raise BemppUnavailable("hornlab-bempp-bem is not installed.")
    status = bempp_status()
    if not status["available"]:
        raise BemppUnavailable(status["reason"])
    aperture_tag = require_coupled_aperture_tag(
        context,
        mesh_metadata,
        backend="BEMPP",
    )
    if aperture_tag is not None and not status.get("coupled_infinite_baffle"):
        raise BemppUnavailable(
            "Installed hornlab-bempp-bem does not support coupled "
            "infinite-baffle aperture tags."
        )
    if context.frequencies_hz is not None and bempp_solve_frequencies is None:
        raise BemppUnavailable(
            "Installed hornlab-bempp-bem does not support explicit frequency lists."
        )
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
            supported=aperture_tag is None,
            unsupported_reason="unsupported_coupled_infinite_baffle",
            cap_bytes=field_trace_cap_bytes,
        )
    )
    backend = status.get("assembly_backend") or PREFERRED_ASSEMBLY_BACKEND
    started = time.time()
    if status.get("warning"):
        # The user asked for a solve, not for a lecture, but silently assembling
        # on the slow backend is how someone spends a week wondering why.
        logger.warning("%s", status["warning"])
        if stage_callback:
            stage_callback("setup", 0.0, status["warning"])
    if stage_callback:
        stage_callback("setup", 0.0, f"Configuring BEMPP BEM solve ({backend})")

    def progress(index: int, total: int, frequency_hz: float) -> None:
        if cancellation_callback:
            cancellation_callback()
        fraction = index / max(1, total)
        if progress_callback:
            progress_callback(fraction)
        if stage_callback:
            stage_callback(
                "frequency_solve",
                fraction,
                f"Solving frequency {index + 1}/{total} with BEMPP BEM",
            )

    def on_frequency_result(
        index: int, frequency_hz: float, entry: dict[str, Any]
    ) -> bool:
        if cancellation_callback:
            cancellation_callback()
        if result_callback is not None:
            result_callback(
                index,
                build_provisional_frequency_response(
                    index=index,
                    frequency_hz=frequency_hz,
                    entry=entry,
                    config=config,
                    context=context,
                    backend="bempp",
                    sound_speed_m_per_s=solver_sound_speed_m_per_s(
                        "hornlab_bempp_bem"
                    ),
                ),
            )
        return True

    formulation = DEFAULT_BEM_FORMULATION
    if BIEFormulation is not None:
        formulation = getattr(BIEFormulation, "COMPLEX_K", formulation)
    requested_workers = _resolved_workers()
    workers = (
        1
        if force_serial or context.frequencies_hz is not None or aperture_tag is not None
        else requested_workers
    )
    if force_serial and requested_workers != 1:
        message = (
            f"Ignoring WG2_SOLVE_WORKERS={requested_workers} inside the killable "
            "BEMPP worker. WG keeps one warm serial native process so Stop can "
            "terminate the complete solve tree promptly."
        )
        logger.warning("%s", message)
        if stage_callback:
            stage_callback("setup", 0.0, message)
    config_kwargs: dict[str, Any] = {
        "freq_min_hz": context.frequency_range[0],
        "freq_max_hz": context.frequency_range[1],
        "freq_count": context.num_frequencies,
        "freq_spacing": context.frequency_spacing,
        "formulation": formulation,
        "complex_k_shift": DEFAULT_COMPLEX_K_SHIFT,
        "observation": observation_config(
            context,
            ObservationConfig,
            BemppUnavailable,
            "hornlab-bempp-bem",
            msh_text=msh_text,
        ),
        "frame_override": native_observation_frame(
            context,
            msh_text,
            ObservationFrame,
        ),
        "progress_callback": progress,
        "mesh_scale": 1.0,
        "native_symmetry_plane": (
            None if aperture_tag is not None else native_symmetry_plane(context)
        ),
        "assembly_backend": backend,
        "opencl_device": OPENCL_DEVICE_TYPE,
        "precision": "single",
        "return_surface_traces": retain_traces,
    }
    if aperture_tag is not None:
        config_kwargs["aperture_tag"] = aperture_tag
    # The BEMPP package's parallel sweep deliberately has no callback seam.
    # Keep an explicit multi-worker opt-in fast; the default serial/cancellable
    # path streams provisional rows just like Metal and Boundary Lab.
    if result_callback is not None and workers == 1:
        config_kwargs["on_frequency_result"] = on_frequency_result
    try:
        config = SolveConfig(**config_kwargs)
    except TypeError as exc:
        message = str(exc)
        if "formulation" in message:
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support the required BEM formulation option.") from exc
        if "complex_k_shift" in message:
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support the required complex-k shift option.") from exc
        if "frame_override" in message:
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support the required explicit observation frame.") from exc
        if "on_frequency_result" in message:
            raise BemppUnavailable(
                "Installed hornlab-bempp-bem does not support streamed frequency results."
            ) from exc
        if "return_surface_traces" in message:
            raise BemppUnavailable(
                "Installed hornlab-bempp-bem does not support retained surface traces."
            ) from exc
        if "aperture_tag" in message:
            raise BemppUnavailable(
                "Installed hornlab-bempp-bem does not support coupled "
                "infinite-baffle aperture tags."
            ) from exc
        raise
    if context.source_motion != "normal":
        if not hasattr(config, "source_motion"):
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support axial source motion.")
        config.source_motion = context.source_motion
    if hasattr(config, "require_closed_mesh"):
        config.require_closed_mesh = (
            context.mesh_validation_mode == "strict" and _closed_mode(context)
        )
    if hasattr(config, "workers"):
        config.workers = workers
        if _sweep_will_split(workers, context.num_frequencies):
            # Saying so before the solve starts means nobody discovers the
            # cancellation caveat by pressing Stop.
            message = (
                f"WG2_SOLVE_WORKERS={workers} splits this sweep of "
                f"{context.num_frequencies} frequencies across worker processes. "
                "Stop cannot cancel a frequency already running in one, so a "
                "cancelled sweep returns only once every worker has finished; "
                "unset the variable for the default single cancellable process."
            )
            logger.info("%s", message)
            if stage_callback:
                stage_callback("setup", 0.0, message)

    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as handle:
            path = Path(handle.name)
            handle.write(msh_text)
        if (
            result_callback is not None
            and workers == 1
            and bempp_solve_frequencies is not None
        ):
            result = bempp_solve_frequencies(
                str(path), live_execution_frequencies(context).tolist(), config
            )
            sort_native_result_frequencies(result)
        elif context.frequencies_hz is None:
            result = bempp_solve(str(path), config)
        else:
            result = bempp_solve_frequencies(
                str(path), list(context.frequencies_hz), config
            )
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temporary BEMPP mesh %s: %s", path, exc)
    if cancellation_callback:
        cancellation_callback()
    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging BEMPP BEM solver results")

    metadata = {
        "solver_backend": "bempp",
        "solver_mode": "full_3d",
        "engine": "hornlab-bempp-bem",
        "phase_time_convention": "exp(+ikr)",
        "assembly_backend": backend,
        "assemblyBackend": backend,
        "assembly_backend_warning": status.get("warning"),
        "device_interface": {
            "selected": f"bempp-cl-{backend}",
            f"bempp-cl-{backend}": status,
        },
        "mesh_validation": {"mode": context.mesh_validation_mode, "backend": "hornlab-bempp-bem"},
        "verbose": context.verbose,
        "performance": {
            "total_time_seconds": time.time() - started,
            "native_timings": json_safe_native_value(dict(getattr(result, "timings", {}) or {})),
        },
        "field_trace_retention": {
            "estimated_bytes": trace_estimated_bytes,
            "cap_bytes": trace_cap_bytes,
        },
        "bempp": {
            "native_symmetry_plane": getattr(config, "native_symmetry_plane", None),
            "formulation": json_safe_native_value(getattr(config, "formulation", formulation)),
            "complex_k_shift": float(getattr(config, "complex_k_shift", DEFAULT_COMPLEX_K_SHIFT)),
            "assembly_backend": backend,
            "opencl_device": getattr(config, "opencl_device", OPENCL_DEVICE_TYPE),
            "precision": getattr(config, "precision", "single"),
            "workers": getattr(config, "workers", 1),
            "solver_log": json_safe_native_value(response_solver_log(getattr(result, "solver_log", []))),
        },
    }
    if aperture_tag is not None:
        metadata["infinite_baffle"] = {
            "backend": "full_3d_coupled",
            "aperture_tag": aperture_tag,
            "source": "hornlab-waveguide-mesher",
        }
        metadata["bempp"]["aperture_tag"] = aperture_tag
    response = build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
        sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
    )
    field_traces = (
        build_field_trace_artifact(
            msh_text,
            [("default", result)],
            config,
            backend=BEMPP_FIELD_TRACE_BACKEND,
            sound_speed_m_per_s=solver_sound_speed_m_per_s("hornlab_bempp_bem"),
        )
        if retain_traces
        else None
    )
    if retain_traces and field_traces is None:
        trace_reason = "trace_output_missing"
    response["_field_traces"] = field_traces
    response["_field_trace_unavailable_reason"] = trace_reason
    return response


class BemppEngine:
    name = "bempp"

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
            from .circsym import AxisymmetricEngine

            return await AxisymmetricEngine().run(
                request,
                cancel_cb=cancel_cb,
                stage_cb=stage_cb,
                artifact_cb=artifact_cb,
                result_cb=result_cb,
            )
        context = SolverContext.from_request(request, solver_mode="full_3d")
        mesh = await build_solver_mesh(
            request.design,
            request.options,
            cancel_cb,
            lambda stage, progress, message: stage_cb(stage, progress, message),
        )
        if artifact_cb is not None:
            await artifact_cb(mesh["msh_text"], mesh["stats"])
        cancel_cb()
        from .bempp_process import solve_bempp_in_process

        results = await solve_bempp_in_process(
            mesh["msh_text"],
            context,
            mesh_metadata=mesh["metadata"],
            mesh_stats=mesh["stats"],
            cancel_cb=cancel_cb,
            stage_cb=stage_cb,
            result_cb=result_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        results.setdefault("metadata", {})["solve_path"] = "full-3d"
        results.setdefault("metadata", {})["axisymmetric_eligibility_reasons"] = [
            "the solve planner selected the full-3D BEMPP formulation"
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


__all__ = ["BemppEngine", "BemppUnavailable", "bempp_status", "solve_bempp_from_msh_text"]
