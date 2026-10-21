#!/usr/bin/env python3
"""Freeze artifacts only on the stated WG/mesher reference; see corpus README."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.tests.freeform_oldconfig_support import (  # noqa: E402
    CASES,
    DATA,
    MANIFEST,
    build_twice,
    canonical,
    config_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=[case["name"] for case in CASES])
    parser.add_argument("--artifact", choices=["config", "mesh", "step"], required=True)
    parser.add_argument(
        "--overwrite", action="store_true", help="explicitly replace frozen evidence"
    )
    args = parser.parse_args()
    case = next(case for case in CASES if case["name"] == args.case)
    destination = DATA / "expected" / case["name"] / (args.artifact + ".json")
    if destination.exists() and not args.overwrite:
        parser.error(f"refusing to overwrite {destination.relative_to(ROOT)}; use --overwrite")
    reference = MANIFEST["reference"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # Test/docs-only commits are allowed; never freeze changed production code.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            reference["waveguide_generator"],
            "--",
            "server",
            ":(exclude)server/tests",
            "shared",
            "pins.json",
            "launch",
            "launchers",
        ],
        cwd=ROOT,
        check=True,
    )
    direct = json.loads(
        importlib.metadata.distribution("hornlab-waveguide-mesher").read_text("direct_url.json")
        or "{}"
    )
    if direct.get("vcs_info", {}).get("commit_id") != reference["mesher"]:
        parser.error("freezing requires the pinned reference mesher " + reference["mesher"])
    os.environ["WG2_TEST_MESH_IN_PROCESS"] = "0"
    os.environ["WG2_MESH_BUILD_TIMEOUT_S"] = "45"
    started = time.monotonic()
    if args.artifact == "config":
        result = config_snapshot(case)
    else:
        result = build_twice(case, args.artifact)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(canonical(result))
    summary = result.get("sha256", result.get("error", "canonical config"))
    print(
        f"{args.case} {args.artifact}: {time.monotonic() - started:.3f}s; {head}; {summary}",
        flush=True,
    )


if __name__ == "__main__":
    main()
