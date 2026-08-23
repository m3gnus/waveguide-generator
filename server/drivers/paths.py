"""The per-user driver library folder.

Mirrors ``server/platform/paths.py:resolve_data_dir``'s per-platform logic,
but under a shared ``HornLab`` vendor folder rather than this product's own
data directory: the library is meant to be one CSV drop folder a person can
point several HornLab tools at, not something private to Waveguide
Generator's own application-support tree.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
from typing import Mapping


DRIVER_LIBRARY_DIR_ENV = "WG2_DRIVER_LIBRARY_DIR"
VENDOR_DIRECTORY = "HornLab"
LIBRARY_SUBDIRECTORY = "driver-databases"


def resolve_driver_library_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the driver library folder without creating it.

    Explicit ``override`` wins over ``WG2_DRIVER_LIBRARY_DIR``, which is how
    tests keep fixture folders isolated from a developer's real library.
    """

    env = os.environ if environ is None else environ
    configured = override if override is not None else env.get(DRIVER_LIBRARY_DIR_ENV)
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
                "APPDATA is not set, so the driver library directory cannot be "
                "determined. Set APPDATA or WG2_DRIVER_LIBRARY_DIR and start again."
            )
        root = Path(appdata)
    else:
        xdg_data_home = env.get("XDG_DATA_HOME")
        root = Path(xdg_data_home) if xdg_data_home else home_dir / ".local" / "share"

    return (root / VENDOR_DIRECTORY / LIBRARY_SUBDIRECTORY).expanduser().absolute()


def ensure_driver_library_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the driver library folder and create it if this is the first use."""

    folder = resolve_driver_library_dir(override, system=system, environ=environ, home=home)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


__all__ = [
    "DRIVER_LIBRARY_DIR_ENV",
    "LIBRARY_SUBDIRECTORY",
    "VENDOR_DIRECTORY",
    "ensure_driver_library_dir",
    "resolve_driver_library_dir",
]
