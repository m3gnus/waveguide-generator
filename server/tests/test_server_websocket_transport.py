"""Real launcher-selected flow control, without sockets or application startup."""
from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path

import pytest
from uvicorn.protocols.utils import ClientDisconnected
from uvicorn.server import ServerState
from websockets.protocol import State

from launch import serve


async def _app(scope, receive, send):
    raise AssertionError("Transport tests must never start an application")


def _production_config():
    # Execute the actual Config expression, avoiding main()'s installation
    # locks, app creation and threads. Do not repeat its transport choice here.
    tree = ast.parse(Path(serve.__file__).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "Config"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "uvicorn"]
    assert len(calls) == 1
    namespace = dict(vars(serve), tracker=None, app=_app, port=3100)
    config = eval(compile(ast.Expression(calls[0]), serve.__file__, "eval"), namespace)
    config.load()
    return config


class _Transport:
    def __init__(self):
        self.frames = []
        self.closed = False

    def write(self, data):
        self.frames.append(bytes(data))

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    def can_write_eof(self):
        return False


def _open_protocol(config):
    protocol = config.ws_protocol_class(config, ServerState(), {})
    protocol.transport = _Transport()
    protocol.state = State.OPEN
    protocol.handshake_started_event.set()
    protocol.handshake_completed_event.set()
    protocol.transfer_data_task = asyncio.get_running_loop().create_future()
    return protocol


async def _until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def _cancel(*tasks):
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_production_transport_keepalive_survives_a_paused_outbound_send(caplog):
    async def exercise():
        protocol = _open_protocol(_production_config())
        protocol.pause_writing()
        protocol.ping_interval = 0.005
        sender = asyncio.create_task(protocol.asgi_send({
            "type": "websocket.send", "text": "pending result delta",
        }))
        keepalive = None
        try:
            await _until(lambda: protocol._drain_waiter is not None)
            keepalive = asyncio.create_task(protocol.keepalive_ping())
            await _until(lambda: bool(protocol.pings) or keepalive.done())
            assert not sender.done()
            assert len(protocol.transport.frames) == 2
            assert not keepalive.done()
            protocol.resume_writing()
            await asyncio.wait_for(sender, 2)
            assert not [record for record in caplog.records
                        if record.exc_info and record.exc_info[0] is AssertionError]
        finally:
            await _cancel(sender, *([keepalive] if keepalive else []), protocol.transfer_data_task)
    asyncio.run(exercise())


def test_production_transport_cancelled_drain_releases_the_next_sender():
    async def exercise():
        protocol = _open_protocol(_production_config())
        protocol.pause_writing()
        first = asyncio.create_task(protocol.asgi_send({"type": "websocket.send", "text": "first"}))
        second = None
        try:
            await _until(lambda: protocol._drain_waiter is not None)
            previous_waiter = protocol._drain_waiter
            second = asyncio.create_task(protocol.asgi_send({"type": "websocket.send", "text": "second"}))
            await asyncio.sleep(0)
            await _cancel(first)
            assert previous_waiter.cancelled()
            await _until(lambda: protocol._drain_waiter is not previous_waiter or second.done())
            assert not second.done()
            protocol.resume_writing()
            await asyncio.wait_for(second, 2)
            assert protocol._drain_waiter is None
        finally:
            await _cancel(first, *([second] if second else []), protocol.transfer_data_task)
    asyncio.run(exercise())


@pytest.mark.parametrize("error", [None, ConnectionResetError("peer reset")])
def test_production_transport_connection_lost_releases_paused_and_queued_sends(error):
    async def exercise():
        protocol = _open_protocol(_production_config())
        # Uvicorn normally registers this in connection_made(). No socket or
        # app startup is needed to drive its real connection_lost callback.
        protocol.connections.add(protocol)
        protocol.pause_writing()
        first = asyncio.create_task(protocol.asgi_send({"type": "websocket.send", "text": "first"}))
        second = None
        try:
            await _until(lambda: protocol._drain_waiter is not None)
            second = asyncio.create_task(protocol.asgi_send({"type": "websocket.send", "text": "second"}))
            await asyncio.sleep(0)
            assert not first.done() and not second.done()
            protocol.connection_lost(error)
            results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 2)
            # On orderly TCP EOF the already-written first message may finish;
            # the queued send must report disconnect, never await a dead peer.
            assert results[0] is None or isinstance(results[0], ClientDisconnected)
            assert isinstance(results[1], ClientDisconnected)
            assert protocol.connection_lost_waiter.done()
            assert protocol._drain_waiter is None
            assert not protocol._flow_control_lock.locked()
            if hasattr(protocol, "close_connection_task"):
                await asyncio.wait_for(protocol.close_connection_task, 2)
            assert protocol.transport.closed
        finally:
            # These sends are expected to finish with ClientDisconnected;
            # consume their outcomes without re-raising them during cleanup.
            tasks = [first, *([second] if second else []), protocol.transfer_data_task]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(exercise())


def test_production_transport_keeps_ping_pong_and_timeout(monkeypatch):
    async def exercise():
        config = _production_config()
        assert config.ws_ping_interval == 20 and config.ws_ping_timeout == 20
        protocol = _open_protocol(config)
        protocol.ping_interval = 0.005
        protocol.ping_timeout = 0.02
        failures = []
        # Resolve the actual Ping's waiter as the protocol's matching Pong
        # handler does. Observe timeout without starting socket-close tasks.
        monkeypatch.setattr(protocol, "fail_connection", lambda code, reason: failures.append((code, reason)))
        keepalive = asyncio.create_task(protocol.keepalive_ping())
        try:
            await _until(lambda: bool(protocol.pings))
            first_ping, (pong, _) = next(iter(protocol.pings.items()))
            pong.set_result(0.001)
            protocol.pings.clear()
            await _until(lambda: bool(protocol.pings))
            assert first_ping not in protocol.pings and not failures
            await asyncio.wait_for(keepalive, 2)
            assert failures == [(1011, "keepalive ping timeout")]
        finally:
            await _cancel(keepalive, protocol.transfer_data_task)
    asyncio.run(exercise())


def test_production_transport_retains_parser_bounds_and_compression():
    async def exercise():
        config = _production_config()
        protocol = _open_protocol(config)
        try:
            assert config.ws_max_size == protocol.max_size == serve.DEFAULT_MAX_FRAME_BYTES
            assert config.ws_max_queue == protocol.max_queue == 1
            assert config.ws_per_message_deflate is False
            assert not protocol.available_extensions
            assert protocol.ping_interval == protocol.ping_timeout == 20
            assert config.timeout_graceful_shutdown == 3 and config.access_log is False
        finally:
            await _cancel(protocol.transfer_data_task)
    asyncio.run(exercise())
