"""Reproducible whole-workload benchmark for the cpubem reference.

Run from the repository root with::

    npm run bench:cpubem -- --backends cpubem bempp-opencl --output result.json

The benchmark keeps mesh loading, boundary-data construction, dense assembly,
factorization, and field evaluation separate for cpubem. The public Bempp
backend exposes mesh, per-frequency solve, directivity, and total timings, so
those are recorded at the finest granularity its API provides. When both
implementations run, complex pressure and source impedance are compared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Sequence

import numpy as np
import scipy

from .assembly import (
    AIR_DENSITY,
    SPEED_OF_SOUND,
    assemble_system,
    neumann_from_tags,
    wavenumber,
)
from .geometry import SurfaceMesh, load_msh
from .solve import evaluate_field, solve_dense
from ..bempp_compat import apply_bempp_opencl_workarounds

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MESH = (
    REPO_ROOT / "scripts" / "diagnostics" / "out" / "test_reference_horn.msh"
)
SCHEMA_VERSION = 1


def configure_benchmark_cache() -> Path:
    """Point compiler/runtime caches at a workspace-writable directory."""
    cache_root = REPO_ROOT / "server" / ".benchmark-cache"
    local_app_data = cache_root / "localappdata"
    numba_cache = cache_root / "numba"
    local_app_data.mkdir(parents=True, exist_ok=True)
    numba_cache.mkdir(parents=True, exist_ok=True)
    # platformdirs uses LOCALAPPDATA on Windows for both PyOpenCL compiler
    # artifacts and pytools' generated-invoker database.
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    os.environ["NUMBA_CACHE_DIR"] = str(numba_cache)

    try:
        import platformdirs
    except ImportError:
        # platformdirs is optional for a cpubem-only install. The environment
        # fallbacks above still protect numba and consumers that honor
        # LOCALAPPDATA; only the Windows Known Folder monkeypatch is skipped.
        return cache_root

    def workspace_user_cache_dir(
        appname: str | None = None, *_args: Any, **_kwargs: Any
    ) -> str:
        safe_name = str(appname or "cache").replace("/", "_").replace("\\", "_")
        destination = cache_root / safe_name
        destination.mkdir(parents=True, exist_ok=True)
        return str(destination)

    # On Windows platformdirs resolves a shell Known Folder and ignores a
    # process-local LOCALAPPDATA override. PyOpenCL and pytools call this
    # function lazily, so a benchmark-local resolver keeps both writable
    # without altering application behaviour outside this process.
    platformdirs.user_cache_dir = workspace_user_cache_dir
    return cache_root


def build_observation_points(
    count: int, distance_m: float
) -> tuple[list[str], np.ndarray]:
    """Return horizontal/vertical polar arcs shaped ``(2, count, 3)``."""
    if count < 1:
        raise ValueError("observation count must be at least 1")
    if not np.isfinite(distance_m) or distance_m <= 0.0:
        raise ValueError("observation distance must be finite and positive")

    angles = np.linspace(0.0, np.pi, count, dtype=np.float64)
    radial = distance_m * np.sin(angles)
    axial = distance_m * np.cos(angles)
    horizontal = np.column_stack((radial, np.zeros(count), axial))
    vertical = np.column_stack((np.zeros(count), radial, axial))
    return ["horizontal", "vertical"], np.stack((horizontal, vertical))


def _source_impedance(
    mesh: SurfaceMesh, surface_pressure: np.ndarray, source_tag: int = 2
) -> complex:
    mask = mesh.tag_mask(source_tag)
    if not np.any(mask):
        raise ValueError(f"mesh has no source elements with physical tag {source_tag}")
    local_pressure = surface_pressure[mesh.p1_local2global[mask]].mean(axis=1)
    source_areas = mesh.areas[mask]
    return complex(np.sum(local_pressure * source_areas) / np.sum(source_areas))


def _complex_pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def _array_digest(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _mesh_metadata(path: Path, mesh: SurfaceMesh) -> dict[str, Any]:
    tags, counts = np.unique(mesh.physical_tags, return_counts=True)
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "vertices": int(mesh.p1_dof_count),
        "triangles": int(mesh.dp0_dof_count),
        "physical_tag_counts": {
            str(tag): int(count) for tag, count in zip(tags, counts)
        },
    }


def _time_call(function: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    value = function()
    return value, time.perf_counter() - start


def _run_cpubem(
    mesh_path: Path,
    frequencies_hz: Sequence[float],
    observation_points: np.ndarray,
    *,
    threads: int,
    repeat_index: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    total_start = time.perf_counter()
    mesh, mesh_seconds = _time_call(lambda: load_msh(mesh_path))

    pressure_rows: list[np.ndarray] = []
    impedance: list[complex] = []
    frequency_runs: list[dict[str, Any]] = []
    flat_points = observation_points.reshape(-1, 3)

    for frequency_hz in frequencies_hz:
        q, source_seconds = _time_call(
            lambda frequency_hz=frequency_hz: neumann_from_tags(
                mesh, frequency_hz
            )
        )
        k = wavenumber(frequency_hz)
        (operator, rhs), assembly_seconds = _time_call(
            lambda k=k, q=q: assemble_system(mesh, k, q, threads=threads)
        )
        surface_pressure, factorization_seconds = _time_call(
            lambda operator=operator, rhs=rhs: solve_dense(operator, rhs)
        )
        field, field_seconds = _time_call(
            lambda surface_pressure=surface_pressure, q=q, k=k: evaluate_field(
                mesh,
                surface_pressure,
                q,
                k,
                flat_points,
                threads=threads,
            )
        )
        field = field.reshape(observation_points.shape[:2])
        pressure_rows.append(field)
        impedance.append(_source_impedance(mesh, surface_pressure))
        frequency_runs.append(
            {
                "frequency_hz": float(frequency_hz),
                "source_seconds": source_seconds,
                "assembly_seconds": assembly_seconds,
                "factorization_seconds": factorization_seconds,
                "field_seconds": field_seconds,
                "frequency_total_seconds": (
                    source_seconds
                    + assembly_seconds
                    + factorization_seconds
                    + field_seconds
                ),
            }
        )

    pressure = np.stack(pressure_rows)
    impedance_array = np.asarray(impedance, dtype=np.complex128)
    total_seconds = time.perf_counter() - total_start
    return (
        {
            "backend": "cpubem",
            "repeat": repeat_index,
            "mesh_seconds": mesh_seconds,
            "frequency_runs": frequency_runs,
            "total_seconds": total_seconds,
            "pressure_sha256": _array_digest(pressure),
            "on_axis_pressure": [
                _complex_pair(value) for value in pressure[:, 0, 0]
            ],
            "source_impedance": [
                _complex_pair(value) for value in impedance_array
            ],
        },
        {"pressure": pressure, "impedance": impedance_array},
    )


def _run_bempp(
    mesh_path: Path,
    frequencies_hz: Sequence[float],
    observation_planes: Sequence[str],
    observation_points: np.ndarray,
    *,
    assembly_backend: str,
    precision: str,
    restrict_neumann_space: bool,
    linear_solver: str,
    gmres_tol: float,
    quadrature_order: int,
    singular_quadrature_order: int,
    symmetry_plane: str | None,
    formulation: str,
    gmres_restart: int | None,
    repeat_index: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    configure_benchmark_cache()
    workaround_status = apply_bempp_opencl_workarounds()
    try:
        import hornlab_bempp_bem as hornlab_bempp
        from hornlab_bempp_bem import (
            BIEFormulation,
            LinearSolver,
            ObservationConfig,
            SolveConfig,
            load_mesh,
            solve_frequencies,
        )
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-bempp-bem is unavailable in this interpreter"
        ) from exc

    total_start = time.perf_counter()
    loaded, mesh_seconds = _time_call(
        lambda: load_mesh(
            mesh_path,
            scale=1.0,
            native_symmetry_plane=symmetry_plane,
        )
    )
    custom_points = {
        plane: observation_points[index]
        for index, plane in enumerate(observation_planes)
    }
    config = SolveConfig(
        formulation=BIEFormulation(formulation),
        solver=LinearSolver(linear_solver),
        gmres_tol=gmres_tol,
        gmres_restart=gmres_restart,
        slp_dlp_quadrature=quadrature_order,
        workers=1,
        precision=precision,
        assembly_backend=assembly_backend,
        opencl_device="cpu",
        native_symmetry_plane=symmetry_plane,
        mesh_scale=1.0,
        observation=ObservationConfig(
            planes=list(observation_planes),
            custom_points=custom_points,
        ),
    )
    if hasattr(config, "slp_dlp_singular_quadrature"):
        config.slp_dlp_singular_quadrature = singular_quadrature_order
    if hasattr(config, "restrict_neumann_space"):
        config.restrict_neumann_space = restrict_neumann_space
    result, public_solve_seconds = _time_call(
        lambda: solve_frequencies(loaded, frequencies_hz, config)
    )
    total_seconds = time.perf_counter() - total_start
    pressure = np.asarray(result.pressure_complex, dtype=np.complex128)
    impedance = np.asarray(result.impedance, dtype=np.complex128)
    per_frequency = [
        {
            "frequency_hz": float(entry.get("frequency_hz", frequency_hz)),
            "solve_seconds": float(entry.get("timing_s", 0.0)),
            "iterations": entry.get("iterations"),
            "converged": bool(entry.get("converged", True)),
            "phase_timings": {
                key: float(value)
                for key, value in (entry.get("phase_timings") or {}).items()
            },
        }
        for frequency_hz, entry in zip(frequencies_hz, result.solver_log)
    ]
    return (
        {
            "backend": f"bempp-{assembly_backend}",
            "repeat": repeat_index,
            "module_path": str(Path(hornlab_bempp.__file__).resolve()),
            "opencl_workarounds": workaround_status,
            "mesh_seconds": mesh_seconds,
            "public_solve_seconds": public_solve_seconds,
            "frequency_runs": per_frequency,
            "native_timings": {
                key: float(value) for key, value in result.timings.items()
            },
            "total_seconds": total_seconds,
            "pressure_sha256": _array_digest(pressure),
            "on_axis_pressure": [
                _complex_pair(value) for value in pressure[:, 0, 0]
            ],
            "source_impedance": [_complex_pair(value) for value in impedance],
        },
        {"pressure": pressure, "impedance": impedance},
    )


def compare_outputs(
    reference: dict[str, np.ndarray], candidate: dict[str, np.ndarray]
) -> dict[str, float]:
    """Compare absolute complex outputs without directivity normalization."""
    result: dict[str, float] = {}
    for name in ("pressure", "impedance"):
        left = np.asarray(reference[name])
        right = np.asarray(candidate[name])
        if left.shape != right.shape:
            raise ValueError(
                f"{name} shape mismatch: reference {left.shape}, candidate {right.shape}"
            )
        absolute = np.abs(left - right)
        scale = max(float(np.abs(left).max()), np.finfo(np.float64).tiny)
        result[f"{name}_max_abs_error"] = float(absolute.max())
        result[f"{name}_max_rel_error"] = float(absolute.max() / scale)
        phase_mask = (np.abs(left) > scale * 1e-12) & (
            np.abs(right) > scale * 1e-12
        )
        phase_error = (
            np.abs(np.angle(right[phase_mask] / left[phase_mask]))
            if np.any(phase_mask)
            else np.array([0.0])
        )
        result[f"{name}_max_phase_error_rad"] = float(phase_error.max())
        magnitude_mask = (np.abs(left) > scale * 1e-12) & (
            np.abs(right) > scale * 1e-12
        )
        magnitude_db_error = (
            np.abs(
                20.0
                * np.log10(
                    np.abs(right[magnitude_mask]) / np.abs(left[magnitude_mask])
                )
            )
            if np.any(magnitude_mask)
            else np.array([0.0])
        )
        result[f"{name}_max_magnitude_db_error"] = float(
            magnitude_db_error.max()
        )
        if name == "pressure" and left.ndim >= 2:
            left_db = 20.0 * np.log10(
                np.maximum(np.abs(left), np.finfo(np.float64).tiny)
            )
            right_db = 20.0 * np.log10(
                np.maximum(np.abs(right), np.finfo(np.float64).tiny)
            )
            left_normalized = left_db - left_db[..., :1]
            right_normalized = right_db - right_db[..., :1]
            result["pressure_max_normalized_db_error"] = float(
                np.abs(left_normalized - right_normalized).max()
            )
    return result


def summarize_runs(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return median totals and per-frequency throughput by backend."""
    summaries: dict[str, Any] = {}
    for backend in sorted({str(run["backend"]) for run in runs}):
        matching = [run for run in runs if run["backend"] == backend]
        totals = [float(run["total_seconds"]) for run in matching]
        frequency_counts = [len(run["frequency_runs"]) for run in matching]
        per_frequency = [
            total / count for total, count in zip(totals, frequency_counts)
        ]
        summaries[backend] = {
            "repeats": len(matching),
            "median_total_seconds": median(totals),
            "median_seconds_per_frequency": median(per_frequency),
        }
    return summaries


def build_comparisons(
    latest_outputs: dict[str, dict[str, np.ndarray]], bempp_symmetry: str
) -> dict[str, Any]:
    """Compare like-for-like domains, recording why symmetry runs are skipped."""
    comparisons: dict[str, Any] = {}
    if "cpubem" not in latest_outputs:
        return comparisons
    for backend, outputs in latest_outputs.items():
        if backend == "cpubem":
            continue
        key = f"cpubem_vs_{backend}"
        if bempp_symmetry != "none":
            comparisons[key] = {
                "skipped": True,
                "reason": "bempp symmetry mirrors the reduced mesh; domains differ",
            }
        else:
            comparisons[key] = compare_outputs(latest_outputs["cpubem"], outputs)
    return comparisons


def _host_metadata() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    mesh_path = Path(args.mesh).resolve()
    if not mesh_path.is_file():
        raise FileNotFoundError(
            f"reference mesh not found at {mesh_path}; build it with "
            "npm run diag:mesher:reference-horn"
        )
    frequencies = [float(value) for value in args.frequencies]
    if not frequencies or any(
        not np.isfinite(value) or value <= 0.0 for value in frequencies
    ):
        raise ValueError("frequencies must be finite and positive")

    planes, observation_points = build_observation_points(
        args.observation_count, args.observation_distance
    )
    metadata_mesh = load_msh(mesh_path)
    runs: list[dict[str, Any]] = []
    latest_outputs: dict[str, dict[str, np.ndarray]] = {}
    errors: list[dict[str, Any]] = []

    for repeat_index in range(1, args.repeats + 1):
        for backend in args.backends:
            try:
                if backend == "cpubem":
                    record, outputs = _run_cpubem(
                        mesh_path,
                        frequencies,
                        observation_points,
                        threads=args.threads,
                        repeat_index=repeat_index,
                    )
                else:
                    record, outputs = _run_bempp(
                        mesh_path,
                        frequencies,
                        planes,
                        observation_points,
                        assembly_backend=backend.removeprefix("bempp-"),
                        precision=args.precision,
                        restrict_neumann_space=not args.bempp_full_neumann_space,
                        linear_solver=args.bempp_solver,
                        gmres_tol=args.bempp_gmres_tol,
                        quadrature_order=args.bempp_quadrature,
                        singular_quadrature_order=args.bempp_singular_quadrature,
                        symmetry_plane=(
                            None
                            if args.bempp_symmetry == "none"
                            else args.bempp_symmetry
                        ),
                        formulation=args.bempp_formulation,
                        gmres_restart=(
                            None
                            if args.bempp_gmres_restart == 0
                            else args.bempp_gmres_restart
                        ),
                        repeat_index=repeat_index,
                    )
            except Exception as exc:  # noqa: BLE001 - benchmark must preserve partial data
                errors.append(
                    {
                        "backend": backend,
                        "repeat": repeat_index,
                        "error_type": type(exc).__name__,
                        "message": str(exc).splitlines()[0],
                    }
                )
                continue
            runs.append(record)
            latest_outputs[backend] = outputs

    comparisons = build_comparisons(latest_outputs, args.bempp_symmetry)

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": _host_metadata(),
        "mesh": _mesh_metadata(mesh_path, metadata_mesh),
        "configuration": {
            "backends": list(args.backends),
            "frequencies_hz": frequencies,
            "repeats": args.repeats,
            "threads": args.threads,
            "precision": args.precision,
            "bempp_restrict_neumann_space": not args.bempp_full_neumann_space,
            "bempp_solver": args.bempp_solver,
            "bempp_gmres_tol": args.bempp_gmres_tol,
            "bempp_quadrature": args.bempp_quadrature,
            "bempp_singular_quadrature": args.bempp_singular_quadrature,
            "bempp_symmetry": args.bempp_symmetry,
            "bempp_formulation": args.bempp_formulation,
            "bempp_gmres_restart": args.bempp_gmres_restart,
            "air_density": AIR_DENSITY,
            "speed_of_sound": SPEED_OF_SOUND,
            "observation_planes": planes,
            "observation_count_per_plane": args.observation_count,
            "observation_distance_m": args.observation_distance,
            "cache_directory": str(configure_benchmark_cache()),
        },
        "runs": runs,
        "summary": summarize_runs(runs),
        "comparisons": comparisons,
        "errors": errors,
        "success": not errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mesh",
        type=Path,
        default=DEFAULT_MESH,
        help="Gmsh reference mesh (default: generated reference horn)",
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        choices=("cpubem", "bempp-opencl", "bempp-numba"),
        default=("cpubem",),
        help="backends to benchmark in the same process",
    )
    parser.add_argument(
        "--frequencies",
        nargs="+",
        type=float,
        default=(1000.0,),
        help="frequencies in Hz",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--precision", choices=("single", "double"), default="double"
    )
    parser.add_argument(
        "--bempp-full-neumann-space",
        action="store_true",
        help="disable source-support reduction for Bempp comparison runs",
    )
    parser.add_argument(
        "--bempp-solver",
        choices=("lu", "gmres", "auto"),
        default="lu",
        help="linear solver used by Bempp runs",
    )
    parser.add_argument("--bempp-gmres-tol", type=float, default=1e-5)
    parser.add_argument(
        "--bempp-gmres-restart",
        type=int,
        default=0,
        help="GMRES restart length (0 uses SciPy/Bempp default)",
    )
    parser.add_argument(
        "--bempp-quadrature",
        type=int,
        default=4,
        help="regular quadrature order for SLP/DLP assembly",
    )
    parser.add_argument(
        "--bempp-singular-quadrature",
        type=int,
        default=4,
        help="singular quadrature order for SLP/DLP assembly",
    )
    parser.add_argument(
        "--bempp-symmetry",
        choices=("none", "yz", "xz", "yz+xz"),
        default="none",
        help="mirror symmetry represented by a reduced Bempp mesh",
    )
    parser.add_argument(
        "--bempp-formulation",
        choices=("standard", "complex_k"),
        default="standard",
    )
    parser.add_argument("--observation-count", type=int, default=37)
    parser.add_argument("--observation-distance", type=float, default=2.0)
    parser.add_argument("--output", type=Path, help="write the JSON result here")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    if args.bempp_quadrature < 1:
        parser.error("--bempp-quadrature must be at least 1")
    if args.bempp_singular_quadrature < 1:
        parser.error("--bempp-singular-quadrature must be at least 1")
    if args.bempp_gmres_restart < 0:
        parser.error("--bempp-gmres-restart must be non-negative")

    try:
        result = run_benchmark(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"benchmark failed: {exc}\n")

    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output_path = (
            args.output.resolve()
            if args.output.is_absolute()
            else (REPO_ROOT / args.output).resolve()
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded + "\n", encoding="utf-8")
        print(f"Wrote benchmark result to {output_path}")
    else:
        print(encoded)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
