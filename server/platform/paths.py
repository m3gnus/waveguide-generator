"""Versioned, v2-only application data paths."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
from typing import Mapping


DATA_DIR_ENV = "WG2_DATA_DIR"
APP_DIRECTORY = "WaveguideGenerator2"


@dataclass(frozen=True, slots=True)
class DataPaths:
    """All persistent paths owned by Waveguide Generator v2."""

    root: Path
    db: Path
    logs: Path
    locks: Path


def resolve_data_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the v2 data directory without creating it.

    Explicit ``override`` wins over ``WG2_DATA_DIR``.  The injectable keyword
    arguments keep OS-specific behavior deterministic in tests.
    """

    env = os.environ if environ is None else environ
    configured = override if override is not None else env.get(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().absolute()

    os_name = platform.system() if system is None else system
    home_dir = Path.home() if home is None else Path(home)

    if os_name == "Darwin":
        root = home_dir / "Library" / "Application Support"
    elif os_name == "Windows":
        appdata = env.get("APPDATA")
        if not appdata:
            raise RuntimeError(
                "APPDATA is not set, so the Windows data directory cannot be "
                "determined. Set APPDATA or WG2_DATA_DIR and start again."
            )
        root = Path(appdata)
    else:
        xdg_data_home = env.get("XDG_DATA_HOME")
        root = Path(xdg_data_home) if xdg_data_home else home_dir / ".local" / "share"

    return (root / APP_DIRECTORY).expanduser().absolute()


def data_paths(data_dir: str | os.PathLike[str] | None = None, **kwargs: object) -> DataPaths:
    """Build the v2 path set without touching the filesystem."""

    root = resolve_data_dir(data_dir, **kwargs)
    return DataPaths(root=root, db=root / "db", logs=root / "logs", locks=root / "locks")


def ensure_data_layout(
    data_dir: str | os.PathLike[str] | None = None, **kwargs: object
) -> DataPaths:
    """Create and return the v2 data layout."""

    paths = data_paths(data_dir, **kwargs)
    for path in (paths.root, paths.db, paths.logs, paths.locks):
        path.mkdir(parents=True, exist_ok=True)
    return paths
