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
    validate_parser.add_argument(
        "file",
        type=Path,
        nargs="?",
        help="design .mwg or .cfg file",
    )
    validate_parser.add_argument(
        "--request",
        type=Path,
        metavar="FILE",
        help="validate a canonical JSON SolveRequest instead of a text design",
    )
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
    validate_parser.add_argument(
        "--overlay",
        type=Path,
        metavar="FILE",
        help="apply a versioned JSON solve-options overlay",
    )

    solve_parser = subparsers.add_parser(
        "solve",
        help="solve a design through the persistent job runtime",
    )
    solve_parser.add_argument(
        "design",
        type=Path,
        nargs="?",
        help="design .mwg or .cfg file",
    )
    solve_parser.add_argument(
        "--request",
        type=Path,
        metavar="FILE",
        help="solve a canonical JSON SolveRequest instead of a text design",
    )
    event_group = solve_parser.add_mutually_exclusive_group()
    event_group.add_argument(
        "--json-events",
        action="store_true",
        help="stream the jobs protocol as newline-delimited JSON",
    )
    event_group.add_argument(
        "--events",
        choices=("ndjson", "text"),
        default="text",
        help="event stream format (default: text)",
    )
    solve_parser.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="write a durable, caller-owned artifact bundle to a new directory",
    )
    solve_parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help="override the application data directory",
    )
    solve_parser.add_argument(
        "--engine",
        metavar="NAME",
        help="override file and overlay engine selection",
    )
    solve_parser.add_argument(
        "--overlay",
        type=Path,
        metavar="FILE",
        help="apply a versioned JSON solve-options overlay",
    )

    package_parser = subparsers.add_parser(
        "export-package",
        help="export a solved job's retained traces as a re-simulatable package",
    )
    package_parser.add_argument(
        "job",
        nargs="?",
        help="job id of a completed solve that retained field traces",
    )
    package_parser.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="write the package to a new .zip path",
    )
    package_parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help="override the application data directory",
    )
    package_parser.add_argument(
        "--verify",
        type=Path,
        metavar="PATH",
        help="verify an existing package's manifest and member digests instead",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    engine_registry: EngineRegistry | None = None,
    engine_detector: Callable[[], list[EngineInfo]] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate" and (args.file is None) == (args.request is None):
        parser.error("validate requires exactly one design file or --request FILE")
    if args.command == "solve" and (args.design is None) == (args.request is None):
        parser.error("solve requires exactly one design file or --request FILE")

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
    if args.command == "solve":
        from .solve import solve_command

        return solve_command(args, engine_registry=registry)
    if args.command == "export-package":
        from .export_package import export_package_command

        return export_package_command(args)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
