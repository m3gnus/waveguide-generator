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
# HBB's overrides. Its default roots never coincide with ours; an override can.
HBB_ROOT_ENVS = ("HORNLAB_BEAT_RUNTIME_DIR", "HORNLAB_BEAT_WORKER_DIR")


class RootConflict(ValueError):
    """A WG root would share a directory tree with HBB's state or registry."""


def _isolated(root: Path, env: Mapping[str, str]) -> Path:
    resolved = root.expanduser().resolve()
    for name in HBB_ROOT_ENVS:
        value = env.get(name, "").strip()
        if not value:
            continue
        legacy = Path(value).expanduser().resolve()
        if resolved == legacy or legacy in resolved.parents or resolved in legacy.parents:
            raise RootConflict(f"{root} overlaps HBB's {name} ({legacy}); choose separate directories")
    return root


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
        return _isolated(Path(base).expanduser() / PROVIDER_ID, env)
    return _isolated(
        _data_base(system or sys.platform, env, home or Path.home())
        / "WaveguideGenerator"
        / "beat-runtime"
        / PROVIDER_ID,
        env,
    )


def worker_dir(
    *, environ: Mapping[str, str] | None = None, system: str | None = None,
    home: Path | None = None, temp_dir: Path | None = None, uid: int | None = None,
) -> Path:
    """Root of detached host records, locks and endpoints; outside sessions."""
    env = os.environ if environ is None else environ
    return _isolated(_worker_dir(env, system or sys.platform, home, temp_dir, uid), env)


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
