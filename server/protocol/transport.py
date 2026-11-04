"""FastAPI adapter shared by the transport-independent WebSocket protocols."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState


class FastAPIWebSocketTransport:
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
