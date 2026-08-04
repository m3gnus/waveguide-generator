"""REST correctness path and FastAPI adapter for solve jobs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from server.jobs.events import CLOSE_ORIGIN_REJECTED, JobsProtocol
from server.jobs.models import (
    ClearFailedResponse,
    DeleteResponse,
    JobListResponse,
    JobMetadataPatch,
    JobStatusResponse,
    MetadataResponse,
    SolveAccepted,
    SolveRequest,
    StopResponse,
)
from server.jobs.runtime import (
    EngineUnavailableError,
    JobConflictError,
    JobNotFoundError,
    JobResourceUnavailableError,
    JobRuntime,
    UnknownEngineError,
)
from server.jobs.store import JobStore
from server.engines.registry import EngineRegistry
from server.platform.origin import local_origin


_local_origin = local_origin


class _FastAPIJobsTransport:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def receive(self) -> str | bytes | None:
        try:
            message = await self.websocket.receive()
        except WebSocketDisconnect:
            return None
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("text") is not None:
            return message["text"]
        return message.get("bytes")

    async def send_json(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)

    async def close(self, code: int) -> None:
        if self.websocket.application_state != WebSocketState.DISCONNECTED:
            await self.websocket.close(code=code)


def create_jobs_router(runtime: JobRuntime) -> APIRouter:
    """Build bound routes with v1 error mappings and response shapes.

    Route coverage follows v1 ``server/api/routes_simulation.py:69-297``.
    """

    router = APIRouter()
    router.add_event_handler("startup", runtime.start)
    router.add_event_handler("shutdown", runtime.shutdown)

    @router.post("/api/solve", response_model=SolveAccepted)
    async def submit_solve(body: SolveRequest) -> SolveAccepted:
        try:
            job_id = await runtime.submit(body)
        except UnknownEngineError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EngineUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return SolveAccepted(job_id=job_id)

    @router.post("/api/stop/{job_id}", response_model=StopResponse)
    async def stop_job(job_id: str) -> StopResponse:
        try:
            return StopResponse.model_validate(await runtime.stop(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/status/{job_id}", response_model=JobStatusResponse)
    async def job_status(job_id: str) -> JobStatusResponse:
        try:
            return JobStatusResponse.model_validate(await runtime.get_job(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @router.get("/api/results/{job_id}")
    async def job_results(job_id: str) -> dict[str, Any]:
        try:
            return await runtime.get_results(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/mesh-artifact/{job_id}", response_class=PlainTextResponse)
    async def mesh_artifact(job_id: str) -> PlainTextResponse:
        try:
            content = await runtime.get_mesh_artifact(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return PlainTextResponse(content=content, media_type="text/plain")

    @router.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
    async def job_log(job_id: str) -> PlainTextResponse:
        try:
            content = await runtime.get_log(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return PlainTextResponse(content=content, media_type="text/plain")

    @router.get("/api/jobs", response_model=JobListResponse)
    async def list_jobs(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> JobListResponse:
        try:
            items, total = await runtime.list_jobs(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JobListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    @router.patch("/api/jobs/{job_id}/metadata", response_model=MetadataResponse)
    async def patch_job_metadata(
        job_id: str, body: JobMetadataPatch
    ) -> MetadataResponse:
        try:
            fields = {
                name: getattr(body, name) for name in body.model_fields_set
            }
            await runtime.patch_metadata(job_id, fields)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return MetadataResponse(status="ok")

    @router.delete("/api/jobs/clear-failed", response_model=ClearFailedResponse)
    async def clear_failed() -> ClearFailedResponse:
        deleted_ids = await runtime.clear_failed()
        return ClearFailedResponse(
            deleted=bool(deleted_ids),
            deleted_count=len(deleted_ids),
            deleted_ids=deleted_ids,
        )

    @router.delete("/api/jobs/{job_id}", response_model=DeleteResponse)
    async def delete_job(job_id: str) -> DeleteResponse:
        try:
            await runtime.delete(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return DeleteResponse(deleted=True, job_id=job_id)

    @router.websocket("/ws/jobs")
    async def jobs_websocket(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and not _local_origin(origin):
            await websocket.close(code=CLOSE_ORIGIN_REJECTED)
            return
        await websocket.accept()
        protocol = JobsProtocol(runtime)
        try:
            await protocol.run(_FastAPIJobsTransport(websocket))
        except asyncio.CancelledError:
            await protocol.close_restarting()
            raise
        except WebSocketDisconnect:
            return

    return router


def mount_jobs(
    application: FastAPI, engine_registry: EngineRegistry | None = None
) -> JobRuntime:
    """Attach one data-dir-bound runtime before the frontend catch-all mount."""

    data_dir = Path(application.state.data_dir)
    runtime = JobRuntime(
        JobStore.for_data_dir(data_dir), engine_registry=engine_registry
    )
    application.state.jobs_runtime = runtime
    application.include_router(create_jobs_router(runtime))
    return runtime


__all__ = ["create_jobs_router", "mount_jobs"]
