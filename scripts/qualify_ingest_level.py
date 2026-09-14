"""Ingest-level qualification: real CAD returns through WG's own pipeline.

Record-level fixtures (``qualify_imported_same_mesh.py``) hand the engines a
mesh built here. These hand them what a user's return produces: a STEP body
tagged by role resolution, cut by WG's cutter, normalised into the anchor
frame -- and, for the fresh-install case, submitted through the job runtime
and read back from the store.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from scripts import imported_ingest_fixtures as fixtures
from scripts.qualify_imported_same_mesh import EXACT_TOLERANCE, Row, Solved, relative_error, verified
from scripts.qualify_installed_cpu import QualificationError, check_imported_result

HORN_FREQUENCIES_HZ = (300.0, 1000.0, 3000.0)
#: (label, surface deviation in mm). The chord deviation is what sizes an
#: imported mesh -- the size targets barely move it on a body this small -- and
#: WG accepts 0.1 to 0.35 mm, so this ladder spans every density a user can ask
#: for: the coarsest, the default, and the finest.
HORN_LADDER = (("coarse", 0.35), ("reference", 0.15), ("fine", 0.1))
#: The most an engine's horn answer at the default density may move when the
#: return is meshed at the finest density a user can ask for, judged per
#: engine: 1.5x Metal's recorded reference-to-fine distance (2.00e-2; BEAT-CPU
#: read 3.50e-3). Fixed, so an engine whose horn does not converge fails it
#: rather than widening the bound it is judged by.
HORN_LADDER_CEILING = 3.0e-2
#: The stated bound on two horn answers at the default density that should
#: agree: two engines on one mesh, or one engine on two meshes of one body (a
#: cut and its full domain, a placed return and an unplaced one). Twice the
#: ladder ceiling -- the spheres' step from each answer's error to the two
#: answers' difference, with the ladder ceiling standing in for an error the
#: horn has no formula for.
#:
#: It replaces a Richardson estimate at the sphere-measured order 2, which the
#: recorded horn ladder bore out on neither engine: the coarse level sat 2.12x
#: (Metal) and 8.42x (BEAT-CPU) as far from the finest as the default did,
#: against 4.56x predicted. The tolerances it produced -- 0.140 same-mesh,
#: 0.238 quarter-vs-full on Metal -- sat 8 and 21 times above the differences
#: they judged. The recorded differences against this bound: Metal vs BEAT-CPU
#: 1.82e-2; quarter vs full 1.13e-2 (Metal) and 1.19e-3 (BEAT-CPU).
HORN_TOLERANCE = 2.0 * HORN_LADDER_CEILING


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
    solved = Solved(
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
    return verified(
        solved, engine, [channel.id for channel in request.geometry.drive_channels], HORN_FREQUENCIES_HZ
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


def _fresh_job_verdict(
    engine: str, outcome: Mapping[str, Any], reopened: Any
) -> tuple[bool, dict[str, Any]]:
    """Complete, solved by *engine*, reopened unchanged, and carrying data.

    Status, engine name and an identical reopen all hold for a job that stored
    nothing but zeros. So the stored result is also held to the result
    contract the RC gate holds an installed candidate to: every channel's
    on-axis level and requested directivity finite and not all zero, counted
    on the data and never on the frequency or angle axes.
    """

    results = outcome.get("results")
    engine_block = ((results or {}).get("metadata") or {}).get("solver_engine")
    detail: dict[str, Any] = {
        "status": outcome["row"]["status"],
        "error": outcome["row"].get("error_message"),
        "engine": engine_block,
        "reopened_equal": reopened == results,
    }
    try:
        checked = check_imported_result(results if isinstance(results, Mapping) else {}, engine)
    except QualificationError as exc:
        detail["data"] = f"refused: {exc}"
        carries_data = False
    else:
        detail["data"] = {name: channel["finite_non_zero"] for name, channel in checked["channels"].items()}
        carries_data = True
    ran = engine_block.get("engine") if isinstance(engine_block, Mapping) else None
    ok = detail["status"] == "complete" and detail["reopened_equal"] and ran == engine and carries_data
    return ok, detail


def run_ingest_level(engines: Sequence[str], root: Path, record_row: Callable[[Row], Row]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    workspace = root / "workspace"
    bundle = fixtures.linked_return(workspace, "round")

    # Fixture 3 on a real return: three densities of the same return. The
    # horn has no formula, so each engine's answer at the default density is
    # held to its answer at the finest, at a fixed ceiling. The coarse level is
    # recorded, and so is how much farther it sits: that ratio says how far the
    # ladder is from its asymptotic range, and no tolerance is taken from it.
    ladder: dict[str, dict[str, Solved]] = {engine: {} for engine in engines}
    ingests: dict[str, fixtures.Ingested] = {}
    for label, deviation in HORN_LADDER:
        ingests[label] = fixtures.ingest(bundle, root / f"data-round-{label}", surface_deviation_mm=deviation)
        for engine in engines:
            ladder[engine][label] = _solve_record(engine, ingests[label], "normal")
    reference = ingests["reference"]
    facts["horn_triangles"] = {label: int(ingests[label].record["mesh"]["stats"]["triangle_count"]) for label, _ in HORN_LADDER}
    facts["horn_domain"] = reference.record["symmetry"].get("domain_planes") or reference.record["symmetry"].get("cut_planes")
    facts["horn_ladder_ceiling"] = HORN_LADDER_CEILING
    facts["horn_coarse_over_reference"] = {}
    for engine in engines:
        distance = {
            label: relative_error(ladder[engine][label].observations(), ladder[engine]["fine"].observations())
            for label in ("coarse", "reference")
        }
        record_row(Row("horn return, coarse vs fine density", engine, "fine", "complex, all points", distance["coarse"].tolist(), note="fixture 3 on a real return; recorded"))
        record_row(Row("horn return, reference vs fine density", engine, "fine", "complex, all points", distance["reference"].tolist(), tolerance=HORN_LADDER_CEILING, note="fixture 3 on a real return: the default density against the finest"))
        nearest = float(np.max(distance["reference"]))
        facts["horn_coarse_over_reference"][engine] = float(np.max(distance["coarse"])) / nearest if nearest > 0.0 else None
    facts["horn_same_mesh_tolerance"] = HORN_TOLERANCE

    # Fixture 1 on a real return: the same ingested record into every engine.
    pairs = [(a, b) for index, a in enumerate(engines) for b in engines[index + 1 :]]
    by_motion: dict[str, dict[str, Solved]] = {"normal": {engine: ladder[engine]["reference"] for engine in engines}}
    by_motion["axial"] = {engine: _solve_record(engine, reference, "axial") for engine in engines}
    for motion, solved in by_motion.items():
        for a, b in pairs:
            errors = relative_error(solved[a].observations(), solved[b].observations())
            record_row(Row(f"same mesh: horn quarter return, {motion}", a, b, "complex, all points", errors.tolist(), tolerance=HORN_TOLERANCE))

    # Fixture 6 on a real return: WG's quarter against the forced full domain.
    full = fixtures.ingest(bundle, root / "data-round", symmetry_mode="full")
    facts["horn_full_triangles"] = full.record["mesh"]["stats"]["triangle_count"]
    for engine in engines:
        whole = _solve_record(engine, full, "normal")
        errors = relative_error(ladder[engine]["reference"].observations(), whole.observations())
        record_row(Row("horn: quarter return vs forced full domain", engine, "full", "complex, all points", errors.tolist(), tolerance=HORN_TOLERANCE, note="two meshes of one body on one engine"))

    # An ingest defect, reported rather than judged: the source tagged on a
    # face that looks AWAY from the fluid (the plug's rear, which is what a
    # rounded-cap membrane leaves planar). The full domain keeps the closed
    # body's outward winding; the auto quarter is re-oriented so the source
    # normal points along +z, which inverts every wall. Both engines then
    # solve an inside-out surface, so neither answer means anything.
    #
    # The mesher now winds a reduced mesh from its mirrored parent, and WG is to
    # verify it, so this return will be either corrected or refused at ingest.
    # Both are recorded; only a quarter that still arrives inverted stays a
    # DEFECT row, and a corrected one is judged like any other quarter.
    from server.cadlink.ingest import IngestRefusal

    rear = fixtures.linked_return(workspace, "rearcap", source_shape=1)
    rear_full = fixtures.ingest(rear, root / "data-rearcap", symmetry_mode="full")
    try:
        rear_quarter = fixtures.ingest(rear, root / "data-rearcap")
    except IngestRefusal as exc:
        facts["rear_cap_quarter"] = {"refused_at_ingest": str(exc)}
        record_row(Row("source on the plug's rear: WG's quarter refused at ingest", "ingest", "full", "refusal", [0.0], note=str(exc)))
    else:
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
        inverted = bool(rear_facts["flipped_global"]) and rear_facts["flipped_global"] == rear_facts["triangles"]
        note = (
            f"flipped_global={rear_facts['flipped_global']} of {rear_facts['triangles']} triangles; "
            f"orientation_valid={rear_facts['orientation_valid']}; warnings={rear_facts['warnings']}; findings={rear_facts['findings']}"
        )
        for engine in engines:
            errors = relative_error(_solve_record(engine, rear_quarter, "normal").observations(), _solve_record(engine, rear_full, "normal").observations())
            if inverted:
                record_row(Row("DEFECT (ingest): source on the plug's rear -- WG's quarter vs its full domain", engine, "full", "complex, all points", errors.tolist(), note=note))
            else:
                record_row(Row("source on the plug's rear: WG's quarter vs its full domain", engine, "full", "complex, all points", errors.tolist(), tolerance=HORN_TOLERANCE, note=note))

    # Fixture 4 on a real return: the same instance moved and turned in CAD.
    # Blocked today by ingestion, not by any engine: WG normalises a placed
    # anchor with gmsh's general affineTransform, which rewrites the planar
    # throat as a B-spline, and the throat check then refuses the return. The
    # attempt stays here so the fixture runs the day ingestion keeps the plane.
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
            record_row(Row("horn: placed in CAD (rotated + translated) vs unplaced", engine, "unplaced", "complex, all points", errors.tolist(), tolerance=HORN_TOLERANCE, note="normalisation undoes the placement; OCC re-meshes the placed body"))

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
        ok, jobs[engine] = _fresh_job_verdict(engine, outcome, reopened)
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
