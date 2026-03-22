"""
Bounded Tritonia-M benchmark/repro path.

Provides a repeatable diagnostic flow that:
1. Builds Tritonia-M mesh via OCC
2. Runs a bounded solve (1-frequency or small sweep)
3. Reports mesh-prep success, selected runtime/device, solver stage timings,
   and supported vs unsupported precision modes on the active host.

Usage (run from server/ directory):
    python scripts/benchmark_tritonia.py [options]

Options:
    --freq FLOAT        Single frequency to solve (Hz, default: 1000)
    --sweep             Run a small 3-frequency sweep instead of single frequency
    --device MODE       Device mode: auto|opencl_gpu|opencl_cpu (default: auto)
    --precision MODE    BEM precision: single|double|both (default: single)
                        'both' tests single then double and reports which work
    --json              Output results as JSON
    --no-solve          Skip solve step, only test mesh preparation
    --timeout SECONDS   Max time per solve attempt (default: 120)

Exit codes:
    0: All requested operations succeeded
    1: Mesh preparation failed
    2: All solve attempts failed (but mesh prep succeeded)
    3: Runtime unavailable (bempp/OpenCL not installed)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.solve_readiness import (
    READINESS_PROBE_ID,
    READINESS_SCHEMA_VERSION,
    write_bounded_solve_readiness_record,
)
from solver.deps import (
    BEMPP_AVAILABLE,
    BEMPP_RUNTIME_READY,
    GMSH_OCC_RUNTIME_READY,
    PYTHON_SUPPORTED,
    PYTHON_VERSION,
    BEMPP_VERSION,
    GMSH_VERSION,
)
from solver.device_interface import (
    selected_device_metadata,
    clear_device_selection_caches,
)


@dataclass
class MeshPrepResult:
    success: bool
    error: Optional[str] = None
    vertex_count: int = 0
    triangle_count: int = 0
    tag_counts: Dict[int, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


@dataclass
class PrecisionTestResult:
    precision: str
    attempted: bool = False
    success: bool = False
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    gmres_iterations: Optional[int] = None
    spl_value: Optional[float] = None


@dataclass
class BenchmarkResult:
    runtime_available: bool
    mesh_prep: MeshPrepResult
    device_metadata: Dict[str, Any]
    precision_results: List[PrecisionTestResult]
    host_info: Dict[str, Any]
    unsupported_precision_modes: List[str]
    total_elapsed_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "runtime_available": self.runtime_available,
            "mesh_prep": {
                "success": self.mesh_prep.success,
                "error": self.mesh_prep.error,
                "vertex_count": self.mesh_prep.vertex_count,
                "triangle_count": self.mesh_prep.triangle_count,
                "tag_counts": self.mesh_prep.tag_counts,
                "elapsed_seconds": self.mesh_prep.elapsed_seconds,
            },
            "device_metadata": self.device_metadata,
            "precision_results": [
                {
                    "precision": pr.precision,
                    "attempted": pr.attempted,
                    "success": pr.success,
                    "error": pr.error,
                    "elapsed_seconds": pr.elapsed_seconds,
                    "gmres_iterations": pr.gmres_iterations,
                    "spl_value": pr.spl_value,
                }
                for pr in self.precision_results
            ],
            "host_info": self.host_info,
            "unsupported_precision_modes": self.unsupported_precision_modes,
            "total_elapsed_seconds": self.total_elapsed_seconds,
        }
        return result


def _first_precision_failure(results: List[PrecisionTestResult]) -> Optional[str]:
    for result in results:
        if result.attempted and not result.success and result.error:
            return result.error
    for result in results:
        if result.error:
            return result.error
    return None


def _persist_bounded_solve_validation(result: BenchmarkResult, args: argparse.Namespace) -> None:
    import platform

    attempted = any(item.attempted for item in result.precision_results)
    success = any(item.success for item in result.precision_results)
    failure = _first_precision_failure(result.precision_results)
    if not failure and not result.mesh_prep.success:
        failure = result.mesh_prep.error or "mesh preparation failed"
    if not failure and not result.runtime_available:
        failure = "BEM runtime unavailable"

    record = {
        "schemaVersion": READINESS_SCHEMA_VERSION,
        "probe": READINESS_PROBE_ID,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python_executable": sys.executable,
        },
        "requested_mode": str(args.device or "auto"),
        "selected_mode": result.device_metadata.get("selected_mode"),
        "device_name": result.device_metadata.get("device_name"),
        "frequency_hz": float(args.freq),
        "sweep": bool(args.sweep),
        "precision": str(args.precision or "single"),
        "attempted": attempted,
        "success": success,
        "runtime_available": bool(result.runtime_available),
        "mesh_prep_success": bool(result.mesh_prep.success),
        "failure": failure,
    }

    try:
        write_bounded_solve_readiness_record(record)
    except Exception as exc:  # pragma: no cover - best effort persistence
        print(
            f"[benchmark_tritonia] warning: failed to persist bounded solve readiness record: {exc}",
            file=sys.stderr,
        )


TRITONIA_PARAMS = {
    "formula_type": "OSSE",
    "L": 135.0,
    "a": 45.0,
    "r0": 18.0,
    "a0": 10.0,
    "k": 2.1,
    "q": 0.992,
    "n": 3.7,
    "s": 0.7,
    "quadrants": 1234,
    "enc_depth": 100.0,
    "enc_edge": 20.0,
    "n_angular": 40,
    "n_length": 12,
    "throat_res": 8.0,
    "mouth_res": 15.0,
    "enc_front_resolution": "30,30,30,30",
    "enc_back_resolution": "40,40,40,40",
}


def build_tritonia_mesh() -> MeshPrepResult:
    if not GMSH_OCC_RUNTIME_READY:
        return MeshPrepResult(
            success=False,
            error=f"OCC mesh builder unavailable (gmsh: available={GMSH_VERSION is not None}, supported={GMSH_OCC_RUNTIME_READY})",
        )

    from solver.waveguide_builder import build_waveguide_mesh

    start = time.time()
    try:
        result = build_waveguide_mesh(TRITONIA_PARAMS, include_canonical=True)
        elapsed = time.time() - start

        canonical = result.get("canonical_mesh", {})
        vertices = canonical.get("vertices", [])
        indices = canonical.get("indices", [])
        surface_tags = canonical.get("surfaceTags", [])

        tag_counts: Dict[int, int] = {}
        for tag in surface_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return MeshPrepResult(
            success=True,
            vertex_count=len(vertices) // 3,
            triangle_count=len(indices) // 3,
            tag_counts=tag_counts,
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        return MeshPrepResult(
            success=False,
            error=f"Mesh build failed: {type(e).__name__}: {e}",
            elapsed_seconds=time.time() - start,
        )


def prepare_solver_mesh(mesh_prep_result: MeshPrepResult) -> Optional[Dict[str, Any]]:
    if not mesh_prep_result.success:
        return None

    from solver.waveguide_builder import build_waveguide_mesh

    try:
        result = build_waveguide_mesh(TRITONIA_PARAMS, include_canonical=True)
        canonical = result.get("canonical_mesh", {})
    except Exception:
        return None

    from solver.mesh import prepare_mesh
    try:
        return prepare_mesh(
            vertices=canonical["vertices"],
            indices=canonical["indices"],
            surface_tags=canonical["surfaceTags"],
            mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001},
        )
    except Exception:
        return None


def test_single_solve(
    mesh: Dict[str, Any],
    frequency: float,
    device_mode: str,
    precision: str,
    timeout_seconds: float,
) -> PrecisionTestResult:
    from solver.solve_optimized import solve_optimized
    clear_device_selection_caches()
    start = time.time()
    try:
        import threading
        result_container: Dict[str, Any] = {"result": None, "error": None}
        def run_solve() -> None:
            try:
                result_container["result"] = solve_optimized(
                    mesh=mesh,
                    frequency_range=[frequency, frequency],
                    num_frequencies=1,
                    sim_type="2",
                    verbose=False,
                    mesh_validation_mode="warn",
                    device_mode=device_mode,
                    bem_precision=precision,
                    enable_warmup=False,
                )
            except Exception as e:
                result_container["error"] = e
        thread = threading.Thread(target=run_solve)
        thread.start()
        thread.join(timeout=timeout_seconds)
        if thread.is_alive():
            elapsed = time.time() - start
            return PrecisionTestResult(
                precision=precision,
                attempted=True,
                success=False,
                error=f"Solve exceeded {timeout_seconds} seconds",
                elapsed_seconds=elapsed,
            )
        if result_container["error"] is not None:
            raise result_container["error"]
        results = result_container["result"]
        if results is None:
            return PrecisionTestResult(
                precision=precision,
                attempted=True,
                success=False,
                error="Solve returned no result",
                elapsed_seconds=time.time() - start,
            )
        elapsed = time.time() - start
        perf = results.get("metadata", {}).get("performance", {})
        failures = results.get("metadata", {}).get("failure_count", 0)
        spl_values = results.get("spl_on_axis", {}).get("spl", [])
        if failures > 0:
            failure_details = results.get("metadata", {}).get("failures", [])
            error_msg = failure_details[0].get("detail", "Unknown failure") if failure_details else "Unknown failure"
            return PrecisionTestResult(
                precision=precision,
                attempted=True,
                success=False,
                error=error_msg,
                elapsed_seconds=elapsed,
            )
        return PrecisionTestResult(
            precision=precision,
            attempted=True,
            success=True,
            elapsed_seconds=elapsed,
            gmres_iterations=perf.get("gmres_iterations_per_frequency", [None])[0],
            spl_value=spl_values[0] if spl_values else None,
        )
    except RuntimeError as e:
        err_str = str(e).lower()
        if "opencl" in err_str or "kernel" in err_str or "clbuildprogram" in err_str:
            return PrecisionTestResult(
                precision=precision,
                attempted=True,
                success=False,
                error=f"OpenCL error: {e}",
                elapsed_seconds=time.time() - start,
            )
        return PrecisionTestResult(
            precision=precision,
            attempted=True,
            success=False,
            error=f"Runtime error: {e}",
            elapsed_seconds=time.time() - start,
        )
    except Exception as e:
        return PrecisionTestResult(
            precision=precision,
            attempted=True,
            success=False,
            error=f"Unexpected error: {type(e).__name__}: {e}",
            elapsed_seconds=time.time() - start,
        )
def get_host_info() -> Dict[str, Any]:
    import platform
    return {
        "python_version": PYTHON_VERSION,
        "python_supported": PYTHON_SUPPORTED,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "gmsh_version": GMSH_VERSION,
        "gmsh_ready": GMSH_OCC_RUNTIME_READY,
        "bempp_version": BEMPP_VERSION,
        "bempp_ready": BEMPP_RUNTIME_READY,
    }
def run_benchmark(args: argparse.Namespace) -> BenchmarkResult:
    total_start = time.time()
    host_info = get_host_info()
    runtime_available = BEMPP_RUNTIME_READY
    clear_device_selection_caches()
    device_meta = selected_device_metadata(args.device)
    mesh_prep = build_tritonia_mesh()
    precision_results: List[PrecisionTestResult] = []
    unsupported_modes: List[str] = []
    if not args.no_solve and mesh_prep.success and runtime_available:
        mesh = prepare_solver_mesh(mesh_prep)
        if mesh is None:
            mesh_prep = MeshPrepResult(
                success=False,
                error="Failed to prepare solver mesh from canonical payload",
            )
        else:
            frequencies = [args.freq]
            if args.sweep:
                frequencies = [args.freq * 0.8, args.freq, args.freq * 1.2]
            precisions_to_test = []
            if args.precision == "both":
                precisions_to_test = ["single", "double"]
            else:
                precisions_to_test = [args.precision]
            for precision in precisions_to_test:
                for freq in frequencies:
                    result = test_single_solve(
                        mesh=mesh,
                        frequency=freq,
                        device_mode=args.device,
                        precision=precision,
                        timeout_seconds=args.timeout,
                    )
                    precision_results.append(result)
                    if not result.success and result.attempted:
                        if precision not in unsupported_modes:
                            unsupported_modes.append(precision)
    elif not runtime_available:
        for precision in (["single", "double"] if args.precision == "both" else [args.precision]):
            precision_results.append(
                PrecisionTestResult(
                    precision=precision,
                    attempted=False,
                    success=False,
                    error="BEM runtime unavailable",
                )
            )
    total_elapsed = time.time() - total_start
    return BenchmarkResult(
        runtime_available=runtime_available,
        mesh_prep=mesh_prep,
        device_metadata=device_meta,
        precision_results=precision_results,
        host_info=host_info,
        unsupported_precision_modes=unsupported_modes,
        total_elapsed_seconds=total_elapsed,
    )
def print_human_readable(result: BenchmarkResult) -> None:
    print("=" * 60)
    print("TRITONIA-M BENCHMARK REPORT")
    print("=" * 60)
    print()
    print("HOST INFO")
    print("-" * 40)
    host = result.host_info
    print(f"  Python:          {host['python_version']} (supported: {host['python_supported']})")
    print(f"  Platform:        {host['platform']} ({host['machine']})")
    print(f"  Gmsh:            {host['gmsh_version'] or 'not installed'} (ready: {host['gmsh_ready']})")
    print(f"  Bempp:           {host['bempp_version'] or 'not installed'} (ready: {host['bempp_ready']})")
    print()
    print("DEVICE / RUNTIME")
    print("-" * 40)
    dev = result.device_metadata
    print(f"  Requested mode:  {dev.get('requested_mode', 'auto')}")
    print(f"  Selected mode:   {dev.get('selected_mode', 'none')}")
    print(f"  Interface:       {dev.get('interface', 'unavailable')}")
    print(f"  Device type:     {dev.get('device_type', 'none')}")
    print(f"  Device name:     {dev.get('device_name', 'none')}")
    if dev.get("fallback_reason"):
        print(f"  Fallback reason: {dev['fallback_reason']}")
    print()
    mode_avail = dev.get("mode_availability", {})
    if mode_avail:
        print("  Mode availability:")
        for mode, info in mode_avail.items():
            avail = info.get("available", False)
            reason = info.get("reason")
            status = "available" if avail else f"unavailable ({reason})" if reason else "unavailable"
            print(f"    {mode}: {status}")
    print()
    print("MESH PREPARATION")
    print("-" * 40)
    mesh = result.mesh_prep
    if mesh.success:
        print(f"  Status:          SUCCESS")
        print(f"  Vertices:        {mesh.vertex_count}")
        print(f"  Triangles:       {mesh.triangle_count}")
        print(f"  Tag counts:      {mesh.tag_counts}")
        print(f"  Elapsed:         {mesh.elapsed_seconds:.2f}s")
    else:
        print(f"  Status:          FAILED")
        print(f"  Error:           {mesh.error}")
    print()
    if result.precision_results:
        print("SOLVE RESULTS")
        print("-" * 40)
        for pr in result.precision_results:
            status = "SUCCESS" if pr.success else "FAILED" if pr.attempted else "SKIPPED"
            print(f"  Precision: {pr.precision}")
            print(f"    Status:      {status}")
            if pr.error:
                print(f"    Error:       {pr.error}")
            if pr.success:
                print(f"    Elapsed:     {pr.elapsed_seconds:.2f}s")
                print(f"    GMRES iters: {pr.gmres_iterations}")
                print(f"    SPL:         {pr.spl_value:.2f} dB" if pr.spl_value else "    SPL:         N/A")
            print()
    if result.unsupported_precision_modes:
        print("UNSUPPORTED PRECISION MODES")
        print("-" * 40)
        for mode in result.unsupported_precision_modes:
            print(f"  - {mode}")
        print()
    print("SUMMARY")
    print("-" * 40)
    print(f"  Total elapsed:   {result.total_elapsed_seconds:.2f}s")
    print(f"  Runtime ready:   {result.runtime_available}")
    print(f"  Mesh prep:       {'OK' if result.mesh_prep.success else 'FAILED'}")
    solve_ok = any(pr.success for pr in result.precision_results)
    solve_attempted = any(pr.attempted for pr in result.precision_results)
    if solve_attempted:
        print(f"  Solve:           {'OK' if solve_ok else 'FAILED'}")
    print("=" * 60)
def main():
    parser = argparse.ArgumentParser(
        description="Bounded Tritonia-M benchmark/repro path",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--freq",
        type=float,
        default=1000.0,
        help="Single frequency to solve (Hz)",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run a small 3-frequency sweep instead of single frequency",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "opencl_gpu", "opencl_cpu"],
        help="Device mode",
    )
    parser.add_argument(
        "--precision",
        default="single",
        choices=["single", "double", "both"],
        help="BEM precision mode (both tests single then double)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--no-solve",
        action="store_true",
        help="Skip solve step, only test mesh preparation",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Max time per solve attempt (seconds)",
    )
    args = parser.parse_args()
    result = run_benchmark(args)
    if not args.no_solve:
        _persist_bounded_solve_validation(result, args)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_human_readable(result)
    if not result.mesh_prep.success:
        sys.exit(1)
    if not result.runtime_available:
        sys.exit(3)
    if result.precision_results and not any(pr.success for pr in result.precision_results):
        sys.exit(2)
    sys.exit(0)
if __name__ == "__main__":
    main()
