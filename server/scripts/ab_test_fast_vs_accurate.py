#!/usr/bin/env python
"""
A/B test: Fast preset vs Accurate preset.
Tests the actual default settings on the full asro68 mesh config.

Run with the opencl-cpu-env Python:
    cd server && ~/.waveguide-generator/opencl-cpu-env/bin/python scripts/ab_test_fast_vs_accurate.py
"""

import os
import sys
import time
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Full asro68 mesh config (original resolution)
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

NUM_FREQS = 10


def build_mesh():
    from contracts import WaveguideParamsRequest
    from solver.waveguide_builder import build_waveguide_mesh
    from scripts.benchmark_solver import load_mesh

    print("Building mesh...")
    t0 = time.time()
    request = WaveguideParamsRequest(**OCC_PARAMS)
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".msh", delete=False) as f:
        f.write(result["msh_text"])
        tmp_path = f.name
    mesh = load_mesh(tmp_path)
    os.unlink(tmp_path)
    print(f"  Mesh: {mesh['grid'].number_of_elements} elements in {time.time()-t0:.1f}s")
    return mesh


def run_solve(mesh, label, use_bm, quad_regular=4, wg_size=1):
    import bempp_cl.api as bempp_api
    from solver.solve import solve_optimized

    bempp_api.GLOBAL_PARAMETERS.quadrature.regular = quad_regular
    bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = wg_size

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  BM={'on' if use_bm else 'off'}, quad={quad_regular}, wg={wg_size}")
    print(f"{'='*60}")

    t0 = time.time()
    results = solve_optimized(
        mesh=mesh,
        frequency_range=[100.0, 20000.0],
        num_frequencies=NUM_FREQS,
        sim_type="2",
        verbose=False,
        mesh_validation_mode="off",
        frequency_spacing="log",
        device_mode="opencl_cpu",
        use_burton_miller=use_bm,
        bem_precision="single",
    )
    elapsed = time.time() - t0

    freqs = results.get("frequencies", [])
    spls = results.get("spl_on_axis", {}).get("spl", [])
    failures = results.get("metadata", {}).get("failure_count", 0)

    print(f"  Done: {elapsed:.1f}s total, {elapsed/NUM_FREQS:.1f}s/freq, {failures} failures")
    return {"label": label, "elapsed": elapsed, "per_freq": elapsed / NUM_FREQS,
            "frequencies": freqs, "spl": spls, "failures": failures, "use_bm": use_bm}


def main():
    mesh = build_mesh()

    fast = run_solve(mesh, "FAST preset (BM=off, quad=4, wg=1)", use_bm=False)
    accurate = run_solve(mesh, "ACCURATE preset (BM=on, quad=4, wg=1)", use_bm=True)

    print("\n\n")
    print("=" * 72)
    print("FAST vs ACCURATE COMPARISON")
    print("=" * 72)
    speedup = accurate["elapsed"] / fast["elapsed"]
    print(f"  Fast:     {fast['elapsed']:>7.1f}s total, {fast['per_freq']:>5.1f}s/freq")
    print(f"  Accurate: {accurate['elapsed']:>7.1f}s total, {accurate['per_freq']:>5.1f}s/freq")
    print(f"  Speedup:  {speedup:.2f}x")

    print(f"\n{'Freq (Hz)':>10} {'Accurate':>10} {'Fast':>10} {'Diff':>10}")
    print("-" * 42)
    max_diff = 0
    for i in range(min(len(accurate["frequencies"]), len(fast["frequencies"]))):
        freq = accurate["frequencies"][i]
        a_spl = accurate["spl"][i] if i < len(accurate["spl"]) else None
        f_spl = fast["spl"][i] if i < len(fast["spl"]) else None
        if a_spl is not None and f_spl is not None:
            diff = abs(a_spl - f_spl)
            max_diff = max(max_diff, diff)
            flag = " ***" if diff > 5.0 else " *" if diff > 3.0 else ""
            print(f"{freq:>10.0f} {a_spl:>10.1f} {f_spl:>10.1f} {diff:>9.1f}{flag}")

    print("-" * 42)
    print(f"  Max SPL difference: {max_diff:.1f} dB")
    print(f"  Time saved: {accurate['elapsed'] - fast['elapsed']:.0f}s ({speedup:.1f}x faster)")
    print("=" * 72)

    out = Path(__file__).parent / "ab_test_fast_vs_accurate_results.json"
    with open(out, "w") as f:
        json.dump({"fast": fast, "accurate": accurate}, f, indent=2, default=str)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
