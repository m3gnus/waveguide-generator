"""Small headless equivalent of the status launcher's serve.py command."""

from __future__ import annotations

from pathlib import Path
import sys

from server.platform.paths import app_root


def headless_backend_argv(data_dir: Path) -> list[str]:
    """Start the supported server on this folder, independent of the caller's cwd."""

    return [
        sys.executable,
        str(app_root() / "launch" / "serve.py"),
        "--data-dir",
        str(data_dir.resolve()),
        "--no-browser",
    ]
