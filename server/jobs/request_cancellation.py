"""One disconnect watcher for request-owned native mesh work."""

import asyncio
import threading
from typing import Awaitable, Callable


DISCONNECT_POLL_SECONDS = 0.25


class ClientDisconnected(Exception):
    """The requesting client went away mid-build."""


class RequestCancellation:
    def __init__(self, watch: Callable[[], Awaitable[bool]] | None) -> None:
        self.disconnected = threading.Event()
        self.watcher = asyncio.create_task(self._watch(watch)) if watch is not None else None

    async def _watch(self, watch: Callable[[], Awaitable[bool]]) -> None:
        while not await watch():
            await asyncio.sleep(DISCONNECT_POLL_SECONDS)
        self.disconnected.set()

    def check(self) -> None:
        if self.disconnected.is_set():
            raise ClientDisconnected("mesh client disconnected")

    def close(self) -> None:
        self.disconnected.set()
        if self.watcher is not None:
            self.watcher.cancel()
