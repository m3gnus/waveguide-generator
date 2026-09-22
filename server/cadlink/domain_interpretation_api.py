"""Routes for a model's domain interpretation: what was solved, and Change.

PLAN.md M1c-auto. The model card reads the interpretation an ingestion record
states (``GET``) and records the user's reading for the model's lineage
(``PUT``). The key and the offered readings always come from the record,
never from the request. Recording a reading prepares nothing: Solve prepares
the model again under it, as a new preparation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain_interpretation import DomainReadingError, interpretation_view, record_reading
from .solver_frame_api import snapshot_record
from .store import CadLinkStore


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


class DomainReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation_id: str | None = Field(default=None, alias="operationId", min_length=1)
    ingest_id: str | None = Field(default=None, alias="ingestId", min_length=1)
    reading: Literal["as-shown", "reduced"]
    planes: list[Literal["x0", "y0"]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _one_snapshot(self) -> DomainReadingRequest:
        if (self.operation_id is None) == (self.ingest_id is None):
            raise ValueError("name exactly one of operationId or ingestId")
        if (self.reading == "reduced") != bool(self.planes):
            raise ValueError("a reduced reading names its planes, and only a reduced reading does")
        return self


@router.get("/domain-interpretation")
async def get_domain_interpretation(
    request: Request, operationId: str | None = None, ingestId: str | None = None  # noqa: N803
) -> dict[str, Any]:
    """How this snapshot's domain was read, the readings Change offers, and any pending Change."""

    store: CadLinkStore = request.app.state.cadlink_store

    def load() -> dict[str, Any]:
        record = snapshot_record(store, operation_id=operationId, ingest_id=ingestId)
        return interpretation_view(store, record)

    return await asyncio.to_thread(load)


@router.put("/domain-interpretation")
async def put_domain_interpretation(
    payload: DomainReadingRequest, request: Request
) -> dict[str, Any]:
    """Change: remember the user's reading of this model's domain for its lineage."""

    store: CadLinkStore = request.app.state.cadlink_store

    def change() -> dict[str, Any]:
        record = snapshot_record(
            store, operation_id=payload.operation_id, ingest_id=payload.ingest_id
        )
        reading: dict[str, Any] = {"reading": payload.reading}
        if payload.reading == "reduced":
            reading["planes"] = list(payload.planes)
        try:
            record_reading(store, record, reading)
        except DomainReadingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return interpretation_view(store, record)

    return await asyncio.to_thread(change)


__all__ = ["DomainReadingRequest", "router"]
