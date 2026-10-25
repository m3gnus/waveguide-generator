"""Consume Fusion's explicit history navigation in the delivery pass.

This is a pointer to an existing request, never another submission. WGLink's
shared protocol owns the directory, version, capability and jobs event kind.
Publication runs on the jobs event loop; only file reads/deletes use a worker.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from server.cadlink.ipc import ipc_folder
from server.cadlink.wglink_protocol import (
    SHOW_REQUESTS_DIRECTORY, SHOW_REQUEST_EVENT, SHOW_REQUEST_MAX_AGE_SECONDS, validate_show_request,
)

logger = logging.getLogger(__name__)


def _discard(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except Exception:
        logger.debug("Could not discard Fusion show request %s", path.name, exc_info=True)


def _read_pointer(path: Path) -> dict[str, Any] | None:
    # Open non-blocking before checking the descriptor: a file can be
    # replaced with a FIFO between directory inspection and open.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("invalid pointer file")
        raw = stream.read(65537)
    if len(raw.encode("utf-8")) > 65536:
        raise ValueError("oversize pointer")
    return validate_show_request(json.loads(raw))


def read_show_requests(data_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    folder = ipc_folder(data_dir) / SHOW_REQUESTS_DIRECTORY
    if folder.is_symlink():
        return []
    try:
        paths = list(folder.iterdir())
    except Exception:
        return []
    found = []
    now = time.time()
    for path in paths:
        try:
            # Never open devices, directories, symlinks or FIFOs. A crashed
            # atomic writer leaves <requestId>.json.<random>, never a pointer.
            if path.is_symlink() or not path.is_file():
                _discard(path)
                continue
            if path.suffix != ".json":
                # A live atomic writer still needs its temporary source for
                # os.replace. Other junk is removed on the first encounter.
                if ".json." not in path.name or now - path.stat().st_mtime > SHOW_REQUEST_MAX_AGE_SECONDS:
                    _discard(path)
                continue
            record = _read_pointer(path)
            if record is None or path.stem != record["requestId"]:
                raise ValueError("invalid pointer")
            at = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
            if at.tzinfo is None or not 0 <= now - at.timestamp() <= SHOW_REQUEST_MAX_AGE_SECONDS:
                raise ValueError("expired pointer")
            found.append((path, record))
        except Exception:  # each untrusted file is isolated, including recursive JSON
            logger.debug("Could not read Fusion show request %s", path.name, exc_info=True)
            _discard(path)
    return sorted(found, key=lambda item: (item[1]["at"], item[0].name))


def publish_show_request(record: dict[str, Any], job: dict[str, Any] | None,
                         publish: Callable[..., None]) -> None:
    # Prefer the latest job in a retry chain. A stale add-in row must still
    # reach the current request, and an untrusted jobId cannot name another run.
    publish({"v": 1, "kind": SHOW_REQUEST_EVENT, "operationId": record["requestId"], "at": record["at"],
             **({"jobId": job["id"]} if job else {})},
            expires_at=datetime.fromisoformat(record["at"].replace("Z", "+00:00")).timestamp()
            + SHOW_REQUEST_MAX_AGE_SECONDS)


def remove_show_request(path: Path, record: dict[str, Any]) -> None:
    # A second press can replace the pointer while its event is published.
    # Remove only the press just consumed, leaving a later one for the next pass.
    try:
        if path.is_symlink() or not path.is_file():
            return
        if _read_pointer(path) == record:
            path.unlink(missing_ok=True)
    except Exception:
        logger.debug("Could not remove Fusion show request %s", path.name, exc_info=True)
