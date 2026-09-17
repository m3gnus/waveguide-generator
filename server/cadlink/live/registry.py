"""The live CAD Link sessions of one WG start, kept in memory only.

One :class:`LiveRegistry` exists per resolved data directory, tagged with the
``instanceId`` of the start that created it. Startup installs a new one and
replaces whatever was there; shutdown removes it only if it is still its own.
Every reader goes through :func:`registry_for`, so two applications on
different data directories never see each other's sessions, and an
application that has not started, or has stopped, has no live state at all.

The registry also keeps the latest heartbeat the add-in posted over HTTP,
bound to the session that posted it: a session that ends, is superseded or
expires takes its heartbeat with it.

Nothing here is persisted: a WG restart forgets every session, token, nonce
and live heartbeat.
A session token is never stored. The registry keeps ``sha256(token)``, finds a
session by that digest, and confirms the match with ``hmac.compare_digest``.
(docs/reference/CADLINK-LIVE-PROTOCOL.md, "Live registry, sessions and tokens")
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Mapping
import uuid


SESSION_LIFETIME_SECONDS = 15 * 60
REFRESH_AFTER_SECONDS = 10 * 60
REFRESH_GRACE_SECONDS = 30
IDLE_TIMEOUT_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 4
LONG_POLL_SECONDS = 25

SESSION_UNKNOWN = "session_unknown"
TOKEN_EXPIRED = "token_expired"
SESSION_SUPERSEDED = "session_superseded"
INSTALLATION_MISMATCH = "installation_mismatch"

HEARTBEAT_RECORDED = "recorded"
HEARTBEAT_OLDER = "older"

#: Monotonic seconds for every validity decision (lifetime, refresh grace,
#: idle), so a wall-clock change cannot extend or cut a session. Module-level so
#: a test can move time.
_now = time.monotonic
#: Wall-clock seconds, only for the ``expiresAt``/``refreshAfter`` values reported.
_wall = time.time


class LiveAuthError(Exception):
    """A session token that does not authenticate; ``code`` names why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass
class LiveSession:
    live_session_id: str
    installation_id: str
    adapter_session_id: str
    adapter_version: str
    loaded_identity: dict[str, Any]
    registered_at: float
    token_digest: bytes
    #: Monotonic deadlines (``_now``).
    expires_at: float
    refresh_after: float
    #: The same deadlines as wall-clock seconds, for reporting only.
    expires_at_wall: float
    refresh_after_wall: float
    last_seen: float
    #: The token a refresh replaced, valid until ``previous_valid_until``.
    previous_digest: bytes | None = None
    previous_valid_until: float = 0.0


@dataclass
class LiveRegistry:
    instance_id: str
    secret: str = field(repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _sessions: dict[str, LiveSession] = field(default_factory=dict, repr=False)
    #: installation id -> live session id; one session per installation.
    _by_installation: dict[str, str] = field(default_factory=dict, repr=False)
    #: token digest -> live session id, for current and grace tokens.
    _by_digest: dict[bytes, str] = field(default_factory=dict, repr=False)
    #: digests of superseded sessions' tokens -> when they would have expired.
    _superseded: dict[bytes, float] = field(default_factory=dict, repr=False)
    _used_nonces: set[bytes] = field(default_factory=set, repr=False)
    #: (live session id, its ``updatedAt``, payload) of the latest live heartbeat, or None.
    _heartbeat: tuple[str, datetime, dict[str, Any]] | None = field(default=None, repr=False)

    @classmethod
    def create(cls) -> "LiveRegistry":
        return cls(instance_id=uuid.uuid4().hex, secret=secrets.token_urlsafe(32))

    # -- nonces ---------------------------------------------------------------

    def claim_nonce(self, nonce: bytes) -> bool:
        """Record a verified nonce; False when this instance has seen it already."""

        with self._lock:
            if nonce in self._used_nonces:
                return False
            self._used_nonces.add(nonce)
            return True

    # -- sessions -------------------------------------------------------------

    def register(
        self,
        *,
        installation_id: str,
        adapter_session_id: str,
        adapter_version: str,
        loaded_identity: Mapping[str, Any],
    ) -> tuple[LiveSession, str]:
        """A new session for the installation, superseding its previous one."""

        now = _now()
        wall = _wall()
        token = secrets.token_urlsafe(32)
        digest = token_digest(token)
        session = LiveSession(
            live_session_id=uuid.uuid4().hex,
            installation_id=installation_id,
            adapter_session_id=adapter_session_id,
            adapter_version=adapter_version,
            loaded_identity=dict(loaded_identity),
            registered_at=now,
            token_digest=digest,
            expires_at=now + SESSION_LIFETIME_SECONDS,
            refresh_after=now + REFRESH_AFTER_SECONDS,
            expires_at_wall=wall + SESSION_LIFETIME_SECONDS,
            refresh_after_wall=wall + REFRESH_AFTER_SECONDS,
            last_seen=now,
        )
        with self._lock:
            self._prune(now)
            previous_id = self._by_installation.get(installation_id)
            previous = self._sessions.get(previous_id) if previous_id else None
            if previous is not None:
                self._drop(previous, superseded=True)
            self._sessions[session.live_session_id] = session
            self._by_installation[installation_id] = session.live_session_id
            self._by_digest[digest] = session.live_session_id
        return session, token

    def authenticate(self, token: str, installation_id: str | None) -> LiveSession:
        """The session this token and installation header authenticate, or raise."""

        now = _now()
        digest = token_digest(token)
        with self._lock:
            session_id = self._by_digest.get(digest)
            session = self._sessions.get(session_id) if session_id is not None else None
            if session is None:
                expiry = self._superseded.get(digest)
                if expiry is not None and expiry > now:
                    raise LiveAuthError(SESSION_SUPERSEDED)
                raise LiveAuthError(SESSION_UNKNOWN)
            current = hmac.compare_digest(session.token_digest, digest)
            grace = (
                not current
                and session.previous_digest is not None
                and hmac.compare_digest(session.previous_digest, digest)
            )
            if not current and not grace:
                raise LiveAuthError(SESSION_UNKNOWN)
            if installation_id is None or not hmac.compare_digest(
                session.installation_id.encode("utf-8"), installation_id.encode("utf-8")
            ):
                raise LiveAuthError(INSTALLATION_MISMATCH)
            if self._expired(session, now):
                self._drop(session, superseded=False)
                raise LiveAuthError(TOKEN_EXPIRED)
            if grace and now > session.previous_valid_until:
                self._by_digest.pop(digest, None)
                session.previous_digest = None
                raise LiveAuthError(TOKEN_EXPIRED)
            session.last_seen = now
            return session

    def refresh(self, session: LiveSession, presented_digest: bytes) -> str:
        """A new token for the session; the replaced one stays valid briefly.

        Only the current token refreshes. A token in its grace window still
        authenticates other requests, but cannot mint another token.
        """

        now = _now()
        wall = _wall()
        token = secrets.token_urlsafe(32)
        digest = token_digest(token)
        with self._lock:
            if self._sessions.get(session.live_session_id) is not session:
                raise LiveAuthError(SESSION_UNKNOWN)
            if not hmac.compare_digest(session.token_digest, presented_digest):
                raise LiveAuthError(TOKEN_EXPIRED)
            if session.previous_digest is not None:
                self._by_digest.pop(session.previous_digest, None)
            session.previous_digest = session.token_digest
            session.previous_valid_until = min(now + REFRESH_GRACE_SECONDS, session.expires_at)
            session.token_digest = digest
            session.expires_at = now + SESSION_LIFETIME_SECONDS
            session.refresh_after = now + REFRESH_AFTER_SECONDS
            session.expires_at_wall = wall + SESSION_LIFETIME_SECONDS
            session.refresh_after_wall = wall + REFRESH_AFTER_SECONDS
            session.last_seen = now
            self._by_digest[digest] = session.live_session_id
        return token

    def still_current(self, session: LiveSession, presented_digest: bytes) -> str | None:
        """Why ``session`` no longer authenticates, or None while it still does.

        For a request that authenticated and then waited (a long poll): the
        same codes :meth:`authenticate` answers, without counting as activity.
        """

        now = _now()
        with self._lock:
            if self._sessions.get(session.live_session_id) is not session:
                expiry = self._superseded.get(presented_digest)
                if expiry is not None and expiry > now:
                    return SESSION_SUPERSEDED
                return SESSION_UNKNOWN
            if self._expired(session, now):
                return TOKEN_EXPIRED
            current = hmac.compare_digest(session.token_digest, presented_digest)
            grace = session.previous_digest is not None and hmac.compare_digest(
                session.previous_digest, presented_digest
            )
            if not current and not (grace and now <= session.previous_valid_until):
                return TOKEN_EXPIRED
            return None

    def end(self, session: LiveSession) -> None:
        with self._lock:
            if self._sessions.get(session.live_session_id) is session:
                self._drop(session, superseded=False)

    # -- heartbeat ------------------------------------------------------------

    def record_heartbeat(
        self, session: LiveSession, payload: Mapping[str, Any], updated_at: datetime
    ) -> str | None:
        """Keep ``payload`` (whose ``updatedAt`` is ``updated_at``) as the live heartbeat.

        Returns :data:`HEARTBEAT_RECORDED`; :data:`HEARTBEAT_OLDER` when the
        heartbeat kept already has a later ``updatedAt`` (a request that arrived
        late; the newer one stays, nothing is kept); ``None`` when the session is
        no longer current here (it ended, was superseded or expired after it
        authenticated), and nothing is kept.
        """

        now = _now()
        kept = copy.deepcopy(dict(payload))
        with self._lock:
            if self._sessions.get(session.live_session_id) is not session or self._expired(session, now):
                return None
            if self._heartbeat is not None and updated_at < self._heartbeat[1]:
                return HEARTBEAT_OLDER
            self._heartbeat = (session.live_session_id, updated_at, kept)
            return HEARTBEAT_RECORDED

    def current_heartbeat(self) -> dict[str, Any] | None:
        """The latest live heartbeat, while the session that posted it is current.

        Freshness of the payload itself is the reader's check
        (``fusion_status.select_heartbeat``), the same as for the file.
        """

        now = _now()
        with self._lock:
            if self._heartbeat is None:
                return None
            session_id, _updated_at, payload = self._heartbeat
            session = self._sessions.get(session_id)
            if session is None or self._expired(session, now):
                return None
        # A copy: no reader can change what the registry holds.
        return copy.deepcopy(payload)

    def loaded_identity(self) -> dict[str, Any] | None:
        """What the most recently registered session that is still valid loaded."""

        now = _now()
        with self._lock:
            valid = [s for s in self._sessions.values() if not self._expired(s, now)]
            if not valid:
                return None
            latest = max(valid, key=lambda s: s.registered_at)
            return dict(latest.loaded_identity)

    # -- internals (lock held) --------------------------------------------------

    @staticmethod
    def _expired(session: LiveSession, now: float) -> bool:
        return now >= session.expires_at or now - session.last_seen > IDLE_TIMEOUT_SECONDS

    def _drop(self, session: LiveSession, *, superseded: bool) -> None:
        self._sessions.pop(session.live_session_id, None)
        if self._heartbeat is not None and self._heartbeat[0] == session.live_session_id:
            self._heartbeat = None
        if self._by_installation.get(session.installation_id) == session.live_session_id:
            self._by_installation.pop(session.installation_id, None)
        for digest in (session.token_digest, session.previous_digest):
            if digest is None:
                continue
            self._by_digest.pop(digest, None)
            if superseded:
                self._superseded[digest] = session.expires_at

    def _prune(self, now: float) -> None:
        for session in [s for s in self._sessions.values() if self._expired(s, now)]:
            self._drop(session, superseded=False)
        for digest in [d for d, expiry in self._superseded.items() if expiry <= now]:
            self._superseded.pop(digest, None)


_REGISTRIES: dict[Path, LiveRegistry] = {}
_REGISTRIES_LOCK = threading.Lock()


def _key(data_dir: Path | str) -> Path:
    return Path(data_dir).resolve()


def install_registry(data_dir: Path | str, registry: LiveRegistry) -> None:
    """Make ``registry`` the one for this data directory, replacing any other."""

    with _REGISTRIES_LOCK:
        _REGISTRIES[_key(data_dir)] = registry


def remove_registry_if_ours(data_dir: Path | str, instance_id: str) -> bool:
    with _REGISTRIES_LOCK:
        key = _key(data_dir)
        current = _REGISTRIES.get(key)
        if current is None or current.instance_id != instance_id:
            return False
        del _REGISTRIES[key]
        return True


def registry_for(data_dir: Path | str | None) -> LiveRegistry | None:
    if data_dir is None:
        return None
    with _REGISTRIES_LOCK:
        return _REGISTRIES.get(_key(data_dir))


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "HEARTBEAT_OLDER",
    "HEARTBEAT_RECORDED",
    "IDLE_TIMEOUT_SECONDS",
    "INSTALLATION_MISMATCH",
    "LONG_POLL_SECONDS",
    "LiveAuthError",
    "LiveRegistry",
    "LiveSession",
    "REFRESH_AFTER_SECONDS",
    "REFRESH_GRACE_SECONDS",
    "SESSION_LIFETIME_SECONDS",
    "SESSION_SUPERSEDED",
    "SESSION_UNKNOWN",
    "TOKEN_EXPIRED",
    "install_registry",
    "registry_for",
    "remove_registry_if_ours",
    "token_digest",
]
