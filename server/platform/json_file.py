"""Atomic publication of small JSON records."""

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


def json_bytes(payload: Any) -> bytes:
    """The exact bytes ``write_json_atomic`` publishes, on every platform.

    Callers that journal or verify a record by checksum hash these bytes, so
    the file must never pass through text-mode newline translation (Windows
    would turn every ``\\n`` into ``\\r\\n`` and break the checksum).
    """

    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def sync_directory(directory: Path) -> None:
    """Flush a folder's entries to disk (POSIX), so a rename outlives a power cut."""

    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_json_atomic(
    path: Path,
    payload: dict[str, Any],
    *,
    replace_attempts: int = 1,
    retry_seconds: float = 0.0,
    durable_directory: bool = False,
) -> None:
    """Publish a small cross-process setting without exposing a torn file.

    ``replace_attempts`` retries the final replace while another process holds
    the target (Windows raises ``PermissionError`` while a reader has it open),
    ``retry_seconds`` apart; a record another process polls needs that.
    ``durable_directory`` also flushes the folder after the replace, so the new
    name outlives a power cut. Raises ``OSError`` when the record could not be
    published; its staged file is removed then, unless the removal fails too.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(max(1, replace_attempts)):
            try:
                os.replace(temporary, path)
                replaced = True
                break
            except PermissionError:
                if attempt + 1 >= replace_attempts:
                    raise
                time.sleep(retry_seconds)
        if durable_directory:
            sync_directory(path.parent)
    finally:
        if not replaced:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # a staged file left behind is named with a leading "."; it is never read
