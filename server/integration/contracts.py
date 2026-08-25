"""Small, versioned documents intended for generated and non-Python clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ERROR_CONTRACT_VERSION = 1
# Deliberately still 1 after ``installed_dependency_shas``/``dependency_drift``
# were added. Both are optional additions to an ``extra="allow"`` document, and
# their *presence* already distinguishes a producer that measured the installed
# stack from one that never did -- which is the only thing a version bump would
# have bought. Bumping is not free here: ``ResultProvenance.schema_version`` is
# ``Literal[1]``, the frontend envelope guard tests ``schema_version === 1``,
# and the published OpenAPI snapshot declares ``"const": 1``, so a 2 would make
# every already-shipped client refuse every new result.
PROVENANCE_CONTRACT_VERSION = 1


class IntegrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(IntegrationModel):
    """Stable refusal/failure detail while the legacy ``detail`` string remains."""

    schema_version: Literal[1] = ERROR_CONTRACT_VERSION
    code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None


class ErrorEnvelope(IntegrationModel):
    """Backward-compatible HTTP/CLI error body."""

    detail: str
    error: ErrorDetail


class ArtifactDigest(IntegrationModel):
    """Exact file bytes plus an optional canonical JSON-object identity."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class CliOutcome(IntegrationModel):
    """Terminal NDJSON record emitted after the jobs-protocol stream."""

    kind: Literal["outcome"] = "outcome"
    schema_version: Literal[1] = 1
    status: Literal[
        "complete",
        "refused",
        "failed",
        "cancelled",
        "interrupted",
    ]
    job_id: str | None = None
    client_request_id: str | None = None
    output_directory: str | None = None
    result_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    artifacts: dict[str, ArtifactDigest] | None = None
    error: ErrorDetail | None = None


def error_envelope(
    *,
    code: str,
    stage: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    return ErrorEnvelope(
        detail=message,
        error=ErrorDetail(
            code=code,
            stage=stage,
            message=message,
            retryable=retryable,
            details=details or {},
            client_request_id=client_request_id,
        ),
    ).model_dump(mode="json")


__all__ = [
    "ERROR_CONTRACT_VERSION",
    "PROVENANCE_CONTRACT_VERSION",
    "ArtifactDigest",
    "ErrorDetail",
    "ErrorEnvelope",
    "CliOutcome",
    "error_envelope",
]
