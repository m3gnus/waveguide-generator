"""Export or verify a portable radiation package for one solved job.

Reading a stored job is a pure read: it takes no runtime ownership lock, because
it never schedules, recovers, or mutates anything. The command therefore works
while the GUI server is running on the same data directory, which is the case
that matters -- the user has just watched the run finish.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, TextIO

from server.exports.radiation_package import (
    RadiationPackageError,
    RadiationPackageIssue,
    build_radiation_package,
    validate_radiation_package,
)
from server.jobs.store import JobStore
from server.platform.paths import data_paths


def _print_issues(
    issues: Sequence[RadiationPackageIssue], *, prefix: str, stream: TextIO
) -> None:
    for issue in issues:
        print(f"{prefix}: [{issue.code}] {issue.message}", file=stream)


def _verify(path: Path, *, stdout: TextIO, stderr: TextIO) -> int:
    result = validate_radiation_package(path)
    if not result.ok:
        _print_issues(result.issues, prefix="Package invalid", stream=stderr)
        return 1
    manifest = result.manifest or {}
    members = len(manifest.get("files") or {})
    print(
        f"Package {path}: valid, schema {manifest.get('schema')} "
        f"version {manifest.get('version')}, {members} verified members",
        file=stdout,
        flush=True,
    )
    return 0


def export_package_path(
    args: argparse.Namespace,
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if args.verify is not None:
        if args.job is not None or args.output is not None:
            print(
                "Export refused: --verify takes a package path on its own",
                file=stderr,
            )
            return 1
        return _verify(args.verify, stdout=stdout, stderr=stderr)

    if args.job is None or args.output is None:
        print(
            "Export refused: a job id and --output PATH are both required",
            file=stderr,
        )
        return 1
    if args.output.suffix.lower() != ".zip":
        print(
            f"Export refused: --output must name a .zip path: {args.output}",
            file=stderr,
        )
        return 1

    paths = data_paths(args.data_dir)
    database = paths.db / "simulations.db"
    if not database.is_file():
        print(
            f"Export refused: [job_not_found] no job database in {paths.root}",
            file=stderr,
        )
        return 1

    store = JobStore.for_data_dir(paths.root)
    try:
        result = build_radiation_package(store, args.job, args.output)
    except RadiationPackageError as exc:
        print(f"Could not write the radiation package: {exc}", file=stderr)
        return 1
    except OSError as exc:
        print(f"Could not write the radiation package: {exc}", file=stderr)
        return 1
    finally:
        store.close()

    if not result.ok:
        _print_issues(result.issues, prefix="Export refused", stream=stderr)
        return 1
    manifest = result.manifest or {}
    run_number = (
        f" run #{manifest['run_number']}" if manifest.get("run_number") is not None else ""
    )
    channels = (manifest.get("channels") or {}).get("ids") or []
    frequencies = (manifest.get("frequencies") or {}).get("hz") or []
    print(
        f"Radiation package for job {args.job}{run_number}: {result.path} "
        f"({result.bytes} bytes, {len(frequencies)} frequencies, "
        f"{len(channels)} raw channels)",
        file=stdout,
        flush=True,
    )
    return 0


def export_package_command(args: argparse.Namespace) -> int:
    return export_package_path(args, stdout=sys.stdout, stderr=sys.stderr)


__all__ = ["export_package_command", "export_package_path"]
