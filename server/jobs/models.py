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
        effective_step = (end - start) / (count - 1)
        if self.angle_step is not None:
            if not math.isfinite(self.angle_step):
                raise ValueError("polar_config.angle_step must be finite")
            if not math.isclose(self.angle_step, effective_step, rel_tol=1.0e-7, abs_tol=1.0e-9):
                raise ValueError(
                    "polar_config.angle_step does not match angle_range sample count"
                )
        else:
            object.__setattr__(self, "angle_step", effective_step)
        return self


class SolveOptions(JobModel):
    """Execution choices kept separate from the authoritative v2 design."""

    engine: str = "auto"
    frequency_range: list[float] | None = None
    num_frequencies: int | None = Field(default=None, ge=1, le=401)
    frequency_spacing: Literal["log", "linear"] = "log"
    verbose: bool = False
    mesh_validation_mode: Literal["warn", "strict", "off"] = "warn"
    polar_config: PolarConfig = Field(default_factory=PolarConfig)
    stage_delay_ms: int = Field(default=30, ge=0, le=2000)

    @field_validator("engine")
    @classmethod
    def normalize_engine(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("engine must not be empty")
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


class SolveRequest(JobModel):
    design: DesignConfig
    options: SolveOptions = Field(default_factory=SolveOptions)


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
    rating: int | None = None
    exported_files: list[str]
    auto_export_completed_at: str | None = None
    raw_results_file: str | None = None
    mesh_artifact_file: str | None = None
    log_tail: list[str]


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
