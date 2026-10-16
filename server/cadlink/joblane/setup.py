"""Compatibility imports for existing CAD adapters and installed setup schemas.

The setup contract is owned by jobs.imported_setup for all prepared meshes.
"""

from server.jobs.imported_setup import (
    CadSolveSetup,
    DEFAULTS_ORIGIN,
    DriverReference,
    ImportedSolveSetup,
    PreparationOptions,
    SETUP_SCHEMA_VERSION,
    setup_content,
    setup_digest,
    solve_request_for,
    validate_setup,
)

__all__ = [
    "CadSolveSetup",
    "DEFAULTS_ORIGIN",
    "DriverReference",
    "ImportedSolveSetup",
    "PreparationOptions",
    "SETUP_SCHEMA_VERSION",
    "setup_content",
    "setup_digest",
    "solve_request_for",
    "validate_setup",
]
