"""Argument parsing and dispatch for the headless command-line interface."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from server.engines.registry import EngineInfo, EngineRegistry, detect_engines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wg",
        description="Inspect Waveguide Generator designs without starting the server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate a design and its local solve prerequisites",
    )
    validate_parser.add_argument("file", type=Path, help="design .mwg or .cfg file")
    validate_parser.add_argument(
        "--json", action="store_true", help="emit a versioned JSON report"
    )
    validate_parser.add_argument(
        "--no-mesh",
        action="store_true",
        help="validate without compiling the solver mesh",
    )
    validate_parser.add_argument(
        "--engine",
        metavar="NAME",
        help="override the default AUTO engine selection",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    engine_registry: EngineRegistry | None = None,
    engine_detector: Callable[[], list[EngineInfo]] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    # The registry is injectable at this outer boundary because capability
    # probes load optional native stacks and describe the current machine, not
    # the design. Tests and embedders need a deterministic host description,
    # while normal CLI use must retain the exact detector used by the server.
    registry = engine_registry or EngineRegistry(
        detector=engine_detector or detect_engines
    )
    if args.command == "validate":
        from .validate import validate_command

        return validate_command(args, engine_registry=registry)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
