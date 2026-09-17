"""Routes for an unlinked model's solver frame: preview and confirmation.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". The snapshot is
named by an ingestion record or by a CAD operation (whose preparation names
one); the confirmation's key and requirement always come from that record,
never from the request. Confirming prepares nothing, so the update-restart
latch does not apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ingest import get_ingestion_record
from .solver_frame import FrameConfirmationError, confirm_frame, frame_preview
from .store import CadLinkStore


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


class SolverFrameConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    operation_id: str | None = Field(default=None, alias="operationId", min_length=1)
    ingest_id: str | None = Field(default=None, alias="ingestId", min_length=1)
    axis: str = Field(min_length=1)

    @model_validator(mode="after")
    def _one_snapshot(self) -> SolverFrameConfirmationRequest:
        if (self.operation_id is None) == (self.ingest_id is None):
            raise ValueError("name exactly one of operationId or ingestId")
        return self


def snapshot_record(
    store: CadLinkStore, *, operation_id: str | None, ingest_id: str | None
) -> Mapping[str, Any]:
    """The ingestion record a request names, directly or through an operation."""

    if (operation_id is None) == (ingest_id is None):
        raise HTTPException(status_code=422, detail="name exactly one of operationId or ingestId")
    if operation_id is not None:
        operation = store.get_operation(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail=f"Unknown CAD operation {operation_id}")
        preparation_id = operation.get("preparation_id")
        preparation = store.get_preparation(str(preparation_id)) if preparation_id else None
        if preparation is None:
            raise HTTPException(
                status_code=409,
                detail="This CAD operation has no preparation yet, so there is no geometry to preview.",
            )
        ingest_id = str(preparation["ingest_id"])
    assert ingest_id is not None
    record = get_ingestion_record(store, ingest_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown CAD ingestion {ingest_id}")
    return record


@router.get("/solver-frame")
async def get_solver_frame(
    request: Request, operationId: str | None = None, ingestId: str | None = None  # noqa: N803
) -> dict[str, Any]:
    """Every solver frame axis's matrix for this snapshot, and what its project confirmed."""

    store: CadLinkStore = request.app.state.cadlink_store

    def load() -> dict[str, Any]:
        record = snapshot_record(store, operation_id=operationId, ingest_id=ingestId)
        return frame_preview(store, record)

    return await asyncio.to_thread(load)


@router.put("/solver-frame")
async def put_solver_frame(
    payload: SolverFrameConfirmationRequest, request: Request
) -> dict[str, Any]:
    """Confirm the axis this snapshot's project radiates along."""

    store: CadLinkStore = request.app.state.cadlink_store

    def confirm() -> dict[str, Any]:
        record = snapshot_record(
            store, operation_id=payload.operation_id, ingest_id=payload.ingest_id
        )
        try:
            confirm_frame(store, record, payload.axis)
        except FrameConfirmationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return frame_preview(store, record)

    return await asyncio.to_thread(confirm)


__all__ = ["SolverFrameConfirmationRequest", "router", "snapshot_record"]
