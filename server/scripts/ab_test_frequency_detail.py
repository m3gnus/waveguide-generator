#!/usr/bin/env python
"""
Detailed frequency comparison: BM on vs BM off across many frequencies.
Checks for fictitious resonance artifacts.

Run from server/ directory with the opencl-cpu-env Python:
    ~/.waveguide-generator/opencl-cpu-env/bin/python scripts/ab_test_frequency_detail.py
"""

import os
import sys
import time
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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


def run_solve(mesh, label, num_freq, use_bm, quad_regular=4, wg_size=2, device_interface=None):
    import bempp_cl.api as bempp_api
    from solver.solve import solve_optimized
    from solver.device_interface import clear_device_selection_caches

    orig_quad = bempp_api.GLOBAL_PARAMETERS.quadrature.regular
    orig_wg = bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple
    orig_di = bempp_api.DEFAULT_DEVICE_INTERFACE

    bempp_api.GLOBAL_PARAMETERS.quadrature.regular = quad_regular
    bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = wg_size

    if device_interface:
        bempp_api.DEFAULT_DEVICE_INTERFACE = device_interface
        clear_device_selection_caches()

    device_mode = "auto" if device_interface == "numba" else "opencl_cpu"

    print(f"\n{'='*60}")
    print(f"  {label}: {num_freq} freqs, BM={'on' if use_bm else 'off'}, quad={quad_regular}, wg={wg_size}")
    print(f"{'='*60}")

    t0 = time.time()
    results = solve_optimized(
        mesh=mesh,
        frequency_range=[100.0, 20000.0],
        num_frequencies=num_freq,
        sim_type="2",
        verbose=False,
        mesh_validation_mode="off",
        frequency_spacing="log",
        device_mode=device_mode,
        use_burton_miller=use_bm,
        bem_precision="single",
    )
    elapsed = time.time() - t0

    bempp_api.GLOBAL_PARAMETERS.quadrature.regular = orig_quad
    bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = orig_wg
    if device_interface:
        bempp_api.DEFAULT_DEVICE_INTERFACE = orig_di
        clear_device_selection_caches()

    freqs = results.get("frequencies", [])
    spls = results.get("spl_on_axis", {}).get("spl", [])
    di_vals = results.get("di", {}).get("di", [])
    failures = results.get("metadata", {}).get("failure_count", 0)

    print(f"  Done in {elapsed:.1f}s ({elapsed/num_freq:.1f}s/freq), {failures} failures")
    return {
        "label": label,
        "elapsed": elapsed,
        "per_freq": elapsed / num_freq,
        "frequencies": freqs,
        "spl": spls,
        "di": di_vals,
        "failures": failures,
        "use_bm": use_bm,
        "quad": quad_regular,
        "wg": wg_size,
    }


def main():
    mesh = build_mesh()
    num_freq = 20  # 20 points across 100-20000 Hz (log spaced)

    configs = [
        ("A: BM=on, quad=4, wg=1 (recommended)", True, 4, 1, None),
        ("B: BM=off, quad=4, wg=1", False, 4, 1, None),
        ("C: BM=on, quad=3, wg=1", True, 3, 1, None),
        ("D: BM=off, quad=3, wg=1", False, 3, 1, None),
        ("E: Numba, BM=on, quad=4", True, 4, 2, "numba"),
        ("F: Numba, BM=off, quad=4", False, 4, 2, "numba"),
    ]

    results = []
    for label, use_bm, quad, wg, di in configs:
        r = run_solve(mesh, label, num_freq, use_bm, quad, wg, di)
        results.append(r)

    # Print comparison table
    print("\n\n")
    print("=" * 80)
    print("DETAILED FREQUENCY COMPARISON")
    print("=" * 80)

    # Timing summary
    print(f"\n{'Config':<40} {'Total':>7} {'Per-f':>7} {'Speedup':>8}")
    print("-" * 62)
    baseline = results[0]["elapsed"]
    for r in sorted(results, key=lambda x: x["elapsed"]):
        speedup = baseline / r["elapsed"]
        print(f"  {r['label']:<38} {r['elapsed']:>6.1f}s {r['per_freq']:>6.1f}s {speedup:>7.2f}x")

    # SPL comparison at each frequency
    print(f"\n\nSPL ON-AXIS COMPARISON (dB)")
    print("-" * 100)
    header = f"{'Freq (Hz)':>10}"
    for r in results:
        short = r['label'].split(':')[0]
        header += f" {short:>10}"
    header += "   A-B diff"
    print(header)
    print("-" * 100)

    a_spls = results[0]["spl"]
    b_spls = results[1]["spl"]
    freqs = results[0]["frequencies"]

    max_diff = 0
    for i, freq in enumerate(freqs):
        row = f"{freq:>10.0f}"
        for r in results:
            if i < len(r["spl"]):
                row += f" {r['spl'][i]:>10.1f}"
            else:
                row += f" {'N/A':>10}"

        # Show difference between BM on vs off
        if i < len(a_spls) and i < len(b_spls):
            diff = abs(a_spls[i] - b_spls[i])
            max_diff = max(max_diff, diff)
            flag = " ⚠️" if diff > 3.0 else ""
            row += f"   {diff:>5.1f} dB{flag}"
        print(row)

    print("-" * 100)
    print(f"\nMax BM on/off SPL difference: {max_diff:.1f} dB")
    if max_diff > 5:
        print("⚠️  Significant differences detected — Burton-Miller may be important for accuracy")
    elif max_diff > 2:
        print("⚠️  Moderate differences — Burton-Miller recommended for precise work")
    else:
        print("✓  Differences are small — disabling BM is safe for this geometry")

    # Save
    out_path = Path(__file__).parent / "ab_test_frequency_detail_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
