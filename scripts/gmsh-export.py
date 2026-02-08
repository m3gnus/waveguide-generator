#!/usr/bin/env python3
"""
Unified Gmsh utility for MWG geometry workflows.

Modes:
  1) Export .geo -> .msh + .stl
     python scripts/gmsh-export.py export <geo_file> [output_dir]

  2) Validate generated GEO files (sanity + optional Gmsh parse/mesh)
     python scripts/gmsh-export.py validate <reference_root> <generated_root>

Legacy compatibility:
  python scripts/gmsh-export.py <geo_file> [output_dir]
"""

from __future__ import annotations

import os
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


def load_gmsh():
    try:
        import gmsh  # type: ignore
    except Exception:
        return None
    return gmsh


def process_geo_file(geo_path: str, output_dir: str | None = None) -> bool:
    """Process a .geo file and generate .msh and .stl outputs."""
    if not os.path.exists(geo_path):
        print(f"Error: File not found: {geo_path}")
        return False

    gmsh = load_gmsh()
    if gmsh is None:
        print("Error: gmsh Python module not found. Install with: pip install gmsh")
        return False

    base_name = os.path.splitext(os.path.basename(geo_path))[0]
    if output_dir is None:
        output_dir = os.path.dirname(geo_path) or "."

    os.makedirs(output_dir, exist_ok=True)
    msh_path = os.path.join(output_dir, f"{base_name}.msh")
    stl_path = os.path.join(output_dir, f"{base_name}.stl")

    gmsh.initialize()
    gmsh.option.setNumber("General.Verbosity", 2)
    gmsh.option.setNumber("Mesh.Algorithm", 2)  # MeshAdapt
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.Binary", 0)  # ASCII output for MSH

    try:
        gmsh.open(geo_path)
        gmsh.model.mesh.generate(2)

        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.write(msh_path)
        print(f"Generated: {msh_path}")

        gmsh.option.setNumber("Mesh.Binary", 1)
        gmsh.write(stl_path)
        print(f"Generated: {stl_path}")

        return True
    except Exception as exc:
        print(f"Error processing {geo_path}: {exc}")
        return False
    finally:
        gmsh.finalize()


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
    gmsh = load_gmsh()
    if gmsh is None:
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


def validate_generated_geo(reference_root: str, generated_root: str) -> int:
    ref_root = Path(reference_root)
    gen_root = Path(generated_root)

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


def print_usage() -> None:
    print(__doc__.strip())


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print_usage()
        return 2

    # Legacy mode: keep old invocation for export path.
    if args[0] not in {"export", "validate"} and not args[0].startswith("-"):
        geo_path = args[0]
        output_dir = args[1] if len(args) > 1 else None
        return 0 if process_geo_file(geo_path, output_dir) else 1

    mode = args[0]
    if mode == "export":
        if len(args) < 2:
            print("Usage: python scripts/gmsh-export.py export <geo_file> [output_dir]")
            return 2
        geo_path = args[1]
        output_dir = args[2] if len(args) > 2 else None
        return 0 if process_geo_file(geo_path, output_dir) else 1

    if mode == "validate":
        if len(args) < 3:
            print("Usage: python scripts/gmsh-export.py validate <reference_root> <generated_root>")
            return 2
        return validate_generated_geo(args[1], args[2])

    print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
