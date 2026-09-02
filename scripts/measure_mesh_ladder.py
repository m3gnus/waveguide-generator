"""A/B a per-band mesh ladder sweep against the single-mesh sweep it replaces.

The ladder is only worth having if it does not change the answer, so this
measures both halves of that claim on one design:

* **Agreement**, scored the way this project scores a mesh comparison -- rms of
  the normalized main lobe and the -6 dB beamwidth, never absolute SPL and never
  null depth -- and *only* at frequencies inside both meshes' own
  elements-per-wavelength validity ceilings.  Above a mesh's ceiling the two
  runs are two disagreements about an unconverged solution and comparing them
  says nothing.
* **Seams**, because a step at a band boundary is the failure a user would
  actually see.  A seam shows up as a jump in the ladder-minus-baseline
  difference exactly at a boundary, so the jump across every boundary is
  compared against the jumps everywhere else.

and reports end-to-end wall clock -- mesh builds included -- cold and warm.

Usage::

    python scripts/measure_mesh_ladder.py <design.txt|design.json> \
        --frequencies 40 --range 400 16000 [--resolution-scale 0.5] \
        [--band-ratio 2.0] [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.design.schema import DesignConfig  # noqa: E402
from server.design_io.api import open_design  # noqa: E402
from server.jobs.models import (  # noqa: E402
    ParametricGeometrySource,
    PolarConfig,
    SolveOptions,
    SolveRequest,
)
from server.mesh.builder import clear_solver_mesh_cache  # noqa: E402
from server.solver.mesh_ladder import (  # noqa: E402
    LADDER_ELEMENTS_PER_WAVELENGTH,
    scaled_design,
)

#: Main-lobe cut for the rms score. -6 dB is the same contour the beamwidth
#: uses, so both numbers describe the same part of the pattern.
MAIN_LOBE_DB = -6.0


async def load_design(path: Path) -> DesignConfig:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        return DesignConfig.model_validate(payload.get("design", payload))
    opened = await open_design(text)
    return DesignConfig.model_validate(opened["design"])


def build_request(
    design: DesignConfig, frequencies: Sequence[float], *, mesh_ladder: str
) -> SolveRequest:
    options = SolveOptions(
        engine="metal",
        solver_mode="full_3d",
        frequencies_hz=[float(value) for value in frequencies],
        mesh_ladder=mesh_ladder,
        # Field traces are per-mesh, so a laddered run cannot retain them. Keep
        # both halves of the A/B on the same footing by turning them off for
        # the baseline too; retaining them would only make the baseline slower.
        polar_config=PolarConfig(field_plane=False),
    )
    return SolveRequest(
        options=options,
        geometry=ParametricGeometrySource(type="parametric", design=design),
    )


async def run_sweep(
    design: DesignConfig, frequencies: Sequence[float], *, mesh_ladder: str
) -> tuple[dict[str, Any], float]:
    from server.solver.metal import MetalEngine

    request = build_request(design, frequencies, mesh_ladder=mesh_ladder)
    started = time.perf_counter()
    outcome = await MetalEngine().run(
        request, cancel_cb=lambda: None, stage_cb=lambda *args: None
    )
    return outcome.results, time.perf_counter() - started


def beamwidth_deg(curve: Sequence[Sequence[float]], level_db: float) -> float | None:
    """Interpolate the full angle at ``level_db`` on a one-sided polar curve."""

    angles = [float(point[0]) for point in curve]
    levels = [float(point[1]) for point in curve]
    for index in range(1, len(levels)):
        if levels[index] <= level_db < levels[index - 1]:
            span = levels[index - 1] - levels[index]
            if span <= 0.0:
                return 2.0 * angles[index]
            fraction = (levels[index - 1] - level_db) / span
            return 2.0 * (
                angles[index - 1] + fraction * (angles[index] - angles[index - 1])
            )
    return None


def main_lobe_rms_db(
    baseline: Sequence[Sequence[float]], ladder: Sequence[Sequence[float]]
) -> float | None:
    residuals = [
        float(other[1]) - float(base[1])
        for base, other in zip(baseline, ladder)
        if float(base[1]) >= MAIN_LOBE_DB
    ]
    if not residuals:
        return None
    return math.sqrt(sum(value * value for value in residuals) / len(residuals))


def band_validity_by_frequency(results: dict[str, Any]) -> dict[float, float]:
    """Map each solved frequency to the validity ceiling of the mesh it ran on."""

    ladder = (results.get("metadata") or {}).get("mesh_ladder") or {}
    ceilings: dict[float, float] = {}
    frequencies = [float(value) for value in results["frequencies"]]
    if not ladder.get("applied"):
        diagnostics = (
            (results.get("metadata") or {}).get("metal") or {}
        ).get("native_diagnostics") or []
        for frequency, entry in zip(frequencies, diagnostics):
            limit = (entry or {}).get("mesh_max_valid_frequency_hz")
            ceilings[frequency] = float(limit) if limit is not None else math.inf
        for frequency in frequencies:
            ceilings.setdefault(frequency, math.inf)
        return ceilings
    for band in ladder.get("bands") or []:
        ceiling = float(band["max_valid_frequency_hz"])
        low = float(band["lower_hz"])
        high = float(band["upper_hz"])
        for frequency in frequencies:
            if low < frequency <= high * (1.0 + 1e-12):
                ceilings[frequency] = ceiling
    for frequency in frequencies:
        ceilings.setdefault(frequency, math.inf)
    return ceilings


def compare(
    baseline: dict[str, Any], ladder: dict[str, Any], *, axes: Sequence[str]
) -> dict[str, Any]:
    frequencies = [float(value) for value in baseline["frequencies"]]
    if frequencies != [float(value) for value in ladder["frequencies"]]:
        raise SystemExit("baseline and ladder solved different frequency axes")
    baseline_ceiling = band_validity_by_frequency(baseline)
    ladder_ceiling = band_validity_by_frequency(ladder)

    rows: list[dict[str, Any]] = []
    for index, frequency in enumerate(frequencies):
        ceiling = min(baseline_ceiling[frequency], ladder_ceiling[frequency])
        row: dict[str, Any] = {
            "frequency_hz": frequency,
            "validity_ceiling_hz": ceiling,
            "inside_validity": frequency <= ceiling,
            "axes": {},
        }
        for axis in axes:
            base_curve = baseline["directivity"][axis][index]
            ladder_curve = ladder["directivity"][axis][index]
            base_bw = beamwidth_deg(base_curve, MAIN_LOBE_DB)
            ladder_bw = beamwidth_deg(ladder_curve, MAIN_LOBE_DB)
            row["axes"][axis] = {
                "main_lobe_rms_db": main_lobe_rms_db(base_curve, ladder_curve),
                "baseline_beamwidth_deg": base_bw,
                "ladder_beamwidth_deg": ladder_bw,
                "beamwidth_delta_deg": (
                    None if base_bw is None or ladder_bw is None else ladder_bw - base_bw
                ),
            }
        row["on_axis_delta_db"] = float(ladder["spl_on_axis"]["spl"][index]) - float(
            baseline["spl_on_axis"]["spl"][index]
        )
        rows.append(row)

    inside = [row for row in rows if row["inside_validity"]]
    summary: dict[str, Any] = {
        "frequency_count": len(rows),
        "inside_validity_count": len(inside),
        "axes": {},
    }
    for axis in axes:
        rms = [
            row["axes"][axis]["main_lobe_rms_db"]
            for row in inside
            if row["axes"][axis]["main_lobe_rms_db"] is not None
        ]
        bw = [
            abs(row["axes"][axis]["beamwidth_delta_deg"])
            for row in inside
            if row["axes"][axis]["beamwidth_delta_deg"] is not None
        ]
        summary["axes"][axis] = {
            "main_lobe_rms_db_max": max(rms) if rms else None,
            "main_lobe_rms_db_mean": statistics.fmean(rms) if rms else None,
            "beamwidth_abs_delta_deg_max": max(bw) if bw else None,
            "beamwidth_abs_delta_deg_mean": statistics.fmean(bw) if bw else None,
        }
    return {"rows": rows, "summary": summary}


def seam_check(
    comparison: dict[str, Any], ladder: dict[str, Any], *, axes: Sequence[str]
) -> dict[str, Any]:
    """Compare the step across each band boundary with the steps elsewhere.

    The physical response has real structure, so the ladder curve alone cannot
    show a seam.  What can is the *difference* from the baseline: within a band
    it drifts smoothly, and a seam is a jump in it at exactly one frequency --
    the first point of the next band down.
    """

    metadata = (ladder.get("metadata") or {}).get("mesh_ladder") or {}
    bands = metadata.get("bands") or []
    boundaries = sorted(
        {float(band["lower_hz"]) for band in bands if float(band["lower_hz"]) > 0.0}
    )
    rows = comparison["rows"]
    frequencies = [row["frequency_hz"] for row in rows]

    def crossings(series: list[float | None]) -> dict[str, Any]:
        steps: list[tuple[float, float, bool]] = []
        for index in range(1, len(series)):
            lower, upper = series[index - 1], series[index]
            if lower is None or upper is None:
                continue
            edge = any(
                frequencies[index - 1] <= boundary + 1e-9 < frequencies[index]
                for boundary in boundaries
            )
            steps.append((frequencies[index], abs(upper - lower), edge))
        at_edge = [value for _, value, edge in steps if edge]
        interior = [value for _, value, edge in steps if not edge]
        return {
            "boundary_step_max": max(at_edge) if at_edge else None,
            "boundary_step_mean": statistics.fmean(at_edge) if at_edge else None,
            "interior_step_max": max(interior) if interior else None,
            "interior_step_median": statistics.median(interior) if interior else None,
            "boundary_steps": [
                {"frequency_hz": frequency, "step": value}
                for frequency, value, edge in steps
                if edge
            ],
        }

    result: dict[str, Any] = {
        "band_boundaries_hz": boundaries,
        "on_axis_delta_db": crossings([row["on_axis_delta_db"] for row in rows]),
        "axes": {},
    }
    for axis in axes:
        result["axes"][axis] = {
            "beamwidth_delta_deg": crossings(
                [row["axes"][axis]["beamwidth_delta_deg"] for row in rows]
            ),
        }
    return result


def ladder_summary(results: dict[str, Any]) -> dict[str, Any]:
    metadata = (results.get("metadata") or {}).get("mesh_ladder") or {}
    if not metadata.get("applied"):
        return {"applied": False, "reason": metadata.get("reason")}
    return {
        "applied": True,
        "dropped_native_fields": metadata.get("dropped_native_fields"),
        "projected_solve_work_ratio": metadata.get("projected_solve_work_ratio"),
        "bands": [
            {
                key: band.get(key)
                for key in (
                    "index",
                    "lower_hz",
                    "upper_hz",
                    "frequency_count",
                    "resolution_scale",
                    "triangle_count",
                    "max_valid_frequency_hz",
                    "seam_check",
                    "solve_wall_time_seconds",
                    "fallback_reason",
                )
            }
            for band in metadata.get("bands") or []
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", type=Path, help="ATH .txt or WG design .json")
    parser.add_argument("--range", type=float, nargs=2, default=(400.0, 16000.0))
    parser.add_argument("--frequencies", type=int, default=40)
    parser.add_argument(
        "--resolution-scale",
        type=float,
        default=1.0,
        help=(
            "uniform multiplier on the design's mm resolutions before the A/B, "
            "so a full-band *valid* baseline can be constructed from a design "
            "whose authored mesh is coarser than its sweep"
        ),
    )
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--skip-cold", action="store_true", help="warm timings only (faster)"
    )
    parser.add_argument(
        "--band-ratio",
        type=float,
        default=None,
        help=(
            "band width as a frequency ratio (2.0 = octaves). Set for the "
            "measurement only, by rebinding the planner's default -- band width "
            "is not a per-solve option"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="interleaved A-B repeats; the reported time is the minimum of each leg",
    )
    args = parser.parse_args()

    if args.band_ratio is not None:
        import server.solver.mesh_ladder as mesh_ladder

        mesh_ladder.DEFAULT_BAND_RATIO = args.band_ratio

    design = await load_design(args.design)
    if args.resolution_scale != 1.0:
        design = scaled_design(design, args.resolution_scale)
    frequencies = np.geomspace(
        args.range[0], args.range[1], args.frequencies
    ).tolist()
    axes = ("horizontal", "vertical")

    timings: dict[str, Any] = {"load_average_before": os.getloadavg()}
    cold_baseline: list[float] = []
    cold_ladder: list[float] = []
    warm_baseline: list[float] = []
    warm_ladder: list[float] = []
    baseline: dict[str, Any] | None = None
    ladder: dict[str, Any] | None = None

    # Interleaved A-B-A-B in one process, reported as the minimum. A stray
    # worker on another core inflated a whole benchmark matrix in this
    # workspace on 2026-09-02 while every individual number stayed plausible;
    # contention can only slow a leg, so the minimum is the honest estimate and
    # a wide spread is evidence of contention rather than of solver behaviour.
    for _ in range(args.repeats):
        if not args.skip_cold:
            clear_solver_mesh_cache()
            _, elapsed = await run_sweep(design, frequencies, mesh_ladder="off")
            cold_baseline.append(elapsed)
            clear_solver_mesh_cache()
            _, elapsed = await run_sweep(design, frequencies, mesh_ladder="auto")
            cold_ladder.append(elapsed)
        baseline, elapsed = await run_sweep(design, frequencies, mesh_ladder="off")
        warm_baseline.append(elapsed)
        ladder, elapsed = await run_sweep(design, frequencies, mesh_ladder="auto")
        warm_ladder.append(elapsed)

    def leg(base: list[float], other: list[float]) -> dict[str, Any]:
        return {
            "baseline_s": min(base),
            "ladder_s": min(other),
            "baseline_spread": max(base) - min(base),
            "ladder_spread": max(other) - min(other),
            "speedup": min(base) / min(other) if min(other) else None,
        }

    if cold_baseline:
        timings["cold"] = leg(cold_baseline, cold_ladder)
    timings["warm"] = leg(warm_baseline, warm_ladder)
    timings["load_average_after"] = os.getloadavg()
    assert baseline is not None and ladder is not None

    comparison = compare(baseline, ladder, axes=axes)
    payload = {
        "design": args.design.name,
        "resolution_scale": args.resolution_scale,
        "frequency_range_hz": list(args.range),
        "frequency_count": args.frequencies,
        "band_ratio": args.band_ratio,
        "elements_per_wavelength": LADDER_ELEMENTS_PER_WAVELENGTH,
        "ladder": ladder_summary(ladder),
        "timings": timings,
        "agreement": comparison["summary"],
        "seams": seam_check(comparison, ladder, axes=axes),
        "rows": comparison["rows"],
    }
    text = json.dumps(payload, indent=2)
    if args.json is not None:
        args.json.write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
