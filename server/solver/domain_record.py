"""The one solve-domain record every solve path writes (``domain-decision-v2``).

Stage 1 of the unified symmetry process. Every path that solves a model --
a parametric design, a CAD return (linked to a WG design or authored in CAD),
an imported mesh file and a compiled design assembly -- states its solve
domain in the same record:

``producer``
    Which path wrote it.
``solver_domain``
    The planes the solver mirrors, the fraction of the model the mesh is and
    the multiplier (the shape ``cad-domain-decision-v1`` already had).
``planes``
    Per mirror plane (``x0``, ``y0``): what the geometry said
    (``accepted``, ``rejected``, ``not-tested`` or ``declared``), the evidence
    behind it (tolerance, residual, points off the model), whether the solve
    uses it, and what dropped it when the geometry allowed it but the solve
    does not (``dropped_by``).
``expected``
    The domain the model was expected to give: the design's own verdict for a
    parametric design and for a linked CAD return (WG's verdict for the
    design config the CAD app echoed back), the geometry's for a model
    authored in CAD, the declaration for a mesh file; None when nothing says.
``findings``
    A non-blocking ``symmetry-plane-lost`` finding when the solve keeps fewer
    planes than expected for a reason the user did not choose, saying in
    plain words which plane was lost and why.

A CAD return seals this record in its ingestion record (it extends
``cad-domain-decision-v1``, whose fields stay as they were, so its readers
keep working); a job records it for every path (for a CAD return, its
shared fields, ``record_core``, beside the v1 summary it already kept). Nothing here changes a mesh,
a solve or an export: it states what was decided.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
from typing import Any

from server.contracts.geometry import (
    CUT_PLANE_BY_NATIVE_PLANE,
    CUT_PLANES_BY_QUADRANTS,
    SUPPORTED_CUT_PLANES,
    normalise_quadrants,
)


CONTRACT = "domain-decision-v2"
#: The CAD decision this record extends. Readers accept both.
CAD_V1_CONTRACT = "cad-domain-decision-v1"
CONTRACTS = frozenset({CONTRACT, CAD_V1_CONTRACT})

PRODUCER_PARAMETRIC = "parametric"
PRODUCER_CAD_LINKED = "cad-linked"
PRODUCER_CAD_UNLINKED = "cad-unlinked"
PRODUCER_FILE_MESH = "file-mesh"
PRODUCER_DESIGN_ASSEMBLY = "design-assembly"

GEOMETRY_ACCEPTED = "accepted"
GEOMETRY_REJECTED = "rejected"
GEOMETRY_NOT_TESTED = "not-tested"
GEOMETRY_DECLARED = "declared"

#: The design check (``server/solver/symmetry.py``, 2e-4 of the size) and the
#: CAD check (the mesher's OCC sampling, 5e-4: fitted surfaces).
TOLERANCE_EXACT = "exact"
TOLERANCE_FITTED = "fitted"

DROPPED_BY_GROUND_PLANE = "ground-plane"
DROPPED_BY_ENGINE = "engine"
DROPPED_BY_FEM = "fem"
DROPPED_BY_VERIFICATION = "verification"
DROPPED_BY_USER_MODE = "user-mode"
DROPPED_BY_WOOFER = "woofer"
DROPPED_BY_NATIVE_FORMULA = "native-formula"

#: Losses the user chose (a forced Symmetry, a ground plane): the run's own
#: settings say so, so they are recorded but raise no finding.
USER_CHOSEN = frozenset({DROPPED_BY_USER_MODE, DROPPED_BY_GROUND_PLANE})

EXPECTED_BY_DESIGN = "design"
EXPECTED_BY_GEOMETRY = "geometry"
EXPECTED_BY_DECLARATION = "declaration"

LOST_PLANE_KIND = "symmetry-plane-lost"

_FRACTION = {0: "full", 1: "half", 2: "quarter"}
_FRACTION_WORDS = {"full": "the whole model", "half": "a half", "quarter": "a quarter"}


def ordered_planes(planes: Iterable[str]) -> list[str]:
    """The mirror planes among ``planes``, in x0, y0 order."""

    found = {str(plane) for plane in planes}
    return [plane for plane in SUPPORTED_CUT_PLANES if plane in found]


def solver_domain(planes: Sequence[str]) -> dict[str, Any]:
    """The planes the solver mirrors (in the order given), its fraction and multiplier."""

    planes = [str(plane) for plane in planes]
    return {
        "planes": planes,
        "fraction": _FRACTION.get(len(planes), "unsupported"),
        "multiplier": 2 ** len(planes),
    }


def plane_entry(
    geometry: str,
    *,
    used: bool,
    dropped_by: str | None = None,
    tolerance_mm: float | None = None,
    tolerance_class: str | None = None,
    max_residual_mm: float | None = None,
    points_tested: int | None = None,
    points_off: int | None = None,
    worst_off_mm: float | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """One plane's verdict and evidence."""

    return {
        "geometry": geometry,
        "used": bool(used),
        "dropped_by": dropped_by,
        "tolerance_mm": _number(tolerance_mm),
        "tolerance_class": tolerance_class,
        "max_residual_mm": _number(max_residual_mm),
        "points_tested": _count(points_tested),
        "points_off": _count(points_off),
        "worst_off_mm": _number(worst_off_mm),
        "detail": detail,
    }


def expectation(
    planes: Iterable[str], *, by: str, detail: str | None = None
) -> dict[str, Any]:
    """The domain the model was expected to give, and what says so."""

    ordered = ordered_planes(planes)
    return {
        "planes": ordered,
        "fraction": _FRACTION[len(ordered)],
        "by": by,
        "detail": detail,
    }


def resolution_planes(resolution: Any) -> list[str]:
    """The mirror planes a ``SymmetryResolution`` accepts, as cut planes."""

    return ordered_planes(
        CUT_PLANE_BY_NATIVE_PLANE[native]
        for native in ("yz", "xz")
        if bool(getattr(resolution, native))
    )


def design_expectation(resolution: Any) -> dict[str, Any]:
    """What a design gives, from WG's own verdict for it (``resolve_symmetry``)."""

    return expectation(resolution_planes(resolution), by=EXPECTED_BY_DESIGN)


def geometry_expectation(planes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """What a model's own geometry gives: the planes it accepted or declared.

    None when no plane was tested or declared: nothing to expect.
    """

    judged = [
        plane for plane, entry in planes.items()
        if entry.get("geometry") != GEOMETRY_NOT_TESTED
    ]
    if not judged:
        return None
    return expectation(
        (
            plane for plane, entry in planes.items()
            if entry.get("geometry") in (GEOMETRY_ACCEPTED, GEOMETRY_DECLARED)
        ),
        by=EXPECTED_BY_GEOMETRY,
    )


def decision_record(
    *,
    producer: str,
    domain_planes: Sequence[str],
    planes: Mapping[str, Mapping[str, Any]],
    expected: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose the record; its finding is derived from the record itself.

    ``extra`` carries a producer's own fields (the CAD decision's v1 fields,
    a frame); they never override the record's own keys.
    """

    record: dict[str, Any] = dict(extra or {})
    record.update(
        {
            "contract": CONTRACT,
            "producer": producer,
            "solver_domain": solver_domain(domain_planes),
            "planes": {plane: dict(planes[plane]) for plane in ordered_planes(planes)},
            "expected": dict(expected) if expected is not None else None,
        }
    )
    finding = lost_plane_finding(record)
    record["findings"] = [finding] if finding is not None else []
    return record


# -- the paths ---------------------------------------------------------------


def parametric_decision(
    geometry: Any,
    *,
    resolved_quadrants: int,
    requested_mode: str,
    ground_blocked_plane: str | None,
    native_formula: bool,
    engine_reason: str | None = None,
) -> dict[str, Any]:
    """A parametric design's record.

    ``geometry`` is ``resolve_symmetry``'s verdict before any solve constraint
    (the design's own planes, which is also ``expected``);
    ``ground_blocked_plane`` the cut plane a ground plane blocks;
    ``engine_reason`` set when the engine overrode the domain to full.
    """

    used = set(CUT_PLANES_BY_QUADRANTS[normalise_quadrants(resolved_quadrants)])
    accepted = set(resolution_planes(geometry))
    native_by_plane = {cut: native for native, cut in CUT_PLANE_BY_NATIVE_PLANE.items()}
    reasons = getattr(geometry, "reasons", {}) or {}
    tolerance = _number(getattr(geometry, "tolerance_mm", None))
    planes: dict[str, dict[str, Any]] = {}
    for plane in SUPPORTED_CUT_PLANES:
        detail = "; ".join(str(item) for item in reasons.get(native_by_plane[plane]) or []) or None
        if native_formula:
            planes[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=plane in used,
                dropped_by=DROPPED_BY_NATIVE_FORMULA, detail=detail,
            )
            continue
        dropped_by: str | None = None
        if plane in accepted and plane not in used:
            if plane == ground_blocked_plane:
                dropped_by = DROPPED_BY_GROUND_PLANE
                detail = "a rigid ground plane bounds this plane"
            elif engine_reason:
                dropped_by = DROPPED_BY_ENGINE
                detail = engine_reason
            else:
                dropped_by = DROPPED_BY_USER_MODE
                detail = f"Symmetry is set to {requested_mode}"
        planes[plane] = plane_entry(
            GEOMETRY_ACCEPTED if plane in accepted else GEOMETRY_REJECTED,
            used=plane in used,
            dropped_by=dropped_by,
            tolerance_mm=tolerance,
            tolerance_class=TOLERANCE_EXACT,
            detail=detail,
        )
    return decision_record(
        producer=PRODUCER_PARAMETRIC,
        domain_planes=ordered_planes(used),
        planes=planes,
        expected=None if native_formula else design_expectation(geometry),
    )


def cad_plane_entries(
    symmetry: Mapping[str, Any],
    *,
    domain_planes: Sequence[str],
    verification: Mapping[str, Any] | None = None,
    fem_rejected: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-plane verdicts for a CAD return, from what the preparation recorded.

    ``symmetry`` is the mesher's report (``built["symmetry"]``: the OCC mirror
    verdicts, in STEP units, which WG reads in millimetres); ``verification``
    the post-mesh check with its fallback; ``fem_rejected`` the planes a FEM
    region refused, with the reason.
    """

    used = {str(plane) for plane in domain_planes}
    declared = {str(plane) for plane in symmetry.get("declared_cut_planes") or []}
    report = symmetry.get("planes")
    report = report if isinstance(report, Mapping) else {}
    fallback = (verification or {}).get("fallback")
    fallback = fallback if isinstance(fallback, Mapping) else {}
    fallback_planes = {
        str(plane)
        for plane in [
            *(fallback.get("rejected_cut_planes") or []),
            *(fallback.get("detected_planes") or []),
        ]
    }
    fem_rejected = fem_rejected or {}
    requested_full = symmetry.get("requested_mode") == "full"
    tolerance = _number(symmetry.get("tolerance_step_units"))
    entries: dict[str, dict[str, Any]] = {}
    for plane in SUPPORTED_CUT_PLANES:
        verdict = report.get(plane)
        verdict = verdict if isinstance(verdict, Mapping) else None
        if plane in declared:
            entries[plane] = plane_entry(
                GEOMETRY_DECLARED, used=plane in used,
                detail=str((verdict or {}).get("source") or "declared-by-cad-author"),
            )
        elif verdict is not None and "accepted" in verdict and "points_tested" in verdict:
            accepted = bool(verdict.get("accepted"))
            entries[plane] = plane_entry(
                GEOMETRY_ACCEPTED if accepted else GEOMETRY_REJECTED,
                used=plane in used,
                dropped_by=DROPPED_BY_VERIFICATION if accepted and plane not in used else None,
                tolerance_mm=_number(verdict.get("tolerance_step_units")) or tolerance,
                tolerance_class=TOLERANCE_FITTED,
                max_residual_mm=verdict.get("max_residual_step_units"),
                points_tested=verdict.get("points_tested"),
                points_off=verdict.get("points_off_model"),
                worst_off_mm=verdict.get("worst_off_model_distance_step_units"),
                detail=str(verdict.get("reason")) if verdict.get("reason") else None,
            )
        elif plane in fallback_planes:
            # WG's mirror test accepted and cut it; the reduced mesh then
            # failed its check and the whole model was meshed instead.
            entries[plane] = plane_entry(
                GEOMETRY_ACCEPTED, used=plane in used,
                dropped_by=DROPPED_BY_VERIFICATION,
                detail=str(fallback.get("reason")) if fallback.get("reason") else None,
            )
        elif plane in fem_rejected:
            entries[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=plane in used,
                dropped_by=DROPPED_BY_FEM, detail=fem_rejected[plane],
            )
        else:
            entries[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=plane in used,
                dropped_by=DROPPED_BY_USER_MODE if requested_full else None,
                detail=(
                    "Symmetry is set to full"
                    if requested_full
                    else str(symmetry.get("note")) if symmetry.get("note") else None
                ),
            )
    return entries


def file_mesh_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    """An imported mesh file's record: the planes its import declared."""

    from server.solver.imported import imported_domain_planes

    domain = list(imported_domain_planes(record))
    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    cut = {str(plane) for plane in symmetry.get("cut_planes") or []}
    planes = {
        plane: plane_entry(
            GEOMETRY_ACCEPTED if plane in cut else GEOMETRY_DECLARED if plane in domain
            else GEOMETRY_NOT_TESTED,
            used=plane in domain,
            detail=None if plane in domain else "a mesh file is solved as declared on import",
        )
        for plane in SUPPORTED_CUT_PLANES
    }
    return decision_record(
        producer=PRODUCER_FILE_MESH,
        domain_planes=domain,
        planes=planes,
        expected=expectation(domain, by=EXPECTED_BY_DECLARATION),
    )


def design_assembly_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    """A compiled design assembly's record: always the whole model in this version."""

    from server.solver.imported import imported_domain_planes

    domain = list(imported_domain_planes(record))
    planes = {
        plane: plane_entry(
            GEOMETRY_NOT_TESTED, used=plane in domain,
            dropped_by=None if plane in domain else DROPPED_BY_WOOFER,
            detail=None if plane in domain else "a design with woofers is solved as the whole model",
        )
        for plane in SUPPORTED_CUT_PLANES
    }
    return decision_record(
        producer=PRODUCER_DESIGN_ASSEMBLY,
        domain_planes=domain,
        planes=planes,
        expected=None,
    )


#: The fields every path's record has; a CAD decision adds its own beside them.
RECORD_FIELDS = ("contract", "producer", "solver_domain", "planes", "expected", "findings")


def record_core(decision: Mapping[str, Any]) -> dict[str, Any]:
    """The fields every path shares, from a CAD decision: what a job keeps of it.

    The job already keeps the decision's v1 summary (``decision_summary``);
    ``decision_sha256`` names the sealed decision this is part of.
    """

    core = {key: decision.get(key) for key in RECORD_FIELDS}
    identity = decision.get("identity")
    core["decision_sha256"] = identity.get("decision_sha256") if isinstance(identity, Mapping) else None
    return core


def imported_decision(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The record an imported solve writes for its job, from its ingestion record.

    A CAD return's is the shared part of the v2 decision its ingestion sealed
    (None for a record an earlier build sealed as v1, or none: its job keeps
    that build's summary); a mesh file's and a design assembly's are composed
    here.
    """

    producer = record.get("producer")
    if producer == "mesh-file":
        return file_mesh_decision(record)
    if producer == "design-assembly":
        return design_assembly_decision(record)
    decision = record.get("domain_decision")
    if isinstance(decision, Mapping) and decision.get("contract") == CONTRACT:
        return record_core(decision)
    return None


# -- the finding -------------------------------------------------------------


def lost_planes(record: Mapping[str, Any]) -> list[str]:
    """The planes the record expected that its solve does not mirror."""

    expected = record.get("expected")
    if not isinstance(expected, Mapping):
        return []
    domain = record.get("solver_domain")
    solved = set((domain or {}).get("planes") or []) if isinstance(domain, Mapping) else set()
    return [plane for plane in ordered_planes(expected.get("planes") or []) if plane not in solved]


def plane_words(plane: str) -> str:
    return f"{plane[0]} = 0"


def _plane_loss_reason(entry: Mapping[str, Any]) -> str:
    geometry = entry.get("geometry")
    dropped_by = entry.get("dropped_by")
    detail = entry.get("detail")
    tolerance = entry.get("tolerance_mm")
    if geometry == GEOMETRY_REJECTED:
        off, tested, worst = entry.get("points_off"), entry.get("points_tested"), entry.get("worst_off_mm")
        tolerance_words = f" (tolerance {_mm(tolerance)})" if tolerance is not None else ""
        if off and tested and worst is not None:
            return (
                f"its mirror check failed by up to {_mm(worst)}: {off} of {tested} "
                f"sampled points do not mirror onto the model{tolerance_words}"
            )
        residual = entry.get("max_residual_mm")
        if residual is not None:
            return f"its mirror check failed by up to {_mm(residual)}{tolerance_words}"
        return f"its mirror check failed ({detail})" if detail else "its mirror check failed"
    if dropped_by == DROPPED_BY_VERIFICATION:
        return (
            f"the model cut there did not pass the mesh check ({detail})"
            if detail else "the model cut there did not pass the mesh check"
        )
    if dropped_by == DROPPED_BY_FEM:
        return f"a FEM region does not allow it ({detail})" if detail else "a FEM region does not allow it"
    if dropped_by == DROPPED_BY_ENGINE:
        return f"the engine needs the whole model ({detail})" if detail else "the engine needs the whole model"
    if dropped_by == DROPPED_BY_WOOFER:
        return "a design with woofers is solved as the whole model"
    if dropped_by == DROPPED_BY_NATIVE_FORMULA:
        return "this formula is solved as the whole model"
    if geometry == GEOMETRY_NOT_TESTED:
        return f"WG did not test it ({detail})" if detail else "WG did not test it"
    return detail or "WG did not use it"


def lost_plane_finding(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The non-blocking finding a lost plane raises, or None.

    A plane the user's own settings removed (a forced Symmetry, a ground
    plane) is recorded in ``planes`` but raises nothing: the run says so.
    """

    lost = lost_planes(record)
    planes = record.get("planes")
    planes = planes if isinstance(planes, Mapping) else {}
    entries = {plane: planes.get(plane) if isinstance(planes.get(plane), Mapping) else {} for plane in lost}
    lost = [plane for plane in lost if entries[plane].get("dropped_by") not in USER_CHOSEN]
    if not lost:
        return None
    expected = record["expected"]
    domain = record.get("solver_domain") or {}
    expected_fraction = str(expected.get("fraction"))
    solved_fraction = str(domain.get("fraction"))
    solved_words = _FRACTION_WORDS.get(solved_fraction, solved_fraction)
    producer = record.get("producer")
    if expected.get("by") == EXPECTED_BY_DESIGN:
        subject = (
            "the returned CAD model"
            if producer in (PRODUCER_CAD_LINKED, PRODUCER_CAD_UNLINKED)
            else "it"
        )
        opening = (
            f"This design is {_FRACTION_WORDS.get(expected_fraction, expected_fraction)} model, "
            f"but {subject} solved as {solved_words}"
        )
    else:
        mirrored = " and ".join(plane_words(plane) for plane in expected.get("planes") or [])
        opening = f"The model mirrors at {mirrored}, but it solved as {solved_words}"
    losses = "; ".join(
        f"the {plane_words(plane)} mirror was lost because {_plane_loss_reason(entries[plane])}"
        for plane in lost
    )
    return {
        "kind": LOST_PLANE_KIND,
        "blocking": False,
        "lost_planes": lost,
        "expected_fraction": expected_fraction,
        "solved_fraction": solved_fraction,
        "detail": f"{opening}: {losses}.",
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _mm(value: Any) -> str:
    number = _number(value)
    return "an unknown distance" if number is None else f"{number:.3g} mm"


__all__ = [
    "CAD_V1_CONTRACT",
    "CONTRACT",
    "CONTRACTS",
    "LOST_PLANE_KIND",
    "RECORD_FIELDS",
    "cad_plane_entries",
    "decision_record",
    "design_assembly_decision",
    "design_expectation",
    "expectation",
    "file_mesh_decision",
    "geometry_expectation",
    "imported_decision",
    "lost_plane_finding",
    "lost_planes",
    "parametric_decision",
    "plane_entry",
    "record_core",
    "solver_domain",
]
