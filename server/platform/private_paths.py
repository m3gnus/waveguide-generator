"""Owner-only directories for WG's private on-disk state."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat
import threading


log = logging.getLogger("wg.paths")
# Folders left as they are, each reported once per process: (path, reason).
# Keyed by name and reason, never by inode, because Linux reuses an inode
# number after rmdir + mkdir and would make a recreated folder look handled.
_reported: set[tuple[str, str]] = set()
_reported_lock = threading.Lock()


def _report_once(path: Path, reason: str, message: str, *args: object) -> None:
    key = (os.path.normcase(os.path.abspath(path)), reason)
    with _reported_lock:
        if key in _reported:
            return
        _reported.add(key)
    log.warning(message, *args)


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

    try:
        path.mkdir(mode=0o700, parents=parents, exist_ok=False)
        existed = False
    except FileExistsError:
        existed = True
    # mkdir applied 0700 to a leaf this process created. Only an existing
    # folder needs a look, and one already private needs nothing more: the
    # common case on every delivery pass is one lstat.
    if not existed:
        return path
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o700:
        return path

    try:
        linked = _symlink_in_chain(path, data_root if data_root is not None else path)
    except OSError as exc:
        # Tightening is optional; an ancestor WG cannot inspect must not stop it.
        _report_once(
            path, "uninspectable",
            "Not tightening permissions on %s; could not inspect its path: %s", path, exc,
        )
        return path
    if linked is not None:
        # A data folder kept elsewhere through a symlink is the user's layout.
        _report_once(
            path, "symlink",
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
        _report_once(
            path, "foreign", "Not tightening permissions on %s because WG does not own it.", path
        )
        return path
    with _reported_lock:
        # A folder whose chmod already failed is used as it is; retrying it on
        # every delivery pass would change nothing and cost a call a second.
        gave_up = (os.path.normcase(os.path.abspath(path)), "chmod") in _reported
    if stat.S_IMODE(metadata.st_mode) != 0o700 and not gave_up:
        try:
            path.chmod(0o700)
        except OSError as exc:
            _report_once(
                path, "chmod", "Could not tighten permissions on %s; continuing: %s", path, exc
            )
    return path
