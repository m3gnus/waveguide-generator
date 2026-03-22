#!/usr/bin/env python
"""
Compare simulation results against ATH/ABEC reference data.
Parses Spectrum_ABEC.txt and runs BEM simulations with different settings.

Run from server/ directory:
    ~/.waveguide-generator/opencl-cpu-env/bin/python scripts/compare_ath_results.py
"""

import os
import sys
import time
import tempfile
import json
import re
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


def parse_ath_spectrum(filepath):
    """Parse ATH Spectrum_ABEC.txt to extract on-axis SPL and impedance."""
    with open(filepath, "r") as f:
        text = f.read()

    # Split into data blocks
    blocks = text.split("// " + "-" * 60)

    results = {"impedance": None, "spl_on_axis": None}

    for block in blocks:
        lines = block.strip().split("\n")

        # Parse metadata
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

        level_type = meta.get("Data_LevelType", "")
        legend = meta.get("Data_Legend", "")
        caption = meta.get("Graph_Caption", "")

        # Impedance data
        if level_type == "Impedance10":
            freqs, real_parts, imag_parts = [], [], []
            for dl in data_lines:
                parts = dl.split()
                if len(parts) >= 3:
                    freqs.append(float(parts[0]))
                    real_parts.append(float(parts[1]))
                    imag_parts.append(float(parts[2]))
            results["impedance"] = {
                "frequencies": freqs,
                "real": real_parts,
                "imaginary": imag_parts,
            }
            print(f"  Parsed impedance: {len(freqs)} frequencies ({freqs[0]:.0f}-{freqs[-1]:.0f} Hz)")

        # On-axis SPL (horizontal plane, 0 deg inclination)
        if "PM_SPL_H" in caption and level_type == "Peak":
            freqs = []
            spl_on_axis = []
            p_ref = 20e-6

            for dl in data_lines:
                parts = dl.split()
                if len(parts) < 3:
                    continue
                freq = float(parts[0])
                # First pair is angle 0 (on-axis): real + imag
                re_val = float(parts[1])
                im_val = float(parts[2])
                pressure = np.sqrt(re_val**2 + im_val**2)
                spl = 20 * np.log10(pressure / p_ref) if pressure > 0 else 0
                freqs.append(freq)
                spl_on_axis.append(spl)

            results["spl_on_axis"] = {
                "frequencies": freqs,
                "spl": spl_on_axis,
            }
            print(f"  Parsed on-axis SPL: {len(freqs)} frequencies ({freqs[0]:.0f}-{freqs[-1]:.0f} Hz)")

    return results


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


def run_solve(mesh, label, use_bm, quad=4, wg=1, num_freq=20):
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


def interpolate_ath_to_freqs(ath_data, target_freqs):
    """Interpolate ATH data to match target frequencies."""
    ath_freqs = np.array(ath_data["frequencies"])
    ath_spl = np.array(ath_data["spl"])
    return np.interp(target_freqs, ath_freqs, ath_spl)


def compare_results(label, sim_results, ath_spl_interp, sim_freqs):
    """Compare simulation SPL with ATH reference, normalized to match at 1 kHz."""
    sim_spl = np.array(sim_results["spl_on_axis"]["spl"])

    # Normalize both curves: align at closest frequency to 1 kHz
    ref_idx = np.argmin(np.abs(sim_freqs - 1000.0))
    offset = sim_spl[ref_idx] - ath_spl_interp[ref_idx]
    sim_spl_norm = sim_spl - offset  # shift BEM to match ATH level at 1 kHz

    diffs = sim_spl_norm - ath_spl_interp
    abs_diffs = np.abs(diffs)

    return {
        "label": label,
        "mean_abs_diff": float(np.mean(abs_diffs)),
        "max_abs_diff": float(np.max(abs_diffs)),
        "rms_diff": float(np.sqrt(np.mean(diffs**2))),
        "offset_applied": float(offset),
        "ref_freq": float(sim_freqs[ref_idx]),
        "diffs": diffs.tolist(),
        "sim_spl": sim_spl.tolist(),
        "sim_spl_norm": sim_spl_norm.tolist(),
        "ath_spl_interp": ath_spl_interp.tolist(),
        "frequencies": sim_freqs.tolist(),
    }


def main():
    print("=" * 72)
    print("ATH vs BEM COMPARISON")
    print("=" * 72)

    # Parse ATH reference
    print("\nParsing ATH reference data...")
    ath = parse_ath_spectrum(ATH_FILE)

    if not ath["spl_on_axis"]:
        print("ERROR: Could not parse on-axis SPL from ATH data")
        return

    # Build mesh
    mesh = build_mesh()

    num_freq = 20

    # Run different configurations
    configs = [
        ("BM=on, quad=4, wg=1 (Accurate)", True, 4, 1),
        ("BM=off, quad=4, wg=1 (Fast)", False, 4, 1),
        ("BM=on, quad=3, wg=1", True, 3, 1),
        ("BM=on, quad=5, wg=1", True, 5, 1),
    ]

    all_results = []
    for label, use_bm, quad, wg in configs:
        sim = run_solve(mesh, label, use_bm, quad, wg, num_freq)
        sim_freqs = np.array(sim["frequencies"])
        ath_spl_interp = interpolate_ath_to_freqs(ath["spl_on_axis"], sim_freqs)
        comparison = compare_results(label, sim, ath_spl_interp, sim_freqs)
        comparison["elapsed"] = sim.get("metadata", {}).get("performance", {}).get("total_time_seconds")
        all_results.append(comparison)

    # Print comparison table
    print("\n\n")
    print("=" * 80)
    print("COMPARISON RESULTS vs ATH REFERENCE")
    print("=" * 80)
    print(f"{'Config':<35} {'Mean |diff|':>12} {'Max |diff|':>12} {'RMS diff':>12}")
    print("-" * 72)
    for r in sorted(all_results, key=lambda x: x["mean_abs_diff"]):
        print(f"  {r['label']:<33} {r['mean_abs_diff']:>10.1f} dB {r['max_abs_diff']:>10.1f} dB {r['rms_diff']:>10.1f} dB")

    # Detailed per-frequency comparison for best config
    best = min(all_results, key=lambda x: x["mean_abs_diff"])
    print(f"\nBest match: {best['label']}")
    print(f"  Level offset applied: {best['offset_applied']:+.1f} dB (aligned at {best['ref_freq']:.0f} Hz)")
    print(f"\n{'Freq (Hz)':>10} {'ATH (dB)':>10} {'BEM (dB)':>10} {'Diff':>10}")
    print("-" * 42)

    for i, freq in enumerate(best["frequencies"]):
        flag = " ***" if abs(best["diffs"][i]) > 3 else " *" if abs(best["diffs"][i]) > 1.5 else ""
        print(f"{freq:>10.0f} {best['ath_spl_interp'][i]:>10.1f} {best['sim_spl_norm'][i]:>10.1f} {best['diffs'][i]:>+10.1f}{flag}")

    # Save
    out = Path(__file__).parent / "compare_ath_results.json"
    with open(out, "w") as f:
        json.dump({
            "ath_spl": ath["spl_on_axis"],
            "comparisons": all_results,
        }, f, indent=2, default=str)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
