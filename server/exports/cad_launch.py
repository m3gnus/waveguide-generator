"""Bring Fusion forward once a CAD-link bundle has been written.

Send to CAD writes a file; nothing about that reaches a running Fusion. Raising
its window is the small half of closing that gap -- the add-in's watcher is the
half that notices the bundle -- and it is the half that has to be harmless: a
failure here must never turn a written bundle into a failed export.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess


logger = logging.getLogger(__name__)

DISABLE_ENV = "WG2_CAD_LAUNCH"
_BUNDLE_ID = "com.autodesk.fusion360"
_MACOS_APP_NAME = "Autodesk Fusion"
_WINDOWS_EXE = "Fusion360.exe"


def launch_disabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(DISABLE_ENV, "").strip().lower() in {"0", "false", "no", "off"}


def _windows_command() -> list[str] | None:
    """The newest webdeploy build, since Fusion keeps several side by side."""

    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None
    production = Path(local_appdata) / "Autodesk" / "webdeploy" / "production"
    try:
        candidates = sorted(
            production.glob(f"*/{_WINDOWS_EXE}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return [str(candidates[0])] if candidates else None


def _command(system: str) -> list[str] | None:
    if system == "Darwin":
        if not shutil.which("open"):
            return None
        # By bundle id, so a per-user install under ~/Applications is found
        # without this code knowing where anyone keeps their applications.
        return ["open", "-b", _BUNDLE_ID]
    if system == "Windows":
        return _windows_command()
    return None


def focus_cad(*, system: str | None = None, environ: dict[str, str] | None = None) -> str:
    """Raise or start Fusion. Returns a short reason code for the caller's log."""

    if launch_disabled(environ):
        return "disabled"
    resolved = platform.system() if system is None else system
    command = _command(resolved)
    if command is None:
        return f"unsupported:{resolved.lower() or 'unknown'}"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("Could not bring Fusion forward: %s", exc)
        return "failed"
    if completed.returncode != 0:
        if resolved == "Darwin" and shutil.which("open"):
            # A machine whose Fusion predates the rename registers no bundle id.
            fallback = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["open", "-a", _MACOS_APP_NAME],
                check=False,
                capture_output=True,
                timeout=15,
            )
            if fallback.returncode == 0:
                return "launched"
        logger.info(
            "Could not bring Fusion forward: %s exited %d",
            command[0],
            completed.returncode,
        )
        return "failed"
    return "launched"
