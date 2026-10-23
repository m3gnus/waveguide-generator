"""Atomic publication of small JSON records."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish a small cross-process setting without exposing a torn file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
