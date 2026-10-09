"""One automatic export owner across windows, with fenced HTTP operations.

Reservations are durably blocked *before* generation. If a window or backend
vanishes, retry is explicit: a write may have succeeded without its reply.
No timeout permits a replacement owner while an old HTTP operation is running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from starlette.responses import JSONResponse

from server.jobs.runtime import JobNotFoundError

LEASE_SECONDS = 60
HEADER = b"x-wg-auto-export"


@dataclass
class Reservation:
    token: str
    job_id: str
    formats: list[str]
    selected_formats: list[str]
    deadline: float
    active: int = 0
    released: bool = False


class AutoExportOwner:
    def __init__(self, runtime: Any, clock: Any = time.monotonic):
        self.runtime = runtime
        self.clock = clock
        self.lock = asyncio.Lock()
        self.owner: Reservation | None = None

    def _expire(self) -> None:
        owner = self.owner
        if owner and (owner.released or owner.deadline <= self.clock()) and not owner.active:
            self.owner = None

    def _require(self, token: str) -> Reservation:
        self._expire()
        if (
            not self.owner
            or self.owner.token != token
            or self.owner.released
            or self.owner.deadline <= self.clock()
        ):
            raise HTTPException(
                409, "Automatic export ownership expired; retry the blocked export."
            )
        return self.owner

    async def claim(self, job_id: str, formats: list[str]) -> dict[str, Any]:
        async with self.lock:
            self._expire()
            if self.owner:
                return {"claimed": False, "busy": True}
            try:
                job = await self.runtime.get_job(job_id)
            except JobNotFoundError as exc:
                raise HTTPException(404, "Job not found") from exc
            if job["status"] != "complete" or not job["has_results"]:
                raise HTTPException(409, "Only completed jobs with results can be exported.")
            if formats == ["run_archive"] and job.get("archived_at"):
                return {"claimed": False, "busy": False}
            statuses = job.get("auto_export_formats", {})
            pending = [
                f
                for f in formats
                if statuses.get(f, {}).get("status") not in ("complete", "blocked")
            ]
            if not pending:
                return {"claimed": False, "busy": False}
            token = secrets.token_urlsafe(32)
            timestamp = datetime.now(timezone.utc).isoformat()
            await self.runtime.patch_metadata(
                job_id,
                {
                    "auto_export_formats": {
                        f: {
                            "status": "blocked",
                            "attempted_at": timestamp,
                            "reason": "Automatic export was interrupted or is still running. Retry after resolving any destination conflicts.",
                        }
                        for f in pending
                    }
                },
            )
            self.owner = Reservation(token, job_id, pending, formats, self.clock() + LEASE_SECONDS)
            return {"claimed": True, "token": token, "formats": pending, "job": job}

    async def heartbeat(self, token: str) -> None:
        async with self.lock:
            self._require(token).deadline = self.clock() + LEASE_SECONDS

    async def finish(self, token: str, body: dict[str, Any]) -> None:
        async with self.lock:
            owner = self._require(token)
            if owner.active:
                raise HTTPException(409, "Export requests are still running.")
            statuses = body.get("formats", {})
            if not isinstance(statuses, dict) or set(statuses) != set(owner.formats):
                raise HTTPException(422, "Finish must account for every reserved format.")
            for value in statuses.values():
                if (
                    not isinstance(value, dict)
                    or value.get("status") not in ("complete", "failed", "blocked")
                    or not isinstance(value.get("attempted_at"), str)
                ):
                    raise HTTPException(422, "Invalid export format status.")
            files = body.get("files", [])
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                raise HTTPException(422, "Invalid exported files.")
            changes = {"auto_export_formats": statuses}
            if owner.formats == ["run_archive"]:
                if statuses["run_archive"]["status"] == "complete":
                    changes["archived_at"] = statuses["run_archive"]["attempted_at"]
            else:
                job = await self.runtime.get_job(owner.job_id)
                all_statuses = {**job.get("auto_export_formats", {}), **statuses}
                completed = (
                    body.get("completed_at")
                    if all(
                        all_statuses.get(f, {}).get("status") == "complete"
                        for f in owner.selected_formats
                    )
                    else None
                )
                changes.update(exported_files=files, auto_export_completed_at=completed)
            await self.runtime.patch_metadata(owner.job_id, changes)
            self.owner = None

    async def release(self, token: str) -> None:
        async with self.lock:
            owner = self._require(token)
            owner.released = True
            self._expire()

    async def retry(self, job_id: str) -> None:
        async with self.lock:
            self._expire()
            if self.owner and self.owner.job_id == job_id:
                raise HTTPException(409, "This export is still running. Wait before retrying.")
            try:
                job = await self.runtime.get_job(job_id)
            except JobNotFoundError as exc:
                raise HTTPException(404, "Job not found") from exc
            statuses = {
                f: {**value, "status": "failed", "reason": "Explicit retry requested."}
                for f, value in job.get("auto_export_formats", {}).items()
                if value.get("status") == "blocked"
            }
            await self.runtime.patch_metadata(
                job_id, {"auto_export_formats": statuses, "auto_export_completed_at": None}
            )

    async def enter(self, token: str) -> Reservation:
        async with self.lock:
            owner = self._require(token)
            # Expiry while a previous request runs retains exclusivity but does
            # not authorize another request from the abandoned owner.
            if owner.deadline <= self.clock():
                raise HTTPException(409, "Automatic export ownership expired.")
            owner.active += 1
            return owner

    async def leave(self, owner: Reservation) -> None:
        async with self.lock:
            owner.active -= 1
            self._expire()


class AutoExportMiddleware:
    def __init__(self, app: Any, owner: AutoExportOwner):
        self.app = app
        self.owner = owner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        token = dict(scope.get("headers", [])).get(HEADER) if scope["type"] == "http" else None
        if not token:
            await self.app(scope, receive, send)
            return
        try:
            reservation = await self.owner.enter(token.decode("ascii"))
        except (HTTPException, UnicodeError) as exc:
            await JSONResponse({"detail": str(getattr(exc, "detail", exc))}, status_code=409)(
                scope, receive, send
            )
            return

        async def operation() -> None:
            try:
                await self.app(scope, receive, send)
            finally:
                await self.owner.leave(reservation)

        # Native STEP work cannot be cancelled halfway. Keep its ownership until
        # the handler actually settles even if its HTTP caller is cancelled.
        task = asyncio.create_task(operation())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise


def router_for(owner: AutoExportOwner) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs/{job_id}/auto-export/claim")
    async def claim(job_id: str, request: Request) -> Any:
        body = await request.json()
        formats = body.get("formats") if isinstance(body, dict) else None
        if (
            not isinstance(formats, list)
            or not 0 < len(formats) <= 32
            or not all(isinstance(f, str) and f.isidentifier() and len(f) <= 40 for f in formats)
            or len(set(formats)) != len(formats)
        ):
            raise HTTPException(422, "Expected distinct export formats.")
        return await owner.claim(job_id, formats)

    @router.post("/api/jobs/auto-export/{token}/{action}")
    async def settle(token: str, action: str, request: Request) -> Any:
        if action == "heartbeat":
            await owner.heartbeat(token)
        elif action == "finish":
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(422, "Expected export completion object.")
            await owner.finish(token, body)
        elif action == "release":
            await owner.release(token)
        else:
            raise HTTPException(404, "Unknown export action.")
        return {"status": "ok"}

    @router.post("/api/jobs/{job_id}/auto-export/retry")
    async def retry(job_id: str) -> Any:
        await owner.retry(job_id)
        return {"status": "ok"}

    return router
