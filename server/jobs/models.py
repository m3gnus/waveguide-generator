"""Generated-client-friendly request and response models for solve jobs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.contracts.observations import (
    FieldPlaneRequest as FieldPlaneRequest,
    FieldPlaneResponseSpec as FieldPlaneResponseSpec,
    FieldPlaneSpec as FieldPlaneSpec,
    PolarConfig as PolarConfig,
)

from server.contracts import DriverSpec as DriverSpec, JobModel as JobModel


from server.contracts.solve_options import (
    GroundPlaneConfig as GroundPlaneConfig,
    MAX_EXPLICIT_FREQUENCIES as MAX_EXPLICIT_FREQUENCIES,
    SolveOptions as SolveOptions,
)
from server.solve_request import (
    ChannelCombineSpec as ChannelCombineSpec,
    ChannelFilterSpec as ChannelFilterSpec,
    DelaySpec as DelaySpec,
    DesignSnapshot as DesignSnapshot,
    DriveChannel as DriveChannel,
    DriverGroup as DriverGroup,
    ExteriorTransducerSpec as ExteriorTransducerSpec,
    FilterFamily as FilterFamily,
    FilterSpec as FilterSpec,
    GainSpec as GainSpec,
    GeometrySource as GeometrySource,
    ImportedGeometrySource as ImportedGeometrySource,
    ImportedMeshSizes as ImportedMeshSizes,
    JobDesignIdentity as JobDesignIdentity,
    LumpedSealedRearChamberSpec as LumpedSealedRearChamberSpec,
    MemberProcessing as MemberProcessing,
    PORT_APERTURE_NAME_GROUPS as PORT_APERTURE_NAME_GROUPS,
    ParametricGeometrySource as ParametricGeometrySource,
    PositiveTransducerScalar as PositiveTransducerScalar,
    SemiInductanceSpec as SemiInductanceSpec,
    SolveRequest as SolveRequest,
    expand_legacy_channels as expand_legacy_channels,
    passive_cardioid_apertures as passive_cardioid_apertures,
)


class SolveOptionsResponse(SolveOptions):
    """Stored options are fully dumped; submission defaults remain optional."""

    engine: str
    accuracy: Literal["fast", "accurate"]
    adaptive_frequency_sampling: bool = False
    solver_mode: Literal["auto", "full_3d", "circsym"]
    symmetry: str
    frequency_range: list[float] | None
    num_frequencies: int | None = Field(ge=1, le=MAX_EXPLICIT_FREQUENCIES)
    frequency_spacing: Literal["log", "linear"]
    frequencies_hz: list[float] | None
    verbose: bool
    mesh_ladder: Literal["off", "auto"]
    mesh_validation_mode: Literal["warn", "strict", "off"]
    polar_config: PolarConfig
    ground_plane: GroundPlaneConfig
    stage_delay_ms: int = Field(ge=0, le=2000)


class DesignAvailability(JobModel):
    """Whether a job's stored design can be reopened, and if not, why not.

    A job that cannot be reopened must say what is wrong in words the user can
    act on, because "Rerun is greyed out" is not a diagnosis.
    """

    reopenable: bool
    source: Literal["v2-snapshot", "cad-import", "none"]
    reason_code: Literal[
        "ok",
        "imported_geometry",
        "no_stored_design",
        "unreadable_design",
    ]
    #: Why this job cannot be reopened. Set exactly when ``reopenable`` is false.
    reason: str | None
    #: A fidelity caveat about the stored design, when there is one.
    note: str | None


class CadApprovalsRequest(JobModel):
    preparation_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)


class CadSolveAgainRequest(JobModel):
    setup_revision_id: str | None = None
    frame_axis: str | None = None
    approvals: CadApprovalsRequest | None = None
    submit: bool = True
    mesh_limit_override: bool = False

    @field_validator("frame_axis")
    @classmethod
    def known_axis(cls, value: str | None) -> str | None:
        from server.cadlink.interpretation.solver_frame import AXES as SOLVER_FRAME_AXES

        if value is not None and value not in SOLVER_FRAME_AXES:
            raise ValueError(f"frame_axis must be one of {', '.join(SOLVER_FRAME_AXES)}")
        return value

    def press(self) -> dict[str, Any]:
        return {
            "setup_revision_id": self.setup_revision_id,
            "frame_axis": self.frame_axis,
            "approve_preparation_id": self.approvals.preparation_id if self.approvals else None,
            "approve_finding_ids": tuple(self.approvals.finding_ids) if self.approvals else (),
            "submit": self.submit,
            "mesh_limit_override": self.mesh_limit_override,
        }


class PrepareRemedy(BaseModel):
    kind: Literal["prepare"] = "prepare"
    engine: str
    disabled_reason: str | None = None


class UseEngineRemedy(BaseModel):
    kind: Literal["use_engine"] = "use_engine"
    engine: str
    label: str
    same_solver: bool


class WaitRemedy(BaseModel):
    kind: Literal["wait"] = "wait"


class SettingsRemedy(BaseModel):
    kind: Literal["settings"] = "settings"
    section: str = "simulation"


SolveRemedy = Annotated[
    PrepareRemedy | UseEngineRemedy | WaitRemedy | SettingsRemedy,
    Field(discriminator="kind"),
]


class SolveBlocker(BaseModel):
    code: str
    message: str
    engine: str
    remedies: list[SolveRemedy] = Field(default_factory=list)


class CadStateSnapshot(JobModel):
    document_name: str | None
    manifest_sha256: str | None
    artifact_sha256: str | None
    project_lineage_id: str | None


class CadStatePreparation(JobModel):
    ingest_id: str | None
    preparation_id: str
    blocking_finding_ids: list[str]
    report_sha256: str | None


class CadStateApproval(JobModel):
    preparation_id: str
    finding_id: str


class CadHoldOption(JobModel):
    id: str
    label: str


class CadMeshLimit(JobModel):
    """A triangle-ceiling hold's facts: what the model meshes to, and WG's limit."""

    triangles: int
    limit: int


class CadState(JobModel):
    """The CAD solve's state, derived entirely from this job's record."""

    operation_id: str | None
    state: str
    stage: str | None
    reason: str | None
    message: str | None
    blocker: SolveBlocker | None = None
    held: bool = False
    #: The preparation ended without a result and without needing a choice:
    #: ``message`` is WG's calm sentence, ``options`` what can still work.
    prepare_failed: bool = False
    #: What this asks of the user (``operations.reason_attention``): a real
    #: ``question``, ``ready`` (prepared; Solve starts it), an ``action`` to
    #: take, WG's own ``failed`` preparation, or null.
    attention: Literal["question", "ready", "action", "failed"] | None = None
    next_step: str | None = None
    options: list[CadHoldOption] = Field(default_factory=list)
    job_id: str | None
    snapshot: CadStateSnapshot | None
    preparation: CadStatePreparation | None
    approvals: list[CadStateApproval]
    setup_defaults: bool
    frame_axis_automatic: str | None
    #: Set only for a request the triangle ceiling holds (``mesh_too_large``).
    mesh_limit: CadMeshLimit | None = None
    received_at: str | None
    updated_at: str | None


class SolveAccepted(JobModel):
    job_id: str
    client_request_id: str | None = None


class StopResponse(JobModel):
    message: str
    status: Literal["cancelled", "cancelling"]


#: ``preparing`` is a CAD solve WG has accepted but not yet bound to a request;
#: its stored config is a ``cad_intent``, not a ``SolveRequest``.
JobStatusName = Literal["preparing", "held", "queued", "running", "complete", "error", "cancelled"]


class CadIdentityInstance(JobModel):
    """CAD-authored addresses for one linked placement in an imported run."""

    instance_id: str
    design_id: str | None = None
    body_object_ids: list[str] = Field(default_factory=list)
    assembly_from_link: list[list[float]]
    source_ids: list[str] = Field(default_factory=list)
    default_drive_channel_ids: list[str] = Field(default_factory=list)


class CadDriveChannelIdentity(JobModel):
    """The submitted channel address resolved against immutable return sources."""

    drive_channel_id: str
    source_ids: list[str]
    instance_ids: list[str] = Field(default_factory=list)


class CadIdentityProvenance(JobModel):
    """Versioned CAD placement/body/source/drive graph retained with a run."""

    schema_version: Literal[1]
    ingest_id: str
    selected_instance_id: str | None = None
    solver_anchor_instance_id: str | None = None
    instances: list[CadIdentityInstance] = Field(default_factory=list)
    drive_channels: list[CadDriveChannelIdentity]


class CadReturnIdentity(JobModel):
    """The immutable return accepted by this request, before and after binding."""

    bundle_path: str
    return_id: str | None


class CadSource(JobModel):
    """Where an imported run came from, kept for the run archive.

    A CAD run used to be traceable only through the ingestion record, which is
    addressed by content and says nothing about which document a person opened.
    """

    ingest_id: str | None
    #: The imported model this run belongs to (``server/jobs/models_api.py``):
    #: ``mesh-file:sha256:<original>`` for a file, the lineage for CAD; None
    #: when it belongs to none.
    model_id: str | None = None
    #: The setup revision this run was solved with (``model_solve.py``); None
    #: for a run that recorded none.
    setup_revision_id: str | None = None
    design_id: str | None
    lineage_id: str | None
    #: The folder this design's runs are archived under -- the name its
    #: ``.wglink`` bundle already owns, so a rename does not start a second one.
    archive_stem: str | None
    manifest_sha256: str | None
    transformed_geometry_hash: str | None = None
    solve_model_sha256: str | None = None
    document_name: str | None
    return_state_hash: str | None
    identity: CadIdentityProvenance | None = None
    #: The domain decision the run was submitted and solved under, as the job
    #: recorded it (``server/cadlink/interpretation/domain_decision.py:decision_summary``):
    #: what was read, the planes solved, any reflection, and the frame. Null
    #: for a run saved before decisions existed.
    domain_decision: dict[str, Any] | None = None


class ChannelSolveExecution(JobModel):
    """Execution identity emitted for each solved channel."""

    accuracy: Literal["fast", "accurate"]
    engine: str
    formulation: str | None


class SolveExecution(ChannelSolveExecution):
    # The runtime adds this key only when a fallback occurred. Avoid inserting
    # a null key into responses for executions that never had a fallback.
    fallback_reason: str | None = Field(default=None, exclude_if=lambda value: value is None)


class JobArchiveIdentity(JobModel):
    # Absent archive identity must not change legacy persisted/echoed bytes.
    archive_key: str | None = Field(default=None, exclude_if=lambda value: value is None)
    archive_folder: str = Field(default="", exclude_if=lambda value: not value)
    archive_run: str = Field(default="", exclude_if=lambda value: not value)
    archived_path: str | None = Field(default=None, exclude_if=lambda value: value is None)


class FrequencyChannel(JobModel):
    #: 1-based: the drive channel the frequency count is within.
    index: int = Field(ge=1)
    count: int = Field(ge=1)


class FrequencyProgress(JobModel):
    """How many of a running solve's frequencies are done (``server/jobs/progress.py``)."""

    done: int = Field(ge=0)
    total: int = Field(ge=1)
    #: Only for an imported multi-channel solve, whose count is channel-local.
    channel: FrequencyChannel | None = Field(default=None, exclude_if=lambda value: value is None)


class RunProvenance(BaseModel):
    """Extensible provenance shared by a run and its result."""

    model_config = ConfigDict(extra="allow")

    mesher_version: str | None = Field(default=None, exclude_if=lambda value: value is None)
    geometry_hash: str | None = Field(default=None, exclude_if=lambda value: value is None)
    mesher_source: str | None = Field(default=None, exclude_if=lambda value: value is None)


class JobItem(JobArchiveIdentity, JobDesignIdentity):
    id: str
    client_request_id: str | None = None
    client_metadata: dict[str, Any]
    #: Null for a CAD solve that is still being prepared or ended refused: a
    #: run number names a run, and it is assigned when the solve is queued.
    run_number: int | None
    parent_job_id: str | None
    status: JobStatusName
    progress: float
    stage: str | None
    stage_message: str | None
    #: The frequency count of a running solve, kept through its setup chatter.
    #: Absent when the engine reports none (an indeterminate display) and for
    #: every job that is not solving, so other jobs' response bytes are unchanged.
    frequency_progress: FrequencyProgress | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    created_at: str
    queued_at: str
    started_at: str | None
    completed_at: str | None
    config_summary: dict[str, Any]
    solve_options: SolveOptionsResponse
    solve_accuracy: Literal["fast", "accurate"] = "fast"
    solve_execution: SolveExecution | None
    channel_solve_executions: dict[str, ChannelSolveExecution]
    has_results: bool
    has_mesh_artifact: bool
    has_pressure_basis_artifact: bool = False
    pressure_basis_artifact_bytes: int | None = None
    # A completed job could claim cardioid success and then 404 on the
    # download, because a storage failure was only a server log line. These
    # three carry the artifact's real state to the client: whether it exists,
    # how big it is, and anything that went wrong persisting it.
    has_radiation_impedance_artifact: bool = False
    radiation_impedance_artifact_bytes: int | None = None
    persistence_warnings: list[str] = Field(default_factory=list)
    # Field-plane readiness distinguishes retained traces from jobs that must
    # be re-solved or use a solve mode unsupported by post-solve evaluation.
    # Preserve legacy job response bytes when no FEM field is advertised.
    has_fem_field_artifact: bool = Field(default=False, exclude_if=lambda value: not value)
    field_plane_available: bool = False
    field_trace_bytes: int | None = None
    unavailable_reason: str | None = None
    label: str | None
    error_message: str | None
    cancellation_requested: bool
    mesh_stats: dict[str, Any] | None
    script_snapshot: dict[str, Any] | None
    #: Shared run provenance. Older jobs have no recorded mesher evidence.
    provenance: RunProvenance | None = Field(default=None, exclude_if=lambda value: value is None)
    design_availability: DesignAvailability
    design_revision: int
    polar_grid: dict[str, Any]
    rating: int | None
    exported_files: list[str]
    auto_export_completed_at: str | None
    auto_export_formats: dict[str, Any]
    #: When this run was written to the run archive. The job database can lose
    #: results (removal, or the optional 30-day cleanup); the archive survives.
    archived_at: str | None = None
    raw_results_file: str | None
    mesh_artifact_file: str | None
    results_discarded_at: str | None = None
    mesh_discarded_at: str | None = None
    log_tail: list[str]
    symmetry: dict[str, Any] = Field(default_factory=dict)
    solve_path: Literal["full-3d", "axisymmetric-meridian", "interior-fem", "coupled-fem-bem"] | None = None
    axisymmetric_eligibility_reasons: list[str] = Field(default_factory=list)
    solve_wall_time_seconds: float | None = None
    cad_source: CadSource | None = None
    #: The exact imported-geometry request persisted with a CAD run. This is
    #: deliberately a JSON object rather than today's ImportedGeometrySource:
    #: old rows must remain inspectable even if their accepted wire predates a
    #: later additive field or stricter validation rule.
    cad_setup: dict[str, Any] | None = None
    #: What the CAD operation that made this run resolved, kept on the job
    #: (``task_metadata.cad``): ``operation_id``, ``setup`` (``revision_id``,
    #: ``digest``, ``origin`` = ``wg_defaults`` or ``user``), ``frame`` (``axis``,
    #: ``provenance``, ``confirmed``, ``requirement``) and ``preparation``. Null
    #: for a run made without one.
    cad_provenance: dict[str, Any] | None = None
    #: State and preparation read from this job alone, without its delivery ledger.
    cad_state: CadState | None = None
    #: The CAD intent a ``preparing`` job holds instead of a request (``type`` is
    #: ``cad_intent``, with the operation, return and setup it was accepted
    #: with). Null for every job that has a request.
    cad_intent: dict[str, Any] | None = None
    #: Accepted return identity, from the intent before binding and
    #: ``task_metadata.cad.return_identity`` afterward. Absent when no identity
    #: was retained, preserving historical response bytes. A nullable return id
    #: still names an older delivery whose bundle path is known.
    cad_return: CadReturnIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class JobStatusResponse(JobItem):
    updated_at: str
    message: str | None = None


MeshFreshness = Literal["current", "stale", "unknown"]


class MeshFreshnessResponse(JobModel):
    freshness: MeshFreshness


class JobListResponse(JobModel):
    items: list[JobItem]
    total: int
    limit: int
    offset: int


class MetadataResponse(JobModel):
    status: Literal["ok"]


class ClearFailedResponse(JobModel):
    deleted: bool
    deleted_count: int
    deleted_ids: list[str]


class DeleteResponse(JobModel):
    deleted: Literal[True]
    job_id: str


class JobMetadataPatch(JobModel):
    """V1 task metadata retained by ``server/api/routes_simulation.py:253-277``."""

    label: str | None = None
    script_snapshot: dict[str, Any] | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    exported_files: list[str] | None = None
    auto_export_completed_at: str | None = None
    auto_export_formats: dict[str, Any] | None = None
    archived_at: str | None = None
    raw_results_file: str | None = None
    mesh_artifact_file: str | None = None

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("exported_files")
    @classmethod
    def normalize_exported_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator(
        "auto_export_completed_at", "archived_at", "raw_results_file", "mesh_artifact_file"
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


__all__ = [
    "CadApprovalsRequest",
    "CadSolveAgainRequest",
    "CadState",
    "CadStateApproval",
    "CadStatePreparation",
    "CadStateSnapshot",
    "ClearFailedResponse",
    "DeleteResponse",
    "DesignAvailability",
    "DesignSnapshot",
    "DriveChannel",
    "FieldPlaneRequest",
    "FieldPlaneResponseSpec",
    "FieldPlaneSpec",
    "ImportedGeometrySource",
    "ImportedMeshSizes",
    "FrequencyChannel",
    "FrequencyProgress",
    "JobItem",
    "JobListResponse",
    "JobMetadataPatch",
    "JobStatusResponse",
    "MetadataResponse",
    "PolarConfig",
    "ParametricGeometrySource",
    "SolveAccepted",
    "SolveOptions",
    "SolveRequest",
    "StopResponse",
]
