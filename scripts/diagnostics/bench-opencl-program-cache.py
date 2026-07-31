"""Regenerate the OpenCL program cache benchmark artifact.

    python scripts/diagnostics/bench-opencl-program-cache.py [--repeats 3]

Solves the reference horn at five frequencies in full, half and quarter domains
with the bempp-cl program cache off and on, and records both the speed ratio
and a parity check. The cache is a pure memoization, so the parity numbers must
come out exactly zero; the seconds are host- and load-dependent and only the
ratios are meaningful across machines.

Writes ``benchmarks/cpubem/windows-bempp-opencl-program-cache-blas1-<date>.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MESH_DIR = REPO_ROOT / "scripts" / "diagnostics" / "out"
CASES = {
    "full": ("test_reference_horn.msh", None),
    "half": ("test_reference_horn_q14.msh", "yz"),
    "quarter": ("test_reference_horn_q1.msh", "yz+xz"),
}
FREQUENCIES = [500.0, 750.0, 1000.0, 1500.0, 2000.0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--date", default="2026-07-30")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT / "server"))
    from solver.bempp_compat import apply_bempp_opencl_workarounds

    apply_bempp_opencl_workarounds()

    import logging

    import numpy as np

    logging.disable(logging.INFO)
    try:
        import bempp_cl.core.opencl_kernels as opencl_kernels
        from hornlab_bempp_bem import solve_frequencies
        from hornlab_bempp_bem._opencl_program_cache import (
            clear_opencl_program_cache,
            disable_opencl_program_cache,
            enable_opencl_program_cache,
            opencl_program_cache_size,
        )
        from hornlab_bempp_bem.config import ObservationConfig, SolveConfig
    except ImportError as exc:
        print(f"hornlab-bempp-bem with the program cache is required: {exc}")
        return 1

    frequencies = np.array(FREQUENCIES, dtype=np.float64)
    cases: dict[str, dict] = {}

    for name, (filename, plane) in CASES.items():
        mesh = MESH_DIR / filename
        if not mesh.exists():
            print(f"skipping {name}: {mesh} not found")
            continue
        config = SolveConfig(
            formulation="standard", precision="single",
            assembly_backend="opencl", opencl_device="cpu",
            native_symmetry_plane=plane, gmres_tol=1e-6,
            observation=ObservationConfig(
                planes=["horizontal", "vertical"], distance_m=2.0,
                angle_count=37,
            ),
        )

        def sweep(cached: bool):
            if cached:
                enable_opencl_program_cache()
            else:
                disable_opencl_program_cache()
            start = time.perf_counter()
            result = solve_frequencies(str(mesh), frequencies, config)
            return time.perf_counter() - start, result

        sweep(False)                       # warm the disk caches
        uncached = [sweep(False) for _ in range(args.repeats)]
        clear_opencl_program_cache()
        sweep(True)
        cached = [sweep(True) for _ in range(args.repeats)]

        best_uncached = min(t for t, _ in uncached)
        best_cached = min(t for t, _ in cached)
        reference, actual = uncached[0][1], cached[0][1]
        entry = {
            "mesh": filename,
            "symmetry": plane,
            "n_vertices": int(actual.mesh_info.n_vertices),
            "n_triangles": int(actual.mesh_info.n_triangles),
            "repeats": args.repeats,
            "uncached_s_per_frequency": best_uncached / len(frequencies),
            "cached_s_per_frequency": best_cached / len(frequencies),
            "uncached_median_s": statistics.median(t for t, _ in uncached),
            "cached_median_s": statistics.median(t for t, _ in cached),
            "speedup": best_uncached / best_cached,
            "distinct_programs_cached": opencl_program_cache_size(),
            "max_spl_diff_db": float(
                np.max(np.abs(actual.spl_db - reference.spl_db))
            ),
            "max_pressure_diff_pa": float(
                np.max(np.abs(actual.pressure_complex - reference.pressure_complex))
            ),
            "max_relative_impedance_diff": float(
                np.max(np.abs(actual.impedance - reference.impedance)
                       / np.abs(reference.impedance))
            ),
            "bitwise_identical": bool(
                np.array_equal(actual.spl_db, reference.spl_db)
                and np.array_equal(
                    actual.pressure_complex, reference.pressure_complex
                )
            ),
        }
        cases[name] = entry
        print(f"{name:8s} {entry['uncached_s_per_frequency'] * 1000:7.1f} -> "
              f"{entry['cached_s_per_frequency'] * 1000:7.1f} ms/frequency   "
              f"{entry['speedup']:.2f}x   "
              f"bitwise identical: {entry['bitwise_identical']}")

    enable_opencl_program_cache()
    payload = {
        "schema_version": 1,
        "generator": "scripts/diagnostics/bench-opencl-program-cache.py",
        "host": platform.node(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "blas_threads": os.environ.get("OPENBLAS_NUM_THREADS", "default"),
        "note": (
            "Seconds are host- and load-dependent; the speedup ratios and the "
            "parity fields are the result. BLAS threads are limited around the "
            "dense solve by hornlab_bempp_bem._blas_threads, not globally."
        ),
        "frequencies_hz": FREQUENCIES,
        "cases": cases,
    }
    destination = Path(args.output) if args.output else (
        REPO_ROOT / "benchmarks" / "cpubem"
        / f"windows-bempp-opencl-program-cache-{args.date}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
