"""Provider-scoped roots, separate from HBB and swept solve sessions.

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
# HBB's root overrides. Without them HBB uses the defaults in _hbb_roots.
HBB_ROOT_ENVS = ("HORNLAB_BEAT_RUNTIME_DIR", "HORNLAB_BEAT_WORKER_DIR")


class RootConflict(ValueError):
    """A WG root would share a directory tree with HBB's state or registry."""


def _hbb_roots(
    env: Mapping[str, str], system: str, home: Path, temp_dir: Path | None, uid: int | None
) -> dict[str, Path]:
    """HBB's effective runtime and worker roots (hornlab_beat_bem provision/worker_registry)."""
    roots = {}
    runtime = env.get(HBB_ROOT_ENVS[0], "").strip()
    if runtime:
        roots[HBB_ROOT_ENVS[0]] = Path(runtime)
    elif system == "darwin":
        roots["HBB runtime root"] = home / "Library" / "Application Support" / "hornlab-beat" / "runtime"
    else:
        roots["HBB runtime root"] = _data_base(system, env, home) / "hornlab-beat" / "runtime"
    workers = env.get(HBB_ROOT_ENVS[1], "").strip()
    if workers:
        roots[HBB_ROOT_ENVS[1]] = Path(workers)
    elif system == "win32":
        roots["HBB worker root"] = _data_base(system, env, home) / "HornLab" / "BEAT" / "workers"
    else:
        xdg = env.get("XDG_RUNTIME_DIR", "").strip()
        if xdg:
            roots["HBB worker root"] = Path(xdg) / "hornlab-beat"
        user_id = os.getuid() if uid is None else uid
        tmp = temp_dir or Path(tempfile.gettempdir())
        roots["HBB temporary worker root"] = tmp / f"hornlab-beat-{user_id}"
    return roots


def _isolated(
    root: Path,
    env: Mapping[str, str],
    system: str,
    home: Path,
    temp_dir: Path | None = None,
    uid: int | None = None,
) -> Path:
    resolved = root.expanduser().resolve()
    for name, legacy_root in _hbb_roots(env, system, home, temp_dir, uid).items():
        legacy = legacy_root.expanduser().resolve()
        if resolved == legacy or legacy in resolved.parents or resolved in legacy.parents:
            raise RootConflict(f"{root} overlaps {name} ({legacy}); choose separate directories")
    return root


def checked_root(
    directory: Path,
    *,
    environ: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path:
    """Refuse an explicit runtime/worker directory that overlaps HBB's roots.

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


def hbb_executable(path: Path, *, environ: Mapping[str, str] | None = None) -> bool:
    """Identify legacy-managed binaries, including aliases into HBB roots."""
    env = os.environ if environ is None else environ
    resolved = path.expanduser().resolve()
    return any(resolved.is_relative_to(root.expanduser().resolve()) for root in
               _hbb_roots(env, sys.platform, Path.home(), None, None).values())


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
