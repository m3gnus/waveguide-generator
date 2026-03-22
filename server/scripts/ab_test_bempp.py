#!/usr/bin/env python
"""
A/B test: compare bempp-cl settings for BEM solve speed.

Tests different configurations on the same mesh with 3 frequencies
to find the fastest setup. Run from server/ directory:

    python scripts/ab_test_bempp.py
"""

import os
import sys
import time
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Mesh params matching the 250917asro68 config with reduced resolution ──
OCC_PARAMS = {
    "formula_type": "R-OSSE",
    "R": "160 * (abs(cos(p)/1.8)^3 + abs(sin(p)/1)^4)^(-1/7)",
    "a": "22 * (abs(cos(p)/1.2)^8 + abs(sin(p)/1)^4)^(-1/4)",
    "a0": 15.5,
    "r0": 12.7,
    "k": "4 * (abs(cos(p)/1.2)^8 + abs(sin(p)/1)^4)^(-1/4)",
    "q": 4,
    "r": 0.35,
    "b": 0.4,
    "m": 0.84,
    "tmax": 1.0,
    "quadrants": 1234,
    "enc_depth": 0,
    "wall_thickness": 6.0,
    "n_angular": 50,
    "n_length": 20,
    "throat_res": 8.0,
    "mouth_res": 20.0,
    "rear_res": 40.0,
}

# Test frequencies: low, mid, high — just 3 to keep tests fast
TEST_FREQS = [500.0, 2000.0, 8000.0]
NUM_FREQS = 3


def build_mesh():
    """Build OCC mesh once, return mesh dict for solver."""
    from contracts import WaveguideParamsRequest
    from solver.waveguide_builder import build_waveguide_mesh
    from scripts.benchmark_solver import load_mesh

    print("Building mesh...")
    t0 = time.time()
    request = WaveguideParamsRequest(**OCC_PARAMS)
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    msh_text = result["msh_text"]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".msh", delete=False) as f:
        f.write(msh_text)
        tmp_path = f.name

    mesh = load_mesh(tmp_path)
    os.unlink(tmp_path)

    n_elements = mesh["grid"].number_of_elements
    print(f"  Mesh built: {n_elements} elements in {time.time() - t0:.1f}s")
    return mesh


def run_single_test(mesh, label, bempp_config, solver_kwargs):
    """
    Run a single benchmark test.

    bempp_config: dict of bempp global parameter overrides to apply before solving
    solver_kwargs: dict of kwargs to pass to solve_optimized
    """
    import bempp_cl.api as bempp_api
    from solver.solve import solve_optimized
    from solver.device_interface import clear_device_selection_caches

    # Apply bempp global config
    orig_config = {}
    if "quadrature_regular" in bempp_config:
        orig_config["quadrature_regular"] = bempp_api.GLOBAL_PARAMETERS.quadrature.regular
        bempp_api.GLOBAL_PARAMETERS.quadrature.regular = bempp_config["quadrature_regular"]
    if "quadrature_singular" in bempp_config:
        orig_config["quadrature_singular"] = bempp_api.GLOBAL_PARAMETERS.quadrature.singular
        bempp_api.GLOBAL_PARAMETERS.quadrature.singular = bempp_config["quadrature_singular"]
    if "workgroup_size_multiple" in bempp_config:
        orig_config["workgroup_size_multiple"] = bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple
        bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = bempp_config["workgroup_size_multiple"]
    if "device_interface" in bempp_config:
        orig_config["device_interface"] = bempp_api.DEFAULT_DEVICE_INTERFACE
        bempp_api.DEFAULT_DEVICE_INTERFACE = bempp_config["device_interface"]
        # Clear caches so device selection picks up the new interface
        clear_device_selection_caches()

    # Default solver kwargs
    default_kwargs = {
        "frequency_range": [TEST_FREQS[0], TEST_FREQS[-1]],
        "num_frequencies": NUM_FREQS,
        "sim_type": "2",
        "verbose": False,
        "mesh_validation_mode": "off",
        "frequency_spacing": "linear",
        "device_mode": "auto",
        "use_burton_miller": True,
        "bem_precision": "single",
    }
    default_kwargs.update(solver_kwargs)

    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  bempp config: {bempp_config}")
    print(f"  solver kwargs: { {k:v for k,v in solver_kwargs.items()} }")
    print(f"{'='*60}")

    t0 = time.time()
    try:
        results = solve_optimized(mesh=mesh, **default_kwargs)
        elapsed = time.time() - t0

        metadata = results.get("metadata", {})
        perf = metadata.get("performance", {})
        failures = metadata.get("failure_count", 0)
        spl_values = results.get("spl_on_axis", {}).get("spl", [])

        print(f"  RESULT: {elapsed:.1f}s total")
        print(f"    Per-frequency avg: {elapsed/NUM_FREQS:.1f}s")
        if perf.get("frequency_solve_time"):
            print(f"    Frequency solve:   {perf['frequency_solve_time']:.1f}s")
        if perf.get("directivity_compute_time"):
            print(f"    Directivity:       {perf['directivity_compute_time']:.1f}s")
        print(f"    Failures: {failures}")
        if spl_values:
            print(f"    SPL on-axis: {[f'{v:.1f}' for v in spl_values]}")

        result = {
            "label": label,
            "total_time": elapsed,
            "per_freq_time": elapsed / NUM_FREQS,
            "freq_solve_time": perf.get("frequency_solve_time"),
            "directivity_time": perf.get("directivity_compute_time"),
            "failures": failures,
            "spl_values": spl_values,
            "success": True,
        }

    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  FAILED: {exc} ({elapsed:.1f}s)")
        result = {
            "label": label,
            "total_time": elapsed,
            "per_freq_time": None,
            "failures": None,
            "spl_values": [],
            "success": False,
            "error": str(exc),
        }

    # Restore original config
    if "quadrature_regular" in orig_config:
        bempp_api.GLOBAL_PARAMETERS.quadrature.regular = orig_config["quadrature_regular"]
    if "quadrature_singular" in orig_config:
        bempp_api.GLOBAL_PARAMETERS.quadrature.singular = orig_config["quadrature_singular"]
    if "workgroup_size_multiple" in orig_config:
        bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = orig_config["workgroup_size_multiple"]
    if "device_interface" in orig_config:
        bempp_api.DEFAULT_DEVICE_INTERFACE = orig_config["device_interface"]
        clear_device_selection_caches()

    return result


def main():
    mesh = build_mesh()
    results = []

    # ── TEST CONFIGURATIONS ──
    tests = [
        # Baseline: current settings
        (
            "A: Baseline (OpenCL CPU, BM=on, quad=4, wg=2)",
            {},
            {"device_mode": "opencl_cpu", "use_burton_miller": True},
        ),

        # Test B: Numba backend
        (
            "B: Numba backend (BM=on, quad=4)",
            {"device_interface": "numba"},
            {"device_mode": "auto", "use_burton_miller": True},
        ),

        # Test C: Burton-Miller OFF
        (
            "C: OpenCL CPU, BM=OFF",
            {},
            {"device_mode": "opencl_cpu", "use_burton_miller": False},
        ),

        # Test D: Lower quadrature (regular=2)
        (
            "D: OpenCL CPU, quad_regular=2",
            {"quadrature_regular": 2},
            {"device_mode": "opencl_cpu", "use_burton_miller": True},
        ),

        # Test E: Lower quadrature (regular=3)
        (
            "E: OpenCL CPU, quad_regular=3",
            {"quadrature_regular": 3},
            {"device_mode": "opencl_cpu", "use_burton_miller": True},
        ),

        # Test F: Workgroup size = 1
        (
            "F: OpenCL CPU, workgroup_size=1",
            {"workgroup_size_multiple": 1},
            {"device_mode": "opencl_cpu", "use_burton_miller": True},
        ),

        # Test G: Combined - BM off + quad=2
        (
            "G: OpenCL CPU, BM=OFF + quad=2",
            {"quadrature_regular": 2},
            {"device_mode": "opencl_cpu", "use_burton_miller": False},
        ),

        # Test H: Numba + BM off
        (
            "H: Numba, BM=OFF",
            {"device_interface": "numba"},
            {"device_mode": "auto", "use_burton_miller": False},
        ),

        # Test I: Combined best - BM off + quad=3 + wg=1
        (
            "I: OpenCL CPU, BM=OFF + quad=3 + wg=1",
            {"quadrature_regular": 3, "workgroup_size_multiple": 1},
            {"device_mode": "opencl_cpu", "use_burton_miller": False},
        ),
    ]

    for label, bempp_config, solver_kwargs in tests:
        result = run_single_test(mesh, label, bempp_config, solver_kwargs)
        results.append(result)

    # ── SUMMARY ──
    print("\n\n")
    print("=" * 72)
    print("A/B TEST SUMMARY")
    print("=" * 72)
    print(f"{'Test':<50} {'Total':>7} {'Per-f':>7} {'SPL[0]':>8}")
    print("-" * 72)

    # Sort by total time
    sorted_results = sorted(results, key=lambda r: r["total_time"] if r["success"] else 9999)
    baseline_time = next((r["total_time"] for r in results if "Baseline" in r["label"] and r["success"]), None)

    for r in sorted_results:
        if r["success"]:
            speedup = f"({baseline_time/r['total_time']:.2f}x)" if baseline_time else ""
            spl0 = f"{r['spl_values'][0]:.1f}" if r["spl_values"] else "N/A"
            print(f"  {r['label']:<48} {r['total_time']:>6.1f}s {r['per_freq_time']:>6.1f}s {spl0:>8} {speedup}")
        else:
            print(f"  {r['label']:<48} FAILED")

    print("=" * 72)

    # Save results
    out_path = Path(__file__).parent / "ab_test_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
