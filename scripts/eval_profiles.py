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

# Add project root and sibling dependency clone so local package installs and
# server-managed environments both work.
REPO_ROOT = Path(__file__).resolve().parent.parent
MESHER_SOURCE = REPO_ROOT.parent / "hornlab-waveguide-mesher"
sys.path.insert(0, str(REPO_ROOT))
if MESHER_SOURCE.exists():
    sys.path.insert(0, str(MESHER_SOURCE))

from hornlab_mesher.profile_common import eval_param
from hornlab_mesher.profile_formulas import calculate_osse, calculate_rosse, osse_total_length
from hornlab_mesher.profile_morph import (
    _apply_morphing,
    _guiding_curve_target_radius,
    _invert_osse_coverage_angle,
    _rounded_rect_radius,
)
from hornlab_mesher.profile_sampling import build_point_grid

import numpy as np


ALIASES = {
    "formula_type": "type",
    "throat_ext_length": "throatExtLength",
    "throat_ext_angle": "throatExtAngle",
    "slot_length": "slotLength",
    "gcurve_type": "gcurveType",
    "gcurve_width": "gcurveWidth",
    "gcurve_aspect_ratio": "gcurveAspectRatio",
    "gcurve_dist": "gcurveDist",
    "gcurve_rot": "gcurveRot",
    "gcurve_sf": "gcurveSf",
    "gcurve_se_n": "gcurveSeN",
    "gcurve_sf_a": "gcurveSfA",
    "gcurve_sf_b": "gcurveSfB",
    "gcurve_sf_m1": "gcurveSfM1",
    "gcurve_sf_m2": "gcurveSfM2",
    "gcurve_sf_n1": "gcurveSfN1",
    "gcurve_sf_n2": "gcurveSfN2",
    "gcurve_sf_n3": "gcurveSfN3",
    "morph_target": "morphTarget",
    "morph_width": "morphWidth",
    "morph_height": "morphHeight",
    "morph_corner": "morphCorner",
    "morph_rate": "morphRate",
    "morph_fixed": "morphFixed",
    "morph_allow_shrinkage": "morphAllowShrinkage",
}


def normalise_params(config):
    out = dict(config or {})
    for old, new in ALIASES.items():
        if old in out and new not in out:
            out[new] = out[old]
    return out


def evaluate_profiles(config, t_values, phi_values):
    """Evaluate profile functions for all (t, phi) pairs.

    Returns list of {t, phi, x, y} dicts with the raw 2D profile values
    (before 3D cylindrical projection).
    """
    config = normalise_params(config)
    formula_type = config.get("type", "OSSE")
    results = []

    if formula_type == "R-OSSE":
        for phi in phi_values:
            tmax = float(eval_param(config.get("tmax"), phi, 1.0))
            for t in t_values:
                x, y = calculate_rosse(float(t) * tmax, phi, config)
                results.append({
                    "t": t, "phi": phi,
                    "x": float(x), "y": float(y),
                })

    elif formula_type == "OSSE":
        for phi in phi_values:
            total_len = osse_total_length(config, phi)
            h = eval_param(config.get("h"), phi, 0.0)
            for t in t_values:
                x, y = calculate_osse(float(t) * total_len, phi, config)
                if h:
                    y += h * math.sin(float(t) * math.pi)
                results.append({
                    "t": t, "phi": phi,
                    "x": float(x), "y": float(y),
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
            r = _rounded_rect_radius(phi, rr["half_w"], rr["half_h"], rr["corner_r"])
            results["rounded_rect"].append({"phi": phi, "r": r})

    # Guiding curve radius
    if "guiding_curve" in config:
        gc = config["guiding_curve"]
        results["guiding_curve"] = []
        for phi in gc.get("phi_values", phi_values):
            r = _guiding_curve_target_radius(phi, normalise_params(gc["params"]))
            results["guiding_curve"].append({"phi": phi, "r": r})

    # Coverage angle inversion
    if "coverage_inversion" in config:
        ci = config["coverage_inversion"]
        params = normalise_params(ci.get("params", {}))
        params.update({key: ci[key] for key in ("k", "s", "n", "q", "L") if key in ci})
        angle = _invert_osse_coverage_angle(
            ci["target_r"],
            ci["z_main"],
            0.0,
            params,
            a0_deg=ci["a0_deg"],
            r0_main=ci["r0_main"],
        )
        results["coverage_inversion"] = {"angle_deg": angle}

    # Morph application
    if "morph" in config:
        mp = config["morph"]
        results["morph"] = []
        for case in mp["cases"]:
            r = _apply_morphing(
                case["current_r"], case.get("mouth_radius", case["current_r"]),
                case["t"], case["phi"],
                normalise_params(mp["params"]),
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

    elif mode == "point_grid":
        results = build_point_grid(normalise_params(payload["config"]))
        json.dump(results, sys.stdout)

    else:
        json.dump({"error": f"Unknown mode: {mode}"}, sys.stdout)


if __name__ == "__main__":
    main()
