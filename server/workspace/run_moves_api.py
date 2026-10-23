"""HTTP boundary for the explicit, server-planned archive move tool."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.workspace.run_moves import MoveRefused, RunMoves


class PreviewRunMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    selected: list[str] | None = None
    gather: bool = True
    rename: bool = False


class ConfirmRunMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool


def create_run_moves_router(moves: RunMoves, publish_event: Any = lambda event: None) -> APIRouter:
    router = APIRouter(prefix="/api/workspace/run-moves", tags=["workspace"])

    async def call(method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except (MoveRefused, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            # Publish from the event loop; EventBroker queues are not thread-safe.
            for event in moves.take_events():
                publish_event(event)

    @router.post("/preview")
    async def preview(payload: PreviewRunMove) -> dict[str, Any]:
        """Read-only scan and immutable preview; never moves folders."""
        return await call(moves.preview, **payload.model_dump())

    @router.post("/{move_id}/execute")
    async def execute(move_id: str, payload: ConfirmRunMove) -> dict[str, Any]:
        """Execute only the saved preview explicitly confirmed by the user."""
        return await call(moves.execute, move_id, confirmed=payload.confirmed)

    @router.post("/{move_id}/undo")
    async def undo(move_id: str) -> dict[str, Any]:
        return await call(moves.undo, move_id)

    @router.get("/status")
    async def status() -> dict[str, Any]:
        def reports() -> list[dict]:
            if not moves.directory.exists():
                return []
            records = [moves.read(path.stem) for path in sorted(path for path in moves.directory.iterdir() if path.suffix == ".json")]
            return sorted((record for record in records if record["phase"] not in {"planned", "refused"}), key=lambda record: record["created_at"])
        return {"reports": await call(reports), "recovered": moves.recovered_reports}

    @router.post("/recover")
    async def recover() -> dict[str, Any]:
        """Retry journal recovery after reconnecting a drive or closing an open file."""
        return {"reports": await call(moves.recover)}

    return router
