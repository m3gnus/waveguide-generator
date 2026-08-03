"""FastAPI transport adapter for the transport-independent preview protocol."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from server.preview.core import (
    CLOSE_ORIGIN_REJECTED,
    CLOSE_RESTARTING,
    PreviewProtocol,
)


router = APIRouter()


def _local_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname.rstrip(".").lower()
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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


@router.websocket("/ws/preview")
async def preview_websocket(websocket: WebSocket) -> None:
    """Serve WS-PROTOCOL v1 preview and curve requests."""

    origin = websocket.headers.get("origin")
    if origin is not None and not _local_origin(origin):
        await websocket.close(code=CLOSE_ORIGIN_REJECTED)
        return
    await websocket.accept()
    protocol = PreviewProtocol()
    try:
        await protocol.run(_FastAPITransport(websocket))
    except asyncio.CancelledError:
        await protocol.close_restarting()
        raise
    except WebSocketDisconnect:
        return


__all__ = ["preview_websocket", "router"]
