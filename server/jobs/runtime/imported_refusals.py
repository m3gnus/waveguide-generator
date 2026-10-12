"""Imported-geometry refusals, capability checks, and engine verdicts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from server.cadlink.interpretation.domain_decision import (
    MISMATCH_CODE as DOMAIN_DECISION_MISMATCH,
    decision_mismatch_message,
    decision_problem,
    decision_refusal,
    job_decision_problem,
)
from server.cadlink.interpretation.domain_interpretation import (
    cut_shaped_open_rim,
    has_rigid_rim_observations,
    observe_record_mesh,
    open_half_refusal_message,
)
from server.engines.registry import EngineInfo, EngineRegistry, engine_supports_symmetry
from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.solver.imported import (
    ImportedMeshArtifactError,
    imported_domain_planes,
    read_verified_import_mesh,
)


class ImportedSolveRefusal(ValueError):
    """A typed imported-geometry eligibility refusal at the runtime boundary."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(f"{reason_code}: {message}")


def _imported_decision_refusal(record: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Refuse a record whose sealed domain decision does not describe it.

    Ingest decides a snapshot's domain once (``server/cadlink/interpretation/domain_decision.py``)
    and every submission solves exactly that decision: the planes it mirrors,
    the mesh and observations it was made on, its frame. A record from an
    earlier build carries none and is judged as before.
    """

    if record is None:
        return None
    problem = decision_problem(record)
    if problem is None:
        return None
    return DOMAIN_DECISION_MISMATCH, decision_mismatch_message(problem)


def _imported_job_decision_refusal(
    task_metadata: Mapping[str, Any] | None, record: Mapping[str, Any] | None
) -> tuple[str, str] | None:
    """Refuse a saved job whose decision is not its ingestion record's.

    Execution, a restart and a retry solve the job's record again; the record
    must still carry the very decision the job was submitted and recorded
    under. A job saved before decisions existed is judged by its record alone.
    """

    if record is None:
        return None
    problem = job_decision_problem(task_metadata, record)
    if problem is None:
        return None
    return DOMAIN_DECISION_MISMATCH, decision_mismatch_message(problem)


SIDE_IDENTIFIED_SOURCE_CODE = "imported_side_identified_source"


def _imported_side_identity_refusal(record: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Refuse mirroring a model already cut in CAD that has a left/right-named source.

    Ingest refuses it (``cut_recovery.side_of_source``); this re-reads the
    stored record at every plan, submission and execution, so a record
    prepared before that check -- or under a looser reading of the names --
    cannot bypass it. Planes WG cut itself are not judged: its mirror test is
    per source identity, so no source is mirrored onto another there.
    """

    if record is None:
        return None
    planes = set(imported_domain_planes(record))
    symmetry = record.get("symmetry")
    wg_cut = set((symmetry or {}).get("cut_planes") or []) if isinstance(symmetry, Mapping) else set()
    precut = sorted(planes - wg_cut)
    if not precut:
        return None
    from server.cadlink.interpretation.cut_recovery import side_of_source

    for source in record.get("sources") or ():
        side = side_of_source(source) if isinstance(source, Mapping) else None
        if side is not None:
            return (
                SIDE_IDENTIFIED_SOURCE_CODE,
                f"WG will not solve this CAD return mirrored on {', '.join(precut)}: source "
                f"{source.get('id')} is identified as the {side} one, so its mirror image would "
                "stand in for the other side's own source. Send the whole model, or rename the "
                "source if it is not one side's own driver, and send it again.",
            )
    return None


def _imported_open_half_refusal(
    record: Mapping[str, Any] | None, msh_text: str | None = None
) -> tuple[str, str] | None:
    """Reject an observed CAD cut that would otherwise become a full free-space solve.

    The ingestion detector counts a rim from at least three free mesh edges on
    one origin plane. A spanning rigid-shell rim and one-sided, uncapped geometry
    identify a missing half independently of the source position. A record's
    domain decision states this refusal; the saved observations are judged
    again as well, so no decision can make the check weaker. Older records are
    judged from their verified prepared mesh before they can run.
    """

    if record is None or imported_domain_planes(record):
        return None
    acoustic_domain = str(
        record.get("acoustic_domain") or record.get("sim_type") or "free-space"
    ).strip().lower()
    if bool(record.get("infinite_baffle")) or acoustic_domain not in {
        "free-space", "free_space", "freestanding",
    }:
        return None
    decided = decision_refusal(record)
    if decided is not None:
        return decided
    interpretation = record.get("domain_interpretation")
    if not has_rigid_rim_observations(interpretation):
        # Callers pass the mesh text they already verified against the record.
        try:
            verified = msh_text if msh_text is not None else read_verified_import_mesh(record)
        except ImportedMeshArtifactError:
            return (
                "ingest_domain_observations_unavailable",
                "This CAD return was prepared by an earlier WG and its prepared mesh "
                "is unavailable for the open-shell check. Send the model again before solving.",
            )
        derived = observe_record_mesh(record, verified)
        if derived is None:
            return (
                "ingest_domain_observations_unavailable",
                "This CAD return was prepared by an earlier WG and its prepared mesh "
                "cannot be read for the open-shell check. Send the model again before solving.",
            )
        interpretation = {"observations": derived.to_json()}
    open_rim = cut_shaped_open_rim(interpretation)
    if open_rim is None:
        return None
    return "imported_open_half_shell", open_half_refusal_message(*open_rim)


_IMPORTED_GEOMETRY = "imported"

#: The ingestion-resolved domains, as a refusal names them.
_IMPORTED_DOMAIN_LABELS = {
    1234: "full domain",
    14: "x0 half (mirrored on x = 0)",
    12: "y-only half (mirrored on y = 0)",
    1: "x0+y0 quarter (mirrored on x = 0 and y = 0)",
}

#: Imported features a request can use, as a refusal names them.
_IMPORTED_FEATURE_LABELS = {"passive-cardioid": "the passive-cardioid radiation campaign",
                            "exterior-transducers": "coupled exterior transducers",
                            "fem-regions": "coupled FEM regions (official CPU float64)"}


def _imported_features_needed(geometry: ImportedGeometrySource, record=None) -> set[str]:
    features = {"passive-cardioid"} if geometry.passive_cardioid_enabled else set()
    if any(channel.exterior_transducer is not None for channel in geometry.drive_channels):
        features.add("exterior-transducers")
    if record and record.get("fem_regions") and not geometry.exterior_only:
        features.add("fem-regions")
    return features


def _imported_capability_blocker(
    info: EngineInfo, resolved_quadrants: Any, needed_features: set[str]
) -> tuple[str, str] | None:
    """Why a declaring engine still cannot take this return, from its declaration."""

    if resolved_quadrants is not None and not engine_supports_symmetry(
        info, int(resolved_quadrants)
    ):
        domain = _IMPORTED_DOMAIN_LABELS.get(
            int(resolved_quadrants), f"quadrants {resolved_quadrants}"
        )
        return (
            "imported_symmetry_unsupported_by_engine",
            f"it cannot mirror this return's {domain}; it solves "
            f"{', '.join(info.symmetry_domains) or 'no reduced domain'}",
        )
    missing = sorted(needed_features - set(info.imported_features))
    if missing:
        return (
            "imported_feature_unsupported_by_engine",
            "it does not implement "
            + ", ".join(_IMPORTED_FEATURE_LABELS.get(item, item) for item in missing)
            + " for imported geometry",
        )
    return None


async def _imported_preflight_refusal(
    adapter: Any,
    record: Mapping[str, Any] | None,
    msh_text: str | None,
    drive_channels: Sequence[Any] | None = None,
) -> str | None:
    """An adapter's own verdict on the record and mesh, when it has one.

    An adapter is told the drive channels only when one is axial: whether an
    engine can take an axial source depends on its resolved axis.
    """

    preflight = getattr(adapter, "imported_preflight", None)
    if not callable(preflight) or record is None or msh_text is None:
        return None
    if drive_channels and any(
        getattr(channel, "motion", "normal") == "axial" for channel in drive_channels
    ):
        refusal = await asyncio.to_thread(
            preflight, record, msh_text, drive_channels=list(drive_channels)
        )
    else:
        refusal = await asyncio.to_thread(preflight, record, msh_text)
    return refusal if isinstance(refusal, str) and refusal else None


async def _engines_able_to_take(
    declared: Mapping[str, EngineInfo],
    order: tuple[str, ...],
    *,
    exclude: str,
    resolved_quadrants: Any,
    needed_features: set[str],
    engine_registry: EngineRegistry,
    imported_record: Mapping[str, Any] | None,
    imported_msh_text: str | None,
    drive_channels: Sequence[Any] | None = None,
) -> list[str]:
    """The engines, other than ``exclude``, that would take this return here.

    Each engine's own ``imported_engine_verdict`` -- the one the selector
    shows, its adapter's preflight included -- so a refusal never names an
    engine that would refuse the record in turn. It runs only on the refusal
    path, and solves nothing.
    """

    able: list[str] = []
    for name in order:
        info = declared.get(name)
        if name == exclude or info is None:
            continue
        verdict = await imported_engine_verdict(
            info,
            resolved_quadrants=resolved_quadrants,
            needed_features=needed_features,
            engine_registry=engine_registry,
            imported_record=imported_record,
            imported_msh_text=imported_msh_text,
            drive_channels=drive_channels,
        )
        if verdict.solves:
            able.append(name)
    return able


def _imported_axial_refusal(
    request: SolveRequest, record: Mapping[str, Any] | None, msh_text: str | None
) -> tuple[str, str] | None:
    """An axial source no engine can drive: no outward axis, or off the mirror."""

    geometry = request.geometry
    if (
        record is None
        or msh_text is None
        or not isinstance(geometry, ImportedGeometrySource)
        or not any(channel.motion == "axial" for channel in geometry.drive_channels)
    ):
        return None
    if any(channel.exterior_transducer is not None for channel in geometry.drive_channels):
        return None  # Explicit global axes are validated by the opt-in adapter.
    from server.solver.imported import prepare_axial_drive

    try:
        prepare_axial_drive(record, msh_text, geometry.drive_channels)
    except ValueError as exc:
        return "imported_axial_source_unresolvable", str(exc)
    return None


def _imported_backend_refusal(
    request: SolveRequest, record: Mapping[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Refuse the selected backend at submit/run, without restricting other picks."""
    if (record and record.get("fem_regions") and not request.geometry.exterior_only
            and request.options.engine not in {"auto", "beat", "beat-cpu"}):
        return "fem_backend_unsupported", (
            "FEM regions require official BEAT CPU; choose BEAT CPU, or choose exterior-only")
    return None


def _imported_request_refusal(
    request: SolveRequest, record: Mapping[str, Any] | None = None,
    msh_text: str | None = None,
) -> tuple[str, str] | None:
    """A refusal no engine can lift: the request asks what imported geometry never does."""

    coupled_regions = bool(record and record.get("fem_regions") and not request.geometry.exterior_only)
    if not request.geometry.drive_channels and not coupled_regions:
        return "drive_source_coverage", "The return has no driven exterior source or FEM diaphragm"
    if not coupled_regions and any(c.exterior_transducer is not None for c in request.geometry.drive_channels):
        if not all(c.exterior_transducer is not None for c in request.geometry.drive_channels):
            return "exterior_transducer_mixed_channels", "Exterior-only transducer jobs require a model on every drive channel; mixed drives require FEM regions"
        if any(not c.source_ids for c in request.geometry.drive_channels):
            return "exterior_transducer_missing_source", "An exterior-only transducer needs painted source faces; a FEM-only driver requires FEM regions"
    if coupled_regions:
        from server.solver.coupled_admission import fem_termination_problem, painted_source_ids
        from server.cadlink.build_flags import fem_regions_enabled
        from server.solver.beat_runtime.provider import official_selected

        if not fem_regions_enabled():
            return "fem_required", "FEM regions are disabled by WG2_FEM_REGIONS; remove the override to enable them"
        if not official_selected():
            return "fem_provider_unsupported", ("FEM regions require the official BEAT provider; "
                                                "unset WG2_BEAT_PROVIDER, or choose exterior-only")
        problem = fem_termination_problem(record["fem_regions"], has_exterior=bool(
            any(c.source_ids for c in request.geometry.drive_channels) or painted_source_ids(record)))
        if problem:
            return problem
        planes = imported_domain_planes(record)
        if any(p not in {"x0", "y0"} for p in planes):
            return "fem_domain_unsupported", "FEM regions support only x0 and y0 symmetry planes"
        if planes and any(c.exterior_transducer is not None for c in request.geometry.drive_channels):
            return "fem_domain_unsupported", ("Electrodynamic transducers in FEM solves need a full model; "
                                              "export without symmetry planes")
        if request.options.ground_plane.enabled or record.get("infinite_baffle"):
            return "fem_domain_unsupported", "FEM regions require free space without a ground plane"
        ideal_message = "FEM solves support ideal normal-velocity and electrodynamic voltage drives"
        from server.solver.beat_adapter.coupled_transducers import channel_problem

        for channel in request.geometry.drive_channels:
            problem = channel_problem(channel)
            if problem:
                return problem
        if request.geometry.passive_cardioid_enabled:
            return "fem_drive_unsupported", f"{ideal_message}; passive-cardioid synthesis is unsupported"
        from server.solver.coupled_admission import fem_drive_problem

        problem = fem_drive_problem(record["fem_regions"],
            {c.id: c.source_ids for c in request.geometry.drive_channels},
            painted_sources=painted_source_ids(record),
            combine_id=request.geometry.combine.id if request.geometry.combine else None)
        if problem:
            return problem
    mismatch = _imported_decision_refusal(record)
    if mismatch is not None:
        return mismatch
    sided = _imported_side_identity_refusal(record)
    if sided is not None:
        return sided
    open_half = _imported_open_half_refusal(record, msh_text)
    if open_half is not None:
        return open_half
    axial = _imported_axial_refusal(request, record, msh_text)
    if axial is not None:
        return axial
    from server.solver.driver_lem import DriverMassLimitError, validate_driver_mass

    for channel in request.geometry.drive_channels:
        if channel.driver is not None:
            try:
                validate_driver_mass(channel.driver)
            except DriverMassLimitError as exc:
                return "driver_mass_invalid", f"Channel {channel.id!r}: {exc}"
            except ValueError as exc:
                return "driver_invalid", f"Channel {channel.id!r}: {exc}"
    if request.options.ground_plane.enabled:
        return (
            "imported_ground_plane_unsupported",
            "A rigid ground plane is not available for imported CAD geometry on "
            "any engine: the returned mesh is solved as it arrived, and no engine "
            "here stands it above a floor. Turn the ground plane off to solve "
            "this return.",
        )
    return None


@dataclass(frozen=True)
class ImportedEngineVerdict:
    """Whether one engine can solve one imported request on this host, and why not.

    ``stage`` names the first check that refused: ``declaration`` (the engine
    does not declare imported geometry), ``capability`` (its declared symmetry
    domains or imported features exclude this return), ``availability`` (it is
    not available here), ``adapter`` (the snapshot said available but the
    adapter would not construct), ``preflight`` (its adapter refused this
    record) or ``request`` (the request asks for something no engine does).
    """

    name: str
    label: str
    solves: bool
    stage: str | None = None
    code: str | None = None
    reason: str | None = None


async def imported_engine_verdict(
    info: EngineInfo,
    *,
    resolved_quadrants: Any,
    needed_features: set[str],
    engine_registry: EngineRegistry,
    imported_record: Mapping[str, Any] | None,
    imported_msh_text: str | None,
    drive_channels: Sequence[Any] | None = None,
) -> ImportedEngineVerdict:
    """One engine's verdict on one imported request: the one capability function.

    ``resolve_imported_submission`` walks it to choose an engine and the
    imported plan asks it of every engine for the solver selector, so the list
    the user picks from and the submission that follows cannot disagree.
    """

    label = info.display_label()
    if _IMPORTED_GEOMETRY not in info.geometry_sources:
        return ImportedEngineVerdict(
            info.name,
            label,
            False,
            "declaration",
            "imported_engine_unsupported",
            "does not solve imported CAD geometry",
        )
    blocker = _imported_capability_blocker(info, resolved_quadrants, needed_features)
    if blocker is not None:
        code, reason = blocker
        return ImportedEngineVerdict(info.name, label, False, "capability", code, reason)
    if not info.available:
        return ImportedEngineVerdict(
            info.name,
            label,
            False,
            "availability",
            "engine_unavailable",
            f"unavailable ({info.reason})",
        )
    adapter = await engine_registry.get_engine(info.name)
    if adapter is None:
        unavailable_reason = await engine_registry.unavailable_reason(info.name)
        return ImportedEngineVerdict(
            info.name,
            label,
            False,
            "adapter",
            "engine_unavailable",
            unavailable_reason or "No capability reason was reported.",
        )
    # FEM dispatch validates its own compiled boundaries. Exterior-only
    # preflight cannot describe a mixed drive or an interior-only transducer.
    refusal = (None if "fem-regions" in needed_features else
               await _imported_preflight_refusal(adapter, imported_record, imported_msh_text, drive_channels))
    if refusal is not None:
        return ImportedEngineVerdict(
            info.name,
            label,
            False,
            "preflight",
            "imported_return_unsupported_by_engine",
            refusal,
        )
    return ImportedEngineVerdict(info.name, label, True)


_BEAT_ACCELERATORS = frozenset({"beat-cuda", "beat-rocm", "beat-metal"})

#: Accelerators whose imported path an explicit pick may use in Fast as well as
#: Accurate. beat-metal is the one qualified on Apple Silicon; CUDA and ROCm
#: stay Accurate-only until they have been run on that hardware.
_BEAT_FAST_IMPORTED_EXPLICIT = frozenset({"beat-metal"})


def _imported_capabilities(
    capabilities: tuple[EngineInfo, ...] | list[EngineInfo],
    accuracy: str,
    engine: str = "auto",
) -> dict[str, EngineInfo]:
    """Expose imported BEAT accelerators for the request that may use them.

    Accurate exposes every accelerator. An explicitly named ``beat-metal`` is
    exposed in Fast too: Fast and Accurate run the identical BEAT computation.
    AUTO and the legacy ``beat`` family never gain a Fast accelerator, so their
    resolution is unchanged.
    """

    def exposed(info: EngineInfo) -> bool:
        if info.name not in _BEAT_ACCELERATORS or "imported" in info.geometry_sources:
            return False
        return accuracy == "accurate" or (
            info.name == engine and engine in _BEAT_FAST_IMPORTED_EXPLICIT
        )

    return {
        info.name: replace(info, geometry_sources=(*info.geometry_sources, "imported"))
        if exposed(info) else info
        for info in capabilities
    }


def _imported_capable_if_picked(
    capabilities: tuple[EngineInfo, ...] | list[EngineInfo], accuracy: str, name: str
) -> bool:
    """Whether ``name`` would declare imported geometry if it were the pick."""

    info = _imported_capabilities(capabilities, accuracy, name).get(name)
    return info is not None and _IMPORTED_GEOMETRY in info.geometry_sources


def _imported_accuracy(request: SolveRequest) -> str:
    """The choice relevant to imported capability declarations, after explicit precedence."""

    engine = request.options.engine
    if engine != "auto" and engine != "beat" and not engine.startswith("beat-"):
        return "fast"
    return request.options.accuracy
