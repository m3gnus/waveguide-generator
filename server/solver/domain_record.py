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
    DOMAIN_FRACTION_BY_PLANE_COUNT,
    SUPPORTED_CUT_PLANES,
    SYMMETRY_MODE_QUADRANTS,
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
#: WG could not rebuild a model cut in CAD (``cut_recovery``) and solved it whole.
DROPPED_BY_RECOVERY = "recovery"
#: Nothing recorded says why: never silent.
DROPPED_BY_UNKNOWN = "unknown"

#: Losses recorded but never shown as a note: the user's own settings (a
#: forced Symmetry, a ground plane) and limits of the engine or the model kind
#: the user has no fix for. The run's own settings and plan say so already.
SILENT_LOSSES = frozenset({
    DROPPED_BY_USER_MODE, DROPPED_BY_GROUND_PLANE, DROPPED_BY_ENGINE,
    DROPPED_BY_NATIVE_FORMULA, DROPPED_BY_WOOFER,
})

EXPECTED_BY_DESIGN = "design"
EXPECTED_BY_GEOMETRY = "geometry"
EXPECTED_BY_DECLARATION = "declaration"

LOST_PLANE_KIND = "symmetry-plane-lost"

_FRACTION_WORDS = {"full": "the whole model", "half": "a half", "quarter": "a quarter"}
#: The planes in the user's words (decision 6's "top/bottom mirror").
_MIRROR_WORDS = {"x0": "left/right (x = 0)", "y0": "top/bottom (y = 0)"}


def ordered_planes(planes: Iterable[str]) -> list[str]:
    """The mirror planes among ``planes``, in x0, y0 order."""

    found = {str(plane) for plane in planes}
    return [plane for plane in SUPPORTED_CUT_PLANES if plane in found]


def solver_domain(planes: Sequence[str]) -> dict[str, Any]:
    """The planes the solver mirrors (in the order given), its fraction and multiplier."""

    planes = [str(plane) for plane in planes]
    return {
        "planes": planes,
        "fraction": DOMAIN_FRACTION_BY_PLANE_COUNT.get(len(planes), "unsupported"),
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
    dropped_with: Iterable[str] = (),
    role_mismatches: int | None = None,
) -> dict[str, Any]:
    """One plane's verdict and evidence.

    ``dropped_with`` names the planes whose failure took this one with it (an
    all-or-nothing fallback, a half the solve cannot use alone);
    ``role_mismatches`` counts mirrored samples that land on a surface of
    another role (a driver mirrored onto a wall); ``detail`` is the raw
    evidence, for developers, never a user's sentence.
    """

    return {
        "geometry": geometry,
        "used": bool(used),
        "dropped_by": dropped_by,
        "tolerance_mm": _number(tolerance_mm),
        "tolerance_class": tolerance_class,
        "max_residual_mm": _number(max_residual_mm),
        "points_tested": _count(points_tested),
        "points_off": _count(points_off),
        "role_mismatches": _count(role_mismatches),
        "worst_off_mm": _number(worst_off_mm),
        "detail": detail,
        "dropped_with": ordered_planes(dropped_with),
    }


def expectation(
    planes: Iterable[str], *, by: str, detail: str | None = None
) -> dict[str, Any]:
    """The domain the model was expected to give, and what says so."""

    ordered = ordered_planes(planes)
    return {
        "planes": ordered,
        "fraction": DOMAIN_FRACTION_BY_PLANE_COUNT[len(ordered)],
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


def design_expectation(resolution: Any, *, sampled: bool) -> dict[str, Any] | None:
    """What a design gives, from WG's own verdict for it (``resolve_symmetry``).

    None when the design was not sampled (a native formula, a sampling
    failure; ``symmetry.resolution_was_sampled``): its "full" means unknown.
    """

    return expectation(resolution_planes(resolution), by=EXPECTED_BY_DESIGN) if sampled else None


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
    sampled: bool,
    resolved_quadrants: int,
    requested_mode: str,
    ground_blocked_plane: str | None,
    native_formula: bool,
    engine_reason: str | None = None,
) -> dict[str, Any]:
    """A parametric design's record.

    ``geometry`` is ``resolve_symmetry``'s verdict before any solve constraint
    (the design's own planes, which is also ``expected``), ``sampled`` whether
    it was really sampled; ``ground_blocked_plane`` the cut plane a ground
    plane blocks; ``engine_reason`` set when the engine overrode the domain.
    A plane a forced Symmetry leaves out is the user's choice, whatever the
    engine would also have done.
    """

    used = set(CUT_PLANES_BY_QUADRANTS[normalise_quadrants(resolved_quadrants)])
    accepted = set(resolution_planes(geometry))
    mode = str(requested_mode).strip().lower()
    forced = SYMMETRY_MODE_QUADRANTS.get(mode) if mode != "auto" else None
    # The planes a forced Symmetry keeps (None: AUTO, nothing forced).
    forced_planes = (
        set(CUT_PLANES_BY_QUADRANTS[normalise_quadrants(forced)]) if forced is not None else None
    )
    native_by_plane = {cut: native for native, cut in CUT_PLANE_BY_NATIVE_PLANE.items()}
    reasons = getattr(geometry, "reasons", {}) or {}
    tolerance = _number(getattr(geometry, "tolerance_mm", None))
    planes: dict[str, dict[str, Any]] = {}
    for plane in SUPPORTED_CUT_PLANES:
        detail = "; ".join(str(item) for item in reasons.get(native_by_plane[plane]) or []) or None
        if native_formula or not sampled:
            planes[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=plane in used,
                dropped_by=DROPPED_BY_NATIVE_FORMULA if native_formula else None, detail=detail,
            )
            continue
        dropped_by: str | None = None
        if plane in accepted and plane not in used:
            if forced_planes is not None and plane not in forced_planes:
                dropped_by = DROPPED_BY_USER_MODE
                detail = f"Symmetry is set to {mode}"
            elif plane == ground_blocked_plane:
                dropped_by = DROPPED_BY_GROUND_PLANE
                detail = "a rigid ground plane bounds this plane"
            elif engine_reason:
                dropped_by = DROPPED_BY_ENGINE
                detail = engine_reason
            else:
                dropped_by = DROPPED_BY_UNKNOWN
                detail = None
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
        expected=None if native_formula else design_expectation(geometry, sampled=sampled),
    )


def _occ_verdict(verdict: Any) -> Mapping[str, Any] | None:
    """A plane's OCC mirror verdict, when it is one (``evaluate_occ_plane_symmetry``)."""

    if isinstance(verdict, Mapping) and "accepted" in verdict and "points_tested" in verdict:
        return verdict
    return None


def _verdict_entry(
    verdict: Mapping[str, Any], *, used: bool, tolerance: float | None,
    dropped_by: str | None = None, detail: str | None = None, dropped_with: Iterable[str] = (),
) -> dict[str, Any]:
    accepted = bool(verdict.get("accepted"))
    return plane_entry(
        GEOMETRY_ACCEPTED if accepted else GEOMETRY_REJECTED,
        used=used,
        dropped_by=dropped_by if accepted and not used else None,
        tolerance_mm=_number(verdict.get("tolerance_step_units")) or tolerance,
        tolerance_class=TOLERANCE_FITTED,
        max_residual_mm=verdict.get("max_residual_step_units"),
        points_tested=verdict.get("points_tested"),
        points_off=verdict.get("points_off_model"),
        worst_off_mm=verdict.get("worst_off_model_distance_step_units"),
        role_mismatches=verdict.get("role_mismatches"),
        detail=detail if detail is not None else (str(verdict.get("reason")) if verdict.get("reason") else None),
        dropped_with=dropped_with,
    )


def cad_plane_entries(
    symmetry: Mapping[str, Any],
    *,
    domain_planes: Sequence[str],
    verification: Mapping[str, Any] | None = None,
    fem_rejected: Mapping[str, Mapping[str, Any]] | None = None,
    recovery_failures: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-plane verdicts for a CAD return: only what the preparation knows.

    ``symmetry`` is the mesher's report of the mesh solved (``built["symmetry"]``:
    the OCC mirror verdicts, in STEP units, which WG reads in millimetres).
    When that mesh is a fallback (the whole model after a reduced one failed),
    the verdicts that chose the failed reduction travel in
    ``verification["fallback"]["plane_verdicts"]``, and a FEM refusal carries
    the verdict of the pass it refused (``fem_rejected[plane]["verdict"]``).
    ``fem_rejected`` names the planes a FEM region refused (``reason``,
    ``dropped_with``); ``recovery_failures`` the flip conditions a CAD cut
    failed (``cut_recovery``), after which the model is meshed whole.
    """

    used = {str(plane) for plane in domain_planes}
    declared = {str(plane) for plane in symmetry.get("declared_cut_planes") or []}
    report = symmetry.get("planes")
    report = report if isinstance(report, Mapping) else {}
    fallback = (verification or {}).get("fallback")
    fallback = fallback if isinstance(fallback, Mapping) else {}
    first_pass = fallback.get("plane_verdicts")
    first_pass = first_pass if isinstance(first_pass, Mapping) else {}
    # The planes WG cut and the mesh check then refused (an empty list with a
    # fallback: the cutter itself refused, and no verdict survives).
    cut_then_refused = {str(plane) for plane in fallback.get("rejected_cut_planes") or []}
    cut_refused = bool(fallback) and not cut_then_refused
    # Which of those cuts failed the check, when it says so: a capped plane, or
    # a cut plane the mesh is not open on. The others went with them.
    failing = {str(plane) for plane in fallback.get("capped_planes") or []} & cut_then_refused
    detected = fallback.get("detected_planes")
    if cut_then_refused and isinstance(detected, list):
        failing |= {plane for plane in cut_then_refused if plane not in detected}
    fem_rejected = fem_rejected or {}
    requested_full = symmetry.get("requested_mode") == "full"
    recovery_failed = bool(recovery_failures) and requested_full
    tolerance = _number(symmetry.get("tolerance_step_units"))
    entries: dict[str, dict[str, Any]] = {}
    for plane in SUPPORTED_CUT_PLANES:
        in_use = plane in used
        rejected = fem_rejected.get(plane)
        verdict = (
            _occ_verdict(report.get(plane))
            or _occ_verdict(first_pass.get(plane))
            or (_occ_verdict(rejected.get("verdict")) if isinstance(rejected, Mapping) else None)
        )
        if plane in declared:
            entries[plane] = plane_entry(
                GEOMETRY_DECLARED, used=in_use,
                detail=str((report.get(plane) or {}).get("source") or "declared-by-cad-author"),
            )
        elif isinstance(rejected, Mapping):
            # The FEM loop refuses only a plane WG's mirror test accepted and cut.
            fem = dict(
                used=in_use, dropped_by=DROPPED_BY_FEM,
                detail=str(rejected.get("reason") or "") or None,
                dropped_with=rejected.get("dropped_with") or (),
            )
            entries[plane] = (
                _verdict_entry(verdict, tolerance=tolerance, **fem)  # type: ignore[arg-type]
                if verdict is not None else plane_entry(GEOMETRY_ACCEPTED, **fem)  # type: ignore[arg-type]
            )
        elif plane in cut_then_refused:
            # WG's mirror test accepted and cut it; the reduced mesh then
            # failed its check and the whole model was meshed instead.
            check = dict(
                used=in_use, dropped_by=DROPPED_BY_VERIFICATION,
                detail=str(fallback.get("reason")) if fallback.get("reason") else None,
                dropped_with=sorted(failing) if failing and plane not in failing else (),
            )
            entries[plane] = (
                _verdict_entry(verdict, tolerance=tolerance, **check)  # type: ignore[arg-type]
                if verdict is not None else plane_entry(GEOMETRY_ACCEPTED, **check)  # type: ignore[arg-type]
            )
        elif verdict is not None:
            entries[plane] = _verdict_entry(
                verdict, used=in_use, tolerance=tolerance, dropped_by=DROPPED_BY_UNKNOWN,
            )
        elif cut_refused and plane in {str(item) for item in fallback.get("detected_planes") or []}:
            # The cutter refused its own cut: WG never learnt whether this
            # plane mirrors, only that it could not cut there.
            entries[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=in_use, dropped_by=DROPPED_BY_VERIFICATION,
                detail=str(fallback.get("reason")) if fallback.get("reason") else None,
            )
        elif recovery_failed:
            failed = (recovery_failures or {}).get(plane)
            entries[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=in_use,
                dropped_by=DROPPED_BY_RECOVERY,
                detail="; ".join(
                    str(item.get("message")) for item in failed or []
                    if isinstance(item, Mapping) and item.get("message")
                ) or "a CAD cut could not be rebuilt, so the model is meshed whole",
                dropped_with=() if failed else ordered_planes(recovery_failures or {}),
            )
        else:
            entries[plane] = plane_entry(
                GEOMETRY_NOT_TESTED, used=in_use,
                dropped_by=DROPPED_BY_USER_MODE if requested_full else None,
                detail=(
                    "Symmetry is set to full"
                    if requested_full
                    else str(symmetry.get("note")) if symmetry.get("note") else None
                ),
            )
    return entries


def file_mesh_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    """An imported mesh file's record: the planes its import declared or found.

    New imports are declaration only; a historical preparation may have cut
    the file itself (``auto_cut``), and those planes are the geometry's.
    """

    from server.solver.imported import imported_domain_planes

    domain = list(imported_domain_planes(record))
    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    source = record.get("source_file")
    source = source if isinstance(source, Mapping) else {}
    declared = {str(plane) for plane in source.get("domain_planes") or []}
    cut = {str(plane) for plane in symmetry.get("cut_planes") or []} - declared
    report = ((source.get("transform") or {}).get("symmetry_report") or {}) if isinstance(
        source.get("transform"), Mapping
    ) else {}
    note = str(report.get("note")) if isinstance(report, Mapping) and report.get("note") else None
    planes = {
        plane: plane_entry(
            GEOMETRY_DECLARED if plane in declared
            else GEOMETRY_ACCEPTED if plane in cut
            else GEOMETRY_NOT_TESTED,
            used=plane in domain,
            detail=None if plane in domain else note or "a mesh file is solved as declared on import",
        )
        for plane in SUPPORTED_CUT_PLANES
    }
    if source.get("auto_cut"):
        expected = geometry_expectation(planes)
    else:
        expected = expectation(domain, by=EXPECTED_BY_DECLARATION)
    return decision_record(
        producer=PRODUCER_FILE_MESH,
        domain_planes=domain,
        planes=planes,
        expected=expected,
    )


def design_assembly_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    """A compiled design assembly's record: always the whole model in this version."""

    from server.solver.imported import imported_domain_planes

    domain = list(imported_domain_planes(record))
    woofers = any(
        isinstance(radiator, Mapping) and radiator.get("role") == "LF"
        and radiator.get("state") in {"compiled", "rigid"}
        for radiator in record.get("radiators") or []
    )
    planes = {
        plane: plane_entry(
            GEOMETRY_NOT_TESTED, used=plane in domain,
            dropped_by=None if plane in domain else DROPPED_BY_WOOFER if woofers else None,
            detail=None if plane in domain else (
                "a design with woofers is solved as the whole model" if woofers
                else "a compiled design assembly is solved as the whole model"
            ),
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


def _mirrors(planes: Sequence[str]) -> str:
    words = " and ".join(_MIRROR_WORDS[plane] for plane in planes)
    return f"the {words} {'mirrors were' if len(planes) > 1 else 'mirror was'}"


def _plane_loss(
    plane: str, entry: Mapping[str, Any], *, producer: Any, expected_words: str
) -> tuple[str, str | None]:
    """Why a plane was lost, in the user's words, and what to do: ``(reason, fix)``.

    Built only from the record's structured evidence; the raw ``detail`` is
    developer evidence and never reaches the sentence.
    """

    geometry = entry.get("geometry")
    dropped_by = entry.get("dropped_by")
    with_planes = list(entry.get("dropped_with") or [])
    if with_planes:
        others = " and ".join(_MIRROR_WORDS[plane] for plane in with_planes)
        if dropped_by == DROPPED_BY_VERIFICATION:
            return (f"WG solves the whole model when one of its cuts fails its mesh check, "
                    f"and the {others} cut did"), None
        if dropped_by == DROPPED_BY_FEM:
            return (f"with the {others} mirror gone, a model with FEM regions cannot be "
                    "solved mirrored top/bottom only"), None
        return f"it went with the {others} mirror", None
    if geometry == GEOMETRY_REJECTED:
        fix = (
            f"If the model was not meant to change in CAD, update it from its design and send "
            f"it again to solve it as {expected_words}."
            if producer == PRODUCER_CAD_LINKED else None
        )
        off, tested, worst = entry.get("points_off"), entry.get("points_tested"), entry.get("worst_off_mm")
        tolerance, residual = entry.get("tolerance_mm"), entry.get("max_residual_mm")
        allows = f"; WG allows {_mm(tolerance)}" if tolerance is not None else ""
        if off and tested and worst is not None:
            return (f"the model differs from its mirror image by up to {_mm(worst)} "
                    f"({off} of {tested} checked points{allows})"), fix
        if entry.get("role_mismatches"):
            return ("a driver, port or other source surface mirrors onto a surface that "
                    "is not the same kind"), fix
        if residual is not None and tolerance is not None and residual > tolerance:
            return f"the model differs from its mirror image by up to {_mm(residual)}{allows}", fix
        return "the model does not mirror there", fix
    if dropped_by == DROPPED_BY_VERIFICATION:
        if geometry == GEOMETRY_NOT_TESTED:
            return "WG could not cut the model there", None
        return "WG's cut there did not pass its mesh check", None
    if dropped_by == DROPPED_BY_FEM:
        return ("a FEM region in the model does not mirror there",
                f"Make the FEM region symmetric about the {_MIRROR_WORDS[plane]} plane to "
                f"solve it as {expected_words}.")
    if dropped_by == DROPPED_BY_RECOVERY:
        return "WG could not rebuild the model cut in CAD there", "Send the uncut model from CAD."
    if geometry == GEOMETRY_NOT_TESTED:
        return "WG could not check this mirror for this model", None
    return "WG could not use it for this solve", None


def lost_plane_finding(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The non-blocking finding a lost plane raises, or None.

    Only for a loss the user can act on or should know about (a mirror or
    mesh check that failed, a FEM region, a CAD cut WG could not rebuild).
    A plane removed by the user's own settings or by a limit with no user fix
    (``SILENT_LOSSES``) is recorded in ``planes`` and raises nothing. One
    sentence: planes lost for the same reason share it.
    """

    lost = lost_planes(record)
    planes = record.get("planes")
    planes = planes if isinstance(planes, Mapping) else {}
    entries = {plane: planes.get(plane) if isinstance(planes.get(plane), Mapping) else {} for plane in lost}
    lost = [plane for plane in lost if entries[plane].get("dropped_by") not in SILENT_LOSSES]
    if not lost:
        return None
    expected = record["expected"]
    domain = record.get("solver_domain") or {}
    expected_fraction = str(expected.get("fraction"))
    solved_fraction = str(domain.get("fraction"))
    expected_words = _FRACTION_WORDS.get(expected_fraction, expected_fraction)
    solved_words = _FRACTION_WORDS.get(solved_fraction, solved_fraction)
    producer = record.get("producer")
    cad = producer in (PRODUCER_CAD_LINKED, PRODUCER_CAD_UNLINKED)
    subject = "the returned CAD model" if cad else "it"
    if expected.get("by") == EXPECTED_BY_DESIGN:
        opening = f"This design is {expected_words} model, but WG solves {subject} as {solved_words}"
    else:
        mirrored = " and ".join(_MIRROR_WORDS[plane] for plane in expected.get("planes") or [])
        opening = f"The model mirrors {mirrored}, but WG solves it as {solved_words}"
    reasons: dict[str, list[str]] = {}
    fixes: list[str] = []
    for plane in lost:
        reason, fix = _plane_loss(plane, entries[plane], producer=producer, expected_words=expected_words)
        reasons.setdefault(reason, []).append(plane)
        if fix and fix not in fixes:
            fixes.append(fix)
    losses = "; ".join(f"{_mirrors(group)} lost because {reason}" for reason, group in reasons.items())
    return {
        "kind": LOST_PLANE_KIND,
        "blocking": False,
        "lost_planes": lost,
        "expected_fraction": expected_fraction,
        "solved_fraction": solved_fraction,
        "detail": " ".join([f"{opening}: {losses}.", *fixes]),
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
    if number is None:
        return "an unknown distance"
    return f"{number:.0f} mm" if abs(number) >= 100 else f"{number:.3g} mm"


__all__ = [
    "CAD_V1_CONTRACT",
    "CONTRACT",
    "CONTRACTS",
    "LOST_PLANE_KIND",
    "RECORD_FIELDS",
    "SILENT_LOSSES",
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
