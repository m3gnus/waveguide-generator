"""Atomic publication of small JSON records."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def json_bytes(payload: Any) -> bytes:
    """The exact bytes ``write_json_atomic`` publishes, on every platform.

    Callers that journal or verify a record by checksum hash these bytes, so
    the file must never pass through text-mode newline translation (Windows
    would turn every ``\\n`` into ``\\r\\n`` and break the checksum).
    """

    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish a small cross-process setting without exposing a torn file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
