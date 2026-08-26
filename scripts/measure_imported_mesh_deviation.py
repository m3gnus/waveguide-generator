"""Sweep imported-mesh sizing rules and report the deviation each one achieves.

The imported-mesh sizing rule has been changed twice on the strength of
triangle-count-versus-deviation tables that were never reproducible: the
measurement harness was not committed either time, so the numbers outlived the
code that produced them and ended up describing a variant that had already been
thrown away. This script is that harness.

Both rules are meshed through ``build_imported_mesh`` and measured by the same
``measure_surface_deviation`` call, so a row is comparable to every other row by
construction rather than by recollection.

Usage:

    python scripts/measure_imported_mesh_deviation.py <bundle-dir> \\
        --segments 12 24 32 --deviation 0.15 0.2 0.3

``<bundle-dir>`` is a staged CAD-return bundle: an ``assembly.step`` beside its
``wgreturn.json``, as found under ``imports/bundles`` in the application data
directory. Mesh sizes default to the reference return's; override them when
measuring a different model, because deviation is only comparable at a fixed
size request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.mesh.imported import build_imported_mesh

REFERENCE_SIZES = {
    "rigid_size_mm": 30.0,
    "transition_mm": 30.0,
    "source_size_mm": {"source-hf": 4.0, "source-lf": 30.0, "source-mf": 15.0},
}


def measure(bundle: Path, sizes: dict, label: str, **options: object) -> dict:
    started = time.perf_counter()
    result = asyncio.run(
        run_on_gmsh_worker(
            build_imported_mesh,
            bundle / "assembly.step",
            json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8")),
            sizes,
            options={"measure_deviation": True, **options},
            include_viewport_mesh=False,
        )
    )
    deviation = result.get("surface_deviation") or {}
    row = {
        "label": label,
        "triangles": deviation.get("triangle_count"),
        "peak_mm": deviation.get("peak_mm"),
        "p95_mm": deviation.get("p95_mm"),
        "rms_mm": deviation.get("rms_mm"),
        "peak_surface": deviation.get("peak_surface"),
        "peak_facet_longest_edge_mm": deviation.get("peak_facet_longest_edge_mm"),
        "build_seconds": round(time.perf_counter() - started, 2),
    }
    print(json.dumps(row), flush=True)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--segments", type=int, nargs="*", default=[24])
    parser.add_argument("--deviation", type=float, nargs="*", default=[0.15, 0.2, 0.3])
    parser.add_argument(
        "--grid-samples",
        type=int,
        nargs="*",
        default=[0],
        help="Sagitta parameter-grid resolution; 0 keeps the module default.",
    )
    parser.add_argument("--sizes", type=Path, help="JSON mesh sizes; defaults to the reference return's.")
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()

    sizes = (
        json.loads(arguments.sizes.read_text(encoding="utf-8"))
        if arguments.sizes
        else REFERENCE_SIZES
    )
    rows = [
        measure(
            arguments.bundle,
            sizes,
            f"segments={segments}",
            sizing_rule="segments",
            curvature_segments=segments,
        )
        for segments in arguments.segments
    ]
    for grid in arguments.grid_samples:
        for deviation in arguments.deviation:
            suffix = "" if not grid else f" grid={grid}"
            rows.append(
                measure(
                    arguments.bundle,
                    sizes,
                    f"deviation={deviation}{suffix}",
                    sizing_rule="sagitta",
                    surface_deviation_mm=deviation,
                    **({"sagitta_grid_samples": grid} if grid else {}),
                )
            )
    if arguments.out:
        arguments.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
