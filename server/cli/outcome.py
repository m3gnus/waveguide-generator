"""Stable terminal records for headless NDJSON consumers."""

from __future__ import annotations

import json
from typing import Any, TextIO

from server.integration.contracts import CliOutcome, ErrorDetail


def write_outcome(
    stream: TextIO,
    *,
    status: str,
    job_id: str | None = None,
    client_request_id: str | None = None,
    output_directory: str | None = None,
    result_sha256: str | None = None,
    error_code: str | None = None,
    error_stage: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
    error_details: dict[str, Any] | None = None,
) -> None:
    error = None
    if error_code is not None:
        error = ErrorDetail(
            code=error_code,
            stage=error_stage or "unknown",
            message=error_message or error_code,
            retryable=retryable,
            details=error_details or {},
            client_request_id=client_request_id,
        )
    document = CliOutcome(
        status=status,
        job_id=job_id,
        client_request_id=client_request_id,
        output_directory=output_directory,
        result_sha256=result_sha256,
        error=error,
    )
    print(
        json.dumps(
            document.model_dump(mode="json", exclude_none=True),
            separators=(",", ":"),
            allow_nan=False,
        ),
        file=stream,
        flush=True,
    )


__all__ = ["write_outcome"]
