"""``wg-endpoint.json``: where this WG start serves the live CAD Link session.

Written at startup, only when the launcher told ``create_app`` the port it
reserved, into ``<data dir>/ipc/wglink`` beside ``wg-capabilities.json``, with
the same atomic writer (a private ``mkstemp`` file, fsync, replace; mode 0600 on
POSIX, the per-user profile ACL on Windows). It holds this start's
``registrationSecret``, which never leaves this file and WG's memory: it is
never sent over the wire, logged, or returned by any route. Replaced by every
start; removed at a clean shutdown only while it still names this instance.
(docs/reference/CADLINK-LIVE-PROTOCOL.md, "Endpoint discovery")
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from server.cadlink import fusion_delivery


ENDPOINT_FILENAME = "wg-endpoint.json"
ENDPOINT_SCHEMA_VERSION = 1
PRODUCER = "waveguide-generator"
#: The launcher binds loopback IPv4 only (``launch/serve.py``).
LOOPBACK_HOST = "127.0.0.1"

logger = logging.getLogger(__name__)


def utc_timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def endpoint_document(
    *, instance_id: str, pid: int, port: int, started_at: str, secret: str
) -> dict[str, Any]:
    return {
        "schemaVersion": ENDPOINT_SCHEMA_VERSION,
        "producer": PRODUCER,
        "instanceId": instance_id,
        "pid": pid,
        "baseUrl": f"http://{LOOPBACK_HOST}:{port}",
        "liveProtocol": fusion_delivery.LIVE_PROTOCOL,
        "startedAt": started_at,
        "registrationSecret": secret,
    }


def endpoint_path(data_dir: Path) -> Path:
    return fusion_delivery.ipc_folder(data_dir) / ENDPOINT_FILENAME


def write_endpoint(data_dir: Path, document: dict[str, Any]) -> None:
    folder = fusion_delivery.ipc_folder(data_dir, create=True)
    fusion_delivery._write_json(folder / ENDPOINT_FILENAME, document)


def remove_endpoint_if_ours(data_dir: Path, instance_id: str) -> bool:
    """Remove the endpoint file if it names ``instance_id``; a newer start's stays."""

    path = endpoint_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("instanceId") != instance_id:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Could not remove the live CAD Link endpoint file: %s", exc)
        return False
    return True


__all__ = [
    "ENDPOINT_FILENAME",
    "ENDPOINT_SCHEMA_VERSION",
    "LOOPBACK_HOST",
    "endpoint_document",
    "endpoint_path",
    "remove_endpoint_if_ours",
    "utc_timestamp",
    "write_endpoint",
]
