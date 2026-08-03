"""Transport-independent ``/ws/jobs`` snapshot/cursor protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from itertools import count
import json
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

    This implements the new v2 behavior in ``docs/WS-PROTOCOL.md:45-68``;
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
        self.max_message_bytes = int(max_message_bytes)
        self._closed = False
        self._transport: JobsTransport | None = None

    def resume_payload(self, cursor: int) -> list[dict[str, Any]] | dict[str, Any]:
        """Expose replay-or-snapshot policy for transport-agnostic tests."""

        replay = self.runtime.resume(cursor)
        return self.runtime.snapshot() if replay is None else replay

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
            snapshot = self.runtime.snapshot()
            snapshot["epoch"] = self.epoch
            await transport.send_json(snapshot)
            last_live_cursor = int(snapshot["cursor"])

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
                    keep_open = await self._handle_message(transport, raw)
                    if not keep_open:
                        break
                    receive_task = asyncio.create_task(transport.receive())
                if event_task in done:
                    event = event_task.result()
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

    async def _handle_message(self, transport: JobsTransport, raw: Any) -> bool:
        if isinstance(raw, bytes):
            size = len(raw)
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
                return False
        elif isinstance(raw, str):
            size = len(raw.encode("utf-8"))
        elif isinstance(raw, Mapping):
            size = len(json.dumps(dict(raw), separators=(",", ":")).encode("utf-8"))
        else:
            await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
            return False
        if size > self.max_message_bytes:
            await transport.close(code=CLOSE_TOO_LARGE)
            return False
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
                return False
        else:
            value = dict(raw)
        if value.get("v") != PROTOCOL_VERSION:
            await transport.close(code=CLOSE_UNSUPPORTED_VERSION)
            return False
        epoch = value.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch != self.epoch:
            return True
        if value.get("kind") != "resume":
            return True
        cursor = value.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            snapshot = self.runtime.snapshot()
            snapshot["epoch"] = self.epoch
            await transport.send_json(snapshot)
            return True
        payload = self.resume_payload(cursor)
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["epoch"] = self.epoch
            await transport.send_json(payload)
        else:
            for event in payload:
                message = dict(event)
                message["epoch"] = self.epoch
                await transport.send_json(message)
        return True


__all__ = [
    "CLOSE_ORIGIN_REJECTED",
    "CLOSE_RESTARTING",
    "JobsProtocol",
    "JobsTransport",
]
