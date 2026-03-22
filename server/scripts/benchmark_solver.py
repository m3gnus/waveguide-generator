"""
Repeatable BEM solver benchmark tool.

Usage (run from server/ directory):
    python scripts/benchmark_solver.py <mesh.msh> [options]
    python scripts/benchmark_solver.py --preset reference-horn [options]

Options:
    --preset NAME       Repro preset (currently: reference-horn)
    --freq-min FLOAT    Minimum frequency in Hz (preset defaults to 1000)
    --freq-max FLOAT    Maximum frequency in Hz (preset defaults to 1000)
    --num-freq INT      Number of frequencies (preset defaults to 1)
    --device MODE       Device mode: auto|opencl_gpu|opencl_cpu (default: auto)
    --spacing MODE      Frequency spacing: log|linear (preset defaults to linear)
    --precision-modes   Comma-separated precision list (single,double)
    --no-warmup         Skip the warm-up pass (to measure first-solve penalty)
    --json              Emit machine-readable JSON report

Output:
    Human-readable timing breakdown + precision support matrix.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add server/ to path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_BENCHMARK_PLAN = {
    "freq_min": 500.0,
    "freq_max": 8000.0,
    "num_freq": 10,
    "spacing": "log",
}
REFERENCE_HORN_PLAN = {
    "freq_min": 1000.0,
    "freq_max": 1000.0,
    "num_freq": 1,
    "spacing": "linear",
}
VALID_PRECISION_MODES = ("single", "double")
REFERENCE_HORN_OCC_PAYLOAD = {
    "formula_type": "R-OSSE",
    "R": "140",
    "a": "25",
    "a0": 15.5,
    "r0": 12.7,
    "k": 2.0,
    "q": 3.4,
    "r": 0.4,
    "b": 0.2,
    "m": 0.85,
    "tmax": 1.0,
    "quadrants": 1234,
    "enc_depth": 0,
    "wall_thickness": 6.0,
    "n_angular": 100,
    "n_length": 20,
    "throat_res": 6.0,
    "mouth_res": 15.0,
    "rear_res": 40.0,
}


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


def parse_precision_modes(raw: str) -> Tuple[List[str], List[str]]:
    tokens = [str(token).strip().lower() for token in str(raw or "").split(",")]
    modes: List[str] = []
    invalid: List[str] = []
    for token in tokens:
        if not token:
            continue
        if token in VALID_PRECISION_MODES:
            if token not in modes:
                modes.append(token)
            continue
        invalid.append(token)
    if not modes:
        modes = ["single"]
    return modes, invalid


def resolve_frequency_plan(args: argparse.Namespace) -> Dict[str, Any]:
    if args.preset == "reference-horn":
        plan = dict(REFERENCE_HORN_PLAN)
    else:
        plan = dict(DEFAULT_BENCHMARK_PLAN)

    if args.freq_min is not None:
        plan["freq_min"] = float(args.freq_min)
    if args.freq_max is not None:
        plan["freq_max"] = float(args.freq_max)
    if args.num_freq is not None:
        plan["num_freq"] = int(args.num_freq)
    if args.spacing is not None:
        plan["spacing"] = str(args.spacing).strip().lower()
    return plan


def classify_precision_outcome(run_error: Optional[str], results: Optional[Dict[str, Any]]) -> str:
    if run_error:
        return "unsupported"
    if not isinstance(results, dict):
        return "unsupported"

    metadata = results.get("metadata")
    if not isinstance(metadata, dict):
        return "unsupported"
    failure_count = int(metadata.get("failure_count", 0) or 0)
    frequencies = results.get("frequencies") if isinstance(results.get("frequencies"), list) else []
    if frequencies and failure_count >= len(frequencies):
        return "unsupported"
    return "supported"


def build_reference_horn_occ_mesh() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from contracts import WaveguideParamsRequest
    from solver.waveguide_builder import build_waveguide_mesh

    request = WaveguideParamsRequest(**REFERENCE_HORN_OCC_PAYLOAD)
    started = time.time()
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    prep_time = time.time() - started
    canonical = result.get("canonical_mesh", {}) if isinstance(result, dict) else {}
    stats = result.get("stats", {}) if isinstance(result, dict) else {}
    msh_text = result.get("msh_text")
    if not isinstance(msh_text, str) or not msh_text.strip():
        raise RuntimeError("Reference horn OCC build returned empty msh_text.")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".msh", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(msh_text)
            tmp_path = tmp.name
        mesh = load_mesh(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    mesh_report = {
        "source": "occ_reference_horn_preset",
        "prep_success": True,
        "prep_time_seconds": prep_time,
        "stats": stats if isinstance(stats, dict) else {},
        "canonical_mesh": {
            "vertices": len(canonical.get("vertices") or []) // 3,
            "triangles": len(canonical.get("indices") or []) // 3,
            "surface_tags": len(canonical.get("surfaceTags") or []),
        },
    }
    return mesh, mesh_report


def run_benchmark(args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    from solver.solve import solve_optimized
    from solver.device_interface import selected_device_metadata

    freq_plan = resolve_frequency_plan(args)
    precision_modes, invalid_modes = parse_precision_modes(args.precision_modes)
    if invalid_modes:
        print(
            f"WARNING: Ignoring unsupported precision modes: {', '.join(invalid_modes)}",
            file=sys.stderr,
        )

    report: Dict[str, Any] = {
        "preset": args.preset or "custom",
        "mesh": {},
        "frequency_plan": freq_plan,
        "runtime": {"requested_mode": args.device},
        "precision_runs": [],
    }

    prep_started = time.time()
    try:
        if args.preset == "reference-horn":
            mesh, mesh_report = build_reference_horn_occ_mesh()
            report["mesh"] = mesh_report
        else:
            mesh = load_mesh(args.mesh)
            report["mesh"] = {
                "source": str(args.mesh),
                "prep_success": True,
                "prep_time_seconds": time.time() - prep_started,
            }
    except Exception as exc:
        report["mesh"] = {
            "source": "occ_reference_horn_preset" if args.preset == "reference-horn" else str(args.mesh),
            "prep_success": False,
            "prep_time_seconds": time.time() - prep_started,
            "error": str(exc),
        }
        return 1, report

    n_elements = (
        mesh["grid"].number_of_elements
        if hasattr(mesh["grid"], "number_of_elements")
        else "?"
    )
    dev_meta = selected_device_metadata(args.device) or {}
    report["runtime"]["selected"] = {
        "selected_mode": dev_meta.get("selected_mode"),
        "device_name": dev_meta.get("device_name"),
        "interface": dev_meta.get("interface"),
        "fallback_reason": dev_meta.get("fallback_reason"),
    }

    for mode in precision_modes:
        run_started = time.time()
        run_error = None
        run_results = None
        try:
            run_results = solve_optimized(
                mesh=mesh,
                frequency_range=[freq_plan["freq_min"], freq_plan["freq_max"]],
                num_frequencies=freq_plan["num_freq"],
                sim_type="2",
                verbose=True,
                mesh_validation_mode="warn",
                frequency_spacing=freq_plan["spacing"],
                device_mode=args.device,
                enable_warmup=(not args.no_warmup),
                bem_precision=mode,
            )
        except Exception as exc:
            run_error = str(exc)

        outcome = classify_precision_outcome(run_error, run_results)
        metadata = run_results.get("metadata", {}) if isinstance(run_results, dict) else {}
        perf = metadata.get("performance", {}) if isinstance(metadata, dict) else {}
        report["precision_runs"].append(
            {
                "precision": mode,
                "status": outcome,
                "wall_time_seconds": time.time() - run_started,
                "error": run_error,
                "timings": {
                    "warmup_time_seconds": perf.get("warmup_time_seconds"),
                    "frequency_solve_time": perf.get("frequency_solve_time"),
                    "time_per_frequency": perf.get("time_per_frequency"),
                    "directivity_compute_time": perf.get("directivity_compute_time"),
                    "total_time_seconds": perf.get("total_time_seconds"),
                },
                "failure_count": metadata.get("failure_count") if isinstance(metadata, dict) else None,
                "warning_count": metadata.get("warning_count") if isinstance(metadata, dict) else None,
                "runtime_device": (
                    metadata.get("device_interface") if isinstance(metadata, dict) else None
                ),
            }
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print()
        print("=" * 72)
        print("BENCHMARK RESULTS")
        print("=" * 72)
        print(f"  Mesh source:              {report['mesh'].get('source')}")
        print(f"  Mesh prep success:        {report['mesh'].get('prep_success')}")
        print(f"  Mesh prep time:           {report['mesh'].get('prep_time_seconds', 0):.2f}s")
        if report["mesh"].get("error"):
            print(f"  Mesh prep error:          {report['mesh']['error']}")
        if report["mesh"].get("canonical_mesh"):
            canonical = report["mesh"]["canonical_mesh"]
            print(
                "  Canonical mesh:           "
                f"{canonical.get('vertices', '?')} vertices, {canonical.get('triangles', '?')} triangles"
            )
        print(f"  Grid elements:            {n_elements}")
        print(
            "  Frequency plan:           "
            f"{freq_plan['freq_min']}–{freq_plan['freq_max']} Hz, "
            f"{freq_plan['num_freq']} points ({freq_plan['spacing']})"
        )
        print(f"  Requested device mode:    {args.device}")
        print(
            "  Selected runtime/device:  "
            f"{dev_meta.get('selected_mode', '?')} / {dev_meta.get('device_name', '?')}"
        )
        if dev_meta.get("fallback_reason"):
            print(f"  Fallback reason:          {dev_meta['fallback_reason']}")
        print()
        print("  Precision support matrix:")
        for row in report["precision_runs"]:
            print(
                f"    - {row['precision']}: {row['status']} "
                f"(wall={row['wall_time_seconds']:.2f}s, "
                f"solve={row['timings'].get('frequency_solve_time')}, "
                f"directivity={row['timings'].get('directivity_compute_time')})"
            )
            if row.get("error"):
                print(f"      error: {row['error']}")
        print("=" * 72)

    has_supported_mode = any(row.get("status") == "supported" for row in report["precision_runs"])
    return (0 if has_supported_mode else 2), report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BEM solver benchmark", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("mesh", nargs="?", help="Path to .msh mesh file")
    parser.add_argument(
        "--preset",
        choices=["reference-horn"],
        default=None,
        help="Use a bounded, reproducible benchmark preset",
    )
    parser.add_argument("--freq-min", type=float, default=None, help="Min frequency (Hz)")
    parser.add_argument("--freq-max", type=float, default=None, help="Max frequency (Hz)")
    parser.add_argument("--num-freq", type=int, default=None, help="Number of frequencies")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "opencl_gpu", "opencl_cpu"],
        help="Device mode",
    )
    parser.add_argument(
        "--spacing",
        default=None,
        choices=["log", "linear"],
        help="Frequency spacing",
    )
    parser.add_argument(
        "--precision-modes",
        default=None,
        help="Comma-separated precision modes to test (single,double)",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        default=False,
        help="Skip the warm-up pass (measures first-solve penalty for A/B comparison)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print machine-readable benchmark report JSON",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.preset is None and not args.mesh:
        raise ValueError("Provide <mesh.msh> or use --preset reference-horn.")
    if args.preset is not None and args.mesh:
        raise ValueError("Do not pass <mesh.msh> together with --preset.")
    if args.precision_modes is None:
        args.precision_modes = "single,double" if args.preset == "reference-horn" else "single"


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    exit_code, _report = run_benchmark(args)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
