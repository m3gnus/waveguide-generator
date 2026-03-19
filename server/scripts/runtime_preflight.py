#!/usr/bin/env python3
"""CLI entrypoint for backend runtime preflight checks."""

from __future__ import annotations

import argparse
import pathlib
import sys

SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services.runtime_preflight import run_runtime_preflight  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report backend dependency/runtime status for the selected Python interpreter "
            "(fastapi, gmsh, bempp-cl, OpenCL)."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON payload instead of text summary.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any required check is not ready.",
    )
    parser.add_argument(
        "--device-mode",
        default="auto",
        help="Device mode probe for OpenCL availability (default: auto).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_runtime_preflight(
        strict=bool(args.strict),
        json_output=bool(args.json),
        preferred_mode=str(args.device_mode or "auto"),
    )


if __name__ == "__main__":
    raise SystemExit(main())

