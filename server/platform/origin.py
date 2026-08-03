"""Shared loopback-only HTTP/WebSocket Origin validation."""

from __future__ import annotations

import ipaddress
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


__all__ = ["local_origin"]
