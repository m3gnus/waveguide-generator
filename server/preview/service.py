"""FastAPI transport adapter for the transport-independent preview protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from typing import Any, Mapping

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from server.platform.origin import websocket_request_allowed
from server.preview.core import (
    CLOSE_ORIGIN_REJECTED,
    PreviewComputeService,
    PreviewProtocol,
)



class _FastAPITransport:
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

    async def send_json(self, message: Mapping[str, Any]) -> None:
        await self.websocket.send_json(dict(message))

    async def send_bytes(self, data: bytes) -> None:
        await self.websocket.send_bytes(data)

    async def close(self, code: int) -> None:
        if self.websocket.application_state != WebSocketState.DISCONNECTED:
            await self.websocket.close(code=code)


def create_preview_router(
    service: PreviewComputeService, *, extra_ws_origins: Collection[str] = ()
) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/preview")
    async def preview_websocket(websocket: WebSocket) -> None:
        """Serve WS-PROTOCOL v1 preview and curve requests."""

        origin = websocket.headers.get("origin")
        server = websocket.scope.get("server")
        bound_port = server[1] if isinstance(server, (list, tuple)) and len(server) > 1 else None
        if not websocket_request_allowed(
            origin=origin,
            host=websocket.headers.get("host"),
            scheme=websocket.scope.get("scheme", "ws"),
            bound_port=bound_port,
            extra_origins=extra_ws_origins,
        ):
            await websocket.close(code=CLOSE_ORIGIN_REJECTED)
            return
        await websocket.accept()
        protocol = PreviewProtocol(preview_service=service)
        try:
            await protocol.run(_FastAPITransport(websocket))
        except asyncio.CancelledError:
            await protocol.close_restarting()
            raise
        except WebSocketDisconnect:
            return

    return router


def mount_preview(
    application: FastAPI, *, extra_ws_origins: Collection[str] = ()
) -> PreviewComputeService:
    service = PreviewComputeService()
    application.state.preview_service = service
    application.include_router(
        create_preview_router(service, extra_ws_origins=extra_ws_origins)
    )
    application.router.add_event_handler("shutdown", service.shutdown)
    return service


__all__ = ["create_preview_router", "mount_preview"]
