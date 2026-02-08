#!/usr/bin/env python3
"""
Validate generated GEO files for deterministic parse/mesh viability.

Usage:
  python3 scripts/validate-geo.py <reference_root> <generated_root>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPRESENTATIVE = [
    "0414je3",
    "250729solanaS2",
    "0416ro1",
    "260112aolo1",
]

POINT_RE = re.compile(r"^Point\((\d+)\)=\{([^}]+)\};$")
SAVE_RE = re.compile(r'^Save\s+"([^"]+)";')


def parse_geo_sanity(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    points = 0
    has_save = False
    for raw in text.splitlines():
        line = raw.strip()
        if POINT_RE.match(line):
            points += 1
        if SAVE_RE.match(line):
            has_save = True
    if points == 0:
        return False, "no Point() entries"
    if not has_save:
        return False, "missing Save \"*.msh\" directive"
    return True, f"points={points}"


def gmsh_validate(path: Path) -> tuple[bool, str]:
    try:
        import gmsh  # type: ignore
    except Exception:
        return True, "gmsh module unavailable (sanity-only mode)"

    try:
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(path))
        gmsh.model.mesh.generate(2)
        return True, "gmsh mesh generation ok"
    except Exception as exc:  # pragma: no cover - external runtime
        return False, f"gmsh failed: {exc}"
    finally:
        try:
            gmsh.finalize()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/validate-geo.py <reference_root> <generated_root>")
        return 2

    ref_root = Path(sys.argv[1])
    gen_root = Path(sys.argv[2])

    selected = []
    for name in REPRESENTATIVE:
        if (ref_root / f"{name}.txt").exists():
            selected.append(name)

    if not selected:
        selected = sorted(p.stem for p in ref_root.glob("*.txt"))

    failures = 0
    for name in selected:
        geo_path = gen_root / name / "mesh.geo"
        if not geo_path.exists():
            print(f"{name}: FAIL missing generated GEO at {geo_path}")
            failures += 1
            continue

        ok, msg = parse_geo_sanity(geo_path)
        if not ok:
            print(f"{name}: FAIL {msg}")
            failures += 1
            continue

        ok, gm_msg = gmsh_validate(geo_path)
        if not ok:
            print(f"{name}: FAIL {gm_msg}")
            failures += 1
            continue

        print(f"{name}: OK ({msg}; {gm_msg})")

    if failures:
        print(f"\nGEO validation failures: {failures}")
        return 1

    print("\nGEO validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
