#!/usr/bin/env python3
"""Vendor WGLink's shared protocol: its exact bytes and their source identity, together.

    python scripts/vendor_wglink_protocol.py <WGLink checkout> [--commit <rev>]

Copies ``fusion-addins/WGLink/wglink_protocol.py`` as committed at ``<rev>``
(default: the checkout's HEAD) to ``server/cadlink/wglink_protocol.py`` and
records the commit, path and SHA-256 in ``wglink_protocol.source.json``. It
reads the committed file, never the working tree, so the record always names
bytes the commit really has. ``server/tests/test_wglink_shared_protocol.py``
checks the pair against the recorded commit (``WGLINK_SOURCE_GIT`` points it at
a local clone before that commit is pushed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.platform.json_file import write_json_atomic  # noqa: E402

SOURCE_PATH = "fusion-addins/WGLink/wglink_protocol.py"
VENDORED = ROOT / "server/cadlink/wglink_protocol.py"
MANIFEST = ROOT / "server/cadlink/wglink_protocol.source.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_tree", type=Path, help="a WGLink checkout")
    parser.add_argument("--commit", default="HEAD", help="the WGLink revision to vendor (default HEAD)")
    parser.add_argument("--note", help="the manifest's note (default: names the commit)")
    args = parser.parse_args(argv)
    source = args.source_tree.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "--verify", f"{args.commit}^{{commit}}"], text=True,
    ).strip()
    data = subprocess.check_output(["git", "-C", str(source), "show", f"{commit}:{SOURCE_PATH}"])
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update(commit=commit, path=SOURCE_PATH, sha256=hashlib.sha256(data).hexdigest())
    manifest["note"] = args.note or f"Vendored byte for byte from WGLink {commit[:7]}."
    VENDORED.write_bytes(data)
    write_json_atomic(MANIFEST, manifest)
    print(f"Vendored {SOURCE_PATH} at {commit} ({manifest['sha256']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
