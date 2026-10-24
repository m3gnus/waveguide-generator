"""Immutable simulation setups shared by prepared mesh producers.

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
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.jobs.models import SolveRequest
from server.cadlink.snapshot_mesh import snapshot_mesh

from server.contracts.canonical import canonical_utf8_strict_json as canonical_json


SETUP_SCHEMA_VERSION = 1
#: ``CadSolveSetup.origin`` of a setup WG made from its default settings
#: (``default_setup``), rather than one a person chose.
DEFAULTS_ORIGIN = "wg_defaults"
# What the snapshot contributes to a submission, and so what a setup must not.
_SNAPSHOT_FIELDS = frozenset(
    {"type", "ingest_id", "manifest_sha256", "artifact_sha256", "acknowledged_findings"}
)
# A well-formed identity that names no real snapshot, used only to validate a
# setup as a submission before any snapshot is bound to it.
_PLACEHOLDER_INGEST_ID = "wgi_" + "0" * 26
_PLACEHOLDER_SHA256 = "sha256:" + "0" * 64


def is_default_setup(store: Any, revision_id: str | None) -> bool:
    """Whether a saved revision was made from WG defaults.

    Missing or malformed legacy content is not classified as defaults, so it
    cannot cause a user's saved settings to be refreshed.
    """

    row = store.get_setup_revision(revision_id) if revision_id else None
    try:
        return row is not None and json.loads(row["setup_json"]).get("origin") == DEFAULTS_ORIGIN
    except (TypeError, ValueError, AttributeError):
        return False


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
    #: ``DEFAULTS_ORIGIN`` when WG made this setup from its default settings
    #: for a model nobody chose settings for; None for a person's own. Left
    #: out of the content when None, so every setup recorded before it keeps
    #: its revision id.
    origin: Literal["wg_defaults"] | None = None


def solve_request_for(
    setup: CadSolveSetup,
    *,
    ingest_id: str,
    manifest_sha256: str,
    artifact_sha256: str,
    acknowledged_findings: list[str],
    client_request_id: str | None = None,
    label: str | None = None,
    fem_region_ids: list[str] | None = None,
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
    excluded_ids = geometry.pop("exterior_only_region_ids", None)
    if fem_region_ids and (
        not isinstance(excluded_ids, list) or sorted(excluded_ids) != sorted(fem_region_ids)
    ):
        # A project setup follows sources across snapshots; a FEM exclusion does not.
        geometry["exterior_only"] = False
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
    excluded = setup.geometry.get("exterior_only_region_ids")
    if excluded is not None and (
        not isinstance(excluded, list)
        or any(not isinstance(item, str) or not item for item in excluded)
        or len(set(excluded)) != len(excluded)
    ):
        raise ValueError("exterior_only_region_ids must be distinct nonempty region ids")
    overlap = sorted(_SNAPSHOT_FIELDS & set(setup.geometry))
    if overlap:
        raise ValueError(
            "a setup revision names no snapshot; it must not carry " + ", ".join(overlap)
        )
    unknown_references = sorted(
        set(setup.driver_references)
        - {
            str(channel.get("id"))
            for channel in setup.geometry.get("drive_channels", [])
            if isinstance(channel, Mapping)
        }
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


def _fem_region_ids(record: Mapping[str, Any]) -> list[str]:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), Mapping) else {}
    regions = evidence.get("fem_regions") or record.get("fem_regions") or []
    return sorted(
        str(region["region_id"]) for region in regions
        if isinstance(region, Mapping) and isinstance(region.get("region_id"), str)
    )


def snapshot_setup(setup: CadSolveSetup, manifest: Mapping[str, Any]) -> CadSolveSetup:
    """The effective setup for a new solve; legacy setups keep their identity."""

    mesh = snapshot_mesh(manifest, skipped_source_ids=setup.geometry.get("skipped_source_ids") or ())
    if mesh is None:
        return setup
    return validate_setup({
        **json.loads(setup_content(setup)),
        "geometry": {**setup.geometry, "mesh": mesh.sizes},
        "preparation": {
            **setup.preparation.model_dump(mode="json"), "symmetry_mode": mesh.symmetry_mode,
        },
    })


def setup_of_request(
    request: Mapping[str, Any], record: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """The setup an exact imported request (its JSON) is, and whether it is exact.

    Everything but what the prepared mesh contributes. A request carries no
    FEM exclusion (``exterior_only_region_ids`` is a setup field): an admitted
    exterior-only request excluded exactly its snapshot's FEM regions, which
    the prepared record states, so they are recovered from it. Without the
    record they cannot be, and a solve of the setup would include the FEM air
    again: the setup is then not exact. Shared by a reopened run's derived
    setup (``run_workspace.py``) and the revision an exact request records
    (``model_solve._request_revision``).
    """

    geometry = dict(request.get("geometry") or {})
    for field in _SNAPSHOT_FIELDS:
        geometry.pop(field, None)
    exact = True
    if geometry.get("exterior_only") is True and "exterior_only_region_ids" not in geometry:
        if record is None:
            exact = False
        elif regions := _fem_region_ids(record):
            geometry["exterior_only_region_ids"] = regions
    return {"geometry": geometry, "options": dict(request.get("options") or {})}, exact


def setup_content(setup: CadSolveSetup) -> str:
    """The canonical JSON a revision stores and is identified by."""

    content = setup.model_dump(mode="json")
    if content.get("origin") is None:
        content.pop("origin", None)
    mesh = content["geometry"].get("mesh")
    if isinstance(mesh, dict) and mesh.get("surface_groups") is None:
        mesh.pop("surface_groups", None)
    return canonical_json(content)


def setup_digest(setup: CadSolveSetup) -> str:
    return "sha256:" + hashlib.sha256(setup_content(setup).encode("utf-8")).hexdigest()


__all__ = [
    "CadSolveSetup",
    "DEFAULTS_ORIGIN",
    "DriverReference",
    "PreparationOptions",
    "SETUP_SCHEMA_VERSION",
    "setup_content",
    "setup_digest",
    "setup_of_request",
    "snapshot_setup",
    "solve_request_for",
    "validate_setup",
]

# The historical class name remains the wire-schema name for installed clients.
ImportedSolveSetup = CadSolveSetup
