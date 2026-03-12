"""
Repeatable symmetry-policy benchmark harness.

Usage (run from server/ directory):
    python scripts/benchmark_symmetry.py [options]

Options:
    --case NAME        Run only the named case (repeatable)
    --iterations INT   Re-run each case this many times (default: 25)
    --json             Print machine-readable JSON instead of a text table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from solver.symmetry_benchmark import benchmark_symmetry_cases


def _print_text(payload: dict) -> None:
    print("SYMMETRY BENCHMARK")
    print("=" * 72)
    print(f"Iterations per case: {payload['iterations']}")
    print(f"All expected policies matched: {'yes' if payload['all_passed'] else 'no'}")
    print()

    for case in payload["cases"]:
        policy = case["policy"]
        timing = case["timing"]
        mesh = case["mesh"]
        print(f"{case['name']}: {case['description']}")
        print(
            "  policy=%s reason=%s type=%s reduction=%.1fx applied=%s"
            % (
                policy["decision"],
                policy["reason"],
                policy["detected_symmetry_type"],
                float(policy["reduction_factor"]),
                "yes" if policy["applied"] else "no",
            )
        )
        print(
            "  mesh=%d verts / %d tris -> %d reduced tris"
            % (mesh["vertices"], mesh["triangles"], mesh["reduced_triangles"])
        )
        print(
            "  timing avg=%.3fms total=%.3fms"
            % (float(timing["avg_ms"]), float(timing["total_ms"]))
        )
        if policy.get("throat_center") is not None:
            print(f"  throat_center={policy['throat_center']}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Symmetry benchmark harness",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--case",
        dest="case_names",
        action="append",
        default=[],
        help="Name of a benchmark case to run (repeatable).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=25,
        help="How many times to rerun each case.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print JSON instead of the text summary.",
    )
    args = parser.parse_args()

    payload = benchmark_symmetry_cases(
        case_names=args.case_names or None,
        iterations=args.iterations,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(payload)


if __name__ == "__main__":
    main()
