"""Generated-client-friendly request and response models for solve jobs."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.design.schema import DesignConfig


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SolveOptions(JobModel):
    """Execution choices kept separate from the authoritative v2 design."""

    engine: str = "dryrun"
    frequency_range: list[float] | None = None
    num_frequencies: int | None = Field(default=None, ge=1, le=401)
    frequency_spacing: Literal["log", "linear"] = "log"
    verbose: bool = False
    mesh_validation_mode: Literal["warn", "strict", "off"] = "warn"
    stage_delay_ms: int = Field(default=30, ge=0, le=2000)

    @field_validator("engine")
    @classmethod
    def normalize_engine(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("engine must not be empty")
        return normalized

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
    "SolveAccepted",
    "SolveOptions",
    "SolveRequest",
    "StopResponse",
]
