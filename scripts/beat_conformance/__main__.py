"""Run declared cases through an explicitly supplied compute runner."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from .cases import all_cases
from .recorder import run_cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=[case.name for case in all_cases()])
    parser.add_argument("--solve", help="Importable module:function returning EngineRun (real) or SolveEvidence (synthetic)")
    parser.add_argument("--evidence-mode", choices=("synthetic", "real"), default="synthetic")
    parser.add_argument("--backend", choices=("cpu", "metal"), default="cpu")
    parser.add_argument("--analytic-reference", choices=("sphere", "lem-sphere", "all"),
                        help="Opt-in raw CPU analytic references; real mode and broker execution required")
    parser.add_argument("--julia", type=Path, help="Julia executable for analytic references")
    args = parser.parse_args(argv)
    if args.analytic_reference:
        if args.evidence_mode != "real" or args.solve or args.case or args.backend != "cpu":
            parser.error("Analytic references require real mode, CPU, and no --solve/--case")
        from .reference_qualification import run_references
        summary = run_references(output_dir=args.output_dir, selection=args.analytic_reference, julia=args.julia)
        print(f"Analytic references qualified={summary['passed']}; missing={summary['missing_required_references']}")
        return 0 if summary["passed"] else 1
    if args.julia:
        parser.error("--julia is only valid with --analytic-reference")
    cases = [case for case in all_cases()
             if (args.case is None and (case.backend == "cpu" or args.backend == "metal"))
             or (args.case is not None and case.name in args.case)]
    solve = None
    if args.solve:
        module, separator, function = args.solve.partition(":")
        if not separator:
            parser.error("--solve must be module:function")
        # Load inside the callback so absent engine/runtime dependencies leave
        # failed per-case records rather than an unrecorded preflight failure.
        def solve(request):
            return getattr(importlib.import_module(module), function)(request)
    summary = run_cases(cases, output_dir=args.output_dir, solve=solve, evidence_mode=args.evidence_mode, backend=args.backend)
    print(f"{len(summary['records'])} cases; {summary['real_solved_count']} real frequency solves; "
          f"qualified={summary['passed']}")
    if summary["missing_required_cases"]:
        print("Missing required cases: " + ", ".join(summary["missing_required_cases"]))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
