#!/usr/bin/env python3
"""CLI bridge for cross-pipeline geometry parity testing.

Reads JSON config on stdin, evaluates Python profile functions at a (t, phi)
grid, and writes JSON results to stdout. Used by tests/geometry-parity.test.js
to compare JS and Python profile outputs without a running server.

Usage:
    echo '{"config": {...}, "t_values": [...], "phi_values": [...]}' | python3 scripts/eval_profiles.py
"""

import json
import math
import sys
from pathlib import Path

# Add project root so we can import from server.solver
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.solver.waveguide_builder import (
    _compute_rosse_profile,
    _compute_osse_profile_arrays,
    _build_osse_callables,
    _compute_guiding_curve_radius,
    _invert_osse_coverage_angle,
    _get_rounded_rect_radius,
    _apply_morph,
    _make_callable,
    _get_float,
    _get_int,
)

import numpy as np


def evaluate_profiles(config, t_values, phi_values):
    """Evaluate profile functions for all (t, phi) pairs.

    Returns list of {t, phi, x, y} dicts with the raw 2D profile values
    (before 3D cylindrical projection).
    """
    formula_type = config.get("formula_type", config.get("type", "OSSE"))
    t_arr = np.array(t_values, dtype=float)
    results = []

    if formula_type == "R-OSSE":
        r0_fn = _make_callable(config.get("r0", 12.7), default=12.7)
        a0_fn = _make_callable(config.get("a0", 15.5), default=15.5)
        k_fn = _make_callable(config.get("k", 2.0), default=2.0)
        r_fn = _make_callable(config.get("r", 0.4), default=0.4)
        m_fn = _make_callable(config.get("m", 0.85), default=0.85)
        q_fn = _make_callable(config.get("q", 3.4), default=3.4)
        R_fn = _make_callable(config["R"])
        a_fn = _make_callable(config["a"])
        b_fn = _make_callable(config.get("b", 0.2), default=0.2)
        tmax_fn = _make_callable(config.get("tmax", 1.0), default=1.0)

        for phi in phi_values:
            x_arr, y_arr, L = _compute_rosse_profile(
                t_arr, R_fn(phi), a_fn(phi),
                r0_fn(phi), a0_fn(phi), k_fn(phi),
                r_fn(phi), b_fn(phi), m_fn(phi), q_fn(phi),
                tmax=tmax_fn(phi),
            )
            for j, t in enumerate(t_values):
                results.append({
                    "t": t, "phi": phi,
                    "x": float(x_arr[j]), "y": float(y_arr[j]),
                })

    elif formula_type == "OSSE":
        callables = _build_osse_callables(config)
        for phi in phi_values:
            x_arr, y_arr, total_len = _compute_osse_profile_arrays(
                t_arr, phi, config, callables=callables
            )
            for j, t in enumerate(t_values):
                results.append({
                    "t": t, "phi": phi,
                    "x": float(x_arr[j]), "y": float(y_arr[j]),
                })
    else:
        raise ValueError(f"Unsupported formula_type: {formula_type}")

    return results


def evaluate_individual_functions(config, t_values, phi_values):
    """Evaluate individual helper functions for unit-level comparison.

    Returns dict with results from each function tested at known inputs.
    """
    results = {}

    # Rounded rectangle radius
    if "rounded_rect" in config:
        rr = config["rounded_rect"]
        results["rounded_rect"] = []
        for phi in rr.get("phi_values", phi_values):
            r = _get_rounded_rect_radius(phi, rr["half_w"], rr["half_h"], rr["corner_r"])
            results["rounded_rect"].append({"phi": phi, "r": r})

    # Guiding curve radius
    if "guiding_curve" in config:
        gc = config["guiding_curve"]
        results["guiding_curve"] = []
        for phi in gc.get("phi_values", phi_values):
            r = _compute_guiding_curve_radius(phi, gc["params"])
            results["guiding_curve"].append({"phi": phi, "r": r})

    # Coverage angle inversion
    if "coverage_inversion" in config:
        ci = config["coverage_inversion"]
        angle = _invert_osse_coverage_angle(
            ci["target_r"], ci["z_main"], ci["r0_main"], ci["a0_deg"],
            ci["k"], ci["s"], ci["n"], ci["q"], ci["L"],
        )
        results["coverage_inversion"] = {"angle_deg": angle}

    # Morph application
    if "morph" in config:
        mp = config["morph"]
        results["morph"] = []
        for case in mp["cases"]:
            r = _apply_morph(
                case["current_r"], case["t"], case["phi"],
                mp["params"], case.get("morph_target_info"),
            )
            results["morph"].append({"t": case["t"], "phi": case["phi"], "r": r})

    return results


def main():
    payload = json.loads(sys.stdin.read())

    mode = payload.get("mode", "profiles")

    if mode == "profiles":
        config = payload["config"]
        t_values = payload["t_values"]
        phi_values = payload["phi_values"]
        results = evaluate_profiles(config, t_values, phi_values)
        json.dump(results, sys.stdout)

    elif mode == "functions":
        results = evaluate_individual_functions(
            payload["config"],
            payload.get("t_values", []),
            payload.get("phi_values", []),
        )
        json.dump(results, sys.stdout)

    else:
        json.dump({"error": f"Unknown mode: {mode}"}, sys.stdout)


if __name__ == "__main__":
    main()
