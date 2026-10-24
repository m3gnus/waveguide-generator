"""FIFO asyncio job scheduler and solve lifecycle.

The single-slot FIFO queue, queued/running recovery, active-delete rules, and
strong task references port v1 ``server/services/job_runtime.py:23-41,92-208,
259-285,497-574,629-686``. The dry-run solve itself is dispatched with
``asyncio.to_thread`` just like the retained in-process v1 execution model.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections import deque
from contextlib import asynccontextmanager
import copy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import importlib.metadata
import json
import math
import os
from pathlib import Path
import time
import threading
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence
import uuid

from server.cadlink.intake.ingest import IngestRefusal, current_fem_evidence
from server.mesh.records import get_prepared_mesh as get_ingestion_record
from server.cadlink.interpretation.domain_decision import (
    decision_summary,
)
from server.cadlink.operations import (
    REASON_CODES as CAD_REASON_STATES, REJECTED as CAD_REJECTED, NEEDS_USER_INPUT, hold_details,
    is_calm_failure,
)
from server.cadlink.interpretation.solver_frame import REASON as FRAME_CONFIRMATION_REQUIRED
from server.cadlink.intake.readings import record_frame_refusal
from server.cadlink.store import CadLinkStore
from server.workspace import archive_identity
from server.design.defaults import default_sweep_start_hz
from server.design.schema import DesignConfig, Expr
from server.design.textcfg import parse
from server.engines.registry import (
    SELECTABLE_ENGINE_NAMES,
    EngineRegistry,
    create_engine as get_engine,
    full3d_engine_order,
)
from server.jobs.cad_intent import (
    BIND_BLOCKED,
    BIND_STOPPED,
    BOUND,
    CAD_INTENT,
    INTERRUPTED_MESSAGE as CAD_INTERRUPTED_MESSAGE,
    STAGE_WAITING_FOR_RESTART,
    CadSolveIntent,
    cad_of,
    carried_record,
    intent_of,
    solve_again_intent,
)
from server.platform.feature_flags import fem_regions_enabled
from server.jobs.design_availability import resolve_job_design
from server.jobs.retention import MAX_RETAINED_RESULTS, RESULT_RETENTION_DAYS
from server.jobs.imported_polar import widen_polar_to_derivation
from server.jobs.models import (
    JobArchiveIdentity,
    JobDesignIdentity,
    ChannelCombineSpec,
    FieldPlaneRequest,
    ImportedGeometrySource,
    ParametricGeometrySource,
    SolveOptions,
    SolveRequest,
)
from server.jobs.store import ACTIVE_STATUSES, ALLOWED_STATUSES, JobStore
from server.integration.provenance import canonical_json_sha256, enrich_result_contract
from server.platform.instance import LOCK_OPEN_FLAGS, lock_exclusive, unlock
from server.platform.shutdown_backstop import shutdown_wait_limit
from server.mesh.child import MesherShuttingDownError
from server.mesh.imported import verify_artifact_reduced_orientation
from server.solver.imported import (
    AXIAL_CONTRACT_VERSION,
    ImportedMeshArtifactError,
    imported_domain_planes,
    read_verified_import_mesh,
    verify_record_mesh_text,
)
from server.solver.symmetry import (
    resolve_symmetry,
    restrict_for_ground_plane,
    validate_symmetry_mode,
)
from server.solver.field_plane import FieldPlaneEvaluation, FieldPlaneService
from server.solver.polar_arc import effective_polar_config, effective_polar_grid
from server.solver.formulation import bem_formulation
from server.solver.metal_permit import MetalPermit, process_metal_permit
from server.solver.base import EngineRunResult
from server.solver.watchdog import watch_native_operation, stall_timeout_seconds

from . import logger
from .engine_policy import (
    EngineUnavailableError,
    EngineVerdict,
    SolveBlocker,
    select_engine,
)
from .provisional import (
    _extend_provisional_results,
    _copy_provisional_result,
    merge_provisional_results,
)
from .imported_refusals import (
    ImportedSolveRefusal,
    _imported_decision_refusal,
    _imported_job_decision_refusal,
    _imported_side_identity_refusal,
    _imported_open_half_refusal,
    _imported_features_needed,
    _imported_request_refusal,
    ImportedEngineVerdict,
    imported_engine_verdict,
    _imported_capabilities,
    _imported_accuracy,
    _fem_interior_only,
)
from .cad_lane import (
    _ImportedSubmission,
    _ComposedJob,
    _cad_authored_project,
    _imported_record_sources,
    _cad_identity_provenance,
    _validate_passive_cardioid_topology,
    _imported_symmetry_metadata,
    _validate_imported_polar_grid,
)
from .events import (
    EventBroker,
)

if TYPE_CHECKING:
    from server.jobs.cad_preparation import CadPreparationHost
    from server.updates.restart import RestartApproval


MAX_LOG_LINES = 200
MAX_LOG_CHARS = 32_000
MAX_LOG_EVENT_CHARS = 2_000
CANCELLED_MESSAGE = "Simulation cancelled by user"
#: A failed job's error when its exception said nothing at all. A bare
#: ``EOFError`` from a dead solver child has an empty ``str()``, and an empty
#: error message reads as no reason given.
FAILED_WITHOUT_DETAIL_MESSAGE = "The simulation failed without reporting a reason."
#: What a job that Quit interrupted reads as. That covers a job stopped at a
#: checkpoint during shutdown, and one the shutdown budget cut off and the next
#: start recovered. It is distinct from the crash reason an unmarked orphan
#: gets, and it is not a failure: the user chose to quit.
QUIT_INTERRUPTED_STAGE_MESSAGE = "Interrupted by Quit"
QUIT_INTERRUPTED_MESSAGE = (
    "Interrupted by Quit: Waveguide Generator closed while this simulation was "
    "running. Run it again to get results."
)
#: What a job the update restart ended reads as
#: (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.3): stopped at a
#: checkpoint as WG restarted to install an update, or cut off by the shutdown
#: budget and recovered on the next start. Like Quit, it is not a failure.
UPDATE_RESTART_STAGE_MESSAGE = "Ended by the update restart"
UPDATE_RESTART_MESSAGE = (
    "Ended by the update restart: Waveguide Generator restarted to install an "
    "update while this simulation was running. Run it again to get results."
)
RESTART_RECOVERY_MESSAGE = "Server restarted during execution"
RUNTIME_PERSIST_INTERVAL_SECONDS = 0.15
SHUTDOWN_TASK_TIMEOUT_SECONDS = 10.0
#: How many CAD solves are prepared (retained, meshed) at once. A separate lane
#: from the solve scheduler, so meshing never waits behind a running solve.
CAD_PREPARATION_LANE_WIDTH = 2
BEMPP_DEFAULT_WALL_THICKNESS_MM = 5.0
BEMPP_WALL_ADJUSTMENT_KIND = "bempp_wall_default"
BEMPP_WALL_REASON_CODE = "bempp_free_standing_requires_closed_wall"
BEMPP_WALL_POLICY_VERSION = 1


def _apply_bempp_wall_default(
    request: SolveRequest, engine_name: str
) -> tuple[SolveRequest, dict[str, Any] | None]:
    """Materialize ATH's closed-wall default for BEMPP free-standing solves.

    BEMPP's open-shell pressure space treats free-rim
    degrees of freedom differently from the closed-body formulation. Keep an
    inactive enclosure plus a missing/zero wall from silently entering that
    backend-specific topology. Revalidating the copied wire also keeps the
    authoritative design and its atomic snapshot identical. Native closed bodies
    keep their geometry; bare native horns refuse BEMPP because their contracts
    prohibit adding a wall.

    Returns the request to solve and, when the wall was changed, the adjustment
    that reports it (``None`` when the request is returned unchanged). The
    adjustment keeps an omitted thickness apart from an explicit 0: an explicit
    0 mm "bare shell" is a request the user made, and this policy still
    overrides it. Reporting both as an applied default would hide that.
    """

    if engine_name != "bempp" or not isinstance(
        request.geometry, ParametricGeometrySource
    ):
        return request, None
    root = request.design.root
    from server.design.native import NATIVE_FORMULAS

    if root.formula in NATIVE_FORMULAS:
        if root.formula not in {"OSSE-ROUNDOVER", "SOURCE-DISK"}:
            raise SymmetryValidationError(
                f"BEMPP requires a closed body, but {root.formula} only supports "
                "a bare horn with zero wall thickness. Choose another engine "
                "or a closed-body formula (OSSE-ROUNDOVER or SOURCE-DISK)."
            )
        return request, None
    if root.simulation.sim_type == "infinite-baffle":
        return request, None

    if root.enclosure is not None and root.enclosure.depth is not None:
        enclosure_depth = root.enclosure.depth.constant_value()
        if enclosure_depth is None or float(enclosure_depth) > 0.0:
            return request, None

    wall = root.mesh.wall_thickness
    wall_value = wall.constant_value() if wall is not None else None
    if wall is not None and (wall_value is None or float(wall_value) != 0.0):
        return request, None

    payload = request.model_dump(mode="json")
    geometry = payload["geometry"]
    geometry["design"]["mesh"]["wall_thickness"] = (
        BEMPP_DEFAULT_WALL_THICKNESS_MM
    )
    geometry["design_snapshot"]["design"]["mesh"]["wall_thickness"] = (
        BEMPP_DEFAULT_WALL_THICKNESS_MM
    )
    corrected = SolveRequest.model_validate(payload)
    adjustment: dict[str, Any] = {
        "kind": BEMPP_WALL_ADJUSTMENT_KIND,
        "requested": "omitted" if wall is None else "explicit_zero",
        "effective_mm": BEMPP_DEFAULT_WALL_THICKNESS_MM,
        "reason_code": BEMPP_WALL_REASON_CODE,
        "policy_version": BEMPP_WALL_POLICY_VERSION,
    }
    logger.info("%s", _bempp_wall_adjustment_message(adjustment))
    return corrected, adjustment


def _bempp_wall_adjustment_message(adjustment: Mapping[str, Any]) -> str:
    """One job-log line naming what was asked for and what is solved instead.

    It reads stored rows, so a malformed entry falls back to the policy value
    rather than failing the job over a log line.
    """

    try:
        effective_mm = float(
            adjustment.get("effective_mm", BEMPP_DEFAULT_WALL_THICKNESS_MM)
        )
    except (TypeError, ValueError):
        effective_mm = BEMPP_DEFAULT_WALL_THICKNESS_MM
    effective = f"{effective_mm:g} mm"
    if adjustment.get("requested") == "explicit_zero":
        return (
            "BEMPP wall adjustment: the explicit 0 mm wall thickness (bare shell) "
            f"was overridden with a {effective} closed wall, because free-standing "
            "BEMPP solves run on a closed body."
        )
    return (
        "BEMPP wall adjustment: no wall thickness was set, so ATH's "
        f"{effective} closed-wall default was applied, because free-standing "
        "BEMPP solves run on a closed body."
    )


def _solver_plan_adjustments(
    symmetry_metadata: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """The adjustments recorded in a stored solve plan, tolerating older rows."""

    plan = (symmetry_metadata or {}).get("solver_plan")
    adjustments = plan.get("adjustments") if isinstance(plan, Mapping) else None
    if not isinstance(adjustments, list):
        return []
    return [item for item in adjustments if isinstance(item, Mapping)]


def _failure_message(exc: BaseException) -> str:
    """A failed job's error message: the exception's text, never empty."""

    text = str(exc).strip()
    if text:
        return text
    return f"The simulation failed without reporting a reason ({type(exc).__name__})."


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sweep_description(request: SolveRequest) -> str:
    """Describe the sweep for verbose logs without dumping 401 numbers."""

    explicit = request.options.frequencies_hz
    if explicit is None:
        return f"spacing={request.options.frequency_spacing}"
    head = ", ".join(f"{value:g}" for value in explicit[:6])
    suffix = ", …" if len(explicit) > 6 else ""
    return f"explicit list of {len(explicit)} points [{head}{suffix}]"


def _stored_solve_options(config: Mapping[str, Any]) -> SolveOptions:
    """Read native v2 options or translate the solve fields persisted by v1."""

    nested = config.get("options")
    nested_options = nested if isinstance(nested, Mapping) else {}
    geometry = config.get("geometry")
    if isinstance(config.get("design"), Mapping) or isinstance(geometry, Mapping):
        current = {
            key: value
            for key, value in nested_options.items()
            if key in SolveOptions.model_fields
        }
        return SolveOptions.model_validate(current)

    legacy: dict[str, Any] = {}
    for key in (
        "frequency_range",
        "num_frequencies",
        "frequency_spacing",
        "frequencies_hz",
        "verbose",
        "mesh_validation_mode",
        "polar_config",
    ):
        value = config.get(key)
        if value is not None:
            legacy[key] = value
    backend = str(config.get("solver_backend") or config.get("device_mode") or "auto")
    normalized_backend = backend.strip().lower()
    if "metal" in normalized_backend:
        legacy["engine"] = "metal"
    elif "bem" in normalized_backend:
        legacy["engine"] = "bempp"
    elif "dry" in normalized_backend:
        legacy["engine"] = "dryrun"
    else:
        legacy["engine"] = "auto"
    return SolveOptions.model_validate(legacy)


def _recorded_resolved_quadrants(metadata: Mapping[str, Any]) -> int | None:
    """The solve domain a job recorded at submission, if it recorded one.

    Returns ``None`` for rows that never recorded a resolution -- v1 rows, whose
    declared ``mesh.quadrants`` *is* the domain v1 meshed -- so callers can leave
    the stored design alone rather than assuming a full domain for them.
    """

    symmetry = metadata.get("symmetry")
    if not isinstance(symmetry, Mapping):
        return None
    recorded = symmetry.get("resolved_quadrants")
    if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        return None
    return int(recorded)


_ENCLOSURE_RESOLUTION_CONTRACT = "per-quadrant-v1"


def _legacy_enclosure_resolution_changed(design: DesignConfig) -> bool:
    from server.preview.translate import outer_body_mode

    root = design.root
    if outer_body_mode(root) != "enclosure" or root.enclosure is None:
        return False
    for side in ("front_resolution", "back_resolution"):
        values = getattr(root.enclosure, side)
        if not isinstance(values, tuple):
            continue
        # Old WG used only q1. All non-positive entries share mouth fallback.
        numbers = [item.constant_value() for item in values]
        effective = [number if number is not None and number > 0 else None for number in numbers]
        if any(number != effective[0] for number in effective[1:]):
            return True
    return False


def _solved_under_legacy_axial(results_text: str | None, axial_ids: Sequence[str]) -> bool:
    """Whether stored results do not show every axial channel solved per source axis.

    Retry is allowed only when each axial channel's stored result records
    ``axial_contract == "per-source-axis-v2"``. A missing channel, missing
    motion metadata, a missing contract or the earlier rule all count as
    legacy. No stored result at all is not a legacy result.
    """

    if not results_text:
        return False
    try:
        results = json.loads(results_text)
    except json.JSONDecodeError:
        return True
    channels = results.get("channels") if isinstance(results, Mapping) else None
    channels = channels if isinstance(channels, Mapping) else {}
    for channel_id in axial_ids:
        channel = channels.get(channel_id)
        metadata = channel.get("metadata") if isinstance(channel, Mapping) else None
        if not isinstance(metadata, Mapping) or metadata.get("axial_contract") != AXIAL_CONTRACT_VERSION:
            return True
    return False


def _request_motions(row: Mapping[str, Any]) -> dict[str, str]:
    """Each imported drive channel's motion from a job's archived request."""

    try:
        request = _replay_request(row)
    except Exception:  # noqa: BLE001 - an unreadable request just recovers nothing
        return {}
    geometry = request.geometry
    if not isinstance(geometry, ImportedGeometrySource):
        return {}
    return {channel.id: channel.motion for channel in geometry.drive_channels}


def _replay_request(row: Mapping[str, Any]) -> SolveRequest:
    """Build the faithful request represented by a native or imported row."""

    config = row.get("config_json")
    config = config if isinstance(config, Mapping) else {}
    if config.get("type") == CAD_INTENT:
        # A CAD solve that was never bound holds its intent, not a SolveRequest;
        # the request exists only once the job is bound and queued.
        raise JobConflictError(
            "This solve is still being prepared and has no solve request yet"
            if row.get("status") == "preparing"
            else "This solve ended before it had a solve request. Solve it again "
            "from CAD Link."
        )
    if isinstance(config.get("design"), Mapping) or isinstance(
        config.get("geometry"), Mapping
    ):
        return SolveRequest.model_validate(config).model_copy(deep=True)

    resolution = resolve_job_design(row.get("script_snapshot"))
    if (
        not resolution.reopenable
        or resolution.snapshot is None
        or "design" not in resolution.snapshot
    ):
        raise JobConflictError(
            resolution.reason or "This run has no stored design and cannot be retried"
        )
    metadata = row.get("task_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return SolveRequest.model_validate(
        {
            "design": resolution.snapshot["design"],
            "design_snapshot": resolution.snapshot,
            "options": _stored_solve_options(config).model_dump(mode="json"),
            "label": row.get("label"),
            "design_revision": int(metadata.get("design_revision") or 0),
        }
    )


def _execution_request(
    request: SolveRequest,
    *,
    resolved_quadrants: int,
) -> SolveRequest:
    """Shape a persisted request exactly as the solver receives it."""

    execution_request = request.model_copy(deep=True)
    if isinstance(execution_request.geometry, ParametricGeometrySource):
        execution_request.design.root.mesh.quadrants = Expr(
            value=float(resolved_quadrants)
        )
    return execution_request


class JobNotFoundError(LookupError):
    """Requested job is absent from persistence."""


class JobConflictError(RuntimeError):
    """Requested mutation conflicts with the persisted lifecycle state."""


class JobProtectionError(JobConflictError):
    """Deletion can proceed only if the caller explicitly skips model protection."""


class JobResourceUnavailableError(RuntimeError):
    """Requested job result or artifact is not persisted."""


class JobMeshDiscardedError(JobResourceUnavailableError):
    """A terminal unrated job no longer retains an instantly available mesh."""


@dataclass(frozen=True)
class MeshArtifactDownload:
    """Mesh download body plus provenance that belongs in response headers."""

    content: str
    regenerated: bool = False
    mesher_version: str | None = None


class UnknownEngineError(ValueError):
    """The request named an engine outside the registry."""


def _ground_plane_axis(request: SolveRequest) -> str | None:
    """The axis a requested ground plane bounds, or None when it is off."""

    ground_plane = request.options.ground_plane
    return ground_plane.axis if ground_plane.enabled else None


def _submission_identity(request: SolveRequest) -> dict[str, Any]:
    """What a submission key is compared against on replay.

    The wire request does not carry the BEM formulation: the adapters derive it
    from the mounting. A coupled infinite baffle now runs real k where it used
    to run complex_k, so the same request bytes stand for a different solve. The
    formulation it will run with is therefore part of the identity, and a replay
    of a key that created an earlier (complex_k) infinite-baffle job conflicts
    instead of answering with that job. Requests with any other mounting hash
    exactly as before.
    """

    identity = request.model_dump(mode="json")
    # Preserve durable identities from before this default-off option existed.
    if not request.options.adaptive_frequency_sampling:
        identity["options"].pop("adaptive_frequency_sampling", None)
    design = getattr(request, "design", None)
    if design is not None and design.root.simulation.sim_type == "infinite-baffle":
        bem = bem_formulation(coupled_infinite_baffle=True)
        identity["effective_bem_formulation"] = {
            "formulation": bem.formulation,
            "complex_k_shift": bem.complex_k_shift,
        }
    return identity


def _requested_mounting(request: SolveRequest) -> str | None:
    """The mounting AUTO must find an engine for.

    The two sources are deliberately different layers: the infinite baffle is a
    property of the design (``Simulation.SimType``, and ATH writes it), while
    the ground plane describes the room the design is placed in and lives in
    solve options. A solve has one mounting either way, so they are collapsed
    here rather than at every consumer.

    They cannot both be set, and the refusal is at the submission boundary
    below rather than in an adapter. It used to be in ``server/solver/bempp.py``
    alone, which meant the pair was refused only on the engine that happened to
    implement it: on Metal it was accepted, silently, and cost a symmetry plane
    as well, because ``restrict_for_ground_plane`` had already subtracted the
    matching mirror before any adapter saw the request. The baffle still wins
    here, so AUTO resolves against a mounting that exists.
    """

    if request.design.root.simulation.sim_type == "infinite-baffle":
        return "infinite-baffle"
    if _ground_plane_axis(request) is not None:
        return "ground-plane"
    return request.design.root.simulation.sim_type


class SymmetryValidationError(ValueError):
    """The requested solve domain requires a mirror plane the geometry lacks."""


def _refuse_inverted_reduced_mesh(record: Mapping[str, Any], msh_text: str) -> None:
    """Refuse a reduced mesh wound against the model it mirrors.

    Ingestion judges a reduced mesh's winding when it builds one and when it
    serves one from its cache. A record written before that check existed
    names a mesh nobody judged, and its digest vouches only for the bytes. An
    inverted mesh is perfectly consistent, so nothing downstream notices, and
    every engine would solve it inside out. The plan, the submission and the
    job's own start therefore judge the mesh the solver will read.
    """

    planes = imported_domain_planes(record)
    if not planes:
        return
    try:
        orientation = verify_artifact_reduced_orientation(msh_text, cut_planes=planes)
    except ValueError as exc:
        raise ImportedSolveRefusal(
            "ingest_mesh_unavailable",
            f"the reduced mesh of this CAD return cannot be read to check its "
            f"winding ({exc}). Re-import this return.",
        ) from exc
    inverted = int(orientation["inverted_component_count"])
    if inverted:
        raise ImportedSolveRefusal(
            "imported_reduced_mesh_inverted",
            f"{inverted} of {orientation['component_count']} component(s) of this "
            "CAD return's reduced mesh are wound against the model they mirror, "
            "so every engine would solve it inside out. An earlier WG build "
            "prepared it. Re-import this return: WG prepares it again and checks "
            "its winding.",
            details={
                "cut_planes": list(planes),
                "inverted_component_count": inverted,
                "component_count": int(orientation["component_count"]),
            },
        )


@dataclass(frozen=True)
class SubmissionResolution:
    """Validated submission values ready for durable persistence.

    ``resolve_submission`` produces it for parametric geometry and
    ``resolve_imported_submission`` for imported geometry; both record their
    engine decision in ``symmetry_metadata["solver_plan"]``.
    """

    request: SolveRequest
    engine_name: str
    symmetry_metadata: dict[str, Any]


@dataclass(frozen=True)
class _ImportedValidation:
    request: SolveRequest
    record: dict[str, Any]
    msh_text: str
    symmetry_metadata: dict[str, Any]
    identity: dict[str, Any] | None


@dataclass(frozen=True)
class SubmissionValidation:
    resolved: SubmissionResolution | None
    imported: _ImportedValidation | None = None
    imported_plan: dict[str, Any] | None = None


def _design_quadrants_text(request: SolveRequest) -> str | None:
    quadrants = request.design.root.mesh.quadrants
    return quadrants.text() if quadrants is not None else None



class RemovedSolverError(ValueError):
    """A request names the removed Axisymmetric solver mode or engine."""

    code = "removed_solver_mode"


def _refuse_removed_solver(request: SolveRequest) -> None:
    """Keep historical requests decodable, but never start their removed solver."""

    if request.options.solver_mode == "circsym" or request.options.engine in {"axisym", "circsym"}:
        raise RemovedSolverError(
            "Axisymmetric solving has been removed. Choose Full 3D and a supported engine to run a new solve."
        )


def _evidence_count(evidence: Mapping[str, Any], key: str) -> int:
    value = evidence.get(key)
    return len(value) if isinstance(value, list) else 0


def _refuse_imported_fem(
    geometry: ImportedGeometrySource, record: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Apply the same FEM eligibility rule at submission and recovered execution."""

    try:
        evidence = current_fem_evidence(record)
    except IngestRefusal as exc:
        raise ImportedSolveRefusal("ingest_record_incomplete", str(exc)) from exc
    volumes = evidence.get("fem_air_volumes") or []
    volumes = volumes if isinstance(volumes, list) else [volumes]
    regions = evidence.get("fem_regions") or []
    regions = regions if isinstance(regions, list) else [regions]
    required_fem = any(
        not isinstance(volume, Mapping) or volume.get("required", True)
        for volume in volumes
    )
    from server.cadlink.build_flags import fem_regions_enabled

    if (regions and record.get("fem_regions") and not required_fem
            and not geometry.exterior_only and fem_regions_enabled()):
        return evidence
    if (required_fem or regions) and not geometry.exterior_only:
        exterior_only = "or choose exterior-only to solve without the FEM air."
        if regions and not fem_regions_enabled():
            remedy = "FEM regions are disabled by WG2_FEM_REGIONS; remove the override to enable them, "
        elif regions and not record.get("fem_regions"):
            # Declared, but ingested by a WG that did not mesh FEM regions.
            remedy = "Send the return again so WG can prepare its FEM regions, "
        else:
            remedy = "Declare these volumes as FEM regions with the Waveguide Generator add-in in Fusion and send again, "
        declared = "required FEM air volumes and FEM regions" if required_fem and regions else (
            "FEM regions" if regions else "required FEM air volumes"
        )
        raise ImportedSolveRefusal(
            "fem_required", f"The CAD return declares {declared}. {remedy}{exterior_only}",
        )
    return evidence


def _explicit_accuracy(request: SolveRequest) -> SolveRequest:
    """An explicit non-BEAT engine takes precedence over the accuracy preset."""
    if (request.options.engine not in {"auto", "beat"}
            and not request.options.engine.startswith("beat-")
            and request.options.accuracy == "accurate"):
        request = request.model_copy(deep=True)
        request.options.accuracy = "fast"
    return request


def _raise_engine_blocker(blocker: SolveBlocker, *, imported: bool = False) -> None:
    if blocker.code == "engine_unavailable":
        raise EngineUnavailableError(blocker.message, blocker=blocker)
    if imported:
        raise ImportedSolveRefusal(
            ("fem_backend_unsupported" if blocker.code == "fem_backend_unsupported"
             else "imported_engine_unsupported"), blocker.message,
            details={"engine": blocker.engine, "reason_code": blocker.code,
                     "capable_engines": [r.engine for r in blocker.remedies if r.kind == "use_engine"],
                     "blocker": blocker.model_dump(mode="json")},
        )
    error = SymmetryValidationError(blocker.message)
    error.blocker = blocker
    error.details = {"blocker": blocker.model_dump(mode="json")}
    raise error


async def _settled_candidate(info, registry):
    """Wait for BEMPP qualification only when selection reaches that candidate."""
    if info.qualification == "pending":
        detected = await registry.wait_for_bempp()
        return next((candidate for candidate in detected if candidate.name == info.name), info)
    return info


async def _parametric_engine_verdict(info, request, registry, quadrants):
    """Geometry-specific admission, shared by selection and offered remedies."""
    from server.engines.registry import engine_supports_symmetry

    mounting = _requested_mounting(request)
    if mounting == "ground-plane" and "ground-plane" not in info.mountings:
        return EngineVerdict(False, "capability", "engine_mounting_unsupported",
                             "it cannot solve above a rigid ground plane; choose a compatible engine or turn the ground plane off")
    if mounting == "infinite-baffle" and "infinite-baffle" not in info.mountings:
        return EngineVerdict(False, "capability", "engine_mounting_unsupported",
                             "it cannot solve a coupled infinite baffle; choose a compatible engine or set the simulation type to free-standing")
    if (info.name == "bempp" and mounting == "infinite-baffle"
            and request.options.symmetry != "auto" and quadrants != 1234):
        return EngineVerdict(False, "capability", "engine_symmetry_unsupported",
                             "BEMPP coupled infinite-baffle currently requires Full symmetry; use Auto/Full or choose Metal for half/quarter domains")
    if (not (info.name == "bempp" and mounting == "infinite-baffle" and request.options.symmetry == "auto")
            and not engine_supports_symmetry(info, quadrants)):
        return EngineVerdict(False, "capability", "engine_symmetry_unsupported",
                             f"it does not support { {1234: 'full', 12: 'the xz half', 14: 'the yz half', 1: 'quarter'}.get(quadrants, quadrants)} symmetry; choose a compatible engine or change the requested symmetry")
    try:
        _apply_bempp_wall_default(request, info.name)
    except SymmetryValidationError as exc:
        return EngineVerdict(False, "capability", "engine_geometry_unsupported", str(exc))
    if not info.available:
        return EngineVerdict(False, "availability", "engine_unavailable", info.reason)
    if await registry.get_engine(info.name) is None:
        return EngineVerdict(False, "adapter", "engine_unavailable",
                             await registry.unavailable_reason(info.name))
    return EngineVerdict(True)


async def resolve_submission(
    request: SolveRequest,
    engine_registry: EngineRegistry,
    *,
    preview: bool = False,
) -> SubmissionResolution:
    """Resolve every host-dependent parametric submission decision once.

    ``preview=True`` reports admission refusals in the plan while resolving the
    geometry. The same checks run in the same order for preview and solve.

    This boundary deliberately stops before UUID allocation or store access so
    non-HTTP callers can ask the runtime exactly the same questions as a real
    submission without creating a job. Imported geometry remains tied to its
    ingestion record and immutable mesh artifact, so JobRuntime.submit retains
    that separate preparation and refusal path; its engine choice is
    ``resolve_imported_submission``, below.
    """

    if not isinstance(request.geometry, ParametricGeometrySource):
        raise ImportedSolveRefusal(
            "imported_submission_requires_runtime",
            "imported geometry must be prepared by JobRuntime.submit",
        )

    refusal: str | None = None

    def refuse(error: ValueError | EngineUnavailableError) -> None:
        nonlocal refusal
        if not preview:
            raise error
        if refusal is None:
            refusal = str(error)

    _refuse_removed_solver(request)
    original_requested = request.options.engine
    request = _explicit_accuracy(request)
    engine_name = request.options.engine
    if engine_name not in SELECTABLE_ENGINE_NAMES:
        raise UnknownEngineError(f"Unknown solve engine: {engine_name}")

    # ``auto`` remains a backwards-compatible wire value for Full 3D.
    solver_mode = str(request.options.solver_mode or "full_3d").strip().lower()
    resolution = await asyncio.to_thread(resolve_symmetry, request.design)
    # Subtract the mirror plane a ground plane makes unavailable before the
    # mode is validated, so AUTO degrades quarter to half_yz on a floor and
    # a forced conflicting mode fails naming the ground plane -- instead of
    # meshing a reduced domain the solver will then refuse.
    resolution = restrict_for_ground_plane(
        resolution, _ground_plane_axis(request)
    )
    try:
        resolved_quadrants = validate_symmetry_mode(
            request.options.symmetry, resolution
        )
    except ValueError as exc:
        refuse(SymmetryValidationError(str(exc)))
        resolved_quadrants = resolution.quadrants
    symmetry_metadata = {
        "requested": request.options.symmetry,
        "resolved_quadrants": resolved_quadrants,
        "auto_resolution": resolution.as_dict(),
        "design_quadrants": _design_quadrants_text(request),
    }
    symmetry_metadata["solver_plan"] = {
        "formulation": "full-3d",
        "engine": engine_name,
        "accuracy": request.options.accuracy,
        "requested": original_requested,
        "reason": (
            "explicit solver_mode='full_3d'"
            if solver_mode == "full_3d"
            else "legacy solver_mode='auto' defaults to full-3d"
        ),
        "eligibility_reasons": [],
    }
    detected = await engine_registry.capabilities()
    async def verdict(info):
        info = await _settled_candidate(info, engine_registry)
        return await _parametric_engine_verdict(info, request, engine_registry, resolved_quadrants)
    selection = await select_engine(engine_name, request.options.accuracy, detected, verdict)
    if isinstance(selection, SolveBlocker):
        if refusal is None:
            symmetry_metadata["solver_plan"]["readiness"] = selection.model_dump(mode="json")
        try:
            _raise_engine_blocker(selection)
        except (EngineUnavailableError, SymmetryValidationError) as exc:
            refuse(exc)
    else:
        engine_name = selection.engine
        request = request.model_copy(deep=True)
        request.options.engine = engine_name
        symmetry_metadata["solver_plan"]["engine"] = engine_name
        symmetry_metadata["solver_plan"]["eligibility_reasons"] = selection.passed_over
        if selection.fallback_reason:
            symmetry_metadata["solver_plan"]["fallback_reason"] = selection.fallback_reason

    if (
        _ground_plane_axis(request) is not None
        and request.design.root.simulation.sim_type == "infinite-baffle"
    ):
        # Two half-space models, each claiming the whole exterior. Refused here
        # rather than in an adapter because only bempp's implemented it, so the
        # pair went through on any other engine -- and paid for it twice, since
        # restrict_for_ground_plane subtracts the ground axis's mirror plane
        # before the engine is even chosen. The user was charged roughly double
        # the mesh for a boundary the solve then discarded.
        refuse(SymmetryValidationError(
            "A coupled infinite baffle and a rigid ground plane are two "
            "different half-space boundaries and cannot be combined. The "
            "baffle already mounts the mouth in an unbounded wall; turn the "
            "ground plane off, or set the simulation type to free-standing."
        ))
    if (
        engine_name == "bempp"
        and request.design.root.simulation.sim_type == "infinite-baffle"
    ):
        resolved_quadrants = 1234
        symmetry_metadata["resolved_quadrants"] = resolved_quadrants
        symmetry_metadata["solver_plan"]["symmetry_reason"] = (
            "BEMPP coupled infinite-baffle uses the validated full-domain formulation"
        )
    wall_adjustment = None
    try:
        request, wall_adjustment = _apply_bempp_wall_default(request, engine_name)
    except SymmetryValidationError as exc:
        refuse(exc)
    if wall_adjustment is not None:
        symmetry_metadata["solver_plan"].setdefault("adjustments", []).append(
            wall_adjustment
        )
    if request.design.root.enclosure is not None:
        from hornlab_mesher.config_builder import _enclosure_from_config, resolve_enclosure_bounds
        from server.mesh.builder import _solver_mesher_config

        # Validate the exact execution domain, including engine adjustments.
        # The mesher owns both the resolved mouth and the baffle predicate;
        # symmetry sampling alone never constructs the enclosure bounds.
        execution = _execution_request(request, resolved_quadrants=resolved_quadrants)
        try:
            config = _solver_mesher_config(execution.design)
            # Editor defaults can retain an inactive enclosure block. Reuse
            # the mesher's enclosure resolver: native standalone geometry has
            # no enclosure attribute and cannot enter the bounds validator.
            enclosure = _enclosure_from_config(
                config, config.get("mesh", {}), config.get("enclosure", {}),
                config["formula"],
            )
            if enclosure is not None:
                await asyncio.to_thread(resolve_enclosure_bounds, config, warn_prefix=None)
        except ValueError as exc:
            raise SymmetryValidationError(str(exc)) from exc
    if refusal is not None:
        symmetry_metadata["solver_plan"]["refusal"] = refusal
    return SubmissionResolution(
        request=request,
        engine_name=engine_name,
        symmetry_metadata=symmetry_metadata,
    )


async def _imported_request_refusal_async(request, record=None, msh_text=None, *, cancel_check=None):
    """Inspect geometry off-loop, sharing admission and job cancellation checks."""
    import threading
    from server.solver.component_symmetry import (GeometryCheckCancelled,
                                                 geometry_cancellation)

    stopped = threading.Event()
    def check():
        if stopped.is_set():
            raise GeometryCheckCancelled()
        if cancel_check is not None:
            cancel_check()
    def inspect():
        with geometry_cancellation(check):
            check()
            result = _imported_request_refusal(request, record, msh_text)
            check()
            return result
    work = asyncio.create_task(asyncio.to_thread(inspect))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        stopped.set()
        # Drain the worker so cancellation cannot leave geometry work running.
        try:
            await asyncio.shield(work)
        except (GeometryCheckCancelled, _CancelledAtCheckpoint):
            pass
        except Exception:
            logger.exception("Geometry worker failed while draining task cancellation")
        raise


#: ``resolve_imported_submission`` checks the request itself unless told this.
_REQUEST_UNCHECKED: Any = object()


#: The geometry source an engine must declare to be offered imported geometry.
async def resolve_imported_submission(
    request: SolveRequest,
    engine_registry: EngineRegistry,
    *,
    symmetry_metadata: Mapping[str, Any] | None = None,
    imported_record: Mapping[str, Any] | None = None,
    imported_msh_text: str | None = None,
    request_refusal: tuple[str, str] | None = _REQUEST_UNCHECKED,
) -> SubmissionResolution:
    """Choose the engine for an imported-geometry submission from capability data.

    The imported counterpart of ``resolve_submission``, returning the same
    shape. An engine is offered imported geometry because its ``EngineInfo``
    declares ``"imported"`` among its ``geometry_sources`` -- the declaration is
    the contract, as ``mountings`` is for a ground plane -- never because of its
    name. Metal and BEAT's CPU backend declare it, so AUTO takes Metal where
    Metal runs and otherwise walks the same order ``resolve_auto_engine`` walks
    for a parametric design: ready BEAT CPU precedes BEMPP, including in Fast.

    Declaring imported geometry is not the whole test, because a return can ask
    for something a declaring engine still cannot do. A candidate must also:

    * mirror the ingestion-resolved domain (``symmetry_domains``). A full-3-D
      solver handed a cut mesh it cannot mirror returns a wrong answer rather
      than an error, so BEAT's missing y-only half is refused here, by name;
    * declare every imported feature the request uses (``imported_features``);
    * pass its adapter's own ``imported_preflight`` on the record and mesh,
      when it has one: the checks only the geometry can answer, such as
      whether the BEAT request compiles within its qualified capabilities.

    * ``auto`` takes the first available engine, in ``full3d_engine_order()``,
      that declares imported geometry and passes those checks.
    * The legacy family name ``beat`` takes the best such BEAT backend.
    * Any other engine must declare imported geometry itself; otherwise the
      request is refused with ``imported_engine_unsupported``, naming the
      engines that do. An explicit engine that fails one of the checks above is
      refused with the reason that check names.
    * A rigid ground plane stays refused on every engine.

    An engine that declares imported geometry but is unavailable on this host
    raises ``EngineUnavailableError``: the host lacks a capability, the request
    is not wrong. When every candidate AUTO could use was refused by one of the
    checks, the refusal names each reason instead. The decision, and every
    engine passed over on the way to it, is recorded in ``solver_plan``.

    ``symmetry_metadata`` is the domain ``JobRuntime._prepare_imported_submission``
    resolved from the ingestion record, and ``imported_record`` and
    ``imported_msh_text`` are the record and verified mesh it read. Preparation
    reads the CAD store and the mesh artifact, so it stays in the runtime. This
    function never allocates or persists a job.

    ``request_refusal`` is ``_imported_request_refusal``'s answer when the
    caller already has it for this request and record (the planner does).
    It reads only the geometry, the record and the ground plane, never the
    engine, so an engine rerouted below does not change it.
    """

    if not isinstance(request.geometry, ImportedGeometrySource):
        raise ValueError(
            "resolve_imported_submission resolves imported geometry only; "
            "parametric submissions resolve through resolve_submission"
        )
    request = widen_polar_to_derivation(request, (imported_record or {}).get("polar_grid_derivation"))
    _refuse_removed_solver(request)
    original_requested = request.options.engine
    fem = bool(imported_record and imported_record.get("fem_regions") and not request.geometry.exterior_only)
    request = request if fem else _explicit_accuracy(request)
    requested = request.options.engine
    if request_refusal is _REQUEST_UNCHECKED:
        request_refusal = await _imported_request_refusal_async(request, imported_record, imported_msh_text)
    if request_refusal is not None:
        raise ImportedSolveRefusal(*request_refusal)
    if requested not in SELECTABLE_ENGINE_NAMES:
        raise UnknownEngineError(f"Unknown solve engine: {requested}")

    detected = await engine_registry.capabilities()
    needed_features = _imported_features_needed(request.geometry, imported_record)
    resolved_quadrants = (symmetry_metadata or {}).get("resolved_quadrants")

    async def verdict(info, *, explicit=False):
        info = await _settled_candidate(info, engine_registry)
        declared = _imported_capabilities(
            [info], _imported_accuracy(request), info.name if explicit else requested,
            fem_regions="fem-regions" in needed_features,
        )
        return await imported_engine_verdict(
            declared[info.name], resolved_quadrants=resolved_quadrants,
            needed_features=needed_features, engine_registry=engine_registry,
            imported_record=imported_record, imported_msh_text=imported_msh_text,
            drive_channels=request.geometry.drive_channels, request=request,
        )

    async def alternative(info):
        return await verdict(info, explicit=True)

    fem_reference = ("Interior-only FEM uses CPU float64" if _fem_interior_only(request, imported_record)
                     else "AUTO FEM uses the CPU float64 reference") if fem else None
    selection = await select_engine(requested, request.options.accuracy, detected, verdict,
                                    alternative_verdict=alternative, preferred=("beat-cpu",) if fem else ())
    if isinstance(selection, SolveBlocker):
        if fem_reference and requested in {"auto", "beat"} and selection.engine == "beat-cpu":
            selection.message += f" {fem_reference}."
        _raise_engine_blocker(selection, imported=True)
    selected = selection.engine
    passed_over = selection.passed_over
    cpu_fallback = selection.fallback_reason
    if fem and requested in {"auto", "beat"} and selected == "beat-cpu":
        cpu_fallback = fem_reference
    reason = ("AUTO selected the first available engine, in AUTO order, that declares imported geometry"
              if requested == "auto" else "legacy engine='beat' selected the best available BEAT backend that declares imported geometry"
              if requested == "beat" else cpu_fallback or f"explicit engine={requested!r} declares imported geometry")
    if selected != requested:
        request = request.model_copy(deep=True)
        request.options.engine = selected
    metadata = dict(symmetry_metadata or {})
    metadata["solver_plan"] = {
        "formulation": "full-3d",
        "engine": selected,
        "accuracy": request.options.accuracy,
        # The user's own choice, kept beside what it resolved to: recalling a
        # run must restore this, not the engine AUTO happened to pick.
        "requested": original_requested,
        "reason": reason,
        "eligibility_reasons": passed_over,
    }
    if fem:
        metadata["solver_plan"]["precision"] = selection.precision
    if cpu_fallback is not None:
        metadata["solver_plan"]["fallback_reason"] = cpu_fallback
    return SubmissionResolution(
        request=request,
        engine_name=selected,
        symmetry_metadata=metadata,
    )


async def plan_imported_submission(
    request: SolveRequest,
    engine_registry: EngineRegistry,
    *,
    symmetry_metadata: Mapping[str, Any] | None = None,
    imported_record: Mapping[str, Any] | None = None,
    imported_msh_text: str | None = None,
) -> dict[str, Any]:
    """Every engine's verdict on one imported request, and where it resolves.

    The per-record capability the solver selector shows. Each full-3-D engine
    this host detects is asked ``imported_engine_verdict`` -- the function
    ``resolve_imported_submission`` walks -- and the request itself is then
    resolved exactly as a submission would be, so the enabled entries, the
    reasons beside the disabled ones and the engine a Solve would run on come
    from one capability, with ready BEAT CPU before BEMPP in Fast too.
    Reads only; never allocates or persists a job.
    """

    if not isinstance(request.geometry, ImportedGeometrySource):
        raise ValueError("the imported plan resolves imported geometry only")
    request = widen_polar_to_derivation(request, (imported_record or {}).get("polar_grid_derivation"))
    _refuse_removed_solver(request)
    detected = await engine_registry.capabilities()
    resolved_quadrants = (symmetry_metadata or {}).get("resolved_quadrants")
    needed_features = _imported_features_needed(request.geometry, imported_record)
    request_refusal = await _imported_request_refusal_async(request, imported_record, imported_msh_text)
    verdicts: list[ImportedEngineVerdict] = []
    for name in full3d_engine_order():
        # Each engine's verdict answers "if this engine were picked", which is
        # what the selector offers; the resolution below stays keyed on the
        # request, so AUTO is unchanged.
        info = _imported_capabilities(detected, request.options.accuracy, name,
                                      fem_regions="fem-regions" in needed_features).get(name)
        if info is None:
            continue
        if request_refusal is not None:
            code, reason = request_refusal
            verdicts.append(
                ImportedEngineVerdict(name, info.display_label(), False, "request", code, reason)
            )
            continue
        verdicts.append(
            await imported_engine_verdict(
                info,
                resolved_quadrants=resolved_quadrants,
                needed_features=needed_features,
                engine_registry=engine_registry,
                imported_record=imported_record,
                imported_msh_text=imported_msh_text,
                drive_channels=request.geometry.drive_channels,
                request=request,
            )
        )
    engine: str | None = None
    code: str | None = None
    readiness = None
    try:
        resolution = await resolve_imported_submission(
            request,
            engine_registry,
            symmetry_metadata=symmetry_metadata,
            imported_record=imported_record,
            imported_msh_text=imported_msh_text,
            request_refusal=request_refusal,
        )
    except ImportedSolveRefusal as exc:
        code, reason = exc.reason_code, str(exc)
        readiness = exc.details.get("blocker")
    except EngineUnavailableError as exc:
        code, reason = "engine_unavailable", str(exc)
        readiness = exc.details.get("blocker")
    except UnknownEngineError as exc:
        code, reason = "unknown_engine", str(exc)
    else:
        engine = resolution.engine_name
        reason = str(resolution.symmetry_metadata["solver_plan"]["reason"])
    return {
        "ingest_id": request.geometry.ingest_id,
        "effective_polar_config": request.options.polar_config.model_dump(mode="json"),
        "readiness": readiness,
        "requested": request.options.engine,
        "engine": engine,
        "code": code,
        "reason": reason,
        "domain": (symmetry_metadata or {}).get("resolved"),
        "domain_decision": (
            decision_summary(imported_record) if imported_record is not None else None
        ),
        "engines": [asdict(verdict) for verdict in verdicts],
    }


class _CadLanePort:
    """The runtime as the CAD preparation lane sees it (``cad_preparation.CadJobPort``)."""

    def __init__(self, runtime: "JobRuntime") -> None:
        self._runtime = runtime

    @property
    def job_store(self) -> JobStore:
        return self._runtime.store

    @property
    def binding_refusals(self) -> tuple[type[BaseException], ...]:
        # A submission-key conflict is not among them: binding uses no key.
        return (
            UnknownEngineError,
            SymmetryValidationError,
            ImportedSolveRefusal,
            EngineUnavailableError,
            # A stale stored setup that still says axial motion: the message
            # names the removal, and solving the same request again cannot help.
            RemovedSolverError,
        )

    def restart_refusal(self) -> str | None:
        approval = self._runtime.restart_approval
        return approval.refusal() if approval is not None else None

    def publish(self, event: Mapping[str, Any] | None) -> None:
        """Publish an event from any thread: the lane's writes run in worker threads."""

        runtime = self._runtime
        loop = runtime._loop
        if event is None or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(runtime.events.publish, event)
        except RuntimeError:  # the loop closed in between
            pass

    async def bind_cad_job(
        self, job_id: str, request: SolveRequest, cad_provenance: Mapping[str, Any]
    ) -> str:
        return await self._runtime._bind_cad_job(job_id, request, cad_provenance)


class _CancelledAtCheckpoint(RuntimeError):
    pass


class _RuntimeOwnershipLock:
    """Exclude a second scheduler from recovering or executing the same DB."""

    def __init__(self, store: JobStore) -> None:
        self.path = store.db_path.with_suffix(store.db_path.suffix + ".runtime.lock")
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        try:
            lock_exclusive(descriptor)
        except BlockingIOError:
            os.close(descriptor)
            raise JobConflictError(
                "Another job runtime already owns this Waveguide Generator database"
            ) from None
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            unlock(descriptor)
        finally:
            os.close(descriptor)


@dataclass
class _PendingRuntimeUpdate:
    """Latest visible state, buffered logs, and the pinned in-flight prefix."""

    persisted_stage: str | None
    last_persisted_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stage: str | None = None
    progress: float | None = None
    message: str | None = None
    log_lines: list[str] = field(default_factory=list)
    timer_task: asyncio.Task[Any] | None = None
    expected_log_size: int | None = None
    pinned_log_lines: tuple[str, ...] | None = None
    closed: bool = False

    @property
    def pending(self) -> bool:
        return self.stage is not None or bool(self.log_lines)


@dataclass
class _JobMutationTurnstile:
    """One job's serialized-mutation lock, and how many callers still need it.

    The count is what makes the lock safe to drop: releasing an
    ``asyncio.Lock`` clears ``locked()`` before a queued waiter resumes, so a
    liveness check on the lock alone would discard a turnstile someone is
    still queued on and let the next caller build a second one.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class JobRuntime:
    """One-worker FIFO runtime with a durable HTTP correctness path."""

    def __init__(
        self,
        store: JobStore,
        *,
        engine_registry: EngineRegistry | None = None,
        cadlink_store: CadLinkStore | None = None,
        persistence_interval_seconds: float = RUNTIME_PERSIST_INTERVAL_SECONDS,
        metal_permit: MetalPermit | None = None,
        restart_approval: RestartApproval | None = None,
        results_auto_cleanup: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        #: Whether stored results are pruned by age and count (the user's
        #: "clean up old results" preference, ``server/jobs/retention.py``).
        #: Asked at startup and after every job, so a change applies without
        #: a restart. Without one -- an embedded caller with no settings --
        #: results are kept, which is the preference's default.
        self._results_auto_cleanup = results_auto_cleanup
        #: The server's restart-approved latch (contract §4.2, §4.3). While it
        #: is set no queued job starts, and a shutdown ends running jobs as
        #: ended by the update restart rather than by Quit.
        self.restart_approval = restart_approval
        #: Jobs this runtime's own shutdown stopped for the update restart.
        self._restart_interrupted: set[str] = set()
        #: The loop the scheduler runs on, for a latch released from a thread.
        self._loop: asyncio.AbstractEventLoop | None = None
        #: When a held queue reads the latch again, so the approval's expiry
        #: (§4.2) starts it even if nothing else reads the latch.
        self._expiry_timer: asyncio.TimerHandle | None = None
        if restart_approval is not None:
            restart_approval.add_release_listener(self._restart_called_off)
        self.cadlink_store = cadlink_store
        # Installed by CAD Link assembly. Failure preserves the durable job
        # reference so an unavailable model can be pinned on the next attempt.
        self.protect_cad_document: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None
        self.engine_registry = engine_registry or EngineRegistry(factory=get_engine)
        self.events = EventBroker()
        self._queue: deque[str] = deque()
        self._running: set[str] = set()
        self._cancellation_flags: dict[str, threading.Event] = {}
        self._native_tasks: set[asyncio.Task[Any]] = set()
        #: The CAD preparation lane: accepted CAD solves waiting for a worker,
        #: the ones being prepared now, and what prepares them
        #: (``configure_cad_preparation``). Separate from ``_queue`` and
        #: ``_running``: a solve that is still being prepared is not queued.
        self._prep_queue: deque[str] = deque()
        self._prep_running: set[str] = set()
        self._cad_host: CadPreparationHost | None = None
        self._cad_port = _CadLanePort(self)
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._pending_updates: dict[str, _PendingRuntimeUpdate] = {}
        # Live rows are process-local by design. The final result remains the
        # sole durable/canonical artifact committed by ``complete_job``.
        self._partial_results: dict[str, dict[str, Any]] = {}
        self._persistence_interval_seconds = max(
            0.01, float(persistence_interval_seconds)
        )
        self._started = False
        self._shutting_down = False
        #: A stop has begun (``mark_running_interrupted_by_quit``), which can be
        #: seconds before ``shutdown`` runs. Admission closes then, not later.
        self._stopping = False
        #: Jobs this runtime's own shutdown asked to stop (``_cancel_job``).
        self._quit_interrupted: set[str] = set()
        self._start_lock = asyncio.Lock()
        # One turnstile per job whose stored results are being rewritten in
        # place. Only recombine does that today; the map holds nothing for a
        # job with no such request in flight.
        self._result_mutation: dict[str, _JobMutationTurnstile] = {}
        self._ownership = _RuntimeOwnershipLock(store)
        self.metal_permit = metal_permit or process_metal_permit()
        self.field_plane_service = FieldPlaneService(
            store,
            self.metal_permit,
            solve_queued=lambda: bool(self._queue or self._running),
        )

    @property
    def background_tasks(self) -> frozenset[asyncio.Task[Any]]:
        """Expose strong task refs for invariant tests and graceful shutdown."""

        return frozenset(self._background_tasks)

    @property
    def running_job_ids(self) -> frozenset[str]:
        return frozenset(self._running)

    def _results_cleanup_enabled(self) -> bool:
        """Whether this prune may remove results; anything but a clear yes keeps them."""

        policy = self._results_auto_cleanup
        if policy is None:
            return False
        try:
            return policy() is True
        except Exception:
            logger.exception("Could not read the automatic result cleanup preference")
            return False

    def _restart_pending(self) -> bool:
        """Whether an update restart is approved, so no queued job may start."""

        approval = self.restart_approval
        return approval is not None and approval.pending is not None

    def _restart_called_off(self, _target: str, _reason: str) -> None:
        # The latch came down with no restart (contract §4.2): the jobs it held
        # in the queue may start. Called from whichever thread released it.
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._wake)
        except RuntimeError:  # the loop closed in between
            pass

    def _wake(self) -> None:
        """Start whatever an approved restart was holding: queued solves and preparations."""

        self._ensure_scheduler()
        self._ensure_prep_lane()

    def _check_again_when_the_approval_expires(self) -> None:
        # The latch expires only when something reads it (§4.2). A held queue
        # reads it again when it is due, so the expiry starts the queue even if
        # no route or status read comes. On the loop thread.
        approval = self.restart_approval
        remaining = approval.remaining() if approval is not None else None
        loop = self._loop
        if remaining is None or loop is None or loop.is_closed():
            return
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
        self._expiry_timer = loop.call_later(remaining + 0.05, self._wake)

    def _mark_running(
        self, job_id: str, fields: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> tuple[bool, dict[str, Any] | None]:
        """Mark a queued job running, unless an update restart is approved (§4.3).

        The mark is written under the lock that approving a restart takes, so
        the two are ordered. Either the job was marked running before the
        approval, and the restart ends it with its own reason, or it is not
        marked at all and goes back to the front of the queue. Returns
        whether it was admitted, and the store's event.
        """

        approval = self.restart_approval
        if approval is None:
            admitted, event = True, self.store.start_job(job_id, fields, payload)
        else:
            admitted, event = approval.admit(
                lambda: self.store.start_job(job_id, fields, payload)
            )
            if not admitted:
                self._queue.appendleft(job_id)
        if admitted and event is not None and self._stopping:
            # A stop began between this job's last admission check and its
            # start, after the stop's own marks were written. Mark it too, so
            # the next start never reads it as a crash.
            self.mark_running_interrupted_by_quit("a job started as the stop began")
        return admitted, event

    async def start(self) -> None:
        """Initialize storage, fail running orphans, and requeue queued rows."""

        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            self._shutting_down = False
            self._stopping = False
            self._loop = asyncio.get_running_loop()
            await asyncio.to_thread(self._ownership.acquire)
            try:
                await asyncio.to_thread(self.store.initialize)
                queued, recovery_events = await asyncio.to_thread(
                    self.store.recover_on_startup,
                    RESTART_RECOVERY_MESSAGE,
                    quit_stage_message=QUIT_INTERRUPTED_STAGE_MESSAGE,
                    quit_error_message=QUIT_INTERRUPTED_MESSAGE,
                    update_restart_stage_message=UPDATE_RESTART_STAGE_MESSAGE,
                    update_restart_error_message=UPDATE_RESTART_MESSAGE,
                    preparing_error_message=CAD_INTERRUPTED_MESSAGE,
                )
                unheld_preparations = await asyncio.to_thread(
                    self.store.unheld_preparing_job_ids
                )
                # Read on the loop, where the settings routes write it. Before
                # any client has connected, so this is the persisted value.
                prune_results = self._results_cleanup_enabled()
                await asyncio.to_thread(
                    self.store.prune_terminal_jobs,
                    retention_days=RESULT_RETENTION_DAYS,
                    max_terminal_jobs=MAX_RETAINED_RESULTS,
                    prune_results=prune_results,
                )
            except BaseException:
                await asyncio.to_thread(self._ownership.release)
                raise
            self._queue.extend(row["id"] for row in queued)
            # A CAD solve accepted but not yet prepared when WG stopped is
            # prepared now; one a lane was preparing ended as interrupted above.
            self._prep_queue.extend(unheld_preparations)
            self._started = True
            for event in recovery_events:
                self.events.publish(event)
            self._wake()

    def mark_running_interrupted_by_quit(self, reason: str = "") -> list[str]:
        """Record, the moment a stop begins, that Quit interrupted every running job.

        Called from the shutdown backstop's own thread (``launch/serve.py``),
        not the event loop, and ahead of ``shutdown``, which runs only after
        Uvicorn's drain and the handlers registered before it. If the process
        ends in between, the next start still reads these jobs as interrupted
        by Quit rather than as a crash -- or as ended by the update restart,
        when one is approved (contract §4.3), as ``shutdown`` would mark them.
        It closes admission first, so no queued job starts unmarked in the
        seconds before ``shutdown`` runs; one that was already past its last
        check marks itself (``_mark_running``). Only for a stop that ends this
        runtime: admission stays closed until the next ``start``. Thread-safe
        through the store's lock; never raises. Returns the ids it marked.
        """

        self._stopping = True
        try:
            update_restart = self._restart_pending()
            marked = self.store.mark_running_interrupted_by_quit(
                interrupted_by_update_restart=update_restart
            )
        except Exception:  # noqa: BLE001 - a stop must proceed whatever the store does
            logger.exception("Could not mark running jobs as interrupted by Quit")
            return []
        if marked:
            logger.info(
                "Stop began (%s) with %d running job(s); marked %s: %s",
                reason or "no reason given",
                len(marked),
                "ended by the update restart" if update_restart else "interrupted by Quit",
                ", ".join(marked),
            )
        return marked

    async def shutdown(self) -> None:
        """Cooperatively stop jobs, leaving timeout leftovers to startup recovery.

        Each running job is marked interrupted by Quit in the same write that
        requests its cancellation, before any wait, so the reason survives even
        if the process's shutdown budget ends it mid-wait. The checkpoint wait
        itself is bounded by that budget when one is running
        (``shutdown_wait_limit``), so the owned-process cleanup registered after
        this handler still gets its turn.
        """

        self._shutting_down = True
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        # A shutdown with a restart approved is the update restart: the
        # launcher consumed the handoff request and is stopping this server to
        # replace it (contract §4.3). It marks its jobs as ended by the update
        # restart, not by Quit.
        update_restart = self._restart_pending()
        # Stop accepting new buffered callbacks, then persist the last accepted
        # checkpoint before requesting cancellation. Cancelling first could
        # abandon an asyncio.to_thread file/SQLite write that is already running.
        await self._flush_all_runtime_updates(forget=True)
        tasks = tuple(self._background_tasks)

        cancellation_signalled = False
        for job_id in tuple(self._running):
            signalled = self._request_shutdown_cancellation(job_id, update_restart)
            cancellation_signalled = cancellation_signalled or signalled

        pending: set[asyncio.Task[Any]] = set(tasks)
        if tasks and cancellation_signalled:
            _done, pending = await asyncio.wait(
                tasks, timeout=shutdown_wait_limit(SHUTDOWN_TASK_TIMEOUT_SECONDS)
            )
        for task in pending:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._scheduler_task = None
        self._prep_queue.clear()
        self._prep_running.clear()
        self._background_tasks.clear()
        # Store connections are long-lived now. Windows will not let a test's
        # temporary directory be removed, nor the migration tool replace the
        # database, while a handle is still open on it.
        await asyncio.to_thread(self.store.close)
        await asyncio.to_thread(self._ownership.release)
        self._partial_results.clear()
        self._quit_interrupted.clear()
        self._restart_interrupted.clear()
        self._started = False

    def _request_shutdown_cancellation(self, job_id: str, update_restart: bool) -> bool:
        """Record the stop reason before a job takes its cancellation path."""

        requested = "Update restart requested" if update_restart else "Shutdown requested"
        try:
            event = self.store.request_cancellation(
                job_id,
                {
                    "stage": "cancelling",
                    "stage_message": f"{requested}; waiting for current stage checkpoint",
                    "cancellation_requested": True,
                },
                {"stage": "cancelling", "message": requested},
                interrupted_by_quit=True,
                interrupted_by_update_restart=update_restart,
            )
        except Exception:
            logger.exception(
                "Could not request cancellation during shutdown for job %s", job_id,
            )
            return False
        if event is not None:
            # None means the user had already asked to stop it; that
            # job stays a user cancellation.
            self._quit_interrupted.add(job_id)
            if update_restart:
                self._restart_interrupted.add(job_id)
            self.events.publish(event)
        state = self.store.cancellation_state(job_id)
        requested = bool(state is not None and state[0] == "running" and state[1])
        flag = self._cancellation_flags.get(job_id)
        if requested and flag is not None:
            flag.set()
        return requested

    async def submit(
        self,
        request: SolveRequest,
        *,
        cad_provenance: Mapping[str, Any] | None = None,
        setup_revision_id: str | None = None,
        _validation: SubmissionValidation | None = None,
    ) -> str:
        """Queue a solve.

        ``cad_provenance`` is the CAD operation's record of what it resolved
        (setup revision, frame, operation and preparation). It is not part of
        the wire request: it is kept on the job as ``task_metadata.cad``.
        ``setup_revision_id`` is the setup revision an imported request was
        made from when no CAD provenance names one (a file model's solve,
        ``server/jobs/model_solve.py``).
        """

        _refuse_removed_solver(request)
        await self.start()
        ingest_id = getattr(request.geometry, "ingest_id", None)
        touch = getattr(self.cadlink_store, "touch_ingest", None)
        if ingest_id and touch is not None:
            # Marked used before anything reads it, so a prepared-mesh
            # collection deciding meanwhile keeps it (server/mesh/prepared_gc.py).
            await asyncio.to_thread(touch, str(ingest_id))
        submission_key = request.client_request_id
        submission_request_sha256 = canonical_json_sha256(
            _submission_identity(request)
        )
        if submission_key is not None:
            existing_job_id = await asyncio.to_thread(
                self.store.resolve_submission,
                submission_key,
                submission_request_sha256,
            )
            if existing_job_id is not None:
                return existing_job_id
        composed = await self._compose_job(request, cad_provenance, setup_revision_id, validation=_validation)
        imported = composed.imported

        job_id = str(uuid.uuid4())
        now = _now_iso()
        job_record = {
            "id": job_id,
            "parent_job_id": composed.request.parent_job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "progress": 0.0,
            "stage": "queued",
            "stage_message": "Job queued",
            "error_message": None,
            "cancellation_requested": False,
            "config_json": composed.request.model_dump(mode="json"),
            "config_summary_json": composed.config_summary,
            "has_results": False,
            "has_mesh_artifact": imported is not None,
            "mesh_stats": composed.mesh_stats,
            "label": composed.request.label,
            "script_snapshot": composed.script_snapshot,
            "task_metadata": composed.task_metadata,
        }
        initial_event = ("queued", {"status": "queued", "progress": 0.0})
        if submission_key is not None:
            claimed_job_id, created, event = await asyncio.to_thread(
                self._create_submission,
                job_record,
                idempotent=True,
                submission_key=submission_key,
                request_sha256=submission_request_sha256,
                initial_event=initial_event,
                mesh_artifact=composed.mesh_artifact,
            )
            if not created:
                return claimed_job_id
        elif imported is None:
            event = await asyncio.to_thread(self._create_submission, job_record, initial_event=initial_event)
        else:
            event = await asyncio.to_thread(
                self._create_submission,
                job_record,
                initial_event=initial_event,
                mesh_artifact=composed.mesh_artifact,
            )
        self._queue.append(job_id)
        if event is not None:
            self.events.publish(event)
        self._ensure_scheduler()
        return job_id

    def _create_submission(self, record: dict, *, idempotent: bool = False, **kwargs: Any) -> Any:
        from server.workspace.run_moves import submission_location

        with submission_location(self.cadlink_store, record["config_json"], record["task_metadata"]):
            create = self.store.create_job_idempotent if idempotent else self.store.create_job
            return create(record, **kwargs)

    async def _compose_job(
        self,
        request: SolveRequest,
        cad_provenance: Mapping[str, Any] | None,
        setup_revision_id: str | None = None,
        *, validation: SubmissionValidation | None = None,
    ) -> "_ComposedJob":
        """Everything a job stores of one request, after every submission check.

        The one place a request becomes a job's record: ``submit`` writes it as
        a new queued job, and a CAD solve's binding writes it into the job that
        was being prepared, so both meet the same checks (ingest lookup, hash
        verification, frame, sources, mesh, engine choice, domain decision) and
        store the same things.
        """

        validation = validation or await self.validate_submission(request)
        resolved = validation.resolved
        assert resolved is not None
        imported = (
            await self._prepare_imported_submission(request, validated=validation.imported)
            if validation.imported is not None else None
        )
        request = resolved.request
        symmetry_metadata = resolved.symmetry_metadata

        summary = self._config_summary(request)
        if imported is not None and imported.record.get("producer") == "mesh-file":
            summary["import_origin"] = "mesh-file"
            summary["formula_type"] = "mesh-import"
            summary["mesh_import"] = {
                "source_file": imported.record["source_file"],
                "source_tags": imported.record["source_tags"],
            }
        summary["symmetry"] = symmetry_metadata
        polar_grid = effective_polar_grid(request)
        if isinstance(request.geometry, ParametricGeometrySource):
            assert request.design_snapshot is not None
            script_snapshot = request.design_snapshot.model_dump(mode="json")
        else:
            assert imported is not None
            script_snapshot = imported.anchor_snapshot
        task_metadata: dict[str, Any] = {
            "enclosure_resolution_contract": _ENCLOSURE_RESOLUTION_CONTRACT,
            "solve_accuracy": request.options.accuracy,
            "solver_plan": symmetry_metadata.get("solver_plan"),
            "log_tail": [],
            "design_revision": request.design_revision,
            **request.model_dump(mode="json", include={"design_uid", "design_name", "design_id"}),
            "polar_grid": polar_grid,
            "symmetry": symmetry_metadata,
            "has_pressure_basis_artifact": False,
            "pressure_basis_artifact_bytes": None,
            "has_radiation_impedance_artifact": False,
            "radiation_impedance_artifact_bytes": None,
            "field_plane_available": False,
            "field_trace_bytes": None,
            "unavailable_reason": None,
            "persistence_warnings": [],
        }
        if imported is not None:
            ingest_row = (
                await asyncio.to_thread(self.cadlink_store.get_ingest, request.geometry.ingest_id)
                if self.cadlink_store is not None
                else None
            )
            setup = (cad_provenance or {}).get("setup")
            task_metadata["imported_geometry"] = {
                "ingest_id": request.geometry.ingest_id,
                # The imported model this run belongs to ('' and None: none),
                # the prepared mesh it solved, and the setup revision it was
                # bound to when it has one (server/jobs/models_api.py).
                "model_id": (ingest_row or {}).get("model_id") or None,
                "prepared_id": request.geometry.ingest_id,
                "setup_revision_id": (
                    setup.get("revision_id") if isinstance(setup, Mapping) else None
                ) or setup_revision_id,
                "anchor_design_id": imported.anchor_design_id,
                # A run stays attributable to the design and the CAD document it
                # was solved from long after both have moved on.
                "anchor_lineage_id": imported.anchor_lineage_id,
                "archive_stem": imported.archive_stem,
                "manifest_sha256": request.geometry.manifest_sha256,
                "transformed_geometry_hash": imported.record.get("transformed_geometry_hash"),
                "solve_model_sha256": imported.record.get("solve_model_sha256"),
                # The domain decision this job solves, as ingest sealed it and
                # the submission checked it: planes, kept sides, fraction,
                # frame and source mapping (None for an earlier build's record).
                "domain_decision": decision_summary(imported.record),
                "document": imported.document,
                "identity": imported.identity,
            }
        if imported is not None and cad_provenance:
            task_metadata["cad"] = json.loads(json.dumps(cad_provenance, default=str))
        task_metadata["archive"] = await asyncio.to_thread(
            archive_identity.submission_archive, self.cadlink_store,
            request.model_dump(mode="json"), task_metadata,
        )
        return _ComposedJob(
            request=request,
            config_summary=summary,
            script_snapshot=script_snapshot,
            task_metadata=task_metadata,
            mesh_stats=imported.mesh_stats if imported is not None else None,
            mesh_artifact=imported.msh_text if imported is not None else None,
            imported=imported,
        )

    @staticmethod
    def _validate_imported_model_setup(geometry: ImportedGeometrySource, record: Mapping[str, Any]) -> None:
        """Validate saved physics without approving findings or a CAD frame.

        These checks are also authoritative at Solve. Readiness evidence is
        deliberately checked only when the snapshot is actually submitted.
        """
        file_mesh = record.get("producer") == "mesh-file"
        sources = _imported_record_sources(record)
        source_by_id = {str(source.get("id")): source for source in sources}
        requested_skips = set(geometry.skipped_source_ids)
        unknown_skips = requested_skips - set(source_by_id)
        required_skips = sorted(
            source_id
            for source_id in requested_skips
            if bool(source_by_id.get(source_id, {}).get("required"))
        )
        if unknown_skips:
            raise ImportedSolveRefusal(
                "unknown_skipped_source",
                f"skipped_source_ids names unknown record sources {sorted(unknown_skips)}",
            )
        if required_skips:
            raise ImportedSolveRefusal(
                "required_source_skipped",
                f"required sources cannot be skipped: {required_skips}",
                details={"source_ids": required_skips},
            )
        recorded_skips = set(record.get("skipped_source_ids") or [])
        if recorded_skips != requested_skips:
            raise ImportedSolveRefusal(
                "skipped_sources_mismatch",
                "request skipped_source_ids must match the sources excluded by ingestion",
                details={
                    "request": sorted(requested_skips),
                    "record": sorted(recorded_skips),
                },
            )
        active_sources = set(source_by_id) - requested_skips
        if not active_sources and not (record.get("fem_regions") and not geometry.exterior_only):
            raise ImportedSolveRefusal("drive_source_coverage", "The return has no driven exterior source")
        for channel in geometry.drive_channels:
            if channel.driver is None:
                continue
            source = source_by_id.get(channel.source_ids[0], {})
            observed = source.get("observed")
            observed = observed if isinstance(observed, Mapping) else {}
            area = observed.get("total_area_mm2")
            if not isinstance(area, (int, float)) or float(area) <= 0.0:
                raise ImportedSolveRefusal(
                    "driver_source_area_unavailable",
                    f"channel {channel.id!r} carries a driver model but its "
                    "source has no recorded positive area; re-ingest the return",
                    details={"channel_id": channel.id},
                )
        driven_sources = {
            source_id
            for channel in geometry.drive_channels
            for source_id in channel.source_ids
        }
        if driven_sources != active_sources:
            raise ImportedSolveRefusal(
                "drive_source_coverage",
                "drive channels must cover every non-skipped ingestion source exactly once",
                details={
                    "missing": sorted(active_sources - driven_sources),
                    "extra": sorted(driven_sources - active_sources),
                },
            )
        _validate_passive_cardioid_topology(geometry, record)
        recorded_sizes = record.get("mesh_sizes")
        if file_mesh and geometry.mesh is not None:
            raise ImportedSolveRefusal("mesh_sizes_not_applicable", "This is a ready mesh; import a new mesh to change its triangles.")
        if not file_mesh and not isinstance(recorded_sizes, Mapping):
            raise ImportedSolveRefusal(
                "ingest_record_incomplete",
                "the ingestion record has no authoritative mesh sizes; re-ingest the CAD return",
            )
        if not file_mesh and (geometry.mesh is None or geometry.mesh.model_dump(mode="json") != dict(recorded_sizes)):
            raise ImportedSolveRefusal(
                "mesh_sizes_mismatch",
                "request mesh sizes do not match the sizes used to create the ingestion artifact",
            )


    async def _prepared_model_record(self, request: SolveRequest) -> dict[str, Any]:
        """One identity/feature check for every prepared-mesh producer.

        Read only: planning and admission share it without creating jobs,
        claiming archive folders or interpreting a current CAD connection.
        """
        geometry = request.geometry
        assert isinstance(geometry, ImportedGeometrySource)
        if self.cadlink_store is None:
            raise ImportedSolveRefusal(
                "ingest_registry_unavailable",
                "the prepared-mesh registry is unavailable to this job runtime",
            )
        record = await asyncio.to_thread(
            get_ingestion_record, self.cadlink_store, geometry.ingest_id
        )
        if record is None:
            raise ImportedSolveRefusal(
                "ingest_not_found",
                f"ingestion record {geometry.ingest_id!r} does not exist",
                details={"ingest_id": geometry.ingest_id},
            )
        mismatches = {
            field: {"request": getattr(geometry, field), "record": record.get(field)}
            for field in ("manifest_sha256", "artifact_sha256")
            if getattr(geometry, field) != record.get(field)
        }
        if mismatches:
            raise ImportedSolveRefusal(
                "ingest_sha_mismatch",
                "request hashes do not match the immutable ingestion record",
                details=mismatches,
            )
        from server.contracts.source_contour import validate_contour_request
        try:
            validate_contour_request(geometry, record)
        except ValueError as exc:
            raise ImportedSolveRefusal("native_source_contract", str(exc)) from exc
        if record.get("native_source") is not None and request.options.engine != "metal":
            raise ImportedSolveRefusal("native_source_engine", "native source contours currently require the Metal consumer")
        return record

    async def validate_submission(
        self, request: SolveRequest, *, preview: bool = False,
    ) -> SubmissionValidation:
        """Geometry, engine and setup admission, without a job, archive claim or persistence.

        Plans cover these checks plus the API restart/retirement hold check.
        Submission-key conflicts are idempotency/transport concerns handled only
        by submit; plans do not validate or reserve idempotency keys.

        Plans and job composition use this same boundary. Only the presentation
        of an engine refusal differs in a preview: it includes readiness and,
        for imported geometry, the per-engine verdicts.
        """
        _refuse_removed_solver(request)
        if isinstance(request.geometry, ImportedGeometrySource):
            imported = await self._validate_imported_submission(request)
            request = imported.request
            inputs = dict(
                symmetry_metadata=imported.symmetry_metadata,
                imported_record=imported.record, imported_msh_text=imported.msh_text,
            )
            if preview:
                plan = await plan_imported_submission(request, self.engine_registry, **inputs)
                return SubmissionValidation(None, imported, plan)
            resolved = await resolve_imported_submission(request, self.engine_registry, **inputs)
            return SubmissionValidation(resolved, imported)
        resolved = await resolve_submission(request, self.engine_registry, preview=preview)
        # This also validates any effective observation grid before allocation.
        effective_polar_grid(resolved.request)
        return SubmissionValidation(resolved)

    async def plan_imported(self, request: SolveRequest) -> dict[str, Any]:
        """The submission verdict and engine choices, without writing anything."""
        if not isinstance(request.geometry, ImportedGeometrySource):
            raise ValueError("the imported plan resolves imported geometry only")
        validation = await self.validate_submission(request, preview=True)
        assert validation.imported_plan is not None
        return validation.imported_plan

    async def _validate_imported_submission(
        self, request: SolveRequest,
    ) -> _ImportedValidation:
        """Read and validate the exact prepared inputs a submission would use."""
        geometry = request.geometry
        assert isinstance(geometry, ImportedGeometrySource)
        record = await self._prepared_model_record(request)
        # Every submission -- a direct solve, a retry, a restart, a CAD
        # operation's own -- validates the frame the record was meshed in
        # against one recorded answer: the person's choice, or WG's decision
        # stored for this record at preparation, never a re-derivation
        # (docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame").
        file_mesh = record.get("producer") == "mesh-file"
        frame_refusal = None if file_mesh or record.get("native_source") is not None else await asyncio.to_thread(
            record_frame_refusal, self.cadlink_store, record
        )
        if frame_refusal is not None:
            raise ImportedSolveRefusal(
                FRAME_CONFIRMATION_REQUIRED,
                frame_refusal,
                details={"ingest_id": geometry.ingest_id},
            )

        self._validate_imported_model_setup(geometry, record)

        report_sha = str(record.get("report_sha256") or "")
        acknowledged = set(geometry.acknowledged_findings)
        blocking_ids = [
            str(finding.get("id"))
            for finding in record.get("findings") or []
            if isinstance(finding, Mapping) and bool(finding.get("blocking"))
        ]
        missing_findings = [
            finding_id
            for finding_id in blocking_ids
            if f"{report_sha}:{finding_id}" not in acknowledged
        ]
        if missing_findings:
            raise ImportedSolveRefusal(
                "unacknowledged_findings",
                f"blocking ingestion findings require current report acknowledgements: {missing_findings}",
                details={
                    "report_sha256": report_sha,
                    "missing_finding_ids": missing_findings,
                },
            )

        acoustic_domain = str(
            record.get("acoustic_domain") or record.get("sim_type") or "free-space"
        ).strip().lower()
        if bool(record.get("infinite_baffle")) or acoustic_domain not in {
            "free-space",
            "free_space",
            "freestanding",
        }:
            raise ImportedSolveRefusal(
                "imported_infinite_baffle_unsupported",
                "imported geometry supports free-space solves only; infinite baffle is unavailable",
            )
        record = {
            **record,
            "evidence": await asyncio.to_thread(_refuse_imported_fem, geometry, record),
        }

        symmetry_metadata = _imported_symmetry_metadata(
            record, request.options.symmetry, _ground_plane_axis(request)
        )
        request = widen_polar_to_derivation(request, record.get("polar_grid_derivation"))
        _validate_imported_polar_grid(geometry, request, record)
        polar = request.options.polar_config
        if "diagonal" in polar.enabled_axes and not math.isclose(
            polar.inclination, 45.0
        ):
            raise ImportedSolveRefusal(
                "imported_diagonal_inclination_unsupported",
                "Phase 2 imported solves support diagonal observation only at the "
                "default 45-degree inclination; set the diagonal plane angle to 45",
                details={"inclination": polar.inclination},
            )
        try:
            msh_text = await asyncio.to_thread(read_verified_import_mesh, record)
        except ImportedMeshArtifactError as exc:
            raise ImportedSolveRefusal(
                "ingest_mesh_unavailable",
                str(exc),
            ) from exc
        await asyncio.to_thread(_refuse_inverted_reduced_mesh, record, msh_text)
        effective_polar_grid(request)
        # Validate provenance before any archive identity is claimed as well.
        identity = _cad_identity_provenance(record, geometry)
        return _ImportedValidation(request, dict(record), msh_text, symmetry_metadata, identity)

    async def _prepare_imported_submission(
        self, request: SolveRequest, *, validated: _ImportedValidation | None = None,
    ) -> _ImportedSubmission:
        """Compose persisted provenance only after read-only admission succeeds."""
        validated = validated or await self._validate_imported_submission(request)
        geometry = request.geometry
        assert isinstance(geometry, ImportedGeometrySource)
        record, msh_text = validated.record, validated.msh_text
        symmetry_metadata = validated.symmetry_metadata
        file_mesh = record.get("producer") == "mesh-file"
        mesh_record = record.get("mesh")
        mesh_stats = (
            dict(mesh_record.get("stats") or {})
            if isinstance(mesh_record, Mapping)
            else {}
        )
        anchor = record.get("anchor")
        anchor = anchor if isinstance(anchor, Mapping) else {}
        record_anchor_design_id = (
            str(anchor["design_id"]) if anchor.get("design_id") else None
        )
        anchor_design_id: str | None = None
        anchor_lineage_id: str | None = None
        archive_stem: str | None = None
        anchor_snapshot: dict[str, Any] | None = None
        if record_anchor_design_id is not None:
            design_row = await asyncio.to_thread(
                self.cadlink_store.get_design, record_anchor_design_id
            )
            if design_row is not None:
                anchor_design_id = record_anchor_design_id
                anchor_lineage_id = str(design_row["lineage_id"] or "") or None
                if anchor_lineage_id is not None:
                    # The archive folder is the name the lineage already owns
                    # in ``wglink/``, so a renamed design keeps writing its
                    # history to one folder instead of starting a second one.
                    # The claim reads an already-claimed stem before it writes
                    # one, and the preferred fallback chain claims the
                    # server-normalized portable key even when CAD document
                    # capture is disabled. The browser consumes this value
                    # verbatim, so every imported run and captured model uses
                    # the same globally unique lineage folder.
                    names = await asyncio.to_thread(
                        self.cadlink_store.get_lineage_cad_names, anchor_lineage_id
                    )
                    document = record.get("document")
                    document = document if isinstance(document, Mapping) else {}
                    preferred_archive_stem = (
                        str((names or {}).get("archive_stem") or "").strip()
                        or str((names or {}).get("bundle_stem") or "").strip()
                        or str(document.get("name") or "").strip()
                        or Path(str(design_row.get("filename") or "")).stem
                    )
                    archive_stem = await asyncio.to_thread(
                        self.cadlink_store.claim_archive_stem,
                        anchor_lineage_id,
                        preferred=preferred_archive_stem,
                    )
                try:
                    parsed = await asyncio.to_thread(
                        parse, str(design_row["snapshot_text"])
                    )
                    anchor_snapshot = {
                        "version": 1,
                        "design": parsed.design.model_dump(mode="json"),
                    }
                except Exception:
                    logger.warning(
                        "Could not parse anchor design snapshot %s for imported job",
                        record_anchor_design_id,
                    )
        else:
            anchor_lineage_id, archive_stem = await _cad_authored_project(
                self.cadlink_store, record
            )
        if file_mesh:
            archive_stem = str(record["archive_stem"])
        identity = validated.identity
        return _ImportedSubmission(
            record=dict(record),
            msh_text=msh_text,
            mesh_stats=mesh_stats,
            symmetry_metadata=symmetry_metadata,
            anchor_design_id=anchor_design_id,
            anchor_lineage_id=anchor_lineage_id,
            archive_stem=archive_stem,
            document=dict(record.get("document") or {}),
            identity=identity,
            anchor_snapshot=anchor_snapshot,
        )

    # -- a CAD solve as a job (docs/architecture/CAD-OPERATIONS.md, "A CAD solve is a job") --
    #
    # WG accepts a CAD solve by creating a job in status ``preparing`` that holds
    # a ``CadSolveIntent``; the preparation lane retains, meshes and checks it, and
    # binding turns it into an ordinary queued job in one transaction. A solve
    # that cannot go on ends as an ``error`` job carrying its refusal, and the
    # user's remedy is Solve again, which is a new job. Delivery, startup recovery
    # and the compatibility routes all enter through this runtime.

    def configure_cad_preparation(self, host: CadPreparationHost | None) -> None:
        """Say where retained returns live and how a return is meshed.

        Without it no CAD solve can be accepted. A ``preparing`` job found at
        start-up waits for the host: nothing prepares it before then.
        """

        self._cad_host = host
        self._ensure_prep_lane()

    def _require_cad_host(self) -> CadPreparationHost:
        if self._cad_host is None or self.cadlink_store is None:
            raise JobConflictError("CAD solves cannot be prepared: no CAD preparation is configured")
        return self._cad_host

    def _preparing_record(self, intent: CadSolveIntent) -> dict[str, Any]:
        now = _now_iso()
        return {
            "id": str(uuid.uuid4()),
            "parent_job_id": intent.parent_job_id,
            "status": "preparing",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "progress": 0.0,
            "stage": "received",
            "stage_message": "Waiting to prepare this solve",
            "error_message": None,
            "cancellation_requested": False,
            "config_json": intent.to_config(),
            "config_summary_json": {"formula_type": "cad-import"},
            "has_results": False,
            "has_mesh_artifact": False,
            "mesh_stats": None,
            "label": intent.label,
            "script_snapshot": None,
            "task_metadata": {"cad": {"operation_id": intent.operation_id}},
        }

    async def accept_cad_solve(
        self, intent: CadSolveIntent, submission_key: str, *,
        prepare: bool = True, cad_record: Mapping[str, Any] | None = None,
        refusal: Mapping[str, str | None] | None = None, cancelled: bool = False,
    ) -> str:
        """Accept a CAD solve: a ``preparing`` job, created once for its submission key.

        Idempotent: the key names the delivery (``cad-solve:<operationId>``) and
        the delivery's digest is the request hash, so accepting the same command
        again returns the job it made, and the same key for another return is a
        ``SubmissionConflictError``. Durable before it returns; the preparation
        lane takes the job up by itself, and again after a restart if WG stopped
        first.
        """

        self._require_cad_host()
        await self.start()
        intent = replace(intent, submission_key=submission_key)
        digest = intent.delivery_digest()
        existing = await asyncio.to_thread(
            self.store.resolve_submission, submission_key, digest
        )
        if existing is not None:
            return existing
        record = self._preparing_record(intent)
        record["task_metadata"]["cad"].update(dict(cad_record or {}))
        if refusal:
            record.update(status="error", stage="error", stage_message=refusal["message"] or "Preparation stopped",
                          error_message=refusal["message"], completed_at=_now_iso())
            record["task_metadata"]["cad"]["refusal"] = dict(refusal)
            if is_calm_failure(refusal["code"], refusal["message"]):
                # Internal detail goes to the developers; the job keeps calm words.
                from server.jobs.cad_preparation import calm_refusal_message

                calm = calm_refusal_message(
                    str(refusal["code"]), str(refusal["message"] or ""), record["task_metadata"]["cad"],
                    job_id=None, operation_id=intent.operation_id,
                )
                record.update(stage_message=calm, error_message=calm)
                record["task_metadata"]["cad"]["refusal"] = {**dict(refusal), "message": calm}
            elif CAD_REASON_STATES.get(refusal["code"]) == NEEDS_USER_INPUT:
                record.update(status="held", stage="held", stage_message="Waiting for you", error_message=None)
                record["task_metadata"]["cad"]["hold"] = hold_details(str(refusal["code"]), str(refusal["message"] or ""))
        if cancelled:
            record.update(status="cancelled", stage="cancelled", stage_message="Solve cancelled",
                          cancellation_requested=True, completed_at=_now_iso())
        if cancelled:
            initial_event = ("cancelled", {"message": record["stage_message"]})
        elif refusal:
            initial_event = ("stage" if record["status"] == "held" else "failed",
                             {"stage": record["stage"], "code": refusal["code"], "message": record["stage_message"]})
        else:
            initial_event = ("stage", {"stage": "received", "message": record["stage_message"], "progress": 0.0})
        job_id, created, event = await asyncio.to_thread(
            self.store.create_job_idempotent,
            record,
            submission_key=submission_key,
            request_sha256=digest,
            initial_event=initial_event,
        )
        if created:
            for retired in await asyncio.to_thread(self.store.retire_held_cad_requests, job_id):
                self.events.publish(retired)
            if prepare and not refusal and not cancelled:
                self._offer_to_prep_lane(job_id, event)
            elif event is not None:
                self.events.publish(event)
        return job_id

    async def dismiss_cad_solve(self, operation_id: str) -> None:
        """Delete a refused intent and its refused ancestors as one dismissal."""

        await self.start()
        try:
            ids, events = await asyncio.to_thread(self.store.dismiss_cad_intents, operation_id)
        except ValueError as exc:
            raise JobConflictError(str(exc)) from exc
        for job_id in ids:
            self._prep_queue = deque(item for item in self._prep_queue if item != job_id)
        for event in events:
            self.events.publish(event)

    async def prepare_cad_solve(self, job_id: str, **press: Any) -> str:
        """Compatibility Prepare: capture the first manual press, or Solve again."""

        row = self._require_job(job_id)
        intent = intent_of(row)
        if intent is not None and row["status"] == "preparing":
            if row.get("started_at") is None and cad_of(row).get("manual_waiting"):
                updated = replace(
                    intent, setup_revision_id=press.get("setup_revision_id"),
                    frame_axis=press.get("frame_axis") or intent.frame_axis,
                    submit=press.get("submit", True),
                    label=press.get("label", intent.label),
                    approvals=(
                        {"preparation_id": press["approve_preparation_id"],
                         "finding_ids": list(press.get("approve_finding_ids") or ())}
                        if press.get("approve_preparation_id") else None
                    ),
                )
                press_sha256 = self._cad_solve_press_sha256(job_id, **{
                    key: press[key] for key in (
                        "setup_revision_id", "frame_axis", "approve_preparation_id",
                        "approve_finding_ids", "submit",
                    ) if key in press
                })
                if await asyncio.to_thread(
                    self.store.admit_cad_press, job_id, updated.to_config(), press_sha256
                ):
                    self.schedule_cad_solve(job_id)
            else:
                self.schedule_cad_solve(job_id)
            return job_id
        if intent is not None:
            return await self.solve_cad_again(job_id, **press)
        return job_id

    @staticmethod
    def _cad_solve_press_sha256(
        job_id: str, *, setup_revision_id: str | None = None,
        frame_axis: str | None = None, approve_preparation_id: str | None = None,
        approve_finding_ids: tuple[str, ...] | list[str] = (), submit: bool = True,
        mesh_limit_override: bool = False,
    ) -> str:
        return hashlib.sha256(json.dumps({
            "parent_job_id": job_id,
            "setup_revision_id": setup_revision_id,
            "frame_axis": frame_axis,
            "approve_preparation_id": approve_preparation_id,
            "approve_finding_ids": sorted(approve_finding_ids),
            "submit": submit,
            **({"mesh_limit_override": True} if mesh_limit_override else {}),
        }, sort_keys=True).encode()).hexdigest()

    def recover_cad_solve_press(self, job_id: str, **press: Any) -> str | None:
        """Resolve an exact continuation replay before any admission checks."""

        row = self._require_job(job_id)
        digest = self._cad_solve_press_sha256(job_id, **press)
        # The first press admitted on this very job (a waiting manual solve):
        # a replay is that job once it has bound its request, however its run
        # ended, and while it still prepares. Only a preparation refused or
        # stopped before binding takes the same settings as a new Solve again.
        if cad_of(row).get("first_press_sha256") == digest and (
            intent_of(row) is None or row["status"] not in {"held", "error", "cancelled"}
        ):
            return job_id
        seen = {job_id}
        child_id = self.store.job_for_submission_key(f"cad-solve-again:{job_id}")
        while child_id is not None and child_id not in seen:
            seen.add(child_id)
            child = self._require_job(child_id)
            if cad_of(child).get("solve_again_press_sha256") == digest:
                return child_id
            child_id = self.store.job_for_submission_key(f"cad-solve-again:{child_id}")
        return None

    async def solve_cad_again(
        self,
        job_id: str,
        *,
        setup_revision_id: str | None = None,
        frame_axis: str | None = None,
        approve_preparation_id: str | None = None,
        approve_finding_ids: tuple[str, ...] | list[str] = (),
        submit: bool = True,
        request_id: str | None = None,
        label: str | None = None,
        mesh_limit_override: bool = False,
    ) -> str:
        """Solve again a CAD solve that ended before it had a request: a new job.

        ``request_id`` is the client id of the model Solve that continues it
        (``server/jobs/model_solve.py``), kept on the new job so a replay of
        that press finds it; ``label`` is that press's run name.

        For a job WG refused (setup required, findings to review, the frame, a
        failed preparation) or that was stopped. The new job continues it: same
        return, the setup the first recorded unless a new one is named, the
        frame axis it was held to, its approvals, and its preparation, which it
        resumes when the same snapshot, setup, frame and domain make the same one,
        so nothing is meshed again and approvals bound to it still hold. A
        solve WG rejected as invalid is not retried: it is sent again from CAD.
        """

        await self.start()
        row = self._require_job(job_id)
        # The press is distinct from the parent we eventually continue: a changed
        # press on an ancestor follows refused descendants, while the same press
        # returns its child even if that preparation has already been refused.
        press_sha256 = self._cad_solve_press_sha256(
            job_id, setup_revision_id=setup_revision_id, frame_axis=frame_axis,
            approve_preparation_id=approve_preparation_id,
            approve_finding_ids=approve_finding_ids, submit=submit,
            mesh_limit_override=mesh_limit_override,
        )
        replay = await asyncio.to_thread(
            self.recover_cad_solve_press, job_id, setup_revision_id=setup_revision_id,
            frame_axis=frame_axis, approve_preparation_id=approve_preparation_id,
            approve_finding_ids=approve_finding_ids, submit=submit,
            mesh_limit_override=mesh_limit_override,
        )
        if replay is not None:
            return replay
        existing_child_message = "This solve already has a continuing job; use that job instead"
        while True:
            if intent_of(row) is None:
                raise JobConflictError("This solve already has a solve request; retry it instead")
            if row["status"] in ACTIVE_STATUSES:
                raise JobConflictError("This solve is still being prepared and has no solve request yet")
            self._require_cad_host()
            cad = (row.get("task_metadata") or {}).get("cad") or {}
            refusal = cad.get("refusal") if isinstance(cad.get("refusal"), Mapping) else None
            if refusal is not None and CAD_REASON_STATES.get(str(refusal.get("code"))) == CAD_REJECTED:
                raise JobConflictError(str(refusal.get("message") or "WG rejected this return"))
            continuation_key = f"cad-solve-again:{row['id']}"
            existing = await asyncio.to_thread(self.store.job_for_submission_key, continuation_key)
            if existing is None:
                if refusal is not None and refusal.get("code") == "superseded":
                    raise JobConflictError("This request was replaced by a newer request from Fusion.")
                if cad.get("solve_again_press_sha256") == press_sha256:
                    return str(row["id"])
                intent = solve_again_intent(
                    row,
                    setup_revision_id=setup_revision_id,
                    frame_axis=frame_axis,
                    approve_preparation_id=approve_preparation_id,
                    approve_finding_ids=approve_finding_ids,
                    submit=submit,
                    label=label,
                    mesh_limit_override=mesh_limit_override,
                )
                intent = replace(intent, submission_key=continuation_key)
                record = self._preparing_record(intent)
                # Carry the newest refused job's record, exactly as a press on it
                # would: retained snapshot, setup, preparation and its approvals.
                record["task_metadata"]["cad"].update(carried_record(row))
                carried_frame = record["task_metadata"]["cad"].get("frame")
                if frame_axis and isinstance(carried_frame, Mapping) and carried_frame.get("axis") != frame_axis:
                    # The press names another axis: the parent's frame record no
                    # longer describes this solve. Binding records the new one.
                    record["task_metadata"]["cad"].pop("frame")
                record["task_metadata"]["cad"]["solve_again_press_sha256"] = press_sha256
                if request_id:
                    record["task_metadata"]["cad"]["model_solve_request_id"] = request_id
                existing, created, event = await asyncio.to_thread(
                    self.store.create_job_idempotent,
                    record,
                    submission_key=continuation_key,
                    request_sha256=hashlib.sha256(str(row["id"]).encode()).hexdigest(),
                    initial_event=(
                        "stage",
                        {"stage": "received", "message": record["stage_message"], "progress": 0.0},
                    ),
                )
                if created:
                    for retired in await asyncio.to_thread(self.store.retire_held_cad_requests, existing):
                        self.events.publish(retired)
                    self._offer_to_prep_lane(existing, event)
                    return existing
            # Check the durable intent both after lookup and after a competing
            # create. An error from a bound solver is not a refused preparation.
            child = self._require_job(existing)
            if cad_of(child).get("solve_again_press_sha256") == press_sha256:
                return existing
            if intent_of(child) is None or child["status"] not in {"preparing", "held", "error"}:
                raise JobConflictError(existing_child_message)
            if child["status"] == "preparing":
                raise JobConflictError(existing_child_message)
            row = child

    def schedule_cad_solve(self, job_id: str) -> None:
        """Offer a durable intent after its delivery has finished collection."""

        row = self._require_job(job_id)
        if (row["status"] == "preparing" and not cad_of(row).get("manual_waiting")
                and job_id not in self._prep_queue and job_id not in self._prep_running):
            self._offer_to_prep_lane(job_id, None)

    def _offer_to_prep_lane(self, job_id: str, event: Mapping[str, Any] | None) -> None:
        if event is not None:
            self.events.publish(event)
        self._prep_queue.append(job_id)
        self._ensure_prep_lane()

    def _ensure_prep_lane(self) -> None:
        """Start preparing accepted CAD solves, a few at a time, while no restart is approved.

        While an update restart is approved (contract §4.2) nothing is prepared and
        nothing is written: the jobs stay as they are and are taken up when the latch
        comes down (``_wake``), or by the next process.
        """

        if (
            self._shutting_down
            or self._stopping
            or not self._started
            or self._cad_host is None
            or not self._prep_queue
        ):
            return
        if self._restart_pending():
            self._say_prep_waits_for_restart()
            self._check_again_when_the_approval_expires()
            return
        while self._prep_queue and len(self._prep_running) < CAD_PREPARATION_LANE_WIDTH:
            job_id = self._prep_queue.popleft()
            if job_id in self._prep_running:
                continue
            self._prep_running.add(job_id)
            self._keep_task(
                asyncio.create_task(
                    self._run_cad_preparation(job_id), name=f"wg2-cad-prepare-{job_id}"
                )
            )

    def _say_prep_waits_for_restart(self) -> None:
        """Mark the solves waiting for the lane as waiting for the update restart.

        A stage and its words, nothing more: they are not claimed, so a restart
        that is called off finds them exactly as they were.
        """

        message = self._cad_port.restart_refusal()
        for job_id in tuple(self._prep_queue):
            try:
                event = self.store.mark_unheld_preparing_job(
                    job_id, stage=STAGE_WAITING_FOR_RESTART, stage_message=message or ""
                )
            except Exception:
                logger.exception("Could not mark CAD job %s as waiting for the restart", job_id)
                continue
            if event is not None:
                self.events.publish(event)

    async def _run_cad_preparation(self, job_id: str) -> None:
        host = self._cad_host
        try:
            if host is None:
                return
            # Imported here: it reaches the CAD store's modules, which import
            # this package, so the runtime cannot import it while it loads.
            from server.jobs.cad_preparation import run_cad_preparation

            outcome = await run_cad_preparation(self._cad_port, host, job_id)
            if outcome == "waiting":
                # Handed back for an approved restart: it is offered again the
                # moment the latch is down.
                self._prep_queue.appendleft(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The CAD preparation lane failed on job %s", job_id)
        finally:
            self._prep_running.discard(job_id)
            self._ensure_prep_lane()

    async def wait_cad_preparations(self, timeout: float = 30.0) -> None:
        """Wait until the preparation lane has nothing queued or running (tests wait on it).

        A lane held by an approved update restart is settled too: what it holds waits
        for the latch, not for this call.
        """

        async def settled() -> None:
            while self._prep_running or (self._prep_queue and not self._restart_pending()):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(settled(), timeout=timeout)

    async def _bind_cad_job(
        self, job_id: str, request: SolveRequest, cad_provenance: Mapping[str, Any]
    ) -> str:
        """Bind a prepared request to its job and queue it: one transaction.

        Every submission check runs first, exactly as ``submit`` runs them
        (``_compose_job``), so a request the jobs system would refuse ends the
        job as refused. Then ``JobStore.bind_preparing_job`` writes the request,
        the job's metadata and mesh artifact and its run number, and moves
        ``preparing`` to ``queued``, all or nothing. Returns ``BOUND``;
        ``BIND_STOPPED`` when the job was stopped meanwhile (nothing written);
        ``BIND_BLOCKED`` when an update restart was approved first (nothing
        written: the bind is ordered with the approval as a job start is).
        """

        _refuse_removed_solver(request)
        composed = await self._compose_job(request, cad_provenance)
        assert composed.imported is not None and composed.mesh_artifact is not None

        def bind() -> dict[str, Any] | None:
            from server.workspace.run_moves import submission_location

            with submission_location(self.cadlink_store, composed.request.model_dump(mode="json"), composed.task_metadata):
                return self.store.bind_preparing_job(
                    job_id,
                    config=composed.request.model_dump(mode="json"),
                    config_summary=composed.config_summary,
                    task_metadata=composed.task_metadata,
                    mesh_artifact=composed.mesh_artifact,
                    mesh_stats=composed.mesh_stats,
                    script_snapshot=composed.script_snapshot,
                    label=composed.request.label,
                    parent_job_id=composed.request.parent_job_id,
                    initial_event=("queued", {"status": "queued", "progress": 0.0}),
                )

        approval = self.restart_approval
        if approval is None:
            admitted, event = True, await asyncio.to_thread(bind)
        else:
            admitted, event = await asyncio.to_thread(approval.admit, bind)
        if not admitted:
            return BIND_BLOCKED
        if event is None:
            return BIND_STOPPED
        self._queue.append(job_id)
        self.events.publish(event)
        self._ensure_scheduler()
        return BOUND

    async def stop(self, job_id: str) -> dict[str, str]:
        await self.start()
        row = self._require_job(job_id)
        status = row["status"]
        if status not in ACTIVE_STATUSES:
            raise JobConflictError(f"Cannot stop job with status: {status}")
        if status in {"preparing", "queued"}:
            await self._flush_runtime_update(job_id, forget=True)
            self._remove_from_queue(job_id)
            self._prep_queue = deque(item for item in self._prep_queue if item != job_id)
            # Against the status just read: a preparing job races the binding
            # that queues it, and a queued one races its start. One wins. A
            # stop that lost reads the job again and answers for what it is now.
            event = await asyncio.to_thread(
                self.store.cancel_job_from,
                job_id,
                status,
                {
                    "status": "cancelled",
                    "progress": 0.0,
                    "stage": "cancelled",
                    "stage_message": "Simulation cancelled",
                    "error_message": CANCELLED_MESSAGE,
                    "completed_at": _now_iso(),
                    "cancellation_requested": False,
                },
                "cancelled",
                {"message": CANCELLED_MESSAGE},
            )
            if event is None:
                return await self.stop(job_id)
            self.events.publish(event)
            return {"message": f"Job {job_id} has been cancelled", "status": "cancelled"}

        # Do not let a delayed progress checkpoint overwrite the visible
        # cancelling stage after this method returns.
        await self._flush_runtime_update(job_id, forget=True)
        event = self.store.request_cancellation(
            job_id,
            {
                "stage": "cancelling",
                "stage_message": "Cancellation requested; waiting for current stage checkpoint",
                "cancellation_requested": True,
            },
            {"stage": "cancelling", "message": "Cancellation requested"},
        )
        # The native checkpoint may act immediately: persist intent before
        # signalling it, with no intervening event-loop yield.
        flag = self._cancellation_flags.get(job_id)
        if flag is not None:
            flag.set()
        if event is None:
            current = self._require_job(job_id)
            if current.get("status") != "running":
                raise JobConflictError(f"Cannot stop job with status: {current['status']}")
            return {
                "message": f"Cancellation already requested for job {job_id}",
                "status": "cancelling",
            }
        self.events.publish(event)
        return {
            "message": f"Cancellation requested for job {job_id}",
            "status": "cancelling",
        }

    async def retry(self, job_id: str) -> str:
        """Submit a faithful replay from the target's persisted request config."""

        await self.start()
        row = self._require_job(job_id)
        if intent_of(row) is not None:
            # Never bound to a request: there is nothing to replay. A new job
            # continues it (``solve_cad_again``).
            return await self.solve_cad_again(job_id)
        request = _replay_request(row)
        _refuse_removed_solver(request)
        if isinstance(request.geometry, ImportedGeometrySource) and any(
            channel.motion == "axial" for channel in request.geometry.drive_channels
        ):
            results_text = await asyncio.to_thread(self.store.get_results_text, job_id)
            axial_ids = [
                channel.id for channel in request.geometry.drive_channels
                if channel.motion == "axial" and channel.exterior_transducer is None
            ]
            if axial_ids and _solved_under_legacy_axial(results_text, axial_ids):
                # A retry promises the same solve again. Axial drive now runs
                # per source axis, so replaying this request would silently
                # answer a different question than the run it repeats.
                raise ImportedSolveRefusal(
                    "imported_axial_legacy_retry",
                    "This run's axial sources were solved under the earlier "
                    "frame-axis rule. Start a new solve to use per-source axes.",
                )
        if isinstance(request.geometry, ImportedGeometrySource) and self.cadlink_store is not None:
            # A retry solves the parent's record again: only under the domain
            # decision the parent was submitted with.
            record = await asyncio.to_thread(
                get_ingestion_record, self.cadlink_store, request.geometry.ingest_id
            )
            metadata = row.get("task_metadata")
            refusal = _imported_job_decision_refusal(
                metadata if isinstance(metadata, Mapping) else None, record
            )
            if refusal is not None:
                raise ImportedSolveRefusal(*refusal)
        request.parent_job_id = job_id
        # Retry is explicitly a new run, not a transport replay of the source
        # submission. Reusing its key would either recover the source job or
        # conflict because the parent id is now different.
        request.client_request_id = None
        provenance = None
        if isinstance(request.geometry, ImportedGeometrySource) and cad_of(row):
            provenance = {
                key: value for key, value in cad_of(row).items()
                if key not in {
                    "refusal", "solve_again_press_sha256", "last_stage", "manual_waiting",
                    # A model Solve's client id names that press's job only.
                    "model_solve_request_id",
                }
            }
            provenance["retried_from"] = job_id
        if isinstance(request.geometry, ImportedGeometrySource):
            # An imported run is solved again as its model is solved, with the
            # setup it recorded (server/jobs/model_solve.py).
            from server.jobs.model_solve import recorded_setup_revision, submit_imported_request

            return await submit_imported_request(
                self, request, cad_provenance=provenance,
                setup_revision_id=recorded_setup_revision(row),
            )
        return await self.submit(request)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        await self.start()

        def load() -> dict[str, Any]:
            return self._serialize_job(
                self._require_job(job_id),
                detailed=True,
                cadlink_store=self.cadlink_store,
            )

        return await asyncio.to_thread(load)

    async def get_effective_request(self, job_id: str) -> SolveRequest:
        """Return the normalized request durably stored for one job."""

        await self.start()

        def load() -> SolveRequest:
            return _replay_request(self._require_job(job_id))

        return await asyncio.to_thread(load)

    async def get_execution_request(self, job_id: str) -> SolveRequest:
        """Return the request shape passed to the solver for one job."""

        await self.start()

        def load() -> SolveRequest:
            row = self._require_job(job_id)
            metadata = (
                dict(row.get("task_metadata") or {})
                if isinstance(row.get("task_metadata"), Mapping)
                else {}
            )
            symmetry = dict(metadata.get("symmetry") or {})
            resolved_quadrants = int(symmetry.get("resolved_quadrants", 1234))
            return _execution_request(
                _replay_request(row),
                resolved_quadrants=resolved_quadrants,
            )

        return await asyncio.to_thread(load)

    async def list_jobs(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        await self.start()
        statuses = self.parse_status_filter(status)

        def load() -> tuple[list[dict[str, Any]], int]:
            rows, total = self.store.list_jobs(
                statuses=statuses, limit=limit, offset=offset
            )
            return [
                self._serialize_job(row, cadlink_store=self.cadlink_store)
                for row in rows
            ], total

        return await asyncio.to_thread(load)

    async def unreleased_cad_return_states(self) -> list[dict[str, str]]:
        """The CAD model states runs still owe an archive copy of.

        Read by the CAD document capture so that ingesting a changed model
        cannot delete the only copy a queued or unarchived run can still be
        archived from.
        """

        return await asyncio.to_thread(self.store.unreleased_cad_return_states)

    async def get_results(self, job_id: str) -> dict[str, Any]:
        return json.loads(await self.get_results_text(job_id))

    async def get_results_text(self, job_id: str) -> str:
        """The stored results JSON, read off the event loop and not re-parsed."""

        content, _digest = await self.get_results_payload(job_id)
        return content

    async def get_results_payload(self, job_id: str) -> tuple[str, str]:
        """Return exact result text and its persisted SHA-256 off the event loop."""

        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        payload = await asyncio.to_thread(self.store.get_results_payload, job_id)
        if payload is None:
            raise JobResourceUnavailableError("Results not available")
        return payload

    async def get_archive_snapshot(self, job_id: str) -> dict[str, Any]:
        """Copy every retained archive input before retention may resume.

        The store read is deliberately one operation.  Expensive presentation
        and public pressure-basis conversion happens afterwards from copied
        bytes, so retention is neither blocked by NumPy work nor able to make a
        multi-member archive observe a mixture of pre- and post-prune state.
        """

        from server.jobs.radiation_impedance import (
            radiation_impedance_presentation,
        )
        from server.solver.combine import deserialize_channel_bases
        from server.solver.pressure_basis import export_pressure_basis

        await self.start()
        snapshot = await asyncio.to_thread(self.store.get_archive_snapshot, job_id)
        if snapshot is None:
            raise JobNotFoundError(job_id)
        job = snapshot["job"]
        if job["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {job['status']}")
        results_text = snapshot["results_text"]
        if results_text is None:
            raise JobResourceUnavailableError("Results not available")
        try:
            results = json.loads(results_text)
        except json.JSONDecodeError as exc:
            raise JobResourceUnavailableError(
                "Stored results are not readable JSON"
            ) from exc

        pressure_bases: list[dict[str, str]] = []
        bases_npz = snapshot["channel_bases"]
        if bases_npz is not None:
            try:
                channel_ids = list(deserialize_channel_bases(bases_npz)["channel_ids"])
                motions = _request_motions(job)
                for channel_id in channel_ids:
                    artifact = export_pressure_basis(
                        bases_npz, results, str(channel_id), motions
                    )
                    pressure_bases.append(
                        {
                            "channel_id": artifact.channel_id,
                            "content_base64": base64.b64encode(
                                artifact.content
                            ).decode("ascii"),
                        }
                    )
            except (KeyError, TypeError, ValueError) as exc:
                raise JobResourceUnavailableError(
                    f"Pressure-basis artifact could not be read: {exc}"
                ) from exc

        radiation: dict[str, Any] | None = None
        radiation_npz = snapshot["radiation_impedance"]
        if radiation_npz is not None:
            try:
                presentation = radiation_impedance_presentation(radiation_npz)
            except ValueError as exc:
                raise JobResourceUnavailableError(
                    f"Radiation-impedance artifact could not be read: {exc}"
                ) from exc
            radiation = {
                "content_base64": base64.b64encode(radiation_npz).decode("ascii"),
                "presentation": presentation,
            }

        return {
            "schema_version": 1,
            "results": results,
            "results_sha256": snapshot["results_sha256"],
            "mesh_artifact": snapshot["mesh_text"],
            "pressure_bases": pressure_bases,
            "radiation_impedance": radiation,
        }

    async def get_radiation_impedance(self, job_id: str) -> bytes:
        """Return the lossless passive-cardioid radiation matrix artifact."""

        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        artifact = await asyncio.to_thread(
            self.store.get_radiation_impedance, job_id
        )
        if artifact is None:
            raise JobResourceUnavailableError(
                "This job has no passive-cardioid radiation-impedance artifact"
            )
        return artifact

    async def get_pressure_basis(
        self, job_id: str, channel_id: str | None = None
    ) -> Any:
        """Build one public engineering-convention basis from retained fields."""

        from server.solver.pressure_basis import export_pressure_basis

        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        bases = await asyncio.to_thread(self.store.get_channel_bases, job_id)
        if bases is None:
            raise JobResourceUnavailableError(
                "This job has no retained pressure basis; re-solve an imported "
                "Metal run to create one"
            )
        results_text = await asyncio.to_thread(self.store.get_results_text, job_id)
        if results_text is None:
            raise JobResourceUnavailableError("Results not available")
        motions = _request_motions(row)
        try:
            return await asyncio.to_thread(
                export_pressure_basis, bases, json.loads(results_text), channel_id, motions
            )
        except ValueError as exc:
            if "cannot be established" not in str(exc):
                raise
            raise JobResourceUnavailableError(str(exc)) from exc

    async def get_radiation_impedance_presentation(
        self, job_id: str
    ) -> dict[str, Any]:
        """Read the stored matrix into its engineering presentation contract."""

        from server.jobs.radiation_impedance import (
            radiation_impedance_presentation,
        )

        artifact = await self.get_radiation_impedance(job_id)
        try:
            return await asyncio.to_thread(
                radiation_impedance_presentation, artifact
            )
        except ValueError as exc:
            raise JobResourceUnavailableError(
                f"Radiation-impedance artifact could not be read: {exc}"
            ) from exc

    @asynccontextmanager
    async def _serialized_result_mutation(self, job_id: str) -> AsyncIterator[None]:
        """Hold one job's results for a whole read-modify-write cycle.

        Rewriting a job's stored results in place is read, rebuild, write.
        Two overlapping requests for one job would both rebuild from the
        pre-edit results and the one the database keeps would be whichever
        finished last -- so a crossover edit that is changed again before its
        reply returns could leave the abandoned spec persisted, and the client
        no way to tell. Waiters are served in arrival order, which is also the
        order the replies describe.
        """

        turnstile = self._result_mutation.get(job_id)
        if turnstile is None:
            turnstile = _JobMutationTurnstile()
            self._result_mutation[job_id] = turnstile
        turnstile.users += 1
        try:
            async with turnstile.lock:
                yield
        finally:
            turnstile.users -= 1
            if turnstile.users <= 0 and self._result_mutation.get(job_id) is turnstile:
                del self._result_mutation[job_id]

    async def recombine_results(
        self, job_id: str, spec: ChannelCombineSpec
    ) -> dict[str, Any]:
        """Recompute the combined channel from stored bases and persist it."""

        from server.solver.recombine import recombine_stored_results

        await self.start()
        async with self._serialized_result_mutation(job_id):
            row = self._require_job(job_id)
            if row["status"] != "complete":
                raise JobConflictError(
                    f"Job not complete. Current status: {row['status']}"
                )
            results_text = await asyncio.to_thread(
                self.store.get_results_text, job_id
            )
            if results_text is None:
                raise JobResourceUnavailableError("Results not available")
            bases = await asyncio.to_thread(self.store.get_channel_bases, job_id)
            if bases is None:
                raise JobResourceUnavailableError(
                    "This job has no stored channel bases; re-solve to enable "
                    "crossover changes without a new solve"
                )
            request = _replay_request(row)
            results = json.loads(results_text)
            updated = await asyncio.to_thread(
                recombine_stored_results, results, bases, spec, request
            )
            await asyncio.to_thread(self.store.store_results, job_id, updated)
            return updated

    async def evaluate_field_plane(
        self, job_id: str, request: FieldPlaneRequest
    ) -> FieldPlaneEvaluation:
        """Evaluate one retained exterior field without mutating job results."""

        await self.start()
        return await self.field_plane_service.evaluate(job_id, request)

    async def get_partial_results(self, job_id: str) -> dict[str, Any]:
        """Return the current process-local provisional result snapshot."""

        await self.start()
        self._require_job(job_id)
        item = self._partial_results.get(job_id)
        if item is None:
            raise JobResourceUnavailableError("Partial results not available")
        return copy.deepcopy(item)

    def partial_result_messages(self) -> list[dict[str, Any]]:
        """Full live snapshots sent once when a jobs socket connects."""

        return [
            {
                "v": 1,
                "kind": "partialResult",
                "jobId": job_id,
                "revision": int(item["revision"]),
                "snapshot": True,
                "result": copy.deepcopy(item["result"]),
            }
            for job_id, item in self._partial_results.items()
        ]

    async def get_mesh_artifact_download(self, job_id: str) -> MeshArtifactDownload:
        await self.start()
        row = self._require_job(job_id)
        # Real MSH artifacts are multi-megabyte text blobs. Reading one through
        # SQLite on the event loop stalls both WS channels for the full copy.
        artifact = await asyncio.to_thread(self.store.get_mesh_artifact, job_id)
        if artifact:
            return MeshArtifactDownload(content=artifact)

        metadata = row.get("task_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if not metadata.get("mesh_discarded_at"):
            raise JobResourceUnavailableError("No mesh artifact is available for this job")

        config = row.get("config_json")
        config = config if isinstance(config, Mapping) else {}
        geometry = config.get("geometry")
        if isinstance(geometry, Mapping) and geometry.get("type") == "imported":
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "this job was solved from imported geometry, so its stored parametric "
                "anchor is not the solve mesh."
            )

        resolution = resolve_job_design(row.get("script_snapshot"))
        if not resolution.reopenable or resolution.snapshot is None:
            reason = resolution.reason or "the job has no stored design"
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                f"{reason}"
            )

        snapshot = resolution.snapshot
        raw_design = snapshot.get("design", snapshot)
        try:
            design = DesignConfig.model_validate(raw_design)
        except Exception as exc:
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "the stored design no longer resolves in the current server "
                f"({exc})."
            ) from exc

        # The snapshot carries the *declared* quadrants, but the solve ran on the
        # domain symmetry resolution picked for it, which ``_run_job`` replays
        # from this same field. Without replaying it here too, a job solved on a
        # quarter domain regenerates as a full-domain mesh: a different artifact,
        # not merely a differently-built one.
        if (
            metadata.get("enclosure_resolution_contract") != _ENCLOSURE_RESOLUTION_CONTRACT
            and _legacy_enclosure_resolution_changed(design)
        ):
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "this job predates per-quadrant enclosure resolutions and used only "
                "the first front/back value. Reopen the design and run a new solve "
                "to use all quadrant values."
            )
        resolved_quadrants = _recorded_resolved_quadrants(metadata)
        if resolved_quadrants is not None:
            design.root.mesh.quadrants = Expr(value=float(resolved_quadrants))

        try:
            from server.mesh.builder import build_solver_mesh

            # Regeneration reproduces the solve *domain* exactly (above), but
            # deliberately uses the server's currently pinned mesher, which may
            # differ from the version that produced the solve -- hence the
            # ``regenerated``/mesher-version flags on the response. Force a fresh
            # build for every download and never put it back in the job store:
            # retention would only discard it again.
            regenerated = await build_solver_mesh(
                design,
                _stored_solve_options(config),
                force_rebuild=True,
            )
            content = regenerated.get("msh_text")
            if not isinstance(content, str) or not content:
                raise RuntimeError("the current mesher returned no MSH payload")
        except Exception as exc:
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "the stored design no longer resolves with the current mesher "
                f"({exc})."
            ) from exc

        try:
            mesher_version = importlib.metadata.version("hornlab-waveguide-mesher")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - build failed first
            mesher_version = None
        return MeshArtifactDownload(
            content=content,
            regenerated=True,
            mesher_version=mesher_version,
        )

    async def get_mesh_artifact(self, job_id: str) -> str:
        return (await self.get_mesh_artifact_download(job_id)).content

    async def get_log(self, job_id: str) -> str:
        await self.start()
        self._require_job(job_id)
        await self._flush_runtime_update(job_id)
        return await asyncio.to_thread(self.store.get_job_log, job_id)

    async def patch_metadata(self, job_id: str, fields: Mapping[str, Any]) -> None:
        await self.start()
        row = self._require_job(job_id)
        changed = dict(fields)
        if changed.get("archived_at"):
            await self._protect_cad_document(row)
            source = self._serialize_job(row, cadlink_store=self.cadlink_store).get('cad_source')
            path = await asyncio.to_thread(archive_identity.complete_archive, self.cadlink_store, row, source)
            if path is not None:
                changed["archived_path"] = path
        column_fields: dict[str, Any] = {}
        if "label" in changed:
            column_fields["label"] = changed.pop("label")
        if "script_snapshot" in changed:
            value = changed.pop("script_snapshot")
            column_fields["script_snapshot_json"] = (
                json.dumps(value) if value is not None else None
            )
        if not changed and not column_fields:
            return
        changed_ok, event = self.store.mutate_job_metadata(
            job_id,
            changed,
            column_fields=column_fields,
            event_type="metadata",
            payload={"changed": dict(fields)},
        )
        if not changed_ok or event is None:
            raise JobNotFoundError(job_id)
        self.events.publish(event)

    async def _protect_cad_document(self, row: Mapping[str, Any]) -> None:
        if self.protect_cad_document is not None:
            try:
                await self.protect_cad_document(row)
            except OSError as exc:
                raise JobProtectionError(str(exc)) from exc

    async def delete(self, job_id: str, *, skip_model_protection: bool = False) -> None:
        await self.start()
        row = self._require_job(job_id)
        if row["status"] in ACTIVE_STATUSES:
            raise JobConflictError("Cannot delete active job")
        # Legacy runs have no pin receipt, but archive completion already made
        # the protection decision before releasing their retention reference.
        archived = (row.get("task_metadata") or {}).get("archived_at")
        if not skip_model_protection and not archived and (row["status"] == "complete" or row.get("has_results")):
            await self._protect_cad_document(row)
        deleted, event = self.store.delete_job_with_event(job_id, preserve_files=skip_model_protection)
        if not deleted or event is None:
            raise JobNotFoundError(job_id)
        self._remove_from_queue(job_id)
        self._running.discard(job_id)
        self._partial_results.pop(job_id, None)
        self.events.publish(event)

    async def clear_failed(self) -> list[str]:
        await self.start()
        ids, events = self.store.delete_jobs_by_status_with_events(["error"])
        for job_id in ids:
            self._remove_from_queue(job_id)
            self._running.discard(job_id)
            self._partial_results.pop(job_id, None)
        for event in events:
            self.events.publish(event)
        return ids

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait for the queue and worker to empty (test/shutdown observation helper)."""

        async def wait_loop() -> None:
            while self._queue or self._running or (
                self._scheduler_task is not None and not self._scheduler_task.done()
            ):
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait_loop(), timeout)

    def snapshot(self) -> dict[str, Any]:
        """Return the current WS snapshot; callers must have started the runtime."""

        rows, cursor = self.store.snapshot_jobs()
        return {
            "v": 1,
            "kind": "snapshot",
            "cursor": cursor,
            "jobs": [
                self._serialize_job(row, cadlink_store=self.cadlink_store)
                for row in rows
            ],
        }

    async def snapshot_async(self) -> dict[str, Any]:
        """Build the potentially large/cold snapshot without blocking both WS loops."""

        return await asyncio.to_thread(self.snapshot)

    def resume(self, cursor: int) -> list[dict[str, Any]] | None:
        return self.store.replay_events(cursor)

    async def resume_async(self, cursor: int) -> list[dict[str, Any]] | None:
        return await asyncio.to_thread(self.resume, cursor)

    def _ensure_scheduler(self) -> None:
        if self.metal_permit.quarantine_reason is not None:
            return
        if self._shutting_down or self._stopping or not self._started or not self._queue:
            return
        if self._restart_pending():
            # Not while a restart is approved: its release starts the
            # scheduler again (``_restart_called_off``), and so does its expiry.
            self._check_again_when_the_approval_expires()
            return
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        task = asyncio.create_task(self._drain_scheduler(), name="wg2-job-fifo")
        self._scheduler_task = task
        self._keep_task(task)

    def _keep_task(self, task: asyncio.Task[Any]) -> None:
        """Hold tasks strongly, porting v1 ``job_runtime.py:30-41``."""

        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_scheduler(self) -> None:
        try:
            # One lease spans the current FIFO drain. This lets already-pending
            # field work resume only after every queued solve, rather than in
            # the narrow hand-off between adjacent jobs.
            async with self.metal_permit.solve():
                # Admission stops when a stop begins, not only at shutdown: a
                # queued job must not start in the gap a finished or cancelled
                # one leaves, where no Quit marks it and the budget cuts it off.
                # It stays queued for the next start. It stops, too, while an
                # update restart is approved (contract §4.3): the job stays
                # queued for the new process, or for this one if the restart is
                # called off.
                while (
                    self._queue
                    and self.metal_permit.quarantine_reason is None
                    and not self._shutting_down
                    and not self._stopping
                    and not self._restart_pending()
                ):
                    job_id = self._queue.popleft()
                    row = self.store.get_job_row(job_id)
                    if row is None or row["status"] != "queued":
                        continue
                    flag = threading.Event()
                    if row.get("cancellation_requested"):
                        flag.set()
                    self._cancellation_flags[job_id] = flag
                    self._running.add(job_id)
                    try:
                        await self._run_job(job_id, row)
                    finally:
                        self._running.discard(job_id)
                        if self.metal_permit.quarantine_reason is None:
                            self._cancellation_flags.pop(job_id, None)
                        try:
                            prune_results = self._results_cleanup_enabled()
                            _removed_ids, prune_events = await asyncio.to_thread(
                                self.store.prune_terminal_jobs_with_events,
                                retention_days=RESULT_RETENTION_DAYS,
                                max_terminal_jobs=MAX_RETAINED_RESULTS,
                                prune_results=prune_results,
                            )
                            for event in prune_events:
                                self.events.publish(event)
                        except Exception:
                            logger.exception("Post-job retention pruning failed")
        finally:
            self._scheduler_task = None
            if self._queue and not self._shutting_down and not self._stopping:
                self._ensure_scheduler()

    async def _run_job(self, job_id: str, row: Mapping[str, Any]) -> None:
        try:
            effective_request = SolveRequest.model_validate(row["config_json"])
            _refuse_removed_solver(effective_request)
            task_metadata = (
                dict(row.get("task_metadata") or {})
                if isinstance(row.get("task_metadata"), Mapping)
                else {}
            )
            imported_metadata = task_metadata.get("imported_geometry")
            imported_metadata = (
                imported_metadata if isinstance(imported_metadata, Mapping) else {}
            )
            cad_identity = imported_metadata.get("identity")
            cad_identity = cad_identity if isinstance(cad_identity, Mapping) else None
            symmetry_metadata = dict(task_metadata.get("symmetry") or {})
            resolved_quadrants = int(
                symmetry_metadata.get("resolved_quadrants", 1234)
            )
            request = _execution_request(
                effective_request,
                resolved_quadrants=resolved_quadrants,
            )
            imported_record: dict[str, Any] | None = None
            if not isinstance(request.geometry, ParametricGeometrySource):
                if self.cadlink_store is None:
                    raise ImportedSolveRefusal(
                        "ingest_registry_unavailable",
                        "the CAD ingestion registry is unavailable during execution",
                    )
                imported_record = await asyncio.to_thread(
                    get_ingestion_record,
                    self.cadlink_store,
                    request.geometry.ingest_id,
                )
                if imported_record is None:
                    raise ImportedSolveRefusal(
                        "ingest_not_found",
                        f"ingestion record {request.geometry.ingest_id!r} disappeared before execution",
                    )
                imported_record = {
                    **imported_record,
                    "evidence": await asyncio.to_thread(
                        _refuse_imported_fem, request.geometry, imported_record
                    ),
                }
                job_msh_text = await asyncio.to_thread(
                    self.store.get_mesh_artifact, job_id
                )
                job_artifact_present = job_msh_text is not None
                try:
                    if job_msh_text is not None:
                        verify_record_mesh_text(imported_record, job_msh_text)
                    else:
                        job_msh_text = await asyncio.to_thread(
                            read_verified_import_mesh, imported_record
                        )
                except ImportedMeshArtifactError as exc:
                    raise ImportedSolveRefusal(
                        "ingest_mesh_unavailable",
                        str(exc),
                    ) from exc
                # A job queued by an earlier build carries a mesh its digest
                # vouches for and nobody judged; it is checked here, before any
                # engine is fetched, as every submission now is.
                await asyncio.to_thread(
                    _refuse_inverted_reduced_mesh, imported_record, job_msh_text
                )
                # An older record's open-shell check parses the mesh: off the loop.
                open_half = (
                    _imported_decision_refusal(imported_record)
                    or _imported_job_decision_refusal(task_metadata, imported_record)
                    or _imported_side_identity_refusal(imported_record)
                    or await asyncio.to_thread(_imported_open_half_refusal, imported_record, job_msh_text)
                )
                if open_half is not None:
                    raise ImportedSolveRefusal(*open_half)
                imported_record = {
                    **imported_record,
                    "_execution_msh_text": job_msh_text,
                    "_execution_mesh_source": (
                        "job-artifact" if job_artifact_present else "imports-cache"
                    ),
                }
            if imported_record and imported_record.get("fem_regions") and not request.geometry.exterior_only:
                refusal = await _imported_request_refusal_async(
                    request, imported_record, job_msh_text,
                    cancel_check=lambda: self._check_cancelled(job_id))
                if refusal:
                    raise ImportedSolveRefusal(*refusal)
                # Queued requests from older builds obey the same policy at
                # execution; an explicit backend is never silently replaced.
                await resolve_imported_submission(
                    request, self.engine_registry, symmetry_metadata=symmetry_metadata,
                    imported_record=imported_record, imported_msh_text=job_msh_text,
                    request_refusal=None,
                )
            try:
                engine = await self.engine_registry.get_engine(request.options.engine)
            except Exception:
                state = self.store.cancellation_state(job_id)
                if state is not None and state[0] != "queued":
                    return
                raise
            if engine is None:
                state = self.store.cancellation_state(job_id)
                if state is not None and state[0] != "queued":
                    return
                raise EngineUnavailableError(
                    f"Solve engine '{request.options.engine}' became unavailable"
                )
            if self._shutting_down or self._stopping:
                # A stop began while this job was being prepared. It has not
                # started, so its row stays queued for the next start.
                return
            # An update restart may have been approved while this job was being
            # prepared (contract §4.3). ``_mark_running`` refuses to start it
            # then, and puts it back at the front of the queue.
            # Batch Q extends only this established engine-call seam.  FIFO
            # scheduling/recovery remains untouched; real adapters own their
            # gmsh-worker + asyncio.to_thread orchestration, following v1
            # ``simulation_runner.py:397-427,430-527``.
            if request.options.engine != "dryrun":
                admitted, event = self._mark_running(
                    job_id,
                    {
                        "status": "running",
                        "started_at": _now_iso(),
                        "stage": "initializing",
                        "stage_message": f"Initializing {engine.name} solver",
                        "progress": 0.05,
                    },
                    {
                        "status": "running",
                        "stage": "initializing",
                        "progress": 0.05,
                    },
                )
                if not admitted or event is None:
                    return
                self.events.publish(event)
                await self._run_real_engine(
                    job_id,
                    request,
                    engine,
                    effective_request=effective_request,
                    symmetry_metadata=symmetry_metadata,
                    imported_record=imported_record,
                    cad_identity=cad_identity,
                )
                return
            admitted, event = self._mark_running(
                job_id,
                {
                    "status": "running",
                    "started_at": _now_iso(),
                    "stage": "mesh",
                    "stage_message": "Building dry-run mesh",
                    "progress": 0.05,
                },
                {"status": "running", "stage": "mesh", "progress": 0.05},
            )
            if not admitted or event is None:
                return
            self.events.publish(event)

            design = request.design.model_dump(mode="json")
            delay = request.options.stage_delay_ms / 1000.0
            if request.options.verbose:
                await self._append_log(
                    job_id,
                    "Verbose solve options: "
                    f"engine={request.options.engine}, "
                    f"sweep={_sweep_description(request)}, "
                    f"mesh_validation={request.options.mesh_validation_mode}, "
                    f"polar={request.options.polar_config.model_dump(mode='json')}",
                )
            await self._stage(job_id, "mesh", 0.10, "Building dry-run mesh", delay)
            mesh_text, mesh_stats = await asyncio.to_thread(engine.mesh_artifact, design)
            self._check_cancelled(job_id)
            try:
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, mesh_text)
                self.store.update_job(job_id, mesh_stats_json=json.dumps(mesh_stats))
            except Exception as exc:
                # Optional artifact persistence is intentionally non-fatal, as in
                # v1 ``simulation_runner.py:451-466``.
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)

            await self._stage(job_id, "assemble", 0.30, "Assembling dry-run system", delay)
            await self._stage(job_id, "solve", 0.45, "Solving synthetic frequencies", delay)
            for progress in (0.58, 0.70, 0.82):
                self._check_cancelled(job_id)
                event = self._transition(
                    job_id,
                    {"progress": progress},
                    "progress",
                    {"stage": "solve", "progress": progress},
                )
                self.events.publish(event)
                await asyncio.sleep(delay / 3.0 if delay else 0)

            start, end, count = self._frequency_options(request)
            solve_started = time.perf_counter()
            results = await asyncio.to_thread(
                engine.solve,
                design,
                frequency_start_hz=start,
                frequency_end_hz=end,
                num_frequencies=count,
                frequency_spacing=request.options.frequency_spacing,
                frequencies_hz=request.options.frequencies_hz,
                polar_config=effective_polar_config(request),
                mesh_validation_mode=request.options.mesh_validation_mode,
                verbose=request.options.verbose,
            )
            result_metadata = results.setdefault("metadata", {})
            result_metadata.setdefault("solve_path", "full-3d")
            result_metadata.update(
                {
                    "field_plane_available": False,
                    "field_trace_bytes": None,
                    "unavailable_reason": "unsupported_solve_mode",
                }
            )
            result_metadata["solve_wall_time_seconds"] = (
                time.perf_counter() - solve_started
            )
            self._record_execution_metadata(job_id, result_metadata)
            results = self._with_request_metadata(
                results,
                request,
                effective_request=effective_request,
                symmetry_metadata=symmetry_metadata,
            )
            await asyncio.to_thread(self.store.mutate_job_metadata, job_id, {
                "solve_execution": results["metadata"]["solve_execution"],
            })
            self._check_cancelled(job_id)
            await self._stage(
                job_id, "postprocess", 0.90, "Postprocessing synthetic results", delay
            )
            self._check_cancelled(job_id)
            await self._append_log(job_id, "Dry-run result persistence ready")

            try:
                await self._flush_runtime_update(job_id, forget=True)
            except Exception as exc:
                logger.warning(
                    "Final runtime log flush failed for completed job %s: %s",
                    job_id,
                    exc,
                )

            completed_at = _now_iso()
            try:
                event = await asyncio.to_thread(
                    self.store.complete_job,
                    job_id,
                    results,
                    {
                        "status": "complete",
                        "stage": "complete",
                        "stage_message": "Simulation complete",
                        "progress": 1.0,
                        "completed_at": completed_at,
                        "cancellation_requested": False,
                        "error_message": None,
                    },
                    {"status": "complete", "progress": 1.0},
                )
            except Exception as exc:
                # Results persistence failure must end in error rather than leave a
                # running or falsely-complete row (v1 ``simulation_runner.py:540-553``).
                logger.error("Persistence error for job %s: %s", job_id, exc)
                await self._fail_job(
                    job_id,
                    "Results could not be saved. The simulation completed but persistence failed.",
                )
                return
            if event is None:
                self._check_cancelled(job_id)
                return
            self.events.publish(event)
        except MesherShuttingDownError:
            # Mesher admission closes before runtime.shutdown requests job
            # cancellation. A job reaching mesh in that gap is already stopped
            # by Quit (or an update restart), not a simulation failure.
            self._request_shutdown_cancellation(job_id, self._restart_pending())
            await self._cancel_job(job_id)
        except _CancelledAtCheckpoint:
            await self._cancel_job(job_id)
        except asyncio.CancelledError:
            # Preserve 'running' for the next process's orphan recovery.
            raise
        except Exception as exc:
            logger.error("Simulation error for job %s: %s", job_id, exc, exc_info=True)
            await self._fail_job(job_id, _failure_message(exc))

    async def _cancel_job(self, job_id: str) -> None:
        # A job that this process's own shutdown stopped at a checkpoint ends
        # just as recovery records one the budget cut off: interrupted by Quit,
        # or ended by the update restart when that was the shutdown.
        if job_id in self._restart_interrupted:
            message, stage_message = UPDATE_RESTART_MESSAGE, UPDATE_RESTART_STAGE_MESSAGE
        elif job_id in self._quit_interrupted:
            message, stage_message = QUIT_INTERRUPTED_MESSAGE, QUIT_INTERRUPTED_STAGE_MESSAGE
        else:
            message, stage_message = CANCELLED_MESSAGE, CANCELLED_MESSAGE
        self._partial_results.pop(job_id, None)
        await self._discard_channel_bases(job_id)
        await self._discard_radiation_impedance(job_id)
        await self._discard_fem_field(job_id)
        try:
            event = self._transition(
                job_id,
                {
                    "status": "cancelled",
                    "stage": "cancelled",
                    "stage_message": stage_message,
                    "error_message": message,
                    "completed_at": _now_iso(),
                    "cancellation_requested": False,
                },
                "cancelled",
                {"message": message},
            )
        except Exception:
            logger.exception("Could not persist cancellation state for job %s", job_id)
        else:
            self.events.publish(event)

        try:
            await self._flush_runtime_update(job_id, forget=True)
        except Exception:
            logger.exception("Could not persist final logs for cancelled job %s", job_id)

    async def _record_field_persistence_failure(self, job_id, metadata, warning):
        """One failure policy for optional ordinary and coupled plane data."""
        metadata.update({
            "field_plane_available": False, "field_trace_bytes": None,
            "unavailable_reason": "artifact_persistence_failed",
        })
        await self._record_field_warning(job_id, metadata, warning)

    async def _record_field_warning(self, job_id, metadata, warning):
        warnings = metadata.setdefault("warnings", [])
        warnings.append(warning)
        metadata["warning_count"] = len(warnings)
        await self._append_log(job_id, warning)

    async def _persist_coupled_fields(self, job_id, outcome):
        """Retain optional plane data without invalidating the acoustic result.

        coupled_envelope has already mapped the validated sweep, including port
        and transducer responses. Only plane replay reads these sidecars.
        """
        if outcome.execution_metadata:
            self.store.mutate_job_metadata(job_id, outcome.execution_metadata)
        if outcome.fem_field_arrays is None:
            return
        self._check_cancelled(job_id)
        try:
            def publish_fem():
                return self.store.store_fem_field_arrays(job_id, outcome.fem_field_arrays())
            retained = await asyncio.to_thread(publish_fem)
        except Exception as exc:
            logger.warning("FEM field persistence failed for job %s: %s", job_id, exc)
            # Match ordinary field-trace retention: preserve solved acoustics,
            # disable planes, and record why both in the result and job log.
            warning = "FEM field persistence failed; planes are unavailable. Re-solve to retain field data."
            await self._record_field_persistence_failure(
                job_id, outcome.results.setdefault("metadata", {}), warning,
            )
            self.store.mutate_job_metadata(job_id, {
                "has_fem_field_artifact": False, "fem_field_artifact_bytes": None,
                "has_bem_traces_artifact": False, "bem_traces_artifact_bytes": None,
                "fem_plane_unavailable_reason": warning, "plane_scopes": [],
            })
            return
        if not retained:
            self._check_cancelled(job_id)
            raise JobNotFoundError(job_id)

        def publish_bem():
            reason = outcome.fem_plane_unavailable_reason
            self._check_cancelled(job_id)
            try:
                traces = outcome.bem_trace_arrays() if outcome.bem_trace_arrays else None
                if traces is not None:
                    if not self.store.store_bem_trace_arrays(job_id, traces):
                        reason = "Exterior and combined planes unavailable: retained FEM field changed or was removed before BEM trace publication. Re-solve to retain matching traces."
            except OSError:
                logger.exception("Could not retain coupled BEM traces for job %s", job_id)
                reason = "Exterior and combined planes unavailable: BEM trace storage failed. Re-solve after checking available disk space."
            except ValueError as exc:
                logger.exception("Could not retain coupled BEM traces for job %s", job_id)
                reason = ("Exterior and combined planes unavailable: BEM traces exceed the 256 MiB retention limit. Re-solve with a smaller mesh or frequency sweep."
                          if "256 MiB" in str(exc) else
                          "Exterior and combined planes unavailable: BEM trace data is invalid. Re-solve to recreate the traces.")
            except Exception:
                logger.exception("Could not retain coupled BEM traces for job %s", job_id)
                reason = "Exterior and combined planes unavailable: BEM trace preparation failed. Re-solve to retry trace retention."
            if reason:
                self.store.mutate_job_metadata(job_id, {"fem_plane_unavailable_reason": reason,
                                                       "has_bem_traces_artifact": False,
                                                       "bem_traces_artifact_bytes": None,
                                                       "plane_scopes": ["interior"]})
            return reason

        reason = await asyncio.to_thread(publish_bem)
        if reason:
            await self._record_field_warning(job_id, outcome.results.setdefault("metadata", {}), reason)

    async def _run_real_engine(
        self,
        job_id: str,
        request: SolveRequest,
        engine: Any,
        *,
        effective_request: SolveRequest | None = None,
        symmetry_metadata: Mapping[str, Any] | None = None,
        imported_record: Mapping[str, Any] | None = None,
        cad_identity: Mapping[str, Any] | None = None,
    ) -> None:
        """Run one real adapter while preserving Batch J's lifecycle seam.

        Native callbacks arrive from solver threads.  They are marshalled back
        onto the owning event loop before touching WS subscriber queues; stages
        map to the same overall ranges as v1
        ``server/services/simulation_runner.py:263-294``.
        """

        effective_request = effective_request or request

        await self._append_log(job_id, f"Initializing {engine.name} solver")
        fallback_reason = ((symmetry_metadata or {}).get("solver_plan") or {}).get("fallback_reason")
        if fallback_reason:
            await self._append_log(job_id, str(fallback_reason))
        for adjustment in _solver_plan_adjustments(symmetry_metadata):
            if adjustment.get("kind") == BEMPP_WALL_ADJUSTMENT_KIND:
                await self._append_log(
                    job_id, _bempp_wall_adjustment_message(adjustment)
                )
        if request.options.verbose:
            await self._append_log(
                job_id,
                "Verbose solve options: "
                f"sweep={_sweep_description(request)}, "
                f"mesh_validation={request.options.mesh_validation_mode}, "
                f"polar={request.options.polar_config.model_dump(mode='json')}",
            )

        loop = asyncio.get_running_loop()
        stage_tasks: set[asyncio.Task[Any]] = set()
        artifact_persisted = False
        last_progress = time.monotonic()
        callbacks_stopped = threading.Event()

        def stage_callback(stage: str, progress: float, message: str) -> None:
            nonlocal last_progress
            if callbacks_stopped.is_set():
                return
            last_progress = time.monotonic()
            def schedule() -> None:
                if callbacks_stopped.is_set():
                    return
                task = asyncio.create_task(
                    self._report_real_stage(job_id, stage, progress, message),
                    name=f"wg2-stage-{job_id}",
                )
                stage_tasks.add(task)

            loop.call_soon_threadsafe(schedule)

        def result_callback(index: int, result: dict[str, Any]) -> None:
            nonlocal last_progress
            if callbacks_stopped.is_set():
                return
            last_progress = time.monotonic()
            # Native callbacks run in the solver worker. Provisional state and
            # subscriber queues remain owned by the asyncio thread.
            def accept():
                if not callbacks_stopped.is_set():
                    self._accept_partial_result(job_id, int(index), result)
            loop.call_soon_threadsafe(accept)

        async def artifact_callback(msh_text: str, mesh_stats: dict[str, Any]) -> None:
            """Persist immediately after meshing, before native solve like v1 lines 451-493."""

            nonlocal artifact_persisted
            if callbacks_stopped.is_set():
                raise _CancelledAtCheckpoint(CANCELLED_MESSAGE)
            if mesh_stats:
                self.store.update_job(job_id, mesh_stats_json=json.dumps(mesh_stats))
                # A mesh diagnosis that only reaches mesh_stats is a diagnosis
                # nobody reads. These used to be fatal -- an over-budget mesh
                # ended the solve -- so now that the solve continues, the log
                # is where the reason for a slow one has to appear.
                for warning in mesh_stats.get("warnings") or []:
                    await self._append_log(job_id, str(warning))
            try:
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, msh_text)
            except Exception as exc:
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)
            else:
                artifact_persisted = True

        async def execution_metadata_callback(metadata):
            self._check_cancelled(job_id)
            self.store.mutate_job_metadata(job_id, metadata)

        try:
            solve_started = time.perf_counter()
            if imported_record is not None:
                # Ephemeral execution context, like _execution_msh_text;
                # never written back into the ingestion record or settings.
                task_metadata = self.store.get_job_row(job_id)["task_metadata"]
                imported_record = {
                    **imported_record,
                    "_execution_metadata": {
                        "solver_plan": task_metadata.get("solver_plan"),
                        "fem_execution_reroute": task_metadata.get("fem_execution_reroute"),
                        "resolved_quadrants": (symmetry_metadata or {}).get("resolved_quadrants"),
                    },
                }
            async def invoke():
                outcome = await engine.run(
                    request,
                    cancel_cb=lambda: self._check_cancelled(job_id),
                    stage_cb=stage_callback,
                    artifact_cb=artifact_callback,
                    result_cb=result_callback,
                    imported_record=imported_record,
                    execution_metadata_cb=execution_metadata_callback,
                )
                if not isinstance(outcome, EngineRunResult):
                    raise TypeError(f"{engine.name} returned no normalized EngineRunResult")
                return outcome
            def stop_callbacks():
                callbacks_stopped.set()
                flag = self._cancellation_flags.get(job_id)
                if flag is not None:
                    flag.set()

            stall_limit = stall_timeout_seconds()
            outcome = await watch_native_operation(
                invoke(), permit=self.metal_permit, retained=self._native_tasks,
                last_progress=lambda: last_progress,
                timeout_seconds=stall_limit, stop_callbacks=stop_callbacks,
            )
        finally:
            # Drain callbacks queued by the final native frequency/result hook.
            await asyncio.sleep(0)
            if stage_tasks:
                gathered = await asyncio.gather(*tuple(stage_tasks), return_exceptions=True)
                for error in gathered:
                    if isinstance(error, BaseException) and not isinstance(
                        error, asyncio.CancelledError
                    ):
                        logger.error(
                            "Stage/progress persistence failed for job %s: %s",
                            job_id,
                            error,
                            exc_info=(type(error), error, error.__traceback__),
                        )
                stage_tasks.clear()

        self._check_cancelled(job_id)
        outcome_metadata = outcome.results.setdefault("metadata", {})
        if isinstance(request.geometry, ImportedGeometrySource) and imported_record is not None:
            evidence = imported_record.get("evidence")
            evidence = evidence if isinstance(evidence, Mapping) else {}
            fem_regions = evidence.get("fem_regions") or []
            fem_regions = fem_regions if isinstance(fem_regions, list) else []
            if fem_regions and request.geometry.exterior_only:
                # The runtime owns this note for every imported engine, using
                # the same exclusion metadata as legacy FEM air volumes.
                outcome_metadata["exterior_only"] = True
                outcome_metadata["fem_exclusion"] = {
                    "excluded": True,
                    "declared_volume_count": _evidence_count(evidence, "fem_air_volumes"),
                    "declared_region_count": len(fem_regions),
                    "region_ids": [
                        region["region_id"] for region in fem_regions
                        if isinstance(region, Mapping) and region.get("region_id")
                    ],
                    "reason": "Phase 2 exterior_only override",
                }
        outcome_metadata.setdefault("solve_path", "full-3d")
        outcome_metadata["solve_wall_time_seconds"] = time.perf_counter() - solve_started
        if outcome.msh_text and not artifact_persisted:
            try:
                if outcome.mesh_stats is not None:
                    self.store.update_job(job_id, mesh_stats_json=json.dumps(outcome.mesh_stats))
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, outcome.msh_text)
            except Exception as exc:
                # V1 makes the downloadable artifact optional
                # (``simulation_runner.py:451-466``).
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)
        field_traces = getattr(outcome, "field_traces", None)
        field_trace_reason = getattr(outcome, "field_trace_unavailable_reason", None)
        if field_traces is not None:
            try:
                stored_field_traces = await asyncio.to_thread(
                    self.store.store_field_traces,
                    job_id,
                    field_traces,
                )
            except Exception as exc:
                logger.warning(
                    "Field-trace persistence failed for job %s: %s",
                    job_id,
                    exc,
                )
                await self._record_field_persistence_failure(
                    job_id, outcome_metadata,
                    "Field-trace persistence failed; planes are unavailable. Re-solve to retain field data.",
                )
            else:
                if stored_field_traces is None:
                    self._check_cancelled(job_id)
                    raise JobNotFoundError(job_id)
                outcome_metadata.update(
                    {
                        "field_plane_available": True,
                        "field_trace_bytes": stored_field_traces.bytes,
                        "unavailable_reason": None,
                    }
                )
        else:
            outcome_metadata.update(
                {
                    "field_plane_available": False,
                    "field_trace_bytes": None,
                    "unavailable_reason": field_trace_reason or "unsupported_solve_mode",
                }
            )
        await self._persist_coupled_fields(job_id, outcome)
        self._record_execution_metadata(job_id, outcome_metadata)
        channel_bases = getattr(outcome, "channel_bases", None)
        if channel_bases is not None:
            try:
                await asyncio.to_thread(
                    self.store.store_channel_bases, job_id, channel_bases
                )
            except Exception as exc:
                # Recombination degrades to "re-solve to change the crossover";
                # the solve itself must not fail over an optional artifact.
                logger.warning(
                    "Channel-bases persistence failed for job %s: %s", job_id, exc
                )
                row = self.store.get_job_row(job_id)
                task_metadata = (
                    dict(row.get("task_metadata") or {})
                    if row is not None
                    else {}
                )
                warnings = [
                    str(value)
                    for value in task_metadata.get("persistence_warnings") or []
                ]
                warnings.append(f"Pressure-basis artifact could not be saved: {exc}")
                await asyncio.to_thread(
                    self.store.mutate_job_metadata,
                    job_id,
                    {
                        "has_pressure_basis_artifact": False,
                        "pressure_basis_artifact_bytes": None,
                        "persistence_warnings": warnings,
                    },
                )
        radiation_matrix = getattr(outcome, "radiation_impedance", None)
        if radiation_matrix is not None:
            try:
                await asyncio.to_thread(
                    self.store.store_radiation_impedance,
                    job_id,
                    radiation_matrix,
                )
            except Exception as exc:
                # Results remain usable; only the optional lossless matrix
                # download is unavailable if persistence fails.
                warning = (
                    "Passive-cardioid radiation-impedance artifact could not be "
                    f"saved: {exc}"
                )
                logger.warning(
                    "Radiation-impedance persistence failed for job %s: %s",
                    job_id,
                    exc,
                )
                row = self.store.get_job_row(job_id)
                task_metadata = (
                    dict(row.get("task_metadata") or {})
                    if row is not None
                    else {}
                )
                warnings = [
                    str(value)
                    for value in task_metadata.get("persistence_warnings") or []
                ]
                warnings.append(warning)
                await asyncio.to_thread(
                    self.store.mutate_job_metadata,
                    job_id,
                    {
                        "has_radiation_impedance_artifact": False,
                        "radiation_impedance_artifact_bytes": None,
                        "persistence_warnings": warnings,
                    },
                )

        self._check_cancelled(job_id)
        try:
            await self._flush_runtime_update(job_id, forget=True)
        except Exception as exc:
            logger.warning(
                "Final runtime log flush failed for completed job %s: %s",
                job_id,
                exc,
            )
        completed_at = _now_iso()
        try:
            finished_results = self._with_request_metadata(
                outcome.results,
                request,
                effective_request=effective_request,
                symmetry_metadata=symmetry_metadata,
                cad_identity=cad_identity,
            )
            channel_executions = {
                channel_id: payload.get("metadata", {}).get("solve_execution")
                for channel_id, payload in (finished_results.get("channels") or {}).items()
                if isinstance(payload, Mapping)
            }
            await asyncio.to_thread(self.store.mutate_job_metadata, job_id, {
                "solve_execution": finished_results["metadata"]["solve_execution"],
                "channel_solve_executions": channel_executions,
            })
            event = await asyncio.to_thread(
                self.store.complete_job,
                job_id,
                finished_results,
                {
                    "status": "complete",
                    "stage": "complete",
                    "stage_message": "Simulation complete",
                    "progress": 1.0,
                    "completed_at": completed_at,
                    "cancellation_requested": False,
                    "error_message": None,
                },
                {"status": "complete", "progress": 1.0},
            )
        except Exception as exc:
            logger.error("Persistence error for job %s: %s", job_id, exc)
            await self._fail_job(
                job_id,
                "Results could not be saved. The simulation completed but persistence failed.",
            )
            return
        if event is not None:
            self.events.publish(event)
        else:
            self._check_cancelled(job_id)
        self._partial_results.pop(job_id, None)

    def _accept_partial_result(
        self, job_id: str, index: int, result: Mapping[str, Any]
    ) -> None:
        """Merge and fan out a solver-thread frequency callback."""

        if job_id not in self._running or index < 0:
            return
        revision = index + 1
        previous = self._partial_results.get(job_id)
        previous_revision = int(previous["revision"]) if previous is not None else 0
        if revision <= previous_revision:
            return
        merged = (
            _extend_provisional_results(previous["result"], result)
            if previous is not None
            else _copy_provisional_result(result)
        )
        self._partial_results[job_id] = {"revision": revision, "result": merged}
        self.events.publish(
            {
                "v": 1,
                "kind": "partialResult",
                "jobId": job_id,
                "revision": revision,
                "snapshot": False,
                "result": copy.deepcopy(dict(result)),
            }
        )

    async def _report_real_stage(
        self,
        job_id: str,
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        normalized = max(0.0, min(1.0, float(progress)))
        if stage in {"mesh_prepare", "mesh_validate"}:
            public_stage = "mesh"
            overall = 0.10 + normalized * 0.20
        elif stage in {"setup", "solver_setup"}:
            public_stage = "assemble"
            overall = 0.30 + normalized * 0.05
        elif stage == "frequency_solve":
            public_stage = "solve"
            overall = 0.35 + normalized * 0.50
        elif stage == "radiation_impedance":
            public_stage = "radiation_impedance"
            overall = 0.85 + normalized * 0.10
        elif stage in {"directivity", "finalizing"}:
            public_stage = "postprocess"
            overall = 0.85 + normalized * 0.14
        else:
            public_stage = str(stage)
            overall = normalized
        try:
            await self._stage(job_id, public_stage, overall, message, 0.0)
        except (_CancelledAtCheckpoint, JobNotFoundError):
            return

    async def _stage(
        self, job_id: str, stage: str, progress: float, message: str, delay: float
    ) -> None:
        self._check_cancelled(job_id)
        await self._queue_runtime_update(
            job_id,
            stage=stage,
            progress=progress,
            message=message,
            log_message=message,
        )
        await asyncio.sleep(delay)
        if delay:
            self._check_cancelled(job_id)

    async def _append_log(self, job_id: str, message: str) -> None:
        await self._queue_runtime_update(job_id, log_message=str(message))

    def _runtime_update_state(self, job_id: str) -> _PendingRuntimeUpdate:
        state = self._pending_updates.get(job_id)
        if state is not None:
            return state
        row = self._require_job(job_id)
        state = _PendingRuntimeUpdate(
            persisted_stage=(str(row["stage"]) if row.get("stage") is not None else None),
            last_persisted_at=time.monotonic(),
        )
        self._pending_updates[job_id] = state
        return state

    async def _queue_runtime_update(
        self,
        job_id: str,
        *,
        stage: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        log_message: str | None = None,
    ) -> None:
        if self._shutting_down:
            return
        state = self._runtime_update_state(job_id)
        async with state.lock:
            if (
                self._shutting_down
                or state.closed
                or self._pending_updates.get(job_id) is not state
            ):
                return
            stage_changed = stage is not None and stage != (
                state.stage if state.stage is not None else state.persisted_stage
            )
            if stage_changed and state.pending:
                await self._flush_runtime_update_locked(job_id, state)

            if stage is not None:
                state.stage = str(stage)
                state.progress = float(progress if progress is not None else 0.0)
                state.message = str(message or "")
            if log_message is not None:
                state.log_lines.append(str(log_message))

            elapsed = time.monotonic() - state.last_persisted_at
            if stage_changed or elapsed >= self._persistence_interval_seconds:
                await self._flush_runtime_update_locked(job_id, state)
            else:
                self._schedule_runtime_flush(
                    job_id,
                    state,
                    self._persistence_interval_seconds - elapsed,
                )

    def _schedule_runtime_flush(
        self, job_id: str, state: _PendingRuntimeUpdate, delay: float
    ) -> None:
        if state.timer_task is not None and not state.timer_task.done():
            return

        async def flush_later() -> None:
            try:
                await asyncio.sleep(max(0.0, delay))
                async with state.lock:
                    await self._flush_runtime_update_locked(job_id, state)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Buffered runtime persistence failed for job %s", job_id)
            finally:
                if state.timer_task is asyncio.current_task():
                    state.timer_task = None

        task = asyncio.create_task(flush_later(), name=f"wg2-persist-{job_id}")
        state.timer_task = task
        self._keep_task(task)

    async def _flush_runtime_update_locked(
        self, job_id: str, state: _PendingRuntimeUpdate
    ) -> None:
        timer = state.timer_task
        if timer is not None and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
        state.timer_task = None
        while not state.closed and state.pending:
            fields: dict[str, Any] = {}
            stage_payload: dict[str, Any] | None = None
            if state.stage is not None:
                fields = {
                    "stage": state.stage,
                    "stage_message": state.message or "",
                    "progress": (
                        state.progress if state.progress is not None else 0.0
                    ),
                }
                stage_payload = {
                    "stage": state.stage,
                    "message": state.message or "",
                    "progress": (
                        state.progress if state.progress is not None else 0.0
                    ),
                }

            if state.log_lines and state.pinned_log_lines is None:
                # Keep this exact prefix across failures and cancellation. A
                # later callback may extend log_lines before the retry, but it
                # must not change the bytes checked at expected_log_size.
                state.pinned_log_lines = tuple(state.log_lines)
            log_lines = state.pinned_log_lines or ()
            if log_lines and state.expected_log_size is None:
                state.expected_log_size = await asyncio.to_thread(
                    self.store.job_log_size, job_id
                )

            changed, events = await asyncio.to_thread(
                self.store.persist_runtime_update,
                job_id,
                fields,
                stage_payload=stage_payload,
                log_lines=log_lines,
                expected_log_size=state.expected_log_size,
                max_log_lines=MAX_LOG_LINES,
                max_log_chars=MAX_LOG_CHARS,
                max_log_event_chars=MAX_LOG_EVENT_CHARS,
            )
            if changed:
                if state.stage is not None:
                    state.persisted_stage = state.stage
                for event in events:
                    self.events.publish(event)

            state.stage = None
            state.progress = None
            state.message = None
            if log_lines:
                if tuple(state.log_lines[: len(log_lines)]) != log_lines:
                    raise RuntimeError("Pinned runtime log batch is no longer a prefix")
                del state.log_lines[: len(log_lines)]
            state.pinned_log_lines = None
            state.expected_log_size = None
            state.last_persisted_at = time.monotonic()

    async def _flush_runtime_update(self, job_id: str, *, forget: bool = False) -> None:
        state = self._pending_updates.get(job_id)
        if state is None:
            return
        try:
            async with state.lock:
                await self._flush_runtime_update_locked(job_id, state)
        finally:
            if forget:
                state.closed = True
                timer = state.timer_task
                if (
                    timer is not None
                    and timer is not asyncio.current_task()
                    and not timer.done()
                ):
                    timer.cancel()
                state.timer_task = None
                if self._pending_updates.get(job_id) is state:
                    self._pending_updates.pop(job_id, None)

    async def _flush_all_runtime_updates(self, *, forget: bool = False) -> None:
        for job_id in tuple(self._pending_updates):
            await self._flush_runtime_update(job_id, forget=forget)

    async def _fail_job(self, job_id: str, message: str) -> None:
        if not message.strip():
            message = FAILED_WITHOUT_DETAIL_MESSAGE
        self._partial_results.pop(job_id, None)
        await self._discard_channel_bases(job_id)
        await self._discard_radiation_impedance(job_id)
        await self._discard_fem_field(job_id)
        try:
            event = self._transition(
                job_id,
                {
                    "status": "error",
                    "stage": "error",
                    "stage_message": "Simulation failed",
                    "error_message": message,
                    "completed_at": _now_iso(),
                },
                "failed",
                {"message": message},
            )
        except Exception:
            logger.exception("Could not persist failure state for job %s", job_id)
        else:
            self.events.publish(event)

        try:
            await self._flush_runtime_update(job_id, forget=True)
        except Exception:
            logger.exception("Could not persist final logs for failed job %s", job_id)

    async def _discard_fem_field(self, job_id: str) -> None:
        try:
            await asyncio.to_thread(self.store.delete_fem_field, job_id)
        except Exception:
            logger.exception("Could not discard FEM field for job %s", job_id)

    async def _discard_radiation_impedance(self, job_id: str) -> None:
        try:
            await asyncio.to_thread(self.store.delete_radiation_impedance, job_id)
        except Exception:
            logger.exception(
                "Could not clean up radiation-impedance artifact for job %s",
                job_id,
            )

    async def _discard_channel_bases(self, job_id: str) -> None:
        try:
            await asyncio.to_thread(self.store.delete_channel_bases, job_id)
        except Exception:
            logger.exception(
                "Could not clean up pressure-basis artifact for job %s", job_id
            )

    def _check_cancelled(self, job_id: str) -> None:
        flag = self._cancellation_flags.get(job_id)
        if flag is not None:
            if flag.is_set():
                raise _CancelledAtCheckpoint(CANCELLED_MESSAGE)
            return
        # Deliberately synchronous: the solver thread calls this from inside a
        # native progress callback, where there is no event loop to await on.
        state = self.store.cancellation_state(job_id)
        if state is None:
            raise JobNotFoundError(job_id)
        status, cancellation_requested = state
        if cancellation_requested or status == "cancelled":
            raise _CancelledAtCheckpoint(CANCELLED_MESSAGE)

    def _transition(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        changed, event = self.store.update_job_with_event(
            job_id, fields, event_type, payload
        )
        if not changed or event is None:
            raise JobNotFoundError(job_id)
        return event

    def _require_job(self, job_id: str) -> dict[str, Any]:
        row = self.store.get_job_row(job_id)
        if row is None:
            raise JobNotFoundError(job_id)
        return row

    def _remove_from_queue(self, job_id: str) -> None:
        if not self._queue:
            return
        self._queue = deque(item for item in self._queue if item != job_id)

    @staticmethod
    def parse_status_filter(raw: str | None) -> list[str] | None:
        if raw is None:
            return None
        values = list(dict.fromkeys(token.strip() for token in raw.split(",") if token.strip()))
        if not values:
            return None
        invalid = [value for value in values if value not in ALLOWED_STATUSES]
        if invalid:
            raise ValueError(
                f"status filter contains unsupported values: {', '.join(invalid)}"
            )
        return values

    @staticmethod
    def _config_summary(request: SolveRequest) -> dict[str, Any]:
        start, end, count = JobRuntime._frequency_options(request)
        if isinstance(request.geometry, ParametricGeometrySource):
            return {
                "formula_type": request.design.formula,
                "frequency_range": [start, end],
                "num_frequencies": count,
                "frequency_source": (
                    "explicit_list"
                    if request.options.frequencies_hz is not None
                    else "generated_grid"
                ),
                "engine": request.options.engine,
                "accuracy": request.options.accuracy,
                "design_revision": request.design_revision,
                "polar_grid": effective_polar_grid(request),
            }
        return {
            "formula_type": "cad-import",
            "geometry_type": "imported",
            "frequency_range": [start, end],
            "num_frequencies": count,
            "frequency_source": (
                "explicit_list"
                if request.options.frequencies_hz is not None
                else "generated_grid"
            ),
            "engine": request.options.engine,
            "accuracy": request.options.accuracy,
            "design_revision": 0,
            "ingest_id": request.geometry.ingest_id,
            "drive_channel_ids": [
                channel.id for channel in request.geometry.drive_channels
            ],
            "combine": (
                {
                    "id": request.geometry.combine.id,
                    "members": list(request.geometry.combine.members),
                    "crossovers_hz": request.geometry.combine.linked_crossovers_hz(),
                    "reference": request.geometry.combine.resolved_reference,
                }
                if request.geometry.combine is not None
                else None
            ),
            "polar_grid": effective_polar_grid(request),
        }

    @staticmethod
    def _with_request_metadata(
        results: Mapping[str, Any],
        request: SolveRequest,
        *,
        effective_request: SolveRequest | None = None,
        symmetry_metadata: Mapping[str, Any] | None = None,
        cad_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(results)
        metadata = dict(enriched.get("metadata") or {})
        if isinstance(request.geometry, ParametricGeometrySource):
            metadata["design_revision"] = request.design_revision
        else:
            metadata["geometry_type"] = "imported"
            metadata["ingest_id"] = request.geometry.ingest_id
        metadata["polar_grid"] = effective_polar_grid(request)
        if symmetry_metadata is not None:
            metadata["symmetry"] = dict(symmetry_metadata)
        engine = request.options.engine
        choice = request.options.accuracy
        def execution(payload: Mapping[str, Any]) -> dict[str, Any]:
            local = payload.get("metadata") or {}
            formulation = next(
                (block["formulation"] for name in ("solver_engine", "beat", "metal", "axisym", "bempp")
                 if isinstance((block := local.get(name)), Mapping)
                 and isinstance(block.get("formulation"), str)),
                None,
            )
            return {"accuracy": choice, "engine": engine, "formulation": formulation}
        metadata["solve_execution"] = execution(enriched)
        fallback_reason = ((symmetry_metadata or {}).get("solver_plan") or {}).get("fallback_reason")
        if fallback_reason:
            metadata["solve_execution"]["fallback_reason"] = fallback_reason
        channels = enriched.get("channels")
        if isinstance(channels, Mapping):
            copied = {}
            for channel_id, payload in channels.items():
                if isinstance(payload, Mapping):
                    item = dict(payload)
                    channel_meta = dict(item.get("metadata") or {})
                    channel_meta["solve_execution"] = execution(item)
                    item["metadata"] = channel_meta
                    copied[channel_id] = item
                else:
                    copied[channel_id] = payload
            enriched["channels"] = copied
        enriched["metadata"] = metadata
        return enrich_result_contract(
            enriched,
            request,
            effective_request=effective_request,
            cad_identity=cad_identity,
        )

    def _record_execution_metadata(
        self, job_id: str, result_metadata: Mapping[str, Any]
    ) -> None:
        changes: dict[str, Any] = {
            "solve_path": result_metadata.get("solve_path", "full-3d"),
            "axisymmetric_eligibility_reasons": list(
                result_metadata.get("axisymmetric_eligibility_reasons") or []
            ),
            "solve_wall_time_seconds": float(
                result_metadata.get("solve_wall_time_seconds") or 0.0
            ),
        }
        for key in (
            "field_plane_available",
            "field_trace_bytes",
            "unavailable_reason",
        ):
            if key in result_metadata:
                changes[key] = result_metadata[key]
        self.store.mutate_job_metadata(
            job_id,
            changes,
        )

    @staticmethod
    def _frequency_options(request: SolveRequest) -> tuple[float, float, int]:
        """Summarize the sweep as (start, end, count).

        An explicit list is summarized by its own endpoints and length, so job
        listings and config summaries describe the sweep that actually runs.
        """

        simulation = (
            request.design.root.simulation
            if isinstance(request.geometry, ParametricGeometrySource)
            else None
        )

        def numeric(expr: Any, default: float) -> float:
            value = getattr(expr, "value", None) if expr is not None else None
            return float(value) if value is not None else default

        explicit = request.options.frequencies_hz
        if explicit is not None:
            return float(explicit[0]), float(explicit[-1]), len(explicit)
        if request.options.frequency_range is not None:
            start, end = request.options.frequency_range
        else:
            if isinstance(request.geometry, ImportedGeometrySource):
                raise ValueError("imported geometry requires an explicit frequency range")
            assert simulation is not None
            start = numeric(simulation.f1, default_sweep_start_hz())
            end = numeric(simulation.f2, 20_000.0)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("frequency bounds must be finite")
        count = request.options.num_frequencies
        if count is None:
            if isinstance(request.geometry, ImportedGeometrySource):
                raise ValueError("imported geometry requires an explicit frequency count")
            assert simulation is not None
            count = int(round(numeric(simulation.num_frequencies, 32.0)))
        count = max(1, min(401, count))
        if start <= 0 or end <= start:
            start, end = default_sweep_start_hz(), 20_000.0
        return float(start), float(end), count

    @staticmethod
    def _serialize_job(
        row: Mapping[str, Any],
        *,
        detailed: bool = False,
        cadlink_store: CadLinkStore | None = None,
    ) -> dict[str, Any]:
        from server.jobs.cad_preparation import job_cad_state

        metadata = row.get("task_metadata") if isinstance(row.get("task_metadata"), dict) else {}
        field_metadata_present = any(
            key in metadata
            for key in ("field_plane_available", "field_trace_bytes", "unavailable_reason")
        )
        field_unavailable_reason = metadata.get("unavailable_reason")
        if (
            not field_metadata_present
            and row.get("status") == "complete"
            and bool(row.get("has_results"))
        ):
            field_unavailable_reason = "solve_predates_traces"
        stored_config = row.get("config_json") or {}
        cad_intent = (
            dict(stored_config)
            if isinstance(stored_config, Mapping)
            and stored_config.get("type") == "cad_intent"
            else None
        )
        geometry = stored_config.get("geometry")
        imported = isinstance(geometry, Mapping) and geometry.get("type") == "imported"
        design = resolve_job_design(row.get("script_snapshot"))
        if cad_intent is not None:
            design_availability = {
                "reopenable": False,
                "source": "cad-import",
                "reason_code": "no_stored_design",
                "reason": (
                    "This solve is still being prepared from a CAD return, so it "
                    "has no design to reopen yet."
                    if row.get("status") == "preparing"
                    else "This solve ended while it was being prepared from a CAD "
                    "return, before it had a design to reopen. Solve it again "
                    "from CAD Link."
                ),
                "note": None,
            }
        elif imported:
            design_availability = {
                "reopenable": False,
                "source": "cad-import",
                "reason_code": "imported_geometry",
                "reason": (
                    "Solved from an imported mesh; there are no WG parameters to edit."
                    if row.get("config_summary_json", {}).get("import_origin") == "mesh-file"
                    else "Solved from a Fusion return; there are no WG parameters to edit."
                ),
                "note": None,
            }
        else:
            design_availability = design.as_availability()
        cad_source = None
        if imported:
            imported_metadata = metadata.get("imported_geometry")
            imported_metadata = (
                imported_metadata if isinstance(imported_metadata, Mapping) else {}
            )
            geometry = geometry if isinstance(geometry, Mapping) else {}
            ingest_id = imported_metadata.get("ingest_id") or geometry.get("ingest_id")
            design_id = imported_metadata.get("anchor_design_id")
            lineage_id = imported_metadata.get("anchor_lineage_id")
            archive_stem = imported_metadata.get("archive_stem")
            manifest_sha256 = (
                imported_metadata.get("manifest_sha256")
                or geometry.get("manifest_sha256")
            )
            stored_document = imported_metadata.get("document")
            stored_document = (
                stored_document if isinstance(stored_document, Mapping) else {}
            )
            recovered_document: Mapping[str, Any] = {}
            transformed_geometry_hash = imported_metadata.get("transformed_geometry_hash")
            solve_model_sha256 = imported_metadata.get("solve_model_sha256")

            # Jobs created before CAD project history retained only the ingest
            # id (and, in the newest legacy slice, the anchor design id). The
            # immutable ingestion and design registries still contain the rest,
            # so recover it at the API boundary instead of making those runs
            # permanently anonymous in the project history.
            needs_registry_recovery = any(
                key not in imported_metadata
                for key in (
                    "anchor_design_id", "anchor_lineage_id", "archive_stem",
                    "manifest_sha256", "document", "transformed_geometry_hash",
                    "solve_model_sha256",
                )
            )
            if cadlink_store is not None and needs_registry_recovery:
                try:
                    record = (
                        get_ingestion_record(cadlink_store, str(ingest_id))
                        if ingest_id
                        else None
                    )
                    if isinstance(record, Mapping):
                        transformed_geometry_hash = transformed_geometry_hash or record.get("transformed_geometry_hash")
                        solve_model_sha256 = solve_model_sha256 or record.get("solve_model_sha256")
                        anchor = record.get("anchor")
                        anchor = anchor if isinstance(anchor, Mapping) else {}
                        design_id = design_id or anchor.get("design_id")
                        manifest_sha256 = (
                            manifest_sha256 or record.get("manifest_sha256")
                        )
                        document = record.get("document")
                        recovered_document = (
                            document if isinstance(document, Mapping) else {}
                        )
                    design_row = (
                        cadlink_store.get_design(str(design_id))
                        if design_id
                        else None
                    )
                    if design_row is not None:
                        lineage_id = lineage_id or design_row.get("lineage_id")
                    names = (
                        cadlink_store.get_lineage_cad_names(str(lineage_id))
                        if lineage_id
                        else None
                    ) or {}
                    archive_stem = (
                        archive_stem
                        or names.get("archive_stem")
                        or names.get("bundle_stem")
                    )
                except Exception:
                    # Job listing must remain available if the advisory CAD
                    # registry cannot be read. The stored fields still surface.
                    logger.warning(
                        "Could not recover legacy CAD provenance for job %s",
                        row.get("id"),
                        exc_info=True,
                    )
            # Recorded on jobs from model records on; resolved for older ones
            # from the ingest's model, else the project the run was filed under.
            model_id = imported_metadata.get("model_id")
            if "model_id" not in imported_metadata and cadlink_store is not None and ingest_id:
                try:
                    model_id = (cadlink_store.get_ingest(str(ingest_id)) or {}).get("model_id")
                except Exception:
                    logger.warning("Could not read the model of job %s", row.get("id"), exc_info=True)
            if "model_id" not in imported_metadata:
                model_id = model_id or lineage_id
            cad = metadata.get("cad") if isinstance(metadata.get("cad"), Mapping) else {}
            cad_setup = cad.get("setup") if isinstance(cad.get("setup"), Mapping) else {}
            cad_source = {
                "ingest_id": ingest_id,
                "model_id": model_id or None,
                # The setup revision the run was made from, when it recorded one:
                # an unedited Solve of a restored run sends it back.
                "setup_revision_id": (
                    imported_metadata.get("setup_revision_id") or cad_setup.get("revision_id") or None
                ),
                "design_id": design_id,
                "lineage_id": lineage_id,
                "archive_stem": archive_stem,
                "manifest_sha256": manifest_sha256,
                "transformed_geometry_hash": transformed_geometry_hash,
                "solve_model_sha256": solve_model_sha256,
                "document_name": (
                    stored_document.get("name")
                    or recovered_document.get("name")
                    or None
                ),
                "return_state_hash": (
                    stored_document.get("return_state_hash")
                    or recovered_document.get("return_state_hash")
                ),
                "identity": imported_metadata.get("identity"),
                "domain_decision": (
                    dict(imported_metadata["domain_decision"])
                    if isinstance(imported_metadata.get("domain_decision"), Mapping)
                    else None
                ),
            }
        # Without a registry or recorded archive provenance there is no archive
        # identity to advertise. API reads have the registry, which resolves old
        # rows through the same legacy-location owner as every archive writer.
        archive_location = (
            archive_identity.job_archive_location(row, cad_source)
            if cadlink_store is not None or metadata.get("archive") or metadata.get("archived_path") or cad_source
            else None
        )
        item = {
            **JobDesignIdentity(
                design_uid=stored_config.get("design_uid"),
                design_name=stored_config.get("design_name"),
            ).model_dump(mode="json"),
            "id": row.get("id"),
            "client_request_id": stored_config.get("client_request_id"),
            "client_metadata": stored_config.get("client_metadata") or {},
            # A CAD solve that is still being prepared, or ended refused, was
            # never a run: it has no run number, and its lineage is in its intent.
            "run_number": row.get("run_number"),
            "parent_job_id": row.get("parent_job_id")
            or (cad_intent.get("parent_job_id") if cad_intent is not None else None),
            "status": row.get("status"),
            "progress": float(row.get("progress", 0.0)),
            "stage": row.get("stage"),
            "stage_message": row.get("stage_message"),
            "created_at": row.get("created_at"),
            "queued_at": row.get("queued_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "config_summary": row.get("config_summary_json") or {},
            "solve_options": _stored_solve_options(stored_config).model_dump(mode="json"),
            "solve_accuracy": metadata.get("solve_accuracy") or "fast",
            "solve_execution": metadata.get("solve_execution"),
            "channel_solve_executions": metadata.get("channel_solve_executions") or {},
            "has_results": bool(row.get("has_results")),
            "has_mesh_artifact": bool(row.get("has_mesh_artifact")),
            "has_pressure_basis_artifact": bool(
                metadata.get("has_pressure_basis_artifact")
            ),
            "pressure_basis_artifact_bytes": metadata.get(
                "pressure_basis_artifact_bytes"
            ),
            "has_radiation_impedance_artifact": bool(
                metadata.get("has_radiation_impedance_artifact")
            ),
            "radiation_impedance_artifact_bytes": metadata.get(
                "radiation_impedance_artifact_bytes"
            ),
            **(
                {"has_fem_field_artifact": True}
                if metadata.get("has_fem_field_artifact") and fem_regions_enabled()
                else {}
            ),
            "field_plane_available": bool(metadata.get("field_plane_available")),
            "field_trace_bytes": metadata.get("field_trace_bytes"),
            "unavailable_reason": field_unavailable_reason,
            "persistence_warnings": [
                str(value) for value in metadata.get("persistence_warnings") or []
            ],
            "label": row.get("label"),
            "error_message": row.get("error_message"),
            "cancellation_requested": bool(row.get("cancellation_requested")),
            "mesh_stats": row.get("mesh_stats"),
            "script_snapshot": design.snapshot
            if design.snapshot is not None
            else row.get("script_snapshot"),
            "design_availability": design_availability,
            "design_revision": int(metadata.get("design_revision") or 0),
            "polar_grid": metadata.get("polar_grid") or {},
            "rating": metadata.get("rating"),
            "exported_files": metadata.get("exported_files") or [],
            "auto_export_completed_at": metadata.get("auto_export_completed_at"),
            "archived_at": metadata.get("archived_at"),
            **JobArchiveIdentity(
                archive_key=(metadata.get("archive") or {}).get("key"),
                archive_folder=archive_location.folder if archive_location else "",
                archive_run=archive_location.run if archive_location else "",
                archived_path=metadata.get("archived_path"),
            ).model_dump(mode="json"),
            "auto_export_formats": metadata.get("auto_export_formats") or {},
            "raw_results_file": metadata.get("raw_results_file"),
            "mesh_artifact_file": metadata.get("mesh_artifact_file"),
            "results_discarded_at": metadata.get("results_discarded_at"),
            "mesh_discarded_at": metadata.get("mesh_discarded_at"),
            "log_tail": metadata.get("log_tail") or [],
            "symmetry": metadata.get("symmetry") or {},
            "solve_path": metadata.get("solve_path"),
            "axisymmetric_eligibility_reasons": metadata.get(
                "axisymmetric_eligibility_reasons"
            ) or [],
            "solve_wall_time_seconds": metadata.get("solve_wall_time_seconds"),
            "cad_provenance": (
                dict(metadata["cad"]) if isinstance(metadata.get("cad"), Mapping) else None
            ),
            "cad_state": job_cad_state(row),
            "cad_source": cad_source,
            "cad_setup": dict(geometry) if imported else None,
            "cad_intent": cad_intent,
        }
        state = item["cad_state"]
        if state and state.get("prepare_failed"):
            # A failed preparation shows WG's calm words everywhere, including
            # rows written before raw text stopped being stored.
            item["error_message"] = state.get("message")
            provenance = item["cad_provenance"]
            if provenance and isinstance(provenance.get("refusal"), Mapping):
                provenance["refusal"] = {**provenance["refusal"], "message": state.get("message")}
        if detailed:
            item["updated_at"] = row.get("updated_at")
            item["message"] = item["error_message"]
        return item


__all__ = [
    "EngineUnavailableError",
    "EventBroker",
    "ImportedSolveRefusal",
    "JobConflictError",
    "JobProtectionError",
    "JobMeshDiscardedError",
    "JobNotFoundError",
    "JobResourceUnavailableError",
    "JobRuntime",
    "MeshArtifactDownload",
    "SubmissionResolution",
    "SymmetryValidationError",
    "UnknownEngineError",
    "merge_provisional_results",
    "resolve_imported_submission",
    "resolve_submission",
]
