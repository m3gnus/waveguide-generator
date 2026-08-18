"""Process-wide asynchronous arbitration for Metal GPU work."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


class FieldEvaluationBusy(RuntimeError):
    """A solve is running or queued and has priority over field work."""


class FieldEvaluationSuperseded(RuntimeError):
    """A newer field request replaced this request in the pending slot."""

    def __init__(self, request_id: str, replacement_request_id: str) -> None:
        super().__init__(
            f"field request {request_id!r} was superseded by "
            f"{replacement_request_id!r}"
        )
        self.request_id = request_id
        self.replacement_request_id = replacement_request_id


@dataclass(slots=True)
class MetalLease:
    """One idempotently releasable solve or field permit lease."""

    _permit: "MetalPermit"
    _kind: str
    _token: object
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._permit._release(self._kind, self._token)


@dataclass(slots=True)
class _FieldWaiter:
    request_id: str
    token: object
    future: asyncio.Future[None]


class MetalPermit:
    """Give solves priority and coalesce field work to one pending request.

    A single solve or field lease may run at once. Field requests get one
    additional pending slot; replacing that slot completes the older waiter
    with :class:`FieldEvaluationSuperseded`. Async primitives are bound lazily
    so the process singleton remains usable by sequential test event loops.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._solve_waiters: deque[tuple[object, asyncio.Future[None]]] = deque()
        self._solve_owner: object | None = None
        self._field_owner: object | None = None
        self._pending_field: _FieldWaiter | None = None

    @property
    def solve_active_or_waiting(self) -> bool:
        return self._solve_owner is not None or bool(self._solve_waiters)

    @property
    def field_running(self) -> bool:
        return self._field_owner is not None

    @property
    def field_pending(self) -> bool:
        return self._pending_field is not None

    def _loop_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            assert self._lock is not None
            return self._lock
        if self._loop is not None and not self._loop.is_closed() and (
            self._solve_owner is not None
            or self._field_owner is not None
            or self._solve_waiters
            or self._pending_field is not None
        ):
            raise RuntimeError("Metal permit is active on another event loop")
        self._loop = loop
        self._lock = asyncio.Lock()
        self._solve_waiters.clear()
        self._solve_owner = None
        self._field_owner = None
        self._pending_field = None
        return self._lock

    async def acquire_solve(self) -> MetalLease:
        """Wait for current field work, ahead of every pending field request."""

        lock = self._loop_lock()
        loop = asyncio.get_running_loop()
        token = object()
        future: asyncio.Future[None] = loop.create_future()
        async with lock:
            self._solve_waiters.append((token, future))
            self._advance_locked()
        try:
            await future
        except asyncio.CancelledError:
            async with lock:
                if self._solve_owner is token:
                    self._solve_owner = None
                else:
                    self._solve_waiters = deque(
                        item for item in self._solve_waiters if item[0] is not token
                    )
                self._advance_locked()
            raise
        return MetalLease(self, "solve", token)

    async def acquire_field(
        self, request_id: str, *, solve_queued: bool = False
    ) -> MetalLease:
        """Acquire or enter the latest-wins field slot.

        ``solve_queued`` lets the scheduler's durable FIFO state close the
        small gap before its own solve waiter reaches this permit.
        """

        lock = self._loop_lock()
        loop = asyncio.get_running_loop()
        token = object()
        future: asyncio.Future[None] = loop.create_future()
        waiter = _FieldWaiter(request_id=request_id, token=token, future=future)
        async with lock:
            if solve_queued or self.solve_active_or_waiting:
                raise FieldEvaluationBusy("a solve is running or queued")
            if self._field_owner is None:
                self._field_owner = token
                future.set_result(None)
            else:
                superseded = self._pending_field
                self._pending_field = waiter
                if superseded is not None and not superseded.future.done():
                    superseded.future.set_exception(
                        FieldEvaluationSuperseded(
                            superseded.request_id,
                            request_id,
                        )
                    )
        try:
            await future
        except asyncio.CancelledError:
            async with lock:
                if self._pending_field is waiter:
                    self._pending_field = None
                elif self._field_owner is token:
                    self._field_owner = None
                    self._advance_locked()
            raise
        return MetalLease(self, "field", token)

    async def _release(self, kind: str, token: object) -> None:
        lock = self._loop_lock()
        async with lock:
            if kind == "solve":
                if self._solve_owner is not token:
                    raise RuntimeError("solve Metal lease is not active")
                self._solve_owner = None
            elif kind == "field":
                if self._field_owner is not token:
                    raise RuntimeError("field Metal lease is not active")
                self._field_owner = None
            else:  # pragma: no cover - MetalLease constructs only known kinds.
                raise RuntimeError(f"unknown Metal lease kind {kind!r}")
            self._advance_locked()

    def _advance_locked(self) -> None:
        if self._solve_owner is not None or self._field_owner is not None:
            return
        while self._solve_waiters:
            token, future = self._solve_waiters.popleft()
            if future.cancelled():
                continue
            self._solve_owner = token
            future.set_result(None)
            return
        pending, self._pending_field = self._pending_field, None
        if pending is not None and not pending.future.cancelled():
            self._field_owner = pending.token
            pending.future.set_result(None)

    @asynccontextmanager
    async def solve(self) -> AsyncIterator[None]:
        lease = await self.acquire_solve()
        try:
            yield
        finally:
            await lease.release()


_PROCESS_METAL_PERMIT = MetalPermit()


def process_metal_permit() -> MetalPermit:
    """Return the one Metal arbitration lane shared by this server process."""

    return _PROCESS_METAL_PERMIT


__all__ = [
    "FieldEvaluationBusy",
    "FieldEvaluationSuperseded",
    "MetalLease",
    "MetalPermit",
    "process_metal_permit",
]
