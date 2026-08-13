"""Transport-independent ``/ws/jobs`` snapshot/cursor protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from itertools import count
import json
import math
from typing import Any, Protocol

from server.jobs.runtime import JobRuntime


PROTOCOL_VERSION = 1
HEARTBEAT_SECONDS = 15.0
MAX_MESSAGE_BYTES = 64 * 1024
CLOSE_UNSUPPORTED_VERSION = 4400
CLOSE_ORIGIN_REJECTED = 4401
CLOSE_TOO_LARGE = 4413
CLOSE_RESTARTING = 1012
_EPOCHS = count(1)


class JobsTransport(Protocol):
    async def receive(self) -> str | bytes | Mapping[str, Any] | None: ...

    async def send_json(self, message: Mapping[str, Any]) -> None: ...

    async def close(self, code: int) -> None: ...


class JobsProtocol:
    """Snapshot first, then durable events with replay-or-snapshot resume.

    This implements the behavior in ``docs/reference/WS-PROTOCOL.md``;
    lifecycle payloads remain derivable from the v1-compatible HTTP store.
    """

    def __init__(
        self,
        runtime: JobRuntime,
        *,
        epoch: int | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self.runtime = runtime
        self.epoch = next(_EPOCHS) if epoch is None else int(epoch)
        self.heartbeat_seconds = float(heartbeat_seconds)
        if not math.isfinite(self.heartbeat_seconds) or self.heartbeat_seconds <= 0.0:
            raise ValueError("heartbeat_seconds must be finite and positive")
        if isinstance(max_message_bytes, bool) or not isinstance(max_message_bytes, int):
            raise ValueError("max_message_bytes must be a positive integer")
        self.max_message_bytes = max_message_bytes
        if self.max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be a positive integer")
        self._closed = False
        self._transport: JobsTransport | None = None

    def resume_payload(self, cursor: int) -> list[dict[str, Any]] | dict[str, Any]:
        """Expose replay-or-snapshot policy for transport-agnostic tests."""

        replay = self.runtime.resume(cursor)
        return self.runtime.snapshot() if replay is None else replay

    async def _resume_payload_async(
        self, cursor: int
    ) -> list[dict[str, Any]] | dict[str, Any]:
        replay = await self.runtime.resume_async(cursor)
        return await self.runtime.snapshot_async() if replay is None else replay

    async def run(self, transport: JobsTransport) -> None:
        await self.runtime.start()
        self._transport = transport
        queue = self.runtime.events.subscribe()
        receive_task: asyncio.Task[Any] | None = None
        event_task: asyncio.Task[Any] | None = None
        last_live_cursor = 0
        try:
            await transport.send_json(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "hello",
                    "epoch": self.epoch,
                    "heartbeatSec": self.heartbeat_seconds,
                    "limits": {"maxFrameBytes": self.max_message_bytes},
                }
            )
            snapshot = await self.runtime.snapshot_async()
            snapshot["epoch"] = self.epoch
            await transport.send_json(snapshot)
            last_live_cursor = int(snapshot["cursor"])
            for partial in self.runtime.partial_result_messages():
                partial["epoch"] = self.epoch
                await transport.send_json(partial)

            receive_task = asyncio.create_task(transport.receive())
            event_task = asyncio.create_task(queue.get())
            while not self._closed:
                done, _ = await asyncio.wait(
                    {receive_task, event_task},
                    timeout=self.heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await transport.send_json(
                        {"v": 1, "kind": "ping", "epoch": self.epoch}
                    )
                    continue
                if receive_task in done:
                    raw = receive_task.result()
                    if raw is None:
                        break
                    keep_open, response_cursor = await self._handle_message(transport, raw)
                    if response_cursor is not None:
                        last_live_cursor = max(last_live_cursor, response_cursor)
                    if not keep_open:
                        break
                    receive_task = asyncio.create_task(transport.receive())
                if event_task in done:
                    event = event_task.result()
                    if event.get("kind") == "partialResult":
                        message = dict(event)
                        message["epoch"] = self.epoch
                        await transport.send_json(message)
                    else:
                        cursor = int(event["cursor"])
                        if cursor > last_live_cursor:
                            message = dict(event)
                            message["epoch"] = self.epoch
                            await transport.send_json(message)
                            last_live_cursor = cursor
                    event_task = asyncio.create_task(queue.get())
        finally:
            self.runtime.events.unsubscribe(queue)
            for task in (receive_task, event_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (receive_task, event_task) if task is not None),
                return_exceptions=True,
            )

    async def close_restarting(self) -> None:
        self._closed = True
        if self._transport is not None:
            await self._transport.close(code=CLOSE_RESTARTING)

    async def _handle_message(
        self, transport: JobsTransport, raw: Any
    ) -> tuple[bool, int | None]:
        if isinstance(raw, bytes):
            size = len(raw)
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
                return False, None
        elif isinstance(raw, str):
            size = len(raw.encode("utf-8"))
        elif isinstance(raw, Mapping):
            try:
                size = len(json.dumps(dict(raw), separators=(",", ":")).encode("utf-8"))
            except (RecursionError, TypeError, ValueError):
                await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
                return False, None
        else:
            await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
            return False, None
        if size > self.max_message_bytes:
            await transport.close(code=CLOSE_TOO_LARGE)
            return False, None
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, RecursionError):
                await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
                return False, None
        else:
            value = dict(raw)
        if not isinstance(value, dict):
            await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
            return False, None
        version = value.get("v")
        if type(version) is not int or version != PROTOCOL_VERSION:
            await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
            return False, None
        epoch = value.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch != self.epoch:
            return True, None
        if value.get("kind") != "resume":
            return True, None
        cursor = value.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            snapshot = await self.runtime.snapshot_async()
            snapshot["epoch"] = self.epoch
            await transport.send_json(snapshot)
            return True, int(snapshot["cursor"])
        payload = await self._resume_payload_async(cursor)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["epoch"] = self.epoch
            await transport.send_json(payload)
            response_cursor = int(payload["cursor"])
        else:
            response_cursor = cursor
            for event in payload:
                message = dict(event)
                message["epoch"] = self.epoch
                await transport.send_json(message)
                response_cursor = max(response_cursor, int(message["cursor"]))
        return True, response_cursor


__all__ = [
    "CLOSE_ORIGIN_REJECTED",
    "CLOSE_RESTARTING",
    "JobsProtocol",
    "JobsTransport",
]
