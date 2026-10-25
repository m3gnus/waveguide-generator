"""Subscriber queues for durable lifecycle events and provisional results."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping


class EventBroker:
    """Fan durable lifecycle events and ephemeral result deltas to subscribers."""

    def __init__(self, *, queue_size: int = 256) -> None:
        self._latest: tuple[dict[str, Any], float] | None = None
        self.queue_size = max(1, queue_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def has_subscribers(self) -> bool:
        """A display can hear ephemeral navigation; keep its pointer until then."""
        return bool(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        if self._latest is not None:
            message, expires_at = self._latest
            if time.time() <= expires_at:
                queue.put_nowait(dict(message))
            else:
                self._latest = None
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish_latest(self, event: Mapping[str, Any], *, expires_at: float) -> None:
        """Publish now and replay the latest navigation once to each new subscriber."""
        self._latest = (dict(event), expires_at)
        self.publish(event)

    def publish(self, event: Mapping[str, Any]) -> None:
        message = dict(event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A hole forces the client reconciliation rule. Drop the oldest
                # buffered event so the subscriber can observe the cursor gap.
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
