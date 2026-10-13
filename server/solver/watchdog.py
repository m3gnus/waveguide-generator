"""Observe native work without cancelling the coroutine that owns its thread.

A deadline cannot terminate an in-process native call. On expiry, quarantine the
process admission lane until restart and retain the operation until it exits.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import math
import os
import time
from typing import TypeVar

from .metal_permit import MetalPermit

T = TypeVar("T")
STALL_TIMEOUT_ENV = "WG2_NATIVE_STALL_TIMEOUT_SECONDS"
DEFAULT_STALL_TIMEOUT_SECONDS = 2 * 60 * 60.0


def stall_timeout_seconds() -> float:
    raw = os.environ.get(STALL_TIMEOUT_ENV, "")
    value = float(raw) if raw else DEFAULT_STALL_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{STALL_TIMEOUT_ENV} must be finite and positive")
    return value


async def watch_native_operation(
    operation: Awaitable[T], *, permit: MetalPermit,
    retained: set[asyncio.Task], last_progress: Callable[[], float],
    timeout_seconds: float, stop_callbacks: Callable[[], None],
) -> T:
    work = asyncio.create_task(operation, name="wg2-native-operation")
    retained.add(work)

    def finished(task: asyncio.Task) -> None:
        retained.discard(task)
        if not task.cancelled():
            task.exception()  # observe late failures after the caller has left

    work.add_done_callback(finished)
    try:
        while True:
            remaining = timeout_seconds - (time.monotonic() - last_progress())
            done, _ = await asyncio.wait({work}, timeout=max(0, remaining))
            if done:
                return work.result()
            if time.monotonic() - last_progress() >= timeout_seconds:
                stop_callbacks()
                permit.quarantine(
                    "Native work may still be running. Restart the application before "
                    "running another solve or field evaluation."
                )
                raise TimeoutError(
                    f"Native solver made no progress for {timeout_seconds:g} seconds. "
                    "Restart the application before running another solve or field evaluation."
                )
    except asyncio.CancelledError:
        stop_callbacks()
        if not work.done():
            permit.quarantine(
                "Native work may still be running. Restart the application before "
                "running another solve or field evaluation."
            )
        raise
