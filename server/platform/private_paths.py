"""Owner-only directories for WG's private on-disk state."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat
import threading


log = logging.getLogger("wg.paths")
_checked_paths: set[str] = set()
_checked_paths_lock = threading.Lock()


def _path_key(path: Path) -> str:
    """A lexical, absolute key which deliberately does not follow symlinks."""

    return os.path.normcase(os.path.abspath(path))


def _symlink_in_chain(path: Path, data_root: Path) -> Path | None:
    """The first symlink from the configured data root through ``path``."""

    absolute_path = Path(os.path.abspath(path))
    absolute_root = Path(os.path.abspath(data_root))
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError:
        # Callers that do not have a configured data root retain the old leaf
        # check. Shipping-path callers always pass their unresolved root.
        absolute_root = absolute_path
        relative = Path()
    candidate = absolute_root
    for component in (None, *relative.parts):
        if component is not None:
            candidate /= component
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            return candidate
    return None


def ensure_private_directory(
    path: Path, *, parents: bool = False, data_root: Path | None = None
) -> Path:
    """Create ``path`` and make an owned POSIX directory mode 0700.

    Windows access remains governed by the user's profile ACLs. In particular,
    passing ``mode`` to ``mkdir`` there is not an ACL operation, so this helper
    deliberately makes no permission change on Windows.
    """

    if os.name == "nt":
        path.mkdir(mode=0o700, parents=parents, exist_ok=True)
        return path

    key = _path_key(path)
    with _checked_paths_lock:
        already_checked = key in _checked_paths
    if already_checked:
        path.mkdir(mode=0o700, parents=parents, exist_ok=True)
        return path

    try:
        path.lstat()
        existed = True
    except FileNotFoundError:
        existed = False
    path.mkdir(mode=0o700, parents=parents, exist_ok=True)

    # Another caller may have completed this one-time inspection while this
    # caller was ensuring that the directory exists.
    with _checked_paths_lock:
        already_checked = key in _checked_paths
        if not already_checked:
            _checked_paths.add(key)
    if already_checked:
        return path

    # mkdir applied 0700 to a leaf this process created. Existing directories
    # alone need inspection and possible tightening.
    if not existed:
        return path

    metadata = path.lstat()
    try:
        linked = _symlink_in_chain(path, data_root if data_root is not None else path)
    except OSError as exc:
        # Tightening is optional; an ancestor WG cannot inspect must not stop it.
        log.warning("Not tightening permissions on %s; could not inspect its path: %s", path, exc)
        return path
    if linked is not None:
        # A data folder kept elsewhere through a symlink is the user's layout.
        log.warning(
            "Not tightening permissions on %s because its data path contains "
            "the symbolic link %s.",
            path,
            linked,
        )
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
        try:
            path.chmod(0o700)
        except OSError as exc:
            log.warning("Could not tighten permissions on %s; continuing: %s", path, exc)
    return path
