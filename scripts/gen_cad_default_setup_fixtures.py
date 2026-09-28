#!/usr/bin/env python3
"""Regenerate, or verify, the WG default CAD setup parity fixtures.

``shared/solve-defaults.json`` states WG's default solve settings once. The
fixtures under ``server/tests/fixtures/cad_default_setup`` pin the setup the
backend builds from them for a few source inventories; the frontend's parity
test (``frontend/src/shell/cadDefaultSetupParity.test.ts``) must build exactly
the same. After changing a default, run this with ``--write`` and then both
suites: the backend writes the fixtures, and the frontend proves it agrees.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.cadlink.default_setup import default_setup  # noqa: E402
from server.cadlink.setup import setup_content  # noqa: E402


FIXTURES = _ROOT / "server" / "tests" / "fixtures" / "cad_default_setup"
ABOUT = (
    "WG's default CAD setup for these sources and solver selection, as the frontend "
    "records it. The backend's default_setup.py and the frontend's first-time settings "
    "(buildCadProjectSetup on untouched stores) must both produce exactly this. "
    "Regenerate with scripts/gen_cad_default_setup_fixtures.py --write."
)


def _source(source_id: str, role: str, size: float, channel: str, required: bool = True):
    return {
        "id": source_id,
        "role": role,
        "required": required,
        "suggested_resolution_mm": size,
        "default_drive_channel_id": channel,
    }


CASES: dict[str, dict] = {
    "hf-only": {
        "sources": [_source("source-hf", "HF", 4, "drive-hf")],
        "selection": {"engine": "auto", "accuracy": "fast"},
    },
    "two-way-accurate": {
        "sources": [
            _source("source-hf", "HF", 3, "drive-hf"),
            _source("source-lf", "LF", 12, "drive-lf"),
        ],
        "selection": {"engine": "auto", "accuracy": "accurate"},
    },
    "three-way-lf-mf-hf": {
        "sources": [
            _source("source-hf", "HF", 2, "drive-hf"),
            _source("source-mf", "MF", 6, "drive-mf"),
            _source("source-lf", "LF", 14, "drive-lf"),
        ],
        "selection": {"engine": "auto", "accuracy": "fast"},
    },
    "three-way-shared-channel": {
        "sources": [
            _source("source-lf-a", "LF", 15, "drive-lf"),
            _source("source-lf-b", "LF", 15, "drive-lf"),
            _source("source-x", "source", 6, "drive-x", required=False),
            _source("source-hf", "HF", 2.5, "drive-hf"),
        ],
        "selection": {"engine": "bempp", "accuracy": "fast"},
    },
}


def render(case: dict) -> str:
    setup = json.loads(setup_content(default_setup({"sources": case["sources"]}, case["selection"])))
    # The frontend's shape: what it records, without the backend's own fields.
    setup.pop("origin")
    setup.pop("label")
    setup["preparation"].pop("surface_deviation_mm")
    body = {"about": ABOUT, **case, "setup": setup}
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="rewrite the fixtures")
    group.add_argument("--check", action="store_true", help="fail when a fixture is stale")
    args = parser.parse_args(argv)
    stale = []
    for name, case in CASES.items():
        path = FIXTURES / f"{name}.json"
        rendered = render(case)
        if args.write:
            path.write_text(rendered, encoding="utf-8")
        elif not path.exists() or path.read_text(encoding="utf-8") != rendered:
            stale.append(path.name)
    if stale:
        print(
            "Stale default-setup fixtures: " + ", ".join(stale)
            + "; run scripts/gen_cad_default_setup_fixtures.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
