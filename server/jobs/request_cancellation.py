"""One disconnect watcher for request-owned native mesh work."""

import asyncio
import threading
from typing import Any, Awaitable, Callable

from server.cadlink.store import StaleAttempt


DISCONNECT_POLL_SECONDS = 0.25


class ClientDisconnected(Exception):
    """The requesting client went away mid-build."""


class RequestCancellation:
    def __init__(self, watch: Callable[[], Awaitable[bool]] | None, *,
                 commit_guard: Any = None, cancel_cb: Any = None) -> None:
        self.commit_guard = commit_guard
        self.cancel_cb = cancel_cb
        self.disconnected = threading.Event()
        self.watcher = asyncio.create_task(self._watch(watch)) if watch is not None else None

    async def _watch(self, watch: Callable[[], Awaitable[bool]]) -> None:
        while not await watch():
            await asyncio.sleep(DISCONNECT_POLL_SECONDS)
        self.disconnected.set()

    def current(self, conn: Any = None) -> bool:
        if self.disconnected.is_set():
            raise ClientDisconnected("mesh client disconnected")
        if self.cancel_cb is not None:
            self.cancel_cb()
        return self.commit_guard is None or bool(self.commit_guard(conn))

    def check(self) -> None:
        if not self.current():
            raise StaleAttempt("the preparation is no longer current")

    def close(self) -> None:
        self.disconnected.set()
        if self.watcher is not None:
            self.watcher.cancel()
