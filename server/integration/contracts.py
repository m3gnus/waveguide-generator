"""Small, versioned documents intended for generated and non-Python clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ERROR_CONTRACT_VERSION = 1
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
    "ErrorDetail",
    "ErrorEnvelope",
    "error_envelope",
]
