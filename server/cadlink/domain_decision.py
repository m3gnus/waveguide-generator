"""One decision about an imported model's solve domain and frame.

Stage 3 of the CAD Link simplification (``cad-domain-decision-v1``). WG makes
one decision per prepared snapshot and every later reader consumes it rather
than deriving its own answer:

- ingest makes it (:func:`decide_domain_and_frame`) from the evidence the
  preparation had before meshing (``DomainPlan``), the observations of the
  solve mesh, what the mesher cut and verified, the solver frame and the
  sources' identities, and seals it in the ingestion record
  (``domain_decision``);
- the model card and the Solve card's plan show it (``interpretation_view``,
  the imported plan's ``domain_decision``);
- a preparation reuses a record only while its decision still matches it;
- every submission and every execution checks the decision against the record
  it is about to solve (:func:`decision_problem`) and takes its refusal from it;
- the job records it (:func:`decision_summary`), so a run states the domain,
  planes, kept sides, fraction, frame and source mapping it actually used.

The result states, in the CAD (exported) frame unless named otherwise:

``input_reading``
    ``full`` (a whole model: nothing open but WG's own cut), ``cut`` (a model
    already cut in CAD: mirrored on evidence, or refused), ``open-sheet`` (open
    edges that are not a cut: a single-sheet horn mouth, a standalone source
    sheet, a port through a wall; solved as shown) or ``unresolved`` (a face
    that may cap a cut or be a wall, or a clean rim that does not span its
    shell: geometry alone cannot say; solved as shown).
``cad_cuts``
    Each CAD cut: plane, solver plane, kept side, what says so, whether its
    mesh was reflected, and whether it is mirrored (on evidence), recovered
    (from its geometry, ``cut_recovery.py``) or refused -- with the flip
    conditions it failed. ``off_centre_cuts`` are cuts off the origin planes,
    which are refused in this version.
``wg_cut_planes`` / ``solver_domain``
    The planes WG cut, and the planes the solver mirrors with the fraction
    (full, half, quarter) of the model the mesh is.
``reflected_axes`` / ``reflection``
    The CAD axes the solve mesh was reflected across (a cut that kept the
    negative side), and how: the planes, the parity of the reflections (the
    triangle winding is reversed once per reflection) and what did it. The
    reflection is recorded apart from the frame, which stays a proper
    rotation.
``frame``
    The proper rigid transform from CAD to solver coordinates.
``sources``
    Per source identity: its tag and how much of it the solve mesh keeps.
``confidence`` / ``evidence`` / ``offered_changes`` / ``refusal``
    How the decision was reached, what supports and contradicts it, the
    Changes the model card may offer, and the refusal every engine meets.
``identity``
    Hashes of the snapshot, the observations, the resolved choice, the solve
    mesh, and of the decision itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from typing import Any

import numpy as np

from .domain_interpretation import (
    DECLARATION,
    MIN_RIM_EDGES,
    PLANES,
    PROVENANCE,
    USER,
    USER_LINEAGE,
    LINEAGE,
    DomainPlan,
    EvidenceOutcome,
    Observations,
    READING_AS_SHOWN,
    READING_REDUCED,
    cut_shaped_open_rim,
    interpretation_record,
    off_centre_cut_refusal_message,
    open_half_refusal_message,
)


CONTRACT = "cad-domain-decision-v1"

INPUT_FULL = "full"
INPUT_CUT = "cut"
INPUT_OPEN_SHEET = "open-sheet"
INPUT_UNRESOLVED = "unresolved"

#: Geometry alone decides (a whole model, WG's own verified cut).
CONFIDENCE_ESTABLISHED = "established"
#: A CAD cut mirrored because recorded evidence says so and the geometry agrees.
CONFIDENCE_EVIDENCED = "evidenced"
#: Open geometry the detector cannot classify; solved as shown.
CONFIDENCE_UNRESOLVED = "unresolved"
#: The snapshot cannot be solved as it stands.
CONFIDENCE_REFUSED = "refused"

OPEN_HALF_CODE = "imported_open_half_shell"
MISMATCH_CODE = "imported_domain_decision_mismatch"
#: Retired with S3-2: a negative-side cut is recovered by mesh reflection or
#: refused with the condition it failed. Kept for records an earlier build wrote.
REFLECTION_PENDING = "pending-s3-2"

_FRACTION = {0: "full", 1: "half", 2: "quarter"}
_DETERMINANT_TOLERANCE = 1.0e-6


def _canonical(value: Any) -> bytes:
    from server.exports.geometry_identity import normalize_json_value

    return json.dumps(
        normalize_json_value(value), allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _sha256(value: Any) -> str | None:
    if value is None:
        return None
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _kept_side(observation: Mapping[str, Any] | None) -> str | None:
    """Which side of its plane the observed model lies on; None when both or neither."""

    if not isinstance(observation, Mapping):
        return None
    negative = int(observation.get("negative_vertices") or 0)
    positive = int(observation.get("positive_vertices") or 0)
    if positive and not negative:
        return "positive"
    if negative and not positive:
        return "negative"
    return None


def _proper_frame(normalisation: Mapping[str, Any]) -> dict[str, Any]:
    matrix = normalisation.get("matrix")
    try:
        array = np.asarray(matrix if matrix is not None else np.eye(4), dtype=float)
    except (TypeError, ValueError):
        array = np.full((4, 4), np.nan)
    if array.shape != (4, 4):
        array = np.full((4, 4), np.nan)
    determinant = float(np.linalg.det(array[:3, :3])) if np.all(np.isfinite(array)) else float("nan")
    proper = bool(
        np.isfinite(determinant)
        and abs(determinant - 1.0) <= _DETERMINANT_TOLERANCE
        and np.allclose(array[:3, :3] @ array[:3, :3].T, np.eye(3), atol=1.0e-6)
    )
    solver_frame = normalisation.get("solver_frame")
    solver_frame = solver_frame if isinstance(solver_frame, Mapping) else {}
    return {
        "solver_from_cad": array.tolist() if np.all(np.isfinite(array)) else None,
        "determinant": round(determinant, 9) if np.isfinite(determinant) else None,
        "proper": proper,
        "axis": solver_frame.get("axis"),
        "up": solver_frame.get("up"),
        "allowed_axes": list(solver_frame.get("allowed_axes") or []),
    }


def _source_mapping(
    source_tags: Mapping[str, Any],
    post_cut_source_areas: Mapping[str, Any],
    skipped_source_ids: Iterable[str],
    observed_planes: Mapping[str, Mapping[str, Any]],
    *,
    reflection: Mapping[str, Any] | None = None,
    channels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Each source identity, its mesh tag, and what of it the solve mesh keeps.

    With a reflected mesh, each source's meshed area before and after the
    reflection (``reflect_triangle_mesh``): the same identity, tag and area.
    """

    tag_areas = (reflection or {}).get("tag_areas") or {}
    final_areas = (reflection or {}).get("final_tag_areas") or {}
    mapping: dict[str, Any] = {}
    for source_id, tag in sorted(source_tags.items()):
        areas = post_cut_source_areas.get(source_id)
        areas = areas if isinstance(areas, Mapping) else {}
        reflected: dict[str, Any] | None = None
        if reflection and reflection.get("axes"):
            held = tag_areas.get(str(int(tag))) or {}
            reflected = {
                "axes": list(reflection.get("axes") or []),
                "meshed_area_before_mm2": held.get("before"),
                "meshed_area_after_mm2": final_areas.get(str(int(tag))),
            }
        mapping[str(source_id)] = {
            "tag": int(tag),
            "channel": (channels or {}).get(str(source_id)),
            "reflected": reflected,
            "retained_fraction": areas.get("retained_fraction"),
            "retained_area_mm2": areas.get("retained_child_area_mm2"),
            "parent_area_mm2": areas.get("parent_area_mm2"),
            "meets_planes": [
                plane for plane in PLANES
                if str(source_id) in (observed_planes.get(plane) or {}).get("sources_on_plane", [])
            ],
            "bisected_by": [
                plane for plane in PLANES
                if str(source_id) in (observed_planes.get(plane) or {}).get("sources_bisected", [])
            ],
        }
    return {
        "by_id": mapping,
        "skipped": sorted(str(item) for item in skipped_source_ids),
    }


def decide_domain_and_frame(
    plan: DomainPlan,
    observations: Observations | None,
    built: Mapping[str, Any],
    *,
    manifest_sha256: str,
    declared_planes: Sequence[str],
    applied: Sequence[str] = (),
    outcome: EvidenceOutcome | None = None,
    cache_identity: Mapping[str, Any] | None = None,
    normalisation: Mapping[str, Any] | None = None,
    skipped_source_ids: Iterable[str] = (),
    mesh_content_sha256: str | None = None,
    recovery: Mapping[str, Any] | None = None,
    reflected: Sequence[str] = (),
    manifest_sources: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The interpretation record and the decision, for the mesh the snapshot is solved with.

    ``plan`` is the evidence before meshing, ``observations`` the detector's
    reading of the solve mesh as it arrived, ``built`` the chosen mesher
    result, ``applied`` the evidenced planes that revalidated and were
    mirrored, ``recovery`` the verdict on the cuts the geometry shows
    (``cut_recovery.CutRecovery.to_json``) and ``reflected`` the mirrored
    planes whose mesh was reflected. Returns ``(domain_interpretation,
    domain_decision)``: the first is the M1c-auto record; the second the
    decision every later reader consumes.
    """

    symmetry = dict(built.get("symmetry") or {})
    interpretation = interpretation_record(
        plan,
        observations,
        {**symmetry, "declared_cut_planes": list(declared_planes)},
        applied=applied,
        outcome=outcome,
        cache_identity=cache_identity,
        recovery=recovery,
        reflected=reflected,
    )
    normalisation = normalisation if normalisation is not None else dict(built.get("normalisation") or {})
    allocation = built.get("tag_allocation") or {}
    source_tags = dict(allocation.get("source_tags") or built.get("source_tags") or {})
    tag_map = dict(allocation.get("tag_map") or built.get("tag_map") or {})
    decision = _decide(
        plan,
        interpretation,
        observations,
        symmetry,
        manifest_sha256=manifest_sha256,
        normalisation=normalisation,
        source_tags=source_tags,
        tag_map=tag_map,
        post_cut_source_areas=dict(built.get("post_cut_source_areas") or {}),
        skipped_source_ids=skipped_source_ids,
        mesh_content_sha256=mesh_content_sha256,
        recovery=recovery,
        reflection=built.get("reflection") if isinstance(built.get("reflection"), Mapping) else None,
        channels={
            str(source.get("id")): source.get("default_drive_channel_id")
            for source in manifest_sources
            if isinstance(source, Mapping)
        },
    )
    return interpretation, decision


def _decide(
    plan: DomainPlan,
    interpretation: Mapping[str, Any],
    observations: Observations | None,
    symmetry: Mapping[str, Any],
    *,
    manifest_sha256: str,
    normalisation: Mapping[str, Any],
    source_tags: Mapping[str, Any],
    tag_map: Mapping[str, Any],
    post_cut_source_areas: Mapping[str, Any],
    skipped_source_ids: Iterable[str],
    mesh_content_sha256: str | None,
    recovery: Mapping[str, Any] | None = None,
    reflection: Mapping[str, Any] | None = None,
    channels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observed_json = observations.to_json() if observations is not None else None
    observed_planes: Mapping[str, Mapping[str, Any]] = (observed_json or {}).get("planes") or {}
    other_open = int((observed_json or {}).get("other_open_edges") or 0)
    domain_planes = [str(plane) for plane in symmetry.get("domain_planes") or symmetry.get("cut_planes") or []]
    wg_cut_planes = [str(plane) for plane in symmetry.get("cut_planes") or []]
    reading = interpretation.get("reading")
    mirrored = list(interpretation.get("planes") or []) if reading == READING_REDUCED else []
    evidence_source = plan.source
    recovered = bool((interpretation.get("evidence") or {}).get("recovered"))
    recovery = recovery if isinstance(recovery, Mapping) else {}
    failures = recovery.get("failures") if isinstance(recovery.get("failures"), Mapping) else {}
    reflected_planes = [str(plane) for plane in interpretation.get("reflected_planes") or []]
    reflection_axes = [str(axis) for axis in (reflection or {}).get("axes") or []]

    # -- the CAD cuts: what evidence mirrored, and what geometry shows unmirrored
    cad_cuts: list[dict[str, Any]] = []
    features_by_plane: dict[str, list[dict[str, Any]]] = {}
    for item in plan.features:
        features_by_plane.setdefault(str(item.get("plane")), []).append(dict(item))
    for plane in mirrored:
        observation = observed_planes.get(plane)
        cad_cuts.append(
            {
                "plane": plane,
                # A recovered cut is solved in the frame it was modelled in.
                "solver_plane": plane if recovered else (observation or {}).get("solver_plane") or plane,
                "kept_side": _kept_side(observation) or "positive",
                "found_by": "geometry" if recovered else evidence_source,
                "features": features_by_plane.get(plane, []),
                "reflected": plane in reflected_planes,
                "status": "recovered" if recovered else "mirrored",
            }
        )
    open_rim = (
        cut_shaped_open_rim({"observations": observed_json})
        if observed_json is not None and not domain_planes
        else None
    )
    for plane in PLANES:
        if plane in mirrored:
            continue
        observation = observed_planes.get(plane)
        if not isinstance(observation, Mapping) or observation.get("wg_cut"):
            continue
        rigid_rim = int(observation.get("rigid_cut_rim_edges") or 0)
        if rigid_rim < MIN_RIM_EDGES:
            continue
        if observation.get("solver_plane") == "z0" and not observation.get("sources_bisected"):
            # An open end on the solver's z0 (a horn mouth) is not a cut.
            continue
        kept = _kept_side(observation)
        entry: dict[str, Any] = {
            "plane": plane,
            "solver_plane": observation.get("solver_plane"),
            "kept_side": kept,
            "found_by": "geometry",
            "features": features_by_plane.get(plane, []),
            "rim_edges": int(observation.get("rim_edges") or 0),
            "rigid_cut_rim_edges": rigid_rim,
            "reflected": False,
            "status": "refused" if open_rim is not None else "unmirrored",
            # The flip conditions this cut failed (``cut_recovery.py``); none
            # listed when WG did not judge it (a declaration, the user's own
            # reading, a linked return).
            "recovery": {
                "judged": bool(recovery),
                "failed": [dict(item) for item in failures.get(plane) or []],
                "by_change": any(
                    choice.get("reading") == READING_REDUCED and plane in (choice.get("planes") or [])
                    for choice in interpretation.get("choices") or []
                ),
            },
        }
        cad_cuts.append(entry)
    off_centre = [
        dict(item) for item in (observed_json or {}).get("off_origin_rims") or []
    ] if not domain_planes else []

    # -- evidence: supporting, contradicted, ignored
    supporting: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    if evidence_source is not None:
        record = {
            "source": evidence_source,
            "reading": plan.reading,
            "planes": list(plane for plane in plan.planes),
            "features": [dict(item) for item in plan.features],
        }
        if evidence_source == DECLARATION or (mirrored and not recovered):
            supporting.append(record)
        elif evidence_source in (USER, USER_LINEAGE) and plan.reading == READING_AS_SHOWN:
            supporting.append(record)
    not_applicable = dict((interpretation.get("evidence") or {}).get("not_applicable") or {})
    for plane, reason in sorted(not_applicable.items()):
        conflicts.append(
            {
                "source": evidence_source,
                "plane": plane,
                "features": features_by_plane.get(plane, []),
                "observed": reason,
            }
        )
    observed_cut_planes = sorted(cut["plane"] for cut in cad_cuts if cut["found_by"] == "geometry")
    recorded = sorted(set(plan.planes)) if evidence_source in (PROVENANCE, LINEAGE, USER, USER_LINEAGE) else []
    if recorded and observed_cut_planes and set(recorded).isdisjoint(observed_cut_planes):
        # Provenance that names another plane than the one the rim is on (an
        # x cut recorded as z0, say) is shown, never obeyed.
        conflicts.append(
            {
                "source": evidence_source,
                "kind": "plane-mismatch",
                "recorded_planes": recorded,
                "observed_cut_planes": observed_cut_planes,
            }
        )

    # -- the frame, and refusals
    frame = _proper_frame(normalisation)
    refusal: dict[str, Any] | None = None
    if open_rim is not None:
        reason = "; ".join(
            str(item.get("message")) for item in failures.get(open_rim[0]) or [] if item.get("message")
        )
        refusal = {
            "code": OPEN_HALF_CODE,
            "message": open_half_refusal_message(*open_rim, reason=reason or None),
        }
    elif off_centre:
        first = off_centre[0]
        refusal = {
            "code": OPEN_HALF_CODE,
            "message": off_centre_cut_refusal_message(
                str(first["plane_axis"]), float(first["offset_mm"]), int(first["rim_edges"])
            ),
        }
    elif not frame["proper"]:
        refusal = {
            "code": "imported_frame_improper",
            "message": "The solver frame this model was meshed in is not a proper rotation; "
            "send the model again.",
        }

    # -- the reading of the input
    judged = interpretation.get("conclusions") or {}
    possible_cap = any("possible-cap" in (item.get("reasons") or []) for item in judged.values())
    candidate = any(item.get("status") == "candidate" for item in judged.values())
    if evidence_source == DECLARATION or mirrored or open_rim is not None or off_centre:
        input_reading = INPUT_CUT
    elif observed_json is None or possible_cap or candidate:
        # A face that may cap a cut or be a wall, or a clean positive-side rim
        # that does not span the shell: geometry alone cannot say.
        input_reading = INPUT_UNRESOLVED
    elif other_open or any(
        int(item.get("rim_edges") or 0) >= MIN_RIM_EDGES and not item.get("wg_cut")
        for item in observed_planes.values()
    ):
        input_reading = INPUT_OPEN_SHEET
    else:
        input_reading = INPUT_FULL

    if refusal is not None:
        confidence = CONFIDENCE_REFUSED
    elif recovered:
        # Geometry alone establishes the cut once every flip condition holds.
        confidence = CONFIDENCE_ESTABLISHED
    elif mirrored or evidence_source == DECLARATION:
        confidence = CONFIDENCE_EVIDENCED
    elif input_reading in (INPUT_FULL, INPUT_OPEN_SHEET):
        confidence = CONFIDENCE_ESTABLISHED
    else:
        confidence = CONFIDENCE_UNRESOLVED

    fraction = _FRACTION.get(len(domain_planes), "unsupported")
    decision: dict[str, Any] = {
        "contract": CONTRACT,
        "input_reading": input_reading,
        "resolved_reading": reading,
        "cad_cuts": cad_cuts,
        "off_centre_cuts": off_centre,
        "wg_cut_planes": wg_cut_planes,
        "solver_domain": {
            "planes": domain_planes,
            "fraction": fraction,
            "multiplier": 2 ** len(domain_planes),
        },
        "reflected_axes": reflection_axes,
        "reflection": {
            "implemented": True,
            "planes": [str(plane) for plane in (reflection or {}).get("planes") or []],
            "axes": reflection_axes,
            "parity": int((reflection or {}).get("parity") or 0),
            "winding_reversed": bool((reflection or {}).get("winding_reversed")),
            "by": (reflection or {}).get("by"),
        },
        "frame": frame,
        "sources": _source_mapping(
            source_tags,
            post_cut_source_areas,
            skipped_source_ids,
            observed_planes,
            reflection=reflection,
            channels=channels,
        ),
        "confidence": confidence,
        "evidence": {
            "supporting": supporting,
            "conflicts": conflicts,
            "ignored": [dict(item) for item in plan.ignored],
        },
        "offered_changes": [dict(choice) for choice in interpretation.get("choices") or []],
        "refusal": refusal,
        "identity": {
            "snapshot_sha256": manifest_sha256,
            "observations_sha256": _sha256(observed_json),
            "choice_sha256": _sha256(plan.identity()),
            "mesh_content_sha256": mesh_content_sha256,
            "source_map_sha256": source_map_sha256(source_tags, tag_map),
        },
    }
    decision = json.loads(_canonical(decision))
    decision["identity"]["decision_sha256"] = decision_sha256(decision)
    return decision


def source_map_sha256(source_tags: Mapping[str, Any], tag_map: Mapping[str, Any]) -> str:
    """Which mesh tag is which source identity: what the solver drives, as a hash."""

    return str(_sha256({"source_tags": dict(source_tags), "tag_map": dict(tag_map)}))


def _source_problem(decision: Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    """Why the record's source identities are not the ones the decision was made on."""

    identity = decision.get("identity") or {}
    record_tags = record.get("source_tags")
    record_tags = record_tags if isinstance(record_tags, Mapping) else {}
    record_map = record.get("tag_map")
    record_map = record_map if isinstance(record_map, Mapping) else {}
    if identity.get("source_map_sha256") != source_map_sha256(record_tags, record_map):
        return "its domain decision was made on another source-to-tag mapping"
    sources = decision.get("sources")
    by_id = sources.get("by_id") if isinstance(sources, Mapping) else None
    if not isinstance(by_id, Mapping):
        return "its domain decision names no sources"
    held = {str(key): (item or {}).get("tag") for key, item in by_id.items()}
    try:
        current = {str(key): int(value) for key, value in record_tags.items()}
    except (TypeError, ValueError):
        return "its source tags are unreadable"
    if held != current:
        return "its domain decision maps the sources to other tags than the record"
    areas = record.get("post_cut_source_areas")
    areas = areas if isinstance(areas, Mapping) else {}
    for source_id, item in by_id.items():
        kept = areas.get(source_id)
        kept = kept.get("retained_fraction") if isinstance(kept, Mapping) else None
        if (item or {}).get("retained_fraction") != kept:
            return f"its domain decision keeps another part of source {source_id} than the record"
    skipped = sources.get("skipped") if isinstance(sources, Mapping) else None
    if sorted(str(item) for item in skipped or []) != sorted(
        str(item) for item in record.get("skipped_source_ids") or []
    ):
        return "its domain decision skips other sources than the record"
    return None


def decision_sha256(decision: Mapping[str, Any]) -> str:
    """The decision's own identity: everything in it but this hash."""

    body = json.loads(_canonical(dict(decision)))
    identity = dict(body.get("identity") or {})
    identity.pop("decision_sha256", None)
    body["identity"] = identity
    return str(_sha256(body))


def decision_problem(record: Mapping[str, Any]) -> str | None:
    """Why this record's decision does not describe the record, or None.

    None for a record without a decision (an earlier build): its readers
    derive what they need from the record as before. A decision that exists
    must be intact, must name this snapshot and this solve mesh, must mirror
    exactly the planes the solver will, must be the decision about these
    observations, and its frame must be the record's.
    """

    decision = record.get("domain_decision")
    if decision is None:
        return None
    if not isinstance(decision, Mapping) or decision.get("contract") != CONTRACT:
        return "its domain decision is missing or from an unknown contract"
    identity = decision.get("identity")
    if not isinstance(identity, Mapping):
        return "its domain decision has no identity"
    try:
        intact = identity.get("decision_sha256") == decision_sha256(decision)
    except (TypeError, ValueError):
        intact = False
    if not intact:
        return "its domain decision does not match its own hash"
    if identity.get("snapshot_sha256") != record.get("manifest_sha256"):
        return "its domain decision is about another snapshot"
    if identity.get("mesh_content_sha256") != record.get("mesh_content_sha256"):
        return "its domain decision is about another solve mesh"
    from server.solver.imported import imported_domain_planes

    domain = decision.get("solver_domain")
    planes = list(domain.get("planes") or []) if isinstance(domain, Mapping) else None
    if planes != list(imported_domain_planes(record)):
        return "its domain decision mirrors other planes than the record"
    interpretation = record.get("domain_interpretation")
    observations = interpretation.get("observations") if isinstance(interpretation, Mapping) else None
    if identity.get("observations_sha256") != _sha256(observations):
        return "its domain decision was made on other observations"
    normalisation = record.get("normalisation")
    matrix = normalisation.get("matrix") if isinstance(normalisation, Mapping) else None
    frame = decision.get("frame")
    held = frame.get("solver_from_cad") if isinstance(frame, Mapping) else None
    if matrix is not None and (held is None or not np.array_equal(np.asarray(held, float), np.asarray(matrix, float))):
        return "its domain decision was made in another solver frame"
    return _source_problem(decision, record)


def job_decision_problem(
    task_metadata: Mapping[str, Any] | None, record: Mapping[str, Any]
) -> str | None:
    """Why a saved job's decision is not the ingestion record's, or None.

    A job records the decision it was submitted under (:func:`decision_summary`).
    Execution, a restart and a retry solve the record again, so they solve
    only that very decision. A job saved before decisions existed has no such
    entry and is judged by the record alone, as before.
    """

    imported = (task_metadata or {}).get("imported_geometry")
    if not isinstance(imported, Mapping) or "domain_decision" not in imported:
        return None
    saved = imported.get("domain_decision")
    decision = record.get("domain_decision")
    saved_sha = saved.get("decision_sha256") if isinstance(saved, Mapping) else None
    current_sha = (
        (decision.get("identity") or {}).get("decision_sha256")
        if isinstance(decision, Mapping)
        else None
    )
    if saved_sha != current_sha:
        return "the job was submitted under another domain decision than its ingestion record's"
    if saved is not None and dict(saved) != decision_summary(record):
        return "the job's recorded domain decision differs from its ingestion record's"
    return None


def decision_mismatch_message(problem: str) -> str:
    return (
        f"WG will not solve this CAD return: {problem}. Send the model again so WG "
        "prepares it and decides its domain afresh."
    )


def decision_refusal(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """The refusal this record's (intact) decision states, as ``(code, message)``."""

    decision = record.get("domain_decision")
    refusal = decision.get("refusal") if isinstance(decision, Mapping) else None
    if not isinstance(refusal, Mapping):
        return None
    return str(refusal.get("code")), str(refusal.get("message"))


def decision_summary(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """What a job and the Solve card record of the decision: the values the solve uses."""

    decision = record.get("domain_decision")
    if not isinstance(decision, Mapping):
        return None
    identity = decision.get("identity") or {}
    return {
        "contract": decision.get("contract"),
        "decision_sha256": identity.get("decision_sha256"),
        "input_reading": decision.get("input_reading"),
        "resolved_reading": decision.get("resolved_reading"),
        "cad_cuts": [
            {
                key: cut.get(key)
                for key in ("plane", "solver_plane", "kept_side", "found_by", "status", "reflected")
            }
            for cut in decision.get("cad_cuts") or []
        ],
        "wg_cut_planes": list(decision.get("wg_cut_planes") or []),
        "solver_domain": dict(decision.get("solver_domain") or {}),
        "reflected_axes": list(decision.get("reflected_axes") or []),
        "reflection": {
            key: (decision.get("reflection") or {}).get(key)
            for key in ("axes", "parity", "winding_reversed")
        },
        "frame": {
            key: (decision.get("frame") or {}).get(key)
            for key in ("solver_from_cad", "determinant", "proper", "axis", "up")
        },
        "sources": {
            source_id: {key: item.get(key) for key in ("tag", "retained_fraction")}
            for source_id, item in ((decision.get("sources") or {}).get("by_id") or {}).items()
        },
        "confidence": decision.get("confidence"),
        "refusal": decision.get("refusal"),
    }


__all__ = [
    "CONTRACT",
    "INPUT_CUT",
    "INPUT_FULL",
    "INPUT_OPEN_SHEET",
    "INPUT_UNRESOLVED",
    "MISMATCH_CODE",
    "OPEN_HALF_CODE",
    "REFLECTION_PENDING",
    "decide_domain_and_frame",
    "decision_mismatch_message",
    "decision_problem",
    "decision_refusal",
    "decision_sha256",
    "decision_summary",
    "job_decision_problem",
    "source_map_sha256",
]
