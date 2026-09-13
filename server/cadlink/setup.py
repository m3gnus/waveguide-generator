"""Immutable simulation setup revisions for CAD solves.

A setup revision is everything a solve of one CAD snapshot needs except the
snapshot itself (docs/architecture/CAD-OPERATIONS.md, "Setup revisions"): the
drive channels with resolved driver numbers, the voltages, mesh sizes, skipped
sources, exterior-only, the combine and passive-cardioid settings, the ingest
preparation options, and the solve options. Which library driver a channel's
numbers came from is kept beside them for the record; the numbers are what a
retry reproduces, because the driver library has no revisions.

A revision is immutable: its id names its content, and identical content is one
revision. It is bound to an operation at the binding point and never changes
afterwards; a later change is a new revision, and, once bound, a new operation.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.jobs.models import SolveRequest

from .operations import canonical_json


SETUP_SCHEMA_VERSION = 1
# What the snapshot contributes to a submission, and so what a setup must not.
_SNAPSHOT_FIELDS = frozenset(
    {"type", "ingest_id", "manifest_sha256", "artifact_sha256", "acknowledged_findings"}
)
# A well-formed identity that names no real snapshot, used only to validate a
# setup as a submission before any snapshot is bound to it.
_PLACEHOLDER_INGEST_ID = "wgi_" + "0" * 26
_PLACEHOLDER_SHA256 = "sha256:" + "0" * 64


class DriverReference(BaseModel):
    """Where a channel's resolved driver numbers came from, for the record."""

    model_config = ConfigDict(extra="forbid")

    driver_id: str = Field(min_length=1)
    source: str | None = None


class PreparationOptions(BaseModel):
    """The ingest options a preparation meshes the snapshot with."""

    model_config = ConfigDict(extra="forbid")

    area_drift_overrides: list[str] = Field(default_factory=list)
    symmetry_mode: Literal["auto", "full"] = "auto"
    surface_deviation_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class CadSolveSetup(BaseModel):
    """One setup revision's content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SETUP_SCHEMA_VERSION
    #: ``ImportedGeometrySource`` fields other than the snapshot's own.
    geometry: dict[str, Any]
    #: ``SolveOptions`` fields.
    options: dict[str, Any] = Field(default_factory=dict)
    preparation: PreparationOptions = Field(default_factory=PreparationOptions)
    #: Drive channel id -> the library driver its numbers were resolved from.
    driver_references: dict[str, DriverReference] = Field(default_factory=dict)
    label: str | None = None


def solve_request_for(
    setup: CadSolveSetup,
    *,
    ingest_id: str,
    manifest_sha256: str,
    artifact_sha256: str,
    acknowledged_findings: list[str],
    client_request_id: str | None = None,
    label: str | None = None,
) -> SolveRequest:
    """The exact solve request a setup makes of one prepared snapshot."""

    geometry = {
        **setup.geometry,
        "type": "imported",
        "ingest_id": ingest_id,
        "manifest_sha256": manifest_sha256,
        "artifact_sha256": artifact_sha256,
        "acknowledged_findings": list(acknowledged_findings),
    }
    return SolveRequest.model_validate(
        {
            "geometry": geometry,
            "options": dict(setup.options),
            "label": label if label is not None else setup.label,
            "client_request_id": client_request_id,
        }
    )


def validate_setup(value: Mapping[str, Any]) -> CadSolveSetup:
    """Parse a setup and prove it is a valid submission once a snapshot is bound."""

    setup = CadSolveSetup.model_validate(value)
    overlap = sorted(_SNAPSHOT_FIELDS & set(setup.geometry))
    if overlap:
        raise ValueError(
            "a setup revision names no snapshot; it must not carry "
            + ", ".join(overlap)
        )
    unknown_references = sorted(
        set(setup.driver_references)
        - {str(channel.get("id")) for channel in setup.geometry.get("drive_channels", [])
           if isinstance(channel, Mapping)}
    )
    if unknown_references:
        raise ValueError(
            "driver_references name unknown drive channels: " + ", ".join(unknown_references)
        )
    solve_request_for(
        setup,
        ingest_id=_PLACEHOLDER_INGEST_ID,
        manifest_sha256=_PLACEHOLDER_SHA256,
        artifact_sha256=_PLACEHOLDER_SHA256,
        acknowledged_findings=[],
    )
    return setup


def setup_content(setup: CadSolveSetup) -> str:
    """The canonical JSON a revision stores and is identified by."""

    return canonical_json(setup.model_dump(mode="json"))


def setup_digest(setup: CadSolveSetup) -> str:
    return "sha256:" + hashlib.sha256(setup_content(setup).encode("utf-8")).hexdigest()


__all__ = [
    "CadSolveSetup",
    "DriverReference",
    "PreparationOptions",
    "SETUP_SCHEMA_VERSION",
    "setup_content",
    "setup_digest",
    "solve_request_for",
    "validate_setup",
]
