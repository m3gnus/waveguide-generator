#!/usr/bin/env python
"""
Compare directivity patterns: BEM vs ATH/ABEC reference.
Both use normalized polar data (on-axis = 0 dB).

Run from server/ directory:
    ~/.waveguide-generator/opencl-cpu-env/bin/python scripts/compare_ath_directivity.py
"""

import os
import sys
import time
import tempfile
import json
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ATH_FILE = "/Users/magnus/IM Dropbox/Magnus Andersen/DOCS/code/misc/250917asro68 ATH results/Spectrum_ABEC.txt"

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


def parse_ath_polar_data(filepath):
    """Parse ATH horizontal polar data: complex pressure at each angle per frequency."""
    with open(filepath, "r") as f:
        text = f.read()

    blocks = text.split("// " + "-" * 60)
    angles = list(range(0, 181, 5))  # 0, 5, 10, ... 180

    for block in blocks:
        lines = block.strip().split("\n")
        meta = {}
        data_lines = []
        in_data = False

        for line in lines:
            line = line.strip()
            if line == "Data":
                in_data = True
                continue
            if line == "Data_End":
                in_data = False
                continue
            if in_data and line:
                data_lines.append(line)
            elif "=" in line and not line.startswith("//"):
                key, _, val = line.partition("=")
                meta[key.strip()] = val.strip().strip('"')

        if meta.get("Graph_Caption") != "PM_SPL_H":
            continue

        # Parse complex pressure data: freq  re0 im0  re1 im1  ... re36 im36
        result = {"frequencies": [], "polar_db": []}
        for dl in data_lines:
            parts = dl.split()
            freq = float(parts[0])
            # Extract complex pressure for each angle
            pressures = []
            for j in range(37):  # 37 angles: 0, 5, 10, ... 180
                idx = 1 + j * 2
                if idx + 1 < len(parts):
                    re_val = float(parts[idx])
                    im_val = float(parts[idx + 1])
                    mag = np.sqrt(re_val**2 + im_val**2)
                    pressures.append(mag)

            # Normalize to on-axis (angle 0) and convert to dB
            on_axis = pressures[0] if pressures[0] > 0 else 1e-30
            polar_db = [20 * np.log10(p / on_axis) if p > 0 else -60 for p in pressures]

            result["frequencies"].append(freq)
            result["polar_db"].append(polar_db)

        print(f"  ATH polar: {len(result['frequencies'])} frequencies, {len(angles)} angles")
        return result, angles

    return None, None


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


def run_solve(mesh, label, use_bm, quad=4, wg=1, num_freq=10):
    import bempp_cl.api as bempp_api
    from solver.solve import solve_optimized

    bempp_api.GLOBAL_PARAMETERS.quadrature.regular = quad
    bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = wg

    print(f"\n  Running: {label} ({num_freq} freq)...")
    t0 = time.time()
    results = solve_optimized(
        mesh=mesh,
        frequency_range=[100.0, 20000.0],
        num_frequencies=num_freq,
        sim_type="2",
        polar_config={
            "angle_range": [0, 180, 5],
            "norm_angle": 5,
            "distance": 2.0,
            "enabled_axes": ["horizontal"],
        },
        verbose=False,
        mesh_validation_mode="off",
        frequency_spacing="log",
        device_mode="opencl_cpu",
        use_burton_miller=use_bm,
        bem_precision="single",
    )
    elapsed = time.time() - t0
    print(f"  Done: {elapsed:.1f}s ({elapsed/num_freq:.1f}s/freq)")
    return results


def extract_bem_polar_db(results):
    """Extract normalized polar patterns from BEM results."""
    directivity = results.get("directivity", {})
    freqs = results.get("frequencies", [])

    # Find horizontal plane data
    horiz = directivity.get("horizontal", [])
    if not horiz:
        # Try first available plane
        for key in directivity:
            if isinstance(directivity[key], list) and len(directivity[key]) > 0:
                horiz = directivity[key]
                break

    if not horiz:
        print("  WARNING: No directivity data found")
        return None

    polar_db_per_freq = []
    for freq_data in horiz:
        if not isinstance(freq_data, (list, dict)):
            continue
        # freq_data is a list of [angle, spl] pairs or dict
        if isinstance(freq_data, dict):
            angles = freq_data.get("angles", [])
            spl = freq_data.get("spl", [])
        elif isinstance(freq_data, list):
            if len(freq_data) == 0:
                continue
            if isinstance(freq_data[0], (list, tuple)):
                angles = [p[0] for p in freq_data]
                spl = [p[1] for p in freq_data]
            else:
                continue

        # Normalize: subtract on-axis (first angle) value
        if spl:
            on_axis_spl = spl[0]
            normalized = [s - on_axis_spl for s in spl]
            polar_db_per_freq.append({"angles": angles, "normalized_db": normalized})

    return polar_db_per_freq


def compare_directivity(label, bem_polar, ath_polar, ath_angles, bem_freqs, ath_freqs):
    """Compare normalized polar patterns at matching frequencies."""
    # Find frequency pairs that are close enough
    comparisons = []
    for i, bem_f in enumerate(bem_freqs):
        if i >= len(bem_polar):
            continue
        # Find closest ATH frequency
        ath_idx = np.argmin(np.abs(np.array(ath_freqs) - bem_f))
        ath_f = ath_freqs[ath_idx]
        if abs(ath_f - bem_f) / bem_f > 0.15:  # within 15%
            continue

        bem_data = bem_polar[i]
        ath_data = ath_polar[ath_idx]

        # Interpolate BEM data to ATH angles
        bem_angles = np.array(bem_data["angles"])
        bem_db = np.array(bem_data["normalized_db"])
        ath_db = np.array(ath_data)

        # Only compare common angle range
        max_angle = min(max(bem_angles), 180)
        common_angles = [a for a in ath_angles if a <= max_angle]

        bem_interp = np.interp(common_angles, bem_angles, bem_db)
        ath_vals = ath_db[:len(common_angles)]

        diffs = bem_interp - ath_vals
        abs_diffs = np.abs(diffs)

        comparisons.append({
            "freq": bem_f,
            "ath_freq": ath_f,
            "mean_abs_diff": float(np.mean(abs_diffs)),
            "max_abs_diff": float(np.max(abs_diffs)),
            "angles": common_angles,
            "bem_db": bem_interp.tolist(),
            "ath_db": ath_vals.tolist(),
            "diffs": diffs.tolist(),
        })

    return comparisons


def main():
    print("=" * 72)
    print("ATH vs BEM DIRECTIVITY COMPARISON")
    print("=" * 72)

    print("\nParsing ATH reference...")
    ath_polar, ath_angles = parse_ath_polar_data(ATH_FILE)
    if not ath_polar:
        print("ERROR: Could not parse ATH polar data")
        return

    mesh = build_mesh()

    configs = [
        ("Accurate (BM=on, q=4)", True, 4),
        ("Fast (BM=off, q=4)", False, 4),
    ]

    num_freq = 10  # Fewer freq for directivity (expensive)

    all_comparisons = {}
    for label, use_bm, quad in configs:
        sim = run_solve(mesh, label, use_bm, quad, num_freq=num_freq)
        bem_polar = extract_bem_polar_db(sim)
        if not bem_polar:
            print(f"  No polar data for {label}")
            continue

        comps = compare_directivity(
            label, bem_polar, ath_polar["polar_db"],
            ath_angles, sim["frequencies"], ath_polar["frequencies"]
        )
        all_comparisons[label] = comps

        # Summary
        if comps:
            mean_all = np.mean([c["mean_abs_diff"] for c in comps])
            max_all = max(c["max_abs_diff"] for c in comps)
            print(f"  {label}: mean polar error = {mean_all:.1f} dB, max = {max_all:.1f} dB")

    # Print detailed results
    print("\n\n")
    print("=" * 80)
    print("DIRECTIVITY COMPARISON RESULTS")
    print("=" * 80)

    for label, comps in all_comparisons.items():
        print(f"\n--- {label} ---")
        print(f"{'Freq':>8} {'ATH freq':>10} {'Mean |diff|':>12} {'Max |diff|':>12}")
        print("-" * 44)
        for c in comps:
            flag = " ***" if c["max_abs_diff"] > 6 else " *" if c["max_abs_diff"] > 3 else ""
            print(f"{c['freq']:>8.0f} {c['ath_freq']:>10.0f} {c['mean_abs_diff']:>10.1f} dB {c['max_abs_diff']:>10.1f} dB{flag}")

        if comps:
            overall_mean = np.mean([c["mean_abs_diff"] for c in comps])
            overall_max = max(c["max_abs_diff"] for c in comps)
            print(f"{'Overall':>8} {'':>10} {overall_mean:>10.1f} dB {overall_max:>10.1f} dB")

    # Print polar detail at a few key frequencies for best config
    best_label = min(all_comparisons.keys(), key=lambda k: np.mean([c["mean_abs_diff"] for c in all_comparisons[k]]))
    best_comps = all_comparisons[best_label]
    print(f"\nBest match: {best_label}")

    # Show polar at ~1kHz and ~4kHz
    for target_f in [1000, 4000, 10000]:
        closest = min(best_comps, key=lambda c: abs(c["freq"] - target_f))
        if abs(closest["freq"] - target_f) / target_f > 0.3:
            continue
        print(f"\n  Polar at {closest['freq']:.0f} Hz (ATH: {closest['ath_freq']:.0f} Hz):")
        print(f"  {'Angle':>6} {'ATH':>8} {'BEM':>8} {'Diff':>8}")
        for j, angle in enumerate(closest["angles"]):
            if j < len(closest["ath_db"]) and j < len(closest["bem_db"]):
                if angle % 15 == 0:  # print every 15 degrees
                    flag = " *" if abs(closest["diffs"][j]) > 3 else ""
                    print(f"  {angle:>5}° {closest['ath_db'][j]:>7.1f} {closest['bem_db'][j]:>7.1f} {closest['diffs'][j]:>+7.1f}{flag}")

    out = Path(__file__).parent / "compare_ath_directivity_results.json"
    with open(out, "w") as f:
        json.dump(all_comparisons, f, indent=2, default=str)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
