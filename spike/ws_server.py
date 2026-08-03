"""FastAPI server for the Phase 0 live-preview and chart spikes."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import sqlite3
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from frame_codec import encode, grid_to_mesh


# Uvicorn configures this logger at INFO, so startup measurements are visible
# without mutating process-wide logging configuration.
LOGGER = logging.getLogger("uvicorn.error")
SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parent
V1_ROOT = REPO_ROOT.parent / "Waveguide Generator"
V1_SERVER = V1_ROOT / "server"
V1_DATABASE = V1_SERVER / "data" / "simulations.db"
STATIC_ROOT = SPIKE_ROOT / "static"
PAYLOAD_ROOT = SPIKE_ROOT / "payloads"
FAMILY_FILES = {
    "osse": "osse.json",
    "rosse": "rosse.json",
    "icw": "icw.json",
    "freeform": "freeform.json",
}
LODS = {
    "coarse": {"n_angular": 8, "n_length": 4},
    "fine": {"n_angular": 96, "n_length": 48},
}

ViewportBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
_builder: ViewportBuilder | None = None
_payloads: dict[str, dict[str, Any]] = {}


def _load_builder() -> ViewportBuilder:
    sys.path.insert(0, str(V1_SERVER))
    from solver.mesher_adapter import build_viewport_geometry

    return build_viewport_geometry


def _load_payloads() -> dict[str, dict[str, Any]]:
    return {
        family: json.loads((PAYLOAD_ROOT / filename).read_text(encoding="utf-8"))
        for family, filename in FAMILY_FILES.items()
    }


def _database_uri() -> str:
    return f"{V1_DATABASE.as_uri()}?mode=ro"


def _largest_results_json() -> str:
    with sqlite3.connect(_database_uri(), uri=True) as connection:
        row = connection.execute(
            "SELECT results_json FROM simulation_results "
            "ORDER BY length(results_json) DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("v1 database contains no simulation_results rows")
    return str(row[0])


def _payload_for_request(family: str, lod: str, override: Any) -> dict[str, Any]:
    if family not in _payloads:
        raise ValueError(f"unknown family {family!r}")
    if lod not in LODS:
        raise ValueError(f"unknown LOD {lod!r}")
    payload = copy.deepcopy(_payloads[family])
    payload.update(LODS[lod])
    if override is None:
        return payload
    if isinstance(override, bool) or not isinstance(override, (int, float)):
        raise ValueError("paramOverride must be one finite scalar factor")
    factor = float(override)
    if not math.isfinite(factor) or not 0.5 <= factor <= 1.5:
        raise ValueError("paramOverride factor must be between 0.5 and 1.5")

    # One scalar controls the same physical parameter for every family: throat
    # radius. FREEFORM stores that shared radius in both H/V profile anchors.
    if family == "freeform":
        for profile_name in ("profile_h", "profile_v"):
            points = payload[profile_name]["points"]
            points[0][1] = float(points[0][1]) * factor
    else:
        payload["r0"] = float(payload["r0"]) * factor
    return payload


def _compute_frame(request: Mapping[str, Any]) -> bytes:
    if _builder is None:
        raise RuntimeError("viewport builder was not pre-warmed")
    sequence = int(request["seq"])
    payload = _payload_for_request(
        str(request["family"]).lower(),
        str(request["lod"]).lower(),
        request.get("paramOverride"),
    )
    started = time.perf_counter()
    viewport = _builder(payload)
    eval_ms = (time.perf_counter() - started) * 1000.0
    return encode(sequence, eval_ms, grid_to_mesh(viewport))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _builder, _payloads
    started = time.perf_counter()
    _payloads = _load_payloads()
    _builder = _load_builder()
    family_times: dict[str, float] = {}
    for family in FAMILY_FILES:
        call_started = time.perf_counter()
        _builder(_payloads[family])
        family_times[family] = (time.perf_counter() - call_started) * 1000.0
    total_ms = (time.perf_counter() - started) * 1000.0
    LOGGER.info("viewport pre-warm %.2f ms total; families=%s", total_ms, family_times)
    yield


app = FastAPI(
    title="Waveguide preview spike",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/results/real", include_in_schema=False)
async def real_results() -> Response:
    raw_json = await asyncio.to_thread(_largest_results_json)
    return Response(content=raw_json, media_type="application/json")


async def _receiver(
    websocket: WebSocket,
    state: dict[str, Any],
    request_event: asyncio.Event,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        try:
            request = await websocket.receive_json()
            if not isinstance(request, dict):
                raise ValueError("preview request must be a JSON object")
            if "seq" not in request or "family" not in request or "lod" not in request:
                raise ValueError("preview request requires seq, family, and lod")
            int(request["seq"])
            # Validate without doing the expensive viewport call.
            _payload_for_request(
                str(request["family"]).lower(),
                str(request["lod"]).lower(),
                request.get("paramOverride"),
            )
            queued = state.get("pending")
            if queued is not None:
                async with send_lock:
                    await websocket.send_json({"type": "dropped", "seq": int(queued["seq"])})
            state["pending"] = request
            request_event.set()
        except WebSocketDisconnect:
            return
        except (TypeError, ValueError, KeyError) as error:
            try:
                async with send_lock:
                    await websocket.send_json({"type": "error", "message": str(error)})
            except WebSocketDisconnect:
                return


async def _worker(
    websocket: WebSocket,
    state: dict[str, Any],
    request_event: asyncio.Event,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        await request_event.wait()
        request_event.clear()
        request = state.get("pending")
        state["pending"] = None
        if request is None:
            continue
        try:
            frame = await asyncio.to_thread(_compute_frame, request)
        except Exception as error:  # noqa: BLE001 - keep the live preview socket usable
            LOGGER.exception("preview request %s failed", request.get("seq"))
            async with send_lock:
                await websocket.send_json(
                    {"type": "error", "seq": int(request["seq"]), "message": str(error)}
                )
            continue
        async with send_lock:
            await websocket.send_bytes(frame)


@app.websocket("/ws/preview")
async def preview_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    state: dict[str, Any] = {"pending": None}
    request_event = asyncio.Event()
    send_lock = asyncio.Lock()
    receiver = asyncio.create_task(
        _receiver(websocket, state, request_event, send_lock), name="preview-receiver"
    )
    worker = asyncio.create_task(
        _worker(websocket, state, request_event, send_lock), name="preview-worker"
    )
    done, pending = await asyncio.wait(
        {receiver, worker}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        if task.cancelled():
            continue
        error = task.exception()
        if error is not None and not isinstance(error, WebSocketDisconnect):
            raise error
