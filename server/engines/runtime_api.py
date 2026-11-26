"""User-initiated BEAT runtime preparation through the launcher lifecycle."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from server.solver import beat_cpu_runtime


class RuntimePreparationResponse(BaseModel):
    backend: Literal["cpu", "metal", "cuda", "rocm"]
    state: Literal["preparing"] = "preparing"


class EnginePrewarmRequest(BaseModel):
    engine: str
    #: The user's own engine choice as the page holds it, which can be ahead
    #: of the server's settings store by the page's debounced upload. Absent
    #: when the page has none; the server then reads its stored preference.
    preference: str | None = None


def mount_runtime_preparation(application: FastAPI) -> None:
    @application.post("/api/runtimes/beat/prewarm", status_code=202)
    async def prewarm_runtime(selection: EnginePrewarmRequest) -> dict[str, str]:
        # Schedule only. Probe, admission and compilation stay off the HTTP loop.
        await application.state.prewarm_beat_worker(selection.engine, preference=selection.preference)
        return {"state": "scheduled"}

    @application.post("/api/runtimes/beat/{backend}/prepare", status_code=202,
                      response_model=RuntimePreparationResponse,
                      responses={409: {"description": "A BEAT runtime preparation is already running."},
                                 400: {"description": "Runtime preparation is unavailable or disabled."}})
    async def prepare_runtime(backend: Literal["cpu", "metal", "cuda", "rocm"]) -> RuntimePreparationResponse:
        try:
            await asyncio.to_thread(beat_cpu_runtime.start_runtime_preparation, backend)
        except beat_cpu_runtime.RuntimePreparationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RuntimePreparationResponse(backend=backend)
