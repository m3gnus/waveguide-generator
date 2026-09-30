"""Durable, per-data-directory evidence for automatic WGLink activation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Literal

from server.platform.paths import data_paths

UsageReason = Literal["setup-task", "install-action", "addin-delivery"]
_REASONS = {"setup-task", "install-action", "addin-delivery"}


def usage_path(data_dir: Path) -> Path:
    """Setup and WG share this record, independent of the application root."""
    return data_paths(data_dir).root / "integrations" / "wglink" / "cadlink-in-use.json"


def read_usage(data_dir: Path) -> dict[str, object] | None:
    """Read a WG- or setup-written v1 record; missing/invalid evidence grants nothing."""
    try:
        record = json.loads(usage_path(data_dir).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or type(record.get("schemaVersion")) is not int:
            return None
        if record["schemaVersion"] != 1 or record.get("reason") not in _REASONS:
            return None
        timestamp = record.get("recordedAt")
        if not isinstance(timestamp, str):
            return None
        if datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
            return None
        return record
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def record_usage(data_dir: Path, reason: UsageReason) -> None:
    """Atomically persist a choice or accepted add-in delivery before acknowledging it."""
    if reason not in _REASONS:
        raise ValueError("unknown CAD Link usage reason")
    path = usage_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schemaVersion": 1,
        "reason": reason,
        "recordedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        # Only a failed write leaves the temporary file behind.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if os.name != "nt":
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def cadlink_in_use(*, data_dir: Path, owned_addin: bool) -> bool:
    """Use only ownership or the explicit/delivery record in this data directory.

    Existing ingests, projects and operations have no reliable transport origin:
    hand-opened returns share them. Mode, exports and design metadata are inert.
    Delivery acceptance records usage in CAD Link, independent of solve callers.
    This probe creates no directories, settings or database.
    """
    return owned_addin or read_usage(data_dir) is not None
