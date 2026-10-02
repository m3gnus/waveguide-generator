"""Read bounded installer outcomes without inventing a successful rollback."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import stat

MAX_OUTCOME_BYTES = 16 * 1024


def read_outcome(data_dir: Path, *, consume: bool = True) -> dict | None:
    path = data_dir / "update-install" / "outcome.json"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTCOME_BYTES:
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                return None
            raw = stream.read(MAX_OUTCOME_BYTES + 1)
        after = path.lstat()
        if (len(raw) > MAX_OUTCOME_BYTES or not stat.S_ISREG(after.st_mode)
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            return None
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            return None
        required = ("from", "to", "result", "when", "log")
        if any(not isinstance(value.get(key), str) or len(value[key]) > 4096 for key in required):
            return None
        if value["result"] not in {"installed", "failed", "rollback_incomplete"}:
            return None
        datetime.fromisoformat(value["when"].replace("Z", "+00:00"))
        result = {key: value[key] for key in required}
        for key in ("backupPath", "journalPath", "reason"):
            if isinstance(value.get(key), str) and len(value[key]) <= 4096:
                result[key] = value[key]
        # Exit code 3 requires manual recovery; even an inconsistent helper
        # record must not turn that into "previous version kept".
        result["previousKept"] = value.get("previousKept") is True and value["result"] == "failed"
        if consume:
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                return None
            path.unlink()
        return result
    except (OSError, ValueError, TypeError):
        return None
