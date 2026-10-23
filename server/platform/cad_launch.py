"""Open archived Fusion files or bring Fusion forward after a CAD-link export.

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

from server.platform.process import background_process_kwargs


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


def _run_command(command: list[str], system: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, check=False, capture_output=True, timeout=15,
        **background_process_kwargs(system=system),
    )


def open_cad_document(document: Path, *, system: str | None = None) -> None:
    """Import an archived .f3d through Fusion or the OS file association.

    The caller resolves and confines the archived file. Launch policy is also
    enforced here so a changed setting cannot be bypassed by a stale UI.
    """

    if launch_disabled():
        raise RuntimeError("Opening Fusion is disabled on this computer.")
    if document.suffix.lower() != ".f3d":
        raise RuntimeError("Only archived Fusion .f3d files can be opened.")
    resolved = platform.system() if system is None else system
    try:
        if resolved == "Darwin":
            completed = _run_command(["open", "-a", _MACOS_APP_NAME, str(document)], resolved)
            if completed.returncode == 0:
                return
            # Older Fusion installations can register the file type under a
            # different application name. Let Launch Services find that handler.
            if _run_command(["open", str(document)], resolved).returncode == 0:
                return
        elif resolved == "Windows":
            os.startfile(str(document))
            return
        else:
            raise RuntimeError("Opening Fusion files is supported on macOS and Windows.")
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Could not open the Fusion file. Check that Fusion 360 is installed.") from exc
    raise RuntimeError("Could not open the Fusion file. Install Fusion 360 or set it as the .f3d handler.")


def focus_cad(*, system: str | None = None, environ: dict[str, str] | None = None) -> str:
    """Raise or start Fusion. Returns a short reason code for the caller's log."""

    if launch_disabled(environ):
        return "disabled"
    resolved = platform.system() if system is None else system
    command = _command(resolved)
    if command is None:
        return f"unsupported:{resolved.lower() or 'unknown'}"
    try:
        completed = _run_command(command, resolved)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("Could not bring Fusion forward: %s", exc)
        return "failed"
    if completed.returncode != 0:
        if resolved == "Darwin" and shutil.which("open"):
            # A machine whose Fusion predates the rename registers no bundle id.
            fallback = _run_command(["open", "-a", _MACOS_APP_NAME], resolved)
            if fallback.returncode == 0:
                return "launched"
        logger.info(
            "Could not bring Fusion forward: %s exited %d",
            command[0],
            completed.returncode,
        )
        return "failed"
    return "launched"
