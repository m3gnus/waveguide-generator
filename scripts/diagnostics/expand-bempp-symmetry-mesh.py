"""Expand a half/quarter Gmsh surface mesh for Bempp parity diagnostics."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import meshio
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT.parent / "hornlab-bempp-bem"))

from hornlab_bempp_bem.symmetry import expand_symmetry_mesh


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--plane", choices=("yz", "xz", "yz+xz"), required=True)
    args = parser.parse_args()

    source = meshio.read(args.input)
    triangles = np.asarray(source.get_cells_type("triangle"), dtype=np.int64)
    tags = np.asarray(
        source.get_cell_data("gmsh:physical", "triangle"), dtype=np.int32,
    )
    expanded = expand_symmetry_mesh(
        np.asarray(source.points, dtype=np.float64),
        triangles,
        tags,
        args.plane,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    meshio.write(
        args.output,
        meshio.Mesh(
            points=expanded.vertices_nx3,
            cells=[("triangle", expanded.triangles_nx3)],
            cell_data={
                "gmsh:physical": [expanded.physical_tags],
                "gmsh:geometrical": [expanded.physical_tags],
            },
            field_data=source.field_data,
        ),
        file_format="gmsh22",
        binary=False,
    )
    print(
        f"Wrote {expanded.vertices_nx3.shape[0]} vertices and "
        f"{expanded.triangles_nx3.shape[0]} triangles to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
