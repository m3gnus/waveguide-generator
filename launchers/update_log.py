"""The ``update.log`` in the application data directory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from launchers.install_paths import resolved_path


def append_update_log(data_dir: Path, message: str) -> None:
    """Append one update event. Best effort: logging never changes control flow."""

    try:
        logs = resolved_path(data_dir) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with (logs / "update.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:  # noqa: BLE001 - logging must never break the caller
        pass
