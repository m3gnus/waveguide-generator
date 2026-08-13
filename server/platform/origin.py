"""Shared loopback-only HTTP/WebSocket Origin validation."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection
from urllib.parse import urlsplit


def local_origin(origin: str) -> bool:
    """Accept only local HTTP(S) origins with a syntactically valid port."""

    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return False
        host = parsed.hostname.rstrip(".").lower()
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_request_host(host_header: str | None, *, scheme: str, bound_port: object) -> bool:
    """Accept a loopback Host authority only for the socket that received it."""

    if not host_header:
        return False
    try:
        parsed = urlsplit(f"//{host_header}")
        expected_port = int(bound_port)
        effective_port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or not 1 <= expected_port <= 65535
    ):
        return False
    if effective_port is None:
        effective_port = 443 if scheme in {"https", "wss"} else 80
    return effective_port == expected_port and local_origin(
        f"{'https' if scheme in {'https', 'wss'} else 'http'}://{host_header}"
    )


def parse_extra_websocket_origins(raw: str | None) -> frozenset[str]:
    """Parse the opt-in WS origin list, silently dropping non-loopback entries."""

    if not raw:
        return frozenset()
    candidates = (entry.strip() for entry in raw.split(","))
    return frozenset(origin for origin in candidates if origin and local_origin(origin))


def request_origin_allowed(
    *,
    origin: str | None,
    host: str | None,
    scheme: str,
    bound_port: object,
    extra_origins: Collection[str] = (),
) -> bool:
    """Require the request's exact authority or an opted-in local Origin."""

    if origin is None:
        return True
    if origin in extra_origins and local_origin(origin):
        return True
    try:
        origin_url = urlsplit(origin)
        host_url = urlsplit(f"//{host}")
        origin_port = origin_url.port
        if origin_port is None:
            origin_port = 443 if origin_url.scheme == "https" else 80
        origin_host = (origin_url.hostname or "").rstrip(".").lower()
        target_host = (host_url.hostname or "").rstrip(".").lower()
        expected_port = int(bound_port)
    except (TypeError, ValueError):
        return False
    expected_origin_scheme = "https" if scheme in {"https", "wss"} else "http"
    return (
        local_origin(origin)
        and origin_url.scheme == expected_origin_scheme
        and origin_host == target_host
        and origin_port == expected_port
        and not origin_url.path
        and not origin_url.query
        and not origin_url.fragment
    )


def websocket_request_allowed(
    *,
    origin: str | None,
    host: str | None,
    scheme: str,
    bound_port: object,
    extra_origins: Collection[str] = (),
) -> bool:
    """Validate WS Host and require an exact authority or opted-in local Origin."""

    if not local_request_host(host, scheme=scheme, bound_port=bound_port):
        return False
    return request_origin_allowed(
        origin=origin,
        host=host,
        scheme=scheme,
        bound_port=bound_port,
        extra_origins=extra_origins,
    )


__all__ = [
    "local_origin",
    "local_request_host",
    "parse_extra_websocket_origins",
    "request_origin_allowed",
    "websocket_request_allowed",
]
