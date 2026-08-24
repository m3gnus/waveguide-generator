"""Versioned final-result envelopes shared by runtime and API boundaries."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from server.jobs.models import CadIdentityProvenance


class ExtensibleResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ResultProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1]
    wg_version: str
    dependency_shas: dict[str, str]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_identity: Literal["execution"]
    execution_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_engine: str
    cad_identity: CadIdentityProvenance | None = None


class ParametricResultEnvelope(ExtensibleResultModel):
    result_kind: Literal["parametric"]
    result_contract_version: Literal[1]
    client_request_id: str | None
    client_metadata: dict[str, JsonValue]
    provenance: ResultProvenance
    metadata: dict[str, Any]


class MultiChannelResultEnvelope(ExtensibleResultModel):
    """One solve, one channel per drive channel, addressed by ``channel_order``.

    Every channel is a parametric-shaped result whose ``metadata`` names its
    drive address: ``drive_channel_id``, the ``source_ids`` it drives, ``role``
    (the driver band ``HF``/``MF``/``LF``, null when the sources carry no band
    role) and, when the ingestion record names its sources, ``source_labels``
    parallel to ``source_ids``. A combined channel adds ``derived_from_channels``
    and a ``combine`` payload whose ``members`` and ``member_roles`` are parallel
    lists, so a client can label a crossover pair without the ingestion record.
    """

    result_kind: Literal["multi_channel"]
    result_contract_version: Literal[2]
    client_request_id: str | None
    client_metadata: dict[str, JsonValue]
    provenance: ResultProvenance
    metadata: dict[str, Any]
    channels: dict[str, dict[str, Any]]
    channel_order: list[str]
    #: Sorted union of every channel's frequency grid. Optional: envelopes
    #: persisted before the field existed remain valid without it.
    frequencies: list[float] | None = None


ResultEnvelope = Annotated[
    ParametricResultEnvelope | MultiChannelResultEnvelope,
    Field(discriminator="result_kind"),
]

# Construct the discriminator once. Persistence uses this adapter only as a
# validation pass and retains the original mapping for its zero-copy GET path.
RESULT_ENVELOPE_ADAPTER = TypeAdapter(ResultEnvelope)


__all__ = [
    "MultiChannelResultEnvelope",
    "ParametricResultEnvelope",
    "RESULT_ENVELOPE_ADAPTER",
    "ResultEnvelope",
    "ResultProvenance",
]
