"""Consume Fusion's explicit history navigation in the delivery pass.

This is a pointer to an existing request, never another submission. WGLink's
shared protocol owns the directory, version, capability and jobs event kind.
Publication runs on the jobs event loop; only file reads/deletes use a worker.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from server.cadlink.ipc import ipc_folder
from server.cadlink.wglink_protocol import SHOW_REQUESTS_DIRECTORY, SHOW_REQUEST_EVENT, validate_show_request

logger = logging.getLogger(__name__)


def read_show_requests(data_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    folder = ipc_folder(data_dir) / SHOW_REQUESTS_DIRECTORY
    if folder.is_symlink():
        return []
    try:
        paths = sorted(folder.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    except OSError:
        return []
    found = []
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            record = validate_show_request(json.loads(path.read_text(encoding="utf-8")))
            if record is not None and path.stem == record["requestId"]:
                found.append((path, record))
            else:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.debug("Could not read Fusion show request %s", path.name, exc_info=True)
    return found


def publish_show_request(record: dict[str, Any], job: dict[str, Any] | None,
                         publish: Callable[[dict[str, Any]], None]) -> None:
    # Prefer the latest job in a retry chain. A stale add-in row must still
    # reach the current request, and an untrusted jobId cannot name another run.
    publish({"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": record["requestId"],
             **({"jobId": job["id"]} if job else {})})


def remove_show_request(path: Path, record: dict[str, Any]) -> None:
    # A second press can replace the pointer while its event is published.
    # Remove only the press just consumed, leaving a later one for the next pass.
    try:
        if validate_show_request(json.loads(path.read_text(encoding="utf-8"))) == record:
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        logger.debug("Could not remove Fusion show request %s", path.name, exc_info=True)
