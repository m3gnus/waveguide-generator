"""Owner-only directories for WG's private on-disk state."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat


log = logging.getLogger("wg.paths")


def ensure_private_directory(path: Path, *, parents: bool = False) -> Path:
    """Create ``path`` and make an owned POSIX directory mode 0700.

    Windows access remains governed by the user's profile ACLs. In particular,
    passing ``mode`` to ``mkdir`` there is not an ACL operation, so this helper
    deliberately makes no permission change on Windows.
    """

    path.mkdir(mode=0o700, parents=parents, exist_ok=True)
    if os.name == "nt":
        return path

    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) and path.is_dir():
        # A data folder kept elsewhere through a symlink is the user's layout:
        # use it as it is rather than refuse to start or re-mode its target.
        log.warning("Not tightening permissions on %s because it is a symbolic link.", path)
        return path
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(path)
    getuid = getattr(os, "getuid", None)
    if getuid is None or metadata.st_uid != getuid():
        log.warning(
            "Not tightening permissions on %s because WG does not own it.", path
        )
        return path
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        path.chmod(0o700)
    return path
