"""The restart-approved latch (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.2).

A restart is approved at the moment the handoff request is written (§4.1): by
``BundleUpdateInstaller`` for a bundle, by ``UpdateService.request_install``
for a checkout. From then until this process exits, nothing may start work the
restart would end, so the routes that start installation-owned work refuse
while the latch is set.

The latch lives in this process on purpose, not in the request file. The
launcher deletes the file as it consumes it, before it stops the server, so a
check of the file would reopen exactly while the server is shutting down.

It is released in two cases, and only these:

* the attempt fails before the handoff request exists (the writer releases it);
* the launcher discards a request instead of handing off, and says so over the
  status control channel (``launch/serve.py`` ``_watch_statusapp``).

A handoff that fails after the launcher has stopped the server restarts it, and
the new process starts unlatched. A process that exits takes the latch with it.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading


#: The error code a refused submission carries (``error.code``).
UPDATE_RESTART_PENDING = "update_restart_pending"

#: Told ``(target, reason)`` when an approved restart is called off.
ReleaseListener = Callable[[str, str], object]

log = logging.getLogger("wg.updates")


class RestartApproval:
    """Whether this server has approved a restart that has not happened yet."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target: str | None = None
        self._listeners: list[ReleaseListener] = []

    @property
    def pending(self) -> str | None:
        """What the approved restart installs, or ``None`` when none is pending."""

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
        log.info(
            "Restart approved to install %s; new solves are refused until it happens",
            target,
        )

    def release(self, reason: str) -> bool:
        """Clear the latch because no handoff is pending any more.

        Returns whether it was set. Releasing an unset latch is harmless, so
        every failure path may call this without first asking. Listeners are
        told only when a set latch comes down.
        """

        with self._lock:
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


__all__ = ["UPDATE_RESTART_PENDING", "ReleaseListener", "RestartApproval"]
