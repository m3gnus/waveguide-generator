#!/usr/bin/env python3
"""Render or verify the release snapshot of WG's OpenAPI document."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.app import create_app  # noqa: E402
from server.platform.paths import app_root  # noqa: E402


REPO_ROOT = app_root()
OUTPUT = REPO_ROOT / "docs" / "reference" / "openapi.v1.json"


def render() -> str:
    schema = create_app(data_dir=REPO_ROOT / ".openapi-contract-data").openapi()
    return json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="update the snapshot")
    group.add_argument("--check", action="store_true", help="fail when it has drifted")
    args = parser.parse_args(argv)
    rendered = render()
    if args.write:
        OUTPUT.write_text(rendered, encoding="utf-8")
        return 0
    if args.check:
        try:
            current = OUTPUT.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"OpenAPI snapshot is unavailable: {exc}", file=sys.stderr)
            return 1
        if current != rendered:
            print("OpenAPI snapshot has drifted; run scripts/gen_openapi.py --write", file=sys.stderr)
            return 1
        return 0
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
