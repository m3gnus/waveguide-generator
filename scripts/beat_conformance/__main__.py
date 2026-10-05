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
    parser.add_argument("--solve", help="Importable module:function returning SolveEvidence")
    parser.add_argument("--evidence-mode", choices=("synthetic", "real"), default="synthetic")
    args = parser.parse_args(argv)
    cases = [case for case in all_cases() if args.case is None or case.name in args.case]
    solve = None
    if args.solve:
        module, separator, function = args.solve.partition(":")
        if not separator:
            parser.error("--solve must be module:function")
        # Load inside the callback so absent engine/runtime dependencies leave
        # failed per-case records rather than an unrecorded preflight failure.
        def solve(request):
            return getattr(importlib.import_module(module), function)(request)
    summary = run_cases(cases, output_dir=args.output_dir, solve=solve, evidence_mode=args.evidence_mode)
    print(f"{len(summary['records'])} cases; {summary['real_solved_count']} real frequency solves; "
          f"qualified={summary['passed']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
