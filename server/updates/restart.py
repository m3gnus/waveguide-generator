"""The restart-approved latch (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.2).

A restart is approved at the moment the handoff request is written (§4.1): by
``BundleUpdateInstaller`` for a bundle, by ``UpdateService.request_install``
for a checkout. From then until this process exits, nothing may start work the
restart would end, so the routes that start installation-owned work refuse
while the latch is set.

The latch lives in this process on purpose, not in the request file. The
launcher deletes the file as it consumes it, before it stops the server, so a
check of the file would reopen exactly while the server is shutting down.

It comes down in three cases:

* the attempt fails before the handoff request exists (the writer releases it);
* the launcher discards a request instead of handing off, and says so over the
  status control channel (``launch/serve.py`` ``_watch_statusapp``);
* it expires, ``RESTART_APPROVAL_TTL`` after it was set, if this process is
  still running. This is the backstop for a launcher that could not deliver
  its notice.

A handoff that fails after the launcher has stopped the server restarts it, and
the new process starts unlatched. A process that exits takes the latch with it.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time


#: The error code a refused submission carries (``error.code``).
UPDATE_RESTART_PENDING = "update_restart_pending"

#: How long an approved restart may wait for its handoff before the latch
#: expires, in seconds. A real handoff is over in seconds: the launcher
#: consumes the request on its next tick (a quarter of a second in the status
#: window, one second in the desktop window, plus the 0.75 s a checkout request
#: waits for its HTTP answer to arrive), then stops the server within its
#: shutdown timeout (8 s, then the process tree is killed). A server still
#: running five minutes after approval therefore has no handoff pending. Five
#: minutes rather than one leaves room for a machine that is paging or
#: suspended, at the cost of refusing solves that long in the rare case that
#: the launcher could not say it discarded the request.
RESTART_APPROVAL_TTL = 300.0

#: Told ``(target, reason)`` when an approved restart is called off.
ReleaseListener = Callable[[str, str], object]

log = logging.getLogger("wg.updates")


class RestartApproval:
    """Whether this server has approved a restart that has not happened yet.

    Expiry is checked whenever the latch is read -- by a refusing route, the
    update status and the install status -- so it needs no thread of its own.
    """

    def __init__(
        self,
        *,
        ttl: float = RESTART_APPROVAL_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._target: str | None = None
        self._approved_at = 0.0
        self._listeners: list[ReleaseListener] = []

    @property
    def pending(self) -> str | None:
        """What the approved restart installs, or ``None`` when none is pending."""

        self.expire_if_due()
        with self._lock:
            return self._target

    def add_release_listener(self, listener: ReleaseListener) -> None:
        """Be told when a restart is called off, so the update dialog can say why."""

        with self._lock:
            self._listeners.append(listener)

    def approve(self, target: str) -> None:
        """Latch: ``target`` names what the restart installs, for the refusal message."""

        with self._lock:
            self._target = target
            self._approved_at = self._clock()
        log.info(
            "Restart approved to install %s; new solves are refused until it happens",
            target,
        )

    def expire_if_due(self) -> bool:
        """Release a latch older than ``RESTART_APPROVAL_TTL``. Returns whether it did."""

        with self._lock:
            due = self._target is not None and self._clock() - self._approved_at >= self._ttl
            approved_at = self._approved_at
        if not due:
            return False
        return self._release(
            f"no handoff followed within {self._ttl:g} s of the approval, so none is pending",
            approved_at=approved_at,
        )

    def release(self, reason: str) -> bool:
        """Clear the latch because no handoff is pending any more.

        Returns whether it was set. Releasing an unset latch is harmless, so
        every failure path may call this without first asking. Listeners are
        told only when a set latch comes down, and one line is logged then.
        """

        return self._release(reason)

    def _release(self, reason: str, *, approved_at: float | None = None) -> bool:
        with self._lock:
            if approved_at is not None and approved_at != self._approved_at:
                # A new approval arrived after this one was found due.
                return False
            target, self._target = self._target, None
            listeners = list(self._listeners)
        if target is None:
            return False
        log.warning(
            "The restart to install %s was called off (%s); new solves are accepted again",
            target,
            reason,
        )
        for listener in listeners:
            try:
                listener(target, reason)
            except Exception:  # noqa: BLE001 - the latch is down whatever a listener does
                log.exception("A restart release listener failed")
        return True

    def refusal(self) -> str | None:
        """The message a refused request carries, or ``None`` when nothing is pending."""

        target = self.pending
        if target is None:
            return None
        return (
            f"Waveguide Generator is about to restart to install {target}, so it is not "
            "starting new solves. Submit this again after the restart."
        )


__all__ = [
    "RESTART_APPROVAL_TTL",
    "UPDATE_RESTART_PENDING",
    "ReleaseListener",
    "RestartApproval",
]
