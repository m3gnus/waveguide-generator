"""Both mounted WebSocket routes use the same lossless transport adapter."""

import asyncio
from types import MappingProxyType, SimpleNamespace

from fastapi import FastAPI, WebSocketDisconnect
import pytest

from server.jobs import api as jobs_api
from server.preview import service as preview_service
from server.protocol.transport import FastAPIWebSocketTransport


@pytest.mark.parametrize("route", ["preview", "jobs"])
@pytest.mark.parametrize(("message", "expected"), [
    ({"type": "websocket.receive", "text": "request", "bytes": b"ignored"}, "request"),
    ({"type": "websocket.receive", "text": "", "bytes": b"ignored"}, ""),
    ({"type": "websocket.receive", "bytes": b"\x00\xff"}, b"\x00\xff"),
    ({"type": "websocket.receive"}, None),
    ({"type": "websocket.disconnect", "code": 1000}, None),
    (WebSocketDisconnect(1001), None),
])
def test_mounted_routes_share_receive_send_and_idempotent_close(monkeypatch, route, message, expected):
    sent = []

    class Protocol:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, transport):
            assert type(transport) is FastAPIWebSocketTransport
            assert await transport.receive() == expected
            await transport.send_json(MappingProxyType({"event": "ready", "value": 1.25, "label": "é"}))
            if route == "preview":
                await transport.send_bytes(b"\x00\xffpreview")
            await transport.close(1000)
            await transport.close(1000)

    application = FastAPI()
    if route == "preview":
        monkeypatch.setattr(preview_service, "PreviewProtocol", Protocol)
        application.include_router(preview_service.create_preview_router(SimpleNamespace()))
    else:
        monkeypatch.setattr(jobs_api, "JobsProtocol", Protocol)
        application.include_router(jobs_api.create_jobs_router(SimpleNamespace(start=lambda: None, shutdown=lambda: None)))

    async def exercise():
        incoming = [{"type": "websocket.connect"}, message]

        async def receive():
            item = incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def send(item):
            sent.append(item)

        await application({
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
            "path": f"/ws/{route}", "query_string": b"", "root_path": "",
            "headers": [(b"host", b"127.0.0.1:3100")],
            "server": ("127.0.0.1", 3100), "client": ("127.0.0.1", 12345),
            "subprotocols": [],
        }, receive, send)

    asyncio.run(exercise())
    assert sent == [
        {"type": "websocket.accept", "subprotocol": None, "headers": []},
        {"type": "websocket.send", "text": '{"event":"ready","value":1.25,"label":"é"}'},
        *([{"type": "websocket.send", "bytes": b"\x00\xffpreview"}] if route == "preview" else []),
        {"type": "websocket.close", "code": 1000, "reason": ""},
    ]
