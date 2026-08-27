"""Measure triangle quality across real meshes, so the threshold is not invented.

Nothing in this pipeline gated element quality. There is no aspect-ratio,
minimum-angle, skewness or SICN check in ``server``, ``hornlab_mesher`` or
``hornlab_metal_bem``, and gmsh is never asked to optimise or report it. Only
outright degeneracy is caught, at ``areas <= 1e-15``, so a sliver with finite
area passed every check -- and BEM conditioning is sensitive to exactly that.

Adding a gate is easy; adding one at a defensible threshold is not. This walks a
directory of ``.msh`` files, reports the distribution of
``mesh_element_quality_report`` over them, and shows how many meshes each
candidate threshold would flag. Run it before moving
``POOR_TRIANGLE_RADIUS_RATIO``.

    python scripts/measure_mesh_element_quality.py <directory> [...]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import meshio
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.mesh.integrity import mesh_element_quality_report  # noqa: E402


def read_triangles(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        mesh = meshio.read(path)
    except Exception:  # noqa: BLE001 - a library of archived files, not inputs
        return None
    blocks = [cells.data for cells in mesh.cells if cells.type == "triangle"]
    if not blocks:
        return None
    return np.asarray(mesh.points, dtype=float), np.concatenate(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="stop after N meshes")
    args = parser.parse_args(argv)

    paths = sorted(
        {path for root in args.roots for path in root.rglob("*.msh")}
    )
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("No .msh files found.")
        return 1

    rows: list[tuple[str, dict[str, object]]] = []
    for path in paths:
        found = read_triangles(path)
        if found is None:
            continue
        report = mesh_element_quality_report(*found)
        if report["measured_triangle_count"]:
            rows.append((path.name, report))

    if not rows:
        print("No readable triangle meshes.")
        return 1

    print(f"{'mesh':<44}{'tris':>9}{'min gamma':>12}{'mean':>8}{'min angle':>11}")
    for name, report in rows:
        print(
            f"{name[:43]:<44}{report['measured_triangle_count']:>9}"
            f"{report['min_radius_ratio']:>12.5f}{report['mean_radius_ratio']:>8.3f}"
            f"{report['min_angle_deg']:>11.3f}"
        )

    worst = np.array([float(report["min_radius_ratio"]) for _name, report in rows])
    angles = np.array([float(report["min_angle_deg"]) for _name, report in rows])
    print(f"\n{len(rows)} meshes.")
    print(
        f"worst gamma: min {worst.min():.5f}  median {np.median(worst):.5f}  "
        f"max {worst.max():.5f}"
    )
    print(
        f"worst angle: min {angles.min():.3f}  median {np.median(angles):.3f}  "
        f"max {angles.max():.3f}"
    )
    print("\nMeshes flagged at each candidate gamma threshold:")
    for threshold in (0.5, 0.3, 0.2, 0.1, 0.05, 0.01):
        flagged = int(np.count_nonzero(worst < threshold))
        print(f"  gamma < {threshold:<6}{flagged:>4} of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
