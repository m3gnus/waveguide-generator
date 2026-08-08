"""Generated-client-friendly request and response models for solve jobs."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.design.schema import DesignConfig


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolarConfig(JobModel):
    """Directivity observation contract shared by every solve engine."""

    angle_range: tuple[float, float, int] = (0.0, 180.0, 37)
    # The three-value range is authoritative. angle_step retains the UI's
    # requested step so non-divisible spans remain observable in metadata.
    angle_step: float | None = Field(default=None, gt=0)
    distance: float = Field(default=2.0, ge=0.1)
    norm_angle: float = 10.0
    inclination: float = 45.0
    enabled_axes: list[Literal["horizontal", "vertical", "diagonal"]] = Field(
        default_factory=lambda: ["horizontal", "vertical", "diagonal"],
        min_length=1,
    )
    observation_origin: Literal["mouth", "throat"] = "mouth"
    spherical_sampling: bool = False
    spherical_theta_count: int = Field(default=37, ge=5, le=121)
    spherical_phi_count: int = Field(default=72, ge=8, le=241)

    @model_validator(mode="before")
    @classmethod
    def convert_angle_step(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        raw_range = result.get("angle_range")
        raw_step = result.get("angle_step")
        if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
            if raw_step is None:
                raise ValueError(
                    "polar_config.angle_step is required when angle_range contains only start/end"
                )
            try:
                start, end, step = float(raw_range[0]), float(raw_range[1]), float(raw_step)
                count = int(round((end - start) / step)) + 1
            except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
                raise ValueError("polar_config angle range/step must be finite numbers") from exc
            result["angle_range"] = (start, end, count)
        return result

    @field_validator("enabled_axes", mode="before")
    @classmethod
    def normalize_enabled_axes(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            return value
        return list(dict.fromkeys(str(axis).strip().lower() for axis in value))

    @field_validator("observation_origin", mode="before")
    @classmethod
    def normalize_observation_origin(cls, value: Any) -> Any:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_polar_domain(self) -> "PolarConfig":
        start, end, count = self.angle_range
        finite_values = (start, end, self.distance, self.norm_angle, self.inclination)
        if not all(math.isfinite(value) for value in finite_values):
            raise ValueError("polar_config numeric values must be finite")
        if end <= start:
            raise ValueError("polar_config.angle_range must be increasing")
        if not 2 <= count <= 721:
            raise ValueError("polar_config angle sample count must be between 2 and 721")
        if self.angle_step is not None:
            if not math.isfinite(self.angle_step):
                raise ValueError("polar_config.angle_step must be finite")
        return self

    def resolved_grid(self) -> dict[str, float | int | None]:
        start, end, count = self.angle_range
        return {
            "start": float(start),
            "end": float(end),
            "count": int(count),
            "requested_step": (
                float(self.angle_step) if self.angle_step is not None else None
            ),
            "resolved_step": float((end - start) / (count - 1)),
        }


class SolveOptions(JobModel):
    """Execution choices kept separate from the authoritative v2 design."""

    engine: str = "auto"
    symmetry: str = "auto"
    frequency_range: list[float] | None = None
    num_frequencies: int | None = Field(default=None, ge=1, le=401)
    frequency_spacing: Literal["log", "linear"] = "log"
    # Explicit sweep points, solved verbatim instead of a generated grid. The
    # BEM cost per point is flat (same-size matrix at every frequency), so this
    # is about *where* the points land, not about spending fewer of them.
    frequencies_hz: list[float] | None = None
    verbose: bool = False
    mesh_validation_mode: Literal["warn", "strict", "off"] = "warn"
    polar_config: PolarConfig = Field(default_factory=PolarConfig)
    stage_delay_ms: int = Field(default=30, ge=0, le=2000)

    @field_validator("engine", "symmetry")
    @classmethod
    def normalize_named_option(cls, value: str, info: Any) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must not be empty")
        if info.field_name == "symmetry" and normalized not in {
            "auto",
            "full",
            "half_xz",
            "half_yz",
            "quarter",
        }:
            raise ValueError(
                "symmetry must be one of auto, full, half_xz, half_yz, or quarter"
            )
        return normalized

    @field_validator("frequency_spacing", "mesh_validation_mode", mode="before")
    @classmethod
    def normalize_option_enum(cls, value: Any) -> Any:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_frequency_range(self) -> "SolveOptions":
        if self.frequency_range is None:
            return self
        if len(self.frequency_range) != 2:
            raise ValueError("frequency_range must contain [start_hz, end_hz]")
        start, end = self.frequency_range
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("frequency_range values must be finite")
        if start <= 0 or end <= start:
            raise ValueError("frequency_range must be positive and increasing")
        return self

    @model_validator(mode="after")
    def validate_explicit_frequencies(self) -> "SolveOptions":
        if self.frequencies_hz is None:
            return self
        # Refuse rather than silently pick a winner: a caller that sends both a
        # list and a grid has two different sweeps in mind, and quietly dropping
        # one is exactly the class of silent no-op this codebase keeps paying for.
        conflicts = [
            name
            for name, value in (
                ("frequency_range", self.frequency_range),
                ("num_frequencies", self.num_frequencies),
            )
            if value is not None
        ]
        if conflicts:
            raise ValueError(
                "frequencies_hz replaces the generated grid and cannot be combined "
                f"with {' or '.join(conflicts)}"
            )
        if not self.frequencies_hz:
            raise ValueError("frequencies_hz must contain at least one frequency")
        if len(self.frequencies_hz) > 401:
            raise ValueError("frequencies_hz must contain at most 401 frequencies")
        if not all(math.isfinite(value) for value in self.frequencies_hz):
            raise ValueError("frequencies_hz values must be finite")
        if any(value <= 0 for value in self.frequencies_hz):
            raise ValueError("frequencies_hz values must be positive")
        # Ascending order is a result-contract requirement, not solver taste: the
        # frequency axis is emitted verbatim and every chart and exporter reads it
        # as monotonic. Sorting silently would hide a mistyped list.
        if any(
            later <= earlier
            for earlier, later in zip(self.frequencies_hz, self.frequencies_hz[1:])
        ):
            raise ValueError(
                "frequencies_hz must be strictly ascending with no duplicates"
            )
        return self


class DesignSnapshot(JobModel):
    version: Literal[1] = 1
    design: DesignConfig


class DesignAvailability(JobModel):
    """Whether a job's stored design can be reopened, and if not, why not.

    Jobs imported from v1 are the reason this exists. Most of them are
    recovered into v2's own snapshot shape and are indistinguishable from a
    natively solved job; the rest must say what is wrong in words the user can
    act on, because "Rerun is greyed out" is not a diagnosis.
    """

    reopenable: bool = True
    source: Literal[
        "v2-snapshot", "v1-design-state", "v1-mesher-payload", "none"
    ] = "v2-snapshot"
    reason_code: Literal[
        "ok", "recovered", "freeform_legacy_design", "no_stored_design", "unreadable_design"
    ] = "ok"
    #: Why this job cannot be reopened. Set exactly when ``reopenable`` is false.
    reason: str | None = None
    #: A fidelity caveat about a design that *was* recovered.
    note: str | None = None


class SolveRequest(JobModel):
    design: DesignConfig
    options: SolveOptions = Field(default_factory=SolveOptions)
    label: str | None = None
    design_revision: int = Field(default=0, ge=0)
    design_snapshot: DesignSnapshot | None = None

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_design_sweep_controls(self) -> "SolveRequest":
        """Reject design sweep values that would otherwise silently default."""

        root = self.design.root
        simulation = root.simulation

        def scalar(expr: Any, default: float, field: str) -> float:
            if expr is None:
                return default
            number = expr.constant_value()
            if number is None:
                raise ValueError(f"design.simulation.{field} must be a scalar number")
            return float(number)

        if (
            self.options.frequencies_hz is None
            and self.options.frequency_range is None
        ):
            start = scalar(simulation.f1, 200.0, "f1")
            end = scalar(simulation.f2, 20_000.0, "f2")
            if start <= 0.0 or end <= start:
                raise ValueError(
                    "design simulation frequency bounds must be positive and increasing"
                )

        if self.options.frequencies_hz is None and self.options.num_frequencies is None:
            count = scalar(simulation.num_frequencies, 24.0, "num_frequencies")
            if not count.is_integer() or not 1 <= int(count) <= 401:
                raise ValueError(
                    "design.simulation.num_frequencies must be an integer from 1 to 401"
                )

        structural_controls = [
            (root.scale, "design.scale"),
            (root.mesh.quadrants, "design.mesh.quadrants"),
            (root.mesh.wall_thickness, "design.mesh.wall_thickness"),
            (root.mesh.max_edge, "design.mesh.max_edge"),
            (root.source.shape, "design.source.shape"),
            (
                root.enclosure.depth if root.enclosure is not None else None,
                "design.enclosure.depth",
            ),
        ]
        if root.source.velocity_convention in {None, "legacy"}:
            structural_controls.append(
                (root.source.velocity, "design.source.velocity")
            )
        for expression, field in structural_controls:
            if expression is not None and expression.constant_value() is None:
                raise ValueError(f"{field} must be a scalar number")
        return self

    @model_validator(mode="after")
    def validate_snapshot_matches_design(self) -> "SolveRequest":
        if self.design_snapshot is None:
            # Backward-compatible API callers still become atomic records: the
            # server versions the already-validated canonical design wire.
            object.__setattr__(
                self,
                "design_snapshot",
                DesignSnapshot(design=self.design.model_copy(deep=True)),
            )
        elif self.design_snapshot.design != self.design:
            raise ValueError("design_snapshot.design must match design")
        return self


class SolveAccepted(JobModel):
    job_id: str


class StopResponse(JobModel):
    message: str
    status: Literal["cancelled", "cancelling"]


JobStatusName = Literal["queued", "running", "complete", "error", "cancelled"]


class JobItem(JobModel):
    id: str
    status: JobStatusName
    progress: float
    stage: str | None = None
    stage_message: str | None = None
    created_at: str
    queued_at: str
    started_at: str | None = None
    completed_at: str | None = None
    config_summary: dict[str, Any]
    has_results: bool
    has_mesh_artifact: bool
    label: str | None = None
    error_message: str | None = None
    cancellation_requested: bool
    mesh_stats: dict[str, Any] | None = None
    script_snapshot: dict[str, Any] | None = None
    design_availability: DesignAvailability = Field(default_factory=DesignAvailability)
    design_revision: int
    polar_grid: dict[str, Any]
    rating: int | None = None
    exported_files: list[str]
    auto_export_completed_at: str | None = None
    auto_export_formats: dict[str, Any]
    raw_results_file: str | None = None
    mesh_artifact_file: str | None = None
    log_tail: list[str]
    symmetry: dict[str, Any] = Field(default_factory=dict)
    solve_path: Literal["full-3d", "axisymmetric-meridian"] | None = None
    axisymmetric_eligibility_reasons: list[str] = Field(default_factory=list)
    solve_wall_time_seconds: float | None = None


class JobStatusResponse(JobItem):
    updated_at: str
    message: str | None = None


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

    @field_validator("auto_export_completed_at", "raw_results_file", "mesh_artifact_file")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


__all__ = [
    "ClearFailedResponse",
    "DeleteResponse",
    "DesignAvailability",
    "DesignSnapshot",
    "JobItem",
    "JobListResponse",
    "JobMetadataPatch",
    "JobStatusResponse",
    "MetadataResponse",
    "PolarConfig",
    "SolveAccepted",
    "SolveOptions",
    "SolveRequest",
    "StopResponse",
]
