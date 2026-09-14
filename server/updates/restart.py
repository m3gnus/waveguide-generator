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

Each approval has an id, and a release for an approval a newer one replaced
changes nothing. When one comes down, the writer that approved it takes its
request back (:func:`revoke_request`) before it reports that the update did
not start, so a launcher that comes back late finds nothing to hand off.

A handoff that fails after the launcher has stopped the server restarts it, and
the new process starts unlatched. A process that exits takes the latch with it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time
from typing import TypeVar


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


@dataclass(frozen=True)
class RestartRelease:
    """An approved restart that came down: which approval, why, and whether it expired."""

    target: str
    reason: str
    #: The id ``approve`` returned for it.
    approval: int
    #: It expired, rather than being released by someone who knows no handoff
    #: is coming: the launcher may have taken the request for a handoff.
    expired: bool


#: Told a :class:`RestartRelease` when an approved restart is called off.
ReleaseObserver = Callable[[RestartRelease], object]

#: A handoff request nobody took is renamed to ``<request><REVOKED_SUFFIX>``.
REVOKED_SUFFIX = ".revoked"
_REVOKE_ATTEMPTS = 10
_REVOKE_RETRY_DELAY = 0.05

_Started = TypeVar("_Started")

log = logging.getLogger("wg.updates")


def revoke_request(path: Path) -> bool:
    """Rename a handoff request out of the launcher's reach. Returns whether it was still there.

    Atomic against the launcher, which reads a request and then deletes it: a
    delete that finds the file gone hands off nothing (``consume_update_request``
    in ``launchers/statusapp/updater.py``, and the v0.3.1 and v0.3.2 launchers
    alike). ``False`` means the launcher already took it, so a handoff may be
    under way -- or, rarely, that it could not be renamed (Windows refuses
    while the launcher has it open), and then it may still be taken.
    """

    request = Path(path)
    revoked = request.with_name(request.name + REVOKED_SUFFIX)
    failure: OSError | None = None
    for attempt in range(_REVOKE_ATTEMPTS):
        if attempt:
            time.sleep(_REVOKE_RETRY_DELAY)
        try:
            os.replace(request, revoked)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            failure = exc
    log.warning("Could not revoke the update request %s (%s); it may still be taken", request, failure)
    return False


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
        #: The id of the latest approval; each ``approve`` takes the next one.
        self._approval = 0
        self._listeners: list[ReleaseListener] = []
        self._observers: list[ReleaseObserver] = []

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

    def add_release_observer(self, observer: ReleaseObserver) -> None:
        """Be told which approval came down, and whether it expired (:class:`RestartRelease`)."""

        with self._lock:
            self._observers.append(observer)

    def approve(self, target: str) -> int:
        """Latch: ``target`` names what the restart installs, for the refusal message.

        Returns this approval's id. A writer that later releases it passes the
        id, so a release that arrives after a newer approval changes nothing.
        """

        with self._lock:
            self._approval += 1
            approval = self._approval
            self._target = target
            self._approved_at = self._clock()
        log.info(
            "Restart approved to install %s; new solves are refused until it happens",
            target,
        )
        return approval

    def current(self) -> int | None:
        """The id of the approval that is pending, or ``None``. Expiry is not checked."""

        with self._lock:
            return self._approval if self._target is not None else None

    def remaining(self) -> float | None:
        """Seconds until the approved restart expires, or ``None`` when none is pending."""

        with self._lock:
            if self._target is None:
                return None
            return max(0.0, self._ttl - (self._clock() - self._approved_at))

    def admit(self, start: Callable[[], _Started]) -> tuple[bool, _Started | None]:
        """Run ``start`` unless a restart is approved, under the lock ``approve`` takes.

        The job runtime marks a job running through this (contract §4.3), so a
        job start and an approval are ordered: once ``approve`` has returned, no
        job is marked running. ``start`` must be short and must not read this
        latch. Returns ``(False, None)`` when a restart is pending.
        """

        self.expire_if_due()
        with self._lock:
            if self._target is not None:
                return False, None
            return True, start()

    def expire_if_due(self) -> bool:
        """Release a latch older than ``RESTART_APPROVAL_TTL``. Returns whether it did."""

        with self._lock:
            due = self._target is not None and self._clock() - self._approved_at >= self._ttl
            approval = self._approval
        if not due:
            return False
        return self._release(
            f"no handoff followed within {self._ttl:g} s of the approval, so none is pending",
            approval=approval,
            expired=True,
        )

    def release(self, reason: str, approval: int | None = None) -> bool:
        """Clear the latch because no handoff is pending any more.

        ``approval`` is the id ``approve`` returned; a release for an approval
        that a newer one replaced changes nothing. Without it, whatever is
        pending is released: the launcher's notice knows no id, and only one
        approval is ever pending. Returns whether it came down. Releasing an
        unset latch is harmless, so every failure path may call this without
        first asking. Listeners are told only when a set latch comes down, and
        one line is logged then.
        """

        return self._release(reason, approval=approval, expired=False)

    def _release(self, reason: str, *, approval: int | None, expired: bool) -> bool:
        with self._lock:
            if approval is not None and approval != self._approval:
                # A newer approval replaced the one this release is about.
                return False
            target, self._target = self._target, None
            released = self._approval
            listeners = list(self._listeners)
            observers = list(self._observers)
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
        event = RestartRelease(target, reason, released, expired)
        for observer in observers:
            try:
                observer(event)
            except Exception:  # noqa: BLE001 - the latch is down whatever an observer does
                log.exception("A restart release observer failed")
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
    "REVOKED_SUFFIX",
    "UPDATE_RESTART_PENDING",
    "ReleaseListener",
    "ReleaseObserver",
    "RestartApproval",
    "RestartRelease",
    "revoke_request",
]
