"""Application logging to stderr and a small rotating disk log."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

from .paths import DataPaths, ensure_data_layout


MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_FILENAME = "server.log"


def _rotate_if_needed(log_path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    try:
        oversized = log_path.stat().st_size >= max_bytes
    except FileNotFoundError:
        return
    if oversized:
        os.replace(log_path, log_path.with_name(f"{log_path.name}.1"))


def setup_logging(
    data: DataPaths | str | os.PathLike[str] | None = None,
    *,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure the process root logger and return the WG2 logger."""

    paths = data if isinstance(data, DataPaths) else ensure_data_layout(data)
    log_path = paths.logs / LOG_FILENAME
    _rotate_if_needed(log_path)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    root = logging.getLogger()
    root.setLevel(level)

    for handler in tuple(root.handlers):
        if getattr(handler, "_wg2_handler", False):
            root.removeHandler(handler)
            handler.close()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler._wg2_handler = True  # type: ignore[attr-defined]

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._wg2_handler = True  # type: ignore[attr-defined]

    root.addHandler(stderr_handler)
    root.addHandler(file_handler)
    logger = logging.getLogger("wg2")
    logger.info("Logging to %s", log_path)
    return logger


def flush_logs() -> None:
    """Flush all active logging handlers, best-effort during shutdown."""

    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except (OSError, ValueError):
            pass
