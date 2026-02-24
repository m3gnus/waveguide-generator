"""
Repeatable BEM solver benchmark tool.

Usage (run from server/ directory):
    python scripts/benchmark_solver.py <mesh.msh> [options]

Options:
    --freq-min FLOAT    Minimum frequency in Hz (default: 500)
    --freq-max FLOAT    Maximum frequency in Hz (default: 8000)
    --num-freq INT      Number of frequencies (default: 10)
    --device MODE       Device mode: auto|opencl_gpu|opencl_cpu|numba (default: auto)
    --spacing MODE      Frequency spacing: log|linear (default: log)
    --no-warmup         Skip the warm-up pass (to measure first-solve penalty)

Output:
    Human-readable timing breakdown + GMRES iteration stats.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add server/ to path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_mesh(msh_path: str):
    try:
        from solver.deps import bempp_api
    except ImportError as exc:
        print(f"ERROR: bempp not available: {exc}", file=sys.stderr)
        sys.exit(1)

    path = Path(msh_path)
    if not path.exists():
        print(f"ERROR: mesh file not found: {msh_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading mesh: {path.name}")
    grid = bempp_api.import_grid(str(path))

    # Identify throat elements (tag=2) for the solver mesh dict format
    try:
        tags = grid.domain_indices
    except AttributeError:
        tags = None

    import numpy as np
    throat_elements = np.where(tags == 2)[0] if tags is not None else np.array([], dtype=int)

    return {
        "grid": grid,
        "throat_elements": throat_elements,
        "original_vertices": grid.vertices,
        "original_indices": grid.elements,
        "original_surface_tags": tags,
        "unit_detection": {"source": "benchmark", "warnings": []},
        "mesh_metadata": {},
    }


def run_benchmark(args):
    from solver.solve_optimized import solve_optimized
    from solver.device_interface import selected_device_metadata

    mesh = load_mesh(args.mesh)
    n_elements = mesh["grid"].number_of_elements if hasattr(mesh["grid"], "number_of_elements") else "?"
    print(f"  Elements: {n_elements}")
    print(f"  Frequency range: {args.freq_min}–{args.freq_max} Hz ({args.num_freq} points, {args.spacing})")
    print(f"  Device mode: {args.device}")
    print()

    dev_meta = selected_device_metadata(args.device)
    print(f"  Selected device: {dev_meta.get('selected_mode', '?')} / {dev_meta.get('device_name', '?')}")
    if dev_meta.get("fallback_reason"):
        print(f"  Fallback reason: {dev_meta['fallback_reason']}")
    print()

    t0 = time.time()
    enable_warmup = not args.no_warmup
    print(f"  Warm-up: {'enabled' if enable_warmup else 'disabled (--no-warmup)'}")
    results = solve_optimized(
        mesh=mesh,
        frequency_range=[args.freq_min, args.freq_max],
        num_frequencies=args.num_freq,
        sim_type="2",
        enable_symmetry=True,
        verbose=True,
        mesh_validation_mode="warn",
        frequency_spacing=args.spacing,
        device_mode=args.device,
        enable_warmup=enable_warmup,
    )
    wall_time = time.time() - t0

    perf = results["metadata"]["performance"]
    meta = results["metadata"]

    print()
    print("=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)
    print(f"  Wall time (total):        {wall_time:.2f}s")
    print(f"  Warm-up:                  {perf.get('warmup_time_seconds', 0):.2f}s")
    print(f"  Frequency solve:          {perf['frequency_solve_time']:.2f}s")
    print(f"  Per-frequency (avg):      {perf['time_per_frequency']:.3f}s")
    print(f"  Directivity compute:      {perf['directivity_compute_time']:.2f}s")
    if perf.get("reduction_speedup", 1.0) > 1.0:
        print(f"  Symmetry speedup:         {perf['reduction_speedup']:.1f}x")
    print()

    iters = perf.get("gmres_iterations_per_frequency", [])
    valid_iters = [n for n in iters if n is not None]
    if valid_iters:
        print(f"  GMRES avg iterations:     {perf.get('avg_gmres_iterations', 0):.1f}")
        print(f"  GMRES min/max:            {min(valid_iters)} / {max(valid_iters)}")
        print(f"  GMRES per-frequency:      {iters}")
    print()

    dev = meta.get("device_interface", {})
    print(f"  Device selected:          {dev.get('selected_mode', '?')}")
    print(f"  Runtime interface:        {dev.get('runtime_selected', dev.get('interface', '?'))}")
    if dev.get("runtime_retry_attempted"):
        print(f"  Runtime retry:            {dev.get('runtime_retry_outcome', '?')}")

    if meta["failure_count"] > 0:
        print(f"\n  FAILURES ({meta['failure_count']}):")
        for f in meta["failures"]:
            print(f"    {f['frequency_hz']:.1f} Hz — {f['code']}: {f['detail']}")

    if meta["warning_count"] > 0:
        print(f"\n  WARNINGS ({meta['warning_count']}):")
        for w in meta["warnings"]:
            print(f"    {w.get('frequency_hz', '?'):.1f} Hz — {w['code']}")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="BEM solver benchmark", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("mesh", help="Path to .msh mesh file")
    parser.add_argument("--freq-min", type=float, default=500.0, help="Min frequency (Hz)")
    parser.add_argument("--freq-max", type=float, default=8000.0, help="Max frequency (Hz)")
    parser.add_argument("--num-freq", type=int, default=10, help="Number of frequencies")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "opencl_gpu", "opencl_cpu", "numba"],
        help="Device mode",
    )
    parser.add_argument(
        "--spacing",
        default="log",
        choices=["log", "linear"],
        help="Frequency spacing",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        default=False,
        help="Skip the warm-up pass (measures first-solve penalty for A/B comparison)",
    )
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
