"""Ingest-level qualification: real CAD returns through WG's own pipeline.

Record-level fixtures (``qualify_imported_same_mesh.py``) hand the engines a
mesh built here. These hand them what a user's return produces: a STEP body
tagged by role resolution, cut by WG's cutter, normalised into the anchor
frame -- and, for the fresh-install case, submitted through the job runtime
and read back from the store.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from scripts import imported_ingest_fixtures as fixtures
from scripts.qualify_imported_same_mesh import EXACT_TOLERANCE, TOLERANCE_MARGIN, Row, Solved, relative_error

HORN_FREQUENCIES_HZ = (300.0, 1000.0, 3000.0)
#: (label, surface deviation in mm). The chord deviation is what sizes an
#: imported mesh -- the size targets barely move it on a body this small -- and
#: WG accepts 0.1 to 0.35 mm, so this ladder spans every density a user can ask
#: for: the coarsest, the default, and the finest.
HORN_LADDER = (("coarse", 0.35), ("reference", 0.15), ("fine", 0.1))
#: Convergence order in element size h, measured on the analytic spheres: the
#: error falls about 4x per 4.3x triangles (error ~ 1/N ~ h^2). The finest horn
#: is only ~1.3x the reference, so its distance from the reference understates
#: the reference's own error; Richardson with this order recovers it.
HORN_ORDER = 2.0


def _richardson_factor(reference_triangles: int, fine_triangles: int, order: float = HORN_ORDER) -> float:
    """``e_ref = |u_ref - u_fine| * factor`` for an error falling as h^order, h ~ N^-1/2."""

    return 1.0 / (1.0 - (reference_triangles / fine_triangles) ** (order / 2.0))


def _solve_record(engine: str, ingested: fixtures.Ingested, motion: str) -> Solved:
    from server.engines.registry import create_engine
    from server.solver.combine import deserialize_channel_bases

    request = fixtures.request_for_record(
        ingested, engine=engine, motion=motion, frequencies=HORN_FREQUENCIES_HZ
    )
    started = time.perf_counter()
    outcome = asyncio.run(
        create_engine(engine).run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_: None,
            imported_record=dict(ingested.record),
        )
    )
    bases = deserialize_channel_bases(outcome.channel_bases)
    first = bases["results_by_id"][bases["channel_ids"][0]]
    return Solved(
        engine=engine,
        channel_ids=list(bases["channel_ids"]),
        frequencies_hz=np.asarray(bases["frequencies_hz"], dtype=float),
        angles_deg=np.asarray(first.observation_angles_deg, dtype=float),
        planes=list(first.observation_planes),
        pressure={name: np.asarray(bases["results_by_id"][name].pressure_complex) for name in bases["channel_ids"]},
        sphere={name: bases["results_by_id"][name].sphere_pressure_complex for name in bases["channel_ids"]},
        sphere_theta_deg=first.sphere_theta_deg,
        sphere_phi_deg=first.sphere_phi_deg,
        wall_seconds=time.perf_counter() - started,
        metadata={"solver_engine": outcome.results.get("metadata", {}).get("solver_engine")},
    )


async def _run_job(data_dir: Path, store: Any, request: Any, timeout_s: float = 900.0) -> dict[str, Any]:
    from server.engines.registry import EngineRegistry
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    runtime = JobRuntime(JobStore(data_dir / "jobs.db"), engine_registry=EngineRegistry(), cadlink_store=store)
    try:
        job_id = await runtime.submit(request)
        deadline = time.monotonic() + timeout_s
        while True:
            row = runtime.store.get_job_row(job_id)
            if row["status"] in {"complete", "error", "cancelled"}:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"job {job_id} did not finish in {timeout_s} s")
            await asyncio.sleep(0.5)
        return {"job_id": job_id, "row": row, "results": runtime.store.get_results(job_id)}
    finally:
        await runtime.shutdown()


def _reopen(data_dir: Path, job_id: str) -> dict[str, Any] | None:
    from server.jobs.store import JobStore

    return JobStore(data_dir / "jobs.db").get_results(job_id)


async def _plan(data_dir: Path, store: Any, request: Any) -> dict[str, Any]:
    from server.engines.registry import EngineRegistry
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    runtime = JobRuntime(JobStore(data_dir / "jobs.db"), engine_registry=EngineRegistry(), cadlink_store=store)
    try:
        return await runtime.plan_imported(request)
    finally:
        await runtime.shutdown()


def run_ingest_level(engines: Sequence[str], root: Path, record_row: Callable[[Row], Row]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    workspace = root / "workspace"
    bundle = fixtures.linked_return(workspace, "round")

    # Fixture 3 on a real return: three densities of the same return. The
    # horn has no formula, so each engine's error at the reference density is
    # estimated from its own ladder: the reference-to-finest distance, scaled
    # by Richardson for the sphere-measured order. The coarse level checks
    # that order -- its distance must be about what the order predicts.
    ladder: dict[str, dict[str, Solved]] = {engine: {} for engine in engines}
    ingests: dict[str, fixtures.Ingested] = {}
    for label, deviation in HORN_LADDER:
        ingests[label] = fixtures.ingest(bundle, root / f"data-round-{label}", surface_deviation_mm=deviation)
        for engine in engines:
            ladder[engine][label] = _solve_record(engine, ingests[label], "normal")
    reference = ingests["reference"]
    triangles = {label: int(ingests[label].record["mesh"]["stats"]["triangle_count"]) for label, _ in HORN_LADDER}
    facts["horn_triangles"] = triangles
    facts["horn_domain"] = reference.record["symmetry"].get("domain_planes") or reference.record["symmetry"].get("cut_planes")
    factor = _richardson_factor(triangles["reference"], triangles["fine"])
    half_order = HORN_ORDER / 2.0
    predicted_ratio = (triangles["coarse"] ** -half_order - triangles["fine"] ** -half_order) / (
        triangles["reference"] ** -half_order - triangles["fine"] ** -half_order
    )
    facts["horn_richardson_factor"] = factor
    facts["horn_order_check"] = {"predicted_coarse_over_reference": predicted_ratio}
    estimated: dict[str, np.ndarray] = {}
    for engine in engines:
        distance = {
            label: relative_error(ladder[engine][label].observations(), ladder[engine]["fine"].observations())
            for label in ("coarse", "reference")
        }
        for label in ("coarse", "reference"):
            record_row(Row(f"horn return, {label} vs fine density", engine, "fine", "complex, all points", distance[label].tolist(), note="fixture 3 on a real return"))
        estimated[engine] = factor * distance["reference"]
        record_row(Row("horn return, reference error (Richardson estimate)", engine, "extrapolated", "complex, all points", estimated[engine].tolist(), note=f"{factor:.2f} x the reference-to-fine distance, order {HORN_ORDER:g}"))
        facts["horn_order_check"][engine] = float(np.max(distance["coarse"]) / np.max(distance["reference"]))
    discretisation = {engine: float(np.max(values)) for engine, values in estimated.items()}
    # The same bound as the spheres': both engines' errors at this density,
    # summed per frequency (the triangle inequality), worst frequency, margin.
    horn_tolerance = TOLERANCE_MARGIN * float(np.max(sum(estimated.values())))
    facts["horn_same_mesh_tolerance"] = horn_tolerance

    # Fixture 1 on a real return: the same ingested record into every engine.
    pairs = [(a, b) for index, a in enumerate(engines) for b in engines[index + 1 :]]
    by_motion: dict[str, dict[str, Solved]] = {"normal": {engine: ladder[engine]["reference"] for engine in engines}}
    by_motion["axial"] = {engine: _solve_record(engine, reference, "axial") for engine in engines}
    for motion, solved in by_motion.items():
        for a, b in pairs:
            errors = relative_error(solved[a].observations(), solved[b].observations())
            record_row(Row(f"same mesh: horn quarter return, {motion}", a, b, "complex, all points", errors.tolist(), tolerance=horn_tolerance))

    # A consistency check on the estimates, recorded rather than used: one
    # engine's true error cannot exceed another's plus their measured
    # difference. Feeding that bound back into the tolerance would judge the
    # difference by itself, so it only says how far an estimate overshoots.
    facts["horn_error_consistency"] = {}
    for engine in engines:
        bounds = [
            estimated[other] + relative_error(by_motion["normal"][engine].observations(), by_motion["normal"][other].observations())
            for other in engines
            if other != engine
        ]
        if bounds:
            facts["horn_error_consistency"][engine] = {
                "richardson_estimate": float(np.max(estimated[engine])),
                "bound_from_other_engines": float(np.max(np.minimum.reduce(bounds))),
            }

    # Fixture 6 on a real return: WG's quarter against the forced full domain.
    full = fixtures.ingest(bundle, root / "data-round", symmetry_mode="full")
    facts["horn_full_triangles"] = full.record["mesh"]["stats"]["triangle_count"]
    for engine in engines:
        whole = _solve_record(engine, full, "normal")
        errors = relative_error(ladder[engine]["reference"].observations(), whole.observations())
        record_row(Row("horn: quarter return vs forced full domain", engine, "full", "complex, all points", errors.tolist(), tolerance=2.0 * TOLERANCE_MARGIN * discretisation[engine], note="different meshes of one body: bounded by twice the engine's discretisation error"))

    # An ingest defect, reported rather than judged: the source tagged on a
    # face that looks AWAY from the fluid (the plug's rear, which is what a
    # rounded-cap membrane leaves planar). The full domain keeps the closed
    # body's outward winding; the auto quarter is re-oriented so the source
    # normal points along +z, which inverts every wall. Both engines then
    # solve an inside-out surface, so neither answer means anything.
    rear = fixtures.linked_return(workspace, "rearcap", source_shape=1)
    rear_quarter = fixtures.ingest(rear, root / "data-rearcap")
    rear_full = fixtures.ingest(rear, root / "data-rearcap", symmetry_mode="full")
    postprocess = rear_quarter.record["mesh"]["metadata"]["postprocess"]
    facts["rear_cap_quarter"] = {
        "cut_planes": rear_quarter.record["symmetry"].get("cut_planes"),
        "triangles": rear_quarter.record["mesh"]["stats"]["triangle_count"],
        "flipped_global": postprocess.get("flipped_global"),
        "full_flipped_global": rear_full.record["mesh"]["metadata"]["postprocess"].get("flipped_global"),
        "orientation_valid": rear_quarter.record["mesh"]["integrity"].get("orientation_valid"),
        "warnings": rear_quarter.record["mesh"]["stats"].get("warnings"),
        "findings": sorted({str(item["kind"]) for item in rear_quarter.record.get("findings") or []}),
    }
    rear_facts = facts["rear_cap_quarter"]
    for engine in engines:
        errors = relative_error(_solve_record(engine, rear_quarter, "normal").observations(), _solve_record(engine, rear_full, "normal").observations())
        record_row(Row("DEFECT (ingest): source on the plug's rear -- WG's quarter vs its full domain", engine, "full", "complex, all points", errors.tolist(), note=(
            f"quarter re-oriented: flipped_global={rear_facts['flipped_global']} of {rear_facts['triangles']} triangles; "
            f"orientation_valid={rear_facts['orientation_valid']}; warnings={rear_facts['warnings']}; findings={rear_facts['findings']}"
        )))

    # Fixture 4 on a real return: the same instance moved and turned in CAD.
    # Blocked today by ingestion, not by any engine: WG normalises a placed
    # anchor with gmsh's general affineTransform, which rewrites the planar
    # throat as a B-spline, and the throat check then refuses the return. The
    # attempt stays here so the fixture runs the day ingestion keeps the plane.
    from server.cadlink.ingest import IngestRefusal

    placement = fixtures.placement_matrix([0.3, 1.0, 0.2], 70.0, [120.0, -40.0, 55.0])
    try:
        placed = fixtures.ingest(fixtures.linked_return(workspace, "placed", placement=placement), root / "data-placed")
    except IngestRefusal as exc:
        facts["placed_refusal"] = str(exc)
        # Only the known refusal is the known block. Any other refusal is a
        # new failure of the placed return, and is judged as one.
        known = "anchor throat face did not resolve after placement" in str(exc)
        record_row(Row(
            "horn: placed in CAD (rotated + translated) -- BLOCKED at ingest" if known else "horn: placed in CAD -- UNEXPECTED ingest refusal",
            "ingest", "unplaced", "refusal", [1.0], tolerance=None if known else 0.5, note=str(exc),
        ))
    else:
        facts["placed_frame"] = placed.record["anchor"]["throat_frame"]
        for engine in engines:
            moved = _solve_record(engine, placed, "normal")
            errors = relative_error(moved.observations(), ladder[engine]["reference"].observations())
            record_row(Row("horn: placed in CAD (rotated + translated) vs unplaced", engine, "unplaced", "complex, all points", errors.tolist(), tolerance=2.0 * TOLERANCE_MARGIN * discretisation[engine], note="normalisation undoes the placement; OCC re-meshes the placed body"))

    # Fixture 7: a y-only half, through the real plan and detector.
    skewed = fixtures.ingest(fixtures.linked_return(workspace, "skewed", skew_mm=12.0), root / "data-skewed")
    plan = asyncio.run(_plan(root / "data-skewed", skewed.store, fixtures.request_for_record(skewed, engine="auto", frequencies=HORN_FREQUENCIES_HZ)))
    verdicts = {entry["name"]: entry for entry in plan["engines"]}
    facts["y_only_plan"] = {name: {key: verdicts[name].get(key) for key in ("solves", "stage", "code", "reason")} for name in engines if name in verdicts}
    facts["y_only_auto_engine"] = plan["engine"]
    refused = "beat-cpu" in verdicts and not verdicts["beat-cpu"]["solves"] and verdicts["beat-cpu"]["code"] == "imported_symmetry_unsupported_by_engine"
    record_row(Row("y-only half: BEAT-CPU refused at submission with the reason", "beat-cpu", "plan", "verdict", [0.0 if refused else 1.0], tolerance=0.5, note=str(verdicts.get("beat-cpu", {}).get("reason"))))

    # The end-to-end fixture: a linked return copied from another machine into
    # a fresh app data directory (no design registry, no export records, no
    # cached mesh), under paths with spaces and non-ASCII characters; imported,
    # prepared, solved through the job runtime, stored, and reopened.
    copied = root / "Kopia från annan dator" / "Högtalare ÅÄÖ.wgreturn"
    copied.parent.mkdir(parents=True)
    shutil.copytree(bundle, copied)
    fresh_dir = root / "App Data – Ärende 1"
    fresh = fixtures.ingest(copied, fresh_dir)
    facts["fresh_findings"] = [(item["kind"], item.get("verdict"), item.get("blocking")) for item in fresh.record.get("findings") or []]
    jobs: dict[str, Any] = {}
    for engine in engines:
        request = fixtures.request_for_record(fresh, engine=engine, frequencies=HORN_FREQUENCIES_HZ)
        outcome = asyncio.run(_run_job(fresh_dir, fresh.store, request))
        reopened = _reopen(fresh_dir, outcome["job_id"])
        jobs[engine] = {
            "status": outcome["row"]["status"],
            "error": outcome["row"].get("error_message"),
            "engine": ((outcome["results"] or {}).get("metadata") or {}).get("solver_engine"),
            "reopened_equal": reopened == outcome["results"],
        }
        ran = (jobs[engine]["engine"] or {}).get("engine")
        ok = outcome["row"]["status"] == "complete" and reopened == outcome["results"] and ran == engine
        record_row(Row("fresh app data dir: import, prepare, solve, store, reopen", engine, "end to end", "job", [0.0 if ok else 1.0], tolerance=0.5, note=str(jobs[engine])))
    facts["fresh_jobs"] = jobs

    # A return whose body evidence says it was edited in CAD after WG exported
    # it. The STEP is the unedited body -- only the fingerprints differ -- so
    # the exact match is expected by construction: what this row tests is that
    # acknowledging the blocking freshness finding leaves the solve unchanged.
    edited = fixtures.ingest(fixtures.linked_return(workspace, "edited", body_state="modified"), root / "data-edited")
    facts["edited_findings"] = [(item["kind"], item.get("verdict"), item.get("blocking")) for item in edited.record.get("findings") or []]
    for engine in engines:
        solved = _solve_record(engine, edited, "normal")
        errors = relative_error(solved.observations(), ladder[engine]["reference"].observations())
        record_row(Row("return edited in CAD (acknowledged) vs the unedited return", engine, "unedited", "complex, all points", errors.tolist(), tolerance=EXACT_TOLERANCE, note="unedited STEP, edited body evidence: the acknowledgement must not change the solve"))
    return facts


__all__ = ["run_ingest_level"]
