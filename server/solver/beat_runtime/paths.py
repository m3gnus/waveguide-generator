"""Provider-scoped roots, separate from swept solve sessions.

These accessors only select paths; future state/registry owners create private
directories. WG overrides select bases, never the provider directory itself.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sys
import tempfile
import stat

PROVIDER_ID = "wg-beat-engine"
STATE_SCHEMA = 1
HOST_PROTOCOL = "wg-beat-host"
HOST_PROTOCOL_VERSION = 2
RUNTIME_DIR_ENV = "WG2_BEAT_RUNTIME_DIR"
WORKER_DIR_ENV = "WG2_BEAT_WORKER_DIR"
def _isolated(
    root: Path,
    env: Mapping[str, str],
    system: str,
    home: Path,
    temp_dir: Path | None = None,
    uid: int | None = None,
) -> Path:
    return root


def checked_root(
    directory: Path,
    *,
    environ: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path:
    """Normalize an explicit runtime/worker directory.

    Every writer that accepts a directory argument calls this before touching
    the filesystem; the defaults from runtime_dir()/worker_dir() already pass.
    Path.resolve follows symlinks and, on Windows, junctions.
    """
    env = os.environ if environ is None else environ
    return _isolated(Path(directory), env, system or sys.platform, home or Path.home())


def is_link(path: Path) -> bool:
    """Include Windows junctions and other reparse points in link checks."""
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _data_base(system: str, env: Mapping[str, str], home: Path) -> Path:
    if system == "darwin":
        return home / "Library" / "Application Support"
    if system == "win32":
        return Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
    return Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else home / ".local" / "share"


def runtime_dir(
    *, environ: Mapping[str, str] | None = None, system: str | None = None,
    home: Path | None = None,
) -> Path:
    """Root of portable Julia, depot, downloads and provisioning records."""
    env = os.environ if environ is None else environ
    system = system or sys.platform
    home = home or Path.home()
    base = env.get(RUNTIME_DIR_ENV)
    if base:
        root = Path(base).expanduser() / PROVIDER_ID
    elif system == "win32":
        # Keep Julia's 155-character archive members below Windows MAX_PATH.
        root = _data_base(system, env, home) / "WaveguideGenerator" / "beat"
    else:
        root = _data_base(system, env, home) / "WaveguideGenerator" / "beat-runtime" / PROVIDER_ID
    return _isolated(root, env, system, home)


def worker_dir(
    *, environ: Mapping[str, str] | None = None, system: str | None = None,
    home: Path | None = None, temp_dir: Path | None = None, uid: int | None = None,
) -> Path:
    """Root of detached host records, locks and endpoints; outside sessions."""
    env = os.environ if environ is None else environ
    system = system or sys.platform
    home = home or Path.home()
    root = _worker_dir(env, system, home, temp_dir, uid)
    return _isolated(root, env, system, home, temp_dir, uid)


def _worker_dir(
    env: Mapping[str, str],
    system: str,
    home: Path | None,
    temp_dir: Path | None,
    uid: int | None,
) -> Path:
    if env.get(WORKER_DIR_ENV):
        return Path(env[WORKER_DIR_ENV]).expanduser() / PROVIDER_ID
    if system == "win32":
        return _data_base(system, env, home or Path.home()) / "WaveguideGenerator" / "beat-workers" / PROVIDER_ID
    if env.get("XDG_RUNTIME_DIR"):
        return Path(env["XDG_RUNTIME_DIR"]) / PROVIDER_ID
    user_id = os.getuid() if uid is None else uid
    return (temp_dir or Path(tempfile.gettempdir())) / f"{PROVIDER_ID}-{user_id}"
