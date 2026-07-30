"""Compare a mirror-reduced Bempp solve with its exact expanded mesh."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))
sys.path.insert(0, str(REPO_ROOT.parent / "hornlab-bempp-bem"))

from solver.cpubem.benchmark import (
    apply_bempp_opencl_workarounds,
    compare_outputs,
    configure_benchmark_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reduced", type=Path, required=True)
    parser.add_argument("--expanded", type=Path, required=True)
    parser.add_argument("--plane", choices=("yz", "xz", "yz+xz"), required=True)
    parser.add_argument("--frequencies", nargs="+", type=float, default=[1000.0])
    parser.add_argument("--precision", choices=("single", "double"), default="single")
    parser.add_argument("--solver", choices=("lu", "gmres"), default="gmres")
    parser.add_argument(
        "--formulation",
        choices=("standard", "complex_k"),
        default="standard",
    )
    parser.add_argument("--gmres-tol", type=float, default=1.0e-5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    configure_benchmark_cache()
    apply_bempp_opencl_workarounds()

    from hornlab_bempp_bem import (
        BIEFormulation,
        LinearSolver,
        ObservationConfig,
        SolveConfig,
        load_mesh,
        solve_frequencies,
    )
    from hornlab_bempp_bem.observation import infer_frame

    expanded_mesh = load_mesh(args.expanded)
    reduced_mesh = load_mesh(
        args.reduced, native_symmetry_plane=args.plane,
    )
    frame = infer_frame(
        expanded_mesh.grid,
        expanded_mesh.physical_tags,
        source_tag=2,
    )
    observation = ObservationConfig(
        planes=["horizontal", "vertical"],
        distance_m=2.0,
        angle_count=74,
    )
    common = dict(
        solver=LinearSolver(args.solver),
        formulation=BIEFormulation(args.formulation),
        gmres_tol=args.gmres_tol,
        precision=args.precision,
        assembly_backend="opencl",
        opencl_device="cpu",
        frame_override=frame,
        observation=observation,
    )
    expanded = solve_frequencies(
        expanded_mesh,
        args.frequencies,
        SolveConfig(**common),
    )
    reduced = solve_frequencies(
        reduced_mesh,
        args.frequencies,
        SolveConfig(native_symmetry_plane=args.plane, **common),
    )

    metrics = compare_outputs(
        {
            "pressure": np.asarray(expanded.pressure_complex),
            "impedance": np.asarray(expanded.impedance),
        },
        {
            "pressure": np.asarray(reduced.pressure_complex),
            "impedance": np.asarray(reduced.impedance),
        },
    )
    result = {
        "reduced_mesh": str(args.reduced.resolve()),
        "expanded_mesh": str(args.expanded.resolve()),
        "symmetry_plane": args.plane,
        "frequencies_hz": args.frequencies,
        "precision": args.precision,
        "gmres_tol": args.gmres_tol,
        "solver": args.solver,
        "formulation": args.formulation,
        "metrics": metrics,
        "expanded_timings": expanded.timings,
        "reduced_timings": reduced.timings,
        "expanded_solver_log": expanded.solver_log,
        "reduced_solver_log": reduced.solver_log,
    }
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
