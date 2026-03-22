#!/usr/bin/env python
"""
Compare directivity: BEM vs ATH with FULL resolution mesh from asro68 config.
Mesh: throat=5, mouth=8, rear=25 (original ATH config).

Run from server/ directory:
    ~/.waveguide-generator/opencl-cpu-env/bin/python scripts/compare_ath_directivity_fullmesh.py
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

# FULL resolution mesh matching the ATH config exactly
OCC_PARAMS_FULL = {
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
    "throat_res": 5.0,    # Original ATH config
    "mouth_res": 8.0,     # Original ATH config
    "rear_res": 25.0,     # Original ATH config
}


def parse_ath_polar_data(filepath):
    """Parse ATH horizontal polar data."""
    with open(filepath, "r") as f:
        text = f.read()

    blocks = text.split("// " + "-" * 60)
    angles = list(range(0, 181, 5))

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

        result = {"frequencies": [], "polar_db": []}
        for dl in data_lines:
            parts = dl.split()
            freq = float(parts[0])
            pressures = []
            for j in range(37):
                idx = 1 + j * 2
                if idx + 1 < len(parts):
                    re_val = float(parts[idx])
                    im_val = float(parts[idx + 1])
                    mag = np.sqrt(re_val**2 + im_val**2)
                    pressures.append(mag)

            on_axis = pressures[0] if pressures[0] > 0 else 1e-30
            polar_db = [20 * np.log10(p / on_axis) if p > 0 else -60 for p in pressures]

            result["frequencies"].append(freq)
            result["polar_db"].append(polar_db)

        print(f"  ATH polar: {len(result['frequencies'])} frequencies, {len(angles)} angles")
        return result, angles

    return None, None


def build_mesh(params):
    from contracts import WaveguideParamsRequest
    from solver.waveguide_builder import build_waveguide_mesh
    from scripts.benchmark_solver import load_mesh

    print("Building mesh...")
    t0 = time.time()
    request = WaveguideParamsRequest(**params)
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".msh", delete=False) as f:
        f.write(result["msh_text"])
        tmp_path = f.name
    mesh = load_mesh(tmp_path)
    os.unlink(tmp_path)
    n = mesh['grid'].number_of_elements
    print(f"  Mesh: {n} elements in {time.time()-t0:.1f}s")
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
    horiz = directivity.get("horizontal", [])
    if not horiz:
        for key in directivity:
            if isinstance(directivity[key], list) and len(directivity[key]) > 0:
                horiz = directivity[key]
                break

    if not horiz:
        return None

    polar_db_per_freq = []
    for freq_data in horiz:
        if isinstance(freq_data, dict):
            angles = freq_data.get("angles", [])
            spl = freq_data.get("spl", [])
        elif isinstance(freq_data, list) and len(freq_data) > 0 and isinstance(freq_data[0], (list, tuple)):
            angles = [p[0] for p in freq_data]
            spl = [p[1] for p in freq_data]
        else:
            continue

        if spl:
            on_axis_spl = spl[0]
            normalized = [s - on_axis_spl for s in spl]
            polar_db_per_freq.append({"angles": angles, "normalized_db": normalized})

    return polar_db_per_freq


def main():
    print("=" * 72)
    print("ATH vs BEM DIRECTIVITY — FULL RESOLUTION MESH")
    print("=" * 72)

    print("\nParsing ATH reference...")
    ath_polar, ath_angles = parse_ath_polar_data(ATH_FILE)
    if not ath_polar:
        print("ERROR: Could not parse ATH polar data")
        return

    mesh = build_mesh(OCC_PARAMS_FULL)

    # Only run Accurate (BM=on) for fair comparison
    sim = run_solve(mesh, "Accurate (BM=on, q=4, full mesh)", True, quad=4, num_freq=10)
    bem_polar = extract_bem_polar_db(sim)
    if not bem_polar:
        print("No polar data extracted")
        return

    # Compare
    print("\n\n")
    print("=" * 80)
    print("DIRECTIVITY COMPARISON — FULL MESH vs ATH")
    print("=" * 80)

    ath_freqs = np.array(ath_polar["frequencies"])
    sim_freqs = np.array(sim["frequencies"])

    for i, bem_f in enumerate(sim_freqs):
        if i >= len(bem_polar):
            continue
        ath_idx = np.argmin(np.abs(ath_freqs - bem_f))
        ath_f = ath_freqs[ath_idx]
        if abs(ath_f - bem_f) / bem_f > 0.15:
            continue

        bem_data = bem_polar[i]
        ath_data = ath_polar["polar_db"][ath_idx]

        bem_angles = np.array(bem_data["angles"])
        bem_db = np.array(bem_data["normalized_db"])

        common_angles = [a for a in ath_angles if a <= max(bem_angles)]
        bem_interp = np.interp(common_angles, bem_angles, bem_db)
        ath_vals = np.array(ath_data[:len(common_angles)])

        diffs = bem_interp - ath_vals
        abs_diffs = np.abs(diffs)
        mean_err = float(np.mean(abs_diffs))
        max_err = float(np.max(abs_diffs))

        print(f"\n  {bem_f:.0f} Hz (ATH: {ath_f:.0f} Hz) — mean err: {mean_err:.1f} dB, max err: {max_err:.1f} dB")
        print(f"  {'Angle':>6} {'ATH':>8} {'BEM':>8} {'Diff':>8}")
        for j, angle in enumerate(common_angles):
            if j < len(ath_vals) and j < len(bem_interp):
                if angle % 15 == 0:
                    flag = " ***" if abs(diffs[j]) > 6 else " *" if abs(diffs[j]) > 3 else ""
                    print(f"  {angle:>5}° {ath_vals[j]:>7.1f} {bem_interp[j]:>7.1f} {diffs[j]:>+7.1f}{flag}")

    out = Path(__file__).parent / "compare_ath_directivity_fullmesh_results.json"
    with open(out, "w") as f:
        json.dump({"sim_freqs": sim_freqs.tolist(), "config": "full_mesh_accurate"}, f, indent=2, default=str)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
