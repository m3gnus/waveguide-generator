"""Run a startup warmup off the startup path.

Two caches are expensive to fill and are filled lazily by whichever request
arrives first: the mesher import graph and the engine capability probe. Both
were being paid by the user's first interaction rather than by the seconds the
shell spends painting.

Awaiting them in a startup handler would only move the stall -- Uvicorn does
not accept connections until startup completes. So each warmup runs as a
background task, and a request that beats it simply waits on the same lock it
would have taken anyway.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time


log = logging.getLogger("wg.warmup")

#: How long shutdown waits for a warmup to finish before giving up on it.
#:
#: A warmup is an optimisation, so quitting must never wait on one for long.
#: One second still covers both real warmups comfortably in the normal case
#: (39 ms for the mesher import, 260 ms for the engine probe, and 500-950 ms
#: for the probe when it has to import Metal and BEMPP cold), while capping
#: what a "start it, quit immediately" run can cost at roughly a second
#: instead of five.
DRAIN_TIMEOUT_SECONDS = 1.0


class BackgroundWarmup:
    """One named warmup task, started at boot and drained at shutdown."""

    def __init__(self, name: str, work: Callable[[], Awaitable[object]]) -> None:
        self._name = name
        self._work = work
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        """The in-flight task, for tests and diagnostics."""

        return self._task

    async def _run(self) -> None:
        began = time.monotonic()
        try:
            await self._work()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A warmup is an optimisation. Whatever failed here will fail again
            # at the real call site, where the error message belongs.
            log.info("Warmup %s did not complete: %s", self._name, exc)
            return
        log.info("Warmup %s finished in %.0f ms", self._name, (time.monotonic() - began) * 1000)

    async def start(self) -> None:
        """Schedule the warmup. Never blocks, and never stacks duplicates."""

        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"wg2-warmup-{self._name}")

    async def stop(self, drain_timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """Finish a warmup still running when the server stops.

        Draining rather than cancelling outright is deliberate. Both warmups do
        their work inside ``asyncio.to_thread``, and cancelling the task does
        not stop the thread -- it only abandons the result, leaving the import
        or the probe running against an executor the loop is about to tear
        down. Warmups are short (measured 39 ms for the mesher import, 260 ms
        for the engine probe), so waiting for real quiescence is cheap.

        The timeout is the backstop for a warmup that wedges: past it
        ``wait_for`` cancels the task, reaps it, and shutdown proceeds.
        """

        task = self._task
        if task is None or task.done():
            self._task = None
            return
        try:
            await asyncio.wait_for(task, drain_timeout)
        except TimeoutError:
            # wait_for has already cancelled and awaited the task by now.
            log.warning(
                "Warmup %s did not drain in %.1fs; cancelled so shutdown can proceed",
                self._name,
                drain_timeout,
            )
        # Deliberately not a `finally`: if the caller itself is cancelled
        # mid-drain the handle stays put, so a later stop() can still reap the
        # task rather than losing it.
        self._task = None


__all__ = ["BackgroundWarmup"]
