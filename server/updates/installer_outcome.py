"""Read bounded installer outcomes without inventing a successful rollback."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import stat

MAX_OUTCOME_BYTES = 16 * 1024


def read_outcome(data_dir: Path, *, consume: bool = True) -> dict | None:
    path = data_dir / "update-install" / "outcome.json"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTCOME_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        required = ("from", "to", "result", "when", "log")
        if any(not isinstance(value.get(key), str) or len(value[key]) > 4096 for key in required):
            return None
        if value["result"] not in {"installed", "failed", "rollback_incomplete"}:
            return None
        datetime.fromisoformat(value["when"].replace("Z", "+00:00"))
        result = {key: value[key] for key in required}
        for key in ("backupPath", "reason"):
            if isinstance(value.get(key), str) and len(value[key]) <= 4096:
                result[key] = value[key]
        # Exit code 3 requires manual recovery; even an inconsistent helper
        # record must not turn that into "previous version kept".
        result["previousKept"] = value.get("previousKept") is True and value["result"] == "failed"
        if consume:
            path.unlink()
        return result
    except (OSError, ValueError, TypeError):
        return None
