"""Guarded CPU fallback adapter for ``hornlab-bempp-bem``.

The import/load behavior, symmetry, source-motion feature detection, staged
frequency solve, and result mapping port v1
``server/solver/bempp_solver.py:21-67,153-188,229-356``.  Absence is a normal
capability state and never gets faked.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import importlib
import importlib.metadata
import logging
import os
import platform
import tempfile
import time
from pathlib import Path
from typing import Any

from server.jobs.models import SolveRequest
from server.mesh.builder import build_solver_mesh

from .base import ArtifactCallback, CancelCallback, EngineRunResult, StageCallback
from .context import SolverContext
from .formulation import DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT
from .infinite_baffle import reject_bempp_infinite_baffle
from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
)


try:
    from hornlab_bempp_bem import BIEFormulation, ObservationConfig, SolveConfig, solve as bempp_solve
except (ImportError, OSError):
    BIEFormulation = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    SolveConfig = None  # type: ignore[assignment]
    bempp_solve = None  # type: ignore[assignment]


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


def _resolved_workers() -> int:
    """How many processes the frequency sweep may use.

    Zero means the engine's own auto mode, which is the default and is
    self-limiting: ``hornlab_bempp_bem`` splits only when each worker would get
    at least 40 frequencies, because a spawned worker re-imports bempp-cl and
    re-JITs its kernels before it can solve anything. Short sweeps therefore
    stay in one warm process, where they are dramatically faster.

    The caveat that comes with splitting is real and is why this is reported in
    the solve metadata rather than being silent: Stop cannot cancel frequencies
    already running in sibling worker processes. ``WG2_SOLVE_WORKERS=1`` forces
    the single-process behaviour back.
    """

    raw = os.environ.get("WG2_SOLVE_WORKERS", "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Ignoring WG2_SOLVE_WORKERS=%r: expected a non-negative integer", raw
        )
        return 0


def _load_api() -> bool:
    global BIEFormulation, ObservationConfig, SolveConfig, bempp_solve
    if ObservationConfig is not None and SolveConfig is not None and bempp_solve is not None:
        return True
    try:
        package = importlib.import_module("hornlab_bempp_bem")
        BIEFormulation = getattr(package, "BIEFormulation")
        ObservationConfig = getattr(package, "ObservationConfig")
        SolveConfig = getattr(package, "SolveConfig")
        bempp_solve = getattr(package, "solve")
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

_OPENCL_GUIDANCE = (
    "Install an OpenCL runtime and start again. A CPU runtime is enough: on "
    "Windows the Intel CPU Runtime for OpenCL registers an ICD under "
    "HKLM\\SOFTWARE\\Khronos\\OpenCL\\Vendors; on Linux install pocl or your "
    "vendor's ICD; on macOS the system OpenCL framework already provides one."
)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"


def _opencl_status() -> tuple[bool, str]:
    """Is there a device to assemble on, not merely a pyopencl import?

    pyopencl imports cleanly with no ICD installed and only fails when something
    asks for a platform, which would be the solve. Enumerating here keeps a
    missing runtime a capability answer instead of a mid-solve crash.
    """

    try:
        import pyopencl
    except BaseException as exc:  # noqa: BLE001 - a broken build raises more than ImportError
        return False, f"pyopencl cannot load ({_describe(exc)}). {_OPENCL_GUIDANCE}"

    try:
        platforms = pyopencl.get_platforms()
    except Exception as exc:  # noqa: BLE001 - pyopencl raises its own LogicError
        return False, f"no OpenCL platform is usable ({_describe(exc)}). {_OPENCL_GUIDANCE}"
    for platform_entry in platforms:
        try:
            devices = platform_entry.get_devices()
        except Exception:  # noqa: BLE001 - one broken ICD must not hide a working one
            continue
        if devices:
            return True, (
                f"bempp-cl assembles on OpenCL device {devices[0].name.strip()} "
                f"({platform_entry.name.strip()})"
            )
    return False, f"an OpenCL runtime is present but exposes no device. {_OPENCL_GUIDANCE}"


def numba_fallback_warning(opencl_reason: str) -> str:
    """Say which backend is really running and exactly what to fix."""

    return (
        "Falling back to the numba assembly backend because OpenCL is unusable: "
        f"{opencl_reason} Until that is fixed, solves assemble on numba, which is "
        "slower, and the first solve after each start spends roughly a minute "
        "compiling kernels during which Stop cannot take effect."
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
    return {
        "available": usable,
        "reason": reason,
        "version": _version(),
        "assembly_backend": backend,
        "warning": warning,
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
    root = context.design.root
    enclosure = (
        float(root.enclosure.depth.value)
        if root.enclosure is not None and root.enclosure.depth is not None and root.enclosure.depth.value is not None
        else 0.0
    )
    wall = (
        float(root.mesh.wall_thickness.value)
        if root.mesh.wall_thickness is not None and root.mesh.wall_thickness.value is not None
        else 0.0
    )
    return enclosure > 0.0 or wall > 0.0


def solve_bempp_from_msh_text(
    msh_text: str,
    context: SolverContext,
    *,
    mesh_metadata: dict[str, Any] | None = None,
    progress_callback: Any = None,
    stage_callback: StageCallback | None = None,
    cancellation_callback: CancelCallback | None = None,
) -> dict[str, Any]:
    """Solve one authoritative Gmsh artifact on the guarded CPU backend."""

    context.validate()
    del mesh_metadata
    if context.solver_mode == "circsym":
        raise ValueError("BEMPP cannot run solver_mode='circsym'; use full_3d or the Metal CircSym engine")
    reject_bempp_infinite_baffle(context)
    if not _load_api() or SolveConfig is None or bempp_solve is None:
        raise BemppUnavailable("hornlab-bempp-bem is not installed.")
    status = bempp_status()
    if not status["available"]:
        raise BemppUnavailable(status["reason"])
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

    formulation = DEFAULT_BEM_FORMULATION
    if BIEFormulation is not None:
        formulation = getattr(BIEFormulation, "COMPLEX_K", formulation)
    try:
        config = SolveConfig(
            freq_min_hz=context.frequency_range[0],
            freq_max_hz=context.frequency_range[1],
            freq_count=context.num_frequencies,
            freq_spacing=context.frequency_spacing,
            formulation=formulation,
            complex_k_shift=DEFAULT_COMPLEX_K_SHIFT,
            observation=observation_config(
                context,
                ObservationConfig,
                BemppUnavailable,
                "hornlab-bempp-bem",
                msh_text=msh_text,
            ),
            progress_callback=progress,
            mesh_scale=1.0,
            native_symmetry_plane=native_symmetry_plane(context),
            assembly_backend=backend,
            opencl_device="cpu",
            precision="single",
        )
    except TypeError as exc:
        message = str(exc)
        if "formulation" in message:
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support the required BEM formulation option.") from exc
        if "complex_k_shift" in message:
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support the required complex-k shift option.") from exc
        raise
    if context.source_motion != "normal":
        if not hasattr(config, "source_motion"):
            raise BemppUnavailable("Installed hornlab-bempp-bem does not support axial source motion.")
        config.source_motion = context.source_motion
    if hasattr(config, "require_closed_mesh"):
        config.require_closed_mesh = (
            context.mesh_validation_mode == "strict" and _closed_mode(context)
        )
    workers = _resolved_workers()
    if hasattr(config, "workers"):
        config.workers = workers
        if workers != 1 and context.num_frequencies >= 80:
            # 80 is the engine's own threshold for splitting at all (two
            # workers, 40 frequencies each). Saying so before the solve starts
            # means nobody discovers the cancellation caveat by pressing Stop.
            message = (
                f"This sweep of {context.num_frequencies} frequencies may run across "
                "several worker processes. Stop cannot cancel a frequency already "
                "running in one; set WG2_SOLVE_WORKERS=1 to keep the solve in a "
                "single cancellable process."
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
        result = bempp_solve(str(path), config)
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
        "bempp": {
            "native_symmetry_plane": getattr(config, "native_symmetry_plane", None),
            "formulation": json_safe_native_value(getattr(config, "formulation", formulation)),
            "complex_k_shift": float(getattr(config, "complex_k_shift", DEFAULT_COMPLEX_K_SHIFT)),
            "assembly_backend": backend,
            "opencl_device": getattr(config, "opencl_device", "cpu"),
            "precision": getattr(config, "precision", "single"),
            "workers": getattr(config, "workers", 1),
            "solver_log": json_safe_native_value(response_solver_log(getattr(result, "solver_log", []))),
        },
    }
    return build_solver_response(
        result=result,
        config=config,
        context=context,
        start_time=started,
        metadata=metadata,
    )


class BemppEngine:
    name = "bempp"

    async def run(
        self,
        request: SolveRequest,
        *,
        cancel_cb: CancelCallback,
        stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None,
    ) -> EngineRunResult:
        if (request.design.root.simulation.solver_mode or "").strip().lower() == "circsym":
            raise ValueError("BEMPP cannot run solver_mode='circsym'; select Metal or use full_3d")
        context = SolverContext.from_request(request, solver_mode="full_3d")
        reject_bempp_infinite_baffle(context)
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
            solve_bempp_from_msh_text,
            mesh["msh_text"],
            context,
            mesh_metadata=mesh["metadata"],
            stage_callback=stage_cb,
            cancellation_callback=cancel_cb,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        results.setdefault("metadata", {})["solve_path"] = "full-3d"
        results.setdefault("metadata", {})["axisymmetric_eligibility_reasons"] = [
            "axisymmetric-meridian is a Metal-only fast path"
        ]
        return EngineRunResult(results=results, msh_text=mesh["msh_text"], mesh_stats=mesh["stats"])


__all__ = ["BemppEngine", "BemppUnavailable", "bempp_status", "solve_bempp_from_msh_text"]
