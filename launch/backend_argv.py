"""Small headless equivalent of the status launcher's serve.py command."""

from __future__ import annotations

from pathlib import Path
import sys

from server.platform.paths import app_root


def headless_backend_argv(data_dir: Path | None) -> list[str]:
    """Preserve the launcher's defaults; only forward an explicit folder override."""

    command = [
        sys.executable,
        str(app_root() / "launch" / "serve.py"),
    ]
    if data_dir is not None:
        command.extend(("--data-dir", str(data_dir.resolve())))
    command.append("--no-browser")
    return command
