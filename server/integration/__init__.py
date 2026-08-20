"""Language-neutral integration contracts shared by HTTP and headless clients."""

from .contracts import (
    ERROR_CONTRACT_VERSION,
    PROVENANCE_CONTRACT_VERSION,
    ErrorDetail,
    ErrorEnvelope,
    error_envelope,
)
from .provenance import enrich_result_contract

__all__ = [
    "ERROR_CONTRACT_VERSION",
    "PROVENANCE_CONTRACT_VERSION",
    "ErrorDetail",
    "ErrorEnvelope",
    "enrich_result_contract",
    "error_envelope",
]
