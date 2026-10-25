#!/usr/bin/env python3
"""Vendor the pinned mesher's pure sizing model into a WGLink checkout.

    python scripts/vendor_wglink_mesh_sizing.py <mesher checkout> <WGLink checkout>

The revision and repository come from WG's pins.json; never from mesher HEAD.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.vendor_source import vendor_source  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mesher', type=Path)
    parser.add_argument('wglink', type=Path)
    args = parser.parse_args()
    pin = json.loads((ROOT / 'pins.json').read_text())['modules']['hornlab-waveguide-mesher']
    target = args.wglink / 'fusion-addins/WGLink'
    manifest = vendor_source(args.mesher, pin['sha'], 'hornlab_mesher/mesh_sizing.py',
                            target / 'wglink_mesh_sizing.py', target / 'wglink_mesh_sizing.source.json',
                            repository=pin['repo'])
    print(f"Vendored mesh sizing at {manifest['commit']} ({manifest['sha256']}).")


if __name__ == '__main__':
    main()
