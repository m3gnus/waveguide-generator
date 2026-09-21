"""What WG's request consumer is doing, for the user and not only the log.

M1 transfer contract, C4: an accepted request must never sit silently in
``received``. The consumer -- the backend's delivery loop, which reads the WG
request inbox and starts what it accepted -- can be off, can decline to
advance (no WGLink folder, an approved update restart), or can hang inside a
pass, and from outside all of those look exactly like an idle consumer. This
records which, and the time of the last pass that completed, so the CAD Link
panel can say so. It also keeps the recent refusals of taken files that have
no operation row of their own (a malformed request, a conflict), bounded.

One per application, in ``app.state.cad_delivery_status``. Written from the
delivery loop and the inbox reader's thread, read by the status route.
"""

from __future__ import annotations

from collections import deque
import threading
from typing import Any, Mapping

from .identity import utc_now

#: The consumer has not been started (an application assembled without its
#: start-up, or a test driving passes itself).
NOT_STARTED = "not_started"
RUNNING = "running"
#: ``WG2_CAD_DELIVERY=0``: WG does not collect requests at all.
DISABLED = "disabled"
#: The loop ended (shutdown).
STOPPED = "stopped"
#: How many recent refusals the panel is given.
RECENT_REFUSALS = 20


class DeliveryStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumer = NOT_STARTED
        self._declined: str | None = None
        self._pass_started_at: str | None = None
        self._last_pass_completed_at: str | None = None
        self._refusals: deque[dict[str, Any]] = deque(maxlen=RECENT_REFUSALS)

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self, f"_{name}", value)

    def running(self) -> bool:
        with self._lock:
            return self._consumer == RUNNING

    def disabled(self) -> None:
        self._set(consumer=DISABLED)

    def started(self) -> None:
        self._set(consumer=RUNNING)

    def stopped(self) -> None:
        self._set(consumer=STOPPED)

    def pass_started(self) -> None:
        self._set(pass_started_at=utc_now())

    def pass_finished(self, declined: str | None) -> None:
        self._set(declined=declined, last_pass_completed_at=utc_now())

    def pass_failed(self, error: str) -> None:
        # Not a completed pass: liveness stays where the last good one left it.
        self._set(declined=f"The last delivery pass failed: {error}")

    def refused(self, refusal: Mapping[str, Any]) -> None:
        with self._lock:
            self._refusals.appendleft(dict(refusal))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "consumer": self._consumer,
                "declined": self._declined,
                "passStartedAt": self._pass_started_at,
                "lastPassCompletedAt": self._last_pass_completed_at,
                "recentRefusals": list(self._refusals),
            }


def delivery_status(state: Any) -> DeliveryStatus:
    """The application's status, made on first use for an app assembled piecemeal."""

    status = getattr(state, "cad_delivery_status", None)
    if status is None:
        status = DeliveryStatus()
        state.cad_delivery_status = status
    return status


__all__ = [
    "DISABLED",
    "DeliveryStatus",
    "NOT_STARTED",
    "RECENT_REFUSALS",
    "RUNNING",
    "STOPPED",
    "delivery_status",
]
