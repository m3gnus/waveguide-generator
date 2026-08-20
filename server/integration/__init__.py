"""Language-neutral integration contracts shared by HTTP and headless clients."""

from .contracts import (
    ERROR_CONTRACT_VERSION,
    PROVENANCE_CONTRACT_VERSION,
    ErrorDetail,
    ErrorEnvelope,
    error_envelope,
)
from .provenance import enrich_result_contract


def mount_integration(application):
    # Keep package import free of FastAPI/design-schema work for clients that
    # only need the lightweight error or provenance helpers.
    from .api import mount_integration as mount

    return mount(application)

__all__ = [
    "ERROR_CONTRACT_VERSION",
    "PROVENANCE_CONTRACT_VERSION",
    "ErrorDetail",
    "ErrorEnvelope",
    "enrich_result_contract",
    "error_envelope",
    "mount_integration",
]
