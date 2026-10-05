"""Provider-scoped roots, separate from HBB and swept solve sessions.

These accessors only select paths; future state/registry owners create private
directories. WG overrides select bases, never the provider directory itself.
"""

from collections.abc import Mapping
import os
from pathlib import Path
import sys
import tempfile

PROVIDER_ID = "wg-beat-engine"
STATE_SCHEMA = 1
HOST_PROTOCOL = "wg-beat-host"
HOST_PROTOCOL_VERSION = 1
RUNTIME_DIR_ENV = "WG2_BEAT_RUNTIME_DIR"
WORKER_DIR_ENV = "WG2_BEAT_WORKER_DIR"


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
    base = env.get(RUNTIME_DIR_ENV)
    if base:
        return Path(base).expanduser() / PROVIDER_ID
    return _data_base(system or sys.platform, env, home or Path.home()) / "WaveguideGenerator" / "beat-runtime" / PROVIDER_ID


def worker_dir(
    *, environ: Mapping[str, str] | None = None, system: str | None = None,
    home: Path | None = None, temp_dir: Path | None = None, uid: int | None = None,
) -> Path:
    """Root of detached host records, locks and endpoints; outside sessions."""
    env = os.environ if environ is None else environ
    system = system or sys.platform
    if env.get(WORKER_DIR_ENV):
        return Path(env[WORKER_DIR_ENV]).expanduser() / PROVIDER_ID
    if system == "win32":
        return _data_base(system, env, home or Path.home()) / "WaveguideGenerator" / "beat-workers" / PROVIDER_ID
    if env.get("XDG_RUNTIME_DIR"):
        return Path(env["XDG_RUNTIME_DIR"]) / PROVIDER_ID
    user_id = os.getuid() if uid is None else uid
    return (temp_dir or Path(tempfile.gettempdir())) / f"{PROVIDER_ID}-{user_id}"
