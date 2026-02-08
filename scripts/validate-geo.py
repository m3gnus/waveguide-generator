#!/usr/bin/env python3
"""
Compatibility wrapper for GEO validation.

Validation has been merged into scripts/gmsh-export.py:
  python3 scripts/gmsh-export.py validate <reference_root> <generated_root>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script_path = Path(__file__).with_name("gmsh-export.py")
    cmd = [sys.executable, str(script_path), "validate", *sys.argv[1:]]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
